import pytest

from originrag.analysis.answer_format import normalize_answer_format
from originrag.analysis.answer_qc import assess_answer_quality, unsupported_technical_terms


def sensor_pack() -> dict:
    return {
        "question": "What did the sensor comparison measure?",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Example 2025, Results]",
                "text": "The Sensor2 instrument measured signal intensity.",
            }
        ],
    }


@pytest.mark.parametrize(
    "suffix", ["basiert", "basierte", "basierten", "basiertem", "basierter", "basiertes"]
)
def test_german_based_inflections_preserve_supported_identifier(suffix: str) -> None:
    assert unsupported_technical_terms(f"Sensor2-{suffix}", sensor_pack()) == []


@pytest.mark.parametrize(
    "term",
    [
        "Sensor3-basierten",
        "Sensor20-basierte",
        "Sensor2-9-basiertes",
        "Sensor2-Camera9-basierter",
        "Sensor2-unbasierten",
        "Sensor2-basiswert",
    ],
)
def test_german_inflection_does_not_admit_new_identifier_or_suffix(term: str) -> None:
    assert unsupported_technical_terms(term, sensor_pack()) == [term]


@pytest.mark.parametrize("gap", ["\n", "\n\n", "\n \n\t\n"])
def test_formatter_collapses_only_adjacent_empty_canonical_heading_duplicates(gap: str) -> None:
    heading = "## Evidence-backed statements"
    answer = f"{heading}{gap}{heading}\nSensor2 measured signal intensity (E001)."
    normalized = normalize_answer_format(answer, "answer")

    assert normalized.count(heading) == 1
    assert "Sensor2 measured signal intensity (E001)." in normalized
    assert heading not in assess_answer_quality(normalized, sensor_pack(), task="answer")["empty_sections"]


def test_formatter_collapses_more_than_two_adjacent_heading_duplicates() -> None:
    heading = "## Evidence-backed statements"
    answer = f"{heading}\n{heading}\n{heading}\nThe result remains unchanged."

    assert normalize_answer_format(answer, "answer") == f"{heading}\nThe result remains unchanged."


@pytest.mark.parametrize(
    "answer",
    [
        "## Evidence-backed statements\nA first result.\n## Evidence-backed statements\nAnother result.",
        "## Evidence-backed statements\n## Limitations / uncertainty\nA limitation.",
        "## Custom heading\n## Custom heading\nA custom section.",
        "```markdown\n## Evidence-backed statements\n## Evidence-backed statements\n```",
        "~~~markdown\n## Evidence-backed statements\n## Evidence-backed statements\n~~~",
    ],
)
def test_formatter_retains_substantive_sections_distinct_headings_and_code(answer: str) -> None:
    assert normalize_answer_format(answer, "answer") == answer


def test_duplicate_heading_without_body_still_fails_empty_section_check() -> None:
    heading = "## Evidence-backed statements"
    normalized = normalize_answer_format(f"{heading}\n{heading}", "answer")

    qc = assess_answer_quality(normalized, sensor_pack(), task="answer")

    assert heading in qc["empty_sections"]
    assert not qc["passed"]


def test_normalization_keeps_unknown_identifier_qc_failure() -> None:
    heading = "## Evidence-backed statements"
    normalized = normalize_answer_format(f"{heading}\n{heading}\nSensor3-basierten (E001).", "answer")

    qc = assess_answer_quality(normalized, sensor_pack(), task="answer")

    assert qc["possible_unsupported_terms"] == ["Sensor3-basierten"]
    assert "possible_unsupported_terms" in qc["warnings"]
    assert not qc["passed"]
