from __future__ import annotations

from typing import Any

from pfempire.retrieval.llm_query_planner import plan_queries_with_ollama
from pfempire.retrieval.query_plan import (
    build_retrieval_queries,
    current_question_from_context,
    source_lookup_query,
)
from pfempire.retrieval.query_plan_schema import validate_query_plan


SUPPORTED_PLANNER_BACKENDS = {"rule_based", "ollama"}


def plan_queries(
    question: str,
    task_mode: str = "evidence_qa",
    planner_backend: str = "rule_based",
    planner_model: str | None = None,
    max_queries: int = 8,
    planner_temperature: float = 0.0,
    planner_timeout: int = 120,
    **kwargs: Any,
) -> dict[str, Any]:
    backend = (planner_backend or "rule_based").strip().lower()
    if backend not in SUPPORTED_PLANNER_BACKENDS:
        raise ValueError(f"Unsupported planner backend: {planner_backend}. Supported: ollama, rule_based")
    if backend == "ollama":
        return ollama_or_fallback_plan(
            question=question,
            task_mode=task_mode,
            task=kwargs.get("task"),
            planner_model=planner_model,
            max_queries=max_queries,
            planner_temperature=planner_temperature,
            planner_timeout=planner_timeout,
        )
    return rule_based_plan(
        question=question,
        task_mode=task_mode,
        max_queries=max_queries,
        requested_backend=backend,
        **kwargs,
    )


def ollama_or_fallback_plan(
    *,
    question: str,
    task_mode: str,
    task: str | None,
    planner_model: str | None,
    max_queries: int,
    planner_temperature: float,
    planner_timeout: int,
) -> dict[str, Any]:
    llm_result = plan_queries_with_ollama(
        question,
        task_mode=task_mode,
        task=task,
        planner_model_name=planner_model,
        max_queries=max_queries,
        temperature=planner_temperature,
        timeout=planner_timeout,
    )
    if llm_result.plan:
        plan = dict(llm_result.plan)
        plan = ensure_minimum_query_diversity(
            plan, question=question, task_mode=task_mode, task=task, max_queries=max_queries
        )
        plan.update(
            {
                "planner_backend_requested": "ollama",
                "planner_backend_used": "ollama",
                "planner_model": llm_result.model,
                "planner_call_performed": llm_result.call_performed,
                "planner_fallback_used": False,
                "planner_raw_output": llm_result.raw_output,
                "planner_validation": llm_result.validation,
                "query_plan_source": "ollama",
            }
        )
        return plan

    fallback = rule_based_plan(
        question=question,
        task_mode=task_mode,
        task=task,
        max_queries=max_queries,
        requested_backend="ollama",
    )
    fallback.update(
        {
            "planner_model": llm_result.model,
            "planner_call_performed": llm_result.call_performed,
            "planner_fallback_used": True,
            "planner_raw_output": llm_result.raw_output,
            "planner_validation": llm_result.validation,
            "planner_error": llm_result.error,
            "query_plan_source": "rule_based_fallback_after_ollama_failure",
        }
    )
    fallback["warnings"] = list(
        dict.fromkeys([*fallback.get("warnings", []), "ollama_planner_failed_rule_based_fallback_used"])
    )
    return fallback


def ensure_minimum_query_diversity(
    plan: dict[str, Any],
    *,
    question: str,
    task_mode: str,
    task: str | None,
    max_queries: int,
    min_queries: int = 3,
) -> dict[str, Any]:
    del min_queries
    query_limit = max(1, max_queries)
    subqueries = list(plan.get("subqueries") or [])[:query_limit]
    original_count = len(subqueries)

    supplement = rule_based_plan(
        question=question,
        task_mode=task_mode,
        max_queries=max_queries,
        requested_backend="rule_based",
        task=task,
    )
    supplement_queries = list(supplement.get("subqueries", []))
    if task == "sources":
        preferred_source_query = source_lookup_query(question).lower()
        supplement_queries.sort(
            key=lambda query: str(query.get("query") or "").strip().lower() != preferred_source_query
        )
    existing = {str(query.get("query") or "").strip().lower() for query in subqueries}
    for query in supplement_queries:
        if len(subqueries) >= query_limit:
            break
        query_text = str(query.get("query") or "").strip()
        if not query_text or query_text.lower() in existing:
            continue
        copy = dict(query)
        copy["rationale"] = "Supplemental diversity query for multi-query retrieval/RRF."
        subqueries.append(copy)
        existing.add(query_text.lower())

    if task == "sources":
        subqueries = anchor_source_lookup_query(
            subqueries, question=question, max_queries=query_limit
        )

    for index, query in enumerate(subqueries, start=1):
        query["query_id"] = f"Q{index:03d}"
    plan["subqueries"] = subqueries
    plan["queries"] = subqueries
    plan["direct_evidence_queries"] = [
        query.get("query", "")
        for query in subqueries
        if query.get("query_type") != "bridge_indirect"
    ]
    plan["indirect_bridge_queries"] = [
        query.get("query", "")
        for query in subqueries
        if query.get("query_type") == "bridge_indirect"
    ]
    if len(subqueries) > original_count:
        warnings = list(plan.get("warnings") or [])
        warnings.append("planner_subqueries_supplemented_for_multi_query_retrieval")
        plan["warnings"] = list(dict.fromkeys(warnings))
    return plan


def anchor_source_lookup_query(
    subqueries: list[dict[str, Any]], *, question: str, max_queries: int
) -> list[dict[str, Any]]:
    """Keep the user's own lookup as the first query when an LLM expands it."""
    anchor = current_question_from_context(question)
    normalized_anchor = anchor.strip().lower()
    if not normalized_anchor:
        return subqueries[: max(1, max_queries)]
    remaining = [
        dict(query)
        for query in subqueries
        if str(query.get("query") or "").strip().lower() != normalized_anchor
    ]
    anchored = [
        {
            "query": anchor,
            "query_type": "original",
            "rationale": "Deterministic anchor from the user's paper-search request.",
            "must_include_terms": [],
            "optional_terms": [],
            "preferred_sections": ["title", "abstract", "results"],
            "excluded_sections": ["references"],
            "label": "paper_lookup",
        },
        *remaining,
    ]
    return anchored[: max(1, max_queries)]


def rule_based_plan(
    *,
    question: str,
    task_mode: str,
    max_queries: int,
    requested_backend: str = "rule_based",
    task: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    retrieval_task = task or task_from_mode(task_mode)
    retrieval_queries = build_retrieval_queries(question, task=retrieval_task, max_queries=max_queries)
    subqueries = [
        {
            "query_id": f"Q{index:03d}",
            "query": query.query,
            "query_type": query_type_from_label(query.label),
            "rationale": f"Rule-based retrieval expansion: {query.label}.",
            "must_include_terms": [],
            "optional_terms": [],
            "preferred_sections": ["results", "discussion", "abstract"],
            "excluded_sections": ["references"],
            "label": query.label,
        }
        for index, query in enumerate(retrieval_queries, start=1)
    ]
    raw_plan = {
        "question": " ".join(str(question or "").split()),
        "task_mode": task_mode,
        "planner_backend": "rule_based",
        "detected_concepts": [],
        "subqueries": subqueries,
        "direct_evidence_queries": [query["query"] for query in subqueries if query["query_type"] != "bridge_indirect"],
        "indirect_bridge_queries": [query["query"] for query in subqueries if query["query_type"] == "bridge_indirect"],
        "warnings": [],
    }
    is_valid, messages, normalized = validate_query_plan(raw_plan, max_queries=max_queries)
    normalized.update(
        {
            "planner_backend_requested": requested_backend,
            "planner_backend_used": "rule_based",
            "planner_model": None,
            "planner_call_performed": False,
            "planner_fallback_used": requested_backend != "rule_based",
            "planner_raw_output": None,
            "planner_validation": {"is_valid": is_valid, "messages": messages},
            "query_plan_source": "rule_based",
        }
    )
    return normalized


def task_from_mode(task_mode: str) -> str:
    mode = str(task_mode or "").lower()
    if "gap" in mode:
        return "gaps"
    if "hypothesis" in mode:
        return "hypothesis"
    if "source" in mode or "dataset" in mode:
        return "sources"
    return "answer"


def query_type_from_label(label: str) -> str:
    clean = str(label or "").lower()
    if clean == "main":
        return "original"
    if "current" in clean:
        return "original"
    if "mechanism" in clean or "cyto" in clean or "epcr" in clean:
        return "mechanism"
    if "phenotype" in clean or "severe" in clean:
        return "phenotype"
    if "method" in clean or "tool" in clean or "dataset" in clean:
        return "method"
    if "gap" in clean or "host" in clean:
        return "bridge_indirect"
    return "concept_expansion"
