from __future__ import annotations

from pathlib import Path

from pfempire.analysis.faithfulness import check_answer_grounding, write_grounding_report


def evidence_pack() -> dict:
    return {
        "chunks": [
            {
                "evidence_id": "E001",
                "title": "PfEMP1 binding and severe malaria",
                "text": (
                    "Parasite PfEMP1 binding to endothelial protein C receptor was associated "
                    "with severe malaria in children."
                ),
            }
        ]
    }


def issue_names(report: dict) -> set[str]:
    return {str(issue["issue"]) for issue in report["issues"]}


def test_unrelated_fabricated_claim_with_valid_id_fails_lexical_alignment() -> None:
    answer = """## Evidence-backed statements

Pancreatic cancer is cured by daily aspirin treatment (E001).

## Limitations / uncertainty

The clinical safety profile remains uncertain.

## References

- E001
"""

    report = check_answer_grounding(answer, evidence_pack())

    assert report["passed"] is False
    assert "citation_text_mismatch" in issue_names(report)
    assert report["lexical_alignment"][0]["status"] == "mismatch"
    assert "not semantic entailment" in report["check_scope_note"]


def test_supported_claim_with_inflection_variants_passes_alignment() -> None:
    answer = """## Evidence-backed statements

- PfEMP1 binds the endothelial protein C receptor in children with severe malaria (E001).

## Limitations / uncertainty

This reports an association and does not establish causality.

## References

- E001
"""

    report = check_answer_grounding(answer, evidence_pack())

    assert report["passed"] is True
    assert "citation_text_mismatch" not in issue_names(report)
    assert report["lexical_alignment"][0]["status"] == "aligned"
    assert {"bind", "pfemp1", "severe"} <= set(
        report["lexical_alignment"][0]["overlapping_terms"]
    )


def test_each_cited_bullet_is_checked_independently() -> None:
    answer = """## Evidence-backed statements

- PfEMP1 binding is associated with severe malaria (E001).
- Pancreatic cancer is cured by daily aspirin treatment (E001).

## References

- E001
"""

    report = check_answer_grounding(answer, evidence_pack())

    assert report["passed"] is False
    assert [item["status"] for item in report["lexical_alignment"]] == [
        "aligned",
        "mismatch",
    ]


def test_cautious_inference_section_is_not_mistaken_for_direct_source_claim() -> None:
    answer = """## Hypothesis Card

### evidence_backed_statements
- PfEMP1 binding is associated with severe malaria (E001).

### inference
- A wholly new intervention might be worth testing (E001).

### key_references
- E001
"""

    report = check_answer_grounding(answer, evidence_pack())

    assert report["passed"] is True
    assert len(report["lexical_alignment"]) == 1
    assert report["lexical_alignment"][0]["status"] == "aligned"


def test_short_heading_and_citation_only_format_are_not_false_failures() -> None:
    answer = """## Evidence-backed statements

### Finding

- E001

## Limitations / uncertainty

Insufficient detail for a claim.

## References

- E001
"""

    report = check_answer_grounding(answer, evidence_pack())

    assert report["passed"] is True
    assert "citation_text_mismatch" not in issue_names(report)
    assert report["lexical_alignment"][0]["status"] == "not_assessed"


def test_missing_cited_source_text_is_not_treated_as_a_lexical_contradiction() -> None:
    pack = {"chunks": [{"evidence_id": "E001", "title": "", "text": ""}]}
    answer = """## Evidence-backed statements

Pancreatic cancer is cured by daily aspirin treatment (E001).

## References

- E001
"""

    report = check_answer_grounding(answer, pack)

    assert report["passed"] is True
    assert report["lexical_alignment"][0]["status"] == "not_assessed"


def test_grounding_report_explicitly_labels_heuristic_scope(tmp_path: Path) -> None:
    report = check_answer_grounding(
        "Aspirin cures pancreatic cancer in all patients (E001).", evidence_pack()
    )
    markdown_path = tmp_path / "grounding.md"
    issues_path = tmp_path / "issues.tsv"

    write_grounding_report(report, markdown_path, issues_path)

    markdown = markdown_path.read_text(encoding="utf-8")
    assert "conservative lexical overlap" in markdown
    assert "not semantic entailment" in markdown
