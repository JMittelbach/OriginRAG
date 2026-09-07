from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from originrag.task_modes import get_task_mode, load_task_modes  # noqa: E402
from originrag.retrieval.query_plan import build_retrieval_queries  # noqa: E402


def main() -> int:
    args = parse_args()
    mode_name = args.task_mode or infer_task_mode(args.question)
    mode = get_task_mode(mode_name)
    out_dir = resolve_repo_path(args.out_root) / timestamped_slug(args.question)
    out_dir.mkdir(parents=True, exist_ok=True)

    query_plan = [
        {"label": query.label, "query": query.query}
        for query in build_retrieval_queries(
            args.question,
            task=retrieval_task_for_mode(mode_name),
            max_queries=args.max_queries,
        )
    ]
    run_config = {
        "question": args.question,
        "task_mode": mode_name,
        "task_mode_config": mode,
        "query_plan": query_plan,
        "model_call_performed": False,
        "llm_needed_later": mode_name
        in {"literature_review", "gap_finder", "hypothesis_generator", "hypothesis_critic"},
    }
    (out_dir / "run_config.json").write_text(json.dumps(run_config, indent=2), encoding="utf-8")
    (out_dir / "task_plan.md").write_text(render_plan_markdown(run_config), encoding="utf-8")
    print(f"Wrote task plan to {display_path(out_dir / 'task_plan.md')}")
    print(f"Wrote run config to {display_path(out_dir / 'run_config.json')}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan a no-model OriginRAG research task.")
    parser.add_argument("question")
    parser.add_argument("--task-mode", choices=sorted(load_task_modes().keys()))
    parser.add_argument("--out-root", default="outputs/research_tasks")
    parser.add_argument("--max-queries", type=int, default=8)
    return parser.parse_args()


def infer_task_mode(question: str) -> str:
    normalized = question.lower()
    if any(term in normalized for term in ["gap", "missing", "underexplored", "fehlt", "lücke"]):
        return "gap_finder"
    if any(term in normalized for term in ["hypothesis", "hypothese", "prediction"]):
        return "hypothesis_generator"
    if any(term in normalized for term in ["dataset", "cohort", "accession", "data set"]):
        return "dataset_finder"
    if any(term in normalized for term in ["compare", "vergleich", "method", "tool"]):
        return "method_comparison"
    if any(term in normalized for term in ["contradict", "conflict", "widerspruch"]):
        return "contradiction_finder"
    if any(term in normalized for term in ["mechanism", "map", "pathway"]):
        return "mechanism_mapping"
    return "evidence_qa"


def retrieval_task_for_mode(task_mode: str) -> str:
    if task_mode == "gap_finder":
        return "gaps"
    if task_mode in {"hypothesis_generator", "hypothesis_critic"}:
        return "hypothesis"
    if task_mode == "dataset_finder":
        return "sources"
    return "answer"


def render_plan_markdown(run_config: dict) -> str:
    mode = run_config["task_mode_config"]
    query_lines = "\n".join(
        f"- `{query['label']}`: {query['query']}" for query in run_config["query_plan"]
    )
    return "\n".join(
        [
            "# Research Task Plan",
            "",
            f"Question: {run_config['question']}",
            f"Task mode: {run_config['task_mode']}",
            f"Description: {mode['description']}",
            "",
            "Preferred sections:",
            ", ".join(mode["preferred_sections"]),
            "",
            "Excluded sections:",
            ", ".join(mode["excluded_sections"]),
            "",
            f"Max chunks: {mode['max_chunks']}",
            f"Max per paper: {mode['max_per_paper']}",
            f"Expected output schema: {mode['output_schema']}",
            "",
            "Suggested retrieval strategy:",
            "- Run planned subqueries with section and metadata filters.",
            "- Enforce paper diversity before evidence synthesis.",
            "- Grade the evidence pack before any answer generation.",
            "",
            "Planned subqueries:",
            query_lines,
            "",
            f"LLM needed later: {run_config['llm_needed_later']}",
            "Model call performed: 0",
            "",
        ]
    )


def timestamped_slug(text: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "research-task"
    return f"{timestamp}_{slug}"


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
