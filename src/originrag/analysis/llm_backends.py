from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from originrag.paths import load_project_env
from originrag.cancellation import current_cancellation, run_process, urlopen


REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_LOADED = False


@dataclass
class LLMResult:
    backend: str
    answer: str | None
    error: str | None = None
    usage: dict[str, int] | None = None


def generate_answer(prompt: str, backend: str = "none") -> LLMResult:
    _ensure_local_env_loaded()
    backend_key = (backend or "none").strip().lower()
    if backend_key == "none":
        return LLMResult(backend="none", answer=None)
    if backend_key == "ollama":
        return _call_ollama(prompt)
    if backend_key == "deepseek":
        return _call_deepseek(prompt)
    if backend_key == "qwen":
        return _call_qwen(prompt)
    if backend_key == "queue":
        return _call_queue(prompt)
    return LLMResult(
        backend=backend_key,
        answer=None,
        error=(
            f"Unsupported backend: {backend}. "
            "Supported backends are: none, ollama, deepseek, qwen, queue."
        ),
        )


def _ensure_local_env_loaded() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    load_project_env(REPO_ROOT / ".env")
    _ENV_LOADED = True


def _call_ollama(prompt: str) -> LLMResult:
    base_url = _env_value("PFEMPIRE_OLLAMA_BASE_URL", "HYPORAG_OLLAMA_BASE_URL") or "http://localhost:11434"
    model = _env_value("PFEMPIRE_OLLAMA_MODEL", "HYPORAG_OLLAMA_MODEL") or _default_qwen_model()
    return _call_ollama_generate(prompt, backend="ollama", base_url=base_url, model=model)


def _call_deepseek(prompt: str) -> LLMResult:
    base_url = (
        _env_value("PFEMPIRE_DEEPSEEK_BASE_URL", "HYPORAG_DEEPSEEK_BASE_URL")
        or _env_value("PFEMPIRE_OLLAMA_BASE_URL", "HYPORAG_OLLAMA_BASE_URL")
        or "http://localhost:11434"
    )
    model = _env_value("PFEMPIRE_DEEPSEEK_MODEL", "HYPORAG_DEEPSEEK_MODEL") or "deepseek-r1:8b"
    return _call_ollama_generate(prompt, backend="deepseek", base_url=base_url, model=model)


def _call_qwen(prompt: str) -> LLMResult:
    base_url = (
        _env_value("PFEMPIRE_QWEN_BASE_URL", "HYPORAG_QWEN_BASE_URL")
        or _env_value("PFEMPIRE_OLLAMA_BASE_URL", "HYPORAG_OLLAMA_BASE_URL")
        or "http://localhost:11434"
    )
    model = _env_value("PFEMPIRE_QWEN_MODEL", "HYPORAG_QWEN_MODEL") or _default_qwen_model()
    return _call_ollama_generate(prompt, backend="qwen", base_url=base_url, model=model)


def _call_queue(prompt: str) -> LLMResult:
    queue_dir = Path(_env_value("PFEMPIRE_LLM_QUEUE_DIR", "HYPORAG_LLM_QUEUE_DIR") or "outputs/llm_queue")
    model = (
        _env_value("PFEMPIRE_QUEUE_MODEL", "HYPORAG_QUEUE_MODEL")
        or _env_value("PFEMPIRE_QWEN_MODEL", "HYPORAG_QWEN_MODEL")
        or _env_value("PFEMPIRE_DEEPSEEK_MODEL", "HYPORAG_DEEPSEEK_MODEL")
        or _default_qwen_model()
    )
    timeout_seconds = _llm_timeout_seconds()
    job_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:10]}"
    jobs_dir = queue_dir / "jobs"
    results_dir = queue_dir / "results"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    job_path = jobs_dir / f"{job_id}.json"
    result_path = results_dir / f"{job_id}.json"
    cancel_path = jobs_dir / f"{job_id}.cancel"
    _write_json_atomic(
        job_path,
        {
            "job_id": job_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "backend": "queue",
            "provider": "ollama",
            "model": model,
            "prompt": prompt,
            "status": "pending",
        },
    )
    token = current_cancellation.get()
    release = token.register(lambda: cancel_path.touch()) if token else lambda: None
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            if token and token.event.is_set():
                try:
                    job_path.replace(jobs_dir / f"{job_id}.cancelled")
                except FileNotFoundError:
                    pass
                else:
                    token.check()
            if result_path.exists():
                if token:
                    token.check()
                result = _read_json(result_path)
                answer = str(result.get("answer") or "").strip()
                error = result.get("error")
                return LLMResult(
                    backend="queue", answer=answer or None,
                    error=str(error) if error else None,
                )
            time.sleep(0.2 if token else 1.0)
        if token:
            token.check()
    finally:
        release()
    return LLMResult(
        backend="queue",
        answer=None,
        error=f"queue timed out after {timeout_seconds}s waiting for job {job_id} at {result_path}",
    )


def _call_ollama_generate(prompt: str, backend: str, base_url: str, model: str) -> LLMResult:
    timeout_seconds = _llm_timeout_seconds()
    if _force_cli():
        return _call_ollama_cli(
            prompt=prompt,
            backend=backend,
            model=model,
            timeout_seconds=timeout_seconds,
        )
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_ctx": _llm_num_ctx(),
            "num_predict": _llm_num_predict(),
        },
    }
    try:
        response = _post_json(
            f"{base_url.rstrip('/')}/api/generate",
            payload,
            headers={},
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        if _use_cli_fallback():
            cli_result = _call_ollama_cli(
                prompt=prompt,
                backend=backend,
                model=model,
                timeout_seconds=timeout_seconds,
            )
            if cli_result.answer:
                cli_result.error = f"http_fallback_used: {exc}"
                return cli_result
            return LLMResult(
                backend=backend,
                answer=None,
                error=(
                    f"{backend} local HTTP call failed for model {model} "
                    f"after timeout={timeout_seconds}s: {exc}; "
                    f"CLI fallback failed: {cli_result.error}"
                ),
            )
        return LLMResult(
            backend=backend,
            answer=None,
            error=f"{backend} local call failed for model {model} after timeout={timeout_seconds}s: {exc}",
        )
    answer = str(response.get("response") or "").strip()
    return LLMResult(
        backend=backend,
        answer=answer or None,
        error=None if answer else f"{backend} returned no response for model {model}.",
        usage={
            key: int(response[key])
            for key in (
                "prompt_eval_count",
                "eval_count",
                "prompt_eval_duration",
                "eval_duration",
                "total_duration",
            )
            if response.get(key) is not None
        }
        or None,
    )


def _llm_timeout_seconds() -> int:
    raw_value = _env_value("PFEMPIRE_LLM_TIMEOUT_SECONDS", "HYPORAG_LLM_TIMEOUT_SECONDS") or "600"
    try:
        timeout = int(raw_value)
    except ValueError:
        return 600
    return max(timeout, 30)


def _llm_num_predict() -> int:
    raw_value = _env_value("PFEMPIRE_OLLAMA_NUM_PREDICT", "HYPORAG_OLLAMA_NUM_PREDICT") or "2200"
    try:
        num_predict = int(raw_value)
    except ValueError:
        return 2200
    return max(num_predict, 256)


def _llm_num_ctx() -> int:
    raw_value = _env_value("PFEMPIRE_OLLAMA_NUM_CTX", "HYPORAG_OLLAMA_NUM_CTX") or "65536"
    try:
        num_ctx = int(raw_value)
    except ValueError:
        return 65536
    return max(num_ctx, 2048)


def _default_qwen_model() -> str:
    return "qwen3:30b"


def _use_cli_fallback() -> bool:
    raw_value = _env_value("PFEMPIRE_OLLAMA_CLI_FALLBACK", "HYPORAG_OLLAMA_CLI_FALLBACK") or "1"
    return raw_value.strip().lower() not in {"0", "false", "no", "off"}


def _force_cli() -> bool:
    raw_value = _env_value("PFEMPIRE_OLLAMA_FORCE_CLI", "HYPORAG_OLLAMA_FORCE_CLI") or "0"
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _call_ollama_cli(
    prompt: str,
    backend: str,
    model: str,
    timeout_seconds: int,
) -> LLMResult:
    try:
        completed = (run_process if current_cancellation.get() else subprocess.run)(
            ["ollama", "run", model],
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception as exc:
        return LLMResult(
            backend=backend,
            answer=None,
            error=f"{backend} ollama CLI fallback failed for model {model}: {exc}",
        )
    answer = clean_ollama_cli_output(completed.stdout)
    if completed.returncode != 0:
        stderr = clean_ollama_cli_output(completed.stderr)
        return LLMResult(
            backend=backend,
            answer=answer or None,
            error=f"{backend} ollama CLI returned {completed.returncode}: {stderr}",
        )
    return LLMResult(
        backend=backend,
        answer=answer or None,
        error=None if answer else f"{backend} ollama CLI returned no response for model {model}.",
    )


def clean_ollama_cli_output(text: str) -> str:
    no_ansi = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text or "")
    no_controls = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", no_ansi)
    lines = []
    for line in no_controls.splitlines():
        clean = line.strip()
        if clean and clean not in {"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}:
            lines.append(clean)
    cleaned = "\n".join(lines).strip()
    thinking_marker = "...done thinking."
    if thinking_marker in cleaned:
        return cleaned.split(thinking_marker, 1)[1].strip()
    return cleaned


def _post_json(url: str, payload: dict, headers: dict[str, str], timeout_seconds: int) -> dict:
    encoded = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    tmp_path.replace(path)


def _env_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None
