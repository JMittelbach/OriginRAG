import pytest

from conftest import make_candidate
from pfempire.trace.selector import SelectorConfig, select_evidence


def test_trace_selects_missing_critical_facet(blueprint):
    candidates = [
        make_candidate(i, facets={"f1": 1, "f2": 0}, score=30 - i)
        for i in range(3)
    ] + [
        make_candidate(
            9,
            facets={"f1": 0, "f2": 1},
            scales={"clinical": 1},
            score=5,
        )
    ]
    result = select_evidence(
        candidates,
        blueprint,
        {},
        config=SelectorConfig(token_budget=300, minimum_marginal_gain=0),
        selector_name="trace_full",
    )
    assert "C9" in {c.chunk_id for c in result.selected_candidates}


def test_budget_family_and_determinism(blueprint):
    candidates = [
        make_candidate(1, family="F", tokens=100),
        make_candidate(2, family="F", tokens=100),
        make_candidate(3, tokens=100),
    ]
    config = SelectorConfig(token_budget=200, minimum_marginal_gain=0)
    first = select_evidence(
        candidates, blueprint, {}, config=config, selector_name="trace_full"
    )
    second = select_evidence(
        candidates, blueprint, {}, config=config, selector_name="trace_full"
    )
    assert first.token_cost <= 200
    assert [candidate.chunk_id for candidate in first.selected_candidates] == [
        candidate.chunk_id for candidate in second.selected_candidates
    ]
    assert (
        len(
            {
                candidate.paper_id
                for candidate in first.selected_candidates
                if candidate.publication_family_id == "F"
            }
        )
        <= 1
    )


def test_beam_option_is_deterministic_scaffold(blueprint):
    config = SelectorConfig(
        token_budget=200,
        minimum_marginal_gain=0,
        selector_algorithm="beam_search",
    )
    candidates = [make_candidate(i) for i in range(3)]
    first = select_evidence(candidates, blueprint, {}, config=config)
    second = select_evidence(candidates, blueprint, {}, config=config)
    assert [candidate.chunk_id for candidate in first.selected_candidates] == [
        candidate.chunk_id for candidate in second.selected_candidates
    ]


def test_trace_respects_minimum_paper_breadth_before_early_stop(blueprint):
    candidates = [make_candidate(i, tokens=50) for i in range(5)]
    config = SelectorConfig(
        token_budget=500,
        minimum_marginal_gain=0,
        minimum_selected_papers=4,
    )
    result = select_evidence(
        candidates,
        blueprint,
        {},
        config=config,
        selector_name="trace_full",
    )
    assert result.raw_paper_count >= 4


def test_selector_rejects_duplicate_chunk_ids(blueprint):
    first = make_candidate(1)
    duplicate = make_candidate(2).model_copy(update={"chunk_id": first.chunk_id})

    with pytest.raises(ValueError, match="chunk_id values must be unique"):
        select_evidence([first, duplicate], blueprint, {}, selector_name="trace_full")


def test_family_limit_allows_multiple_chunks_from_same_paper(blueprint):
    candidates = [
        make_candidate(1, paper="P1", family="F1", tokens=50),
        make_candidate(2, paper="P1", family="F1", tokens=50),
    ]
    config = SelectorConfig(
        token_budget=100,
        max_chunks_per_paper=2,
        max_publication_versions_per_family=1,
        minimum_marginal_gain=0,
        minimum_selected_papers=1,
    )

    result = select_evidence(
        candidates,
        blueprint,
        {},
        config=config,
        selector_name="topk_relevance",
    )

    assert len(result.selected_candidates) == 2
