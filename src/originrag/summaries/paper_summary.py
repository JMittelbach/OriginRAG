from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from originrag.analysis.ollama_client import call_ollama_generate
from originrag.retrieval.llm_query_planner import parse_json_object


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROMPT_PATH = REPO_ROOT / "config/prompts/structured_paper_summary.md"

SUMMARY_FIELDS = [
    "paper_id",
    "title",
    "year",
    "short_summary",
    "background",
    "methods",
    "datasets_or_cohorts",
    "key_findings",
    "limitations",
    "host_angle",
    "parasite_angle",
    "immune_angle",
    "omics_angle",
    "computational_angle",
    "disease_context",
    "relevance_to_pfempire",
    "source_chunks",
]


@dataclass
class PaperSummaryResult:
    summary: dict[str, Any] | None
    raw_output: str | None
    error: str | None
    model: str | None


def summarize_paper_with_ollama(
    paper: dict[str, str],
    chunks: list[dict],
    *,
    model: str,
    temperature: float = 0.0,
    timeout: int = 180,
    max_chunks: int = 8,
) -> PaperSummaryResult:
    selected = select_summary_chunks(chunks, max_chunks=max_chunks)
    prompt = build_summary_prompt(paper, selected)
    result = call_ollama_generate(
        prompt,
        model=model,
        temperature=temperature,
        timeout=timeout,
        num_predict=1800,
    )
    if result.error or not result.text:
        return PaperSummaryResult(None, result.text, result.error or "empty_summary_output", result.model)
    try:
        parsed = parse_json_object(result.text)
    except ValueError as exc:
        return PaperSummaryResult(None, result.text, str(exc), result.model)
    summary = normalize_summary(parsed, paper, selected)
    return PaperSummaryResult(summary, result.text, None, result.model)


def build_summary_prompt(paper: dict[str, str], chunks: list[dict]) -> str:
    template = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")
    evidence = []
    for chunk in chunks:
        evidence.append(
            "\n".join(
                [
                    f"Chunk ID: {chunk.get('chunk_id', '')}",
                    f"Section: {chunk.get('section', 'unknown')}",
                    f"Pages: {page_range(chunk)}",
                    "Text:",
                    compact(str(chunk.get("text") or ""), 1800),
                ]
            )
        )
    return f"""{template}

Return JSON only. Use this exact top-level shape:
{{
  "paper_id": "...",
  "title": "...",
  "year": "...",
  "short_summary": "...",
  "background": "...",
  "methods": "...",
  "datasets_or_cohorts": [],
  "key_findings": [],
  "limitations": [],
  "host_angle": "...",
  "parasite_angle": "...",
  "immune_angle": "...",
  "omics_angle": "...",
  "computational_angle": "...",
  "disease_context": "...",
  "relevance_to_pfempire": "...",
  "source_chunks": []
}}

Paper metadata:
- paper_id: {paper.get('paper_id', '')}
- title: {paper.get('title', '')}
- year: {paper.get('year', '')}
- doi: {paper.get('doi', '')}

Evidence chunks:

{chr(10).join(evidence)}
"""


def normalize_summary(parsed: dict[str, Any], paper: dict[str, str], chunks: list[dict]) -> dict[str, Any]:
    summary = {field: parsed.get(field, "") for field in SUMMARY_FIELDS}
    summary["paper_id"] = str(summary.get("paper_id") or paper.get("paper_id") or "")
    summary["title"] = str(summary.get("title") or paper.get("title") or "")
    summary["year"] = str(summary.get("year") or paper.get("year") or "")
    for field in ["datasets_or_cohorts", "key_findings", "limitations", "source_chunks"]:
        value = summary.get(field)
        if isinstance(value, str):
            summary[field] = [value] if value.strip() else []
        elif isinstance(value, list):
            summary[field] = [str(item) for item in value if str(item).strip()]
        else:
            summary[field] = []
    known_chunks = {str(chunk.get("chunk_id")) for chunk in chunks if chunk.get("chunk_id")}
    summary["source_chunks"] = [chunk for chunk in summary["source_chunks"] if chunk in known_chunks] or sorted(known_chunks)
    return summary


def select_summary_chunks(chunks: list[dict], max_chunks: int = 8) -> list[dict]:
    preferred = {"abstract": 0, "results": 1, "discussion": 2, "methods": 3, "data_availability": 4}
    ranked = sorted(
        chunks,
        key=lambda chunk: (
            preferred.get(str(chunk.get("section") or "unknown"), 9),
            int(chunk.get("chunk_index") or 9999),
        ),
    )
    return ranked[: max(1, max_chunks)]


def page_range(chunk: dict) -> str:
    start = chunk.get("page_start")
    end = chunk.get("page_end")
    if start in ("", None) and end in ("", None):
        return "?"
    return str(start) if start == end else f"{start}-{end}"


def compact(text: str, max_chars: int) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    return clean if len(clean) <= max_chars else clean[: max_chars - 20].rstrip() + " [truncated]"


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
