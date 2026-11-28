from __future__ import annotations

import pytest

from originrag.analysis.chat import ChatTurnContext, build_chat_retrieval_question, is_followup_question, resolve_chat_task
from originrag.retrieval.quick_list import paper_list_request


@pytest.mark.parametrize("query,expected", [
    ("Could you give me ten articles about gap junctions?", "sources"),
    ("Gib mir fünf Studien über Solarzellen", "sources"),
    ("Look in the papers on coffee and tell me who funded them.", "answer"),
    ("Fass bitte die Studien zu Solarzellen zusammen.", "answer"),
    ("Who funded the papers about coffee?", "answer"),
    ("How do gap junctions work?", "answer"),
    ("Which hypothesis did the authors test?", "answer"),
    ("Welche Methoden wurden in den Studien zu Solarzellen verwendet?", "answer"),
    ("Find papers about ceramics and summarize their results.", "answer"),
])
def test_deliverable_not_topic_controls_routing(query, expected):
    assert resolve_chat_task(query) == expected
    assert bool(paper_list_request(query)) == (expected == "sources")

def test_resolve_chat_task_auto_detects_hypothesis() -> None:
    task = resolve_chat_task(
        "Mach mir eine testbare Hypothese zu PfEMP1 antibody breadth.",
        configured_task="auto",
    )

    assert task == "hypothesis"


def test_resolve_chat_task_defaults_to_evidence_answer() -> None:
    assert (
        resolve_chat_task(
            "How consistently is EPCR-binding PfEMP1 associated with severe malaria?",
            configured_task="auto",
        )
        == "answer"
    )


@pytest.mark.parametrize("question", [
    "paper which used ATS antibody",
    "papers which use ats antibody",
    "a study that used an anti-GFP antibody",
    "articles using BRD4 antibodies",
    "reports which examined antibody specificity",
])
def test_relative_clause_paper_requests_use_source_search(question: str) -> None:
    assert resolve_chat_task(question) == "sources"


def test_paper_usage_with_explicit_synthesis_still_requests_answer() -> None:
    assert resolve_chat_task(
        "Find papers which used GFP antibody, and how strong is the evidence?"
    ) == "answer"


def test_resolve_chat_task_auto_detects_gaps() -> None:
    task = resolve_chat_task("Find research gaps in sex differences and malaria immunity.", configured_task="auto")

    assert task == "gaps"


def test_resolve_chat_task_auto_detects_german_gap_without_umlaut() -> None:
    task = resolve_chat_task("Gibt es luecken bei sex differences in malaria?", configured_task="auto")

    assert task == "gaps"


def test_resolve_chat_task_does_not_treat_german_study_question_as_gap() -> None:
    task = resolve_chat_task("Welche Studien untersuchten RIFINs?", configured_task="auto")

    assert task == "sources"


@pytest.mark.parametrize(
    "question",
    [
        "Welche Aspekte von RIFINs sind nicht untersucht?",
        "Welche RIFIN-Funktionen sind kaum untersucht?",
        "Was ist bei STEVORs noch wenig untersucht?",
    ],
)
def test_resolve_chat_task_keeps_german_understudied_questions_as_gaps(
    question: str,
) -> None:
    assert resolve_chat_task(question, configured_task="auto") == "gaps"


def test_resolve_chat_task_auto_detects_source_lookup() -> None:
    task = resolve_chat_task("Wie hieß das paper mit der Kenya Kohorte?", configured_task="auto")

    assert task == "sources"


def test_resolve_chat_task_auto_detects_topical_paper_discovery() -> None:
    task = resolve_chat_task(
        "Find primary studies linking var expression to malaria syndromes.",
        configured_task="auto",
    )

    assert task == "sources"


def test_resolve_chat_task_auto_detects_ranked_empirical_paper_request() -> None:
    task = resolve_chat_task(
        "Find and rank the strongest empirical papers that directly investigate "
        "associations between var genes and severe malaria.",
        configured_task="auto",
    )

    assert task == "sources"


def test_resolve_chat_task_mixed_paper_and_evidence_assessment_uses_answer() -> None:
    assert (
        resolve_chat_task(
            "Which papers link EPCR-binding PfEMP1 to severe malaria, and how strong "
            "is the evidence?",
            configured_task="auto",
        )
        == "answer"
    )


def test_resolve_chat_task_source_followup_inherits_previous_task() -> None:
    assert (
        resolve_chat_task(
            "And newer ones?",
            configured_task="auto",
            previous_task="sources",
        )
        == "sources"
    )


@pytest.mark.parametrize(
    "question",
    ["How about newer papers?", "Wie viele neuere Papers gibt es?"],
)
def test_resolve_chat_task_source_inventory_followup_stays_source_search(
    question: str,
) -> None:
    assert (
        resolve_chat_task(
            question,
            configured_task="auto",
            previous_task="sources",
        )
        == "sources"
    )


def test_resolve_chat_task_source_relative_clause_does_not_force_synthesis() -> None:
    assert (
        resolve_chat_task(
            "Find papers that assess whether RIFINs bind KIR2DL1.",
            configured_task="auto",
        )
        == "sources"
    )


@pytest.mark.parametrize(
    "question",
    [
        "Why are they relevant?",
        "And why are they relevant?",
        "What do these findings mean?",
        "How do their findings compare?",
        "Wie unterscheiden sich ihre Ergebnisse?",
        "Summarize them.",
        "Wie stark ist die Evidenz?",
    ],
)
def test_resolve_chat_task_source_followup_can_request_synthesis(question: str) -> None:
    assert (
        resolve_chat_task(
            question,
            configured_task="auto",
            previous_task="sources",
        )
        == "answer"
    )


@pytest.mark.parametrize(
    "question",
    [
        "Encuentra y clasifica los estudios primarios sobre var y malaria grave.",
        "Trouve et classe les études primaires sur var et le paludisme grave.",
    ],
)
def test_resolve_chat_task_auto_detects_multilingual_paper_search(question: str) -> None:
    assert resolve_chat_task(question, configured_task="auto") == "sources"


def test_resolve_chat_task_auto_detects_identifier_lookup() -> None:
    assert (
        resolve_chat_task("Find DOI 10.7554/eLife.110810.1", configured_task="auto")
        == "sources"
    )


def test_resolve_chat_task_respects_explicit_task() -> None:
    assert resolve_chat_task("Find gaps in this area.", configured_task="answer") == "answer"


def test_resolve_chat_task_rejects_unknown_task() -> None:
    with pytest.raises(ValueError, match="Unsupported chat task"):
        resolve_chat_task("Question", configured_task="review")


def test_followup_question_gets_previous_context() -> None:
    question = build_chat_retrieval_question(
        "Und was ist mit sex differences?",
        ["What evidence links PfEMP1 to severe malaria?"],
    )

    assert "Previous user question: What evidence links PfEMP1 to severe malaria?" in question
    assert "Current user question: Und was ist mit sex differences?" in question


def test_followup_question_gets_previous_sources_and_titles() -> None:
    question = build_chat_retrieval_question(
        "Und was heißt das für EPCR?",
        [
            ChatTurnContext(
                user_question="Warum ist Plasmodium falciparum so virulent?",
                retrieval_question="Warum ist Plasmodium falciparum so virulent?",
                source_labels=("[Turner 2013, Results]", "[Jespersen 2016, Results]"),
                source_titles=("Severe malaria is associated with EPCR binding",),
            )
        ],
    )

    assert "Previous cited paper titles: Severe malaria is associated with EPCR binding" in question
    assert "[Turner 2013, Results]" in question
    assert "Current user question: Und was heißt das für EPCR?" in question


def test_standalone_question_does_not_get_previous_context() -> None:
    question = build_chat_retrieval_question(
        "What evidence links PfEMP1 antibody breadth to asymptomatic infection?",
        ["What evidence links PfEMP1 to severe malaria?"],
    )

    assert question == "What evidence links PfEMP1 antibody breadth to asymptomatic infection?"


def test_is_followup_question_handles_short_german_followups() -> None:
    assert is_followup_question("Und warum genau?")


def test_is_followup_question_handles_short_pronoun_questions() -> None:
    assert is_followup_question("Was heißt das?")


@pytest.mark.parametrize("question", [
    "What is statistical power?", "Explain digital filters.", "Are white dwarfs bright?",
    "Wie funktionieren digitale Filter?", "Was ist die Derivation?", "Why do stars emit light?",
    "New question: what does this sensor measure?", "Anderes Thema: wie heißt das Verfahren?",
])
def test_short_standalone_questions_do_not_inherit_an_unrelated_topic(question):
    assert not is_followup_question(question)
    assert build_chat_retrieval_question(question, ["Earlier question about ocean currents"]) == question


@pytest.mark.parametrize("question", [
    "Why?", "Why not?", "And why exactly?", "What were their main limitations?",
    "Could you please explain what they measured and how that relates to the earlier result?",
    "Compare the first two papers.", "Was bedeutet das?", "Warum nicht?", "Wurde das direkt gemessen?",
    "What are the limitations?", "Pourquoi?",
])
def test_explicit_followups_keep_conversation_context(question):
    assert is_followup_question(question)
