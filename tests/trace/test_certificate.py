from originrag.trace.certificate import build_certificate
from conftest import make_candidate

def test_insufficient_blocks_strong_claim(blueprint):
    cert=build_certificate("q",[],blueprint,{})
    assert cert.evidence_sufficiency=="insufficient" and cert.allowed_claim_strength=="insufficient_evidence"
    assert cert.evidence_accounting_version == "unit_max_v2"
def test_contradiction_requires_reviewed_polarity(blueprint):
    blueprint.required_polarities=["support","contradiction"]
    cert=build_certificate("q",[make_candidate(1,facets={"f1":1,"f2":1})],blueprint,{})
    assert "required_polarity_missing" in cert.reasons

def test_suggested_blueprint_cannot_receive_good_grade(blueprint):
    candidates=[
        make_candidate(index,facets={"f1":1,"f2":1},scales={"molecular":1,"clinical":1}).model_copy(
            update={"evidence_type_scores":{"biochemical":1,"observational":1}}
        )
        for index in range(1,5)
    ]
    cert=build_certificate("q",candidates,blueprint,{},lineage_graph_status="reviewed")
    assert cert.evidence_sufficiency=="usable"
    assert "blueprint_not_human_reviewed" in cert.reasons


def test_unknown_lineage_graph_caps_claim_strength(blueprint):
    candidates = [
        make_candidate(index, facets={"f1": 1, "f2": 1}, scales={"molecular": 1, "clinical": 1})
        for index in range(1, 4)
    ]

    cert = build_certificate(
        "q",
        candidates,
        blueprint.model_copy(update={"review_status": "human_reviewed"}),
        {},
        lineage_graph_status="not_provided",
    )

    assert cert.evidence_sufficiency == "weak"
    assert cert.allowed_claim_strength == "hypothesis_only"
    assert "lineage_graph_not_provided" in cert.reasons


def test_paper_floor_and_support_are_hard_certificate_requirements(blueprint):
    candidates = [
        make_candidate(
            index,
            facets={"f1": 1, "f2": 1},
            scales={"molecular": 1, "clinical": 1},
            polarity={"unknown": 1},
        ).model_copy(
            update={"evidence_type_scores": {"biochemical": 1, "observational": 1}}
        )
        for index in range(1, 4)
    ]

    cert = build_certificate(
        "q",
        candidates,
        blueprint.model_copy(update={"review_status": "human_reviewed"}),
        {},
        minimum_required_papers=4,
        lineage_graph_status="reviewed",
    )

    assert cert.evidence_sufficiency == "weak"
    assert cert.allowed_claim_strength == "hypothesis_only"
    assert "minimum_selected_papers_unmet" in cert.reasons
    assert "required_polarity_missing" in cert.reasons


def test_partial_dependence_below_blueprint_neff_caps_certificate(blueprint):
    candidates = [
        make_candidate(index, facets={"f1": 1, "f2": 1})
        for index in range(1, 4)
    ]
    dependence = {
        (left.paper_id, right.paper_id): 0.75
        for left in candidates
        for right in candidates
        if left.paper_id != right.paper_id
    }

    cert = build_certificate(
        "q",
        candidates,
        blueprint.model_copy(
            update={
                "minimum_independent_lineages": 3,
                "review_status": "human_reviewed",
            }
        ),
        dependence,
        lineage_graph_status="reviewed",
    )

    assert cert.evidence_sufficiency == "weak"
    assert "insufficient_effective_independent_evidence" in cert.reasons


def test_must_linked_report_clone_cannot_satisfy_certificate_floor(blueprint):
    first = make_candidate(
        1,
        facets={"f1": 1, "f2": 1},
        scales={"molecular": 1, "clinical": 1},
    ).model_copy(
        update={
            "evidence_type_scores": {"biochemical": 1, "observational": 1},
            "study_lineage_id": "SHARED",
        }
    )
    clone = make_candidate(
        2,
        facets={"f1": 1, "f2": 1},
        scales={"molecular": 1, "clinical": 1},
    ).model_copy(
        update={
            "evidence_type_scores": {"biochemical": 1, "observational": 1},
            "study_lineage_id": "SHARED",
        }
    )
    reviewed = blueprint.model_copy(
        update={
            "minimum_independent_lineages": 1,
            "review_status": "human_reviewed",
        }
    )

    base = build_certificate(
        "q",
        [first],
        reviewed,
        {},
        minimum_required_papers=2,
        lineage_graph_status="reviewed",
    )
    cloned = build_certificate(
        "q",
        [first, clone],
        reviewed,
        {},
        minimum_required_papers=2,
        lineage_graph_status="reviewed",
    )

    assert "minimum_selected_papers_unmet" in base.reasons
    assert "minimum_selected_papers_unmet" in cloned.reasons
    assert cloned.evidence_sufficiency == base.evidence_sufficiency
    assert cloned.effective_independent_evidence == base.effective_independent_evidence
