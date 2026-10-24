from __future__ import annotations

from pathlib import Path

import pytest

from originrag.analysis.faithfulness import (
    check_answer_grounding,
    citation_claim_blocks,
    write_grounding_report,
)


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


def catalogue_evidence_pack() -> dict:
    return {
        "chunks": [
            {
                "evidence_id": "E001",
                "title": "Catalogue measurement benchmark",
                "text": (
                    "The catalogue search latency was measured at twenty milliseconds "
                    "across repeated queries. Results were cached between searches."
                ),
            }
        ]
    }


@pytest.mark.parametrize(
    "body",
    [
        "- The catalogue search latency was measured across repeated queries (E001).\n"
        "- Volcanic eruptions destroyed coastal villages during winter storms (E001).",
        "1. The catalogue search latency was measured across repeated queries (E001).\n"
        "2. Volcanic eruptions destroyed coastal villages during winter storms (E001).",
        "1) The catalogue search latency was measured across repeated queries (E001).\n"
        "2) Volcanic eruptions destroyed coastal villages during winter storms (E001).",
        "| Finding | Source |\n| :--- | ---: |\n"
        "| The catalogue search latency was measured across repeated queries | E001 |\n"
        "| Volcanic eruptions destroyed coastal villages during winter storms | E001 |",
        "Finding | Source\n--- | ---\n"
        "The catalogue search latency was measured across repeated queries | E001\n"
        "Volcanic eruptions destroyed coastal villages during winter storms | E001",
        "| Finding |\n|---|\n"
        "| The catalogue search latency was measured across repeated queries (E001). |\n"
        "| Volcanic eruptions destroyed coastal villages during winter storms (E001). |",
    ],
)
def test_list_or_table_layout_cannot_mask_unrelated_cited_claim(body: str) -> None:
    answer = f"## Evidence-backed statements\n\n{body}\n\n## References\n\n- E001"

    report = check_answer_grounding(answer, catalogue_evidence_pack())

    assert report["passed"] is False
    assert [item["status"] for item in report["lexical_alignment"]] == [
        "aligned",
        "mismatch",
    ]


def test_ordered_items_retain_continuation_lines_and_citations() -> None:
    body = (
        "1. The catalogue search latency was measured\n"
        "   across repeated queries (E001).\n"
        "2) The results were cached between searches\n"
        "   for the catalogue benchmark (E001)."
    )

    report = check_answer_grounding(
        f"## Evidence-backed statements\n\n{body}\n\n## References\n- E001",
        catalogue_evidence_pack(),
    )

    assert report["passed"] is True
    assert len(report["lexical_alignment"]) == 2
    assert all(item["status"] == "aligned" for item in report["lexical_alignment"])
    assert "across repeated queries" in report["lexical_alignment"][0]["text"]


def test_decimal_or_pipe_in_ordinary_prose_does_not_split_claim() -> None:
    text = (
        "The catalogue benchmark measured repeated queries with\n"
        "1.25 milliseconds of overhead, using search | cache notation\n"
        "and reporting the measured latency (E001)."
    )

    assert citation_claim_blocks(text) == [" ".join(text.splitlines())]


def test_table_cells_handle_escaped_pipes_and_inline_code() -> None:
    text = (
        "| Finding \\| detail | Source |\n"
        "| --- | --- |\n"
        "| The catalogue `search|cache` latency was measured (E001). | E001 |\n"
        "| Results were cached \\| between catalogue searches (E001). | E001 |"
    )

    blocks = citation_claim_blocks(text)
    report = check_answer_grounding(
        f"## Evidence-backed statements\n\n{text}\n\n## References\n- E001",
        catalogue_evidence_pack(),
    )

    assert len(blocks) == 2
    assert "`search|cache`" in blocks[0]
    assert "\\|" in blocks[1]
    assert all(item["status"] == "aligned" for item in report["lexical_alignment"])


def test_pipe_separated_prose_requires_matching_table_separator() -> None:
    text = (
        "The catalogue search | cache latency was measured (E001).\n"
        "--- this is ordinary prose, not a table separator ---\n"
        "The results were cached between repeated queries."
    )

    assert citation_claim_blocks(text) == [" ".join(text.splitlines())]


def test_table_header_cannot_supply_overlap_for_unrelated_data_row() -> None:
    text = (
        "| Catalogue search latency | Source |\n| --- | --- |\n"
        "| Volcanic eruptions destroyed coastal villages during winter storms | E001 |"
    )

    report = check_answer_grounding(
        f"## Evidence-backed statements\n\n{text}\n\n## References\n- E001",
        catalogue_evidence_pack(),
    )

    assert report["passed"] is False
    assert len(report["lexical_alignment"]) == 1
    assert report["lexical_alignment"][0]["status"] == "mismatch"


def test_short_table_data_and_following_prose_keep_existing_threshold() -> None:
    text = (
        "| Metric | Value | Source |\n| --- | --- | --- |\n"
        "| Latency | 20 ms | E001 |\n\n"
        "The catalogue search latency was measured across repeated queries (E001)."
    )

    report = check_answer_grounding(
        f"## Evidence-backed statements\n\n{text}\n\n## References\n- E001",
        catalogue_evidence_pack(),
    )

    assert report["passed"] is True
    assert [item["status"] for item in report["lexical_alignment"]] == [
        "not_assessed",
        "aligned",
    ]


@pytest.mark.parametrize("separator", ["| --- | --- |", "| - | - |"])
@pytest.mark.parametrize("prefix", ["", "> "])
@pytest.mark.parametrize("list_prefix", ["", "- ", "1. "])
def test_header_citation_remains_attached_to_its_table_claims(
    separator: str, prefix: str, list_prefix: str,
) -> None:
    lines = [
        list_prefix + "| Finding (E001) | Detail |",
        separator,
        "| Volcanic eruptions destroyed coastal villages during winter storms | multiple |",
    ]
    text = "\n".join(prefix + line for line in lines)

    report = check_answer_grounding(
        f"## Evidence-backed statements\n\n{text}\n\n## References\n- E001",
        catalogue_evidence_pack(),
    )

    assert report["passed"] is False
    assert report["lexical_alignment"][-1]["status"] == "mismatch"
    assert report["lexical_alignment"][-1]["evidence_ids"] == ["E001"]


def test_legitimate_header_citation_preserves_source_attribution() -> None:
    text = (
        "| Finding (E001) | Detail |\n| - | - |\n"
        "| The catalogue search latency was measured across repeated queries | repeated |"
    )

    report = check_answer_grounding(
        f"## Evidence-backed statements\n\n{text}\n\n## References\n- E001",
        catalogue_evidence_pack(),
    )

    assert report["passed"] is True
    assert report["lexical_alignment"][-1]["status"] == "aligned"
    assert report["lexical_alignment"][-1]["evidence_ids"] == ["E001"]


def test_table_row_citation_is_not_overridden_by_header_citation() -> None:
    text = (
        "| Finding (E001) | Detail |\n| - | - |\n"
        "| Volcanic eruptions destroyed coastal villages during winter storms (E002) | multiple |"
    )
    pack = catalogue_evidence_pack()
    pack["chunks"].append({
        "evidence_id": "E002",
        "text": "Volcanic eruptions destroyed coastal villages during winter storms.",
    })

    report = check_answer_grounding(
        f"## Evidence-backed statements\n\n{text}\n\n## References\n- E001\n- E002",
        pack,
    )

    assert report["passed"] is True
    assert report["lexical_alignment"][-1]["evidence_ids"] == ["E002"]


def test_cited_header_claim_is_not_hidden_by_aligned_table_rows() -> None:
    text = (
        "| Volcanic eruptions destroyed coastal villages during winter storms (E001) | Detail |\n"
        "| - | - |\n"
        "| The catalogue search latency was measured across repeated queries (E001) | repeated |"
    )

    report = check_answer_grounding(
        f"## Evidence-backed statements\n\n{text}\n\n## References\n- E001",
        catalogue_evidence_pack(),
    )

    assert report["passed"] is False
    assert [item["status"] for item in report["lexical_alignment"]] == [
        "mismatch", "aligned",
    ]


def test_table_without_header_or_row_citation_does_not_acquire_one() -> None:
    text = "| Finding | Detail |\n| - | - |\n| Catalogue search latency | measured |"

    blocks = citation_claim_blocks(text)

    assert blocks == ["Catalogue search latency measured"]
