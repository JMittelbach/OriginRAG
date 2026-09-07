import pytest

from conftest import make_candidate

from originrag.trace.schemas import EvidenceBlueprint
from originrag.trace.flow_selector import can_add_flow, retrieval_anchors
from originrag.trace.selector import SelectorConfig, select_evidence


@pytest.mark.parametrize("budget,expected", [(99, False), (100, True), (101, True)])
def test_covered_facets_skip_unused_alternatives(blueprint, budget, expected):
    from originrag.trace.flow_selector import preserves_repair_budget

    class UnusedAlternatives(list):
        def __iter__(self):
            raise AssertionError("covered facets must not scan alternatives")

    single = blueprint.model_copy(update={"required_facets": blueprint.required_facets[:1]})
    assert preserves_repair_budget(make_candidate(1), [], UnusedAlternatives(), single, budget) is expected


def test_repair_budget_matches_original_rule_on_random_inputs(blueprint):
    import random
    from originrag.trace.flow_selector import facet_contribution, preserves_repair_budget, token_cost

    def reference(candidate, selected, remaining, specification, token_budget):
        proposed = [*selected, candidate]
        remaining_budget = token_budget - token_cost(proposed)
        uncovered = [f for f in specification.required_facets if f.is_required
                     and not any(facet_contribution(item, f) > 0.0 for item in proposed)]
        required_budget = 0
        alternatives = [item for item in remaining if item != candidate]
        for facet in uncovered:
            costs = [item.token_cost for item in alternatives if facet_contribution(item, facet) > 0.0]
            if costs:
                required_budget += min(costs)
        return required_budget <= remaining_budget

    rng = random.Random(20260906)
    for _ in range(1000):
        candidates = [make_candidate(i, tokens=rng.randint(0, 300),
                                    facets={f: rng.choice([0.0, 1e-12, 0.49, 0.5, 1.0]) for f in ["f1", "f2"]},
                                    scales={s: rng.choice([0.0, 1.0]) for s in ["molecular", "clinical"]})
                      .model_copy(update={"evidence_type_scores": {"biochemical": 1.0, "observational": 1.0},
                                          "section_quality": rng.choice([0.0, 0.7, 1.0])})
                      for i in range(6)]
        candidate = candidates[0]
        selected = candidates[1:rng.randint(1, 5)]
        remaining = [*candidates, candidate.model_copy(deep=True)]
        budget = rng.randint(-1, 1000)
        specification = blueprint.model_copy(update={"required_facets": blueprint.required_facets[:rng.randint(0, 2)]})
        assert preserves_repair_budget(candidate, selected, remaining, specification, budget) == reference(
            candidate, selected, remaining, specification, budget)


def test_flow_selector_repairs_missing_facet_before_relevance(blueprint):
    candidates = [
        make_candidate(1, facets={"f1": 1.0, "f2": 0.0}, score=100),
        make_candidate(2, facets={"f1": 1.0, "f2": 0.0}, score=90),
        make_candidate(3, facets={"f1": 0.0, "f2": 1.0}, scales={"clinical": 1.0}, score=5).model_copy(
            update={"evidence_type_scores": {"observational": 1.0}}
        ),
    ]
    config = SelectorConfig(
        token_budget=300,
        flow_anchor_papers=0,
        flow_minimum_papers=2,
    )
    result = select_evidence(
        candidates,
        blueprint,
        {},
        selector_name="trace_flow_no_anchor",
        config=config,
    )
    assert "C3" in {candidate.chunk_id for candidate in result.selected_candidates}
    assert result.run_config["algorithm"] == "lineage_capacitated_max_min_flow"


def test_zero_anchor_limit_selects_no_retrieval_anchors() -> None:
    candidates = [make_candidate(1), make_candidate(2)]

    assert retrieval_anchors(candidates, 0) == []


def test_flow_selector_retains_distinct_retrieval_anchors(blueprint):
    candidates = [make_candidate(index, score=100 - index) for index in range(1, 6)]
    config = SelectorConfig(
        token_budget=500,
        flow_anchor_papers=3,
        flow_minimum_papers=3,
    )
    result = select_evidence(
        candidates,
        blueprint,
        {},
        selector_name="trace_flow",
        config=config,
    )
    reasons = [row["reason"] for row in result.run_config["decision_trace"]]
    assert reasons[:3] == ["retrieval_anchor"] * 3
    assert {"C1", "C2", "C3"}.issubset(
        {candidate.chunk_id for candidate in result.selected_candidates}
    )


def test_named_study_front_anchor_precedes_broader_support_from_same_paper() -> None:
    front_anchor = make_candidate(1, paper="P_NAMED", score=10_000).model_copy(
        update={
            "metadata": {
                "raw_result": {
                    "chunk_id": "C1",
                    "paper_id": "P_NAMED",
                    "score": 10_000,
                    "contributing_queries": ["EXPLICIT_NAMED_STUDY"],
                    "explicit_named_study_anchor": True,
                }
            }
        }
    )
    broader_support = make_candidate(2, paper="P_NAMED", score=100).model_copy(
        update={
            "metadata": {
                "raw_result": {
                    "chunk_id": "C2",
                    "paper_id": "P_NAMED",
                    "score": 100,
                    "contributing_queries": ["Q1", "Q2", "Q3", "Q4"],
                    "explicit_named_study_support": True,
                }
            }
        }
    )

    anchors = retrieval_anchors([broader_support, front_anchor], 1)

    assert [candidate.chunk_id for candidate in anchors] == ["C1"]


def test_flow_retrieval_anchors_are_distinct_study_lineages(blueprint):
    candidates = [
        make_candidate(1, score=100).model_copy(update={"study_lineage_id": "L_SHARED"}),
        make_candidate(2, score=99).model_copy(update={"study_lineage_id": "L_SHARED"}),
        make_candidate(3, score=80).model_copy(update={"study_lineage_id": "L_INDEPENDENT_1"}),
        make_candidate(4, score=70).model_copy(update={"study_lineage_id": "L_INDEPENDENT_2"}),
    ]
    config = SelectorConfig(
        token_budget=300,
        flow_anchor_papers=3,
        flow_minimum_papers=3,
    )

    result = select_evidence(
        candidates,
        blueprint,
        {},
        selector_name="trace_flow",
        config=config,
    )

    anchor_rows = [
        row for row in result.run_config["decision_trace"] if row["reason"] == "retrieval_anchor"
    ]
    assert [row["study_lineage_id"] for row in anchor_rows] == [
        "L_SHARED",
        "L_INDEPENDENT_1",
        "L_INDEPENDENT_2",
    ]


def test_flow_retrieval_anchors_skip_strong_partial_dependence(blueprint):
    candidates = [
        make_candidate(1, score=100),
        make_candidate(2, score=99),
        make_candidate(3, score=80),
    ]
    dependence = {("P1", "P2"): 0.75, ("P2", "P1"): 0.75}

    anchors = retrieval_anchors(candidates, 2, dependence=dependence)

    assert [candidate.paper_id for candidate in anchors] == ["P1", "P3"]


def test_required_counterevidence_changes_selection(blueprint):
    constrained: EvidenceBlueprint = blueprint.model_copy(
        update={"required_polarities": ["support", "contradiction"]}
    )
    support = make_candidate(1, polarity={"support": 1.0}, score=100)
    contradiction = make_candidate(2, polarity={"contradiction": 1.0}, score=1)
    config = SelectorConfig(
        token_budget=200,
        flow_anchor_papers=0,
        flow_minimum_papers=1,
    )
    result = select_evidence(
        [support, contradiction],
        constrained,
        {},
        selector_name="trace_flow_no_anchor",
        config=config,
    )
    assert contradiction.chunk_id in {candidate.chunk_id for candidate in result.selected_candidates}


def test_flow_selector_is_deterministic(blueprint):
    candidates = [make_candidate(index, score=20) for index in range(1, 8)]
    config = SelectorConfig(token_budget=600, flow_anchor_papers=2, flow_minimum_papers=4)
    first = select_evidence(candidates, blueprint, {}, selector_name="trace_flow", config=config)
    second = select_evidence(candidates, blueprint, {}, selector_name="trace_flow", config=config)
    assert [candidate.chunk_id for candidate in first.selected_candidates] == [
        candidate.chunk_id for candidate in second.selected_candidates
    ]


def test_selector_config_rejects_invalid_resource_limits() -> None:
    with pytest.raises(ValueError, match="token_budget"):
        SelectorConfig(token_budget=0)
    with pytest.raises(ValueError, match="flow_anchor_papers"):
        SelectorConfig(flow_anchor_papers=-1)
    with pytest.raises(ValueError, match="token_budget"):
        SelectorConfig(token_budget=float("nan"))
    with pytest.raises(ValueError, match="minimum_marginal_gain"):
        SelectorConfig(minimum_marginal_gain=float("inf"))


def test_flow_preserves_budget_for_uncovered_required_facet(blueprint):
    expensive = make_candidate(1, facets={"f1": 1.0, "f2": 0.0}, score=100, tokens=180)
    cheap = make_candidate(2, facets={"f1": 1.0, "f2": 0.0}, score=10, tokens=50)
    complement = make_candidate(
        3,
        facets={"f1": 0.0, "f2": 1.0},
        scales={"clinical": 1.0},
        score=9,
        tokens=50,
    ).model_copy(update={"evidence_type_scores": {"observational": 1.0}})
    result = select_evidence(
        [expensive, cheap, complement],
        blueprint.model_copy(update={"minimum_independent_lineages": 2}),
        {},
        selector_name="trace_flow_no_anchor",
        config=SelectorConfig(
            token_budget=200,
            flow_anchor_papers=0,
            flow_minimum_papers=2,
        ),
    )

    assert {candidate.chunk_id for candidate in result.selected_candidates} == {"C2", "C3"}
    assert result.unresolved_facets == []


def test_flow_family_limit_allows_multiple_chunks_from_same_paper(blueprint):
    candidates = [
        make_candidate(1, paper="P1", family="F1", tokens=50),
        make_candidate(2, paper="P1", family="F1", tokens=50),
    ]
    config = SelectorConfig(
        token_budget=100,
        max_chunks_per_paper=2,
        max_publication_versions_per_family=1,
        flow_anchor_papers=0,
        flow_minimum_papers=1,
    )

    assert can_add_flow(candidates[1], [candidates[0]], config)


def test_diverse_reserve_prefers_complement_above_relevance_floor(blueprint):
    anchors = [
        make_candidate(1, facets={"f1": 1.0, "f2": 1.0}, scales={"molecular": 1.0, "clinical": 1.0}, score=100).model_copy(update={"evidence_type_scores": {"biochemical": 1.0, "observational": 1.0}}),
        make_candidate(2, facets={"f1": 1.0, "f2": 1.0}, scales={"molecular": 1.0, "clinical": 1.0}, score=90).model_copy(update={"evidence_type_scores": {"biochemical": 1.0, "observational": 1.0}}),
    ]
    duplicate = make_candidate(3, facets={}, score=80).model_copy(
        update={"text": anchors[0].text, "facet_scores": {}, "evidence_type_scores": {"biochemical": 1.0}}
    )
    complement = make_candidate(4, facets={}, score=70).model_copy(
        update={
            "text": "longitudinal population cohort follow-up with distinct clinical outcomes",
            "facet_scores": {},
            "evidence_type_scores": {"longitudinal": 1.0},
            "scale_scores": {"population": 1.0},
        }
    )
    expanded = blueprint.model_copy(
        update={
            "preferred_evidence_types": ["biochemical", "longitudinal"],
            "preferred_scales": ["molecular", "population"],
        }
    )
    config = SelectorConfig(
        token_budget=300,
        flow_anchor_papers=2,
        flow_minimum_papers=3,
        flow_reserve_relevance_fraction=0.50,
    )
    diverse = select_evidence(
        [*anchors, duplicate, complement], expanded, {}, selector_name="trace_flow_diverse_reserve", config=config
    )
    relevance = select_evidence(
        [*anchors, duplicate, complement],
        expanded,
        {},
        selector_name="trace_flow",
        config=config,
    )
    assert "C4" in {candidate.chunk_id for candidate in diverse.selected_candidates}
    assert "C3" in {candidate.chunk_id for candidate in relevance.selected_candidates}
