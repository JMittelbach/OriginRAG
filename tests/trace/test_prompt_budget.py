from __future__ import annotations

import pytest

from originrag.trace import runtime
from originrag.trace.schemas import EvidenceBlueprint, EvidenceFacet, TraceCandidate
from originrag.trace.selector import (
    SelectorConfig,
    can_add,
    constraint_violations,
    prompt_token_cost,
    select_evidence,
)


def neutral_blueprint() -> EvidenceBlueprint:
    return EvidenceBlueprint(
        question_id="benchmark",
        question="Which reports measured latency?",
        task_mode="evidence_qa",
        required_facets=[
            EvidenceFacet(
                facet_id="latency",
                question_id="benchmark",
                label="Latency",
                description="Measured system latency",
                facet_type="benchmark",
                biological_scale="methodological",
                evidence_type="methodological",
                aliases=["latency"],
            )
        ],
        minimum_independent_lineages=2,
    )


def candidate(index: int, *, text_cost: int = 10, serialized_cost: int = 120) -> TraceCandidate:
    return TraceCandidate(
        chunk_id=f"C{index}",
        paper_id=f"P{index}",
        text="The benchmark measured system latency.",
        token_cost=text_cost,
        retrieval_score=30 - index,
        section="results",
        section_quality=1.0,
        publication_family_id=f"P{index}",
        study_lineage_id=f"P{index}",
        facet_scores={"latency": 1.0},
        scale_scores={"methodological": 1.0},
        evidence_type_scores={"methodological": 1.0},
        polarity_scores={"support": 1.0},
        metadata={"_prompt_token_cost": serialized_cost},
    )


@pytest.mark.parametrize("invalid", [True, False, 0, -1, 1.5, "200"])
def test_serialized_budget_requires_positive_integer(invalid):
    with pytest.raises(ValueError, match="prompt_token_budget"):
        SelectorConfig(prompt_token_budget=invalid)


def test_both_cost_constraints_are_independently_enforced():
    first = candidate(0, text_cost=60, serialized_cost=120)
    second = candidate(1, text_cost=60, serialized_cost=120)
    assert can_add(second, [first], SelectorConfig(token_budget=120, prompt_token_budget=240))
    assert not can_add(second, [first], SelectorConfig(token_budget=119, prompt_token_budget=240))
    assert not can_add(second, [first], SelectorConfig(token_budget=120, prompt_token_budget=239))
    assert constraint_violations(
        [first, second], SelectorConfig(token_budget=119, prompt_token_budget=239)
    ) == ["token_budget_exceeded", "prompt_token_budget_exceeded"]


@pytest.mark.parametrize(
    "selector, algorithm",
    [
        ("topk_relevance", "lazy_greedy"),
        ("mmr", "lazy_greedy"),
        ("trace_full", "lazy_greedy"),
        ("trace_no_lineage", "lazy_greedy"),
        ("trace_full", "beam_search"),
    ],
)
def test_selectors_obey_serialized_budget_without_changing_text_cost(selector, algorithm):
    candidates = [candidate(index) for index in range(3)]
    config = SelectorConfig(
        token_budget=1000,
        prompt_token_budget=240,
        selector_algorithm=algorithm,
        minimum_selected_papers=3,
        minimum_marginal_gain=0,
    )
    first = select_evidence(candidates, neutral_blueprint(), selector_name=selector, config=config)
    second = select_evidence(candidates, neutral_blueprint(), selector_name=selector, config=config)
    assert len(first.selected_candidates) == 2
    assert first.token_cost == 20
    assert prompt_token_cost(first.selected_candidates) == 240
    assert first.constraint_violations == []
    assert first.run_config["prompt_token_budget"] == 240
    assert first.run_config["prompt_token_cost"] == 240
    assert [item.chunk_id for item in first.selected_candidates] == [
        item.chunk_id for item in second.selected_candidates
    ]


def test_disabled_constraint_ignores_serialized_metadata_and_preserves_run_config():
    candidates = [candidate(index) for index in range(3)]
    for item in candidates:
        item.metadata["_prompt_token_cost"] = "unused untrusted cost"
    selected = select_evidence(
        candidates,
        neutral_blueprint(),
        selector_name="topk_relevance",
        config=SelectorConfig(token_budget=30),
    )
    assert len(selected.selected_candidates) == 3
    assert "prompt_token_budget" not in selected.run_config
    assert "prompt_token_cost" not in selected.run_config
    assert constraint_violations(candidates, SelectorConfig(token_budget=30)) == []


def test_direct_selector_cost_falls_back_to_text_tokens():
    item = candidate(0, text_cost=10)
    item.metadata.clear()
    assert prompt_token_cost([item]) == 10
    assert can_add(item, [], SelectorConfig(prompt_token_budget=10))
    assert not can_add(item, [], SelectorConfig(prompt_token_budget=9))


@pytest.mark.parametrize("invalid", [True, False, 0, -1, 1.5, "200", None])
def test_invalid_direct_serialized_cost_fails_closed(invalid):
    item = candidate(0)
    item.metadata["_prompt_token_cost"] = invalid
    with pytest.raises(ValueError, match="positive integer"):
        prompt_token_cost([item])


@pytest.fixture
def neutral_runtime(monkeypatch, tmp_path):
    blueprint = neutral_blueprint()
    monkeypatch.setattr(runtime, "suggest_blueprint", lambda *args: blueprint)
    monkeypatch.setattr(runtime, "read_yaml", lambda path: {"trace_rag": {
        "minimum_selected_papers": 1,
        "robust_lineage_review_simulation_budget": 1,
    }})
    monkeypatch.setattr(runtime, "write_trace_artifacts", lambda *args: None)
    rows = [
        {
            "chunk_id": f"C{index}",
            "paper_id": f"P{index}",
            "text": "The benchmark measured system latency.",
            "section": "results",
            "score": 30 - index,
            "_prompt_token_cost": 1,
            "metadata": {"_prompt_token_cost": 1},
        }
        for index in range(3)
    ]
    return {
        "question": blueprint.question,
        "task_mode": blueprint.task_mode,
        "results": rows,
        "output_dir": tmp_path,
        "selector": "topk",
        "config_path": tmp_path / "unused-config.yaml",
        "token_budget": 1000,
    }


def test_runtime_uses_trusted_costs_and_certifies_every_selected_report(neutral_runtime):
    result = runtime.run_trace_selection(
        **neutral_runtime,
        prompt_token_budget=240,
        prompt_token_costs={"C0": 120, "C1": 120, "C2": 120},
    )
    assert [row["chunk_id"] for row in result["selected_results"]] == ["C0", "C1"]
    assert all(item.metadata["_prompt_token_cost"] == 120 for item in result["candidates"])
    assert result["certifying_evidence"] == result["selection"].selected_candidates
    accounting = result["selection"].run_config["certifying_evidence"]
    assert accounting["paper_ids"] == ["P0", "P1"]
    assert accounting["strategy"] == "full_selected_set_v2"
    assert result["selection"].run_config["prompt_token_cost"] == 240
    assert neutral_runtime["results"][0]["_prompt_token_cost"] == 1


def test_runtime_without_serialized_cap_is_unchanged(neutral_runtime):
    result = runtime.run_trace_selection(**neutral_runtime)
    assert len(result["selected_results"]) == 3
    assert all("_prompt_token_cost" not in item.metadata for item in result["candidates"])
    assert "prompt_token_budget" not in result["selection"].run_config


@pytest.mark.parametrize(
    "costs, error",
    [
        (None, "map every prepared chunk"),
        ({}, "missing prepared chunk"),
        ({"C0": 120, "C1": 120}, "missing prepared chunk"),
        ({"C0": 120, "C1": 120, "C2": True}, "positive integers"),
        ({"C0": 120, "C1": 120, "C2": 0}, "positive integers"),
        ({"C0": 120, "C1": 120, "C2": -1}, "positive integers"),
        ({"C0": 120, "C1": 120, "C2": 1.5}, "positive integers"),
        ({"C0": 120, "C1": 120, "C2": "120"}, "positive integers"),
        ({"": 120}, "nonempty chunk IDs"),
        ({1: 120}, "nonempty chunk IDs"),
    ],
)
def test_runtime_requires_complete_valid_cost_mapping(neutral_runtime, costs, error):
    with pytest.raises(ValueError, match=error):
        runtime.run_trace_selection(
            **neutral_runtime, prompt_token_budget=240, prompt_token_costs=costs
        )


def test_runtime_rejects_any_selector_bypass_before_certification(neutral_runtime, monkeypatch):
    original = runtime.run_selector

    def bypass(name, candidates, blueprint, dependence, config):
        return original(name, candidates, blueprint, dependence, SelectorConfig(token_budget=1000))

    def certificate_must_not_run(*args, **kwargs):
        raise AssertionError("Oversized selection reached certification")

    monkeypatch.setattr(runtime, "run_selector", bypass)
    monkeypatch.setattr(runtime, "build_certificate", certificate_must_not_run)
    with pytest.raises(ValueError, match="exceeds the serialized prompt token budget"):
        runtime.run_trace_selection(
            **neutral_runtime,
            prompt_token_budget=240,
            prompt_token_costs={"C0": 120, "C1": 120, "C2": 120},
        )
