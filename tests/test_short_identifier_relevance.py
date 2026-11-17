from __future__ import annotations

from originrag.analysis.evidence_pack_grading import (
    bounded_direct_report_evidence,
    grade_evidence_pack,
    question_relevance_terms,
)


def test_short_alphanumeric_identifiers_preserve_order_and_ignore_case() -> None:
    assert question_relevance_terms(
        "Compare R2 r2 R3 X9 A12 X1Y accuracy.", include_short_identifiers=True
    ) == [
        "r2",
        "r3",
        "x9",
        "a12",
        "x1y",
        "accuracy",
    ]


def test_short_identifiers_do_not_expand_acronyms_or_numeric_tokens() -> None:
    assert question_relevance_terms(
        "UV IR accuracy 2025 19 4 1e3 can", include_short_identifiers=True
    ) == ["accuracy"]


def test_short_identifiers_preserve_report_attribution_and_year_filtering() -> None:
    assert question_relevance_terms(
        "According to Example et al. (2025), compare R2 and R3 accuracy.",
        include_short_identifiers=True,
    ) == ["r2", "r3", "accuracy"]


def test_short_identifiers_do_not_remove_existing_question_qualifiers() -> None:
    assert question_relevance_terms(
        "Does R2 cause negative results independently of age?",
        include_short_identifiers=True,
    ) == ["r2", "cause", "negative", "independently", "age"]


def test_default_relevance_terms_preserve_legacy_ranking_inputs() -> None:
    question = "Compare R2 R3 A12 and Q1000 accuracy in 2025."
    assert question_relevance_terms(question) == ["q1000", "accuracy"]
    assert question_relevance_terms(question, include_short_identifiers=False) == [
        "q1000", "accuracy"
    ]


def test_standalone_direct_report_validation_includes_short_identifiers() -> None:
    wrong = bounded_direct_report_evidence(
        sensor_pack("R8 accuracy exceeded R9 accuracy.")
    )
    correct = bounded_direct_report_evidence(
        sensor_pack("R2 accuracy exceeded R3 accuracy.")
    )
    assert wrong["supported"] is False
    assert correct["supported"] is True


def test_different_short_identifiers_do_not_support_the_requested_comparison() -> None:
    wrong = grade_evidence_pack(sensor_pack("R8 accuracy exceeded R9 accuracy."))
    correct = grade_evidence_pack(sensor_pack("R2 accuracy exceeded R3 accuracy."))

    assert wrong["metrics"]["question_terms"] == ["r2", "r3", "accuracy"]
    assert wrong["metrics"]["question_term_hits"] == ["accuracy"]
    assert wrong["metrics"]["joint_question_coverage_supported"] is False
    assert wrong["bounded_direct_report"]["supported"] is False
    assert wrong["grade"] == "bad"

    assert correct["metrics"]["question_term_coverage"] == 1.0
    assert correct["metrics"]["joint_question_coverage_supported"] is True
    assert correct["bounded_direct_report"]["supported"] is True
    assert correct["grade"] == "good"


def sensor_pack(text: str) -> dict:
    return {
        "question": "Compare R2 and R3 accuracy.",
        "chunks": [
            {
                "evidence_id": f"E{index:03d}",
                "paper_id": f"SENSOR_{index}",
                "title": "Sensor comparison",
                "text": text,
                "section": "results",
                "doi": f"10.1000/example{index}",
                "source_type_assessment": {"source_type": "primary_research"},
            }
            for index in range(1, 5)
        ],
    }
