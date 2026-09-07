from conftest import make_candidate

from pfempire.trace.counterfactual import paper_flow_contributions


def test_flow_counterfactual_identifies_critical_complement(blueprint):
    molecular = make_candidate(1, facets={"f1": 1.0, "f2": 0.0})
    clinical = make_candidate(
        2,
        facets={"f1": 0.0, "f2": 1.0},
        scales={"clinical": 1.0},
    ).model_copy(update={"evidence_type_scores": {"observational": 1.0}})
    rows = paper_flow_contributions(
        [molecular, clinical],
        blueprint,
        minimum_papers=2,
        decision_trace=[{"paper_id": clinical.paper_id, "reason": "constraint_repair"}],
    )
    by_paper = {row["paper_id"]: row for row in rows}
    assert by_paper[clinical.paper_id]["bottleneck_contribution"] > 0
    assert "f2" in by_paper[clinical.paper_id]["facets_lost_without_paper"]
    assert by_paper[clinical.paper_id]["breadth_constraint_contribution"] == 1
    assert by_paper[clinical.paper_id]["selection_reason"] == "constraint_repair"
