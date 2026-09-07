from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import yaml

from originrag.analysis.evidence_pack import build_evidence_pack, format_evidence_pack_markdown
from originrag.analysis.evidence_quality import GRADE_ORDER, score_evidence_pack
from originrag.retrieval.fusion import reciprocal_rank_fusion
from originrag.retrieval.keyword import normalize_text, search_chunks
from originrag.retrieval.paper_retrieval import search_papers
from originrag.retrieval.query_planner import plan_queries
from originrag.task_modes import get_task_mode


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ADAPTIVE_CONFIG = REPO_ROOT / "config/adaptive_retrieval.yaml"


def adaptive_retrieve(
    question: str,
    task_mode: str,
    chunks_dir: Path,
    papers_tsv: Path,
    config_path: Path = DEFAULT_ADAPTIVE_CONFIG,
    retriever: str = "keyword",
    max_chunks: int | None = None,
    max_per_paper: int | None = None,
    expected_sources: list[str] | None = None,
) -> dict[str, Any]:
    if retriever not in {"keyword", "hybrid"}:
        raise ValueError("Adaptive Retrieval v1 currently supports retriever='keyword' or retriever='hybrid'.")
    requested_retriever = retriever
    effective_retriever = "keyword"
    retriever_fallback_reason = (
        "semantic_backend_unavailable" if requested_retriever == "hybrid" else None
    )
    config = load_adaptive_config(config_path)
    mode = get_task_mode(task_mode)
    max_chunks = max_chunks or int(mode.get("max_chunks") or config["max_chunks"])
    max_per_paper = max_per_paper or int(mode.get("max_per_paper") or config["max_per_paper"])
    max_subqueries = int(config.get("max_subqueries") or 8)
    query_plan = plan_queries(question, task_mode=task_mode, max_queries=max_subqueries)

    ranked_lists, raw_candidates, paper_candidates, per_subquery_results = retrieve_for_plan(
        query_plan,
        chunks_dir=Path(chunks_dir),
        papers_tsv=Path(papers_tsv),
        task_mode=task_mode,
        mode=mode,
        config=config,
        max_per_paper=max_per_paper,
    )
    fused = reciprocal_rank_fusion(ranked_lists, k=int(config["fusion"]["rrf_k"]))
    diverse = enforce_diversity(dedupe_near_identical(fused), max_chunks=max_chunks, max_per_paper=max_per_paper)
    pack = build_evidence_pack(question, diverse, max_chunks=max_chunks)
    quality = score_evidence_pack(pack, expected_sources=expected_sources)
    correction_actions: list[str] = []

    if should_retry(quality, config):
        retry_result = corrective_retry(
            question=question,
            task_mode=task_mode,
            chunks_dir=Path(chunks_dir),
            papers_tsv=Path(papers_tsv),
            config=config,
            mode=mode,
            current_quality=quality,
            max_chunks=max_chunks,
            max_per_paper=max_per_paper,
            expected_sources=expected_sources,
        )
        correction_actions.extend(retry_result["correction_actions"])
        if GRADE_ORDER[retry_result["evidence_quality"]["grade"]] > GRADE_ORDER[quality["grade"]]:
            query_plan = retry_result["query_plan"]
            raw_candidates = retry_result["raw_candidates"]
            per_subquery_results = retry_result["per_subquery_results"]
            fused = retry_result["fused_candidates"]
            diverse = retry_result["diverse_candidates"]
            pack = retry_result["evidence_pack"]
            quality = retry_result["evidence_quality"]

    return {
        "question": question,
        "task_mode": task_mode,
        "query_plan": query_plan,
        "retriever": effective_retriever,
        "retriever_requested": requested_retriever,
        "retriever_fallback_reason": retriever_fallback_reason,
        "correction_actions": correction_actions,
        "evidence_pack": pack,
        "evidence_quality": quality,
        "diagnostics": {
            "n_planned_queries": len(query_plan.get("queries", [])),
            "n_candidate_papers": len(paper_candidates),
            "n_raw_candidates": len(raw_candidates),
            "n_after_fusion": len(fused),
            "n_after_diversity": len(diverse),
            "n_unique_papers": pack.get("n_unique_papers", 0),
        },
        "retrieval_candidates": diverse,
        "raw_candidates": raw_candidates,
        "per_subquery_results": per_subquery_results,
        "expected_sources": expected_sources or [],
    }


def retrieve_for_plan(
    query_plan: dict,
    *,
    chunks_dir: Path,
    papers_tsv: Path,
    task_mode: str,
    mode: dict,
    config: dict,
    max_per_paper: int,
) -> tuple[list[list[dict]], list[dict], list[dict], list[dict]]:
    ranked_lists = []
    raw_candidates = []
    per_subquery_results = []
    all_papers = {}
    for query in query_plan.get("queries", []):
        query_text = query["query"]
        paper_hits = search_papers(
            query_text,
            papers_tsv=papers_tsv,
            limit=int(config["max_candidate_papers"]),
            task_mode=task_mode,
        )
        for paper in paper_hits:
            all_papers[paper["paper_id"]] = paper
        chunk_hits = search_chunks(
            query=query_text,
            chunks_dir=chunks_dir,
            papers_tsv=papers_tsv,
            limit=int(config["max_candidate_chunks_per_query"]),
            max_per_paper=max_per_paper,
            exclude_references="references" in set(mode.get("excluded_sections", [])),
            exclude_tables=True,
            exclude_figures=True,
            exclude_supplement=False,
            query_mode=str(mode.get("query_mode", "evidence")),
            prefer_sections=list(mode.get("preferred_sections", [])),
            exclude_sections=list(mode.get("excluded_sections", [])),
        )
        decorated_hits = []
        for hit in chunk_hits:
            hit = dict(hit)
            hit["query_id"] = query.get("query_id", "")
            hit["query_label"] = query.get("query_type", "")
            hit["query_text"] = query_text
            hit["subquery_rank"] = hit.get("rank", "")
            hit["candidate_paper_rank"] = paper_rank(hit.get("paper_id"), paper_hits)
            raw_candidates.append(hit)
            per_subquery_results.append(hit)
            decorated_hits.append(hit)
        ranked_lists.append(decorated_hits)
    return ranked_lists, raw_candidates, list(all_papers.values()), per_subquery_results


def corrective_retry(
    *,
    question: str,
    task_mode: str,
    chunks_dir: Path,
    papers_tsv: Path,
    config: dict,
    mode: dict,
    current_quality: dict,
    max_chunks: int,
    max_per_paper: int,
    expected_sources: list[str] | None = None,
) -> dict:
    reasons = set(current_quality.get("reasons", []))
    retry_max_per_paper = max_per_paper
    correction_actions = []
    retry_question = question
    if "too_few_unique_papers" in reasons:
        retry_question = broaden_question(question)
        correction_actions.append("broadened_query_terms")
    if "dominated_by_single_paper" in reasons:
        retry_max_per_paper = 1
        correction_actions.append("lowered_max_per_paper")
    if "too_many_tables_figures_or_supplements" in reasons or "contains_references" in reasons:
        correction_actions.append("hardened_problem_section_filters")

    retry_plan = plan_queries(
        retry_question,
        task_mode=task_mode,
        max_queries=int(config.get("max_subqueries") or 8),
    )
    ranked_lists, raw_candidates, _paper_candidates, per_subquery_results = retrieve_for_plan(
        retry_plan,
        chunks_dir=chunks_dir,
        papers_tsv=papers_tsv,
        task_mode=task_mode,
        mode=mode,
        config={**config, "max_candidate_papers": int(config["max_candidate_papers"]) + 10},
        max_per_paper=retry_max_per_paper,
    )
    fused = reciprocal_rank_fusion(ranked_lists, k=int(config["fusion"]["rrf_k"]))
    diverse = enforce_diversity(dedupe_near_identical(fused), max_chunks=max_chunks, max_per_paper=retry_max_per_paper)
    pack = build_evidence_pack(question, diverse, max_chunks=max_chunks)
    return {
        "query_plan": retry_plan,
        "raw_candidates": raw_candidates,
        "per_subquery_results": per_subquery_results,
        "fused_candidates": fused,
        "diverse_candidates": diverse,
        "evidence_pack": pack,
        "evidence_quality": score_evidence_pack(pack, expected_sources=expected_sources),
        "correction_actions": correction_actions or ["retry_without_parameter_change"],
    }


def should_retry(quality: dict, config: dict) -> bool:
    threshold = str(config.get("correction", {}).get("retry_if_grade_below", "usable"))
    return GRADE_ORDER.get(str(quality.get("grade")), 0) < GRADE_ORDER.get(threshold, 2)


def enforce_diversity(candidates: list[dict], *, max_chunks: int, max_per_paper: int) -> list[dict]:
    kept = []
    per_paper: dict[str, int] = {}
    for candidate in candidates:
        paper_id = str(candidate.get("paper_id") or "")
        if max_per_paper > 0 and per_paper.get(paper_id, 0) >= max_per_paper:
            continue
        kept.append(candidate)
        per_paper[paper_id] = per_paper.get(paper_id, 0) + 1
        if len(kept) >= max_chunks:
            break
    for rank, item in enumerate(kept, start=1):
        item["rank"] = rank
    return kept


def dedupe_near_identical(candidates: list[dict]) -> list[dict]:
    kept = []
    seen = set()
    for candidate in candidates:
        key = normalize_text(str(candidate.get("text_preview") or candidate.get("snippet") or ""))[:240]
        if key and key in seen:
            continue
        seen.add(key)
        kept.append(candidate)
    return kept


def paper_rank(paper_id: object, paper_hits: list[dict]) -> int | str:
    for paper in paper_hits:
        if paper.get("paper_id") == paper_id:
            return int(paper.get("rank") or 0)
    return ""


def broaden_question(question: str) -> str:
    terms = [term for term in normalize_text(question).split() if len(term) >= 4]
    dropped = {"what", "does", "link", "links", "evidence", "between", "with"}
    kept = [term for term in terms if term not in dropped]
    return " ".join(kept[:10]) or question


def load_adaptive_config(path: Path = DEFAULT_ADAPTIVE_CONFIG) -> dict:
    path = path if Path(path).is_absolute() else REPO_ROOT / path
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    config = payload.get("adaptive_retrieval", {})
    if not config:
        raise ValueError(f"Adaptive retrieval config missing adaptive_retrieval block: {path}")
    return config


def write_adaptive_outputs(result: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "query_plan.json", result["query_plan"])
    write_per_subquery_results_tsv(
        out_dir / "per_subquery_results.tsv",
        result.get("per_subquery_results", []),
    )
    write_candidates_tsv(out_dir / "retrieval_candidates.tsv", result.get("retrieval_candidates", []))
    write_json(out_dir / "evidence_pack.json", result["evidence_pack"])
    (out_dir / "evidence_pack.md").write_text(
        format_evidence_pack_markdown(result["evidence_pack"]),
        encoding="utf-8",
    )
    write_json(out_dir / "evidence_quality.json", result["evidence_quality"])
    write_json(
        out_dir / "run_config.json",
        {
            "question": result["question"],
            "task_mode": result["task_mode"],
            "retriever": result["retriever"],
            "retriever_requested": result.get("retriever_requested", result["retriever"]),
            "retriever_fallback_reason": result.get("retriever_fallback_reason"),
            "diagnostics": result["diagnostics"],
            "correction_actions": result["correction_actions"],
            "insufficient_evidence": result["evidence_quality"].get("grade") in {"bad", "weak"},
        },
    )
    (out_dir / "adaptive_retrieval_report.md").write_text(format_report(result), encoding="utf-8")


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_candidates_tsv(path: Path, candidates: list[dict]) -> None:
    fields = [
        "rank",
        "score",
        "rrf_score",
        "paper_id",
        "author",
        "year",
        "title",
        "journal",
        "section",
        "page_start",
        "page_end",
        "chunk_id",
        "contributing_queries",
        "query_ranks",
        "quality_flags",
        "snippet",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fields)
        writer.writeheader()
        for candidate in candidates:
            row = {field: candidate.get(field, "") for field in fields}
            row["contributing_queries"] = ";".join(candidate.get("contributing_queries") or [])
            row["query_ranks"] = json.dumps(candidate.get("query_ranks") or {}, ensure_ascii=False)
            row["quality_flags"] = ";".join(candidate.get("quality_flags") or [])
            writer.writerow(row)


def write_per_subquery_results_tsv(path: Path, results: list[dict]) -> None:
    fields = [
        "query_id",
        "query_label",
        "query_text",
        "subquery_rank",
        "score",
        "raw_score",
        "paper_id",
        "author",
        "year",
        "title",
        "journal",
        "section",
        "page_start",
        "page_end",
        "chunk_id",
        "quality_flags",
        "snippet",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fields)
        writer.writeheader()
        for result in results:
            row = {field: result.get(field, "") for field in fields}
            row["quality_flags"] = ";".join(result.get("quality_flags") or [])
            writer.writerow(row)


def format_report(result: dict) -> str:
    quality = result.get("evidence_quality", {})
    diagnostics = result.get("diagnostics", {})
    lines = [
        "# Adaptive Retrieval Report",
        "",
        f"- Question: {result.get('question', '')}",
        f"- Task mode: {result.get('task_mode', '')}",
        f"- Evidence grade: {quality.get('grade', '')}",
        f"- Recommended action: {quality.get('recommended_action', '')}",
        f"- Insufficient evidence: {quality.get('grade') in {'bad', 'weak'}}",
        f"- Correction actions: {', '.join(result.get('correction_actions') or []) or 'none'}",
        "",
        "## Diagnostics",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in diagnostics.items())
    lines.extend(["", "## Quality Reasons", ""])
    lines.extend(f"- {reason}" for reason in quality.get("reasons", []))
    lines.extend(["", "## Top Sources", ""])
    for source in result.get("evidence_pack", {}).get("source_summary", []):
        lines.append(f"- {source.get('source_label', '')}: {source.get('title', '')}")
    lines.append("")
    return "\n".join(lines)
