from pathlib import Path
import sys

import pytest

from originrag.analysis.answer_qc import assess_answer_quality
from originrag.provenance.library import LibraryStatusResolver

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from web_chat import build_public_abstention, serialize_turn_result
import web_chat


@pytest.mark.parametrize("warnings", [
    ["unbalanced_delimiters"],
    ["missing_required_sections", "empty_required_sections"],
    ["incomplete_prose_section"],
])
def test_format_failure_does_not_claim_missing_evidence_or_bad_citations(warnings):
    answer = build_public_abstention(
        assurance={}, evidence_grade={}, grounding_rejected=True,
        quality_warnings=warnings,
    )
    assert "incomplete or malformed text" in answer
    assert "does not mean that matching papers are missing" in answer
    assert "failed the citation check" not in answer
    assert "Answer withheld" in answer


@pytest.mark.parametrize("warnings", [
    ["unbalanced_delimiters", "possible_unsupported_terms"],
    ["unknown_validation_warning"],
])
def test_content_or_unknown_failures_are_not_dismissed_as_formatting(warnings):
    answer = build_public_abstention(
        assurance={}, evidence_grade={}, grounding_rejected=True,
        quality_warnings=warnings,
    )
    assert "failed answer validation" in answer
    assert "incomplete or malformed text" not in answer


def test_citation_rejection_still_reports_citation_check():
    answer = build_public_abstention(
        assurance={}, evidence_grade={}, grounding_rejected=True,
    )
    assert "failed the citation check" in answer


def test_web_serialization_preserves_format_rejection_reason(tmp_path, monkeypatch):
    registry = tmp_path / "paper_registry.tsv"
    registry.write_text("paper_id\ttitle\n", encoding="utf-8")
    resolver = LibraryStatusResolver(
        registry_path=registry, bib_dir=tmp_path / "bib", chunks_dir=tmp_path / "chunks",
    )
    monkeypatch.setattr(web_chat, "default_library_resolver", lambda: resolver)
    response = serialize_turn_result(
        {
            "pack": {"chunks": []},
            "rejected_answer_path": str(tmp_path / "rejected.md"),
            "answer_qc": {"passed": False, "warnings": ["unbalanced_delimiters"]},
        },
        tmp_path, "What do the sensor papers report?", "answer",
    )
    assert response["answer_state"] == "withheld"
    assert "incomplete or malformed text" in response["answer"]


def test_retrieval_shortfall_is_still_reported_as_evidence_shortfall():
    answer = build_public_abstention(
        assurance={"audit_run": True, "status": "insufficient_even_optimistically"},
        evidence_grade={}, grounding_rejected=False,
    )
    assert "More evidence needed" in answer


@pytest.mark.parametrize("limitation_heading", ["", "## Limitations / uncertainty\n"])
def test_actual_section_qc_is_reported_as_a_format_failure(limitation_heading):
    pack = {"chunks": [{"evidence_id": "E001", "text": "The sensor was calibrated."}]}
    draft = (
        "## Evidence-backed statements\nThe sensor was calibrated (E001).\n\n"
        "## Cautious inference\nNo additional inference is warranted.\n\n"
        f"{limitation_heading}\n"
        "## Missing evidence\nFurther validation is needed.\n\n"
        "## References\n- E001: The sensor study.\n"
    )
    qc = assess_answer_quality(draft, pack, task="answer")
    assert not qc["passed"]
    assert qc["warnings"] == [
        "empty_required_sections" if limitation_heading else "missing_required_sections"
    ]
    answer = build_public_abstention(
        assurance={}, evidence_grade={}, grounding_rejected=True,
        quality_warnings=qc["warnings"],
    )
    assert "incomplete or malformed text" in answer
