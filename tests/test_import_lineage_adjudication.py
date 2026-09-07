from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from import_lineage_adjudication import adjudication_overrides  # noqa: E402
from build_evidence_lineage_graph import apply_overrides  # noqa: E402
from pfempire.trace.io import write_tsv  # noqa: E402
from pfempire.trace.runtime import lineage_review_status  # noqa: E402


def reviewed_rows() -> list[dict[str, str]]:
    rows = []
    for left, right in itertools.combinations(["P1", "P2", "P3", "P4", "P5"], 2):
        same_publication = (left, right) == ("P1", "P2")
        relation = "same_publication" if same_publication else "distinct_origin"
        mapping = "same_origin" if same_publication else "distinct_origin"
        rows.append(
            {
                "question_id": "Q1",
                "paper_id_a": left,
                "paper_id_b": right,
                "reviewer_1_id": "R1",
                "reviewer_1_timestamp": "2026-08-03T10:00:00+00:00",
                "reviewer_1_relation": relation,
                "reviewer_1_evidence_locator": "Methods p.2",
                "reviewer_1_rationale": "Compared cohorts and identifiers.",
                "reviewer_2_id": "R2",
                "reviewer_2_timestamp": "2026-08-03T11:00:00+00:00",
                "reviewer_2_relation": relation,
                "reviewer_2_evidence_locator": "Registry record",
                "reviewer_2_rationale": "Checked publication and dataset history.",
                "adjudicator_id": "R3",
                "adjudication_timestamp": "2026-08-03T12:00:00+00:00",
                "adjudicated_relation": relation,
                "adjudicated_origin_mapping": mapping,
                "distinct_dependence_lower": "0.0" if not same_publication else "",
                "distinct_dependence_upper": "0.0" if not same_publication else "",
                "adjudication_evidence_locator": "Methods and registry",
                "adjudication_rationale": "Resolved after source comparison.",
                "review_status": "adjudicated",
            }
        )
    return rows


def test_complete_five_paper_review_emits_ten_strict_overrides() -> None:
    overrides = adjudication_overrides(reviewed_rows(), question_id="Q1")

    assert len(overrides) == 10
    assert overrides[0]["action"] == "mark_same_publication"
    assert overrides[0]["reviewer"] == "R1;R2"
    assert overrides[0]["origin_review_status"] == "adjudicated"
    assert all(row["action"] == "mark_distinct_origin" for row in overrides[1:])


def test_imported_overrides_create_a_closure_complete_reviewed_graph(
    tmp_path: Path,
) -> None:
    overrides = adjudication_overrides(reviewed_rows(), question_id="Q1")
    path = tmp_path / "overrides.tsv"
    write_tsv(path, overrides)
    edges = []

    apply_overrides(edges, path)

    assert len(edges) == 10
    assert lineage_review_status(edges, ["P1", "P2", "P3", "P4", "P5"]) == (
        "reviewed"
    )
    same_publication = next(
        edge for edge in edges if {edge.paper_id_a, edge.paper_id_b} == {"P1", "P2"}
    )
    assert "same_publication" in same_publication.relationship_types


def test_missing_pair_is_rejected() -> None:
    with pytest.raises(ValueError, match="all 10 pairs"):
        adjudication_overrides(reviewed_rows()[:-1], question_id="Q1")


def test_unresolved_pair_is_rejected() -> None:
    rows = reviewed_rows()
    rows[0]["adjudicated_relation"] = "unresolved"
    rows[0]["adjudicated_origin_mapping"] = "unresolved"

    with pytest.raises(ValueError, match="trusted graph remains blocked"):
        adjudication_overrides(rows, question_id="Q1")


def test_same_reviewer_twice_is_rejected() -> None:
    rows = reviewed_rows()
    rows[0]["reviewer_2_id"] = "R1"

    with pytest.raises(ValueError, match="reviewers must be distinct"):
        adjudication_overrides(rows, question_id="Q1")
