from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from pfempire.retrieval.keyword import normalize_text


ALLOWED_QUERY_TYPES = {
    "original",
    "exact_terms",
    "concept_expansion",
    "mechanism",
    "phenotype",
    "method",
    "bridge_direct",
    "bridge_indirect",
}
DEFAULT_PREFERRED_SECTIONS = ["results", "discussion", "abstract"]
DEFAULT_EXCLUDED_SECTIONS = ["references"]
ANSWER_LIKE_KEYS = {"answer", "final_answer", "conclusion", "summary_answer", "hypothesis"}
ANSWER_LIKE_PHRASES = {
    "in conclusion",
    "the answer is",
    "therefore",
    "this demonstrates",
    "this proves",
    "the evidence shows",
    "we can conclude",
}
MAX_QUERY_CHARS = 220
MAX_QUERY_SENTENCES = 2


def validate_query_plan(plan: dict, max_queries: int = 8) -> tuple[bool, list[str], dict]:
    messages: list[str] = []
    normalized: dict[str, Any] = deepcopy(plan) if isinstance(plan, dict) else {}
    if not isinstance(plan, dict):
        return False, ["query_plan_not_json_object"], empty_plan(max_queries=max_queries)

    if any(key in plan and str(plan.get(key) or "").strip() for key in ANSWER_LIKE_KEYS):
        messages.append("planner_output_contains_answer_like_key")

    question = " ".join(str(plan.get("question") or "").split())
    task_mode = " ".join(str(plan.get("task_mode") or "evidence_qa").split()) or "evidence_qa"
    subqueries = plan.get("subqueries", plan.get("queries"))

    if not question:
        messages.append("missing_question")
    if not isinstance(subqueries, list) or not subqueries:
        messages.append("missing_or_empty_subqueries")
        subqueries = []

    normalized_queries: list[dict[str, Any]] = []
    for index, raw_query in enumerate(subqueries, start=1):
        if not isinstance(raw_query, dict):
            messages.append(f"subquery_{index}_not_object")
            continue
        normalized_query = normalize_subquery(raw_query, index)
        query_text = normalized_query["query"]
        if not normalized_query["query_id"]:
            messages.append(f"subquery_{index}_missing_query_id")
        if not query_text:
            messages.append(f"subquery_{index}_empty_query")
        if looks_answer_like(query_text):
            messages.append(f"subquery_{index}_answer_like_query")
        normalized_queries.append(normalized_query)

    if len(normalized_queries) > max_queries:
        messages.append(f"subqueries_truncated_to_{max_queries}")
        normalized_queries = normalized_queries[:max_queries]

    normalized.update(
        {
            "question": question,
            "task_mode": task_mode,
            "planner_backend": str(plan.get("planner_backend") or "rule_based"),
            "detected_concepts": normalize_list_of_dicts(plan.get("detected_concepts")),
            "subqueries": normalized_queries,
            "queries": normalized_queries,
            "direct_evidence_queries": normalize_list(plan.get("direct_evidence_queries")),
            "indirect_bridge_queries": normalize_list(plan.get("indirect_bridge_queries")),
            "warnings": normalize_list(plan.get("warnings")),
        }
    )
    if messages:
        normalized["warnings"] = unique_strings([*normalized["warnings"], *messages])

    critical_prefixes = ("missing_", "subquery_", "planner_output_contains_answer_like_key")
    is_valid = bool(normalized_queries) and not any(message.startswith(critical_prefixes) for message in messages)
    return is_valid, messages or ["valid_query_plan"], normalized


def normalize_subquery(raw_query: dict, index: int) -> dict[str, Any]:
    query_type = str(raw_query.get("query_type") or "original").strip() or "original"
    if query_type not in ALLOWED_QUERY_TYPES:
        query_type = "concept_expansion"
    return {
        "query_id": str(raw_query.get("query_id") or f"Q{index:03d}").strip(),
        "query": " ".join(str(raw_query.get("query") or "").split()),
        "query_type": query_type,
        "label": compact_text(raw_query.get("label"), max_chars=80),
        "rationale": compact_text(raw_query.get("rationale"), max_chars=240),
        "must_include_terms": normalize_list(raw_query.get("must_include_terms")),
        "optional_terms": normalize_list(raw_query.get("optional_terms")),
        "preferred_sections": normalize_list(raw_query.get("preferred_sections")) or DEFAULT_PREFERRED_SECTIONS,
        "excluded_sections": normalize_list(raw_query.get("excluded_sections")) or DEFAULT_EXCLUDED_SECTIONS,
        "filters": raw_query.get("filters") if isinstance(raw_query.get("filters"), dict) else {},
    }


def looks_answer_like(text: str) -> bool:
    clean = " ".join(str(text or "").split())
    if len(clean) > MAX_QUERY_CHARS:
        return True


    sentence_count = len(re.findall(r"[.;](?=\s|$)", clean))
    if sentence_count > MAX_QUERY_SENTENCES:
        return True
    normalized = normalize_text(clean)
    return any(phrase in normalized for phrase in ANSWER_LIKE_PHRASES)


def compact_text(value: object, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:max_chars].strip()


def normalize_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [item.strip() for item in value.split(";")]
    elif isinstance(value, list):
        items = [str(item or "").strip() for item in value]
    else:
        return []
    return unique_strings(item for item in items if item)


def normalize_list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def unique_strings(values: object) -> list[str]:
    kept = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            kept.append(text)
    return kept


def empty_plan(max_queries: int = 8) -> dict[str, Any]:
    return {
        "question": "",
        "task_mode": "evidence_qa",
        "planner_backend": "rule_based",
        "detected_concepts": [],
        "subqueries": [],
        "queries": [],
        "direct_evidence_queries": [],
        "indirect_bridge_queries": [],
        "warnings": [f"empty_plan_max_queries_{max_queries}"],
    }
