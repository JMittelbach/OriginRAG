from __future__ import annotations

from originrag.retrieval.query_plan_schema import validate_query_plan
from originrag.retrieval.query_plan_schema import looks_answer_like


def test_scientific_identifiers_are_not_counted_as_sentences() -> None:
    assert not looks_answer_like("PfRH5 RH5.1 Matrix-M RH5.2 VLP phase 1 phase 2 efficacy")


def test_valid_query_plan_passes_validation() -> None:
    plan = {
        "question": "What evidence links PfEMP1 to cerebral malaria?",
        "task_mode": "evidence_qa",
        "subqueries": [
            {
                "query_id": "Q001",
                "query": "PfEMP1 EPCR cerebral malaria",
                "query_type": "mechanism",
            }
        ],
    }

    is_valid, messages, normalized = validate_query_plan(plan, max_queries=8)

    assert is_valid
    assert messages == ["valid_query_plan"]
    assert normalized["subqueries"][0]["preferred_sections"] == ["results", "discussion", "abstract"]
    assert normalized["queries"] == normalized["subqueries"]


def test_answer_like_query_plan_is_invalid() -> None:
    plan = {
        "question": "What evidence links PfEMP1 to cerebral malaria?",
        "task_mode": "evidence_qa",
        "subqueries": [
            {
                "query_id": "Q001",
                "query": "The evidence shows that PfEMP1 causes cerebral malaria. Therefore it is proven.",
            }
        ],
    }

    is_valid, messages, _normalized = validate_query_plan(plan, max_queries=8)

    assert not is_valid
    assert "subquery_1_answer_like_query" in messages


def test_two_sentence_literature_query_is_not_misclassified_as_an_answer() -> None:
    query = (
        "How does the memory T-cell repertoire change from early childhood to "
        "adolescence? Please provide literature."
    )

    assert not looks_answer_like(query)
