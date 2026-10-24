from __future__ import annotations

import pytest

from originrag.analysis import ollama_client
from originrag.analysis.answer_backends import generate_answer_with_backend, ollama_usage
from originrag.cancellation import TurnCancelled


@pytest.fixture(autouse=True)
def isolated_runtime():
    token = ollama_client.CURRENT_RUNTIME.set(None)
    try:
        yield
    finally:
        ollama_client.CURRENT_RUNTIME.reset(token)


def generate(monkeypatch, response, *, prefix="## Evidence-backed statements"):
    monkeypatch.setattr(ollama_client, "post_json", lambda *args, **kwargs: response)
    return ollama_client.generate_text(
        "Report the measured catalogue latency.",
        model="qwen3:32b",
        base_url="http://127.0.0.1:11434",
        assistant_prefix=prefix,
    )


@pytest.mark.parametrize("response", [
    {}, {"response": None}, {"response": ""}, {"response": " \n\t "},
    {"response": "<think>private reasoning only</think>"},
    {"response": "private reasoning only</think>\n"},
])
@pytest.mark.parametrize("prefix", [None, "## Evidence-backed statements"])
def test_empty_model_output_is_not_promoted_to_an_answer(monkeypatch, response, prefix):
    result = generate(monkeypatch, response, prefix=prefix)

    assert result.call_performed is True
    assert result.text is None
    assert result.error == "ollama_returned_empty_response"


@pytest.mark.parametrize("response", [
    None, [], ["measured 20 ms"], "measured 20 ms", 123, True,
    {"response": []}, {"response": {"text": "measured 20 ms"}},
    {"response": 20}, {"response": True},
])
def test_wrong_response_schema_returns_explicit_failure(monkeypatch, response):
    result = generate(monkeypatch, response)

    assert result.call_performed is True
    assert result.text is None
    assert result.error.startswith("ollama_invalid_response")


@pytest.mark.parametrize("body", ["", "A partial answer that must not be displayed."])
def test_backend_error_cannot_be_hidden_by_a_prefix_or_partial_answer(monkeypatch, body):
    result = generate(monkeypatch, {"response": body, "error": "model is unavailable"})

    assert result.text is None
    assert result.error == "ollama_backend_error: model is unavailable"


def test_nonstreaming_response_marked_incomplete_is_not_a_finished_answer(monkeypatch):
    result = generate(monkeypatch, {"response": "The measured latency was", "done": False})

    assert result.text is None
    assert result.error == "ollama_incomplete_response"


@pytest.mark.parametrize("done", ["false", 0, 1, [], {}])
def test_invalid_completion_flag_is_rejected(monkeypatch, done):
    result = generate(monkeypatch, {"response": "Measured 20 ms.", "done": done})

    assert result.text is None
    assert result.error.startswith("ollama_invalid_response")


@pytest.mark.parametrize("body", [
    "Measured 20 ms.",
    "<think>private reasoning</think>Measured 20 ms.",
    "private reasoning</think>Measured 20 ms.",
])
def test_real_output_keeps_required_heading_after_reasoning_removal(monkeypatch, body):
    result = generate(monkeypatch, {"response": body, "done": True})

    assert result.text == "## Evidence-backed statements\nMeasured 20 ms."
    assert result.error is None


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_exact_prefilled_heading_echo_is_not_prepended_twice(monkeypatch, newline):
    body = f"## Evidence-backed statements{newline}{newline}Measured 20 ms."
    result = generate(monkeypatch, {"response": body})

    assert result.text == body
    assert result.text.count("## Evidence-backed statements") == 1
    assert result.error is None


@pytest.mark.parametrize("body", [
    "## Evidence-backed statements",
    "## Evidence-backed statements\n\n  ",
    "<think>private reasoning</think>## Evidence-backed statements",
    "## Evidence-backed statements\n<think>private reasoning</think>",
])
def test_prefilled_heading_echo_alone_is_still_empty_output(monkeypatch, body):
    result = generate(monkeypatch, {"response": body})

    assert result.text is None
    assert result.error == "ollama_returned_empty_response"


@pytest.mark.parametrize("body", [
    "Measured 20 ms.",
    "## Cautious inference\nMeasured 20 ms.",
    "## Evidence-backed statements continued\nMeasured 20 ms.",
    "Measured 20 ms.\n## Evidence-backed statements\nA later heading remains untouched.",
])
def test_prefill_echo_handling_preserves_other_generated_text(monkeypatch, body):
    result = generate(monkeypatch, {"response": body})

    assert result.text == "## Evidence-backed statements\n" + body
    assert result.error is None


def test_echo_handling_does_not_hide_model_generated_duplicate_headings(monkeypatch):
    body = "## Evidence-backed statements\n## Evidence-backed statements\nMeasured 20 ms."
    result = generate(monkeypatch, {"response": body})

    assert result.text == body
    assert result.text.count("## Evidence-backed statements") == 2


@pytest.mark.parametrize("value", [None, True, False, -1, "invalid", "12", 1.5, float("nan"), float("inf"), [], {}, 2**63, 10**400])
def test_invalid_optional_usage_counters_do_not_crash_or_invent_measurements(value):
    assert ollama_usage({"eval_count": value, "prompt_eval_count": 10}) == {
        "prompt_eval_count": 10,
    }


@pytest.mark.parametrize("metadata", [None, [], ["eval_count"], "invalid", True, 42])
def test_optional_usage_object_must_be_a_mapping(metadata):
    assert ollama_usage(metadata) is None


def test_usage_preserves_valid_nonnegative_integer_measurements():
    metadata = {
        "prompt_eval_count": 0, "eval_count": 42, "prompt_eval_duration": 123,
        "eval_duration": 456, "load_duration": 789, "total_duration": 1368,
        "unrelated_metadata": "ignored",
    }

    assert ollama_usage(metadata) == {
        key: value for key, value in metadata.items() if type(value) is int
    }


def test_bad_optional_telemetry_does_not_discard_valid_answer(monkeypatch):
    monkeypatch.setattr(ollama_client, "post_json", lambda *args, **kwargs: {
        "response": "Measured 20 ms.", "eval_count": "unavailable",
        "eval_duration": 1000000000, "prompt_eval_count": 10,
    })
    result = generate_answer_with_backend(
        "Report the measured catalogue latency.", backend="qwen", model="qwen3:32b",
        base_url="http://127.0.0.1:11434",
    )

    assert result.llm_result.answer == "Measured 20 ms."
    assert result.llm_result.error is None
    assert result.llm_result.usage == {"eval_duration": 1000000000, "prompt_eval_count": 10}


@pytest.mark.parametrize("count,duration", [
    ("invalid", 1000000000), (2, True), (True, 1000000000), (2, -1),
    (2, 10**400), (10**400, 1000000000),
])
def test_invalid_runtime_metrics_are_not_recorded(monkeypatch, count, duration):
    records = []

    class Manager:
        def record(self, *args):
            records.append(args)

    ollama_client.CURRENT_RUNTIME.set({
        "provider": "ollama", "model": "qwen3:32b",
        "base_url": "http://127.0.0.1:11434", "manager": Manager(),
    })
    result = generate(monkeypatch, {
        "response": "Measured 20 ms.", "eval_count": count, "eval_duration": duration,
    })

    assert result.error is None
    assert records == []


def test_valid_runtime_measurements_are_preserved(monkeypatch):
    records = []

    class Manager:
        def record(self, *args):
            records.append(args)

    runtime = {
        "provider": "ollama", "model": "qwen3:32b",
        "base_url": "http://127.0.0.1:11434", "manager": Manager(),
    }
    ollama_client.CURRENT_RUNTIME.set(runtime)
    result = generate(monkeypatch, {
        "response": "Measured 20 ms.", "eval_count": 40, "eval_duration": 2000000000,
    })

    assert result.error is None
    assert records == [(runtime, 40, 2.0, "decode")]


def test_response_hardening_does_not_swallow_cancellation(monkeypatch):
    def cancelled(*args, **kwargs):
        raise TurnCancelled()

    monkeypatch.setattr(ollama_client, "post_json", cancelled)
    with pytest.raises(TurnCancelled):
        ollama_client.generate_text(
            "Report the measured catalogue latency.", model="qwen3:32b",
            base_url="http://127.0.0.1:11434",
        )
