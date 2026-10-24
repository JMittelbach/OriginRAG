from __future__ import annotations

import pytest

from originrag.trace.blueprint import matched_cellular_aliases, suggest_blueprint
from originrag.trace.facets import score_candidate_facets
from originrag.trace.schemas import TraceCandidate


@pytest.mark.parametrize("question", [
    "Which database cache policies reduce query latency? State the result concisely.",
    "Which database cache policies reduce query latency in the United States?",
    "Which database cache policies reduce query latency? Provide a concise statement.",
    "Which database cache policies reduce query latency in steady-state operation?",
])
def test_noncellular_uses_of_state_do_not_create_cellular_requirements(question):
    blueprint = suggest_blueprint("cache_question", question, "evidence_synthesis")
    required = [facet for facet in blueprint.required_facets if facet.is_required]

    assert [facet.facet_id for facet in required] == ["cache_question_direct_answer_evidence"]
    assert required[0].facet_type == "methodological_context"
    assert required[0].aliases
    assert blueprint.minimum_independent_lineages == 2
    assert blueprint.required_polarities == ["support"]
    assert blueprint.review_status == "suggested"


@pytest.mark.parametrize("question", [
    "state the result concisely", "results from the united states",
    "provide a statement", "evaluate steady state and finite states",
])
def test_state_requires_an_explicit_cellular_context(question):
    assert matched_cellular_aliases(question) == []


def test_explicit_cell_states_keep_the_existing_requirement():
    blueprint = suggest_blueprint(
        "cell_question", "Which cell states are reported?", "evidence_synthesis",
    )
    required = [facet for facet in blueprint.required_facets if facet.is_required]

    assert [facet.facet_id for facet in required] == ["cell_question_cellular_context"]
    assert required[0].facet_type == "cellular_process"
    assert {"cell state", "cell states", "subcluster"} <= set(required[0].aliases)
    assert blueprint.minimum_independent_lineages == 2


def test_formatting_state_does_not_grant_unrelated_evidence_credit():
    blueprint = suggest_blueprint(
        "cache_question",
        "Which database cache policies reduce query latency? State the result concisely.",
        "evidence_synthesis",
    )
    candidate = TraceCandidate(
        chunk_id="weather_1", paper_id="weather", text="Winter rainfall reached 23 mm.",
        token_cost=8, retrieval_score=1.0, section="results",
    )

    assert score_candidate_facets(candidate, blueprint)["cache_question_direct_answer_evidence"] == 0.0
