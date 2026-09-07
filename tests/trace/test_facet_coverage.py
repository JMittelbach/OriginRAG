from pfempire.trace.facets import facet_coverage
from conftest import make_candidate

def test_facet_coverage_saturates(blueprint):
    one=facet_coverage([make_candidate(1,facets={"f1":1,"f2":0})],blueprint.required_facets)["total"]
    two=facet_coverage([make_candidate(1,facets={"f1":1,"f2":0}),make_candidate(2,facets={"f1":1,"f2":0})],blueprint.required_facets)["total"]
    three=facet_coverage([make_candidate(i,facets={"f1":1,"f2":0}) for i in range(3)],blueprint.required_facets)["total"]
    assert two-one>three-two
