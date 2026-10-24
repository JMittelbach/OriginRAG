from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ask_corpus  # noqa: E402


FAILED_QC = {"passed": False, "warnings": ["missing_calibration_reference"]}
PASSED_QC = {"passed": True, "warnings": []}


@pytest.fixture
def repair_harness(monkeypatch, tmp_path):
    """Exercise orchestration only, with no corpus or model service access."""
    state = SimpleNamespace(
        initial="Calibration report.",
        repairs=[],
        qc_reports=[],
        calls=[],
        template="Reference-backed calibration report.",
    )

    def initial_answer(**kwargs):
        return ask_corpus.LLMResult(backend="ollama", answer=state.initial), {
            "backend_requested": "ollama",
            "backend_used": "ollama",
            "model": "test-model",
            "call_performed": True,
            "blocked": False,
            "block_reason": None,
        }

    def repair_answer(prompt, **kwargs):
        state.calls.append((prompt, kwargs))
        if not state.repairs:
            pytest.fail("Generated another answer after the expected repair budget")
        return SimpleNamespace(
            llm_result=ask_corpus.LLMResult(backend="ollama", answer=state.repairs.pop(0)),
            backend_used="ollama",
            model="test-model",
            call_performed=True,
        )

    def quality(answer, evidence_pack, *, task):
        if not state.qc_reports:
            pytest.fail("Assessed more answers than expected")
        return dict(state.qc_reports.pop(0))

    def normalize(result, task, evidence_pack=None):
        return ask_corpus.LLMResult(
            backend=result.backend,
            answer=result.answer.strip() if result.answer else None,
            error=result.error,
            usage=result.usage,
        )

    monkeypatch.setattr(ask_corpus, "generate_initial_answer", initial_answer)
    monkeypatch.setattr(ask_corpus, "generate_answer_with_backend", repair_answer)
    monkeypatch.setattr(ask_corpus, "assess_answer_quality", quality)
    monkeypatch.setattr(ask_corpus, "normalize_llm_result", normalize)
    monkeypatch.setattr(ask_corpus, "build_template_answer", lambda **kwargs: state.template)
    monkeypatch.setattr(
        ask_corpus, "build_answer_repair_prompt",
        lambda **kwargs: "Repair calibration report: " + kwargs["previous_answer"],
    )

    def run(*, repairs=3, fallback=False):
        # This supported task skips specialized direct-answer shortcuts entirely.
        return ask_corpus.generate_with_qc_repair(
            question="Which calibration references are missing?",
            task="gaps",
            backend="ollama",
            initial_prompt="Summarize the calibration references.",
            evidence_pack={"chunks": []},
            output_dir=tmp_path,
            max_repair_attempts=repairs,
            use_template_fallback=fallback,
            answer_model="test-model",
        )

    state.run = run
    return state


def test_identical_normalized_repair_stops_without_pretending_success(repair_harness):
    state = repair_harness
    state.repairs = ["  Calibration report.\n"]
    state.qc_reports = [FAILED_QC, FAILED_QC]

    result, qc, attempts, metadata = state.run()

    assert result.answer == "Calibration report."
    assert result.backend == metadata["backend_used"] == "ollama"
    assert qc == FAILED_QC
    assert [attempt["kind"] for attempt in attempts] == ["initial", "repair"]
    assert all(attempt["qc_passed"] is False for attempt in attempts)
    assert len(state.calls) == 1
    assert metadata["repair_stop_reason"] == "unchanged_answer"
    assert metadata["call_performed"] is True
    assert not metadata.get("template_fallback_used", False)


def test_identical_failed_repair_can_fall_back_with_truthful_metadata(repair_harness):
    state = repair_harness
    state.repairs = [state.initial]
    state.qc_reports = [FAILED_QC, FAILED_QC, PASSED_QC]

    result, qc, attempts, metadata = state.run(fallback=True)

    assert len(state.calls) == 1
    assert result.answer == state.template
    assert result.backend == metadata["backend_used"] == "template"
    assert qc == PASSED_QC
    assert [attempt["kind"] for attempt in attempts] == ["initial", "repair", "template_fallback"]
    assert metadata["backend_requested"] == "ollama"
    assert metadata["call_performed"] is True
    assert metadata["repair_stop_reason"] == "unchanged_answer"
    assert metadata["template_fallback_used"] is True


def test_changed_answer_keeps_repair_enabled_for_same_warning(repair_harness):
    state = repair_harness
    state.repairs = ["More precise calibration report.", "Complete calibration report."]
    state.qc_reports = [FAILED_QC, FAILED_QC, PASSED_QC]

    result, qc, attempts, metadata = state.run()

    assert len(state.calls) == 2
    assert result.answer == "Complete calibration report."
    assert qc == PASSED_QC
    assert [attempt["kind"] for attempt in attempts] == ["initial", "repair", "repair"]
    assert metadata.get("repair_stop_reason") != "unchanged_answer"


@pytest.mark.parametrize("changed_qc", [
    {"passed": False, "warnings": ["missing_calibration_units"]},
    {**FAILED_QC, "missing_sections": ["References"]},
])
def test_same_answer_with_changed_qc_keeps_repair_enabled(repair_harness, changed_qc):
    state = repair_harness
    state.repairs = [state.initial, "Complete calibration report."]
    state.qc_reports = [FAILED_QC, changed_qc, PASSED_QC]

    result, qc, attempts, metadata = state.run()

    assert len(state.calls) == 2
    assert result.answer == "Complete calibration report."
    assert qc == PASSED_QC
    assert len(attempts) == 3
    assert metadata.get("repair_stop_reason") != "unchanged_answer"


def test_successful_repair_never_runs_remaining_budget_or_template(repair_harness):
    state = repair_harness
    state.repairs = ["Complete calibration report."]
    state.qc_reports = [FAILED_QC, PASSED_QC]

    result, qc, attempts, metadata = state.run(fallback=True)

    assert len(state.calls) == 1
    assert result.answer == "Complete calibration report."
    assert result.backend == metadata["backend_used"] == "ollama"
    assert qc == PASSED_QC
    assert len(attempts) == 2
    assert not metadata.get("template_fallback_used", False)


@pytest.mark.parametrize("template_qc", [FAILED_QC, PASSED_QC])
def test_template_backend_metadata_is_truthful_without_repairs(repair_harness, template_qc):
    state = repair_harness
    state.qc_reports = [FAILED_QC, template_qc]

    result, qc, attempts, metadata = state.run(repairs=0, fallback=True)

    assert state.calls == []
    assert result.answer == state.template
    assert result.backend == metadata["backend_used"] == "template"
    assert metadata["backend_requested"] == "ollama"
    assert metadata["call_performed"] is True
    assert metadata["template_fallback_used"] is True
    assert qc == template_qc
    assert attempts[-1]["qc_passed"] is template_qc["passed"]
