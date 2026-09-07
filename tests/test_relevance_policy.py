from originrag.curation.relevance_policy import relevance_decision


def test_keeps_molecular_pfrh5_vaccine_paper() -> None:
    decision = relevance_decision(
        title="PfRH5 vaccine; from the bench to the vial",
        journal="npj Vaccines",
        category="host_immunity",
    )

    assert decision.keep
    assert "vaccine_or_vaccine_trial" not in decision.reasons


def test_still_excludes_general_vaccine_policy_paper() -> None:
    decision = relevance_decision(
        title="Malaria vaccine uptake and allocation policy",
        journal="Global Health Policy",
        category="public_health",
    )

    assert not decision.keep
    assert "vaccine_or_vaccine_trial" in decision.reasons
