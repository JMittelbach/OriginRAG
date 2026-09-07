import pytest

from conftest import make_candidate

from originrag.trace.flow import (
    _max_min_ratio_by_flow_search,
    _max_min_ratio_by_subsets,
    compute_evidence_flow,
)


def test_one_lineage_cannot_fully_fund_two_required_facets(blueprint):
    candidate = make_candidate(
        1,
        facets={"f1": 1.0, "f2": 1.0},
        scales={"molecular": 1.0, "clinical": 1.0},
    ).model_copy(
        update={
            "study_lineage_id": "L1",
            "evidence_type_scores": {"biochemical": 1.0, "observational": 1.0},
        }
    )
    flow = compute_evidence_flow([candidate], blueprint, lineage_capacity=1.0)
    assert 0.82 < flow.bottleneck_coverage < 0.84
    assert set(flow.unresolved_facets) == {"f1", "f2"}


def test_duplicate_publications_do_not_create_new_flow(blueprint):
    first = make_candidate(1, facets={"f1": 1.0, "f2": 1.0}, scales={"molecular": 1.0, "clinical": 1.0}).model_copy(
        update={"study_lineage_id": "SAME_STUDY", "evidence_type_scores": {"biochemical": 1.0, "observational": 1.0}}
    )
    second = make_candidate(2, facets={"f1": 1.0, "f2": 1.0}, scales={"molecular": 1.0, "clinical": 1.0}).model_copy(
        update={"study_lineage_id": "SAME_STUDY", "evidence_type_scores": {"biochemical": 1.0, "observational": 1.0}}
    )
    one = compute_evidence_flow([first], blueprint)
    duplicate = compute_evidence_flow([first, second], blueprint)
    assert duplicate.total_flow == one.total_flow
    assert duplicate.bottleneck_coverage == one.bottleneck_coverage


def test_independent_complementary_lineage_resolves_flow(blueprint):
    molecular = make_candidate(1, facets={"f1": 1.0, "f2": 0.0}).model_copy(
        update={"study_lineage_id": "L1"}
    )
    clinical = make_candidate(2, facets={"f1": 0.0, "f2": 1.0}, scales={"clinical": 1.0}).model_copy(
        update={"study_lineage_id": "L2", "evidence_type_scores": {"observational": 1.0}}
    )
    flow = compute_evidence_flow([molecular, clinical], blueprint)
    assert flow.bottleneck_coverage == 1.0
    assert flow.normalized_coverage == 1.0
    assert flow.unresolved_facets == []


def test_unbounded_ablation_exposes_lineage_capacity_effect(blueprint):
    candidate = make_candidate(1, facets={"f1": 1.0, "f2": 1.0}, scales={"molecular": 1.0, "clinical": 1.0}).model_copy(
        update={"study_lineage_id": "L1", "evidence_type_scores": {"biochemical": 1.0, "observational": 1.0}}
    )
    bounded = compute_evidence_flow([candidate], blueprint, lineage_capacity=1.0)
    unbounded = compute_evidence_flow([candidate], blueprint, lineage_capacity=2.0)
    assert bounded.bottleneck_coverage < 1.0
    assert unbounded.bottleneck_coverage == 1.0


def test_progressive_flow_preserves_covered_facet_when_another_is_missing(blueprint):
    molecular = make_candidate(1, facets={"f1": 1.0, "f2": 0.0})

    flow = compute_evidence_flow([molecular], blueprint)

    assert flow.facet_coverage["f1"] == 1.0
    assert flow.facet_coverage["f2"] == 0.0
    assert flow.normalized_coverage == 0.5
    assert flow.unresolved_facets == ["f2"]


def test_adding_weak_new_facet_evidence_does_not_reduce_existing_coverage(blueprint):
    molecular = make_candidate(1, facets={"f1": 1.0, "f2": 0.0})
    weak_clinical = make_candidate(
        2,
        facets={"f1": 0.0, "f2": 0.1},
        scales={"clinical": 1.0},
    ).model_copy(update={"evidence_type_scores": {"observational": 1.0}})

    before = compute_evidence_flow([molecular], blueprint)
    after = compute_evidence_flow([molecular, weak_clinical], blueprint)

    assert after.facet_coverage["f1"] >= before.facet_coverage["f1"]
    assert after.normalized_coverage >= before.normalized_coverage


def test_shared_dataset_consumes_one_flow_capacity(blueprint):
    molecular = make_candidate(1, facets={"f1": 1.0, "f2": 0.0}).model_copy(
        update={"dataset_lineage_id": "SHARED_DATASET"}
    )
    clinical = make_candidate(
        2,
        facets={"f1": 0.0, "f2": 1.0},
        scales={"clinical": 1.0},
    ).model_copy(
        update={
            "dataset_lineage_id": "SHARED_DATASET",
            "evidence_type_scores": {"observational": 1.0},
        }
    )

    result = compute_evidence_flow([molecular, clinical], blueprint)

    assert result.total_flow <= 1.0 + 1e-9
    assert result.unresolved_facets


def test_facet_polarity_requirement_must_be_met_by_contributing_candidate(blueprint):
    counter_blueprint = blueprint.model_copy(
        update={
            "required_facets": [
                blueprint.required_facets[1].model_copy(
                    update={"polarity_requirement": "contradiction"}
                )
            ],
            "required_polarities": ["contradiction"],
        }
    )
    supporting_match = make_candidate(
        1,
        facets={"f2": 1.0},
        scales={"clinical": 1.0},
        polarity={"support": 1.0},
    ).model_copy(update={"evidence_type_scores": {"observational": 1.0}})
    irrelevant_contradiction = make_candidate(
        2,
        facets={"f2": 0.0},
        scales={"clinical": 1.0},
        polarity={"contradiction": 1.0},
    ).model_copy(update={"evidence_type_scores": {"observational": 1.0}})

    flow = compute_evidence_flow(
        [supporting_match, irrelevant_contradiction], counter_blueprint
    )

    assert flow.normalized_coverage == 0.0
    assert flow.unresolved_facets == ["f2"]


def test_subset_cut_ratio_matches_max_flow_feasibility_search():
    for lineage_count in range(1, 5):
        for facet_count in range(1, 5):
            lineages = [f"L{index}" for index in range(lineage_count)]
            facets = [f"F{index}" for index in range(facet_count)]
            capacities = {
                (lineage, facet): (((lineage_index + 2) * (facet_index + 3)) % 9) / 8
                for lineage_index, lineage in enumerate(lineages)
                for facet_index, facet in enumerate(facets)
            }
            demands = {
                facet: 0.4 + 0.2 * facet_index
                for facet_index, facet in enumerate(facets)
            }

            subset_ratio = _max_min_ratio_by_subsets(
                lineages,
                capacities,
                demands,
                lineage_capacity=1.0,
            )
            search_ratio = _max_min_ratio_by_flow_search(
                lineages,
                capacities,
                demands,
                lineage_capacity=1.0,
            )

            assert subset_ratio == pytest.approx(search_ratio, abs=2e-8)
