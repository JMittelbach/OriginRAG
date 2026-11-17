from __future__ import annotations

from copy import deepcopy
import re

import pytest

from originrag.analysis.answer_qc import assess_answer_quality
from originrag.analysis.template_answers import (
    build_template_answer,
    complete_fallback_excerpt,
)


def chunk(evidence_id: str, text: str, **fields: object) -> dict:
    return {
        "evidence_id": evidence_id,
        "paper_id": evidence_id,
        "citation_label": "[Example, 2024, Results]",
        "text": text,
        **fields,
    }


def answer_for(chunks: list[dict], **fields: object) -> tuple[str, dict]:
    pack = {"chunks": chunks, **fields}
    answer = build_template_answer(
        "Summarize the evidence about SensorA measurements.", pack, task="answer"
    )
    return answer, pack


@pytest.mark.parametrize(
    "text",
    [
        "SensorA was measured, but no difference was detected after adjustment.",
        "Dr. A. Chen et al. measured SensorA (e.g. Fig. 2; mean 1.5 units), "
        "but did not validate it.",
        "SensorA was used as an input, i.e. a measured covariate, not as a validated output.",
        "SensorA was measured [using the calibrated sensor (Fig. 2)].",
        "(SensorA was measured, but its performance was not validated.)",
        "SensorA was measured; however, the adjusted comparison was not significant.",
        "SensorA was measured in the test set, excluding the damaged units.",
    ],
)
def test_complete_excerpt_keeps_source_qualifiers_and_abbreviations(text: str) -> None:
    assert complete_fallback_excerpt(text) == text


def test_complete_excerpt_retains_late_negation_without_clipping() -> None:
    text = (
        "SensorA was measured in " + "carefully calibrated units, " * 12
        + "but did not improve accuracy after adjustment."
    )
    assert 260 < len(text) <= 512
    assert complete_fallback_excerpt(text) == text
    assert complete_fallback_excerpt(text, max_chars=260) == ""


def test_over_budget_sentence_is_skipped_whole_including_late_qualifier() -> None:
    long_sentence = (
        "SensorA improved the measured response in " + "calibrated units, " * 35
        + "but did not improve after adjustment."
    )
    safe_sentence = "SensorB was measured in a separate test."
    assert len(long_sentence) > 512
    assert complete_fallback_excerpt(long_sentence) == ""
    assert complete_fallback_excerpt(long_sentence + " " + safe_sentence) == safe_sentence


@pytest.mark.parametrize(
    "text",
    [
        "SensorA was measured (Fig.",
        "SensorA was measured in the test set (1205...",
        "SensorA was measured [using a calibrated device.",
        "SensorA was measured, but the comparison...",
        "SensorA was measured, but the comparison…",
        "SensorA was measured [...] with a calibrated device.",
        "SensorA was measured, but the comparison was",
        "SensorA was measured).",
        "20 0 20 40 20 0 20 40 SensorA High Med Low Bottom.",
        "Sensor Accuracy Confidence.",
        "Results Sensor Accuracy Confidence.",
        "Results | SensorA was measured in the test set.",
        "Downloaded from https://example.test SensorA was measured in the test set.",
        "the calibrated units were measured in the test set.",
        "",
    ],
)
def test_unusable_fragments_are_not_completed_or_rewritten(text: str) -> None:
    assert complete_fallback_excerpt(text) == ""


@pytest.mark.parametrize(
    "qualifier",
    [
        "However, it did not improve after adjustment.",
        "But the comparison was not statistically significant.",
        "Dennoch war der Unterschied nach der Korrektur nicht signifikant.",
        "Jedoch war der Unterschied nach der Korrektur nicht signifikant.",
    ],
)
def test_adjacent_contrast_is_kept_with_its_statement(qualifier: str) -> None:
    text = "SensorA improved the measured response. " + qualifier
    assert complete_fallback_excerpt(text) == text
    assert complete_fallback_excerpt(text, max_chars=len(text) - 1) == ""


@pytest.mark.parametrize(
    "tail",
    [
        "However, it did not improve after",
        "But the comparison was not significant (Fig.",
        "Jedoch war der Unterschied nach der Korrektur nicht",
        "However, it did not improve after recalibration).",
        "However, it did not improve after ] treatment.",
    ],
)
def test_truncated_contrast_invalidates_the_preceding_positive_statement(tail: str) -> None:
    assert complete_fallback_excerpt("SensorA improved the measured response. " + tail) == ""


def test_excerpt_is_deterministic_bounded_and_whitespace_normalized_source_text() -> None:
    text = "SensorA\nwas measured,\tbut no difference was detected after adjustment."
    expected = " ".join(text.split())
    for max_chars in [0, 10, len(expected), 512]:
        outputs = [complete_fallback_excerpt(text, max_chars=max_chars) for _ in range(3)]
        assert outputs == [outputs[0]] * 3
        assert len(outputs[0]) <= max_chars
        assert outputs[0] in expected
        assert outputs[0] in ("", expected)


def test_generic_fallback_uses_later_readable_chunk_from_same_paper() -> None:
    chunks = [
        chunk("E001", "SensorA was measured (Fig.", paper_id="SAME"),
        chunk(
            "E002", "SensorA was measured, but its performance was not validated.",
            paper_id="SAME", quality_flags=["figure_like"],
        ),
        chunk("E003", "20 0 20 40 SensorA High Med Low Bottom", quality_flags=["table_like"]),
    ]
    before = deepcopy(chunks)
    answer, pack = answer_for(chunks)
    assert chunks == before
    assert re.findall(r"\bE\d{3}\b", answer) == ["E002", "E002"]
    assert "Source excerpts (not a synthesis):" in answer
    assert "performance was not validated." in answer
    assert "does not synthesize an answer" in answer
    assert "has not assessed whether additional evidence is needed" in answer
    assert "single causal mechanism" not in answer
    assert "longitudinal datasets would be needed" not in answer
    assert assess_answer_quality(answer, pack, task="answer")["passed"] is True


def test_empty_readable_set_does_not_invent_findings_or_citations() -> None:
    answer, _ = answer_for([chunk("E001", "SensorA was measured (Fig.")])
    assert "Not supported by retrieved evidence." in answer
    assert "E001" not in answer
    assert "reports that" not in answer
    assert "retrieved as relevant evidence" not in answer


def test_named_scope_does_not_expand_when_its_excerpts_are_unusable() -> None:
    answer, _ = answer_for([
        chunk("E001", "SensorA was measured (Fig.", explicit_named_study_anchor=True),
        chunk("E002", "SensorB was measured in a separate study."),
    ])
    assert not re.search(r"\bE\d{3}\b", answer)
    assert "SensorB" not in answer


@pytest.mark.parametrize("first_text", ["SensorA was measured (Fig.", "SensorA was measured."])
def test_bounded_scope_never_adds_outside_papers_when_one_excerpt_is_unusable(
    first_text: str,
) -> None:
    answer, _ = answer_for(
        [
            chunk("E001", first_text),
            chunk("E002", "SensorA was measured (1205..."),
            chunk("E003", "SensorB was measured in a separate study."),
        ],
        bounded_direct_report={
            "supported": True,
            "scope": "named_report_comparison_only",
            "evidence": [{"evidence_id": "E001"}, {"evidence_id": "E002"}],
        },
    )
    expected = [] if "Fig." in first_text else ["E001", "E001"]
    assert re.findall(r"\bE\d{3}\b", answer) == expected
    assert "SensorB" not in answer


def test_comparison_priority_cannot_reintroduce_unreadable_chunks(monkeypatch) -> None:
    monkeypatch.setattr(
        "originrag.analysis.template_answers.build_direct_comparison_strength_answer",
        lambda question, pack: "",
    )
    monkeypatch.setattr(
        "originrag.analysis.template_answers.eligible_direct_comparison_candidate_chunks",
        lambda question, pack: [c for c in pack["chunks"] if c.get("preferred")],
    )
    pack = {"chunks": [
        chunk("E001", "SensorA was measured (Fig.", preferred=True),
        chunk("E002", "SensorA was measured in the calibration set."),
    ]}
    before = deepcopy(pack)
    answer = build_template_answer(
        "How strong is the evidence about SensorA measurements?", pack, task="answer"
    )
    assert re.findall(r"\bE\d{3}\b", answer) == ["E002", "E002"]
    assert pack == before
