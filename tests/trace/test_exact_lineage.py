import random
from itertools import combinations, product

import pytest

from conftest import make_candidate
import pfempire.trace.exact_lineage as exact_lineage_module
from pfempire.trace.exact_lineage import (
    exact_partition_flow_analysis,
    verify_exact_partition_witnesses,
)
from pfempire.trace.flow import compute_evidence_flow
from pfempire.trace.method_versions import (
    COMPLETE_BOX_PREFIX_EXACT_METHOD,
    COMPLETE_BOX_SUBSET_EXACT_METHOD,
    EXACT_PARTITION_ENUMERATION_METHOD,
    EXTENDED_CONSTRAINED_PARTITION_METHOD,
    ROBUST_LINEAGE_CERTIFICATE_VERSION,
)
from pfempire.trace.robust_lineage import (
    LineageDependenceInterval,
    robust_evidence_flow,
)
from pfempire.trace.schemas import EvidenceBlueprint, EvidenceFacet


def relation(
    paper_a: str,
    paper_b: str,
    kind: str,
) -> LineageDependenceInterval:
    same = kind == "must_link"
    return LineageDependenceInterval(
        paper_id_a=paper_a,
        paper_id_b=paper_b,
        distinct_dependence_lower=float(same),
        distinct_dependence_upper=float(same),
        relation=kind,
        review_status="human_reviewed",
        source="exact_test",
    )


def all_facet_candidate(index: int):
    return make_candidate(
        index,
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


def test_exact_search_matches_exhaustive_partition_oracle(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 5)]
    relations = [
        relation("P1", "P2", "must_link"),
        relation("P1", "P3", "cannot_link"),
    ]

    exact = exact_partition_flow_analysis(
        candidates,
        blueprint,
        relations,
        max_components=9,
        max_search_states=100_000,
    )
    oracle = []
    for labels in product(range(4), repeat=4):
        if labels[0] != 0 or set(labels) != set(range(max(labels) + 1)):
            continue
        if labels[0] != labels[1] or labels[0] == labels[2]:
            continue
        units = {
            candidate.paper_id: str(labels[index]) for index, candidate in enumerate(candidates)
        }
        flow = compute_evidence_flow(candidates, blueprint, unit_by_paper=units)
        oracle.append((flow, len(set(labels))))
    oracle_flow = min(
        (flow for flow, _ in oracle),
        key=lambda flow: (
            flow.bottleneck_coverage,
            flow.normalized_coverage,
            flow.total_flow,
        ),
    )

    assert exact.completed is True
    assert exact.pessimistic_flow is not None
    assert exact.pessimistic_flow.bottleneck_coverage == pytest.approx(
        oracle_flow.bottleneck_coverage
    )
    assert exact.pessimistic_flow.normalized_coverage == pytest.approx(
        oracle_flow.normalized_coverage
    )
    assert exact.lineage_count_lower == min(count for _, count in oracle)


def test_effective_evidence_ratio_default_is_legacy_equivalent(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 4)]
    relations = [
        LineageDependenceInterval(paper_id_a="P1", paper_id_b="P2"),
        relation("P1", "P3", "cannot_link"),
        relation("P2", "P3", "cannot_link"),
    ]
    weights = {candidate.paper_id: 1.0 for candidate in candidates}

    implicit_exact = exact_partition_flow_analysis(
        candidates,
        blueprint,
        relations,
        paper_weights=weights,
    )
    explicit_exact = exact_partition_flow_analysis(
        candidates,
        blueprint,
        relations,
        effective_evidence_ratio=0.90,
        paper_weights=weights,
    )
    implicit_robust = robust_evidence_flow(candidates, blueprint, relations)
    explicit_robust = robust_evidence_flow(
        candidates,
        blueprint,
        relations,
        effective_evidence_ratio=0.90,
    )

    assert implicit_exact.model_dump(mode="json") == explicit_exact.model_dump(mode="json")
    assert implicit_robust.model_dump(mode="json") == explicit_robust.model_dump(mode="json")


def test_facet_flow_gate_default_is_legacy_equivalent(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 4)]
    relations = [
        relation(left, right, "cannot_link") for left, right in combinations(["P1", "P2", "P3"], 2)
    ]

    implicit = robust_evidence_flow(candidates, blueprint, relations)
    explicit = robust_evidence_flow(
        candidates,
        blueprint,
        relations,
        require_facet_flow=True,
        force_safe_relaxation=False,
    )

    assert implicit.model_dump(mode="json") == explicit.model_dump(mode="json")


def test_no_facet_flow_ablation_removes_only_the_flow_gate(blueprint):
    candidates = [make_candidate(index, facets={"f1": 1.0, "f2": 0.0}) for index in range(1, 4)]
    relations = [
        relation(left, right, "cannot_link") for left, right in combinations(["P1", "P2", "P3"], 2)
    ]

    full = robust_evidence_flow(candidates, blueprint, relations)
    ablated = robust_evidence_flow(
        candidates,
        blueprint,
        relations,
        require_facet_flow=False,
    )

    assert full.effective_evidence == ablated.effective_evidence
    assert full.lineage_count_lower == ablated.lineage_count_lower == 3
    assert full.pessimistic_flow == ablated.pessimistic_flow
    assert full.pessimistic_flow.unresolved_facets == ["f2"]
    assert full.status == "insufficient_even_optimistically"
    assert ablated.status == "robustly_sufficient"
    assert ablated.exact_partition_analysis.joint_partition_pass_exists is True


def test_forced_safe_relaxation_bypasses_even_a_unique_exact_partition(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 4)]
    relations = [
        relation(left, right, "cannot_link") for left, right in combinations(["P1", "P2", "P3"], 2)
    ]

    exact = robust_evidence_flow(candidates, blueprint, relations)
    forced = robust_evidence_flow(
        candidates,
        blueprint,
        relations,
        force_safe_relaxation=True,
    )

    assert exact.partition_bound_method == "exact_search"
    assert exact.exact_partition_analysis.completed is True
    assert forced.partition_bound_method == "safe_relaxation"
    assert forced.exact_partition_analysis.completed is False
    assert forced.exact_partition_analysis.reason == "forced_safe_relaxation"
    assert forced.pessimistic_flow == forced.relaxed_pessimistic_flow


def test_effective_evidence_ratio_changes_exact_joint_boundary(blueprint):
    candidates = [all_facet_candidate(index) for index in (1, 2)]
    partially_dependent = LineageDependenceInterval(
        paper_id_a="P1",
        paper_id_b="P2",
        distinct_dependence_lower=0.20,
        distinct_dependence_upper=0.20,
        relation="cannot_link",
        review_status="human_reviewed",
        source="ratio_boundary",
    )

    legacy = robust_evidence_flow(
        candidates,
        blueprint,
        [partially_dependent],
        effective_evidence_ratio=0.90,
    )
    relaxed = robust_evidence_flow(
        candidates,
        blueprint,
        [partially_dependent],
        effective_evidence_ratio=0.80,
    )

    assert legacy.effective_evidence.lower == pytest.approx(5.0 / 3.0)
    assert relaxed.effective_evidence.lower == pytest.approx(5.0 / 3.0)
    assert legacy.required_effective_evidence == pytest.approx(1.8)
    assert relaxed.required_effective_evidence == pytest.approx(1.6)
    assert legacy.exact_partition_analysis.joint_partition_pass_exists is False
    assert relaxed.exact_partition_analysis.joint_partition_pass_exists is True
    assert legacy.status == "insufficient_even_optimistically"
    assert relaxed.status == "robustly_sufficient"


def test_exact_search_tightens_relaxation_and_can_certify(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 4)]
    one_lineage_required = blueprint.model_copy(update={"minimum_independent_lineages": 1})
    intervals = [
        relation("P1", "P3", "cannot_link"),
        LineageDependenceInterval(paper_id_a="P1", paper_id_b="P2"),
        LineageDependenceInterval(paper_id_a="P2", paper_id_b="P3"),
    ]

    result = robust_evidence_flow(
        candidates,
        one_lineage_required,
        intervals,
    )

    assert result.relaxed_pessimistic_flow.bottleneck_coverage < 1.0
    assert result.pessimistic_flow.bottleneck_coverage == pytest.approx(1.0)
    assert result.relaxed_lineage_count_lower == 1
    assert result.lineage_count_lower == 2
    assert result.partition_relaxation_bottleneck_gap > 0.0
    assert result.status == "robustly_sufficient"


def test_completed_exact_search_replays_and_marks_endpoint_witnesses(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 4)]
    relations = [
        relation(left, right, "cannot_link") for left, right in combinations(["P1", "P2", "P3"], 2)
    ]
    weights = {candidate.paper_id: 1.0 for candidate in candidates}

    exact = exact_partition_flow_analysis(
        candidates,
        blueprint,
        relations,
        paper_weights=weights,
    )

    assert exact.completed is True
    assert exact.witnesses_verified is True
    assert exact.witness_verification_method == "recomputed_endpoint_witnesses_v1"
    assert set(exact.lineage_count_partition) == {"P1", "P2", "P3"}
    verify_exact_partition_witnesses(
        candidates,
        blueprint,
        relations,
        exact,
        paper_weights=weights,
    )

    tampered = exact.model_copy(update={"lineage_count_lower": 2})
    with pytest.raises(RuntimeError, match="does not reproduce lineage_count_lower"):
        verify_exact_partition_witnesses(
            candidates,
            blueprint,
            relations,
            tampered,
            paper_weights=weights,
        )


def test_witness_replay_reuses_flow_for_identical_endpoint_partitions(blueprint, monkeypatch):
    candidates = [all_facet_candidate(index) for index in range(1, 4)]
    relations = [
        relation(left, right, "cannot_link") for left, right in combinations(["P1", "P2", "P3"], 2)
    ]
    weights = {candidate.paper_id: 1.0 for candidate in candidates}
    original = exact_lineage_module.compute_evidence_flow
    calls = 0

    def counted_flow(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(exact_lineage_module, "compute_evidence_flow", counted_flow)

    exact = exact_partition_flow_analysis(
        candidates,
        blueprint,
        relations,
        paper_weights=weights,
    )

    assert exact.witnesses_verified is True
    assert calls == 2


def test_exact_search_respects_conditional_distinct_dependence_intervals(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 4)]
    intervals = [
        relation("P1", "P3", "cannot_link"),
        LineageDependenceInterval(
            paper_id_a="P1",
            paper_id_b="P2",
            distinct_dependence_lower=0.0,
            distinct_dependence_upper=0.2,
            relation="may_link",
            review_status="human_reviewed",
            source="conditional_overlap",
        ),
        LineageDependenceInterval(
            paper_id_a="P2",
            paper_id_b="P3",
            distinct_dependence_lower=0.0,
            distinct_dependence_upper=0.2,
            relation="may_link",
            review_status="human_reviewed",
            source="conditional_overlap",
        ),
    ]

    result = robust_evidence_flow(candidates, blueprint, intervals)

    assert result.effective_evidence.relaxed_box_lower == pytest.approx(1.0)
    assert result.effective_evidence.lower == pytest.approx(9 / 5.4)
    assert result.exact_partition_analysis.effective_evidence_lower == pytest.approx(9 / 5.4)
    assert result.exact_partition_analysis.worst_effective_partition


def test_unit_max_neff_checks_nonmaximal_and_maximal_partitions(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 4)]
    intervals = [
        LineageDependenceInterval(
            paper_id_a="P1",
            paper_id_b="P2",
            distinct_dependence_lower=1.0,
            distinct_dependence_upper=1.0,
            relation="may_link",
            review_status="human_reviewed",
            source="perfect_overlap_if_distinct",
        ),
        relation("P1", "P3", "cannot_link"),
        relation("P2", "P3", "cannot_link"),
    ]

    result = robust_evidence_flow(candidates, blueprint, intervals)

    assert result.effective_evidence.lower == pytest.approx(9 / 5)
    assert result.effective_evidence.upper == pytest.approx(2.0)
    assert result.exact_partition_analysis.evaluated_effective_partitions == 2
    assert result.exact_partition_analysis.evaluated_maximal_partitions == 1


def test_joint_optimistic_feasibility_uses_one_partition() -> None:
    facets = [
        EvidenceFacet(
            facet_id=f"f{index}",
            question_id="joint",
            label=f"Facet {index}",
            description=f"Exclusive evidence facet {index}",
            facet_type="mechanism",
            biological_scale="molecular",
            evidence_type="biochemical",
            polarity_requirement="support",
        )
        for index in range(1, 5)
    ]
    blueprint = EvidenceBlueprint(
        question_id="joint",
        question="Can one partition satisfy every certificate gate?",
        task_mode="evidence_synthesis",
        required_facets=facets,
        required_polarities=["support"],
        minimum_independent_lineages=3,
        review_status="frozen",
    )
    candidates = [make_candidate(index, facets={f"f{index}": 1.0}) for index in range(1, 5)]
    intervals = [
        LineageDependenceInterval(
            paper_id_a="P1",
            paper_id_b="P2",
            distinct_dependence_lower=1.0,
            distinct_dependence_upper=1.0,
            relation="may_link",
            review_status="human_reviewed",
            source="joint_witness_counterexample",
        ),
        *[
            relation(left, right, "cannot_link")
            for left, right in combinations(["P1", "P2", "P3", "P4"], 2)
            if (left, right) != ("P1", "P2")
        ],
    ]

    result = robust_evidence_flow(candidates, blueprint, intervals)

    assert result.certificate_method_version == ROBUST_LINEAGE_CERTIFICATE_VERSION
    assert result.effective_evidence.upper == pytest.approx(3.0)
    assert result.optimistic_flow.bottleneck_coverage == pytest.approx(1.0)
    assert result.lineage_count_upper == 4
    assert result.exact_partition_analysis.joint_partition_pass_exists is False
    assert result.joint_optimistic_feasibility == "does_not_exist"
    assert result.status == "insufficient_even_optimistically"


def test_component_limit_falls_back_to_sound_relaxation(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 4)]
    heterogeneous_box = LineageDependenceInterval(
        paper_id_a="P1",
        paper_id_b="P2",
        distinct_dependence_lower=0.0,
        distinct_dependence_upper=0.8,
        relation="may_link",
        review_status="human_reviewed",
        source="heterogeneous_box",
    )

    result = robust_evidence_flow(
        candidates,
        blueprint,
        [heterogeneous_box],
        exact_partition_max_components=2,
    )

    assert result.exact_partition_analysis.completed is False
    assert result.exact_partition_analysis.reason == "component_limit_exceeded"
    assert result.partition_bound_method == "safe_relaxation"
    assert result.pessimistic_flow == result.relaxed_pessimistic_flow
    assert result.joint_optimistic_feasibility == "exists"
    assert result.joint_optimistic_partition


def test_complete_unknown_box_bypasses_component_limit(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 13)]

    result = robust_evidence_flow(
        candidates,
        blueprint,
        exact_partition_max_components=9,
    )

    exact = result.exact_partition_analysis
    assert exact.completed is True
    assert exact.component_count == 12
    assert exact.reason == "exact_complete_box_prefix"
    assert exact.method == COMPLETE_BOX_PREFIX_EXACT_METHOD
    assert exact.witnesses_verified is True
    assert exact.search_states == 12
    assert exact.lineage_count_lower == 1
    assert exact.effective_evidence_lower == pytest.approx(1.0)
    assert exact.effective_evidence_upper == pytest.approx(12.0)
    assert exact.joint_partition_pass_exists is True
    assert result.partition_bound_method == "exact_search"
    assert result.exact_for_declared_lineage_uncertainty is True


def test_complete_unknown_box_proves_monotone_joint_failure(blueprint):
    candidates = [
        make_candidate(index, facets={"f1": 1.0})
        for index in range(1, 13)
    ]

    result = robust_evidence_flow(
        candidates,
        blueprint,
        exact_partition_max_components=9,
    )

    exact = result.exact_partition_analysis
    assert exact.completed is True
    assert exact.reason == "exact_complete_box_prefix"
    assert exact.joint_partition_pass_exists is False
    assert result.joint_optimistic_feasibility == "does_not_exist"


def test_complete_unknown_box_neff_upper_need_not_use_fine_partition(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 15)]
    intervals = [
        LineageDependenceInterval(
            paper_id_a=left.paper_id,
            paper_id_b=right.paper_id,
            source="complete_box_adversarial_weights",
        )
        for left, right in combinations(candidates, 2)
    ]
    weights = {
        candidate.paper_id: (1.0 if index < 2 else 0.2)
        for index, candidate in enumerate(candidates)
    }
    fine_partition = {candidate.paper_id: candidate.paper_id for candidate in candidates}
    fine_neff = exact_lineage_module._effective_evidence_for_partition(
        weights,
        fine_partition,
        {
            tuple(sorted((edge.paper_id_a, edge.paper_id_b))): edge
            for edge in intervals
        },
        use_upper=False,
    )

    exact = exact_partition_flow_analysis(
        candidates,
        blueprint,
        intervals,
        max_components=9,
        paper_weights=weights,
    )

    assert exact.completed is True
    assert fine_neff == pytest.approx(7.806451612903227)
    assert exact.effective_evidence_upper == pytest.approx(7.810810810810809)
    assert exact.effective_evidence_upper > fine_neff
    assert len(set(exact.best_effective_partition.values())) == 13
    assert exact.witnesses_verified is True


def test_complete_unknown_box_keeps_fallback_for_ambiguous_joint_case() -> None:
    facet = EvidenceFacet(
        facet_id="f",
        question_id="ambiguous_box",
        label="Facet",
        description="Facet requiring two separate high-weight origins",
        facet_type="mechanism",
        biological_scale="molecular",
        evidence_type="biochemical",
        polarity_requirement="support",
        minimum_items=2,
    )
    blueprint = EvidenceBlueprint(
        question_id="ambiguous_box",
        question="Can one complete-box partition pass every gate?",
        task_mode="evidence_synthesis",
        required_facets=[facet],
        required_polarities=["support"],
        minimum_independent_lineages=8,
    )
    candidates = [
        make_candidate(index, facets={"f": 1.0 if index <= 2 else 0.0})
        for index in range(1, 15)
    ]
    intervals = [
        LineageDependenceInterval(
            paper_id_a=left.paper_id,
            paper_id_b=right.paper_id,
            source="ambiguous_complete_box",
        )
        for left, right in combinations(candidates, 2)
    ]
    weights = {
        candidate.paper_id: (1.0 if index < 2 else 0.2)
        for index, candidate in enumerate(candidates)
    }

    exact = exact_partition_flow_analysis(
        candidates,
        blueprint,
        intervals,
        effective_evidence_ratio=7.809 / 8.0,
        max_components=9,
        paper_weights=weights,
    )

    assert exact.completed is False
    assert exact.reason == "extended_search_ambiguity_limit_exceeded"
    assert exact.joint_partition_pass_exists is None


def test_small_complete_box_uses_special_case_before_general_enumeration(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 9)]
    intervals = [
        LineageDependenceInterval(
            paper_id_a=left.paper_id,
            paper_id_b=right.paper_id,
            source="small_complete_box_dispatch",
        )
        for left, right in combinations(candidates, 2)
    ]
    weights = {candidate.paper_id: 1.0 for candidate in candidates}

    specialised = exact_partition_flow_analysis(
        candidates,
        blueprint,
        intervals,
        paper_weights=weights,
    )
    enumeration_reference = exact_partition_flow_analysis(
        candidates,
        blueprint,
        intervals,
        paper_weights=weights,
        use_complete_box_special_case=False,
    )

    assert specialised.completed is True
    assert specialised.reason == "exact_complete_box_prefix"
    assert specialised.method == COMPLETE_BOX_PREFIX_EXACT_METHOD
    assert specialised.search_states == 8
    assert specialised.witnesses_verified is True
    assert enumeration_reference.completed is True
    assert enumeration_reference.method == EXACT_PARTITION_ENUMERATION_METHOD
    assert enumeration_reference.search_states == 5_296
    assert enumeration_reference.leaf_partitions == 4_140
    assert specialised.lineage_count_lower == enumeration_reference.lineage_count_lower
    assert specialised.effective_evidence_lower == pytest.approx(
        enumeration_reference.effective_evidence_lower
    )
    assert specialised.effective_evidence_upper == pytest.approx(
        enumeration_reference.effective_evidence_upper
    )
    assert specialised.joint_partition_pass_exists == (
        enumeration_reference.joint_partition_pass_exists
    )
    assert specialised.pessimistic_flow is not None
    assert enumeration_reference.pessimistic_flow is not None
    assert specialised.pessimistic_flow.total_flow == pytest.approx(
        enumeration_reference.pessimistic_flow.total_flow
    )
    assert specialised.pessimistic_flow.bottleneck_coverage == pytest.approx(
        enumeration_reference.pessimistic_flow.bottleneck_coverage
    )
    assert specialised.pessimistic_flow.facet_flow == pytest.approx(
        enumeration_reference.pessimistic_flow.facet_flow
    )


def test_complete_unknown_box_matches_exhaustive_randomized_cases(blueprint):
    rng = random.Random(20260823)
    weight_values = (0.0, 0.03, 0.2, 0.65, 1.0)

    for paper_count in range(3, 8):
        candidates = [
            all_facet_candidate(index)
            for index in range(1, paper_count + 1)
        ]
        intervals = [
            LineageDependenceInterval(
                paper_id_a=left.paper_id,
                paper_id_b=right.paper_id,
                source="randomized_complete_box",
            )
            for left, right in combinations(candidates, 2)
        ]
        for _ in range(10):
            weights = {
                candidate.paper_id: rng.choice(weight_values)
                for candidate in candidates
            }
            exhaustive = exact_partition_flow_analysis(
                candidates,
                blueprint,
                intervals,
                max_components=paper_count,
                paper_weights=weights,
                use_complete_box_special_case=False,
            )
            subset_exact = exact_partition_flow_analysis(
                candidates,
                blueprint,
                intervals,
                max_components=paper_count - 1,
                paper_weights=weights,
            )

            assert exhaustive.completed is True
            assert subset_exact.completed is True
            positive_weights = [weight for weight in weights.values() if weight > 1e-12]
            expected_method = (
                COMPLETE_BOX_PREFIX_EXACT_METHOD
                if not positive_weights or max(positive_weights) ** 2 > 1e-12
                else COMPLETE_BOX_SUBSET_EXACT_METHOD
            )
            assert subset_exact.method == expected_method
            assert subset_exact.lineage_count_lower == exhaustive.lineage_count_lower
            assert subset_exact.effective_evidence_lower == pytest.approx(
                exhaustive.effective_evidence_lower
            )
            assert subset_exact.effective_evidence_upper == pytest.approx(
                exhaustive.effective_evidence_upper
            )
            assert subset_exact.joint_partition_pass_exists == (
                exhaustive.joint_partition_pass_exists
            )
            assert subset_exact.pessimistic_flow is not None
            assert exhaustive.pessimistic_flow is not None
            assert subset_exact.pessimistic_flow.bottleneck_coverage == pytest.approx(
                exhaustive.pessimistic_flow.bottleneck_coverage
            )
            assert subset_exact.pessimistic_flow.normalized_coverage == pytest.approx(
                exhaustive.pessimistic_flow.normalized_coverage
            )
            assert subset_exact.pessimistic_flow.total_flow == pytest.approx(
                exhaustive.pessimistic_flow.total_flow
            )
            assert subset_exact.witnesses_verified is True


def test_complete_box_prefix_endpoint_matches_retained_subset_reference() -> None:
    rng = random.Random(20260905)
    values = (0.01, 0.03, 0.2, 0.65, 1.0, 1e6)

    for component_count in range(2, 11):
        roots = [f"R{index:02d}" for index in range(component_count)]
        for _ in range(40):
            root_weights = {root: rng.choice(values) for root in roots}
            prefix_result = exact_lineage_module._complete_box_prefix_endpoint(
                root_weights,
                roots,
            )
            subset_candidates = (
                exact_lineage_module._complete_box_subset_reference_candidates(
                    root_weights,
                    roots,
                )
            )

            assert prefix_result[0] == pytest.approx(
                max(value for value, _ in subset_candidates),
                rel=1e-12,
                abs=1e-12,
            )


def test_complete_box_keeps_subset_reference_inside_numeric_clamp(blueprint) -> None:
    candidates = [all_facet_candidate(index) for index in range(1, 5)]
    intervals = [
        LineageDependenceInterval(
            paper_id_a=left.paper_id,
            paper_id_b=right.paper_id,
            source="numeric_guard_complete_box",
        )
        for left, right in combinations(candidates, 2)
    ]
    weights = {
        "P1": 8e-7,
        "P2": 7e-7,
        "P3": 1.1e-12,
        "P4": 0.0,
    }

    exhaustive = exact_partition_flow_analysis(
        candidates,
        blueprint,
        intervals,
        max_components=4,
        paper_weights=weights,
        use_complete_box_special_case=False,
    )
    guarded = exact_partition_flow_analysis(
        candidates,
        blueprint,
        intervals,
        max_components=3,
        paper_weights=weights,
    )

    assert exhaustive.completed is True
    assert guarded.completed is True
    assert guarded.method == COMPLETE_BOX_SUBSET_EXACT_METHOD
    assert guarded.reason == "exact_complete_box_subset_numeric_guard"
    assert guarded.effective_evidence_upper == pytest.approx(
        exhaustive.effective_evidence_upper,
        rel=1e-12,
        abs=1e-12,
    )
    assert guarded.witnesses_verified is True


@pytest.mark.parametrize(
    "weight_values",
    [
        [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        [1e12, 1.0, 1e-3, 1e-6, 1e-9, 0.0],
        [1.0, 1e-12, 1.0000001e-12, 1.1e-12, 0.0, 0.0],
    ],
)
def test_complete_box_prefix_matches_full_enumeration_at_numeric_boundaries(
    blueprint,
    weight_values,
) -> None:
    candidates = [all_facet_candidate(index) for index in range(1, 7)]
    intervals = [
        LineageDependenceInterval(
            paper_id_a=left.paper_id,
            paper_id_b=right.paper_id,
            source="prefix_numeric_boundaries",
        )
        for left, right in combinations(candidates, 2)
    ]
    weights = {
        candidate.paper_id: weight
        for candidate, weight in zip(candidates, weight_values, strict=True)
    }

    exhaustive = exact_partition_flow_analysis(
        candidates,
        blueprint,
        intervals,
        max_components=6,
        paper_weights=weights,
        use_complete_box_special_case=False,
    )
    specialised = exact_partition_flow_analysis(
        candidates,
        blueprint,
        intervals,
        max_components=5,
        paper_weights=weights,
    )

    assert specialised.completed is True
    assert specialised.witnesses_verified is True
    assert specialised.effective_evidence_lower == pytest.approx(
        exhaustive.effective_evidence_lower,
        rel=1e-12,
        abs=1e-12,
    )
    assert specialised.effective_evidence_upper == pytest.approx(
        exhaustive.effective_evidence_upper,
        rel=1e-12,
        abs=1e-12,
    )
    assert specialised.joint_partition_pass_exists == (
        exhaustive.joint_partition_pass_exists
    )


def test_state_limit_never_returns_partial_search_as_exact(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 7)]

    exact = exact_partition_flow_analysis(
        candidates,
        blueprint,
        [],
        max_components=9,
        max_search_states=2,
    )

    assert exact.completed is False
    assert exact.reason == "search_state_limit_exceeded"
    assert exact.pessimistic_flow is None
    assert exact.lineage_count_lower is None


def test_sparse_ten_component_graph_uses_extended_exact_search(blueprint) -> None:
    candidates = [all_facet_candidate(index) for index in range(1, 11)]
    intervals = []
    for left, right in combinations(candidates, 2):
        if {left.paper_id, right.paper_id} == {"P1", "P2"}:
            intervals.append(
                LineageDependenceInterval(
                    paper_id_a=left.paper_id,
                    paper_id_b=right.paper_id,
                    distinct_dependence_lower=0.2,
                    distinct_dependence_upper=0.8,
                    source="sparse_extended_search",
                )
            )
        else:
            intervals.append(relation(left.paper_id, right.paper_id, "cannot_link"))
    weights = {
        candidate.paper_id: 1.0 / index
        for index, candidate in enumerate(candidates, 1)
    }

    extended = exact_partition_flow_analysis(
        candidates,
        blueprint,
        intervals,
        max_components=9,
        paper_weights=weights,
    )
    within_limit_reference = exact_partition_flow_analysis(
        candidates,
        blueprint,
        intervals,
        max_components=10,
        paper_weights=weights,
    )

    assert extended.completed is True
    assert extended.reason == "exact_extended_search_complete"
    assert extended.method == EXTENDED_CONSTRAINED_PARTITION_METHOD
    assert extended.leaf_partitions == 2
    assert extended.witnesses_verified is True
    assert extended.lineage_count_lower == within_limit_reference.lineage_count_lower
    assert extended.effective_evidence_lower == pytest.approx(
        within_limit_reference.effective_evidence_lower
    )
    assert extended.effective_evidence_upper == pytest.approx(
        within_limit_reference.effective_evidence_upper
    )
    assert extended.joint_partition_pass_exists == (
        within_limit_reference.joint_partition_pass_exists
    )
    assert extended.pessimistic_flow == within_limit_reference.pessimistic_flow


def test_extended_search_state_budget_never_reports_partial_exact_result(blueprint) -> None:
    candidates = [all_facet_candidate(index) for index in range(1, 11)]
    intervals = [
        (
            LineageDependenceInterval(
                paper_id_a=left.paper_id,
                paper_id_b=right.paper_id,
                source="sparse_extended_budget",
            )
            if {left.paper_id, right.paper_id} == {"P1", "P2"}
            else relation(left.paper_id, right.paper_id, "cannot_link")
        )
        for left, right in combinations(candidates, 2)
    ]

    result = exact_partition_flow_analysis(
        candidates,
        blueprint,
        intervals,
        max_components=9,
        max_search_states=5,
    )

    assert result.completed is False
    assert result.reason == "search_state_limit_exceeded"
    assert result.method == EXTENDED_CONSTRAINED_PARTITION_METHOD
    assert result.search_states == 6
    assert result.pessimistic_flow is None
    assert result.lineage_count_lower is None


def test_dense_large_graph_does_not_enter_extended_search(blueprint) -> None:
    candidates = [all_facet_candidate(index) for index in range(1, 11)]

    result = exact_partition_flow_analysis(
        candidates,
        blueprint,
        [],
        max_components=9,
    )

    assert result.completed is False
    assert result.search_states == 0
    assert result.reason == "extended_search_ambiguity_limit_exceeded"


def test_extended_search_has_explicit_recursion_guard() -> None:
    reason = exact_lineage_module._extended_search_ineligibility_reason(
        component_count=65,
        ambiguous_pair_count=1,
        configured_component_limit=9,
    )

    assert reason == "extended_search_component_limit_exceeded"


def test_only_maximal_coarsenings_require_flow_evaluation(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 6)]

    exact = exact_partition_flow_analysis(candidates, blueprint, [])

    assert exact.completed is True
    assert exact.leaf_partitions > 1
    assert exact.evaluated_maximal_partitions == 1
    assert exact.lineage_count_lower == 1


def test_exact_search_is_deterministic(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 5)]
    relations = [
        relation("P1", "P3", "cannot_link"),
        relation("P2", "P4", "cannot_link"),
    ]

    first = exact_partition_flow_analysis(candidates, blueprint, relations)
    second = exact_partition_flow_analysis(candidates, blueprint, list(reversed(relations)))

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.method == EXACT_PARTITION_ENUMERATION_METHOD


def test_worst_flow_rounding_cannot_hide_a_failing_partition() -> None:
    facet = EvidenceFacet(
        facet_id="f",
        question_id="rounding",
        label="Facet",
        description="Facet",
        facet_type="test",
        biological_scale="molecular",
        evidence_type="biochemical",
        polarity_requirement="support",
        minimum_items=2,
    )
    blueprint = EvidenceBlueprint(
        question_id="rounding",
        question="Rounding boundary",
        task_mode="test",
        required_facets=[facet],
        required_polarities=["support"],
        minimum_independent_lineages=2,
    )
    scores = (0.59999880000024, 0.6, 0.59999879999976)
    candidates = [
        make_candidate(index, facets={"f": score}, score=100.0)
        for index, score in enumerate(scores, 1)
    ]
    intervals = [
        LineageDependenceInterval(
            paper_id_a="P1",
            paper_id_b="P2",
            distinct_dependence_lower=0.0,
            distinct_dependence_upper=0.0,
            relation="may_link",
            review_status="human_reviewed",
            source="rounding_boundary",
        ),
        relation("P1", "P3", "cannot_link"),
        LineageDependenceInterval(
            paper_id_a="P2",
            paper_id_b="P3",
            distinct_dependence_lower=0.0,
            distinct_dependence_upper=0.0,
            relation="may_link",
            review_status="human_reviewed",
            source="rounding_boundary",
        ),
    ]

    result = robust_evidence_flow(candidates, blueprint, intervals)
    failing = compute_evidence_flow(
        candidates,
        blueprint,
        unit_by_paper={"P1": "u1", "P2": "u1", "P3": "u2"},
    )

    assert failing.bottleneck_coverage == pytest.approx(0.9999989999998001)
    assert failing.bottleneck_coverage < 1.0 - 1e-6
    assert result.pessimistic_flow.bottleneck_coverage <= failing.bottleneck_coverage
    assert result.status == "lineage_review_required"


def test_unique_reviewed_partition_bypasses_component_limit(blueprint):
    candidates = [all_facet_candidate(index) for index in range(1, 7)]
    intervals = [
        relation(left.paper_id, right.paper_id, "cannot_link")
        for left, right in combinations(candidates, 2)
    ]

    result = robust_evidence_flow(
        candidates,
        blueprint,
        intervals,
        exact_partition_max_components=2,
    )

    assert result.exact_partition_analysis.completed is True
    assert result.exact_partition_analysis.reason == "exact_unique_partition"
    assert result.partition_bound_method == "exact_search"
    assert result.exact_for_declared_lineage_uncertainty is True


def test_direct_exact_api_contracts_candidate_resource_units(blueprint):
    candidates = [
        all_facet_candidate(index).model_copy(update={"dataset_lineage_id": "SHARED_DATASET"})
        for index in (1, 2)
    ]

    exact = exact_partition_flow_analysis(candidates, blueprint, [])

    assert exact.completed is True
    assert exact.component_count == 1
    assert exact.lineage_count_lower == 1


def test_direct_exact_api_rejects_resource_distinct_contradiction(blueprint):
    candidates = [
        all_facet_candidate(index).model_copy(update={"dataset_lineage_id": "SHARED_DATASET"})
        for index in (1, 2)
    ]

    with pytest.raises(ValueError, match="inside a must-link component"):
        exact_partition_flow_analysis(
            candidates,
            blueprint,
            [relation("P1", "P2", "cannot_link")],
        )


def test_direct_exact_api_rejects_unknown_or_malformed_relations(blueprint):
    candidates = [all_facet_candidate(index) for index in (1, 2)]
    valid = relation("P1", "P2", "cannot_link")

    with pytest.raises(ValueError, match="unknown lineage relation"):
        exact_partition_flow_analysis(
            candidates,
            blueprint,
            [valid.model_copy(update={"relation": "must_lnik"})],
        )
    with pytest.raises(ValueError, match="unknown papers: P9"):
        exact_partition_flow_analysis(
            candidates,
            blueprint,
            [valid.model_copy(update={"paper_id_b": "P9"})],
        )
    with pytest.raises(ValueError, match="must_link requires dependence bounds"):
        exact_partition_flow_analysis(
            candidates,
            blueprint,
            [valid.model_copy(update={"relation": "must_link"})],
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"lineage_capacity": float("nan")}, "lineage_capacity must be finite"),
        ({"lineage_capacity": 0.0}, "lineage_capacity must be finite"),
        ({"facet_threshold": float("inf")}, "facet_threshold must be finite"),
        ({"facet_threshold": 1.1}, "facet_threshold must be finite"),
        (
            {"effective_evidence_ratio": float("nan")},
            "effective_evidence_ratio must be finite",
        ),
        (
            {"effective_evidence_ratio": -0.01},
            "effective_evidence_ratio must be finite",
        ),
        (
            {"effective_evidence_ratio": 1.01},
            "effective_evidence_ratio must be finite",
        ),
    ],
)
def test_direct_exact_api_rejects_invalid_flow_parameters(blueprint, kwargs, message):
    with pytest.raises(ValueError, match=message):
        exact_partition_flow_analysis(
            [all_facet_candidate(1)],
            blueprint,
            [],
            **kwargs,
        )
