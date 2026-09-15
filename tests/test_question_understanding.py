import json

import pytest

from originrag.analysis import corpus_inventory as inventory
from originrag.analysis import question_understanding as understanding
from originrag.analysis.ollama_client import OllamaResult


ORIGINAL = "welceh hoch variablen gen famleien alsoo welche vershcidennen findest du im koprus oder gibt es?"


def model_reply(payload, error=None):
    return OllamaResult(text=json.dumps(payload) if payload is not None else None,
                        model="test-model", base_url="http://localhost", call_performed=True,
                        error=error)


def prepared(**overrides):
    return {"normalized_question": "Which highly variable gene families occur in the corpus?",
            "language": "de", "intent": "corpus_inventory", "unclear": False,
            "search_queries": ["highly variable gene families", "polymorphic multigene families"],
            **overrides}


@pytest.mark.parametrize("question", [ORIGINAL,
    "Welche hochvariablen Genfamilien findest du im Korpus?",
    "Which highly variable gene families occur in the corpus?"])
def test_question_preparation_preserves_original_and_generates_searches(monkeypatch, question):
    calls = []

    def call(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return model_reply(prepared())

    monkeypatch.setattr(understanding, "call_ollama_generate", call)
    result = understanding.understand_question(question)
    assert result["original_question"] == question
    assert result["intent"] == "corpus_inventory"
    assert result["status"] == "ready"
    plan = understanding.understanding_plan(result, task_mode="evidence_qa", max_queries=3)
    assert plan["planner_call_performed"]
    assert plan["subqueries"][0]["query"] == "highly variable gene families"
    assert len(calls) == 1
    assert calls[0][1]["num_ctx"] == 4096


@pytest.mark.parametrize("payload", [
    prepared(unclear=True), prepared(search_queries="not a list"),
    prepared(search_queries=["this proves the conclusion"]),
    prepared(normalized_question="List gene families."),
])
def test_invalid_or_identifier_losing_preparation_does_not_proceed(monkeypatch, payload):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(payload))
    result = understanding.understand_question("Welche RIFIN Familien stehen in GSE12345?")
    assert result["status"] == "needs_clarification"


def test_negation_must_be_preserved(monkeypatch):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question="Which methods occur in the corpus?")))
    assert understanding.understand_question("Welche Methoden verwenden keine Sequenzierung?")["error"] == "normalization_lost_negation"


def test_identifiers_must_survive_into_actual_search_queries(monkeypatch):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question="What measurements are available in GSE12345?")))
    result = understanding.understand_question("Welche Messungen gibt es in GSE12345?")
    assert result["error"] == "search_queries_lost_identifiers_or_numbers"


@pytest.mark.parametrize("question", [
    "Which gene families cause disease according to the corpus?",
    "Which independently replicated studies occur in our library?",
    "Compare the evidence for treatment efficacy in these papers.",
    "Which methods are not represented in the corpus?",
    "Which types are missing from our library?",
])
def test_inference_requests_cannot_be_downgraded_to_inventory(monkeypatch, question):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question=question)))
    assert understanding.understand_question(question)["intent"] == "question"


def test_model_unavailable_is_not_missing_evidence(monkeypatch):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(None, "timeout"))
    result = understanding.understand_question(ORIGINAL)
    assert result["status"] == "unavailable"
    assert "evidence_sufficient" not in result


def test_family_inventory_rejects_descriptions_and_combined_names():
    question = "Which gene families occur in the corpus?"
    assert inventory.inventory_name("ALPHA Protein Family", question) == "ALPHA"
    assert inventory.inventory_name("most polymorphic gene family", question) == ""
    assert inventory.inventory_name("ALPHA, BETA and GAMMA multigene families", question) == ""
    assert inventory.inventory_name("Multi-Gene", question) == ""
    assert inventory.inventory_name("parasite-specific", question) == ""
    assert inventory.inventory_name("virulence gene families", question) == ""
    assert inventory.inventory_name("ALPHA-II", question) == "ALPHA-II"
    assert inventory.inventory_name("finite elements", "Which methods occur in our library?") == "finite elements"


def test_explicit_inventory_is_recognized_even_with_generic_model_intent(monkeypatch):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        intent="question", search_queries=["variable gene families in corpus"])))
    result = understanding.understand_question(ORIGINAL)
    assert result["intent"] == "corpus_inventory"
    assert result["search_queries"] == ["variable gene families"]


@pytest.mark.parametrize("name,question", [
    ("RIFIN", "Which gene families occur in the corpus?"),
    ("finite elements", "Which numerical methods occur in our papers?"),
    ("sonnets", "Which poetic forms occur in the collection?"),
])
def test_inventory_accepts_only_literal_sourced_names_across_domains(monkeypatch, name, question):
    title = f"A study of {name}"
    monkeypatch.setattr(inventory, "call_ollama_generate", lambda *a, **k: model_reply({"items": [
        {"name": name, "quote": title, "paper_id": "P1"},
        {"name": "invented", "quote": title, "paper_id": "P1"},
        {"name": name, "quote": title, "paper_id": "MISSING"},
        {"name": name, "quote": f"We measured {name}", "paper_id": "P1"},
    ]}))
    result = inventory.extract_inventory(question, [{"paper_id": "P1", "title": title}], [])
    assert result["items"] == [{"name": name, "quote": title, "paper_id": "P1"}]
    assert result["scope"] == "retrieved_reports_only"
    text = inventory.inventory_markdown(result, language="de", sources=[
        {"paper_id": "P1", "evidence_ids": ["E001"]}])
    assert "[[cite:E001]]" in text
    assert "kein vollständiges" in text
    assert "keine unabhängige Replikation" in text


def test_empty_inventory_does_not_assert_corpus_absence():
    text = inventory.inventory_markdown({"items": []}, language="de")
    assert "nicht" in text and "gesamte Korpus" in text


def test_inventory_metadata_citations_do_not_require_chunk_ids():
    report = {"items": [{"name": "finite elements", "paper_id": "P1"}]}
    text = inventory.inventory_markdown(report, sources=[
        {"paper_id": "P1", "title": "Numerical methods", "doi": "10.1234/example"}])
    assert "https://doi.org/10.1234/example" in text


def test_inventory_batches_limit_context_and_deduplicate_names(monkeypatch):
    calls = []

    def call(prompt, **kwargs):
        batch = json.loads(prompt.rsplit("\n", 1)[-1])["documents"]
        calls.append((batch, kwargs))
        return model_reply({"items": [
            {"name": "Alpha gene family", "quote": "Alpha gene family", "paper_id": batch[0]["paper_id"]},
            {"name": "Alpha", "quote": "Alpha gene family", "paper_id": batch[0]["paper_id"]},
        ]})

    monkeypatch.setattr(inventory, "call_ollama_generate", call)
    result = inventory.extract_inventory("Which gene families are in the corpus?", [
        {"paper_id": f"P{i}", "title": "Alpha gene family"} for i in range(30)
    ], [])
    assert len(calls) == 4
    assert all(len(batch) <= 6 and options["num_ctx"] == 8192 for batch, options in calls)
    assert len(result["items"]) == 1


def test_inventory_model_failure_is_not_reported_as_no_matching_names(monkeypatch):
    monkeypatch.setattr(inventory, "call_ollama_generate", lambda *a, **k: model_reply(None, "timeout"))
    result = inventory.extract_inventory("Which methods occur in the corpus?", [
        {"paper_id": "P1", "title": "Numerical methods"}], [])
    assert result["errors"]
    assert "Zeitlimit" in inventory.inventory_markdown(result, language="de")
