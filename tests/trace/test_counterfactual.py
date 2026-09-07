from originrag.trace.counterfactual import paper_counterfactual_contributions
from conftest import make_candidate

def test_counterfactual_rows_are_paper_level_and_finite(blueprint):
    rows=paper_counterfactual_contributions([make_candidate(1),make_candidate(2,facets={"f2":1})],blueprint,{})
    assert {r["paper_id"] for r in rows}=={"P1","P2"}
    assert all(r["utility_contribution"]>=0 for r in rows)
