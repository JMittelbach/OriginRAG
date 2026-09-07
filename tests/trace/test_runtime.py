from __future__ import annotations

import json
from pathlib import Path

from pfempire.trace.lineage import assign_lineage_ids
from pfempire.trace.method_versions import LINEAGE_GRAPH_SCHEMA_VERSION
from pfempire.trace.runtime import lineage_review_status, load_edges_with_status
from pfempire.trace.schemas import EvidenceLineageEdge


def edge(left: str, right: str, *, verified: bool) -> EvidenceLineageEdge:
    return EvidenceLineageEdge(
        paper_id_a=left,
        paper_id_b=right,
        dependence_score=1.0,
        is_verified=verified,
        origin_relation="same_origin" if verified else "unresolved",
        origin_review_status="adjudicated" if verified else "suggested",
        origin_reviewers=["reviewer_a", "reviewer_b"] if verified else [],
        origin_adjudicator="adjudicator" if verified else None,
        origin_review_timestamp="2026-07-18T12:00:00Z" if verified else None,
        origin_evidence_locator="Methods p. 3" if verified else None,
        origin_rationale="Shared experiment" if verified else None,
    )


def write_graph(
    path: Path,
    edges: list[EvidenceLineageEdge],
    *,
    status: str = "adjudicated",
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": LINEAGE_GRAPH_SCHEMA_VERSION,
                "status": status,
                "edges": [item.model_dump(mode="json") for item in edges],
            }
        ),
        encoding="utf-8",
    )


def test_missing_graph_is_unknown_not_independence(tmp_path: Path) -> None:
    edges, status = load_edges_with_status(None, ["A", "B", "C"])

    assert edges == []
    assert status == "not_provided"


def test_one_paper_needs_no_pairwise_graph() -> None:
    assert load_edges_with_status(None, ["A"]) == ([], "not_applicable")


def test_explicit_missing_graph_is_reported_even_for_one_paper(
    tmp_path: Path,
) -> None:
    assert load_edges_with_status(tmp_path / "missing.json", ["A"]) == (
        [],
        "missing",
    )


def test_node_coverage_does_not_masquerade_as_pairwise_review(tmp_path: Path) -> None:
    path = tmp_path / "lineage.json"
    write_graph(path, [edge("A", "B", verified=True), edge("C", "D", verified=True)])

    _, status = load_edges_with_status(path, ["A", "B", "C", "D"])

    assert status == "incomplete"


def test_complete_verified_pair_matrix_is_reviewed(tmp_path: Path) -> None:
    path = tmp_path / "lineage.json"
    write_graph(
        path,
        [
            edge("A", "B", verified=True),
            edge("A", "C", verified=True),
            edge("B", "C", verified=True),
        ],
    )

    _, status = load_edges_with_status(path, ["A", "B", "C"])

    assert status == "reviewed"


def test_developmental_graph_cannot_be_reported_as_reviewed(tmp_path: Path) -> None:
    path = tmp_path / "lineage.json"
    write_graph(path, [edge("A", "B", verified=True)], status="developmental")

    edges, status = load_edges_with_status(path, ["A", "B"])

    assert edges
    assert status == "artifact_developmental"


def test_transitive_origin_closure_counts_as_complete_review() -> None:
    edges = [
        edge("A", "B", verified=True),
        EvidenceLineageEdge(
            paper_id_a="B",
            paper_id_b="C",
            dependence_score=0.0,
            is_verified=True,
            origin_relation="distinct_origin",
            distinct_dependence_lower=0.0,
            distinct_dependence_upper=0.0,
            origin_review_status="adjudicated",
            origin_reviewers=["reviewer_a", "reviewer_b"],
            origin_adjudicator="adjudicator",
            origin_review_timestamp="2026-07-18T12:00:00Z",
            origin_evidence_locator="Methods p. 4",
            origin_rationale="Distinct experiments",
        ),
    ]

    assert lineage_review_status(edges, ["A", "B", "C"]) == "reviewed"


def test_suggested_relationship_does_not_hard_cluster_papers() -> None:
    suggested = edge("A", "B", verified=False)
    verified = edge("A", "B", verified=True)

    suggested_ids = assign_lineage_ids(["A", "B"], [suggested])
    verified_ids = assign_lineage_ids(["A", "B"], [verified])

    assert suggested_ids["A"] != suggested_ids["B"]
    assert verified_ids["A"] == verified_ids["B"]


def test_verified_signal_without_origin_decision_does_not_cluster_papers() -> None:
    signal_only = EvidenceLineageEdge(
        paper_id_a="A",
        paper_id_b="B",
        dependence_score=1.0,
        is_verified=True,
    )

    lineage_ids = assign_lineage_ids(["A", "B"], [signal_only])

    assert lineage_ids["A"] != lineage_ids["B"]
    assert lineage_review_status([signal_only], ["A", "B"]) == "suggested_only"


def test_legacy_verified_score_requires_explicit_origin_migration(tmp_path: Path) -> None:
    path = tmp_path / "legacy_lineage.json"
    path.write_text(
        json.dumps(
            {
                "edges": [
                    {
                        "paper_id_a": "A",
                        "paper_id_b": "B",
                        "dependence_score": 0.05,
                        "is_verified": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    edges, status = load_edges_with_status(path, ["A", "B"])

    assert edges[0].origin_relation == "unresolved"
    assert status == "legacy_origin_relation_missing"


def test_contradictory_verified_transitive_graph_is_rejected() -> None:
    edges = [
        edge("A", "B", verified=True),
        edge("B", "C", verified=True),
        edge("A", "C", verified=True).model_copy(
            update={"dependence_score": 0.0, "origin_relation": "distinct_origin"}
        ),
    ]

    try:
        assign_lineage_ids(["A", "B", "C"], edges)
    except ValueError as exc:
        assert "contradictory reviewed pair decisions" in str(exc)
    else:
        raise AssertionError("contradictory reviewed graph must be rejected")
