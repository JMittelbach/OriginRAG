from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ask_corpus  # noqa: E402


def main() -> int:
    args = parse_args()
    out_dir = resolve_repo_path(args.out_dir)
    if args.retriever == "hybrid":
        print("Hybrid retrieval is not configured yet; falling back to keyword retrieval.")
    result = ask_corpus.run_question(build_ask_args(args), out_dir)
    grade = (result.get("pack", {}).get("grade") or {}).get("grade", "unknown")
    print(f"Wrote evidence retrieval run to {display_path(out_dir)}")
    print(f"Evidence grade: {grade}")
    print(f"Query plan: {display_path(result['query_plan_path'])}")
    print(f"Evidence pack: {display_path(result['pack_path'])}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OriginRAG retrieval and evidence grading without answer generation.")
    parser.add_argument("question")
    parser.add_argument("--task-mode", default="evidence_qa")
    parser.add_argument("--task", choices=["answer", "gaps", "hypothesis", "sources"], default="answer")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--planner-backend", choices=["rule_based", "ollama"], default="rule_based")
    parser.add_argument("--planner-model", default=None)
    parser.add_argument("--planner-temperature", type=float, default=0.0)
    parser.add_argument("--planner-timeout", type=int, default=120)
    parser.add_argument("--retriever", choices=["keyword", "hybrid"], default="keyword")
    parser.add_argument("--preset", choices=sorted(ask_corpus.PRESETS), default="pro")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--candidate-multiplier", type=int)
    parser.add_argument("--max-per-paper", type=int)
    parser.add_argument("--max-chars-per-chunk", type=int)
    parser.add_argument("--min-evidence-grade", choices=["bad", "weak", "usable", "good"], default="usable")
    parser.add_argument("--out-dir", default="outputs/adaptive_retrieval/run")
    parser.add_argument("--evidence-selector", choices=["current", "topk", "mmr", "trace_no_lineage", "trace", "trace_flow", "trace_flow_no_anchor", "trace_flow_unbounded", "trace_flow_relevance_reserve", "trace_flow_diverse_reserve"], default="trace_flow")
    parser.add_argument("--trace-config", default="config/trace_rag.yaml")
    parser.add_argument("--trace-blueprint")
    parser.add_argument("--trace-lineage-graph")
    parser.add_argument("--trace-token-budget", type=int, default=8000)
    parser.add_argument("--trace-selector-algorithm", choices=["lazy_greedy", "beam_search"], default="lazy_greedy")
    parser.add_argument("--show-trace-certificate", action="store_true")
    return parser.parse_args()


def build_ask_args(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        question=args.question,
        retrieval_question=args.question,
        answer_question=args.question,
        chunks_dir=args.chunks_dir,
        papers=args.papers,
        limit=args.limit,
        candidate_multiplier=args.candidate_multiplier,
        max_per_paper=args.max_per_paper,
        max_chars_per_chunk=args.max_chars_per_chunk,
        preset=args.preset,
        backend="none",
        adaptive=True,
        task=args.task,
        task_mode=args.task_mode,
        planner_backend=args.planner_backend,
        planner_model=args.planner_model,
        planner_temperature=args.planner_temperature,
        planner_timeout=args.planner_timeout,
        answer_model=None,
        answer_temperature=0.0,
        answer_timeout=120,
        min_evidence_grade=args.min_evidence_grade,
        force_answer=False,
        out_dir=args.out_dir,
        category=None,
        include_low_quality=False,
        show_context=False,
        query_mode="evidence",
        min_score=0.0,
        exclude_tables=False,
        exclude_figures=False,
        exclude_supplement=False,
        allow_missing_doi_sources=False,
        repair_attempts=0,
        no_template_fallback=True,
        profile_instruction="",
        evidence_selector=args.evidence_selector,
        trace_config=args.trace_config,
        trace_blueprint=args.trace_blueprint,
        trace_lineage_graph=args.trace_lineage_graph,
        trace_token_budget=args.trace_token_budget,
        trace_selector_algorithm=args.trace_selector_algorithm,
        show_trace_certificate=args.show_trace_certificate,
    )


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def display_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
