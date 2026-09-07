from pfempire.trace.utility import compute_utility
from pfempire.trace.facets import facet_coverage
from pfempire.trace.schemas import EvidenceFacet
from conftest import make_candidate


def test_utility_in_unit_interval(blueprint):
    value = compute_utility(
        [
            make_candidate(
                1,
                facets={"f1": 1, "f2": 1},
                scales={"molecular": 1, "clinical": 1},
            )
        ],
        blueprint,
        {},
    )["total"]
    assert 0 <= value <= 1


def test_missing_facet_complement_increases_utility(blueprint):
    first = make_candidate(1, facets={"f1": 1, "f2": 0})
    second = make_candidate(
        2,
        facets={"f1": 0, "f2": 1},
        scales={"clinical": 1},
    )
    assert compute_utility([first, second], blueprint, {})["total"] > compute_utility(
        [first], blueprint, {}
    )["total"]


def test_minimum_items_requires_multiple_evidence_items(blueprint):
    facet = EvidenceFacet(
        facet_id="f1",
        question_id="q1",
        label="EPCR",
        description="EPCR evidence",
        facet_type="mechanism",
        biological_scale="molecular",
        minimum_items=3,
    )
    one = facet_coverage([make_candidate(1, facets={"f1": 1.0})], [facet])
    three = facet_coverage(
        [make_candidate(index, facets={"f1": 1.0}) for index in range(1, 4)],
        [facet],
    )

    assert one["unresolved_required"] == ["f1"]
    assert three["unresolved_required"] == []
