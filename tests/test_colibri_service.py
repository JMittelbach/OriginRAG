from contextlib import contextmanager
import json
import io
import subprocess
import socket
import sys
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

from originrag import colibri_service as service, model_hardware as hw, model_runtime as runtime


@pytest.mark.parametrize("free,load,jobs,expected", [
    ("45000", "0", "", "0"),
    ("1000", "0", "", None),
    ("45000", "80", "", None),
    ("45000", "0", "GPU-1, 42", None),
    ("N/A", "0", "", None),
])
def test_gpu_admission(monkeypatch, free, load, jobs, expected):
    def run(command, **kwargs):
        data = jobs if "compute-apps" in command[1] else f"0, GPU-1, {free}, {load}"
        return SimpleNamespace(stdout=data)
    monkeypatch.setattr(hw.subprocess, "run", run)
    assert hw.choose_gpu() == expected


def test_unknown_gpu_falls_back_to_cpu(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("nvidia-smi", 5)
    monkeypatch.setattr(hw.subprocess, "run", fail)
    assert hw.choose_gpu() is None
    assert hw.foreign_gpu_jobs(123)


def test_foreign_jobs_exclude_our_process_group(monkeypatch):
    monkeypatch.setattr(hw.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="12\n"))
    monkeypatch.setattr(hw.os, "getpgid", lambda pid: 99)
    assert not hw.foreign_gpu_jobs(99)
    assert hw.foreign_gpu_jobs(98)


@pytest.fixture
def config(tmp_path):
    return {name: str(tmp_path / name) for name in ["root", "models", "state", "colibri"]}


def test_key_private_and_stable(config):
    first = service.key_for(config)
    assert len(first) >= 32
    assert service.key_for(config) == first
    from pathlib import Path
    path = Path(config["state"]) / "api.key"
    assert path.stat().st_mode & 0o077 == 0
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        service.key_for(config)


def test_startup_falls_back_without_retrying_generation(config, monkeypatch):
    gateway = service.Gateway(config)
    calls = []
    def start(mode, model, *, gpu_policy):
        calls.append(gpu_policy)
        if gpu_policy == "auto":
            raise ValueError("GPU became busy")
        return "http://127.0.0.1:1"
    monkeypatch.setattr(gateway.manager, "start_colibri", start)
    try:
        with pytest.raises(RuntimeError, match="generation"):
            with gateway.session() as (url, _):
                assert url.endswith(":1")
                raise RuntimeError("generation failed")
        assert calls == ["auto", "cpu"]
        assert not gateway.active
        assert gateway.lock.acquire(blocking=False)
        gateway.lock.release()
    finally:
        gateway.close()


def test_bounded_admission(config):
    gateway = service.Gateway(config)
    try:
        gateway.admission.acquire()
        gateway.admission.acquire()
        with pytest.raises(RuntimeError, match="busy"):
            with gateway.session():
                pytest.fail("must not start")
    finally:
        gateway.admission.release()
        gateway.admission.release()
        gateway.close()


@pytest.fixture
def endpoint():
    gateway = SimpleNamespace(status=lambda: {"loaded": False, "model": "glm-5.3"})
    server = service.Server(("127.0.0.1", 0), gateway, "test-secret-" * 4)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    worker.join()


def test_api_requires_key_and_rejects_browser_origins(endpoint):
    server, url = endpoint
    for headers, code in [
        ({}, 401),
        ({"Authorization": "Bearer " + server.key, "Origin": "https://evil.example"}, 403),
        ({"Authorization": "Bearer " + server.key, "Host": "evil.example"}, 403),
    ]:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(url + "/health", headers=headers))
        assert error.value.code == code
    with urllib.request.urlopen(urllib.request.Request(
        url + "/v1/models", headers={"Authorization": "Bearer " + server.key}
    )) as response:
        assert json.load(response)["data"][0]["id"] == "glm-5.3"


@pytest.mark.parametrize("body", [
    [], {}, {"model": "another", "messages": [{}]},
    {"model": "glm-5.3", "messages": []},
    {"model": "glm-5.3", "messages": [{}], "stream": "yes"},
])
def test_invalid_requests_do_not_start_model(endpoint, body):
    server, url = endpoint
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(urllib.request.Request(
            url + "/v1/chat/completions", data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + server.key},
        ))
    assert error.value.code == 400


def test_shared_endpoint_uses_private_loopback_key(tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_text("a" * 40)
    key.chmod(0o600)
    monkeypatch.setenv("ORIGINRAG_GLM_API_KEY_FILE", str(key))
    monkeypatch.setenv("ORIGINRAG_GLM_SERVICE_URL", "http://127.0.0.1:8766")
    assert runtime.shared_glm_runtime()["api_key"] == "a" * 40
    for invalid in ["http://evil.example:80", "http://127.0.0.1:80/path",
                    "http://user@localhost:80", "http://localhost:80?x=1"]:
        monkeypatch.setenv("ORIGINRAG_GLM_SERVICE_URL", invalid)
        with pytest.raises(ValueError, match="loopback"):
            runtime.shared_glm_runtime()


def test_partial_glm_download_not_available(tmp_path, monkeypatch):
    monkeypatch.delenv("ORIGINRAG_MODELS_DIR", raising=False)
    manager = runtime.ModelManager(tmp_path)
    model = manager.state / "glm-5.3"
    model.mkdir(parents=True)
    runtime.atomic_json(model / ".colibri-download.json", {"complete": False})
    assert not manager.model_installed("glm-5.3")
    runtime.atomic_json(model / ".colibri-download.json", {
        "complete": True, "repo": runtime.COLIBRI_MODELS["glm-5.3"]["repo"],
        "revision": "incorrect", "files": [],
    })
    assert not manager.model_installed("glm-5.3")


def test_model_manager_uses_shared_service_without_second_engine(tmp_path, monkeypatch):
    manager = runtime.ModelManager(tmp_path)
    monkeypatch.setenv("ORIGINRAG_GLM_SERVICE_URL", "http://127.0.0.1:8766")
    monkeypatch.setattr(runtime, "shared_glm_runtime",
                        lambda: {"base_url": "http://127.0.0.1:8766", "api_key": "a" * 40})
    monkeypatch.setattr(runtime, "http_json", lambda *a, **k: {"loaded": False})
    result = manager.runtime({"provider": "colibri", "model": "glm-5.3", "mode": "fast"})
    assert result["base_url"] == "http://127.0.0.1:8766"
    assert manager.process is None


def test_manage_allowlist_does_not_grant_downloads(monkeypatch):
    reader = SimpleNamespace(role="user", username="JannesM")
    monkeypatch.setenv("ORIGINRAG_MODEL_MANAGE_USERS", "jannesm")
    monkeypatch.setenv("ORIGINRAG_MODEL_DOWNLOAD_USERS", "")
    assert runtime.model_manage_allowed(reader)
    assert not runtime.model_download_allowed(reader)
    assert not runtime.model_manage_allowed(SimpleNamespace(role="user", username="other"))
    assert not runtime.model_manage_allowed(None)


@pytest.mark.parametrize("stream", [False, True])
def test_gateway_preserves_requests_and_responses(endpoint, monkeypatch, stream):
    server, url = endpoint
    original = urllib.request.urlopen
    sent = []
    @contextmanager
    def session():
        yield "http://127.0.0.1:9", "engine-secret"
    server.gateway.session = session
    expected = b'data: {"choices":[{"delta":{"content":"42"}}]}\n\ndata: [DONE]\n\n' if stream else b'{"choices":[{"message":{"content":"42"}}]}'
    @contextmanager
    def proxy(request, **kwargs):
        assert request.full_url.startswith("http://127.0.0.1:9/")
        sent.append((json.loads(request.data), request.get_header("Authorization")))
        response = io.BytesIO(expected)
        response.status = 200
        response.headers = {"Content-Type": "text/event-stream" if stream else "application/json"}
        yield response
    monkeypatch.setattr(service, "urlopen", proxy)
    body = {"model": "glm-5.3", "messages": [{"role": "user", "content": "17+25?"}],
            "stream": stream, "temperature": 0, "max_tokens": 16}
    with original(urllib.request.Request(
        url + "/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + server.key},
    )) as response:
        assert response.read() == expected
    assert sent == [(body, "Bearer engine-secret")]


def test_disconnect_cancels_upstream_and_releases_session(endpoint, monkeypatch):
    server, _ = endpoint
    entered, released = threading.Event(), threading.Event()
    @contextmanager
    def session():
        try:
            yield "http://127.0.0.1:9", "engine-secret"
        finally:
            released.set()
    @contextmanager
    def wait_for_cancel(*args, **kwargs):
        entered.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            service.checkpoint()
            time.sleep(0.01)
        pytest.fail("Disconnected client did not cancel its upstream request")
        yield
    server.gateway.session = session
    monkeypatch.setattr(service, "urlopen", wait_for_cancel)
    body = json.dumps({"model": "glm-5.3", "messages": [{"role": "user", "content": "test"}]}).encode()
    client = socket.create_connection(server.server_address)
    client.sendall(
        (f"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\n"
         f"Authorization: Bearer {server.key}\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body)
    assert entered.wait(2)
    client.close()
    assert released.wait(3)


def test_terminal_chat_streams_and_reports_timing(config, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["glm", "--config", "/config.json", "chat"])
    monkeypatch.setattr(service, "load_config", lambda path: config)
    monkeypatch.setattr(service, "key_for", lambda cfg: "test-key")
    inputs = iter(["17 + 25?", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    events = [
        {"choices": [{"delta": {"content": "42"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"completion_tokens": 3}},
    ]
    data = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
    monkeypatch.setattr(service.urllib.request, "urlopen",
                        lambda request, **kw: io.BytesIO(data + b"data: [DONE]\n\n"))
    service.main()
    text = capsys.readouterr().out
    assert "42" in text and "First text after" in text and "3 tokens" in text
