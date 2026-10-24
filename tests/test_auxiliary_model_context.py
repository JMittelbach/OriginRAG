from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from originrag.analysis import question_understanding
from originrag.analysis.ollama_client import OllamaResult
from originrag.model_runtime import COLIBRI_MODELS, CURRENT_RUNTIME


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ask_corpus  # noqa: E402


QUESTION = "Which database methods occur in the corpus?"
PAPERS = [{"paper_id": "P1", "title": "Database query latency", "year": "2025"}]
CHUNKS = [{"paper_id": "P1", "chunk_id": "C1", "section": "results",
           "text": "The database benchmark measured query latency using repeated timed runs."}]


@pytest.fixture(autouse=True)
def isolated_context(monkeypatch):
    token = CURRENT_RUNTIME.set(None)
    monkeypatch.setenv("PFEMPIRE_OLLAMA_NUM_CTX", "16384")
    monkeypatch.delenv("ORIGINRAG_OLLAMA_RUNNER_CONTEXTS", raising=False)
    try:
        yield
    finally:
        CURRENT_RUNTIME.reset(token)


def _failed_result():
    return OllamaResult(text=None, model="qwen-test", base_url="http://localhost",
                        call_performed=True, error="test backend unavailable")


@pytest.mark.parametrize("requested, expected", [(None, 4096), (16384, 16384)])
def test_standalone_preparation_retains_default_and_accepts_context_override(
    monkeypatch, requested, expected,
):
    calls = []

    def generate(prompt, **kwargs):
        calls.append(kwargs)
        return _failed_result()

    monkeypatch.setattr(question_understanding, "call_ollama_generate", generate)
    options = {} if requested is None else {"num_ctx": requested}
    question_understanding.understand_question(QUESTION, model="qwen-test", **options)

    assert calls[0]["num_ctx"] == expected
    assert calls[0]["num_predict"] == 900


def test_pipeline_preparation_uses_configured_answer_context(tmp_path, monkeypatch):
    calls = []

    class StopAfterPreparation(Exception):
        pass

    def prepare(question, **kwargs):
        calls.append(kwargs)
        raise StopAfterPreparation

    monkeypatch.setattr(ask_corpus, "validate_corpus_inputs", lambda *args: None)
    monkeypatch.setattr(ask_corpus, "understand_question", prepare)
    args = SimpleNamespace(question=QUESTION, chunks_dir=str(tmp_path), papers=str(tmp_path / "papers.tsv"),
                           backend="qwen", answer_model="qwen-test", understand_question=True)
    with pytest.raises(StopAfterPreparation):
        ask_corpus.run_question(args, tmp_path / "run")

    assert calls[0]["num_ctx"] == 16384
    assert calls[0]["model"] == "qwen-test"


def test_source_annotations_use_the_answer_context_without_changing_prompt(monkeypatch):
    calls = []

    def generate(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return _failed_result()

    ask_corpus.explain_paper_search_matches(
        QUESTION, PAPERS, CHUNKS, model="qwen-test", generator=generate,
    )

    candidates = ask_corpus.paper_reason_candidates(PAPERS, CHUNKS, requested_fields=())
    assert calls[0][0] == ask_corpus.paper_reason_prompt(QUESTION, candidates, requested_fields=())
    assert calls[0][1]["num_ctx"] == 16384
    assert calls[0][1]["num_predict"] == 600


@pytest.mark.parametrize("configured, expected", [(8192, 16384), (32768, 32768)])
def test_context_alignment_respects_selected_runtime_and_scoped_runner_floor(
    monkeypatch, configured, expected,
):
    runtime = {"provider": "ollama", "model": "selected-model", "base_url": "http://localhost:11436"}
    CURRENT_RUNTIME.set(runtime)
    monkeypatch.setenv("PFEMPIRE_OLLAMA_NUM_CTX", str(configured))
    monkeypatch.setenv("ORIGINRAG_OLLAMA_RUNNER_CONTEXTS", json.dumps({
        runtime["base_url"]: {runtime["model"]: 16384, "caller-model": 65536},
    }))
    calls = []

    def generate(prompt, **kwargs):
        calls.append(kwargs)
        return _failed_result()

    assert ask_corpus.effective_model_context(model="caller-model") == expected
    ask_corpus.explain_paper_search_matches(QUESTION, PAPERS, CHUNKS, model="caller-model", generator=generate)
    assert calls[0]["num_ctx"] == expected
    assert CURRENT_RUNTIME.get() == runtime


def test_alignment_preserves_colibri_catalog_context(monkeypatch):
    model = next(iter(COLIBRI_MODELS))
    CURRENT_RUNTIME.set({"provider": "colibri", "model": model, "base_url": "http://localhost:11436"})
    monkeypatch.setenv("PFEMPIRE_OLLAMA_NUM_CTX", "131072")

    assert ask_corpus.effective_model_context(model="caller-model") == COLIBRI_MODELS[model]["context"]


def test_annotation_batches_and_retries_forward_same_context():
    calls = []

    def generate(prompt, **kwargs):
        calls.append(kwargs)
        return _failed_result()

    ask_corpus.explain_paper_search_matches_in_batches(
        QUESTION, PAPERS, CHUNKS, model="qwen-test", requested_fields=("assay",),
        num_ctx=16384, max_retry_calls=1, generator=generate,
    )
    assert len(calls) == 2
    assert all(call["num_ctx"] == 16384 for call in calls)


def test_oversized_annotation_retains_papers_and_marks_fields_unextracted():
    original = deepcopy(PAPERS)

    def forbidden(*args, **kwargs):
        pytest.fail("Oversized prompt must not reach the model")

    papers, metadata = ask_corpus.explain_paper_search_matches(
        "Detailed catalogue question " * 1000, PAPERS, CHUNKS, model="qwen-test",
        num_ctx=4096, requested_fields=("assay",), generator=forbidden,
    )

    assert [paper["paper_id"] for paper in papers] == ["P1"]
    assert PAPERS == original
    assert metadata["attempted"] is False
    assert metadata["used"] is False
    assert metadata["error"].startswith("source_annotation_context_budget:")
    assert "not evidence absent" in metadata["error"]
    assert papers[0]["question_field_statuses"]["assay"] == ask_corpus.PAPER_DETAIL_UNAVAILABLE_STATUS
    assert papers[0]["question_fields"]["assay"] == ask_corpus.PAPER_SEARCH_DETAIL_NOT_ASSESSED
    assert papers[0]["question_fields_status"] == ask_corpus.PAPER_DETAIL_UNAVAILABLE_STATUS
    assert not ask_corpus.paper_details_are_resolved(papers[0], ("assay",))
    assert papers[0]["selection_reason_status"] == "deterministic_fallback"


def test_guarded_annotation_batches_do_not_count_nonexistent_model_calls():
    def forbidden(*args, **kwargs):
        pytest.fail("Oversized prompts must not reach the model")

    papers, metadata = ask_corpus.explain_paper_search_matches_in_batches(
        "Detailed catalogue question " * 1000, PAPERS, CHUNKS, model="qwen-test",
        num_ctx=4096, requested_fields=("assay",), max_retry_calls=1, generator=forbidden,
    )

    assert metadata["model_call_count"] == 0
    assert metadata["retry_call_count"] == 0
    assert metadata["attempted"] is False
    assert metadata["complete"] is False
    assert metadata["unassessed_detail_record_count"] == 1
    assert metadata["fallback_detail_record_count"] == 1
    assert "not assessed" in metadata["error"]
    assert papers[0]["question_field_statuses"]["assay"] == ask_corpus.PAPER_DETAIL_UNAVAILABLE_STATUS
    assert papers[0]["question_fields"]["assay"] == ask_corpus.PAPER_SEARCH_DETAIL_NOT_ASSESSED


def test_mixed_annotation_batches_count_only_the_dispatched_call(monkeypatch):
    estimates = iter([100000, 1000])
    monkeypatch.setattr(ask_corpus, "estimate_prompt_tokens", lambda prompt: next(estimates))
    calls = []

    def generate(prompt, **kwargs):
        calls.append(kwargs)
        return _failed_result()

    papers, metadata = ask_corpus.explain_paper_search_matches_in_batches(
        QUESTION, [*PAPERS, {"paper_id": "P2", "title": "Another database benchmark"}],
        CHUNKS, model="qwen-test", num_ctx=16384, requested_fields=("assay",),
        batch_size=1, max_retry_calls=0, generator=generate,
    )

    assert len(calls) == metadata["model_call_count"] == 1
    assert metadata["retry_call_count"] == 0
    assert metadata["attempted"] is True
    assert metadata["unassessed_detail_record_count"] == 1
    assert metadata["fallback_detail_record_count"] == 2
    assert [paper["paper_id"] for paper in papers] == ["P1", "P2"]


def test_successful_retry_can_replace_context_unavailable_field():
    unavailable = {
        **PAPERS[0],
        "question_fields": {"assay": ask_corpus.PAPER_SEARCH_DETAIL_NOT_ASSESSED},
        "question_field_statuses": {"assay": ask_corpus.PAPER_DETAIL_UNAVAILABLE_STATUS},
    }
    extracted = {
        **PAPERS[0], "question_fields": {"assay": "Repeated timed benchmark runs"},
        "question_field_statuses": {"assay": ask_corpus.PAPER_DETAIL_MODEL_STATUS},
    }

    merged = ask_corpus.merge_paper_detail_attempts(unavailable, extracted, ("assay",))

    assert merged["question_fields"]["assay"] == "Repeated timed benchmark runs"
    assert merged["question_fields_status"] == ask_corpus.PAPER_DETAIL_MODEL_STATUS
    assert ask_corpus.paper_details_are_resolved(merged, ("assay",))


@pytest.mark.parametrize("extra_tokens, expected_calls", [(0, 1), (-1, 0)])
def test_annotation_budget_includes_output_and_margin(monkeypatch, extra_tokens, expected_calls):
    calls = []
    monkeypatch.setattr(ask_corpus, "estimate_prompt_tokens", lambda prompt: 4096)

    def generate(prompt, **kwargs):
        calls.append(kwargs)
        return _failed_result()

    ask_corpus.explain_paper_search_matches(
        QUESTION, PAPERS, CHUNKS, model="qwen-test", num_ctx=4096 + 600 + 1024 + extra_tokens,
        generator=generate,
    )
    assert len(calls) == expected_calls


@pytest.mark.parametrize("context", [True, 0, -1, "16384"])
def test_invalid_annotation_context_fails_explicitly(context):
    with pytest.raises(ValueError, match="context must be a positive integer"):
        ask_corpus.explain_paper_search_matches(QUESTION, PAPERS, CHUNKS, model="qwen-test", num_ctx=context)


@pytest.mark.parametrize("usage", [
    None, [], ["eval_count"], "unavailable", True,
    {"eval_count": "unavailable", "prompt_eval_count": 12},
    {"eval_count": True, "load_duration": -1, "total_duration": 24},
    {"eval_count": float("nan"), "prompt_eval_count": 1.5, "load_duration": 10**400},
    {"eval_count": 0, "prompt_eval_count": 12, "load_duration": 34, "total_duration": 56},
])
def test_optional_annotation_usage_cannot_crash_or_fabricate_measurements(usage):
    reason = "The database benchmark measured query latency using repeated timed runs."

    def generate(prompt, **kwargs):
        return OllamaResult(
            text=json.dumps({"reasons": [{"paper_id": "P1", "reason": reason}]}),
            model="qwen-test", base_url="http://localhost", call_performed=True,
            metadata=usage,
        )

    papers, metadata = ask_corpus.explain_paper_search_matches(
        QUESTION, PAPERS, CHUNKS, model="qwen-test", generator=generate,
    )

    assert metadata["attempted"] is True
    assert metadata["used"] is True
    assert metadata["error"] is None
    assert papers[0]["selection_reason"] == reason
    measured = {key: metadata[key] for key in (
        "prompt_eval_count", "eval_count", "load_duration", "total_duration",
    ) if key in metadata}
    expected = {
        key: value for key, value in (usage.items() if isinstance(usage, dict) else [])
        if type(value) is int and 0 <= value <= 2**63 - 1
    }
    assert measured == expected
