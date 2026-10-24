import json

import pytest

from originrag import model_runtime as runtime


@pytest.fixture
def endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIGINRAG_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("ORIGINRAG_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("ORIGINRAG_OLLAMA_MODEL_URLS", json.dumps({
        "qwen3.5:35b": "http://127.0.0.1:11435/",
    }))
    manager = runtime.ModelManager(tmp_path)
    yield manager
    manager.close()


def test_endpoint_inventory_and_routing(endpoints, monkeypatch):
    calls = []
    def get(url):
        calls.append(url)
        if url.endswith("/api/ps"):
            return {"models": []}
        names = (["qwen3:32b", "qwen3.5:35b"] if ":11434" in url
                 else ["qwen3.5:35b", "not-configured:1b"])
        return {"models": [{"name": name, "size": 22} for name in names]}
    monkeypatch.setattr(runtime, "http_json", get)
    rows, error = endpoints.inventory()
    assert error is None
    names = [row["model"] for row in rows]
    assert names.count("qwen3.5:35b") == 1
    assert "not-configured:1b" not in names
    assert endpoints.runtime({"provider": "ollama", "model": "qwen3.5:35b"})["base_url"] == "http://127.0.0.1:11435"
    assert endpoints.runtime({"provider": "ollama", "model": "qwen3:32b"})["base_url"] == "http://127.0.0.1:11434"
    endpoints.inventory()
    assert len(calls) == 4


@pytest.mark.parametrize("failed_port,available,missing", [
    (11435, "qwen3:32b", "qwen3.5:35b"),
    (11434, "qwen3.5:35b", "qwen3:32b"),
])
def test_endpoint_outage_does_not_block_other_models(endpoints, monkeypatch, failed_port, available, missing):
    def get(url):
        if f":{failed_port}/" in url:
            raise OSError("offline")
        return {"models": [{"name": "qwen3:32b"}, {"name": "qwen3.5:35b"}]}
    monkeypatch.setattr(runtime, "http_json", get)
    rows, error = endpoints.inventory()
    assert error
    installed = {row["model"] for row in rows if row["installed"]}
    assert available in installed
    assert missing not in installed
    with pytest.raises(ValueError):
        endpoints.change({"action": "select", "provider": "ollama", "model": missing})
    calls = []
    monkeypatch.setattr(runtime.threading.Thread, "start", lambda self: calls.append(True))
    endpoints.change({"action": "select", "provider": "ollama", "model": available})
    assert calls == [True]


@pytest.mark.parametrize("value", [
    '[]', 'null', 'broken', '{"x":null}', '{"": "http://localhost:1"}',
    '{"x":"https://example.com"}', '{"x":"http://192.168.1.1:11434"}',
    '{"x":"http://localhost:0"}', '{"x":"http://localhost:99999"}',
    '{"x":"http://u:p@localhost:11434"}', '{"x":"http://localhost:11434/path"}',
    '{"x":"http://localhost:11434/?token=abc"}', '{"x":"file:///tmp/x"}',
])
def test_invalid_host_routes_rejected(monkeypatch, value):
    monkeypatch.setenv("ORIGINRAG_OLLAMA_MODEL_URLS", value)
    with pytest.raises(ValueError, match="loopback HTTP"):
        runtime.ollama_model_urls()


def test_no_routes_preserves_default(monkeypatch):
    monkeypatch.delenv("ORIGINRAG_OLLAMA_MODEL_URLS", raising=False)
    assert runtime.ollama_model_urls() == {}
