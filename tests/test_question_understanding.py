import json
from itertools import combinations, product

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


@pytest.mark.parametrize("question,subject", [
    ("What does OptiCal measure, and does it correct drift?", "OptiCal"),
    ("What does MAPsim simulate?", "MAPsim"),
    ("How does MAPsim compare with OptiCal?", ""),
    ("Wie funktioniert OptiCal?", "OptiCal"),
    ("How does timing affect measurements?", ""),
    ("What does OptiCal and the other meter measure?", ""),
    ("How does OptiCal compare with MeasureIt?", ""),
    ("What does OptiCal-Lite measure?", ""),
])
def test_named_measurement_subject_is_not_inferred_for_comparisons(question, subject):
    assert understanding.named_method_subject(question) == subject


def test_named_subject_stays_in_each_planned_search_without_an_extra_query():
    record = {"original_question": "What does CalibMap measure?", "model": "local",
              "normalized_question": "What does CalibMap measure?",
              "search_queries": ["optical drift", "CalibMap calibration"]}
    plan = understanding.understanding_plan(record, task_mode="evidence_qa", max_queries=2)
    assert [q["query"] for q in plan["subqueries"]] == ["CalibMap optical drift", "CalibMap calibration"]


def test_negation_must_be_preserved(monkeypatch):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question="Which methods occur in the corpus?")))
    assert understanding.understand_question("Welche Methoden verwenden keine Sequenzierung?")["error"] == "normalization_lost_negation"


@pytest.mark.parametrize("question,normalized,error", [
    ("Welche Methoden verwenden weder Simulationen noch Sensoren?",
     "Which methods use simulations and sensors?", "normalization_lost_negation"),
    ("Which methods use neither simulations nor sensors?",
     "Which methods use simulations and sensors?", "normalization_lost_negation"),
    ("Welche Verfahren benötigen keine Kalibrierung?",
     "Which procedures need calibration?", "normalization_lost_negation"),
    ("Welche Studien verwenden mindestens 10 Messungen?",
     "Which studies use at most 10 measurements?", "normalization_changed_numeric_comparison"),
    ("Which studies use fewer than 10 measurements?",
     "Which studies use at most 10 measurements?", "normalization_changed_numeric_comparison"),
    ("Welche Studien wurden vor 2020 veröffentlicht?",
     "Which studies were published after 2020?", "normalization_changed_numeric_comparison"),
    ("Which studies use at least 10 measurements?",
     "Which studies use 10 measurements?", "normalization_changed_numeric_comparison"),
    ("Which measurements satisfy x<10?",
     "Which measurements satisfy x>10?", "normalization_changed_numeric_comparison"),
    ("Which measurements satisfy x<=10?",
     "Which measurements satisfy x<10?", "normalization_changed_numeric_comparison"),
    ("Which measurements are not <10?",
     "Which measurements are <10?", "normalization_changed_numeric_comparison"),
    ("Which methods have an error rate below 10%?",
     "Which methods have an error rate below 10?", "normalization_changed_percentage_units"),
    ("Vergleiche Atlas mit Borealis.", "Explain Atlas.", "normalization_lost_comparison"),
    ("Welche Verfahren benötigen nicht weniger als 10 Messungen ohne Kalibrierung?",
     "Which procedures require at least 10 measurements with calibration?", "normalization_lost_negation"),
    ("Which methods work without calibration?",
     "Which methods use no fewer than 10 calibrated measurements?", "normalization_lost_negation"),
])
def test_explicit_semantic_changes_fail_question_preparation(monkeypatch, question, normalized, error):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question=normalized, intent="question", search_queries=[normalized])))

    result = understanding.understand_question(question)

    assert result["status"] == "needs_clarification"
    assert result["error"] == error
    assert "normalized_question" not in result
    assert "evidence_sufficient" not in result


@pytest.mark.parametrize("question,normalized", [
    ("Welche Methoden brauchen weder Simulationen noch Sensoren?",
     "Which methods use neither simulations nor sensors?"),
    ("Welche Verfahren benötigen keine Kalibrierung?",
     "Which procedures work without calibration?"),
    ("Welche Studien verwenden nicht weniger als 10 Messungen?",
     "Which studies use at least 10 measurements?"),
    ("Welche Studien verwenden nicht mehr als 10 Messungen?",
     "Which studies use at most 10 measurements?"),
    ("Welche Studien wurden nicht vor 2020 veröffentlicht?",
     "Which studies were published in 2020 or later?"),
    ("Which studies use no fewer than 10 measurements?",
     "Which studies use 10 measurements or more?"),
    ("Which studies use at most 10 measurements?",
     "Which studies use 10 or fewer measurements?"),
    ("Which studies use at least 10 measurements?",
     "Which studies use ≥10 measurements?"),
    ("Which measurements satisfy x<10?",
     "Which measurements have x below 10?"),
    ("Which studies use at least 10 measurements and report p<0.05?",
     "Which studies use at least 10 measurements and report p below 0.05?"),
    ("Welche Methoden haben höchstens 5% Fehler?",
     "Which methods have errors of 5% or less?"),
    ("Which studies use not greater than 10 measurements?",
     "Which studies use at most 10 measurements?"),
    ("Which studies were published not earlier than 2020?",
     "Which studies were published in 2020 or later?"),
    ("Which methods have an error rate below 10%?",
     "Which methods have an error rate less than 10 percent?"),
    ("Which methods have error rates between 10% and 20%?",
     "Which methods have error rates between 10 and 20 percent?"),
    ("Vergleiche Atlas mit Borealis.", "How do Atlas and Borealis differ?"),
    ("Which methods use fewer than 10 measurements without calibration?",
     "Which methods use fewer than 10 measurements and do not require calibration?"),
    ("Welche Methoden gibt es?", "Which methods are available?"),
])
def test_faithful_constraint_paraphrases_remain_usable(monkeypatch, question, normalized):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question=normalized, intent="question", search_queries=[normalized])))

    result = understanding.understand_question(question)

    assert result["status"] == "ready", result["error"]
    assert result["normalized_question"] == normalized


@pytest.mark.parametrize("original_operator,normalized_operator", [
    ("not <", ">="), ("not >", "<="), ("not <=", ">"), ("not >=", "<"),
    ("not ≤", ">"), ("not ≥", "<"), ("nicht <", "≥"), ("nicht >", "≤"),
])
def test_immediately_negated_symbolic_bounds_keep_their_meaning(
    monkeypatch, original_operator, normalized_operator,
):
    question = f"Which measurements are {original_operator}10?"
    normalized = f"Which measurements are {normalized_operator}10?"
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question=normalized, intent="question", search_queries=[normalized])))

    result = understanding.understand_question(question)

    assert result["status"] == "ready", result["error"]


@pytest.mark.parametrize("normalized", [None, [], {}, 10, True])
def test_malformed_normalized_questions_never_reach_constraint_checks(monkeypatch, normalized):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question=normalized)))

    result = understanding.understand_question("Which methods use at least 10 measurements?")

    assert result["status"] == "needs_clarification"
    assert result["error"] == "invalid_normalized_question"


def test_identifiers_must_survive_into_actual_search_queries(monkeypatch):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question="What measurements are available in GSE12345?")))
    result = understanding.understand_question("Welche Messungen gibt es in GSE12345?")
    assert result["error"] == "search_queries_lost_identifiers_or_numbers"


@pytest.mark.parametrize("question,normalized", [
    ("Welche Methoden haben eine Fehlerrate unter 0,05?",
     "Which methods have an error rate below 0.05?"),
    ("Welche Methoden haben eine Fehlerrate unter 0,050?",
     "Which methods have an error rate below 0.05?"),
    ("Which methods have errors below 0.0500%?",
     "Which methods have errors below 0.05 percent?"),
    ("Which methods have errors below 1e-3?",
     "Which methods have errors below 0.001?"),
    ("Which methods have errors below 1e-3?",
     "Which methods have errors below 1.00e-3?"),
    ("Which studies report -2.50 units?", "Which studies report -2.5 units?"),
    ("Welche Methoden benötigen höchstens 2,5 Sekunden?",
     "Which methods need at most 2.5 seconds?"),
    ("Welche Methoden berichten einen Fehler von 0,123?",
     "Which methods report an error of 0.123?"),
    ("Welche Messungen sind unter -0,05?", "Which measurements are below -0.05?"),
    ("Welche Studien berichten einen Effekt von −2?", "Which studies report an effect of -2?"),
    ("Which studies report a change of +2?", "Which studies report a change of 2?"),
    ("Which studies used 10-20 measurements?", "Which studies used between 10 and 20 measurements?"),
    ("Which studies used 10 -20 measurements?", "Which studies used between 10 and 20 measurements?"),
    ("Which studies used 10 - 20 measurements?", "Which studies used between 10 and 20 measurements?"),
    ("Which study was published on 2020-10-21?", "Which study was published on 2020/10/21?"),
    ("What is reported for SAMPLE-2?", "What is reported for SAMPLE-2?"),
    ("What is reported in 10.1234/example-2?", "What is reported in 10.1234/example-2?"),
])
def test_numeric_locale_and_sign_preservation_accept_faithful_preparation(monkeypatch, question, normalized):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question=normalized, intent="question", search_queries=[normalized])))

    result = understanding.understand_question(question)

    assert result["status"] == "ready", result["error"]
    plan = understanding.understanding_plan(result, task_mode="evidence_qa", max_queries=1)
    assert plan["subqueries"][0]["query"] == normalized


@pytest.mark.parametrize("question,normalized", [
    ("Welche Studien berichten einen Effekt von -2?", "Which studies report an effect of 2?"),
    ("Which studies report a change of −0.5?", "Which studies report a change of 0.5?"),
    ("Which studies report for SAMPLE2 -5?", "Which studies report for SAMPLE2 5?"),
    ("Which studies report a change of 2?", "Which studies report a change of -2?"),
    ("Welche Methoden haben eine Fehlerrate unter 0,05?",
     "Which methods have an error rate below 0.5?"),
    ("Which studies use 1,234 measurements?", "Which studies use 1.234 measurements?"),
    ("Which studies use 1.234 measurements?", "Which studies use 1,234 measurements?"),
    ("Which methods have errors below 1e-3?", "Which methods have errors below 2e-3?"),
    ("Which methods have errors below 1e-3?", "Which methods have errors below 1e3?"),
])
def test_sign_changes_and_ambiguous_numeric_locale_changes_fail_preparation(monkeypatch, question, normalized):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question=normalized, intent="question", search_queries=[normalized])))

    result = understanding.understand_question(question)

    assert result["status"] == "needs_clarification"
    assert result["error"] == "normalization_changed_identifiers_or_numbers"


def test_negative_number_must_survive_actual_search_queries(monkeypatch):
    question = "Which studies report an effect of -2?"
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question=question, intent="question", search_queries=["effect size 2 studies"])))

    result = understanding.understand_question(question)

    assert result["error"] == "search_queries_lost_identifiers_or_numbers"


def test_query_budget_keeps_faithful_locale_and_signed_numeric_terms():
    question = "Welche Studien berichten einen Effekt von -2,5?"
    record = {"original_question": question,
              "normalized_question": "Which studies report an effect of -2.5?", "model": "test",
              "search_queries": ["effect size 2.5 studies", "effect size -2.5 studies"]}

    plan = understanding.understanding_plan(record, task_mode="evidence_qa", max_queries=1)

    assert plan["subqueries"][0]["query"] == "effect size -2.5 studies"
    assert "search_queries_reordered_to_preserve_identifiers" in plan["warnings"]


@pytest.mark.parametrize("question,normalized", [
    ("What is AnnData according to the SCANPY paper?",
     "What is AnnData according to the Scanpy paper?"),
    ("Was ist AnnData laut dem SCANPY Paper?",
     "What is AnnData according to the scanpy paper?"),
    ("What does SAMPLE123 contain?", "What does sample123 contain?"),
])
def test_original_identifiers_are_preserved_case_insensitively(monkeypatch, question, normalized):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question=normalized, intent="question", search_queries=[normalized])))

    result = understanding.understand_question(question)

    assert result["status"] == "ready", result["error"]
    plan = understanding.understanding_plan(result, task_mode="evidence_qa", max_queries=1)
    assert plan["subqueries"][0]["query"] == normalized


@pytest.mark.parametrize("candidate", ["Scanpy2", "PreScanpy", "AnnData"])
@pytest.mark.parametrize("changed_field", ["normalized_question", "search_queries"])
def test_changed_or_removed_identifiers_are_not_hidden_by_case_handling(
    monkeypatch, candidate, changed_field,
):
    question = "What does SCANPY provide?"
    payload = prepared(normalized_question="What does Scanpy provide?", intent="question",
                       search_queries=["Scanpy capabilities"])
    payload[changed_field] = (f"What does {candidate} provide?" if changed_field == "normalized_question"
                              else [f"{candidate} capabilities"])
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(payload))

    result = understanding.understand_question(question)

    assert result["status"] == "needs_clarification"
    assert result["error"] == ("normalization_changed_identifiers_or_numbers"
                               if changed_field == "normalized_question"
                               else "search_queries_lost_identifiers_or_numbers")


def test_query_budget_keeps_original_identifier_despite_case_changes():
    record = {"original_question": "What does SCANPY provide?",
              "normalized_question": "What does Scanpy provide?", "model": "test",
              "search_queries": ["analysis tools", "Scanpy capabilities"]}

    plan = understanding.understanding_plan(record, task_mode="evidence_qa", max_queries=1)

    assert plan["subqueries"][0]["query"] == "Scanpy capabilities"
    assert "search_queries_reordered_to_preserve_identifiers" in plan["warnings"]


def test_extreme_numeric_exponents_remain_bounded_and_exact():
    exponent = "9" * 80
    value = "1e" + exponent
    assert understanding.protected_terms(value) == {value}
    assert understanding.preserves_protected_terms({value}, value)
    assert not understanding.preserves_protected_terms({value}, "2e" + exponent)


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


@pytest.mark.parametrize("question", [
    "Which methods in the corpus are faster than Atlas?",
    "Which methods in the corpus are slower than Atlas?",
    "Which methods in the corpus outperform Atlas?",
    "Which reliable methods occur in the corpus?",
    "Which methods in the corpus are reliable?",
    "Which methods are most accurate in our library?",
    "Which algorithms are more efficient in our papers?",
    "Which methods are superior to Atlas in the corpus?",
    "Which methods have the worst errors in the library?",
    "Which methods in the corpus have lower latency than Atlas?",
    "Which methods in the corpus have higher accuracy than Atlas?",
    "Which methods in the corpus have an error rate below 0.05?",
    "Which methods in the corpus need fewer than 10 measurements?",
    "Which methods in the corpus have error rates of 5% or less?",
])
@pytest.mark.parametrize("intent", ["question", "corpus_inventory"])
def test_evaluative_requests_keep_the_question_route(monkeypatch, question, intent):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question=question, intent=intent, language="en", search_queries=[question])))

    result = understanding.understand_question(question)

    assert result["status"] == "ready", result["error"]
    assert result["intent"] == "question"


@pytest.mark.parametrize("question", [
    "Which accuracy metrics occur in the corpus?",
    "Which performance metrics are reported in our papers?",
    "Which reliability metrics occur in the library?",
    "List the numerical methods mentioned in the corpus.",
    "Which datasets occur in our library?",
])
def test_descriptive_metric_inventories_remain_available(monkeypatch, question):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question=question, intent="corpus_inventory", language="en", search_queries=[question])))

    result = understanding.understand_question(question)

    assert result["status"] == "ready", result["error"]
    assert result["intent"] == "corpus_inventory"


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


@pytest.mark.parametrize("queries", [
    ["in the corpus"],
    ["within the library", "from the corpus"],
    ["   "],
])
def test_empty_searches_after_inventory_cleanup_are_not_ready(monkeypatch, queries):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question="Which methods occur in the corpus?", search_queries=queries)))

    result = understanding.understand_question("Which methods occur in the corpus?")

    assert result["status"] == "needs_clarification"
    assert result["error"] == "invalid_query_plan"
    assert "evidence_sufficient" not in result


def test_inventory_cleanup_keeps_and_deduplicates_usable_searches(monkeypatch):
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question="Which methods occur in the corpus?",
        search_queries=["in the corpus", " numerical   methods in the library ", "numerical methods"])))

    result = understanding.understand_question("Which methods occur in the corpus?")
    plan = understanding.understanding_plan(result, task_mode="evidence_qa", max_queries=3)

    assert result["status"] == "ready"
    assert result["search_queries"] == ["numerical methods"]
    assert [query["query"] for query in plan["subqueries"]] == ["numerical methods"]


def test_inventory_cleanup_cannot_erase_an_explicit_identifier(monkeypatch):
    question = "Which methods occur in CORPUS?"
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question=question, search_queries=["methods in CORPUS"])))

    result = understanding.understand_question(question)

    assert result["status"] == "needs_clarification"
    assert result["error"] == "search_queries_lost_identifiers_or_numbers"


@pytest.mark.parametrize("max_queries", [0, 1])
def test_query_limit_keeps_the_search_containing_the_identifier(monkeypatch, max_queries):
    question = "What measurements are reported in SAMPLE123?"
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *a, **k: model_reply(prepared(
        normalized_question=question, intent="question",
        search_queries=["measurement calibration", "SAMPLE123 measurements"])))

    record = understanding.understand_question(question)
    plan = understanding.understanding_plan(record, task_mode="evidence_qa", max_queries=max_queries)

    assert [query["query"] for query in plan["subqueries"]] == ["SAMPLE123 measurements"]
    assert "search_queries_reordered_to_preserve_identifiers" in plan["warnings"]
    assert record["search_queries"] == ["measurement calibration", "SAMPLE123 measurements"]


def test_query_limit_preserves_multiple_identifiers_in_actual_searches():
    question = "Compare SAMPLE123 and SAMPLE456 measurements."
    record = {"original_question": question, "normalized_question": question, "model": "test",
              "search_queries": ["measurement calibration", "SAMPLE123 measurements", "SAMPLE456 measurements"]}

    plan = understanding.understanding_plan(record, task_mode="evidence_qa", max_queries=2)

    assert [query["query"] for query in plan["subqueries"]] == [
        "SAMPLE123 measurements", "SAMPLE456 measurements"]


def test_query_limit_can_use_the_faithful_question_to_keep_all_identifiers():
    question = "Compare SAMPLE123 and SAMPLE456 measurements."
    record = {"original_question": question, "normalized_question": question, "model": "test",
              "search_queries": ["SAMPLE123 measurements", "SAMPLE456 measurements"]}

    plan = understanding.understanding_plan(record, task_mode="evidence_qa", max_queries=1)

    assert [query["query"] for query in plan["subqueries"]] == [question]
    assert "normalized_question_retained_to_preserve_identifiers" in plan["warnings"]


def test_query_limit_cannot_bypass_query_length_validation():
    question = "Compare SAMPLE123 and SAMPLE456 measurements " + "with calibration details " * 10
    record = {"original_question": question, "normalized_question": question, "model": "test",
              "search_queries": ["SAMPLE123 measurements", "SAMPLE456 measurements"]}

    with pytest.raises(ValueError, match="Invalid understood search plan"):
        understanding.understanding_plan(record, task_mode="evidence_qa", max_queries=1)


def test_query_limit_preserves_order_when_identifiers_already_survive():
    question = "What measurements are reported in SAMPLE123?"
    queries = ["SAMPLE123 measurements", "measurement calibration", "measurement uncertainty"]
    record = {"original_question": question, "normalized_question": question,
              "model": "test", "search_queries": queries}

    plan = understanding.understanding_plan(record, task_mode="evidence_qa", max_queries=2)

    assert [query["query"] for query in plan["subqueries"]] == queries[:2]
    assert plan["warnings"] == []


@pytest.mark.parametrize("queries", [[], "SAMPLE123 measurements", [None], ["measurements"] * 5])
def test_understanding_plan_enforces_the_producer_query_bound(queries):
    question = "What measurements are reported in SAMPLE123?"
    record = {"original_question": question, "normalized_question": question,
              "model": "test", "search_queries": queries}

    with pytest.raises(ValueError, match="Invalid understood search queries"):
        understanding.understanding_plan(record, task_mode="evidence_qa", max_queries=2)


def test_identifier_coverage_for_all_small_query_sets_and_budgets():
    identifiers = {"SAMPLE123", "SAMPLE456"}
    choices = [set(), {"SAMPLE123"}, {"SAMPLE456"}, identifiers]
    question = "Compare SAMPLE123 and SAMPLE456 measurements."
    for size in range(1, 5):
        for coverage in product(choices, repeat=size):
            if set().union(*coverage) != identifiers:
                continue
            queries = [" ".join([*sorted(terms), "measurements"]) for terms in coverage]
            record = {"original_question": question, "normalized_question": question,
                      "model": "test", "search_queries": queries}
            for budget in range(1, size + 1):
                plan = understanding.understanding_plan(
                    record, task_mode="evidence_qa", max_queries=budget)
                emitted = [query["query"] for query in plan["subqueries"]]
                assert 1 <= len(emitted) <= budget
                assert identifiers <= set(" ".join(emitted).replace(".", "").split())
                assert record["search_queries"] == queries
                if set().union(*coverage[:budget]) == identifiers:
                    assert emitted == queries[:budget]
                elif any(set().union(*subset) == identifiers
                         for subset in combinations(coverage, budget)):
                    assert all(query in queries for query in emitted)
                    assert "search_queries_reordered_to_preserve_identifiers" in plan["warnings"]
                else:
                    assert emitted[0] == question
                    assert "normalized_question_retained_to_preserve_identifiers" in plan["warnings"]


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
    assert inventory.inventory_markdown(result, language="de") == inventory.inventory_markdown(result, language="en")
    assert "model error or time limit" in inventory.inventory_markdown(result)
    partial = {"items": [{"name": "Method", "paper_id": "P1"}], "errors": ["timeout"]}
    assert inventory.inventory_markdown(partial, language="de").endswith("Part of the extraction could not be completed.")
