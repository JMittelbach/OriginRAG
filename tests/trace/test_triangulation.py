from originrag.trace.utility import saturating_coverage
from conftest import make_candidate

def test_scale_triangulation_rewards_complement():
    molecular=make_candidate(1,scales={"molecular":1})
    clinical=make_candidate(2,scales={"clinical":1})
    assert saturating_coverage([molecular,clinical],"scale_scores",["molecular","clinical"])["total"]>saturating_coverage([molecular],"scale_scores",["molecular","clinical"])["total"]
