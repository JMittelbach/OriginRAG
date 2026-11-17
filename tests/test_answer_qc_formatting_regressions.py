from __future__ import annotations

import pytest

from originrag.analysis.answer_format import normalize_answer_format
from originrag.analysis.answer_qc import (
    assess_answer_quality,
    unbalanced_delimiters,
    unsupported_technical_terms,
)


@pytest.mark.parametrize(
    "answer",
    [
        "See [source](https://example.org/report).",
        "See the report (https://example.org/report).",
        "See [https://example.org/report].",
        "See ([source](https://example.org/report)).",
        "See [source](https://example.org/report_(appendix)).",
        "See (https://example.org/report_(appendix)).",
        "See [source](https://[::1]/report).",
        'See [source](https://example.org/report "Report (appendix)").',
        "See [source](<https://example.org/report_(>).",
        "See <https://example.org/report_)>.",
        "See https://example.org/report_(.",
    ],
)
def test_delimiter_check_preserves_url_wrappers(answer: str) -> None:
    assert unbalanced_delimiters(answer) == []


@pytest.mark.parametrize(
    ("answer", "issues"),
    [
        ("See [source](https://example.org/report", ["parentheses"]),
        ("See [source](https://example.org/report_(appendix)", ["parentheses"]),
        ("See source](https://example.org/report).", ["brackets"]),
        ("See [source](https://example.org/report)).", ["parentheses"]),
        ("See [source](https://example.org/report)unfinished(", ["parentheses"]),
        ("See [source](https://example.org/report)unfinished[", ["brackets"]),
        ("A truncated excerpt (see [source](https://example.org/report).", ["parentheses"]),
        ("A truncated excerpt [see (https://example.org/report).", ["brackets"]),
    ],
)
def test_delimiter_check_retains_real_errors_around_urls(answer: str, issues: list[str]) -> None:
    assert unbalanced_delimiters(answer) == issues


@pytest.mark.parametrize("source", ["T-cell specific", "T cell specific", "T‐cell specific"])
def test_hyphenated_term_requires_same_contiguous_source_wording(source: str) -> None:
    pack = {"chunks": [{"evidence_id": "E001", "text": f"The study reports {source} annotation."}]}

    assert unsupported_technical_terms("T-cell-specific annotation (E001).", pack) == []


@pytest.mark.parametrize(
    "chunks",
    [
        [{"text": "T cells were observed. The cell specific annotation was reviewed."}],
        [{"text": "T"}, {"text": "cell specific annotation"}],
        [{"title": "T", "text": "cell specific annotation"}],
        [{"text": "XT-cell specific annotation."}],
        [{"text": "T-cell specificity was reviewed."}],
    ],
)
def test_hyphenation_does_not_assemble_absent_source_phrase(chunks: list[dict]) -> None:
    assert unsupported_technical_terms("T-cell-specific", {"chunks": chunks}) == [
        "T-cell-specific"
    ]


@pytest.mark.parametrize("term", ["T-cell-Sensor3", "T-cell-Sensor20", "T-cell-Sensor2-9"])
def test_hyphenation_does_not_introduce_identifiers(term: str) -> None:
    pack = {"chunks": [{"text": "T-cell Sensor2 annotation."}]}

    assert unsupported_technical_terms(term, pack) == [term]


@pytest.mark.parametrize("separator", ["-", "‐", "‑", "‒", "–", "—", "―", "−"])
def test_hyphenation_does_not_drop_numeric_identifier_suffix(separator: str) -> None:
    pack = {"chunks": [{"text": f"T-cell Sensor2{separator}9 annotation."}]}

    assert unsupported_technical_terms("T-cell-Sensor2", pack) == ["T-cell-Sensor2"]


@pytest.mark.parametrize("separator", ["-", "‐", "‑", "‒", "–", "—", "―", "−"])
def test_hyphenation_does_not_drop_identifier_prefix(separator: str) -> None:
    pack = {"chunks": [{"text": f"X{separator}T-cell Sensor2 annotation."}]}

    assert unsupported_technical_terms("T-cell-Sensor2", pack) == ["T-cell-Sensor2"]


@pytest.mark.parametrize(
    ("term", "source"),
    [
        ("T-cell-Sensor2-9", "T-cell Sensor2 9 annotation."),
        ("T-cell-9-Sensor2", "T-cell 9 Sensor2 annotation."),
        ("T-cell-Sensor2-9", "T-cell Sensor2−9 annotation."),
        ("T-cell-Sensor2-A1", "T-cell Sensor2 A1 annotation."),
        ("T-cell-Sensor2-9A", "T-cell Sensor2 9A annotation."),
        ("T-cell-Sensor2A-X", "T-cell Sensor2A X annotation."),
    ],
)
def test_hyphenation_does_not_reinterpret_numeric_fragments(term: str, source: str) -> None:
    assert unsupported_technical_terms(term, {"chunks": [{"text": source}]}) == [term]


def test_hyphenation_does_not_reinterpret_any_digit_containing_term() -> None:
    pack = {"chunks": [{"text": "T-cell Sensor2 annotation."}]}

    assert unsupported_technical_terms("T-cell-Sensor2", pack) == ["T-cell-Sensor2"]


@pytest.mark.parametrize(
    "source", ["T-cell specific―variant annotation.", "X―T-cell specific annotation."]
)
def test_hyphenation_preserves_horizontal_bar_boundary_for_digit_free_terms(source: str) -> None:
    pack = {"chunks": [{"text": source}]}

    assert unsupported_technical_terms("T-cell-specific", pack) == ["T-cell-specific"]


def test_exact_numeric_identifier_does_not_need_hyphenation_exception() -> None:
    pack = {"chunks": [{"text": "T-cell-Sensor2-9 annotation."}]}

    assert unsupported_technical_terms("T-cell-Sensor2-9", pack) == []


def _annotation_answer(evidence: str, limitations: str = "The sample is limited.") -> str:
    return f"""## Evidence-backed statements
{evidence}

## Cautious inference
No additional inference is warranted.

## Limitations / uncertainty
{limitations}

## Missing evidence
Additional annotation validation remains needed.

## References
- E001: The annotation study.
"""


def test_repaired_duplicate_heading_and_exact_hyphenation_pass_without_prose_rewrite() -> None:
    pack = {"chunks": [{"evidence_id": "E001", "text": "T-cell specific annotation was reported."}]}
    answer = _annotation_answer(
        "## Evidence-backed statements\nT-cell-specific annotation was reported (E001)."
    )

    normalized = normalize_answer_format(answer, "answer")
    qc = assess_answer_quality(normalized, pack, task="answer")

    assert normalized.count("## Evidence-backed statements") == 1
    assert "T-cell-specific annotation was reported (E001)." in normalized
    assert qc["warnings"] == []


def test_source_hyphenation_does_not_disable_unknown_citation_check() -> None:
    pack = {"chunks": [{"evidence_id": "E001", "text": "T-cell specific annotation was reported."}]}
    answer = _annotation_answer("T-cell-specific annotation was reported (E999).")

    qc = assess_answer_quality(normalize_answer_format(answer, "answer"), pack, task="answer")

    assert not qc["passed"]
    assert qc["unknown_citations"] == ["E999"]
    assert "unknown_evidence_citations" in qc["warnings"]


def test_source_hyphenation_does_not_validate_causal_upgrade() -> None:
    pack = {
        "chunks": [
            {"evidence_id": "E001", "text": "T-cell specific labeling was associated with annotation accuracy."}
        ]
    }
    answer = _annotation_answer("T-cell-specific labeling causes accurate annotation (E001).")

    qc = assess_answer_quality(normalize_answer_format(answer, "answer"), pack, task="answer")

    assert not qc["passed"]
    assert qc["possible_unsupported_terms"] == []
    assert qc["unsupported_causal_claims"]


def test_formatter_does_not_fill_missing_limitations() -> None:
    pack = {"chunks": [{"evidence_id": "E001", "text": "The annotation was reported."}]}
    answer = _annotation_answer("The annotation was reported (E001).", limitations="")

    qc = assess_answer_quality(normalize_answer_format(answer, "answer"), pack, task="answer")

    assert not qc["passed"]
    assert qc["empty_sections"] == ["## Limitations / uncertainty"]


def test_formatter_does_not_close_truncated_fallback_excerpts() -> None:
    pack = {"chunks": [{"evidence_id": "E001", "text": "The annotation was reported (Fig. 4)."}]}
    answer = _annotation_answer("The annotation was reported (Fig. (E001).")

    normalized = normalize_answer_format(answer, "answer")
    qc = assess_answer_quality(normalized, pack, task="answer")

    assert "The annotation was reported (Fig. (E001)." in normalized
    assert not qc["passed"]
    assert qc["unbalanced_delimiters"] == ["parentheses"]
