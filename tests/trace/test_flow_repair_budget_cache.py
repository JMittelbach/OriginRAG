from __future__ import annotations

import random
from itertools import product
from types import SimpleNamespace

import pytest

from originrag.trace import flow_selector
from originrag.trace.schemas import EvidenceBlueprint, EvidenceFacet, TraceCandidate
from originrag.trace.selector import SelectorConfig


def calibration_blueprint(count=3):
    return EvidenceBlueprint(
        question_id="calibration",
        question="Which calibration criteria are covered?",
        task_mode="evidence_qa",
        required_facets=[
            EvidenceFacet(
                facet_id=f"f{index}",
                question_id="calibration",
                label=f"Calibration criterion {index}",
                description="A neutral calibration criterion.",
                facet_type="methodological",
                biological_scale="methodological",
                evidence_type="methodological",
            )
            for index in range(count)
        ],
    )


def calibration_candidate(index, *, facets=None, tokens=100, **updates):
    values = {
        "chunk_id": f"C{index}",
        "paper_id": f"P{index}",
        "text": "Neutral calibration record.",
        "token_cost": tokens,
        "retrieval_score": 10.0,
        "section_quality": 1.0,
        "facet_scores": facets or {},
        "scale_scores": {"methodological": 1.0},
        "evidence_type_scores": {"methodological": 1.0},
        "polarity_scores": {"support": 1.0},
    }
    values.update(updates)
    return TraceCandidate(**values)


def reference_filter(
    candidates, selected, remaining, blueprint, token_budget, *, prompt_token_budget=None,
):
    safe = [
        item for item in candidates
        if flow_selector.preserves_repair_budget(
            item, selected, remaining, blueprint, token_budget
        )
    ]
    if prompt_token_budget is None:
        return safe

    def serialized(item):
        return item.model_copy(update={
            "token_cost": int(item.metadata.get("_prompt_token_cost", item.token_cost)),
        })

    serialized_selected = [serialized(item) for item in selected]
    serialized_remaining = [serialized(item) for item in remaining]
    return [
        item for item in safe
        if flow_selector.preserves_repair_budget(
            serialized(item), serialized_selected, serialized_remaining, blueprint,
            prompt_token_budget,
        )
    ]


def assert_same_items(actual, expected):
    assert [id(item) for item in actual] == [id(item) for item in expected]


def test_batch_matches_reference_exhaustively_for_small_support_and_cost_spaces():
    blueprint = calibration_blueprint(2)
    support_options = [
        {"f0": float(mask & 1 != 0), "f1": float(mask & 2 != 0)}
        for mask in range(4)
    ]
    for supports in product(support_options, repeat=3):
        for costs in product((1, 3), repeat=3):
            remaining = [
                calibration_candidate(index, facets=facets, tokens=cost)
                for index, (facets, cost) in enumerate(zip(supports, costs))
            ]
            for budget in (-1, 0, 1, 2, 3, 4, 5, 6, 9):
                assert_same_items(
                    flow_selector._repair_budget_safe_candidates(
                        remaining, [], remaining, blueprint, budget
                    ),
                    reference_filter(remaining, [], remaining, blueprint, budget),
                )


def test_batch_matches_reference_on_randomized_selected_sets_and_duplicates():
    rng = random.Random(20261022)
    for _ in range(1000):
        blueprint = calibration_blueprint(rng.randrange(7))
        for facet in blueprint.required_facets:
            facet.is_required = rng.choice([True, True, False])
            facet.polarity_requirement = rng.choice([None, "support", "null_result"])

        def make(index):
            return calibration_candidate(
                index,
                facets={facet.facet_id: rng.choice([0.0, 1e-12, 0.49, 0.5, 1.0])
                        for facet in blueprint.required_facets},
                tokens=rng.randrange(1, 400),
                section_quality=rng.choice([0.0, 0.7, 1.0]),
                quality_flags=rng.sample(["flag_a", "flag_b", "flag_c"], rng.randrange(4)),
                scale_scores={"methodological": rng.choice([0.0, 0.2, 1.0])},
                evidence_type_scores={"methodological": rng.choice([0.0, 0.2, 1.0])},
                polarity_scores={"support": rng.random(), "null_result": rng.choice([0.0, 1.0])},
                metadata={"_prompt_token_cost": rng.randrange(1, 600)},
            )

        remaining = [make(index) for index in range(rng.randrange(1, 12))]
        remaining.extend([remaining[0], remaining[0].model_copy(deep=True)])
        selected = [make(index + 100) for index in range(rng.randrange(5))]
        valid = rng.sample(remaining, rng.randrange(len(remaining) + 1))
        # A value-equal copy outside remaining exercises the identity-cache miss.
        valid.append(remaining[0].model_copy(deep=True))
        budget = rng.randrange(-1, 1400)
        assert_same_items(
            flow_selector._repair_budget_safe_candidates(
                valid, selected, remaining, blueprint, budget
            ),
            reference_filter(valid, selected, remaining, blueprint, budget),
        )
        prompt_budget = rng.randrange(0, 2000)
        assert_same_items(
            flow_selector._repair_budget_safe_candidates(
                valid, selected, remaining, blueprint, budget,
                prompt_token_budget=prompt_budget,
            ),
            reference_filter(
                valid, selected, remaining, blueprint, budget,
                prompt_token_budget=prompt_budget,
            ),
        )


def test_batch_keeps_per_facet_cost_even_when_one_provider_covers_multiple_facets():
    blueprint = calibration_blueprint(3)
    trial = calibration_candidate(0, facets={"f0": 1.0}, tokens=20)
    provider = calibration_candidate(1, facets={"f1": 1.0, "f2": 1.0}, tokens=80)
    remaining = [trial, provider]

    assert flow_selector._repair_budget_safe_candidates(
        [trial], [], remaining, blueprint, 100
    ) == []
    assert flow_selector._repair_budget_safe_candidates(
        [trial], [], remaining, blueprint, 180
    ) == [trial]


def test_batch_uses_remaining_providers_not_only_valid_candidates():
    blueprint = calibration_blueprint(2)
    trial = calibration_candidate(0, facets={"f0": 1.0}, tokens=20)
    provider = calibration_candidate(1, facets={"f1": 1.0}, tokens=80)

    assert flow_selector._repair_budget_safe_candidates(
        [trial], [], [trial, provider], blueprint, 99
    ) == []
    assert flow_selector._repair_budget_safe_candidates(
        [trial], [], [trial, provider], blueprint, 100
    ) == [trial]


def test_missing_provider_keeps_original_zero_reservation():
    trial = calibration_candidate(0, facets={"f0": 1.0}, tokens=20)

    assert flow_selector._repair_budget_safe_candidates(
        [trial], [], [trial], calibration_blueprint(2), 20
    ) == [trial]


@pytest.mark.parametrize("required_count", [0, 2])
def test_covered_facets_do_not_scan_remaining(required_count):
    class UnusedRemaining(list):
        def __iter__(self):
            pytest.fail("Covered facets must not scan remaining candidates")

    blueprint = calibration_blueprint(required_count)
    selected = [calibration_candidate(0, facets={"f0": 1.0, "f1": 1.0}, tokens=10)]
    valid = [calibration_candidate(1, tokens=10), calibration_candidate(2, tokens=11)]
    assert flow_selector._repair_budget_safe_candidates(
        valid, selected, UnusedRemaining(), blueprint, 20
    ) == valid[:1]


def test_empty_candidate_batch_does_not_scan_inputs():
    class UnusedInput(list):
        def __iter__(self):
            pytest.fail("An empty candidate batch must not scan inputs")

    assert flow_selector._repair_budget_safe_candidates(
        [], UnusedInput(), UnusedInput(), calibration_blueprint(), 100
    ) == []


def test_batch_evaluates_each_remaining_candidate_facet_only_once(monkeypatch):
    blueprint = calibration_blueprint(3)
    remaining = [
        calibration_candidate(index, facets={f"f{index % 3}": 1.0})
        for index in range(60)
    ]
    expected = reference_filter(remaining, [], remaining, blueprint, 500)
    contribution = flow_selector.facet_contribution
    calls = []

    def counted(candidate, facet):
        calls.append((id(candidate), facet.facet_id))
        return contribution(candidate, facet)

    monkeypatch.setattr(flow_selector, "facet_contribution", counted)
    actual = flow_selector._repair_budget_safe_candidates(
        remaining, [], remaining, blueprint, 500
    )

    assert_same_items(actual, expected)
    assert len(calls) == len(remaining) * len(blueprint.required_facets)
    assert len(set(calls)) == len(calls)


@pytest.mark.parametrize("prompt_budget,expected", [(19, False), (20, True), (21, True)])
def test_flow_optional_prompt_cap_uses_serialized_cost_and_accepts_exact_boundary(
    prompt_budget, expected,
):
    selected = [calibration_candidate(0, tokens=10, metadata={"_prompt_token_cost": 7})]
    candidate = calibration_candidate(1, tokens=10, metadata={"_prompt_token_cost": 13})
    config = SimpleNamespace(**{**vars(SelectorConfig()), "prompt_token_budget": prompt_budget})

    assert flow_selector.can_add_flow(candidate, selected, config) is expected


def test_flow_optional_prompt_cap_falls_back_to_raw_cost_for_missing_metadata():
    selected = [calibration_candidate(0, tokens=10, metadata={"_prompt_token_cost": 7})]
    candidate = calibration_candidate(1, tokens=10)
    config = SimpleNamespace(**{**vars(SelectorConfig()), "prompt_token_budget": 17})

    assert flow_selector.can_add_flow(candidate, selected, config) is True
    config.prompt_token_budget = 16
    assert flow_selector.can_add_flow(candidate, selected, config) is False


def test_flow_optional_prompt_cap_does_not_replace_original_token_budget():
    selected = [calibration_candidate(0, tokens=10, metadata={"_prompt_token_cost": 1})]
    candidate = calibration_candidate(1, tokens=10, metadata={"_prompt_token_cost": 1})
    config = SimpleNamespace(**{
        **vars(SelectorConfig(token_budget=19)), "prompt_token_budget": 100,
    })

    assert flow_selector.can_add_flow(candidate, selected, config) is False


@pytest.mark.parametrize("legacy_config", [False, True])
def test_disabled_prompt_cap_never_reads_serialized_cost(legacy_config):
    class UnusedMetadata(dict):
        def get(self, *args, **kwargs):
            pytest.fail("Disabled prompt caps must preserve the original cost path")

    candidate = calibration_candidate(1, tokens=10).model_copy(update={"metadata": UnusedMetadata()})
    settings = {**vars(SelectorConfig(token_budget=10)), "prompt_token_budget": None}
    if legacy_config:
        settings.pop("prompt_token_budget")

    assert flow_selector.can_add_flow(candidate, [], SimpleNamespace(**settings)) is True


@pytest.mark.parametrize("anchor_count", [0, 2, 4])
def test_full_flow_selection_applies_prompt_cap_to_anchors_and_remaining_candidates(anchor_count):
    candidates = [
        calibration_candidate(
            index, facets={"f0": 1.0}, tokens=10, metadata={"_prompt_token_cost": 13},
        )
        for index in range(8)
    ]
    settings = {
        **vars(SelectorConfig(token_budget=1000, flow_anchor_papers=anchor_count, flow_minimum_papers=4)),
        "prompt_token_budget": 39,
    }

    result = flow_selector.select_flow_evidence(
        candidates, calibration_blueprint(1), SimpleNamespace(**settings)
    )

    assert len(result.selected) == 3
    assert sum(item.metadata["_prompt_token_cost"] for item in result.selected) == 39
    assert result.stopping_reason == "constraint_limited"


@pytest.mark.parametrize("anchor_count", [0, 1, 2])
def test_prompt_reservation_keeps_metadata_heavy_anchor_from_crowding_out_missing_facet(anchor_count):
    candidates = [
        calibration_candidate(
            0, facets={"f0": 1.0}, tokens=10, retrieval_score=1000.0,
            metadata={"_prompt_token_cost": 85},
        ),
        calibration_candidate(
            1, facets={"f0": 1.0}, tokens=10, retrieval_score=10.0,
            metadata={"_prompt_token_cost": 30},
        ),
        calibration_candidate(
            2, facets={"f1": 1.0}, tokens=10, retrieval_score=5.0,
            metadata={"_prompt_token_cost": 50},
        ),
    ]
    config = SelectorConfig(
        token_budget=1000, prompt_token_budget=100,
        flow_anchor_papers=anchor_count, flow_minimum_papers=2,
    )

    result = flow_selector.select_flow_evidence(candidates, calibration_blueprint(2), config)

    assert {item.chunk_id for item in result.selected} == {"C1", "C2"}
    assert sum(item.metadata["_prompt_token_cost"] for item in result.selected) == 80
    assert result.flow.unresolved_facets == []
    assert result.stopping_reason == "flow_constraints_satisfied"


@pytest.mark.parametrize("anchor_count", [0, 2])
def test_empty_reservation_filter_keeps_feasible_shared_provider_selection(anchor_count):
    blueprint = calibration_blueprint(6)
    candidates = [
        calibration_candidate(
            index, facets={f"f{2 * index}": 1.0, f"f{2 * index + 1}": 1.0},
            tokens=10, metadata={"_prompt_token_cost": 10},
        )
        for index in range(3)
    ]
    config = SelectorConfig(
        token_budget=1000, prompt_token_budget=30,
        flow_anchor_papers=anchor_count, flow_minimum_papers=3, flow_lineage_capacity=2.0,
    )
    # Per-facet reservations conservatively count each remaining shared provider
    # twice. The established empty-filter fallback must remain available.
    assert flow_selector._repair_budget_safe_candidates(
        candidates, [], candidates, blueprint, config.token_budget,
        prompt_token_budget=config.prompt_token_budget,
    ) == []

    result = flow_selector.select_flow_evidence(candidates, blueprint, config)

    assert {item.chunk_id for item in result.selected} == {"C0", "C1", "C2"}
    assert sum(item.metadata["_prompt_token_cost"] for item in result.selected) == 30
    assert result.flow.unresolved_facets == []
    assert result.stopping_reason == "flow_constraints_satisfied"


@pytest.mark.parametrize("anchors,lineage_capacity,diverse", list(product((False, True), repeat=3)))
@pytest.mark.parametrize("prompt_budget", [None, 200, 600])
def test_full_selection_is_exactly_equal_with_batched_or_reference_filter(
    monkeypatch, anchors, lineage_capacity, diverse, prompt_budget,
):
    for seed in range(8):
        rng = random.Random(seed)
        blueprint = calibration_blueprint(3)
        blueprint.minimum_independent_lineages = rng.choice([0, 1, 2, 3])
        blueprint.required_polarities = rng.choice([[], ["support"], ["null_result"]])
        candidates = [
            calibration_candidate(
                index,
                facets={f"f{facet}": rng.choice([0.0, 0.25, 0.6, 1.0]) for facet in range(3)},
                tokens=rng.randrange(20, 160),
                paper_id=f"P{index // 2}",
                publication_family_id=f"F{index // 4}",
                study_lineage_id=f"L{index // 4}",
                retrieval_score=rng.choice([1.0, 5.0, 20.0]),
                section_quality=rng.choice([0.0, 0.7, 1.0]),
                polarity_scores={"support": rng.choice([0.0, 1.0]),
                                 "null_result": rng.choice([0.0, 1.0])},
                metadata={"_prompt_token_cost": rng.randrange(30, 220)},
            )
            for index in range(12)
        ]
        candidates.append(candidates[0].model_copy(deep=True))
        rng.shuffle(candidates)
        config = SelectorConfig(
            token_budget=rng.choice([150, 300, 600]),
            prompt_token_budget=prompt_budget,
            max_chunks_per_paper=rng.choice([1, 2]),
            max_publication_versions_per_family=rng.choice([1, 2]),
            facet_coverage_threshold=rng.choice([0.3, 0.6, 0.9]),
            flow_anchor_papers=rng.choice([0, 1, 2]),
            flow_minimum_papers=rng.choice([0, 2, 4]),
            flow_lineage_capacity=rng.choice([0.5, 1.0, 2.0]),
            flow_reserve_relevance_fraction=rng.choice([0.0, 0.25, 1.0]),
        )
        kwargs = {
            "dependence": {("P0", "P1"): 0.5, ("P2", "P3"): 0.25},
            "anchors_enabled": anchors,
            "lineage_capacity_enabled": lineage_capacity,
            "diverse_reserve_enabled": diverse,
        }

        actual = flow_selector.select_flow_evidence(candidates, blueprint, config, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(flow_selector, "_repair_budget_safe_candidates", reference_filter)
            expected = flow_selector.select_flow_evidence(candidates, blueprint, config, **kwargs)

        assert actual == expected
        assert_same_items(actual.selected, expected.selected)
        assert flow_selector.token_cost(actual.selected) == flow_selector.token_cost(expected.selected)
