from __future__ import annotations

import pytest

from originrag.retrieval.query_plan import (
    build_retrieval_queries,
    required_entity_anchor_groups,
)
from originrag.retrieval.source_intent import (
    assess_intervention_evidence,
    is_epcr_pfemp1_intervention_source_request,
)


FROZEN_INTERVENTION_QUESTION = (
    "Find primary studies of therapeutic interventions that disrupt EPCR-binding "
    "PfEMP1 adhesion or its downstream vascular consequences, and report whether "
    "clinical benefit was tested."
)


def test_intervention_intent_requires_source_target_intervention_and_evaluation() -> None:
    assert is_epcr_pfemp1_intervention_source_request(
        FROZEN_INTERVENTION_QUESTION
    )
    assert not is_epcr_pfemp1_intervention_source_request(
        "How does EPCR-binding PfEMP1 contribute to severe malaria?"
    )
    assert not is_epcr_pfemp1_intervention_source_request(
        "Find RIFIN or STEVOR papers on therapeutic targeting."
    )


@pytest.mark.parametrize(
    "question",
    [
        (
            "Are there primary studies testing therapeutic interventions that "
            "disrupt EPCR-binding PfEMP1 adhesion, and was clinical benefit evaluated?"
        ),
        (
            "What primary studies tested therapeutic interventions against "
            "EPCR-binding PfEMP1, and did they evaluate clinical benefit?"
        ),
    ],
)
def test_intervention_intent_accepts_natural_source_question_forms(question: str) -> None:
    assert is_epcr_pfemp1_intervention_source_request(question)


def test_intervention_query_reserves_specific_views_before_generic_expansion() -> None:
    queries = build_retrieval_queries(
        FROZEN_INTERVENTION_QUESTION,
        task="sources",
        max_queries=6,
    )
    labels = [query.label for query in queries]

    assert labels[:4] == [
        "main",
        "epcr_pfemp1_adhesion_intervention",
        "epcr_pfemp1_antibody_intervention",
        "vascular_consequence_intervention",
    ]
    assert all(query.intent == "tested_intervention" for query in queries[1:4])


def test_broad_rifin_stevor_search_reserves_direct_function_views() -> None:
    question = (
        "Find primary studies on Plasmodium falciparum RIFIN or STEVOR proteins "
        "that address receptor binding, immune evasion, rosetting, host-cell "
        "remodelling, or therapeutic targeting."
    )
    queries = build_retrieval_queries(question, task="sources", max_queries=6)
    by_label = {query.label: query for query in queries}

    assert "rifin_receptor_binding" in by_label
    assert "stevor_binding_rosetting" in by_label
    assert "rifin_stevor_membrane" in by_label
    assert all(
        by_label[label].intent == "required_source_facet"
        for label in (
            "rifin_receptor_binding",
            "stevor_binding_rosetting",
            "rifin_stevor_membrane",
        )
    )


@pytest.mark.parametrize(
    "question",
    [
        (
            "Find primary studies of Plasmodium falciparum RIFIN binding to human "
            "immune receptors including LILRB1, LAIR1, KIR2DL1, or KIR2DS1."
        ),
        "What primary studies tested RIFIN receptor binding?",
        "Are there primary studies of RIFIN receptor binding?",
    ],
)
def test_single_rifin_study_search_keeps_family_and_receptor_views(
    question: str,
) -> None:
    queries = build_retrieval_queries(question, task="sources", max_queries=6)

    assert ("rifin", "rifins") in required_entity_anchor_groups(question)
    assert "rifin_receptor_binding" in {query.label for query in queries}


def test_direct_preclinical_intervention_is_labelled_without_clinical_upgrade() -> None:
    assessment = assess_intervention_evidence(
        title="A soluble EPCR decoy blocks PfEMP1 adhesion",
        section="results",
        text=(
            "In this study, a soluble EPCR decoy blocked EPCR-binding PfEMP1 "
            "adhesion in a recombinant binding assay."
        ),
    )

    assert assessment is not None
    assert assessment.evidence_level == "preclinical_functional_intervention"
    assert assessment.clinical_benefit_status == (
        "not_identified_in_retained_evidence"
    )


def test_human_outcome_label_requires_treated_design_and_clinical_endpoint() -> None:
    assessment = assess_intervention_evidence(
        title="Adjunctive therapy for vascular injury in severe malaria",
        section="results",
        text=(
            "We conducted a randomized controlled clinical trial. Patients received "
            "the inhibitor fasudil or control. The treatment reduced endothelial "
            "injury and mortality."
        ),
    )

    assert assessment is not None
    assert assessment.evidence_level == "human_interventional_outcome"
    assert assessment.clinical_benefit_status == (
        "human_clinical_benefit_test_identified"
    )


def test_antibody_origin_in_an_animal_does_not_imply_animal_intervention() -> None:
    assessment = assess_intervention_evidence(
        title="An inhibitory monoclonal antibody blocks PfEMP1 adhesion",
        section="results",
        text=(
            "A mouse monoclonal antibody inhibited EPCR-binding PfEMP1 in a "
            "recombinant binding assay."
        ),
    )

    assert assessment is not None
    assert assessment.evidence_level == "preclinical_functional_intervention"


def test_animal_intervention_requires_treatment_of_the_animal() -> None:
    assessment = assess_intervention_evidence(
        title="A kinase inhibitor protects the vascular barrier in severe malaria",
        section="results",
        text=(
            "In this study, mice were treated with the inhibitor fasudil, which "
            "reduced endothelial injury."
        ),
    )

    assert assessment is not None
    assert assessment.evidence_level == "animal_intervention"


def test_unrelated_saline_mouse_control_does_not_upgrade_preclinical_agent() -> None:
    assessment = assess_intervention_evidence(
        title="A soluble EPCR decoy blocks PfEMP1 adhesion",
        section="results",
        text=(
            "We confirmed blockade in a recombinant binding assay. In a separate "
            "control, mice were treated with saline."
        ),
    )

    assert assessment is not None
    assert assessment.evidence_level == "preclinical_functional_intervention"


def test_unrelated_human_treatment_and_outcome_mentions_do_not_compose() -> None:
    assessment = assess_intervention_evidence(
        title="Endothelial injury in severe malaria",
        section="results",
        text=(
            "Patients received routine antimalarial care. Separately, an in vitro "
            "soluble EPCR decoy blocked PfEMP1 adhesion. Baseline mortality was "
            "recorded for the observational cohort."
        ),
    )

    assert assessment is not None
    assert assessment.evidence_level == "preclinical_functional_intervention"
    assert assessment.clinical_benefit_status == (
        "not_identified_in_retained_evidence"
    )


def test_generic_drug_resistance_sentence_is_not_an_intervention() -> None:
    assert (
        assess_intervention_evidence(
            title="Endothelial activation in severe malaria",
            section="results",
            text="Drug resistance reduced parasite clearance in the cohort.",
        )
        is None
    )


@pytest.mark.parametrize(
    ("title", "section", "text"),
    [
        (
            "Structural conservation of EPCR-binding PfEMP1",
            "results",
            "We measured PfEMP1 binding affinity and mapped the receptor interface.",
        ),
        (
            "Naturally acquired immunity to EPCR-binding PfEMP1",
            "results",
            "We measured naturally acquired IgG in an observational cohort.",
        ),
        (
            "Adhesion-inhibitory antibodies against ICAM-1-binding PfEMP1",
            "results",
            "The monoclonal antibodies blocked ICAM-1 adhesion in a binding assay.",
        ),
        (
            "EPCR-binding PfEMP1 in severe malaria",
            "results",
            "Previous studies showed that soluble EPCR decoys blocked adhesion [12].",
        ),
        (
            "A possible soluble EPCR therapy for severe malaria",
            "discussion",
            "A future clinical trial should test whether the decoy blocks adhesion.",
        ),
    ],
)
def test_mechanism_observation_background_and_proposal_are_not_interventions(
    title: str,
    section: str,
    text: str,
) -> None:
    assert assess_intervention_evidence(title=title, section=section, text=text) is None
