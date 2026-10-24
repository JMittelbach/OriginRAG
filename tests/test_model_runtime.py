import threading
import time
import os
from pathlib import Path

import pytest

from originrag import model_runtime as runtime
from originrag.analysis import ollama_client, answer_backends


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.delenv("ORIGINRAG_MODELS_DIR", raising=False)
    monkeypatch.delenv("ORIGINRAG_COLIBRI_HOME", raising=False)
    result = runtime.ModelManager(tmp_path)
    monkeypatch.setattr(result, "inventory", lambda **kw: ([
        {"provider": "ollama", "model": "test:tiny", "name": "test:tiny",
         "installed": True, "loaded": False, "bytes": 10, "downloadable": False},
    ], None))
    yield result
    result.close()


def wait_job(manager):
    deadline = time.monotonic() + 5
    while manager.job["status"] == "running":
        assert time.monotonic() < deadline
        time.sleep(.01)
    return manager.job


def test_selection_persists_without_editing_environment(manager):
    manager.change({"action": "select", "provider": "ollama", "model": "test:tiny"})
    assert wait_job(manager)["status"] == "done"
    with manager.session() as selection:
        assert selection["model"] == "test:tiny"
        assert runtime.CURRENT_RUNTIME.get()["model"] == "test:tiny"
        with pytest.raises(ValueError, match="running"):
            manager.change({"action": "default"})
    assert runtime.CURRENT_RUNTIME.get() is None
    assert manager.active_requests == 0
    assert runtime.ModelManager(manager.root).selected() == selection
    manager.change({"action": "default"})
    assert wait_job(manager)["status"] == "done"
    assert manager.selected() is None
    assert not (manager.root / ".env").exists()


def test_bad_saved_selection_fails_closed_and_can_be_reset(manager):
    runtime.atomic_json(manager.state / "selection.json", {"provider": "untrusted"})
    with pytest.raises(ValueError, match="invalid"):
        with manager.session():
            pytest.fail("must not silently fall back")
    assert manager.active_requests == 0
    assert manager.snapshot(True)["selection_error"]
    manager.change({"action": "default"})
    assert wait_job(manager)["status"] == "done"


@pytest.mark.parametrize("payload", [
    [], {"action": []}, {"action": "select", "provider": "ollama", "model": []},
    {"action": "select", "provider": "ollama", "model": "no-such-model"},
    {"action": "download", "provider": "ollama", "model": "qwen3:4b-q4_K_M"},
    {"action": "download", "provider": "ollama", "model": "https://evil/model", "confirmed": True},
    {"action": "select", "provider": "colibri", "model": "../outside"},
    {"action": "default", "mode": {}},
])
def test_invalid_commands_do_not_start_jobs(manager, payload):
    with pytest.raises(ValueError):
        manager.change(payload)
    assert manager.job is None


def test_download_cancellation_and_single_operation(manager, monkeypatch):
    entered = threading.Event()
    def download(model):
        entered.set()
        while not manager.cancel.wait(.01):
            pass
        manager.check_cancel()
    monkeypatch.setattr(manager, "download_ollama", download)
    manager.change({"action": "download", "provider": "ollama",
                    "model": "qwen3:4b-q4_K_M", "confirmed": True})
    assert entered.wait(1)
    with pytest.raises(ValueError, match="running"):
        manager.change({"action": "default"})
    manager.change({"action": "cancel"})
    assert wait_job(manager)["status"] == "cancelled"
    assert manager.selected() is None


def test_runtime_selection_is_context_local(manager):
    runtime.atomic_json(manager.state / "selection.json",
                        {"provider": "ollama", "model": "test:tiny", "mode": "fast"})
    outside = []
    with manager.session():
        thread = threading.Thread(target=lambda: outside.append(runtime.CURRENT_RUNTIME.get()))
        thread.start()
        thread.join()
        assert runtime.CURRENT_RUNTIME.get()["model"] == "test:tiny"
    assert outside == [None]


def test_selected_ollama_model_and_speed(manager, monkeypatch):
    sent = []
    def post(url, payload, timeout):
        sent.append(payload)
        return {"response": "Answer.", "eval_count": 20, "eval_duration": 2_000_000_000}
    monkeypatch.setattr(ollama_client, "post_json", post)
    token = runtime.CURRENT_RUNTIME.set(manager.runtime(
        {"provider": "ollama", "model": "test:tiny", "mode": "fast"}))
    try:
        result = ollama_client.generate_text("Question", model="qwen3:30b", assistant_prefix="## Answer")
        assert sent[0]["model"] == "test:tiny"
        assert "raw" not in sent[0] and "think" not in sent[0]
        assert result.model == "test:tiny"
        assert manager.stats[("ollama", "test:tiny")]["tokens_per_second"] == 10
    finally:
        runtime.CURRENT_RUNTIME.reset(token)


@pytest.mark.parametrize("finish,text,error", [
    ("stop", "Grounded answer.", None), ("length", "Truncated", "limit"),
    ("stop", "", "no answer"),
])
def test_colibri_transport_and_truncation(manager, monkeypatch, finish, text, error):
    sent = []
    def post(url, payload=None, **kwargs):
        sent.append((url, payload, kwargs))
        return {"choices": [{"message": {"content": text}, "finish_reason": finish}],
                "usage": {"prompt_tokens": 15, "completion_tokens": 4}}
    monkeypatch.setattr(runtime, "http_json", post)
    selected = {"provider": "colibri", "model": runtime.MODEL_ID, "mode": "fast",
                "manager": manager, "base_url": "http://127.0.0.1:8000", "api_key": "local-test"}
    token = runtime.CURRENT_RUNTIME.set(selected)
    try:
        result = answer_backends.generate_answer_with_backend("Evidence", backend="qwen")
        assert result.backend_used == "colibri"
        assert result.model == runtime.MODEL_ID
        assert sent[0][0].endswith("/v1/chat/completions")
        assert sent[0][1]["messages"][0]["content"] == "Evidence"
        assert "options" not in sent[0][1]
        assert sent[0][2]["api_key"] == "local-test"
        if error:
            assert error in result.llm_result.error and result.llm_result.answer is None
        else:
            assert result.llm_result.answer == text
            assert manager.stats[("colibri", runtime.MODEL_ID)]["metric"] == "request"
            assert "eval_duration" not in result.llm_result.usage
        blocked = answer_backends.generate_answer_with_backend(
            "Evidence", backend="qwen", evidence_is_sufficient=False)
        assert blocked.blocked and len(sent) == 1
    finally:
        runtime.CURRENT_RUNTIME.reset(token)


def test_colibri_rejects_unsupported_structured_output(manager):
    with pytest.raises(ValueError, match="constrained JSON"):
        runtime.colibri_generate("JSON please", {"provider": "colibri", "model": runtime.MODEL_ID},
                                 temperature=0, timeout=5, num_predict=10, format="json")


def test_short_context_error_is_not_replaced_by_another_model(manager, monkeypatch):
    def failure(*args, **kwargs):
        raise ValueError("context_length_exceeded")
    monkeypatch.setattr(runtime, "http_json", failure)
    token = runtime.CURRENT_RUNTIME.set({
        "provider": "colibri", "model": runtime.MODEL_ID, "base_url": "http://127.0.0.1:8000",
        "manager": manager, "mode": "fast",
    })
    try:
        result = ollama_client.generate_text("x" * 30000)
        assert result.text is None and "context_length" in result.error
    finally:
        runtime.CURRENT_RUNTIME.reset(token)


@pytest.mark.skipif(not os.environ.get("ORIGINRAG_TEST_COLIBRI_HOME"),
                    reason="requires the separately downloaded local smoke-test model")
def test_real_colibri_answer_path_and_context_limit(manager, monkeypatch):
    home = Path(os.environ["ORIGINRAG_TEST_COLIBRI_HOME"])
    manager.colibri = home
    manager.state.mkdir(parents=True, exist_ok=True)
    (manager.state / runtime.MODEL_ID).symlink_to(home / "olmoe-test-model", target_is_directory=True)
    monkeypatch.setattr(manager, "model_installed", lambda model=runtime.MODEL_ID: True)
    runtime.atomic_json(manager.state / "selection.json", {
        "provider": "colibri", "model": runtime.MODEL_ID, "mode": "low_memory"})
    with manager.session():
        result = answer_backends.generate_answer_with_backend(
            "What is 17 plus 25? Reply with the number only.", backend="qwen", timeout=90)
        assert not result.llm_result.error, result.llm_result.error
        assert result.llm_result.answer.strip().strip(".") == "42"
        assert result.backend_used == "colibri"
        assert result.model == runtime.MODEL_ID
        assert result.llm_result.usage["eval_count"] > 0
        failure = answer_backends.generate_answer_with_backend(
            "hello " * 5000, backend="qwen", timeout=30)
        assert failure.llm_result.answer is None
        assert "Colibri rejected" in failure.llm_result.error
        assert "context" in failure.llm_result.error.lower()
    manager.close()
    assert manager.process is None
