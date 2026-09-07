from __future__ import annotations

import math

import pfempire.evaluation.lineage_comparison as lineage_comparison
from pfempire.evaluation.lineage_comparison import (
    METHOD_IDS,
    MethodResult,
    binomial_upper_bound,
    duplicate_unit_mapping,
    evaluate_methods,
    paired_false_acceptance_rate_comparisons,
    paired_method_comparisons,
    summarize_results,
)
from pfempire.trace.schemas import EvidenceBlueprint, EvidenceFacet, TraceCandidate
from pfempire.trace.selector import SelectorConfig


def blueprint(minimum_lineages: int = 3) -> EvidenceBlueprint:
    return EvidenceBlueprint(
        question_id="Q1",
        question="Does report multiplication create independent evidence?",
        task_mode="evidence_synthesis",
        required_facets=[
            EvidenceFacet(
                facet_id="mechanism",
                question_id="Q1",
                label="Mechanism",
                description="Mechanistic support",
                facet_type="mechanism",
                biological_scale="molecular",
            )
        ],
        minimum_independent_lineages=minimum_lineages,
        review_status="frozen",
    )


def candidate(
    index: int,
    *,
    title: str,
    doi: str,
    score: float | None = None,
) -> TraceCandidate:
    row = {"title": title, "doi": doi}
    return TraceCandidate(
        chunk_id=f"C{index}",
        paper_id=f"P{index}",
        text=f"Distinct evidence text {index}",
        token_cost=100,
        retrieval_score=float(score if score is not None else 100 - index),
        section_quality=1.0,
        facet_scores={"mechanism": 1.0},
        scale_scores={"molecular": 1.0},
        metadata={"raw_result": row},
    )


def config() -> SelectorConfig:
    return SelectorConfig(
        token_budget=1000,
        max_chunks_per_paper=1,
        flow_anchor_papers=0,
        flow_minimum_papers=0,
    )


def test_near_duplicate_baseline_clusters_publication_versions_only() -> None:
    shared = "Expression of PfEMP1 variants in children with severe malaria"
    candidates = [
        candidate(1, title=shared, doi="10.1101/preprint"),
        candidate(2, title=shared + " — a cohort study", doi="10.1093/journal"),
        candidate(3, title="A distinct mechanistic investigation of EPCR", doi="10.1/x"),
    ]

    units = duplicate_unit_mapping(candidates)

    assert units["P1"] == units["P2"]
    assert units["P1"] != units["P3"]


def test_full_method_blocks_report_multiplication_that_topk_accepts() -> None:
    candidates = [
        candidate(1, title="Study report one", doi="10.1/a"),
        candidate(2, title="Study report two", doi="10.1/b"),
        candidate(3, title="Independent report", doi="10.1/c"),
    ]
    origins = {"P1": "O_SHARED", "P2": "O_SHARED", "P3": "O_OTHER"}

    rows = {
        row.method_id: row
        for row in evaluate_methods(
            candidates,
            blueprint(),
            gold_origin_by_paper=origins,
            config=config(),
        )
    }

    assert set(rows) == set(METHOD_IDS)
    assert rows["relevance_topk"].accepted is True
    assert rows["paper_count_threshold"].accepted is True
    assert rows["one_per_known_lineage"].accepted is False
    assert rows["n_eff_threshold"].accepted is False
    assert rows["pfempire_full_v6"].accepted is False


def test_full_method_accepts_three_adjudicated_origins() -> None:
    candidates = [
        candidate(index, title=f"Independent study {index}", doi=f"10.1/{index}")
        for index in range(1, 4)
    ]
    origins = {f"P{index}": f"O{index}" for index in range(1, 4)}

    rows = evaluate_methods(
        candidates,
        blueprint(),
        gold_origin_by_paper=origins,
        config=config(),
    )

    assert next(row for row in rows if row.method_id == "pfempire_full_v6").accepted


def test_full_method_records_exact_certificate_diagnostics(monkeypatch) -> None:
    clock = iter((2_000_000_000, 2_012_500_000))
    monkeypatch.setattr(lineage_comparison, "perf_counter_ns", lambda: next(clock))
    candidates = [
        candidate(index, title=f"Independent study {index}", doi=f"10.1/{index}")
        for index in range(1, 4)
    ]

    rows = {
        row.method_id: row
        for row in evaluate_methods(
            candidates,
            blueprint(),
            gold_origin_by_paper={f"P{index}": f"O{index}" for index in range(1, 4)},
            config=config(),
        )
    }

    full = rows["pfempire_full_v6"]
    assert full.exact_completed is True
    assert full.exact_for_declared_uncertainty is True
    assert full.component_count == 3
    assert full.search_states >= 1
    assert full.partition_bound_method == "exact_search"
    assert full.fallback_used is False
    assert full.certificate_runtime_ms == 12.5
    assert full.report_origin_homogeneity_verified is False
    assert full.homogeneity_annotated_substantive_paper_count == 0
    assert full.substantive_paper_count_for_homogeneity == 3

    baseline = rows["relevance_topk"]
    assert baseline.exact_completed is None
    assert baseline.exact_for_declared_uncertainty is None
    assert baseline.component_count is None
    assert baseline.search_states is None
    assert baseline.partition_bound_method == "not_applicable"
    assert baseline.fallback_used is None
    assert baseline.certificate_runtime_ms is None
    assert baseline.report_origin_homogeneity_verified is None
    assert baseline.homogeneity_annotated_substantive_paper_count is None
    assert baseline.substantive_paper_count_for_homogeneity is None


def test_full_method_records_large_complete_box_exact_diagnostics(monkeypatch) -> None:
    clock = iter((4_000_000_000, 4_021_000_000))
    monkeypatch.setattr(lineage_comparison, "perf_counter_ns", lambda: next(clock))
    candidates = [
        candidate(index, title=f"Independent study {index}", doi=f"10.1/{index}")
        for index in range(1, 19)
    ]
    fallback_config = SelectorConfig(
        token_budget=3600,
        max_chunks_per_paper=1,
        flow_anchor_papers=0,
        flow_minimum_papers=18,
    )

    full = next(
        row
        for row in evaluate_methods(
            candidates,
            blueprint(),
            gold_origin_by_paper={f"P{index}": f"O{index}" for index in range(1, 19)},
            config=fallback_config,
            lineage_condition="masked_50",
            mask_fraction=1.0,
        )
        if row.method_id == "pfempire_full_v6"
    )

    assert full.exact_completed is True
    assert full.exact_for_declared_uncertainty is True
    assert full.component_count == 18
    assert full.search_states == 18
    assert full.partition_bound_method == "exact_search"
    assert full.fallback_used is False
    assert full.certificate_runtime_ms == 21.0
    assert full.report_origin_homogeneity_verified is False
    assert full.homogeneity_annotated_substantive_paper_count == 0
    assert full.substantive_paper_count_for_homogeneity == 18


def test_masked_condition_hides_gold_from_known_lineage_and_full_method() -> None:
    candidates = [
        candidate(index, title=f"Report {index}", doi=f"10.1/{index}")
        for index in range(1, 4)
    ]
    rows = {
        row.method_id: row
        for row in evaluate_methods(
            candidates,
            blueprint(),
            gold_origin_by_paper={"P1": "O1", "P2": "O1", "P3": "O2"},
            config=config(),
            lineage_condition="masked_50",
            mask_fraction=1.0,
        )
    }

    assert rows["one_per_known_lineage"].accepted is True
    assert rows["pfempire_full_v6"].accepted is False


def test_exact_zero_error_upper_bound_requires_59_acceptances() -> None:
    assert binomial_upper_bound(0, 59, confidence=0.95) < 0.05
    assert binomial_upper_bound(0, 58, confidence=0.95) > 0.05
    assert math.isclose(binomial_upper_bound(0, 0, confidence=0.95), 1.0)


def test_summary_reports_coverage_false_acceptance_and_false_rejection() -> None:
    candidates = [
        candidate(1, title="Study one", doi="10.1/a"),
        candidate(2, title="Study two", doi="10.1/b"),
        candidate(3, title="Study three", doi="10.1/c"),
    ]
    rows = evaluate_methods(
        candidates,
        blueprint(),
        gold_origin_by_paper={"P1": "O1", "P2": "O1", "P3": "O2"},
        config=config(),
    )

    summaries = {row["method_id"]: row for row in summarize_results(rows, {"Q1": False})}

    assert summaries["relevance_topk"]["false_accepts"] == 1
    assert summaries["pfempire_full_v6"]["false_accepts"] == 0
    assert summaries["relevance_topk"]["coverage"] == 1.0

    comparisons = paired_method_comparisons(
        rows, {"Q1": False}, iterations=100, seed=42
    )
    topk = next(
        row
        for row in comparisons
        if row["baseline_method"] == "relevance_topk"
        and row["metric"] == "false_acceptance_reduction"
    )
    assert topk["mean_difference"] == 1.0
    assert topk["ci_low"] == 1.0


def test_paired_risk_comparison_uses_false_accepts_among_accepted_questions() -> None:
    rows = [
        MethodResult("Q1", "masked_50", "relevance_topk", True, (), (), 1, 1, 1, 1),
        MethodResult("Q2", "masked_50", "relevance_topk", True, (), (), 1, 1, 1, 1),
        MethodResult("Q1", "masked_50", "pfempire_full_v6", False, (), (), 1, 1, 1, 1),
        MethodResult("Q2", "masked_50", "pfempire_full_v6", True, (), (), 1, 1, 1, 1),
    ]

    comparisons = paired_false_acceptance_rate_comparisons(
        rows,
        {"Q1": False, "Q2": True},
        iterations=100,
    )

    topk = next(row for row in comparisons if row["baseline_method"] == "relevance_topk")
    assert topk["baseline_false_acceptance_rate"] == 0.5
    assert topk["reference_false_acceptance_rate"] == 0.0
    assert topk["absolute_risk_reduction"] == 0.5
    assert topk["relative_risk_reduction"] == 1.0


def test_paired_risk_comparison_marks_zero_coverage_as_undefined() -> None:
    rows = [
        MethodResult("Q1", "masked_50", "relevance_topk", True, (), (), 1, 1, 1, 1),
        MethodResult("Q1", "masked_50", "pfempire_full_v6", False, (), (), 1, 1, 1, 1),
    ]

    topk = next(
        row
        for row in paired_false_acceptance_rate_comparisons(
            rows,
            {"Q1": False},
            iterations=20,
        )
        if row["baseline_method"] == "relevance_topk"
    )

    assert topk["reference_false_acceptance_rate"] is None
    assert topk["absolute_risk_reduction"] is None
    assert topk["valid_bootstrap_replicates"] == 0
