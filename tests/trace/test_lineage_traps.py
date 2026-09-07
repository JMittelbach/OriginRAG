import pytest

from pfempire.trace.evaluation import evaluate_selector
from pfempire.trace.lineage_traps import (
    build_annotation_perturbation_scenarios,
    build_lineage_trap_scenarios,
    gold_independence_metrics,
    lineage_annotation_quality,
    trap_avoided,
)
from pfempire.trace.selector import SelectorConfig


def config_for(scenario) -> SelectorConfig:
    return SelectorConfig(
        token_budget=scenario.token_budget,
        max_chunks_per_paper=1,
        minimum_marginal_gain=0.0,
        minimum_selected_papers=scenario.flow_minimum_papers,
        flow_anchor_papers=scenario.flow_anchor_papers,
        flow_minimum_papers=scenario.flow_minimum_papers,
    )


def evaluate(scenario, selector: str) -> dict:
    return evaluate_selector(
        selector,
        list(scenario.candidates),
        scenario.blueprint,
        scenario.dependence,
        config=config_for(scenario),
        lineage_graph_status="reviewed",
    )


def test_shared_cohort_trap_distinguishes_publications_from_evidence():
    scenario = build_lineage_trap_scenarios()[0]
    topk = evaluate(scenario, "topk_relevance")
    flow = evaluate(scenario, "trace_flow")

    assert topk["raw_paper_count"] == 3
    assert topk["evidence_lineage_count"] == 1
    assert topk["evidence_inflation_factor"] == pytest.approx(3.0)
    assert flow["evidence_lineage_count"] == 3
    assert flow["evidence_inflation_factor"] == pytest.approx(1.0, abs=0.001)
    assert trap_avoided(scenario, flow["selection"].selected_candidates)


def test_lineage_capacity_blocks_complementary_papers_from_one_dataset():
    scenario = build_lineage_trap_scenarios()[1]
    bounded = evaluate(scenario, "trace_flow")
    unbounded = evaluate(scenario, "trace_flow_unbounded")

    assert bounded["evidence_lineage_count"] == 2
    assert unbounded["evidence_lineage_count"] == 1
    assert trap_avoided(scenario, bounded["selection"].selected_candidates)
    assert not trap_avoided(scenario, unbounded["selection"].selected_candidates)


def test_trace_flow_avoids_all_controlled_lineage_traps():
    primary = [
        scenario
        for scenario in build_lineage_trap_scenarios()
        if scenario.evaluation_role == "primary_trap"
    ]
    for scenario in primary:
        result = evaluate(scenario, "trace_flow")
        assert trap_avoided(scenario, result["selection"].selected_candidates)


def test_missing_lineage_labels_expose_expected_method_dependency():
    scenario = next(
        item
        for item in build_lineage_trap_scenarios()
        if item.trap_type == "missing_lineage_annotations"
    )
    result = evaluate(scenario, "trace_flow")

    assert not trap_avoided(scenario, result["selection"].selected_candidates)


def test_false_positive_lineage_labels_trade_relevance_for_apparent_independence():
    scenario = next(
        item
        for item in build_lineage_trap_scenarios()
        if item.trap_type == "false_positive_lineage_annotations"
    )
    topk = evaluate(scenario, "topk_relevance")
    flow = evaluate(scenario, "trace_flow")

    assert trap_avoided(scenario, topk["selection"].selected_candidates)
    assert trap_avoided(scenario, flow["selection"].selected_candidates)
    assert flow["mean_retrieval_relevance"] < topk["mean_retrieval_relevance"]


def test_gold_independence_exposes_missing_lineage_overcount():
    scenario = next(
        item
        for item in build_lineage_trap_scenarios()
        if item.trap_type == "missing_lineage_annotations"
    )
    result = evaluate(scenario, "topk_relevance")
    gold = gold_independence_metrics(
        scenario, result["selection"].selected_candidates
    )

    assert result["effective_independent_evidence"] == pytest.approx(3.0)
    assert gold["gold_effective_independent_evidence"] == pytest.approx(1.0)


def test_annotation_sweep_has_expected_pairwise_error_levels():
    scenarios = {
        scenario.scenario_id: scenario
        for scenario in build_annotation_perturbation_scenarios()
    }

    exact = lineage_annotation_quality(scenarios["LT_ANNOTATION_EXACT"])
    missed_one = lineage_annotation_quality(scenarios["LT_ANNOTATION_RECALL_033"])
    missed_all = lineage_annotation_quality(scenarios["LT_ANNOTATION_RECALL_000"])
    false_one = lineage_annotation_quality(scenarios["LT_ANNOTATION_PRECISION_050"])
    false_two = lineage_annotation_quality(scenarios["LT_ANNOTATION_PRECISION_030"])

    assert exact == {
        "lineage_annotation_precision": 1.0,
        "lineage_annotation_recall": 1.0,
    }
    assert missed_one["lineage_annotation_recall"] == pytest.approx(1 / 3)
    assert missed_all["lineage_annotation_recall"] == 0.0
    assert false_one["lineage_annotation_precision"] == pytest.approx(0.5)
    assert false_two["lineage_annotation_precision"] == pytest.approx(0.3)


def test_trace_flow_only_avoids_sweep_trap_with_exact_labels():
    outcomes = {}
    for scenario in build_annotation_perturbation_scenarios():
        result = evaluate(scenario, "trace_flow")
        outcomes[scenario.scenario_id] = trap_avoided(
            scenario, result["selection"].selected_candidates
        )

    assert outcomes.pop("LT_ANNOTATION_EXACT")
    assert not any(outcomes.values())


def test_trap_requires_substantive_facet_coverage_not_only_lineage_count():
    scenario = build_lineage_trap_scenarios()[0]
    irrelevant = [
        candidate.model_copy(update={"facet_scores": {}})
        for candidate in scenario.candidates[:3]
    ]

    assert not trap_avoided(scenario, irrelevant)
