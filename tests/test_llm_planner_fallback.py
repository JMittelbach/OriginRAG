from __future__ import annotations

from originrag.analysis.ollama_client import OllamaResult
import originrag.retrieval.llm_query_planner as llm_query_planner
from originrag.retrieval.query_planner import plan_queries


def test_invalid_ollama_json_triggers_rule_based_fallback(monkeypatch) -> None:
    calls = []

    def fake_call(*args, **kwargs) -> OllamaResult:
        calls.append((args, kwargs))
        return OllamaResult(
            text="not json",
            model="qwen3:30b",
            base_url="http://localhost:11434",
            call_performed=True,
            error=None,
        )

    monkeypatch.setattr(llm_query_planner, "call_ollama_generate", fake_call)

    plan = plan_queries(
        "What evidence links PfEMP1 to cerebral malaria?",
        planner_backend="ollama",
        planner_model="qwen3:30b",
        max_queries=4,
    )

    assert calls
    assert plan["planner_backend_requested"] == "ollama"
    assert plan["planner_backend_used"] == "rule_based"
    assert plan["planner_call_performed"] is True
    assert plan["planner_fallback_used"] is True
    assert plan["subqueries"]


def test_planner_prompt_preserves_placeholder_like_user_text() -> None:
    question = "Compare the literal labels {task}, {task_mode}, and {max_queries}."

    prompt = llm_query_planner.build_planner_prompt(
        question,
        task_mode="evidence_qa",
        max_queries=4,
        task="answer",
    )

    assert question in prompt


def test_source_planner_supplement_prioritizes_paper_lookup(monkeypatch) -> None:
    prompts = []

    def fake_call(prompt, **kwargs) -> OllamaResult:
        prompts.append(prompt)
        return OllamaResult(
            text=(
                '{"question":"Which paper had the Kenya cohort with over 700 patients?",'
                '"task_mode":"evidence_qa","subqueries":['
                '{"query_id":"Q001","query":"Kenya cohort malaria over 700 patients"}]}'
            ),
            model="qwen3:30b",
            base_url="http://localhost:11434",
            call_performed=True,
            error=None,
        )

    monkeypatch.setattr(llm_query_planner, "call_ollama_generate", fake_call)

    plan = plan_queries(
        "Which paper had the Kenya cohort with over 700 patients?",
        planner_backend="ollama",
        task="sources",
        max_queries=3,
    )

    assert plan["subqueries"][0]["label"] == "paper_lookup"
    queries = {str(query.get("query") or "").lower() for query in plan["subqueries"]}
    assert "which paper had the kenya cohort with over 700 patients?" in queries
    assert not any("dataset cohort accession" in query for query in queries)
    assert "Resolved user-facing task:\nsources" in prompts[0]
    assert "local paper metadata and local full-text chunks" in prompts[0]
    assert "The original user question is preserved separately for answer generation" in prompts[0]


def test_ollama_plan_never_exceeds_configured_query_limit(monkeypatch) -> None:
    def fake_call(*args, **kwargs) -> OllamaResult:
        return OllamaResult(
            text=(
                '{"question":"What is associated with VAR2CSA?","task_mode":"evidence_qa",'
                '"subqueries":['
                '{"query_id":"Q001","query":"VAR2CSA associations","query_type":"original"},'
                '{"query_id":"Q002","query":"VAR2CSA placental malaria","query_type":"phenotype"},'
                '{"query_id":"Q003","query":"VAR2CSA adhesion mechanism","query_type":"mechanism"}]}'
            ),
            model="qwen3:8b",
            base_url="http://localhost:11434",
            call_performed=True,
            error=None,
        )

    monkeypatch.setattr(llm_query_planner, "call_ollama_generate", fake_call)

    plan = plan_queries(
        "What is associated with VAR2CSA?",
        planner_backend="ollama",
        task="answer",
        max_queries=3,
    )

    assert len(plan["subqueries"]) == 3
    assert [query["query_id"] for query in plan["subqueries"]] == ["Q001", "Q002", "Q003"]
