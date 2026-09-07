from __future__ import annotations

from pfempire.analysis.answer_backends import generate_answer_with_backend
from pfempire.analysis.ollama_client import OllamaResult
from pfempire.retrieval.query_planner import plan_queries


def test_default_planner_is_rule_based() -> None:
    plan = plan_queries("What evidence links PfEMP1 to cerebral malaria?")

    assert plan["planner_backend_requested"] == "rule_based"
    assert plan["planner_backend_used"] == "rule_based"
    assert plan["planner_call_performed"] is False
    assert plan["planner_fallback_used"] is False


def test_default_answer_backend_none_does_not_call_model() -> None:
    result = generate_answer_with_backend("Prompt")

    assert result.backend_requested == "none"
    assert result.backend_used == "none"
    assert result.call_performed is False
    assert result.llm_result.answer is None


def test_qwen_answer_backend_uses_requested_generation_parameters(monkeypatch) -> None:
    calls = []

    monkeypatch.delenv("PFEMPIRE_OLLAMA_NUM_PREDICT", raising=False)
    monkeypatch.delenv("HYPORAG_OLLAMA_NUM_PREDICT", raising=False)

    def fake_call(prompt: str, **kwargs):
        calls.append((prompt, kwargs))
        return OllamaResult(
            text="answer",
            model=str(kwargs["model"]),
            base_url=str(kwargs["base_url"]),
            call_performed=True,
            metadata={"prompt_eval_count": 123, "eval_count": 45, "load_duration": 67},
        )

    monkeypatch.setattr(
        "pfempire.analysis.answer_backends.call_ollama_generate",
        fake_call,
    )

    result = generate_answer_with_backend(
        "Prompt",
        backend="qwen",
        model="qwen3:4b",
        temperature=0.15,
        timeout=37,
    )

    assert result.backend_used == "qwen"
    assert result.model == "qwen3:4b"
    assert calls[0][1]["temperature"] == 0.15
    assert calls[0][1]["timeout"] == 37
    assert calls[0][1]["num_predict"] == 2200
    assert calls[0][1]["num_ctx"] == 65536
    assert result.llm_result.usage == {
        "prompt_eval_count": 123,
        "eval_count": 45,
        "load_duration": 67,
    }


def test_qwen_answer_backend_passes_configured_context_window(monkeypatch) -> None:
    calls = []

    def fake_call(prompt: str, **kwargs):
        calls.append((prompt, kwargs))
        return OllamaResult(
            text="answer",
            model=str(kwargs["model"]),
            base_url=str(kwargs["base_url"]),
            call_performed=True,
            metadata={"prompt_eval_count": 7000},
        )

    monkeypatch.setenv("PFEMPIRE_OLLAMA_NUM_CTX", "12288")
    monkeypatch.setattr(
        "pfempire.analysis.answer_backends.call_ollama_generate",
        fake_call,
    )

    result = generate_answer_with_backend(
        "A long evidence prompt",
        backend="qwen",
        model="qwen3:4b",
    )

    assert result.call_performed is True
    assert calls[0][1]["num_ctx"] == 12288


def test_qwen_answer_backend_passes_configured_prediction_budget(monkeypatch) -> None:
    calls = []

    def fake_call(prompt: str, **kwargs):
        calls.append((prompt, kwargs))
        return OllamaResult(
            text="answer",
            model=str(kwargs["model"]),
            base_url=str(kwargs["base_url"]),
            call_performed=True,
        )

    monkeypatch.setenv("PFEMPIRE_OLLAMA_NUM_PREDICT", "3200")
    monkeypatch.setenv("HYPORAG_OLLAMA_NUM_PREDICT", "2800")
    monkeypatch.setattr(
        "pfempire.analysis.answer_backends.call_ollama_generate",
        fake_call,
    )

    result = generate_answer_with_backend(
        "Prompt",
        backend="qwen",
        model="qwen3:4b",
    )

    assert result.call_performed is True
    assert calls[0][1]["num_predict"] == 3200


def test_qwen_answer_backend_honors_legacy_prediction_budget(monkeypatch) -> None:
    calls = []

    def fake_call(prompt: str, **kwargs):
        calls.append((prompt, kwargs))
        return OllamaResult(
            text="answer",
            model=str(kwargs["model"]),
            base_url=str(kwargs["base_url"]),
            call_performed=True,
        )

    monkeypatch.delenv("PFEMPIRE_OLLAMA_NUM_PREDICT", raising=False)
    monkeypatch.setenv("HYPORAG_OLLAMA_NUM_PREDICT", "2800")
    monkeypatch.setattr(
        "pfempire.analysis.answer_backends.call_ollama_generate",
        fake_call,
    )

    result = generate_answer_with_backend(
        "Prompt",
        backend="qwen",
        model="qwen3:4b",
    )

    assert result.call_performed is True
    assert calls[0][1]["num_predict"] == 2800
