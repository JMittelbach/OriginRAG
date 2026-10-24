from __future__ import annotations

import pytest

from originrag.trace.blueprint import matched_immune_state_aliases, suggest_blueprint
from originrag.trace.facets import score_candidate_facets
from originrag.trace.schemas import TraceCandidate


@pytest.mark.parametrize("question", [
    "My single-cell UMAP looks well mixed after batch integration. Does that prove batch "
    "effects are gone and biological differences are preserved, even if all cases were "
    "sequenced in one batch and all controls in another?",
    "Which single-cell RNA sequencing normalization methods are available?",
    "How do single cell imaging benchmarks compare measurement error?",
    "Does single-cell sampling replace independent biological replicates?",
])
def test_single_cell_modality_does_not_imply_an_immune_cell_target(question):
    blueprint = suggest_blueprint("modality", question, "evidence_qa")
    required = [facet for facet in blueprint.required_facets if facet.is_required]

    assert matched_immune_state_aliases(question) == []
    assert [facet.facet_id for facet in required] == ["modality_direct_answer_evidence"]
    assert required[0].facet_type == "methodological_context"
    assert required[0].aliases
    assert blueprint.minimum_independent_lineages == 2
    assert blueprint.required_polarities == ["support"]
    assert blueprint.review_status == "suggested"


def test_modality_does_not_add_an_immune_facet_to_method_comparison():
    blueprint = suggest_blueprint(
        "comparison", "Compare single-cell normalization algorithms.", "method_comparison",
    )
    required = [facet.facet_id for facet in blueprint.required_facets if facet.is_required]
    assert required == ["comparison_methods"]
    assert blueprint.minimum_independent_lineages == 2


@pytest.mark.parametrize("question", [
    "What immune-cell states were reported?",
    "Which immune state is described by this single-cell study?",
])
def test_explicit_immune_target_is_preserved_without_modality_as_a_support_alias(question):
    blueprint = suggest_blueprint("immune", question, "evidence_qa")
    facet = next(f for f in blueprint.required_facets if f.facet_id == "immune_immune_state")
    assert facet.is_required
    assert facet.aliases == ["immune state", "immune-cell"]
    assert blueprint.minimum_independent_lineages == 2


def test_a_modality_only_passage_cannot_support_an_immune_state_requirement():
    blueprint = suggest_blueprint("immune", "What immune-cell state was reported?", "evidence_qa")
    candidate = TraceCandidate(
        chunk_id="imaging_1", paper_id="imaging",
        text="Single-cell imaging was evaluated with a measurement calibration benchmark.",
        token_cost=20, retrieval_score=1.0, section="methods",
    )
    assert score_candidate_facets(candidate, blueprint)["immune_immune_state"] == 0.0
