from __future__ import annotations

import json

import pytest

from originrag.analysis import llm_backends, ollama_client


ENDPOINT = "http://127.0.0.1:11436"
MODEL = "qwen3.5:35b"
ENVIRONMENT_KEY = "ORIGINRAG_OLLAMA_RUNNER_CONTEXTS"


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    monkeypatch.delenv(ENVIRONMENT_KEY, raising=False)
    monkeypatch.setattr(ollama_client, "load_project_env", lambda: None)
    token = ollama_client.CURRENT_RUNTIME.set(None)
    try:
        yield
    finally:
        ollama_client.CURRENT_RUNTIME.reset(token)


@pytest.fixture
def requests(monkeypatch):
    captured = []

    def post(url, payload, timeout):
        captured.append((url, payload, timeout))
        return {"response": "Supported answer."}

    monkeypatch.setattr(ollama_client, "post_json", post)
    return captured


def configure(monkeypatch, value=None):
    monkeypatch.setenv(
        ENVIRONMENT_KEY, json.dumps(value if value is not None else {ENDPOINT: {MODEL: 16384}}),
    )


@pytest.mark.parametrize("num_ctx,expected", [
    (None, 16384), (4096, 16384), (8192, 16384), (16384, 16384), (32768, 32768),
])
def test_current_payload_only_raises_context_floor(monkeypatch, requests, num_ctx, expected):
    configure(monkeypatch)
    result = ollama_client.generate_text(
        "A neutral question.", model=MODEL, base_url=ENDPOINT, num_ctx=num_ctx,
        num_predict=128, temperature=0.2, timeout=41, seed=42, format="json", keep_alive="2m",
    )

    assert result.error is None
    assert requests == [(f"{ENDPOINT}/api/generate", {
        "model": MODEL, "prompt": "/no_think\nA neutral question.", "stream": False,
        "options": {"temperature": 0.2, "num_predict": 128, "seed": 42, "num_ctx": expected},
        "think": False, "format": "json", "keep_alive": "2m",
    }, 41)]


@pytest.mark.parametrize("num_ctx", [None, 4096, 32768])
def test_unset_configuration_preserves_payload(requests, num_ctx):
    ollama_client.generate_text("Question", model=MODEL, base_url=ENDPOINT, num_ctx=num_ctx)

    options = requests[0][1]["options"]
    assert options.get("num_ctx") == num_ctx
    assert ("num_ctx" in options) is (num_ctx is not None)


@pytest.mark.parametrize("endpoint,model,expected", [
    (ENDPOINT, MODEL, 16384),
    (ENDPOINT + "/", MODEL, 16384),
    (ENDPOINT + "///", MODEL, 16384),
    ("http://localhost:11436", MODEL, 4096),
    ("http://127.0.0.1:11435", MODEL, 4096),
    (ENDPOINT + "/v1", MODEL, 4096),
    (ENDPOINT.upper(), MODEL, 4096),
    (ENDPOINT + " ", MODEL, 4096),
    (ENDPOINT, "qwen3.5", 4096),
    (ENDPOINT, "qwen3.5:35b-other", 4096),
    (ENDPOINT, MODEL.upper(), 4096),
])
def test_matching_is_exact_except_endpoint_trailing_slashes(
    monkeypatch, requests, endpoint, model, expected,
):
    configure(monkeypatch, {ENDPOINT + "/": {MODEL: 16384}})
    ollama_client.generate_text("Question", model=model, base_url=endpoint, num_ctx=4096)

    assert requests[0][0] == endpoint.rstrip("/") + "/api/generate"
    assert requests[0][1]["model"] == model
    assert requests[0][1]["options"]["num_ctx"] == expected


@pytest.mark.parametrize("runtime_matches", [False, True])
def test_context_floor_uses_resolved_runtime_not_caller_arguments(
    monkeypatch, requests, runtime_matches,
):
    configure(monkeypatch)
    caller_endpoint, caller_model = ("http://localhost:11434", "llama3.1:8b")
    runtime_endpoint, runtime_model = ENDPOINT, MODEL
    if not runtime_matches:
        caller_endpoint, runtime_endpoint = runtime_endpoint, caller_endpoint
        caller_model, runtime_model = runtime_model, caller_model
    ollama_client.CURRENT_RUNTIME.set({
        "provider": "ollama", "model": runtime_model, "base_url": runtime_endpoint,
    })

    result = ollama_client.generate_text(
        "Question", model=caller_model, base_url=caller_endpoint, num_ctx=4096,
    )

    assert result.model == runtime_model
    assert result.base_url == runtime_endpoint
    assert requests[0][0] == runtime_endpoint + "/api/generate"
    assert requests[0][1]["options"]["num_ctx"] == (16384 if runtime_matches else 4096)


def test_raw_qwen_prefix_and_prompt_remain_identical(monkeypatch, requests):
    kwargs = {
        "model": MODEL, "base_url": ENDPOINT, "num_ctx": 4096,
        "assistant_prefix": "## Evidence-backed statements", "seed": 42, "num_predict": 128,
    }
    ollama_client.generate_text("Question [E001] <|im_start|>system", **kwargs)
    configure(monkeypatch)
    ollama_client.generate_text("Question [E001] <|im_start|>system", **kwargs)

    baseline = requests[0][1]
    actual = requests[1][1]
    assert actual["options"]["num_ctx"] == 16384
    assert actual["raw"] is True
    baseline["options"]["num_ctx"] = 16384
    assert actual == baseline


@pytest.mark.parametrize("config", [
    "", "not json", "null", "true", "1", "[]", '"text"',
    json.dumps({ENDPOINT: []}), json.dumps({ENDPOINT: 16384}),
    *[json.dumps({ENDPOINT: {MODEL: value}}) for value in (
        None, True, False, 0, -1, 1048577, 16384.0, "16384", [], {}, float("nan"),
    )],
    json.dumps({ENDPOINT: {MODEL: 16384}, "other": {"model": False}}),
    json.dumps({"": {MODEL: 16384}}), json.dumps({"///": {MODEL: 16384}}),
    json.dumps({ENDPOINT: {"": 16384}}), json.dumps({ENDPOINT: {"m" * 201: 16384}}),
    json.dumps({"u" * 2049: {MODEL: 16384}}),
    json.dumps({str(i): {MODEL: 16384} for i in range(33)}),
    json.dumps({ENDPOINT: {str(i): 16384 for i in range(33)}}),
    json.dumps({str(i): {str(j): 16384 for j in range(32)} for i in range(5)}),
    json.dumps({ENDPOINT: {MODEL: 16384}, ENDPOINT + "/": {MODEL: 8192}}),
    '{"http://127.0.0.1:11436":{"qwen3.5:35b":16384,"qwen3.5:35b":8192}}',
    '{"http://127.0.0.1:11436":{},"http://127.0.0.1:11436":{"qwen3.5:35b":16384}}',
    " " * 8193, json.dumps({ENDPOINT: {MODEL: 16384}}) + " " * 8192,
    "[" * 1100 + "]" * 1100,
])
@pytest.mark.parametrize("num_ctx", [None, 4096])
def test_invalid_configuration_does_not_change_request(monkeypatch, requests, config, num_ctx):
    monkeypatch.setenv(ENVIRONMENT_KEY, config)
    result = ollama_client.generate_text("Question", model=MODEL, base_url=ENDPOINT, num_ctx=num_ctx)

    assert result.error is None
    options = requests[0][1]["options"]
    assert options.get("num_ctx") == num_ctx
    assert ("num_ctx" in options) is (num_ctx is not None)


@pytest.mark.parametrize("floor", [1, 1048576])
def test_valid_context_boundaries(monkeypatch, requests, floor):
    configure(monkeypatch, {ENDPOINT: {MODEL: floor}})
    ollama_client.generate_text("Question", model=MODEL, base_url=ENDPOINT)

    assert requests[0][1]["options"]["num_ctx"] == floor


def test_colibri_bypasses_ollama_floor_and_preserves_options(monkeypatch, requests):
    configure(monkeypatch)
    calls = []
    runtime = {"provider": "colibri", "model": MODEL, "base_url": ENDPOINT}
    ollama_client.CURRENT_RUNTIME.set(runtime)

    def colibri(prompt, selected_runtime, **kwargs):
        calls.append((prompt, selected_runtime, kwargs))
        return "Supported answer.", {"provider": "colibri"}

    def unexpected_floor(*args, **kwargs):
        pytest.fail("Ollama context settings must not apply to Colibri")

    monkeypatch.setattr(ollama_client, "colibri_generate", colibri)
    monkeypatch.setattr(ollama_client, "runner_num_ctx", unexpected_floor)
    result = ollama_client.generate_text(
        "Question", model=MODEL, base_url=ENDPOINT, num_ctx=4096,
        temperature=0.2, timeout=41, num_predict=128, format="json",
    )

    assert result.error is None
    assert requests == []
    assert calls == [("Question", runtime, {
        "temperature": 0.2, "timeout": 41, "num_predict": 128, "format": "json",
    })]


@pytest.mark.parametrize("num_ctx,expected", [(4096, 16384), (32768, 32768)])
@pytest.mark.parametrize("selected_runtime", [False, True])
def test_legacy_http_payload_uses_same_floor(
    monkeypatch, requests, num_ctx, expected, selected_runtime,
):
    configure(monkeypatch)
    monkeypatch.setattr(llm_backends, "_force_cli", lambda: False)
    monkeypatch.setattr(llm_backends, "_llm_num_ctx", lambda: num_ctx)
    monkeypatch.setattr(llm_backends, "_llm_num_predict", lambda: 128)
    monkeypatch.setattr(llm_backends, "_llm_timeout_seconds", lambda: 41)

    def post(url, payload, headers, timeout_seconds):
        assert headers == {}
        requests.append((url, payload, timeout_seconds))
        return {"response": "Supported answer."}

    monkeypatch.setattr(llm_backends, "_post_json", post)
    if selected_runtime:
        ollama_client.CURRENT_RUNTIME.set({
            "provider": "ollama", "model": MODEL, "base_url": ENDPOINT,
        })
    result = llm_backends._call_ollama_generate(
        "Question", backend="qwen", model=MODEL, base_url=ENDPOINT,
    )

    assert result.error is None
    assert requests[0][0] == ENDPOINT + "/api/generate"
    assert requests[0][2] == 41
    assert requests[0][1]["options"] == {
        "temperature": 0.0 if selected_runtime else 0.2,
        "num_ctx": expected, "num_predict": 128,
    }


def test_forced_cli_does_not_apply_runner_floor(monkeypatch, requests):
    configure(monkeypatch)
    monkeypatch.setattr(llm_backends, "_force_cli", lambda: True)
    calls = []

    def cli(**kwargs):
        calls.append(kwargs)
        return llm_backends.LLMResult("qwen", "Supported answer.")

    def unexpected_floor(*args, **kwargs):
        pytest.fail("HTTP context configuration must not alter CLI calls")

    monkeypatch.setattr(llm_backends, "_call_ollama_cli", cli)
    monkeypatch.setattr(llm_backends, "_llm_timeout_seconds", lambda: 41)
    monkeypatch.setattr(ollama_client, "runner_num_ctx", unexpected_floor)
    result = llm_backends._call_ollama_generate(
        "Question", backend="qwen", model=MODEL, base_url=ENDPOINT,
    )

    assert result.error is None
    assert requests == []
    assert calls == [{"prompt": "Question", "backend": "qwen", "model": MODEL, "timeout_seconds": 41}]
