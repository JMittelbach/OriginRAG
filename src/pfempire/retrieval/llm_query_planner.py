from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pfempire.analysis.ollama_client import call_ollama_generate, planner_model
from pfempire.retrieval.query_plan_schema import validate_query_plan


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROMPT_PATH = REPO_ROOT / "config/prompts/query_planner_ollama.md"


@dataclass
class LLMPlannerResult:
    plan: dict[str, Any] | None
    raw_output: str | None
    validation: dict[str, Any]
    model: str
    call_performed: bool
    error: str | None = None


def plan_queries_with_ollama(
    question: str,
    *,
    task_mode: str = "evidence_qa",
    task: str | None = None,
    planner_model_name: str | None = None,
    max_queries: int = 8,
    temperature: float = 0.0,
    timeout: int = 120,
    prompt_path: Path = DEFAULT_PROMPT_PATH,
) -> LLMPlannerResult:
    model = planner_model_name or planner_model()
    prompt = build_planner_prompt(
        question=question,
        task_mode=task_mode,
        task=task,
        max_queries=max_queries,
        prompt_path=prompt_path,
    )
    result = call_ollama_generate(
        prompt,
        model=model,
        temperature=temperature,
        timeout=timeout,
        num_predict=4096,
    )
    if result.error or not result.text:
        retry = call_ollama_generate(
            prompt,
            model=model,
            temperature=temperature,
            timeout=timeout,
            num_predict=8192,
        )
        if retry.text or not result.text:
            result = retry
    if result.error or not result.text:
        return LLMPlannerResult(
            plan=None,
            raw_output=result.text,
            validation={"is_valid": False, "messages": [result.error or "empty_ollama_output"]},
            model=result.model,
            call_performed=result.call_performed,
            error=result.error or "empty_ollama_output",
        )

    try:
        parsed = parse_json_object(result.text)
    except ValueError as exc:
        repaired = repair_planner_json(
            raw_output=result.text,
            error=str(exc),
            model=model,
            temperature=temperature,
            timeout=timeout,
        )
        if repaired is None:
            return LLMPlannerResult(
                plan=None,
                raw_output=result.text,
                validation={"is_valid": False, "messages": [str(exc)]},
                model=result.model,
                call_performed=result.call_performed,
                error=str(exc),
            )
        result = repaired
        parsed = parse_json_object(result.text or "")

    is_valid, messages, normalized = validate_query_plan(parsed, max_queries=max_queries)
    return LLMPlannerResult(
        plan=normalized if is_valid else None,
        raw_output=result.text,
        validation={"is_valid": is_valid, "messages": messages},
        model=result.model,
        call_performed=result.call_performed,
        error=None if is_valid else "; ".join(messages),
    )


def repair_planner_json(
    *,
    raw_output: str,
    error: str,
    model: str,
    temperature: float,
    timeout: int,
) -> object | None:
    repair_prompt = f"""Return valid JSON only. Do not explain anything.
The previous retrieval plan JSON was invalid:
{error}

Fix this into one valid JSON object with the same schema:
{raw_output}
"""
    repaired = call_ollama_generate(
        repair_prompt,
        model=model,
        temperature=temperature,
        timeout=timeout,
        num_predict=4096,
    )
    if repaired.error or not repaired.text:
        return None
    try:
        parse_json_object(repaired.text)
    except ValueError:
        return None
    return repaired


def build_planner_prompt(
    question: str,
    task_mode: str,
    max_queries: int,
    prompt_path: Path = DEFAULT_PROMPT_PATH,
    *,
    task: str | None = None,
) -> str:
    template = Path(prompt_path).read_text(encoding="utf-8")
    return (
        template.replace("{task_mode}", task_mode)
        .replace("{task}", str(task or "auto"))
        .replace("{max_queries}", str(max_queries))
        .replace("{question}", question)
    )


def parse_json_object(text: str) -> dict[str, Any]:
    clean = strip_code_fence(text)
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
        if not match:
            raise ValueError("planner_output_not_json") from None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ValueError(f"planner_output_invalid_json: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("planner_output_not_json_object")
    return parsed


def strip_code_fence(text: str) -> str:
    clean = str(text or "").strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
    return clean.strip()
