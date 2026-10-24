import json

import pytest

from originrag.analysis import question_understanding as understanding
from originrag.analysis.ollama_client import OllamaResult


def prepare(monkeypatch, question, queries, *, normalized=None):
    payload = {"normalized_question": normalized or question, "language": "en", "intent": "question",
               "unclear": False, "search_queries": queries}
    monkeypatch.setattr(understanding, "call_ollama_generate", lambda *args, **kwargs: OllamaResult(
        text=json.dumps(payload), model="test-model", base_url="http://localhost",
        call_performed=True, error=None))
    return understanding.understand_question(question)


@pytest.mark.parametrize("question,queries", [
    ("With 3 sensors and 3,000 measurements per sensor, are 9,000 measurements independent replicates?",
     ["sensors independent replicates", "measurement pseudoreplication"]),
    ("A calibration test has p=0.01 and an effect of 0.1. Does this mean 99% certainty?",
     ["calibration p=0.01 effect 0.1 interpretation", "statistical versus practical significance"]),
    ("A sensor study reports p=0.01 and an effect of 0.1. Does this establish a meaningful effect with "
     "99% certainty? Explain using the methods papers in our corpus, including their assumptions, "
     "limitations and distinctions between different kinds of uncertainty.",
     ["sensor p=0.01 effect 0.1 interpretation", "uncertainty interpretation", "statistical significance",
      "practical significance"]),
])
def test_omitted_example_numbers_retained_as_faithful_search_context(monkeypatch, question, queries):
    record = prepare(monkeypatch, question, queries)
    assert record["status"] == "ready"
    assert record["normalized_question"] == question
    assert record["warnings"] == ["numeric_context_retained_from_normalized_question"]
    assert len(record["search_queries"]) <= 4
    assert record["search_queries"][0] == queries[0]
    assert record["search_queries"][-1] in question
    assert all(len(query) <= 220 for query in record["search_queries"])
    assert understanding.preserves_protected_terms(understanding.protected_terms(question),
                                                   " ".join(record["search_queries"]))
    plan = understanding.understanding_plan(record, task_mode="evidence_qa", max_queries=3)
    assert understanding.preserves_protected_terms(understanding.protected_terms(question),
                                                   " ".join(q["query"] for q in plan["subqueries"]))
    assert "numeric_context_retained_from_normalized_question" in plan["warnings"]


@pytest.mark.parametrize("question,queries", [
    ("Which studies in GSE12345 report an effect of 2?", ["studies effect size"]),
    ("What does an effect of -2 mean?", ["effect of 2 interpretation"]),
    ("What do 3 sensors and 9,000 measurements imply?", ["3 sensors 900 measurements interpretation"]),
    ("How should 99% certainty and p=0.01 be interpreted?", ["95% certainty p=0.01 interpretation"]),
])
def test_recovery_does_not_mask_changed_numbers_or_missing_identifiers(monkeypatch, question, queries):
    record = prepare(monkeypatch, question, queries)
    assert record["status"] == "needs_clarification"
    assert record["error"] == "search_queries_lost_identifiers_or_numbers"


def test_numeric_context_recovery_does_not_truncate_long_constraints(monkeypatch):
    question = "Explain how " + "measurements and their uncertainty " * 9 + "at least 7 times matter?"
    record = prepare(monkeypatch, question, ["measurement uncertainty"])
    assert record["status"] == "needs_clarification"


def test_recovery_does_not_introduce_numbers_invented_during_normalization(monkeypatch):
    question = "What do 3 independent sensor measurements mean?"
    normalized = "What do 3 independent sensor measurements mean compared with 100 measurements?"
    record = prepare(monkeypatch, question, ["sensor measurement independence"], normalized=normalized)
    assert record["status"] == "needs_clarification"
    assert record["error"] == "search_queries_lost_identifiers_or_numbers"


def test_unchanged_complete_queries_do_not_add_context(monkeypatch):
    question = "What does an effect of 0.1 mean?"
    record = prepare(monkeypatch, question, ["effect of 0.1 interpretation"])
    assert record["status"] == "ready"
    assert record["search_queries"] == ["effect of 0.1 interpretation"]
    assert record["warnings"] == []
