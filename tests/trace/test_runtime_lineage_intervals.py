from __future__ import annotations

import json
from pathlib import Path

import pytest

from originrag.trace.method_versions import (
    CERTIFYING_EVIDENCE_STRATEGY_VERSION,
    COMPLETE_BOX_PREFIX_EXACT_METHOD,
    ROBUST_LINEAGE_CERTIFICATE_VERSION,
)
from originrag.trace.runtime import (
    certification_core_candidates,
    lineage_intervals_from_edges,
    run_trace_selection,
)
from originrag.trace.schemas import EvidenceBlueprint, EvidenceFacet, EvidenceLineageEdge, TraceCandidate


def candidate(paper_id: str, *, lineage_id: str) -> TraceCandidate:
    return TraceCandidate(
        chunk_id=f"{paper_id}_chunk",
        paper_id=paper_id,
        text=f"Evidence from {paper_id}",
        token_cost=10,
        retrieval_score=1.0,
        publication_family_id=f"publication_{paper_id}",
        study_lineage_id=lineage_id,
    )


def test_certification_core_is_a_deterministic_nine_report_prefix() -> None:
    selected = [
        TraceCandidate(
            chunk_id=f"C{index}",
            paper_id=f"P{index // 2}",
            text=f"Evidence {index}",
            token_cost=10,
            retrieval_score=20.0 - index,
        )
        for index in range(28)
    ]

    core = certification_core_candidates(
        selected,
        max_papers=9,
        exact_partition_max_components=9,
    )

    assert list(dict.fromkeys(item.paper_id for item in core)) == [
        f"P{index}" for index in range(9)
    ]
    assert [item.chunk_id for item in core] == [item.chunk_id for item in selected[:18]]


def test_certification_core_cannot_exceed_exact_component_limit() -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        certification_core_candidates(
            [candidate("P1", lineage_id="P1")],
            max_papers=10,
            exact_partition_max_components=9,
        )


def edge(
    left: str,
    right: str,
    *,
    score: float,
    verified: bool = False,
    hard_duplicate: bool = False,
    origin_relation: str = "unresolved",
    distinct_dependence_lower: float | None = None,
    distinct_dependence_upper: float | None = None,
) -> EvidenceLineageEdge:
    adjudicated = origin_relation != "unresolved"
    return EvidenceLineageEdge(
        paper_id_a=left,
        paper_id_b=right,
        dependence_score=score,
        is_verified=verified,
        is_hard_duplicate=hard_duplicate,
        origin_relation=origin_relation,
        distinct_dependence_lower=distinct_dependence_lower,
        distinct_dependence_upper=distinct_dependence_upper,
        origin_review_status="adjudicated" if adjudicated else "suggested",
        origin_reviewers=["reviewer_a", "reviewer_b"] if adjudicated else [],
        origin_adjudicator="adjudicator" if adjudicated else None,
        origin_review_timestamp="2026-07-18T12:00:00Z" if adjudicated else None,
        origin_evidence_locator="Methods p. 3" if adjudicated else None,
        origin_rationale="Adjudicated origin relation" if adjudicated else None,
    )


def test_unverified_edge_is_widened_to_suggested_may_link() -> None:
    candidates = [candidate("A", lineage_id="LA"), candidate("B", lineage_id="LB")]

    [interval] = lineage_intervals_from_edges(
        [edge("A", "B", score=0.91)],
        candidates,
        graph_artifact_status="adjudicated",
    )

    assert interval.relation == "may_link"
    assert interval.distinct_dependence_lower == 0.0
    assert interval.distinct_dependence_upper == 1.0
    assert interval.review_status == "suggested"


def test_verified_edge_with_shared_assigned_unit_is_must_link() -> None:
    candidates = [
        candidate("A", lineage_id="LINEAGE_SHARED"),
        candidate("B", lineage_id="LINEAGE_SHARED"),
    ]

    [interval] = lineage_intervals_from_edges(
        [edge("A", "B", score=1.0, verified=True)],
        candidates,
        graph_artifact_status="adjudicated",
    )

    assert interval.relation == "must_link"
    assert interval.distinct_dependence_lower == 1.0
    assert interval.distinct_dependence_upper == 1.0
    assert interval.review_status == "frozen"


def test_adjudicated_hard_duplicate_is_human_reviewed_must_link() -> None:
    candidates = [candidate("A", lineage_id="LA"), candidate("B", lineage_id="LB")]

    [interval] = lineage_intervals_from_edges(
        [
            edge(
                "A",
                "B",
                score=1.0,
                hard_duplicate=True,
                origin_relation="same_origin",
            )
        ],
        candidates,
        graph_artifact_status="adjudicated",
    )

    assert interval.relation == "must_link"
    assert interval.distinct_dependence_lower == 1.0
    assert interval.distinct_dependence_upper == 1.0
    assert interval.review_status == "human_reviewed"


def test_legacy_distinct_point_without_v3_interval_is_not_certificate_trusted() -> None:
    candidates = [candidate("A", lineage_id="LA"), candidate("B", lineage_id="LB")]

    [interval] = lineage_intervals_from_edges(
        [
            edge(
                "A",
                "B",
                score=0.35,
                verified=True,
                origin_relation="distinct_origin",
            )
        ],
        candidates,
        graph_artifact_status="adjudicated",
    )

    assert interval.relation == "may_link"
    assert interval.distinct_dependence_lower == pytest.approx(0.0)
    assert interval.distinct_dependence_upper == pytest.approx(1.0)
    assert interval.review_status == "suggested"


def test_reviewed_distinct_edge_preserves_conditional_dependence_interval() -> None:
    candidates = [candidate("A", lineage_id="LA"), candidate("B", lineage_id="LB")]

    [interval] = lineage_intervals_from_edges(
        [
            edge(
                "A",
                "B",
                score=0.35,
                verified=True,
                origin_relation="distinct_origin",
                distinct_dependence_lower=0.1,
                distinct_dependence_upper=0.8,
            )
        ],
        candidates,
        graph_artifact_status="adjudicated",
    )

    assert interval.relation == "cannot_link"
    assert interval.distinct_dependence_lower == pytest.approx(0.1)
    assert interval.distinct_dependence_upper == pytest.approx(0.8)
    assert interval.source.endswith("declared_conditional_interval")


def test_verified_signal_without_origin_decision_remains_uncertain() -> None:
    candidates = [candidate("A", lineage_id="LA"), candidate("B", lineage_id="LB")]

    [interval] = lineage_intervals_from_edges(
        [edge("A", "B", score=0.05, verified=True)],
        candidates,
        graph_artifact_status="adjudicated",
    )

    assert interval.relation == "may_link"
    assert interval.distinct_dependence_lower == 0.0
    assert interval.distinct_dependence_upper == 1.0
    assert interval.review_status == "suggested"


def test_distinct_decision_contradicting_shared_resource_fails_closed() -> None:
    candidates = [
        candidate("A", lineage_id="LINEAGE_SHARED"),
        candidate("B", lineage_id="LINEAGE_SHARED"),
    ]

    with pytest.raises(ValueError, match="distinct_origin contradicts"):
        lineage_intervals_from_edges(
            [
                edge(
                    "A",
                    "B",
                    score=0.1,
                    origin_relation="distinct_origin",
                    distinct_dependence_lower=0.0,
                    distinct_dependence_upper=0.2,
                )
            ],
            candidates,
            graph_artifact_status="adjudicated",
        )


def test_edges_are_kept_only_when_both_endpoints_are_selected() -> None:
    candidates = [candidate("A", lineage_id="LA"), candidate("B", lineage_id="LB")]
    edges = [
        edge("A", "B", score=0.2),
        edge("A", "OUTSIDE_LEFT", score=0.7),
        edge("OUTSIDE_RIGHT", "B", score=0.8),
    ]

    intervals = lineage_intervals_from_edges(edges, candidates)

    assert [(item.paper_id_a, item.paper_id_b) for item in intervals] == [("A", "B")]


def test_duplicate_selected_pair_is_rejected_independent_of_orientation() -> None:
    candidates = [candidate("A", lineage_id="LA"), candidate("B", lineage_id="LB")]
    edges = [
        edge("A", "B", score=0.2),
        edge("B", "A", score=0.2),
    ]

    with pytest.raises(ValueError, match="duplicate"):
        lineage_intervals_from_edges(edges, candidates)


def test_trace_run_emits_robust_lineage_artifacts(tmp_path: Path) -> None:
    blueprint = EvidenceBlueprint(
        question_id="runtime_robust",
        question="What supports the signal?",
        task_mode="evidence_qa",
        required_facets=[
            EvidenceFacet(
                facet_id="signal",
                question_id="runtime_robust",
                label="molecular signal",
                description="molecular signal evidence",
                facet_type="mechanism",
                biological_scale="molecular",
                importance_weight=1.0,
                is_required=True,
                aliases=["signal"],
            )
        ],
        minimum_independent_lineages=2,
    )
    blueprint_path = tmp_path / "blueprint.json"
    blueprint_path.write_text(
        json.dumps(blueprint.model_dump(mode="json")),
        encoding="utf-8",
    )
    results = [
        {
            "paper_id": paper_id,
            "chunk_id": f"chunk_{paper_id}",
            "text": "Results show a molecular protein signal.",
            "section": "results",
            "score": score,
            "doi": f"10.1/{paper_id.lower()}",
        }
        for paper_id, score in (("A", 2.0), ("B", 1.0))
    ]
    output_dir = tmp_path / "out"

    result = run_trace_selection(
        blueprint.question,
        blueprint.task_mode,
        results,
        output_dir,
        selector="topk",
        config_path=Path("config/trace_rag.yaml"),
        blueprint_path=blueprint_path,
        lineage_graph_path=None,
        token_budget=1_000,
    )

    assert result["robust_lineage"].missing_pair_count == 1
    assert result["selection"].run_config["robust_lineage"]["status"] == (
        result["robust_lineage"].status
    )
    assert (output_dir / "trace_robust_lineage_bounds.json").exists()
    assert (output_dir / "trace_robust_lineage_intervals.tsv").exists()
    certifying_path = output_dir / "trace_certifying_evidence.tsv"
    compatibility_path = output_dir / "trace_certification_core.tsv"
    assert certifying_path.exists()
    assert compatibility_path.exists()
    assert len(certifying_path.read_text(encoding="utf-8").splitlines()) == 3
    assert compatibility_path.read_bytes() == certifying_path.read_bytes()
    assert result["selection"].run_config["certifying_evidence"]["strategy"] == (
        CERTIFYING_EVIDENCE_STRATEGY_VERSION
    )
    assert result["selection"].run_config["certifying_evidence"]["paper_ids"] == [
        "A",
        "B",
    ]
    assert result["selection"].run_config["certification_core"]["paper_ids"] == [
        "A",
        "B",
    ]
    assert result["selection"].run_config["certification_core"][
        "certification_paper_count"
    ] == 2
    assert result["selection"].run_config["certification_core"][
        "compatibility_alias"
    ] is True
    assert result["certifying_evidence"] == result["certification_core"]
    complete_path = output_dir / "trace_robust_lineage_complete_intervals.tsv"
    assert complete_path.exists()
    assert len(complete_path.read_text(encoding="utf-8").splitlines()) == 2
    assert (output_dir / "trace_lineage_review_priorities.tsv").exists()
    screening = json.loads(
        (output_dir / "trace_lineage_review_screening.json").read_text(
            encoding="utf-8"
        )
    )
    assert screening["eligible_pair_count"] == 1
    assert screening["simulated_pair_count"] == len(
        result["lineage_review_priorities"]
    )
    assert screening["exhaustive"] is False
    assert screening["evidence_accounting_version"] == "unit_max_v2"
    assert screening["certificate_method_version"] == ROBUST_LINEAGE_CERTIFICATE_VERSION
    assert screening["lineage_graph_schema_version"] == "trace_lineage_graph_v3"
    assert result["selection"].run_config["evidence_accounting_version"] == (
        "unit_max_v2"
    )
    assert result["selection"].run_config["robust_lineage_certificate_version"] == (
        ROBUST_LINEAGE_CERTIFICATE_VERSION
    )
    assert result["selection"].run_config["lineage_graph_runtime_schema_version"] == (
        "trace_lineage_graph_v3"
    )
    assert result["selection"].run_config["lineage_graph_input_schema_version"] is None
    assert result["selection"].run_config["lineage_graph_input_status"] == "not_provided"
    assert result["selection"].run_config["robust_lineage"][
        "certificate_method_version"
    ] == ROBUST_LINEAGE_CERTIFICATE_VERSION
    bounds = json.loads(
        (output_dir / "trace_robust_lineage_bounds.json").read_text(
            encoding="utf-8"
        )
    )
    assert bounds["certificate_method_version"] == ROBUST_LINEAGE_CERTIFICATE_VERSION
    assert bounds["joint_optimistic_feasibility"] == (
        result["robust_lineage"].joint_optimistic_feasibility
    )
    report = (output_dir / "trace_selection_report.md").read_text(encoding="utf-8")
    assert "## Robust lineage certificate" in report


@pytest.mark.parametrize(
    ("report_count", "expect_exact"),
    [(12, True), (20, True)],
)
def test_runtime_certifies_the_full_selected_set_without_core_switching(
    tmp_path: Path,
    report_count: int,
    expect_exact: bool,
) -> None:
    blueprint = EvidenceBlueprint(
        question_id=f"full_selected_{report_count}",
        question="What supports the molecular signal?",
        task_mode="evidence_qa",
        required_facets=[
            EvidenceFacet(
                facet_id="signal",
                question_id=f"full_selected_{report_count}",
                label="molecular signal",
                description="molecular protein signal evidence",
                facet_type="mechanism",
                biological_scale="molecular",
                importance_weight=1.0,
                is_required=True,
                aliases=["signal"],
            )
        ],
        minimum_independent_lineages=2,
    )
    blueprint_path = tmp_path / "blueprint.json"
    blueprint_path.write_text(
        json.dumps(blueprint.model_dump(mode="json")),
        encoding="utf-8",
    )
    results = [
        {
            "paper_id": f"P{index:02d}",
            "chunk_id": f"C{index:02d}",
            "text": (
                "Results support the molecular signal through protein, gene, "
                "domain, receptor, binding, EPCR, PfEMP1, and var evidence."
            ),
            "section": "results",
            "score": float(report_count - index),
            "doi": f"10.1/full-selected-{index}",
        }
        for index in range(report_count)
    ]
    output_dir = tmp_path / "out"

    result = run_trace_selection(
        blueprint.question,
        blueprint.task_mode,
        results,
        output_dir,
        selector="topk",
        config_path=Path("config/trace_rag.yaml"),
        blueprint_path=blueprint_path,
        lineage_graph_path=None,
        token_budget=100_000,
    )

    selected_papers = list(
        dict.fromkeys(
            candidate.paper_id
            for candidate in result["selection"].selected_candidates
        )
    )
    summary = result["selection"].run_config["certifying_evidence"]
    assert len(selected_papers) == report_count
    assert summary["strategy"] == "full_selected_set_v2"
    assert summary["paper_ids"] == selected_papers
    assert summary["certifying_paper_count"] == report_count
    assert summary["context_paper_count"] == report_count
    assert summary["maximum_certification_papers"] is None
    assert result["certifying_evidence"] == result["selection"].selected_candidates
    assert len(result["certificate_lineage_intervals"]) == (
        report_count * (report_count - 1) // 2
    )
    assert result["lineage_review_screening"]["eligible_pair_count"] == (
        report_count * (report_count - 1) // 2
    )
    assert result["robust_lineage"].exact_partition_analysis.completed is expect_exact
    assert (result["robust_lineage"].partition_bound_method == "exact_search") is (
        expect_exact
    )
    if expect_exact:
        assert result["robust_lineage"].exact_partition_analysis.method == (
            COMPLETE_BOX_PREFIX_EXACT_METHOD
        )
    else:
        assert result["robust_lineage"].partition_bound_method == "safe_relaxation"
        assert result["robust_lineage"].exact_partition_analysis.reason == (
            "component_limit_exceeded"
        )

    canonical = output_dir / "trace_certifying_evidence.tsv"
    compatibility = output_dir / "trace_certification_core.tsv"
    assert len(canonical.read_text(encoding="utf-8").splitlines()) == report_count + 1
    assert compatibility.read_bytes() == canonical.read_bytes()


@pytest.mark.parametrize(
    ("question", "task_mode", "message"),
    [
        ("A different question", "evidence_qa", "question does not match"),
        ("What supports the signal?", "mechanism_mapping", "task_mode"),
    ],
)
def test_loaded_blueprint_must_match_requested_problem(
    tmp_path: Path,
    question: str,
    task_mode: str,
    message: str,
) -> None:
    blueprint = EvidenceBlueprint(
        question_id="bound_problem",
        question="What supports the signal?",
        task_mode="evidence_qa",
        required_facets=[
            EvidenceFacet(
                facet_id="signal",
                question_id="bound_problem",
                label="Signal",
                description="Signal",
                facet_type="mechanism",
                biological_scale="molecular",
            )
        ],
    )
    path = tmp_path / "blueprint.json"
    path.write_text(json.dumps(blueprint.model_dump(mode="json")), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        run_trace_selection(
            question,
            task_mode,
            [],
            tmp_path / "out",
            selector="topk",
            config_path=Path("config/trace_rag.yaml"),
            blueprint_path=path,
        )
