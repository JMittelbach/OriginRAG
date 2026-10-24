import json

import pytest

from originrag.analysis import question_understanding as understanding
from originrag.analysis.ollama_client import OllamaResult


def prepare(monkeypatch, question, queries, *, normalized=None):
    calls = []
    payload = {"normalized_question": normalized or question, "language": "en", "intent": "question",
               "unclear": False, "search_queries": queries}

    def call(*args, **kwargs):
        calls.append((args, kwargs))
        return OllamaResult(text=json.dumps(payload), model="test-model", base_url="http://localhost",
                            call_performed=True, error=None)

    monkeypatch.setattr(understanding, "call_ollama_generate", call)
    result = understanding.understand_question(question)
    assert len(calls) == 1
    return result


@pytest.mark.parametrize("question", [
    "Is the comparison reliable even if device type and operator are perfectly confounded?",
    "If device type and operator are perfectly confounded, is the comparison reliable?",
    "Is the comparison reliable when device type and operator are perfectly confounded?",
    "Is the comparison reliable despite perfect confounding between device type and operator?",
    "Is the comparison, even if device type and operator are perfectly confounded, reliable?",
    "Is the comparison reliable if available devices share a calibration source?",
    "Is the comparison reliable if you can only measure repeated observations?",
])
def test_missing_substantive_condition_is_retained_verbatim(monkeypatch, question):
    queries = ["comparison reliability", "measurement quality"]
    record = prepare(monkeypatch, question, queries)
    assert record["status"] == "ready"
    assert record["normalized_question"] == question
    assert record["search_queries"] == [*queries, question]
    assert record["warnings"] == ["conditional_context_retained_from_normalized_question"]


@pytest.mark.parametrize("max_queries", [1, 2, 3, 4])
def test_recovered_condition_survives_downstream_query_limit(monkeypatch, max_queries):
    question = "Is the comparison reliable even if device type and operator are perfectly confounded?"
    queries = ["comparison reliability", "measurement quality", "measurement comparison", "method accuracy"]
    record = prepare(monkeypatch, question, queries)
    assert len(record["search_queries"]) == 4
    assert record["search_queries"][-1] == question
    plan = understanding.understanding_plan(record, task_mode="evidence_qa", max_queries=max_queries)
    assert len(plan["subqueries"]) <= max_queries
    assert question in [query["query"] for query in plan["subqueries"]]
    assert all(len(query["query"]) <= 220 for query in plan["subqueries"])


def test_complete_condition_terms_need_not_be_repeated_as_full_sentence(monkeypatch):
    question = "Is the comparison reliable even if device type and operator are confounded?"
    queries = ["comparison reliability device type operator confounding"]
    record = prepare(monkeypatch, question, queries)
    assert record["status"] == "ready"
    assert record["search_queries"] == queries
    assert record["warnings"] == []


@pytest.mark.parametrize("question", [
    "If possible, explain measurement reliability.",
    "If you can, explain measurement reliability.",
    "If you think it would help, explain measurement reliability.",
    "If I understand correctly, how is measurement reliability assessed?",
    "When you have time, explain measurement reliability.",
    "When convenient, explain measurement reliability.",
    "Explain measurement reliability, if that helps.",
    "When were these measurement methods first developed?",
    "Explain measurement reliability without adding examples.",
])
def test_courtesy_and_nonconditional_questions_do_not_add_queries(monkeypatch, question):
    queries = ["measurement reliability without examples"]
    record = prepare(monkeypatch, question, queries)
    assert record["status"] == "ready"
    assert record["search_queries"] == queries
    assert record["warnings"] == []


def test_lost_negation_in_condition_is_kept(monkeypatch):
    question = "Is the estimate valid if repeated measurements are not independent?"
    queries = ["repeated measurements independent estimate validity"]
    record = prepare(monkeypatch, question, queries)
    assert record["status"] == "ready"
    assert record["search_queries"][-1] == question
    assert "not independent" in record["search_queries"][-1]


@pytest.mark.parametrize("query", [
    "repeated measurements independent estimate validity without outliers",
    "not calibrated repeated measurements independent estimate validity",
    "repeated measurements independent estimate validity not biased",
])
def test_unrelated_negation_cannot_mask_lost_conditional_negation(monkeypatch, query):
    question = "Is the estimate valid if repeated measurements are not independent?"
    record = prepare(monkeypatch, question, [query])
    assert record["status"] == "ready"
    assert record["search_queries"] == [query, question]
    assert record["warnings"] == ["conditional_context_retained_from_normalized_question"]


def test_negation_scope_does_not_extend_into_another_query(monkeypatch):
    question = "Is the estimate valid if repeated measurements are not independent?"
    queries = ["estimate validity not", "independent repeated measurements"]
    record = prepare(monkeypatch, question, queries)
    assert record["status"] == "ready"
    assert record["search_queries"] == [*queries, question]


@pytest.mark.parametrize("query", [
    "repeated measurements not independent estimate validity",
    "repeated measurements not statistically independent estimate validity without outliers",
])
def test_local_condition_negation_is_recognized_without_extra_query(monkeypatch, query):
    question = "Is the estimate valid if repeated measurements are not independent?"
    record = prepare(monkeypatch, question, [query])
    assert record["status"] == "ready"
    assert record["search_queries"] == [query]
    assert record["warnings"] == []


def test_neither_nor_condition_preserves_both_local_negations(monkeypatch):
    question = "Is the estimate valid if measurements are neither reliable nor independent?"
    queries = ["estimate validity measurements neither reliable independent"]
    record = prepare(monkeypatch, question, queries)
    assert record["status"] == "ready"
    assert record["search_queries"] == [*queries, question]


def test_only_verbatim_bounded_sentence_is_added_for_long_question(monkeypatch):
    condition = "Is the estimate reliable even if all observations share a single calibration source?"
    question = ("Explain measurement reliability and the relevant methodological literature in detail, "
                "including uncertainty, assumptions, limitations, reporting and comparisons between approaches. "
                + condition)
    assert len(question) > 220
    record = prepare(monkeypatch, question, ["measurement reliability"])
    assert record["status"] == "ready"
    assert record["search_queries"][-1] == condition
    assert all(len(query) <= 220 for query in record["search_queries"])


def test_unfittable_condition_is_not_truncated_or_rewritten(monkeypatch):
    question = ("Is the estimate reliable even if " + "every reading depends on the same calibration source " * 6
                + "and no independently calibrated observations exist?")
    queries = ["estimate reliability no independent calibration"]
    record = prepare(monkeypatch, question, queries)
    assert record["status"] == "ready"
    assert record["search_queries"] == queries
    assert record["warnings"] == []


def test_changed_normalized_negation_still_fails_before_recovery(monkeypatch):
    question = "Is the estimate valid if repeated measurements are not independent?"
    normalized = "Is the estimate valid if repeated measurements are independent?"
    record = prepare(monkeypatch, question, ["estimate validity"], normalized=normalized)
    assert record["status"] == "needs_clarification"
    assert record["error"] == "normalization_lost_negation"


def test_changed_numeric_bound_still_fails_before_recovery(monkeypatch):
    question = "Is the estimate valid if at least 5 devices share a calibration source?"
    normalized = "Is the estimate valid if at most 5 devices share a calibration source?"
    record = prepare(monkeypatch, question, ["estimate validity 5 devices"], normalized=normalized)
    assert record["status"] == "needs_clarification"
    assert record["error"] == "normalization_changed_numeric_comparison"


def test_recovery_does_not_mask_lost_identifier(monkeypatch):
    question = "Is the estimate for KIT42 valid if devices share a single calibration source?"
    record = prepare(monkeypatch, question, ["estimate validity"])
    assert record["status"] == "needs_clarification"
    assert record["error"] == "search_queries_lost_identifiers_or_numbers"


@pytest.mark.parametrize("addition", ["100 devices", "KIT99 devices"])
def test_recovery_does_not_copy_invented_normalized_identifiers_or_numbers(monkeypatch, addition):
    question = "Is the estimate valid if devices share a calibration source?"
    normalized = f"Is the estimate valid if {addition} share a calibration source?"
    queries = ["estimate validity"]
    record = prepare(monkeypatch, question, queries, normalized=normalized)
    assert record["search_queries"] == queries
    assert record["warnings"] == []


def test_recovery_does_not_mask_mutated_query_numbers(monkeypatch):
    question = "Is the estimate valid if 5 devices share a calibration source?"
    record = prepare(monkeypatch, question, ["estimate validity 6 devices"])
    assert record["status"] == "needs_clarification"
    assert record["error"] == "search_queries_lost_identifiers_or_numbers"


def test_required_identifier_and_condition_must_both_survive_smaller_plan(monkeypatch):
    condition = "Is the estimate reliable even if all observations share a single calibration source?"
    question = ("Discuss KIT42 measurement reliability and the relevant methodological literature in detail, "
                "including uncertainty, assumptions, limitations, reporting and comparisons between approaches. "
                + condition)
    record = prepare(monkeypatch, question, ["KIT42 measurement reliability"])
    assert record["status"] == "ready"
    plan = understanding.understanding_plan(record, task_mode="evidence_qa", max_queries=2)
    assert [query["query"] for query in plan["subqueries"]] == ["KIT42 measurement reliability", condition]
    with pytest.raises(ValueError):
        understanding.understanding_plan(record, task_mode="evidence_qa", max_queries=1)
