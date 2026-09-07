from itertools import combinations, product

import pytest

from conftest import make_candidate
from originrag.trace.evidence_weights import effective_independent_evidence
from originrag.trace.flow import compute_evidence_flow
from originrag.trace.robust_lineage import (
    LineageDependenceInterval,
    complete_lineage_intervals,
    effective_evidence_bounds,
    rank_lineage_reviews,
    robust_evidence_flow,
)
from originrag.trace.schemas import EvidenceBlueprint


def independent_pair(paper_a: str, paper_b: str) -> LineageDependenceInterval:
    return LineageDependenceInterval(
        paper_id_a=paper_a,
        paper_id_b=paper_b,
        distinct_dependence_lower=0.0,
        distinct_dependence_upper=0.0,
        relation="cannot_link",
        review_status="human_reviewed",
        source="test_review",
    )


def same_unit_pair(paper_a: str, paper_b: str) -> LineageDependenceInterval:
    return LineageDependenceInterval(
        paper_id_a=paper_a,
        paper_id_b=paper_b,
        distinct_dependence_lower=1.0,
        distinct_dependence_upper=1.0,
        relation="must_link",
        review_status="human_reviewed",
        source="test_review",
    )


def complementary_candidates():
    molecular = make_candidate(1, facets={"f1": 1.0, "f2": 0.0})
    clinical = make_candidate(
        2,
        facets={"f1": 0.0, "f2": 1.0},
        scales={"clinical": 1.0},
    ).model_copy(update={"evidence_type_scores": {"observational": 1.0}})
    return molecular, clinical


def test_five_unknown_equal_papers_have_bounds_one_to_five():
    bounds = effective_evidence_bounds([make_candidate(index) for index in range(5)])

    assert bounds.lower == pytest.approx(1.0)
    assert bounds.upper == pytest.approx(5.0)


def test_closed_form_bounds_match_endpoint_scenario_enumeration():
    candidates = [make_candidate(index) for index in range(3)]
    pairs = list(combinations([candidate.paper_id for candidate in candidates], 2))
    intervals = [
        LineageDependenceInterval(
            paper_id_a=paper_a,
            paper_id_b=paper_b,
            distinct_dependence_lower=lower,
            distinct_dependence_upper=upper,
            relation="cannot_link",
            review_status="human_reviewed",
            source="test_interval",
        )
        for (paper_a, paper_b), (lower, upper) in zip(
            pairs,
            [(0.0, 0.7), (0.2, 0.8), (0.0, 0.4)],
            strict=True,
        )
    ]

    bounds = effective_evidence_bounds(candidates, intervals)
    scenarios = []
    for endpoints in product(
        *[
            (edge.distinct_dependence_lower, edge.distinct_dependence_upper)
            for edge in intervals
        ]
    ):
        dependence = {pair: value for pair, value in zip(pairs, endpoints, strict=True)}
        scenarios.append(effective_independent_evidence(candidates, dependence)["n_eff"])

    assert bounds.lower == pytest.approx(min(scenarios))
    assert bounds.upper == pytest.approx(max(scenarios))


def test_point_intervals_reproduce_current_effective_evidence():
    candidates = [make_candidate(index) for index in range(3)]
    intervals = [
        LineageDependenceInterval(
            paper_id_a="P0",
            paper_id_b="P1",
            distinct_dependence_lower=0.25,
            distinct_dependence_upper=0.25,
            relation="cannot_link",
            review_status="human_reviewed",
            source="point_estimate",
        ),
        LineageDependenceInterval(
            paper_id_a="P0",
            paper_id_b="P2",
            distinct_dependence_lower=0.5,
            distinct_dependence_upper=0.5,
            relation="cannot_link",
            review_status="human_reviewed",
            source="point_estimate",
        ),
        independent_pair("P1", "P2"),
    ]
    dependence = {("P0", "P1"): 0.25, ("P0", "P2"): 0.5, ("P1", "P2"): 0.0}

    bounds = effective_evidence_bounds(candidates, intervals)
    current = effective_independent_evidence(candidates, dependence)

    assert bounds.lower == pytest.approx(current["n_eff"])
    assert bounds.upper == pytest.approx(current["n_eff"])


def test_narrower_information_cannot_widen_effective_evidence_bounds():
    candidates = [make_candidate(index) for index in range(3)]
    unknown = effective_evidence_bounds(candidates)
    two_reviews = effective_evidence_bounds(
        candidates,
        [independent_pair("P0", "P1"), independent_pair("P0", "P2")],
    )

    assert two_reviews.lower >= unknown.lower
    assert two_reviews.upper <= unknown.upper
    assert two_reviews.upper - two_reviews.lower < unknown.upper - unknown.lower


def test_effective_bounds_can_be_restricted_to_answer_facets():
    relevant = make_candidate(1, facets={"f1": 1.0})
    irrelevant = make_candidate(2, facets={"unrelated": 1.0})

    bounds = effective_evidence_bounds(
        [relevant, irrelevant],
        [independent_pair("P1", "P2")],
        facet_ids={"f1"},
    )

    assert bounds.substantive_paper_count == 1
    assert bounds.lower == pytest.approx(1.0)
    assert bounds.upper == pytest.approx(1.0)


def test_unknown_lineage_relation_requires_review_when_only_lower_bound_fails(blueprint):
    candidates = list(complementary_candidates())

    result = robust_evidence_flow(candidates, blueprint)

    assert result.status == "lineage_review_required"
    assert result.lineage_count_lower == 1
    assert result.lineage_count_upper == 2
    assert result.missing_pair_count == 1
    assert result.pessimistic_flow.bottleneck_coverage < 1.0
    assert result.optimistic_flow.bottleneck_coverage == 1.0


def test_suggested_pair_decisions_cannot_prove_a_certificate_bound(blueprint):
    candidates = list(complementary_candidates())
    suggested_independent = LineageDependenceInterval(
        paper_id_a="P1",
        paper_id_b="P2",
        distinct_dependence_lower=0.0,
        distinct_dependence_upper=0.0,
        relation="cannot_link",
        review_status="suggested",
        source="model_suggestion",
    )
    suggested_same = LineageDependenceInterval(
        paper_id_a="P1",
        paper_id_b="P2",
        distinct_dependence_lower=1.0,
        distinct_dependence_upper=1.0,
        relation="must_link",
        review_status="suggested",
        source="model_suggestion",
    )

    independent_result = robust_evidence_flow(
        candidates,
        blueprint,
        [suggested_independent],
    )
    same_result = robust_evidence_flow(candidates, blueprint, [suggested_same])

    assert independent_result.status == "lineage_review_required"
    assert same_result.status == "lineage_review_required"
    assert independent_result.effective_evidence.lower == pytest.approx(1.0)
    assert same_result.effective_evidence.upper == pytest.approx(2.0)


def test_reviewed_independence_can_make_certificate_robust(blueprint):
    candidates = list(complementary_candidates())

    result = robust_evidence_flow(candidates, blueprint, [independent_pair("P1", "P2")])

    assert result.status == "robustly_sufficient"
    assert result.effective_evidence.lower == pytest.approx(2.0)
    assert result.lineage_count_lower == 2
    assert result.exact_for_declared_lineage_uncertainty is True
    assert result.joint_optimistic_feasibility == "exists"
    assert set(result.joint_optimistic_partition) == {"P1", "P2"}


@pytest.mark.parametrize(
    "ratio",
    [float("nan"), float("inf"), -0.01, 1.01],
)
def test_robust_certificate_rejects_invalid_effective_evidence_ratio(
    blueprint, ratio
):
    with pytest.raises(ValueError, match="effective_evidence_ratio must be finite"):
        robust_evidence_flow(
            list(complementary_candidates()),
            blueprint,
            effective_evidence_ratio=ratio,
        )


def test_missing_facet_is_insufficient_even_with_optimistic_lineage(blueprint):
    result = robust_evidence_flow([make_candidate(1)], blueprint)

    assert result.status == "insufficient_even_optimistically"
    assert result.optimistic_flow.unresolved_facets == ["f2"]
    assert rank_lineage_reviews([make_candidate(1)], blueprint) == []


def test_merging_units_cannot_improve_flow(blueprint):
    candidates = list(complementary_candidates())
    independent = robust_evidence_flow(
        candidates,
        blueprint,
        [independent_pair("P1", "P2")],
    )
    merged = robust_evidence_flow(
        candidates,
        blueprint,
        [same_unit_pair("P1", "P2")],
    )

    assert merged.optimistic_flow.bottleneck_coverage <= (
        independent.optimistic_flow.bottleneck_coverage
    )
    assert merged.optimistic_flow.normalized_coverage <= (
        independent.optimistic_flow.normalized_coverage
    )


def test_distinct_units_can_retain_partial_dependence(blueprint):
    candidates = list(complementary_candidates())
    partial = LineageDependenceInterval(
        paper_id_a="P1",
        paper_id_b="P2",
        distinct_dependence_lower=0.25,
        distinct_dependence_upper=0.25,
        relation="cannot_link",
        review_status="human_reviewed",
        source="sample_overlap_review",
    )

    result = robust_evidence_flow(candidates, blueprint, [partial])

    assert result.lineage_count_lower == 2
    assert result.lineage_count_upper == 2
    assert result.effective_evidence.lower == pytest.approx(1.6)
    assert result.effective_evidence.upper == pytest.approx(1.6)


def test_cannot_link_closure_uses_safe_component_interval_envelope(blueprint):
    candidates = [make_candidate(index) for index in range(1, 5)]
    intervals = [
        same_unit_pair("P1", "P2"),
        same_unit_pair("P3", "P4"),
        LineageDependenceInterval(
            paper_id_a="P1",
            paper_id_b="P3",
            distinct_dependence_lower=0.1,
            distinct_dependence_upper=0.4,
            relation="cannot_link",
            review_status="human_reviewed",
            source="first_component_statement",
        ),
        LineageDependenceInterval(
            paper_id_a="P2",
            paper_id_b="P4",
            distinct_dependence_lower=0.2,
            distinct_dependence_upper=0.8,
            relation="cannot_link",
            review_status="human_reviewed",
            source="second_component_statement",
        ),
        LineageDependenceInterval(
            paper_id_a="P1",
            paper_id_b="P4",
            distinct_dependence_lower=0.15,
            distinct_dependence_upper=0.5,
            relation="cannot_link",
            review_status="human_reviewed",
            source="third_component_statement",
        ),
        LineageDependenceInterval(
            paper_id_a="P2",
            paper_id_b="P3",
            distinct_dependence_lower=0.12,
            distinct_dependence_upper=0.6,
            relation="cannot_link",
            review_status="human_reviewed",
            source="fourth_component_statement",
        ),
    ]

    complete, _ = complete_lineage_intervals(candidates, intervals)
    cross = [
        edge
        for edge in complete
        if {edge.paper_id_a, edge.paper_id_b}
        & {"P1", "P2"}
        and {edge.paper_id_a, edge.paper_id_b} & {"P3", "P4"}
    ]
    result = robust_evidence_flow(candidates, blueprint, intervals)

    assert len(cross) == 4
    assert all(edge.relation == "cannot_link" for edge in cross)
    assert all(edge.distinct_dependence_lower == pytest.approx(0.1) for edge in cross)
    assert all(edge.distinct_dependence_upper == pytest.approx(0.8) for edge in cross)
    assert result.effective_evidence.lower == pytest.approx(4 / 3.6)


def test_missing_cross_pair_widens_distinct_component_envelope(blueprint):
    candidates = [make_candidate(index) for index in range(1, 5)]
    intervals = [
        same_unit_pair("P1", "P2"),
        same_unit_pair("P3", "P4"),
        LineageDependenceInterval(
            paper_id_a="P1",
            paper_id_b="P3",
            distinct_dependence_lower=0.2,
            distinct_dependence_upper=0.4,
            relation="cannot_link",
            review_status="human_reviewed",
            source="only_reviewed_cross_pair",
        ),
    ]

    complete, missing = complete_lineage_intervals(candidates, intervals)
    cross = [
        edge
        for edge in complete
        if {edge.paper_id_a, edge.paper_id_b} & {"P1", "P2"}
        and {edge.paper_id_a, edge.paper_id_b} & {"P3", "P4"}
    ]
    result = robust_evidence_flow(candidates, blueprint, intervals)

    assert missing == 3
    assert all(edge.relation == "cannot_link" for edge in cross)
    assert all(edge.distinct_dependence_lower == pytest.approx(0.0) for edge in cross)
    assert all(edge.distinct_dependence_upper == pytest.approx(1.0) for edge in cross)
    assert result.effective_evidence.lower == pytest.approx(1.0)


def test_review_plan_can_narrow_dependence_between_distinct_units(blueprint):
    candidates = list(complementary_candidates())
    uncertain_overlap = LineageDependenceInterval(
        paper_id_a="P1",
        paper_id_b="P2",
        distinct_dependence_lower=0.0,
        distinct_dependence_upper=1.0,
        relation="cannot_link",
        review_status="human_reviewed",
        source="distinct_studies_overlap_unknown",
    )

    priorities = rank_lineage_reviews(
        candidates,
        blueprint,
        [uncertain_overlap],
        simulation_budget=None,
    )

    assert len(priorities) == 1
    assert priorities[0].certificate_flip_possible is True


def test_exact_partition_search_tightens_incompatible_may_link_relaxation(blueprint):
    candidates = [make_candidate(index) for index in range(1, 4)]
    intervals = [
        independent_pair("P1", "P3"),
        LineageDependenceInterval(paper_id_a="P1", paper_id_b="P2", source="unknown"),
        LineageDependenceInterval(paper_id_a="P2", paper_id_b="P3", source="unknown"),
    ]

    result = robust_evidence_flow(candidates, blueprint, intervals)

    assert result.relaxed_lineage_count_lower == 1
    assert result.lineage_count_lower == 2
    assert result.partition_bound_method == "exact_search"
    assert result.exact_partition_analysis.completed is True
    assert result.exact_for_declared_lineage_uncertainty is True
    assert any("partition flow is exact" in note for note in result.notes)


def test_transitive_must_closure_overrides_conditional_distinct_interval(blueprint):
    candidates = [make_candidate(index) for index in range(1, 4)]
    intervals = [
        same_unit_pair("P1", "P2"),
        same_unit_pair("P2", "P3"),
        LineageDependenceInterval(
            paper_id_a="P1",
            paper_id_b="P3",
            distinct_dependence_lower=0.0,
            distinct_dependence_upper=0.0,
            relation="may_link",
            review_status="suggested",
            source="conditional_distinct_signal",
        ),
    ]

    result = robust_evidence_flow(candidates, blueprint, intervals)

    assert result.lineage_count_lower == 1
    assert result.lineage_count_upper == 1
    assert result.effective_evidence.lower == pytest.approx(1.0)
    assert result.effective_evidence.upper == pytest.approx(1.0)


def test_shared_resource_cannot_be_declared_independent(blueprint):
    candidates = [
        make_candidate(index).model_copy(update={"dataset_lineage_id": "SHARED"})
        for index in (1, 2)
    ]

    with pytest.raises(ValueError, match="cannot_link contradicts"):
        robust_evidence_flow(candidates, blueprint, [independent_pair("P1", "P2")])


def test_review_ranking_prioritizes_high_weight_uncertain_pair(blueprint):
    strong_a = make_candidate(1)
    strong_b = make_candidate(2)
    weak = make_candidate(3, score=0.0).model_copy(update={"section_quality": 0.1})
    one_facet = blueprint.model_copy(
        update={"required_facets": [blueprint.required_facets[0]]}
    )

    priorities = rank_lineage_reviews(
        [strong_a, strong_b, weak],
        one_facet,
        simulation_budget=None,
    )

    assert priorities
    assert {priorities[0].paper_id_a, priorities[0].paper_id_b} == {"P1", "P2"}


def test_wrong_role_and_polarity_do_not_inflate_answer_specific_neff(blueprint):
    relevant = make_candidate(1)
    wrong_role = make_candidate(2, scales={"clinical": 1.0})
    wrong_polarity = make_candidate(3, polarity={"contradiction": 1.0})
    one_facet = blueprint.model_copy(
        update={"required_facets": [blueprint.required_facets[0]]}
    )
    intervals = [
        independent_pair(left, right)
        for left, right in combinations(["P1", "P2", "P3"], 2)
    ]

    result = robust_evidence_flow(
        [relevant, wrong_role, wrong_polarity],
        one_facet,
        intervals,
    )

    assert result.effective_evidence.substantive_paper_count == 1
    assert result.lineage_count_lower == 1
    assert result.status == "insufficient_even_optimistically"


@pytest.mark.parametrize("section_quality", [0.0, 1e-8])
def test_negligible_flow_quality_cannot_create_a_substantive_lineage(
    blueprint,
    section_quality,
):
    valid = make_candidate(1)
    zero_quality = make_candidate(2).model_copy(
        update={"section_quality": section_quality}
    )
    one_facet = blueprint.model_copy(
        update={"required_facets": [blueprint.required_facets[0]]}
    )

    result = robust_evidence_flow(
        [valid, zero_quality],
        one_facet,
        [independent_pair("P1", "P2")],
    )

    assert result.effective_evidence.substantive_paper_count == 1
    assert result.lineage_count_lower == 1
    assert result.optimistic_flow.active_lineages == ["ROBUST:P1"]
    assert result.status == "insufficient_even_optimistically"


def test_missing_global_polarity_blocks_robust_sufficiency(blueprint):
    candidates = [make_candidate(1), make_candidate(2)]
    polarity_blueprint = blueprint.model_copy(
        update={
            "required_facets": [
                blueprint.required_facets[0].model_copy(
                    update={"polarity_requirement": "any"}
                )
            ],
            "required_polarities": ["support", "contradiction"],
        }
    )

    result = robust_evidence_flow(
        candidates,
        polarity_blueprint,
        [independent_pair("P1", "P2")],
    )

    assert result.missing_required_polarities == ["contradiction"]
    assert result.status == "insufficient_even_optimistically"
    assert result.joint_optimistic_feasibility == "exists"
    assert result.joint_optimistic_partition
    assert any("fixed required-polarity" in note for note in result.notes)


def test_vacuous_blueprint_cannot_produce_a_certificate() -> None:
    empty = EvidenceBlueprint(
        question_id="empty",
        question="Empty certificate",
        task_mode="evidence_qa",
        required_facets=[],
        minimum_independent_lineages=0,
    )

    with pytest.raises(ValueError, match="at least one required facet"):
        robust_evidence_flow([], empty)


def test_facet_dependent_report_origins_fail_closed(blueprint):
    clinical_and_binding = make_candidate(
        2,
        facets={"f1": 1.0, "f2": 1.0},
        scales={"molecular": 1.0, "clinical": 1.0},
    ).model_copy(
        update={
            "facet_origin_ids": {
                "f1": "COHORT_SHARED",
                "f2": "EXPERIMENT_NEW",
            }
        }
    )

    with pytest.raises(
        ValueError,
        match="cannot represent facet-dependent mixed origins",
    ):
        robust_evidence_flow([clinical_and_binding], blueprint)


def test_one_report_origin_across_facets_remains_certifiable(blueprint):
    candidate = make_candidate(
        1,
        facets={"f1": 1.0, "f2": 1.0},
        scales={"molecular": 1.0, "clinical": 1.0},
    ).model_copy(
        update={
            "facet_origin_ids": {
                "f1": "STUDY_ONE",
                "f2": "STUDY_ONE",
            },
            "facet_origin_annotations_complete": True,
        }
    )

    result = robust_evidence_flow([candidate], blueprint)

    assert result.effective_evidence.substantive_paper_count == 1
    assert result.report_origin_homogeneity_verified is True
    assert result.homogeneity_annotated_substantive_paper_count == 1


def test_incomplete_origin_annotations_are_disclosed_as_conditional(blueprint):
    candidate = make_candidate(1).model_copy(
        update={"facet_origin_ids": {"f1": "STUDY_ONE"}}
    )

    result = robust_evidence_flow([candidate], blueprint)

    assert result.report_origin_homogeneity_verified is False
    assert result.homogeneity_annotated_substantive_paper_count == 0
    assert result.substantive_paper_count_for_homogeneity == 1
    assert any("conditional on the unverified homogeneity" in note for note in result.notes)


def test_complete_facet_origin_annotation_requires_every_substantive_facet(blueprint):
    candidate = make_candidate(
        1,
        facets={"f1": 1.0, "f2": 1.0},
        scales={"molecular": 1.0, "clinical": 1.0},
    ).model_copy(
        update={
            "facet_origin_ids": {"f1": "STUDY_ONE"},
            "facet_origin_annotations_complete": True,
        }
    )

    with pytest.raises(ValueError, match="missing substantive required facets"):
        robust_evidence_flow([candidate], blueprint)


def test_unknown_facet_origin_annotation_is_rejected(blueprint):
    candidate = make_candidate(1).model_copy(
        update={"facet_origin_ids": {"misspelled_facet": "STUDY_ONE"}}
    )

    with pytest.raises(ValueError, match="outside the required blueprint"):
        robust_evidence_flow([candidate], blueprint)


def test_identical_explicit_origins_require_must_link(blueprint):
    candidates = [
        make_candidate(index).model_copy(
            update={"facet_origin_ids": {"f1": "STUDY_SHARED"}}
        )
        for index in (1, 2)
    ]

    with pytest.raises(ValueError, match="require a must_link relation"):
        robust_evidence_flow(candidates, blueprint, [independent_pair("P1", "P2")])

    result = robust_evidence_flow(
        candidates,
        blueprint,
        [same_unit_pair("P1", "P2")],
    )
    assert result.lineage_count_lower == 1


def test_distinct_explicit_origins_reject_must_link_even_when_incomplete(blueprint):
    candidates = [
        make_candidate(1).model_copy(
            update={"facet_origin_ids": {"f1": "STUDY_ONE"}}
        ),
        make_candidate(2).model_copy(
            update={"facet_origin_ids": {"f1": "STUDY_TWO"}}
        ),
    ]

    with pytest.raises(ValueError, match="distinct explicit origin IDs contradict"):
        robust_evidence_flow(
            candidates,
            blueprint,
            [same_unit_pair("P1", "P2")],
        )


def test_mixed_origin_report_counterexample_has_no_equivalent_report_partition(
    blueprint,
):
    counterexample_blueprint = blueprint.model_copy(
        update={
            "required_facets": [
                blueprint.required_facets[0].model_copy(
                    update={"minimum_items": 2}
                ),
                blueprint.required_facets[1],
            ]
        }
    )
    report_a = make_candidate(1, facets={"f1": 1.0, "f2": 0.0})
    report_b = make_candidate(
        2,
        facets={"f1": 1.0, "f2": 1.0},
        scales={"molecular": 1.0, "clinical": 1.0},
    ).model_copy(
        update={
            "evidence_type_scores": {
                "biochemical": 1.0,
                "observational": 1.0,
            }
        }
    )
    clinical_atom = report_a.model_copy(
        update={"paper_id": "ORIGIN_CLINICAL", "chunk_id": "ATOM_CLINICAL"}
    )
    binding_atom = report_b.model_copy(
        update={
            "paper_id": "ORIGIN_BINDING",
            "chunk_id": "ATOM_BINDING",
            "facet_scores": {"f1": 0.0, "f2": 1.0},
        }
    )

    contribution_flow = compute_evidence_flow(
        [clinical_atom, binding_atom],
        counterexample_blueprint,
        unit_by_paper={
            "ORIGIN_CLINICAL": "ORIGIN_CLINICAL",
            "ORIGIN_BINDING": "ORIGIN_BINDING",
        },
    )
    report_must_link_flow = compute_evidence_flow(
        [report_a, report_b],
        counterexample_blueprint,
        unit_by_paper={"P1": "SHARED_REPORT_UNIT", "P2": "SHARED_REPORT_UNIT"},
    )
    report_separate_flow = compute_evidence_flow(
        [report_a, report_b],
        counterexample_blueprint,
        unit_by_paper={"P1": "P1", "P2": "P2"},
    )

    assert contribution_flow.bottleneck_coverage == pytest.approx(5 / 6)
    assert report_must_link_flow.bottleneck_coverage == pytest.approx(5 / 9)
    assert report_separate_flow.bottleneck_coverage == pytest.approx(1.0)

    false_certificate = robust_evidence_flow(
        [report_a, report_b],
        counterexample_blueprint,
        [independent_pair("P1", "P2")],
    )
    assert false_certificate.status == "robustly_sufficient"
    assert false_certificate.lineage_count_lower == 2
    assert false_certificate.effective_evidence.lower == pytest.approx(2.0)


@pytest.mark.parametrize("section_quality", [0.0, 1e-8])
def test_negligible_quality_chunk_cannot_satisfy_required_polarity(
    blueprint,
    section_quality,
):
    support = make_candidate(1)
    unusable_contradiction = make_candidate(
        2,
        polarity={"contradiction": 1.0},
    ).model_copy(update={"section_quality": section_quality})
    polarity_blueprint = blueprint.model_copy(
        update={
            "required_facets": [
                blueprint.required_facets[0].model_copy(
                    update={"polarity_requirement": "any"}
                )
            ],
            "minimum_independent_lineages": 1,
            "required_polarities": ["support", "contradiction"],
        }
    )

    result = robust_evidence_flow(
        [support, unusable_contradiction],
        polarity_blueprint,
        [independent_pair("P1", "P2")],
    )

    assert result.missing_required_polarities == ["contradiction"]
    assert result.status == "insufficient_even_optimistically"


def test_adding_a_must_linked_duplicate_is_neff_invariant():
    first_two = [make_candidate(1), make_candidate(2)]
    with_duplicate = [*first_two, make_candidate(3)]
    independent = effective_evidence_bounds(
        first_two,
        [independent_pair("P1", "P2")],
    )
    duplicate = effective_evidence_bounds(
        with_duplicate,
        [
            independent_pair("P1", "P2"),
            same_unit_pair("P1", "P3"),
            independent_pair("P2", "P3"),
        ],
    )

    assert independent.lower == pytest.approx(2.0)
    assert duplicate.lower == pytest.approx(independent.lower)
    assert duplicate.upper == pytest.approx(independent.upper)


def test_repeating_an_exact_chunk_cannot_flip_certificate(blueprint):
    weak = make_candidate(1, score=0.0)
    strong = make_candidate(2, score=100.0)
    duplicate_chunk = weak.model_copy(update={"chunk_id": "C1_DUPLICATE"})
    one_facet = blueprint.model_copy(
        update={"required_facets": [blueprint.required_facets[0]]}
    )
    intervals = [independent_pair("P1", "P2")]

    base = robust_evidence_flow([weak, strong], one_facet, intervals)
    repeated = robust_evidence_flow(
        [weak, duplicate_chunk, strong],
        one_facet,
        intervals,
    )

    assert base.status == "insufficient_even_optimistically"
    assert repeated.status == base.status
    assert repeated.effective_evidence.lower == pytest.approx(
        base.effective_evidence.lower
    )
    assert repeated.effective_evidence.upper == pytest.approx(
        base.effective_evidence.upper
    )


def test_cloning_a_must_linked_report_cannot_flip_certificate(blueprint):
    weak = make_candidate(1, score=0.0)
    strong = make_candidate(2, score=100.0)
    clone = make_candidate(3, score=0.0)
    one_facet = blueprint.model_copy(
        update={"required_facets": [blueprint.required_facets[0]]}
    )
    base = robust_evidence_flow(
        [weak, strong],
        one_facet,
        [independent_pair("P1", "P2")],
    )
    cloned = robust_evidence_flow(
        [weak, strong, clone],
        one_facet,
        [
            same_unit_pair("P1", "P3"),
            independent_pair("P1", "P2"),
            independent_pair("P2", "P3"),
        ],
    )

    assert base.status == "insufficient_even_optimistically"
    assert cloned.status == base.status
    assert cloned.effective_evidence.lower == pytest.approx(
        base.effective_evidence.lower
    )
    assert cloned.effective_evidence.upper == pytest.approx(
        base.effective_evidence.upper
    )


def test_invalid_intervals_and_duplicate_pairs_fail_closed():
    with pytest.raises(ValueError, match="cannot exceed"):
        LineageDependenceInterval(
            paper_id_a="A",
            paper_id_b="B",
            distinct_dependence_lower=0.8,
            distinct_dependence_upper=0.2,
        )
    candidates = [make_candidate(1), make_candidate(2)]
    pair = independent_pair("P1", "P2")
    with pytest.raises(ValueError, match="duplicate lineage interval"):
        effective_evidence_bounds(candidates, [pair, pair])


def test_relaxed_bounds_contain_every_small_partition_scenario(blueprint):
    molecular, clinical = complementary_candidates()
    candidates = [
        molecular,
        clinical,
        make_candidate(3, facets={"f1": 1.0, "f2": 1.0}).model_copy(
            update={
                "scale_scores": {"molecular": 1.0, "clinical": 1.0},
                "evidence_type_scores": {
                    "biochemical": 1.0,
                    "observational": 1.0,
                },
            }
        ),
        make_candidate(4, facets={"f1": 1.0}),
    ]
    robust = robust_evidence_flow(candidates, blueprint)
    paper_ids = [candidate.paper_id for candidate in candidates]

    for labels in product(range(len(paper_ids)), repeat=len(paper_ids)):
        if labels[0] != 0 or set(labels) != set(range(max(labels) + 1)):
            continue
        unit_by_paper = dict(zip(paper_ids, map(str, labels), strict=True))
        scenario_flow = compute_evidence_flow(
            candidates,
            blueprint,
            unit_by_paper=unit_by_paper,
        )
        dependence = {
            (paper_a, paper_b): float(labels[index_a] == labels[index_b])
            for index_a, paper_a in enumerate(paper_ids)
            for index_b, paper_b in enumerate(paper_ids)
            if index_a < index_b
        }
        scenario_neff = effective_independent_evidence(candidates, dependence)["n_eff"]

        assert robust.pessimistic_flow.bottleneck_coverage <= (
            scenario_flow.bottleneck_coverage + 1e-9
        )
        assert scenario_flow.bottleneck_coverage <= (
            robust.optimistic_flow.bottleneck_coverage + 1e-9
        )
        assert robust.effective_evidence.lower <= scenario_neff + 1e-9
        assert scenario_neff <= robust.effective_evidence.upper + 1e-9
