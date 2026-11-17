from __future__ import annotations

import atexit
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
import re
import secrets
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

from originrag.cancellation import urlopen


CURRENT_RUNTIME = ContextVar("originrag_model_runtime", default=None)
MODEL_ID = "olmoe-1b-7b"
MODEL_FILE = "OLMoE-1B-7B-0125-Instruct-Q4_K_M.gguf"
MODEL_SHA = "4ddc0e53159ed512b8dd67914a66e27bc618f694672ba43a9a0454eabd9c684f"
MODEL_SIZE = 4213512192
MODEL_URL = (
    "https://huggingface.co/allenai/OLMoE-1B-7B-0125-Instruct-GGUF/resolve/"
    "2ac1d27317927518c9ef7bd99f91f3f1e4ee288d/" + MODEL_FILE
)
OLLAMA_DOWNLOADS = {
    "qwen3.5:4b": 3_400_000_000,
    "qwen3.5:9b": 6_600_000_000,
    "qwen3.5:35b": 22_000_000_000,
    "qwen2.5-coder:7b": 4_700_000_000,
    "qwen3-coder:30b": 19_000_000_000,
    "qwen3:30b": 20_000_000_000,
    "qwen3:4b-q4_K_M": 3_000_000_000,
    "qwen3:8b-q4_K_M": 6_000_000_000,
    "qwen3:30b-a3b-q4_K_M": 20_000_000_000,
}
RECOMMENDATIONS = {
    "qwen3.5:9b": ("General use · balanced", "general", 3),
    "qwen3.5:4b": ("General use · lighter", "general", 2),
    "qwen3.5:35b": ("General use · larger", "general", 4),
    "qwen3-coder:30b": ("Code & LaTeX · larger", "code", 3),
    "qwen2.5-coder:7b": ("Code & LaTeX · lighter", "code", 2),
}
COLIBRI_MODELS = {
    "glm-5.3": {
        "name": "GLM-5.3 · Colibri INT4", "bytes": 419316798141, "context": 16384,
        "engine": "colibri", "low_memory": 32, "fast": 64, "ram_budget": True,
        "repo": "Justvugg/GLM-5.3-colibri-int4-g64",
        "revision": "3930f4bb756e6586e6103d57b42c9b0a7bbf6f4c",
        "note": "Experimental · 16K context. Cold starts can take minutes. GPU when idle, otherwise CPU/RAM; weights stream from storage. Structured-JSON tasks require Ollama.",
    },
    MODEL_ID: {"name": "OLMoE · small test model", "bytes": MODEL_SIZE, "context": 4096,
               "engine": "olmoe", "low_memory": 8, "fast": 64,
               "note": "Experimental · 4K context. For testing only. Structured-JSON tasks such as paper summaries require Ollama."},
    "qwen3-coder-30b": {
        "name": "Qwen3-Coder · 30B / 3B active", "bytes": 19398108288, "context": 16384,
        "engine": "qwen36", "low_memory": 32, "fast": 128,
        "repo": "Justvugg/Qwen3-Coder-30B-A3B-colibri-int4",
        "revision": "d839110a17d31b4e6fa9f3d973b751f779b6b9c6",
        "note": "Experimental · 16K context. Validate answers. Structured-JSON tasks such as paper summaries require Ollama.",
    },
}
_MANAGERS = {}
_MANAGERS_LOCK = threading.Lock()


def model_manage_allowed(user):
    return bool(user and (user.role == "admin" or user.username.casefold() in {
        name.strip().casefold()
        for name in os.environ.get("ORIGINRAG_MODEL_MANAGE_USERS", "").split(",") if name.strip()
    }))


def model_download_allowed(user):
    if user is None:
        return False
    allowed = os.environ.get("ORIGINRAG_MODEL_DOWNLOAD_USERS")
    if allowed is None:
        return user.role == "admin"
    return user.username.casefold() in {
        name.strip().casefold() for name in allowed.split(",") if name.strip()
    }


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def http_json(url, payload=None, timeout=5, api_key=None):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + api_key} if api_key else {})},
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(8 * 1024 * 1024))


def shared_glm_runtime():
    from urllib.parse import urlsplit
    base = os.environ.get("ORIGINRAG_GLM_SERVICE_URL", "").rstrip("/")
    if not base:
        return None
    parsed = urlsplit(base)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("The shared GLM service must use a loopback HTTP address.")
    path = Path(os.environ["ORIGINRAG_GLM_API_KEY_FILE"])
    if path.stat().st_mode & 0o077:
        raise ValueError("The GLM API key file must be private (chmod 600).")
    key = path.read_text().strip()
    if len(key) < 32:
        raise ValueError("The shared GLM service requires an API key.")
    return {"base_url": base, "api_key": key}


def manager_for(root):
    root = Path(root).resolve()
    with _MANAGERS_LOCK:
        if root not in _MANAGERS:
            _MANAGERS[root] = ModelManager(root)
        return _MANAGERS[root]


def ollama_model_urls():
    from urllib.parse import urlsplit
    raw = os.environ.get("ORIGINRAG_OLLAMA_MODEL_URLS", "{}")
    try:
        if len(raw) > 8192:
            raise ValueError
        routes = json.loads(raw)
        if not isinstance(routes, dict) or len(routes) > 32:
            raise ValueError
        for model, url in routes.items():
            if not isinstance(model, str) or not model.strip() or len(model) > 200:
                raise ValueError
            if not isinstance(url, str):
                raise ValueError
            parsed = urlsplit(url)
            if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.username or parsed.password or parsed.path not in {"", "/"}
                    or parsed.query or parsed.fragment or parsed.port == 0):
                raise ValueError
        return {model: url.rstrip("/") for model, url in routes.items()}
    except (ValueError, TypeError):
        raise ValueError("ORIGINRAG_OLLAMA_MODEL_URLS must map model names to loopback HTTP URLs.") from None


class ModelManager:
    def __init__(self, root):
        self.root = Path(root)
        self.state = Path(os.environ.get("ORIGINRAG_MODELS_DIR", self.root / "runtime/models"))
        self.colibri = Path(os.environ.get("ORIGINRAG_COLIBRI_HOME", self.root / "runtime/colibri"))
        self.model_dir = self.state / MODEL_ID
        self.ollama_url = (
            os.environ.get("ORIGINRAG_OLLAMA_BASE_URL")
            or os.environ.get("PFEMPIRE_OLLAMA_BASE_URL") or "http://127.0.0.1:11434"
        ).rstrip("/")
        self.ollama_model_urls = ollama_model_urls()
        self.lock = threading.RLock()
        self.engine_lock = threading.Lock()
        self.active_requests = 0
        self.job = None
        self.cancel = threading.Event()
        self.response = None
        self.process = None
        self.process_log = None
        self.engine_key = None
        self.engine_url = None
        self.engine_api_key = None
        self.accelerator = None
        self.stats = {}
        self.inventory_cache = None
        self.inventory_at = 0
        self.inventory_lock = threading.Lock()
        self.runtime_reachability = {}
        self.discovered = {}
        atexit.register(self.close)

    def selected(self):
        path = self.state / "selection.json"
        if not path.exists():
            return None
        value = json.loads(path.read_text())
        if value is None:
            return None
        if (not isinstance(value, dict) or value.get("provider") not in {"ollama", "colibri"}
                or not isinstance(value.get("model"), str)
                or value.get("mode") not in {"low_memory", "fast"}):
            raise ValueError("The saved model selection is invalid. Restore the configured default in Models.")
        if value["provider"] == "colibri" and value["model"] not in COLIBRI_MODELS:
            raise ValueError("Unsupported saved Colibri model.")
        return value

    def inventory(self, refresh=False):
        with self.inventory_lock:
            if not refresh and self.inventory_cache is not None and time.monotonic() - self.inventory_at < 10:
                return self.inventory_cache
            error = None
            installed, loaded, reachable, error = self.ollama_inventory(self.ollama_url)
            self.runtime_reachability[self.ollama_url] = reachable
            rows = {row["name"]: {
                "provider": "ollama", "model": row["name"], "name": row["name"],
                "installed": True, "loaded": row["name"] in (loaded or {}),
                "bytes": row.get("size"), "downloadable": False,
                "runtime_reachable": reachable, 'loaded_verified': loaded is not None,
                **self.loaded_details((loaded or {}).get(row["name"])),
            } for row in installed if isinstance(row, dict) and isinstance(row.get("name"), str)}
            for url in sorted(set(self.ollama_model_urls.values())):
                names = {name for name, endpoint in self.ollama_model_urls.items() if endpoint == url}
                extra, extra_loaded, extra_reachable, note = self.ollama_inventory(url)
                self.runtime_reachability[url] = extra_reachable
                found = {row['name']: row for row in extra}
                error = error or note
                for name in names:
                    rows[name] = {
                        "provider": "ollama", "model": name, "name": name,
                        "installed": name in found, "loaded": name in (extra_loaded or {}),
                        "runtime_reachable": extra_reachable, 'loaded_verified': extra_loaded is not None,
                        "bytes": found.get(name, {}).get("size"), "downloadable": False,
                        **self.loaded_details((extra_loaded or {}).get(name)),
                        **({"note": note} if note else {}),
                    }
            for name, size in OLLAMA_DOWNLOADS.items():
                rows.setdefault(name, {"provider": "ollama", "model": name, "name": name,
                                       "installed": False, "loaded": False, "bytes": size,
                                       "downloadable": True, "runtime_reachable": reachable})
            self.inventory_cache = (list(rows.values()), error)
            self.inventory_at = time.monotonic()
            return self.inventory_cache

    @staticmethod
    def ollama_inventory(url):
        try:
            installed = http_json(url + '/api/tags')['models']
            if not isinstance(installed, list):
                raise ValueError('Invalid inventory')
            installed = [row for row in installed if isinstance(row, dict) and isinstance(row.get('name'), str)]
        except (OSError, ValueError, KeyError, TypeError):
            return [], None, False, 'Ollama is not reachable. Start its configured service to check installed models.'
        try:
            loaded = http_json(url + '/api/ps')['models']
            if not isinstance(loaded, list):
                raise ValueError('Invalid running-model status')
            loaded = {row['name']: row for row in loaded if isinstance(row, dict) and isinstance(row.get('name'), str)}
            return installed, loaded, True, None
        except (OSError, ValueError, KeyError, TypeError):
            return installed, None, True, 'Installed models are available, but running-model status could not be checked.'

    @staticmethod
    def loaded_details(row):
        if not row:
            return {}
        size, vram = row.get('size'), row.get('size_vram')
        device = 'Runtime (placement unknown)'
        if isinstance(vram, (int, float)) and isinstance(size, (int, float)) and size > 0:
            device = 'GPU' if vram >= size * .95 else 'GPU + RAM' if vram > 0 else 'CPU / RAM'
        return {'device': device, 'loaded_bytes': size, 'vram_bytes': vram,
                'loaded_context': row.get('context_length')}

    def search(self, query):
        from originrag.model_catalog import search_models
        rows = search_models(query)
        with self.lock:
            for row in rows:
                self.discovered[row['model']] = (time.monotonic(), dict(row))
            while len(self.discovered) > 128:
                del self.discovered[next(iter(self.discovered))]
        return rows

    def colibri_ready(self, model=MODEL_ID):
        return ((self.colibri / "c/coli").is_file()
                and os.access(self.colibri / "c" / COLIBRI_MODELS[model]["engine"], os.X_OK))

    def model_installed(self, model=MODEL_ID):
        path = self.state / model
        if model == "glm-5.3":
            try:
                spec = COLIBRI_MODELS[model]
                manifest = json.loads((path / ".colibri-download.json").read_text())
                files = manifest.get("files", [])
                return (
                    manifest.get("complete") is True
                    and manifest.get("repo") == spec["repo"]
                    and manifest.get("revision") == spec["revision"]
                    and len(files) == 148
                    and sum(row["size"] for row in files) == spec["bytes"]
                    and all(isinstance(row["path"], str) and Path(row["path"]).name == row["path"]
                            and (path / row["path"]).is_file()
                            and (path / row["path"]).stat().st_size == row["size"] for row in files)
                )
            except (OSError, ValueError, KeyError, TypeError):
                return False
        return ((path / ".originrag-complete.json").is_file()
                and (path / "config.json").is_file()
                and (path / "tokenizer.json").is_file()
                and any(path.glob("*.safetensors")))

    def snapshot(self, can_manage, *, search_rows=None):
        from originrag.analysis.ollama_client import DEFAULT_MODEL, env_value
        from originrag.model_hardware import hardware_snapshot, estimate_fit
        from urllib.parse import urlsplit
        hardware = hardware_snapshot()
        default = {"provider": "ollama", "model": env_value(
            "ORIGINRAG_OLLAMA_ANSWER_MODEL", "PFEMPIRE_OLLAMA_ANSWER_MODEL", "HYPORAG_OLLAMA_ANSWER_MODEL",
            "ORIGINRAG_OLLAMA_MODEL", "PFEMPIRE_OLLAMA_MODEL", "HYPORAG_OLLAMA_MODEL",
        ) or DEFAULT_MODEL, "mode": "fast"}
        rows, error = self.inventory()
        shared_status = {}
        try:
            shared = shared_glm_runtime()
            if shared:
                shared_status = http_json(shared["base_url"] + "/health",
                                          api_key=shared["api_key"], timeout=1)
        except (OSError, ValueError, KeyError):
            shared_status = {"error": "Shared GLM service is unavailable."}
        with self.lock:
            selection_error = None
            try:
                selection = self.selected()
            except (ValueError, OSError):
                selection = None
                selection_error = "Saved model settings could not be read. Restore the configured default."
            rows = [dict(row) for row in rows]
            present = {row['model']: row for row in rows}
            for _, discovered in self.discovered.values():
                row = present.get(discovered['model'])
                if row is None:
                    rows.append(dict(discovered))
                else:
                    row.update({key: value for key, value in discovered.items()
                                if key not in {'installed', 'loaded', 'downloadable', 'bytes'}})
                    if not row['installed']:
                        row['bytes'] = discovered['bytes']
            for model, spec in COLIBRI_MODELS.items():
                rows.append({
                    "provider": "colibri", "model": model, "name": spec["name"],
                    "installed": self.model_installed(model),
                    "loaded": bool(self.process and self.process.poll() is None
                                   and self.engine_key and self.engine_key[0] == model),
                    "bytes": spec["bytes"], "downloadable": self.colibri_ready(model) and model != "glm-5.3",
                    "accelerator": self.accelerator if self.engine_key and self.engine_key[0] == model else None,
                    "shared_service": bool(model == "glm-5.3" and os.environ.get("ORIGINRAG_GLM_SERVICE_URL")),
                    "context": spec["context"], "note": spec["note"],
                })
            configured = None if selection_error else selection or default
            if configured and not any((row['provider'], row['model']) == (configured['provider'], configured['model']) for row in rows):
                rows.append({**configured, 'name': configured['model'], 'installed': False,
                             'loaded': False, 'downloadable': False, 'bytes': None})
            active = None
            for row in rows:
                row["default"] = (row["provider"], row["model"]) == (default["provider"], default["model"])
                row["selected"] = bool(configured and row['installed'] and (row["provider"], row["model"]) == (configured["provider"], configured["model"]))
                if row['selected']:
                    active = configured
                row["deletable"] = bool(row["installed"] and not row["default"] and not row["selected"]
                                        and not row["loaded"] and not row.get("shared_service")
                                        and not (row["provider"] == "colibri" and (self.state / row["model"]).is_symlink()))
                if row["model"] == "glm-5.3" and row.get("shared_service"):
                    row.update(loaded=shared_status.get("loaded", False),
                               accelerator=shared_status.get("accelerator"))
                    row["note"] += " Shared service: the host sets the memory budget."
                    if shared_status.get("error"):
                        row["note"] += " " + shared_status["error"]
                row["measurement"] = self.stats.get((row["provider"], row["model"]))
                endpoint = self.ollama_model_urls.get(row['model'], self.ollama_url)
                if row['provider'] == 'ollama' and endpoint in self.runtime_reachability:
                    row.setdefault('runtime_reachable', self.runtime_reachability[endpoint])
                local = row['provider'] == 'colibri' or urlsplit(endpoint).hostname in {'localhost', '127.0.0.1', '::1'}
                row['local_runtime'] = local
                row['fit'] = estimate_fit(row, hardware, local_runtime=local)
                if row['provider'] == 'ollama' and row['model'] in RECOMMENDATIONS:
                    purpose, task, rank = RECOMMENDATIONS[row['model']]
                    row.update(purpose=purpose, recommendation_task=task, recommendation_rank=rank)
            for task in ('general', 'code'):
                candidates = [row for row in rows if row.get('recommendation_task') == task
                              and row['fit']['status'] in {'fits', 'loaded'}]
                if candidates:
                    max(candidates, key=lambda row: (
                        row['fit']['device'] not in {None, 'RAM / CPU'}, row['recommendation_rank'],
                    ))['recommended'] = True
            target_unavailable = bool(configured and any(
                row['provider'] == configured['provider'] and row['model'] == configured['model']
                and row.get('runtime_reachable') is False for row in rows))
            return {
                "can_manage": can_manage, "selection": selection, "default": default,
                "effective_selection": active, "configured_selection": configured,
                "selection_status": 'invalid' if selection_error else 'installed' if active else 'unavailable' if target_unavailable else 'missing',
                "models": rows, "hardware": hardware,
                "search_models": [row['model'] for row in search_rows] if search_rows is not None else None,
                "job": dict(self.job) if self.job else None,
                "active_requests": self.active_requests, "ollama_error": error,
                "selection_error": selection_error, "colibri_available": self.colibri_ready(),
            }

    def change(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Expected an object.")
        action = payload.get("action")
        if not isinstance(action, str):
            raise ValueError("A model action is required.")
        if action == "cancel":
            with self.lock:
                if self.job and self.job["status"] == "running":
                    if self.job["action"] != "download":
                        raise ValueError("Only downloads can be cancelled from this panel.")
                    self.cancel.set()
                    self.job["message"] = "Cancelling…"
                    response = self.response
                else:
                    response = None
            if response:
                threading.Thread(target=response.close, daemon=True).start()
            return
        if action not in {"download", "delete", "select", "default", "benchmark", "stop"}:
            raise ValueError("Unknown model action.")
        provider, model = payload.get("provider"), payload.get("model")
        mode = payload.get("mode", "fast")
        if not isinstance(mode, str) or mode not in {"low_memory", "fast"}:
            raise ValueError("Choose low memory or faster inference.")
        if action not in {"default", "stop"}:
            if not isinstance(provider, str) or not isinstance(model, str) or len(model) > 200:
                raise ValueError("Choose a model from the catalogue.")
            if provider == "colibri":
                if model not in COLIBRI_MODELS or (action != "delete" and not self.colibri_ready(model)):
                    raise ValueError("This Colibri model or runtime is not available on this host.")
                installed = self.model_installed(model)
            elif provider == "ollama":
                rows, error = self.inventory(refresh=True)
                installed = any(row["model"] == model and row["installed"] for row in rows)
                target = next((row for row in rows if row['model'] == model), {})
                if error and not installed and not target.get('runtime_reachable'):
                    raise ValueError(error)
                if not installed and model not in OLLAMA_DOWNLOADS:
                    with self.lock:
                        discovered = self.discovered.get(model)
                    if (action != 'download' or not discovered
                            or time.monotonic() - discovered[0] > 1800
                            or payload.get('catalog_digest') != discovered[1]['catalog_digest']):
                        raise ValueError("Search for this model in the catalogue before downloading it.")
                    from originrag.model_catalog import model_metadata, invalidate_model
                    current = model_metadata(model)
                    if current['catalog_digest'] != discovered[1]['catalog_digest']:
                        invalidate_model(model)
                        with self.lock:
                            self.discovered.pop(model, None)
                        raise ValueError('The model download changed. Search again and review its new size.')
            else:
                raise ValueError("Choose Ollama or Colibri.")
            if action != "download" and not installed:
                raise ValueError("Download this model first.")
            if action == "download" and installed:
                raise ValueError("This model is already installed.")
            if action == "download" and model == "glm-5.3":
                raise ValueError("GLM-5.3 is installed by the host with Colibri's resumable downloader.")
            if action == "download" and payload.get("confirmed") is not True:
                raise ValueError("Confirm the model download and disk usage.")
            if action == "delete":
                if payload.get("confirmed") is not True:
                    raise ValueError("Confirm model deletion. A later use requires downloading it again.")
                view = self.snapshot(True)
                row = next((item for item in view["models"]
                            if item["provider"] == provider and item["model"] == model), None)
                if not row or not row["deletable"]:
                    raise ValueError("The active, configured default, loaded or externally managed model cannot be deleted here.")
        with self.lock:
            if self.job and self.job["status"] == "running":
                raise ValueError("Another model operation is running. Wait or cancel it first.")
            if self.active_requests:
                raise ValueError("A model request is running. Wait for it to finish before changing models.")
            if action == "delete" and not any(
                row["provider"] == provider and row["model"] == model and row["deletable"]
                for row in self.snapshot(True)["models"]
            ):
                raise ValueError("This model is active or protected. Refresh the model list before deleting it.")
            self.job = {"id": uuid.uuid4().hex, "status": "running", "action": action,
                        "message": "Preparing…", "completed": 0, "total": 0}
            self.cancel = threading.Event()
        threading.Thread(target=self._work, args=(action, provider, model, mode), daemon=True).start()

    def progress(self, message, completed=0, total=0):
        self.check_cancel()
        with self.lock:
            self.job.update(message=message, completed=completed, total=total)

    def check_cancel(self):
        if self.cancel.is_set():
            raise InterruptedError("Model operation cancelled.")

    def _work(self, action, provider, model, mode):
        try:
            if action == "download":
                if provider == "ollama":
                    self.download_ollama(model)
                elif model != MODEL_ID:
                    self.download_container(model)
                else:
                    self.download_colibri()
            elif action == "default":
                atomic_json(self.state / "selection.json", None)
                self.close()
            elif action == "stop":
                if (self.selected() or {}).get("provider") == "colibri":
                    raise ValueError("Restore the configured default before stopping Colibri.")
                self.close()
            elif action == "delete":
                self.delete_model(provider, model)
            else:
                selection = {"provider": provider, "model": model, "mode": mode}
                self.progress("Starting the selected runtime…")
                runtime = self.runtime(selection)
                self.check_cancel()
                if action == "select":
                    atomic_json(self.state / "selection.json", selection)
                    if provider == "ollama":
                        self.close()
                else:
                    self.progress("Measuring a short test response…")
                    from originrag.analysis.ollama_client import generate_text
                    token = CURRENT_RUNTIME.set(runtime)
                    try:
                        result = generate_text(
                            "Explain in three short sentences why a library catalogue is useful.",
                            timeout=1800 if model == "glm-5.3" else 120, num_predict=96, num_ctx=2048,
                        )
                        if result.error or not result.text:
                            raise ValueError(result.error or "No text was returned.")
                    finally:
                        CURRENT_RUNTIME.reset(token)
            self.check_cancel()
            with self.lock:
                self.inventory_at = 0
                self.job.update(status="done", message="Done")
        except Exception as exc:
            with self.lock:
                self.job.update(status="cancelled" if self.cancel.is_set() else "failed",
                                message="Cancelled" if self.cancel.is_set() else str(exc)[:500])
        finally:
            self.response = None

    def download_ollama(self, model):
        self.progress("Downloading through Ollama…")
        req = urllib.request.Request(
            self.ollama_model_urls.get(model, self.ollama_url) + "/api/pull",
            data=json.dumps({"model": model, "stream": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        deadline = time.monotonic() + 7200
        with urllib.request.urlopen(req, timeout=60) as response:
            self.response = response
            for line in response:
                self.check_cancel()
                if time.monotonic() > deadline:
                    raise TimeoutError("Download exceeded two hours. Retry to resume.")
                item = json.loads(line)
                if item.get("error"):
                    raise ValueError(str(item["error"]))
                self.progress(str(item.get("status", "Downloading…")),
                              int(item.get("completed", 0)), int(item.get("total", 0)))
                if item.get("status") == "success":
                    return
        raise ValueError("Download ended before Ollama confirmed success. Retry to resume.")

    def delete_model(self, provider, model):
        self.progress("Removing the unused model…")
        if provider == "ollama":
            request = urllib.request.Request(
                self.ollama_model_urls.get(model, self.ollama_url) + "/api/delete",
                data=json.dumps({"model": model}).encode(), method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                response.read(4096)
        else:
            target = self.state / model
            if (model not in COLIBRI_MODELS or target.is_symlink()
                    or target.resolve().parent != self.state.resolve() or not target.is_dir()):
                raise ValueError("Only an app-managed model directory can be removed here.")
            shutil.rmtree(target)

    def download_container(self, model):
        spec = COLIBRI_MODELS[model]
        self.state.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.state).free < spec["bytes"] + 5_000_000_000:
            raise ValueError("Not enough free disk space for this model plus a 5 GB reserve.")
        self.progress("Checking the pinned model manifest…")
        base = f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/"
        manifest = http_json(
            f"https://huggingface.co/api/models/{spec['repo']}/revision/{spec['revision']}?blobs=true",
            timeout=30,
        )
        if manifest.get("sha") != spec["revision"]:
            raise ValueError("The model manifest does not match the pinned revision.")
        files = [row for row in manifest["siblings"] if re.fullmatch(
            r"(?:model-[0-9]+\.safetensors|[A-Za-z0-9_.-]+\.json|chat_template\.jinja|LICENSE)",
            row["rfilename"])]
        total = sum(row["size"] for row in files)
        if not 0 < total <= spec["bytes"] + 1_000_000:
            raise ValueError("Unexpected model size in download manifest.")
        target = self.state / (model + "-download")
        target.mkdir(exist_ok=True)
        completed = 0
        deadline = time.monotonic() + 7200
        for row in files:
            self.check_cancel()
            path = target / row["rfilename"]
            expected = row.get("lfs", {}).get("sha256")
            if path.suffix == ".safetensors" and not expected:
                raise ValueError("A weight shard has no SHA-256 checksum.")
            if path.exists() and path.stat().st_size == row["size"] and (
                not expected or self.file_sha(path) == expected
            ):
                completed += row["size"]
                continue
            temporary = path.with_name(path.name + ".partial")
            count = 0
            digest = hashlib.sha256()
            with urllib.request.urlopen(base + row["rfilename"], timeout=30) as response, temporary.open("wb") as out:
                self.response = response
                while block := response.read(1024 * 1024):
                    self.check_cancel()
                    count += len(block)
                    if count > row["size"] or time.monotonic() > deadline:
                        raise ValueError("Model download exceeded its size or time limit.")
                    digest.update(block)
                    out.write(block)
                    self.progress("Downloading " + row["rfilename"], completed + count, total)
            self.response = None
            if count != row["size"] or (expected and digest.hexdigest() != expected):
                raise ValueError("Model checksum verification failed.")
            temporary.replace(path)
            completed += count
        self.check_cancel()
        atomic_json(target / ".originrag-complete.json", {"repo": spec["repo"], "revision": spec["revision"]})
        target.rename(self.state / model)

    @staticmethod
    def file_sha(path):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        return digest.hexdigest()

    def download_colibri(self):
        self.state.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.state).free < 15_000_000_000:
            raise ValueError("At least 15 GB free disk space is required for download and conversion.")
        source = self.state / (MODEL_FILE + ".partial")
        self.progress("Downloading verified OLMoE weights…", 0, MODEL_SIZE)
        digest = hashlib.sha256()
        count = 0
        deadline = time.monotonic() + 7200
        with urllib.request.urlopen(MODEL_URL, timeout=30) as response, source.open("wb") as handle:
            self.response = response
            while block := response.read(1024 * 1024):
                self.check_cancel()
                count += len(block)
                if count > MODEL_SIZE or time.monotonic() > deadline:
                    raise ValueError("Model download exceeded its size or time limit.")
                handle.write(block)
                digest.update(block)
                self.progress("Downloading OLMoE…", count, MODEL_SIZE)
        self.response = None
        if count != MODEL_SIZE or digest.hexdigest() != MODEL_SHA:
            raise ValueError("Model checksum verification failed. Retry the download.")
        self.progress("Converting the model for Colibri…")
        target = self.state / ("olmoe-build-" + uuid.uuid4().hex)
        python = self.engine_python()
        command = [python, str(self.colibri / "c/tools/convert_gguf_to_olmoe.py"),
                   "--input", str(source), "--output", str(target), "--flush-every", "64"]
        try:
            with (self.state / "conversion.log").open("w") as log:
                process = subprocess.Popen(command, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 900
                    while process.poll() is None:
                        self.check_cancel()
                        if time.monotonic() > deadline:
                            raise TimeoutError("Model conversion exceeded 15 minutes.")
                        time.sleep(0.25)
                    if process.returncode:
                        raise ValueError("Conversion failed. The host needs numpy and safetensors; see conversion.log.")
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
            self.check_cancel()
            atomic_json(target / ".originrag-complete.json", {"source_sha256": MODEL_SHA})
            if self.model_dir.exists():
                raise ValueError("A model directory already exists. Ask the host to inspect it before retrying.")
            target.rename(self.model_dir)
            source.unlink()
        finally:
            if target.exists():
                shutil.rmtree(target)

    def runtime(self, selection):
        runtime = dict(selection, manager=self)
        if selection["provider"] == "ollama":
            runtime["base_url"] = self.ollama_model_urls.get(selection["model"], self.ollama_url)
        elif selection["model"] == "glm-5.3" and os.environ.get("ORIGINRAG_GLM_SERVICE_URL"):
            shared = shared_glm_runtime()
            http_json(shared["base_url"] + "/health", api_key=shared["api_key"], timeout=5)
            runtime.update(shared)
        else:
            runtime["base_url"] = self.start_colibri(selection["mode"], selection["model"])
            runtime["api_key"] = self.engine_api_key
        return runtime

    def engine_python(self):
        configured = os.environ.get("ORIGINRAG_COLIBRI_PYTHON")
        isolated = self.colibri / ".venv/bin/python"
        return configured or (str(isolated) if isolated.is_file() else sys.executable)

    def start_colibri(self, mode, model=MODEL_ID, *, gpu_policy="auto"):
        with self.engine_lock:
            if self.process and self.process.poll() is None and self.engine_key == (model, mode):
                return self.engine_url
            self._close_engine()
            if not self.colibri_ready(model) or not self.model_installed(model):
                raise ValueError("Colibri or its downloaded model is unavailable.")
            spec = COLIBRI_MODELS[model]
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            self.state.mkdir(parents=True, exist_ok=True)
            self.process_log = (self.state / "colibri.log").open("a")
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("COLI_", "OMP_")) and key not in {
                       "RAM_GB", "CTX", "CAP", "TOPK", "TOPP", "PILOT", "KV_SLOTS", "SNAP", "SERVE"}}
            env.update(OMP_NUM_THREADS="4", PILOT="0")
            resource_args = ["--cap", str(spec[mode])]
            if spec.get("ram_budget"):
                from originrag.model_hardware import choose_gpu
                gpu = choose_gpu() if gpu_policy == "auto" else None
                self.accelerator = "cuda:" + gpu if gpu is not None else "cpu"
                env.update(COLI_NO_OMP_TUNE="1", OMP_NUM_THREADS="8")
                resource_args = ["--ram", str(spec[mode]), "--gpu", gpu if gpu is not None else "none",
                                 "--auto-tier"]
                if gpu is not None:
                    env["CUDA_DENSE"] = "1"
                    resource_args += ["--vram", "16"]
            self.engine_api_key = secrets.token_urlsafe(32)
            env["COLI_API_KEY"] = self.engine_api_key
            self.engine_url = f"http://127.0.0.1:{port}"
            self.process = subprocess.Popen(
                [self.engine_python(),
                 str(self.colibri / "c/coli"), "serve", "--model", str(self.state / model),
                 "--model-id", model, "--host", "127.0.0.1", "--port", str(port),
                 *resource_args, "--ctx", str(spec["context"]),
                 "--kv-slots", "1", "--max-queue", "2", "--queue-timeout", "30",
                 "--policy", "quality", "--no-tune-profile"],
                cwd=self.colibri / "c", env=env, stdout=self.process_log, stderr=self.process_log,
                start_new_session=True,
            )
            startup_timeout = 900 if spec.get("ram_budget") else 90
            deadline = time.monotonic() + startup_timeout
            try:
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        raise ValueError("Colibri failed to start. See runtime/models/colibri.log.")
                    try:
                        http_json(self.engine_url + "/health", timeout=1, api_key=self.engine_api_key)
                        self.engine_key = (model, mode)
                        return self.engine_url
                    except (OSError, ValueError):
                        time.sleep(0.2)
                raise TimeoutError(f"Colibri did not become ready within {startup_timeout} seconds.")
            except BaseException:
                self._close_engine()
                raise

    def _close_engine(self):
        if self.process and self.process.poll() is None:
            import signal
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
        self.process = None
        self.engine_key = None
        self.accelerator = None
        if self.process_log:
            self.process_log.close()
            self.process_log = None

    def close(self):
        with self.engine_lock:
            self._close_engine()

    @contextmanager
    def session(self, *, override=None):
        if override is not None:
            if (not isinstance(override, dict) or set(override) != {"provider", "model", "mode"}
                    or override["provider"] != "ollama" or not isinstance(override["model"], str)
                    or override["mode"] != "fast"):
                raise ValueError("Choose an installed Ollama model for this request.")
            rows, error = self.inventory(refresh=True)
            if not any(row["model"] == override["model"] and row["installed"] for row in rows):
                raise ValueError("Install the requested model through Models before using it.")
        with self.lock:
            if self.job and self.job["status"] == "running" and self.job["action"] != "download":
                raise ValueError("A model is being switched or tested. Please retry in a moment.")
            selection = override or self.selected()
            self.active_requests += 1
        token = None
        try:
            token = CURRENT_RUNTIME.set(self.runtime(selection) if selection else None)
            yield selection
        finally:
            if token is not None:
                CURRENT_RUNTIME.reset(token)
            with self.lock:
                self.active_requests -= 1

    def record(self, runtime, tokens, seconds, metric):
        if seconds > 0 and tokens > 0:
            with self.lock:
                self.stats[(runtime["provider"], runtime["model"])] = {
                    "tokens_per_second": round(tokens / seconds, 2),
                    "tokens": tokens, "seconds": round(seconds, 3), "metric": metric,
                    "measured_at": int(time.time()), "mode": runtime["mode"],
                }


def colibri_generate(prompt, runtime, *, temperature, timeout, num_predict, format=None):
    body = {
        "model": runtime["model"], "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature, "max_tokens": num_predict, "stream": False,
    }
    if format:
        raise ValueError("This Colibri model does not support constrained JSON output. Choose an Ollama model for this task.")
    start = time.monotonic()
    try:
        data = http_json(runtime["base_url"] + "/v1/chat/completions", body, timeout=timeout,
                         api_key=runtime.get("api_key"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(65536)).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            detail = ""
        raise ValueError(f"Colibri rejected the request ({exc.code}). {detail}") from exc
    elapsed = time.monotonic() - start
    choice = data["choices"][0]
    if choice.get("finish_reason") == "length":
        raise ValueError("Colibri reached its context or output limit. Choose a model with a larger context; no evidence was silently removed.")
    text = choice["message"].get("content")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Colibri returned no answer.")
    usage = data.get("usage", {})
    runtime["manager"].record(runtime, int(usage.get("completion_tokens", 0)), elapsed, "request")
    return text, {
        "runtime": "colibri", "model": runtime["model"],
        "eval_count": usage.get("completion_tokens", 0),
        "prompt_eval_count": usage.get("prompt_tokens", 0),
        "total_duration": round(elapsed * 1e9),
    }
