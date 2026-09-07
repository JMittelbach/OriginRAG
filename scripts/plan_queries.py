from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.retrieval.query_planner import plan_queries  # noqa: E402


def main() -> int:
    args = parse_args()
    plan = plan_queries(
        args.question,
        task_mode=args.task_mode,
        planner_backend=args.planner_backend,
        planner_model=args.planner_model,
        max_queries=args.max_queries,
        planner_temperature=args.planner_temperature,
        planner_timeout=args.planner_timeout,
        task=args.task,
    )
    payload = dict(plan)
    raw_output = payload.pop("planner_raw_output", None)
    validation = payload.get("planner_validation") or {}
    source = payload.get("query_plan_source") or payload.get("planner_backend_used") or "unknown"
    if args.out:
        out_path = resolve_repo_path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        raw_path = out_path.with_name("planner_raw_output.txt")
        validation_path = out_path.with_name("planner_validation.json")
        source_path = out_path.with_name("query_plan_source.txt")
        raw_path.write_text("" if raw_output is None else str(raw_output), encoding="utf-8")
        validation_path.write_text(json.dumps(validation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        source_path.write_text(str(source) + "\n", encoding="utf-8")
        print(f"Wrote query plan to {display_path(out_path)}")
        print(f"Wrote planner raw output to {display_path(raw_path)}")
        print(f"Wrote planner validation to {display_path(validation_path)}")
        print(f"Wrote query plan source to {display_path(source_path)}")
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan OriginRAG retrieval subqueries.")
    parser.add_argument("question")
    parser.add_argument("--task-mode", default="evidence_qa")
    parser.add_argument("--task", choices=["answer", "gaps", "hypothesis", "sources"], default="answer")
    parser.add_argument("--planner-backend", choices=["rule_based", "ollama"], default="rule_based")
    parser.add_argument("--planner-model", default=None)
    parser.add_argument("--planner-temperature", type=float, default=0.0)
    parser.add_argument("--planner-timeout", type=int, default=120)
    parser.add_argument("--max-queries", type=int, default=8)
    parser.add_argument("--out")
    return parser.parse_args()


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
