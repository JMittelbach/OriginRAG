from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
SCRIPTS_DIR = REPO_ROOT / "scripts"
for path in [SRC_DIR, SCRIPTS_DIR]:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ask_corpus  # noqa: E402
from originrag.analysis.faithfulness import load_and_check, write_grounding_report  # noqa: E402
from originrag.analysis.ollama_client import check_server, list_models, model_available  # noqa: E402
from originrag.utils.io import write_json  # noqa: E402


def main() -> int:
    args = parse_args()
    out_dir = resolve_repo_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    reachable, server_error = check_server(timeout=5)
    models, model_error = list_models(timeout=5) if reachable else ([], server_error)
    has_model = model_available(args.model, models)

    report = {
        "ollama_reachable": reachable,
        "ollama_error": model_error,
        "model": args.model,
        "model_available": has_model,
        "question": args.question,
    }
    if not reachable or not has_model:
        write_json(out_dir / "llm_smoke_test.json", report)
        (out_dir / "llm_smoke_test_report.md").write_text(format_report(report), encoding="utf-8")
        print(f"Wrote smoke report: {display_path(out_dir / 'llm_smoke_test_report.md')}")
        return 1

    result = ask_corpus.run_question(build_ask_args(args), out_dir)
    answer_path = result.get("answer_path")
    grounding = None
    if answer_path:
        grounding = load_and_check(answer_path, result["pack_json_path"])
        write_grounding_report(
            grounding,
            out_dir / "answer_grounding_report.md",
            out_dir / "answer_grounding_issues.tsv",
        )
    run_config = json.loads((out_dir / "run_config.json").read_text(encoding="utf-8"))
    report.update(
        {
            "planner_backend_used": run_config.get("planner_backend_used"),
            "planner_call_performed": run_config.get("planner_call_performed"),
            "evidence_grade": run_config.get("evidence_grade"),
            "answer_backend_used": run_config.get("answer_backend_used"),
            "answer_call_performed": run_config.get("answer_call_performed"),
            "answer_path": display_path(answer_path) if answer_path else None,
            "grounding_passed": None if grounding is None else grounding.get("passed"),
            "grounding_report": display_path(out_dir / "answer_grounding_report.md") if grounding else None,
        }
    )
    write_json(out_dir / "llm_smoke_test.json", report)
    (out_dir / "llm_smoke_test_report.md").write_text(format_report(report), encoding="utf-8")
    print(f"Wrote smoke report: {display_path(out_dir / 'llm_smoke_test_report.md')}")
    return 0 if report.get("answer_call_performed") else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a small local LLM-native OriginRAG smoke test.")
    parser.add_argument("--model", default="qwen3:30b")
    parser.add_argument("--question", default="What evidence links PfEMP1 to cerebral malaria?")
    parser.add_argument("--out-dir", default="outputs/llm_smoke_test")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--max-per-paper", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=240)
    return parser.parse_args()


def build_ask_args(args: argparse.Namespace):
    from types import SimpleNamespace

    return SimpleNamespace(
        question=args.question,
        retrieval_question=args.question,
        answer_question=args.question,
        chunks_dir="data/chunks",
        papers="data/metadata/papers.tsv",
        limit=args.limit,
        candidate_multiplier=4,
        max_per_paper=args.max_per_paper,
        max_chars_per_chunk=900,
        preset="balanced",
        backend="ollama",
        adaptive=True,
        task="answer",
        task_mode="evidence_qa",
        planner_backend="ollama",
        planner_model=args.model,
        planner_temperature=0.0,
        planner_timeout=args.timeout,
        answer_model=args.model,
        answer_temperature=0.0,
        answer_timeout=args.timeout,
        min_evidence_grade="usable",
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
    )


def format_report(report: dict) -> str:
    lines = [
        "# LLM Smoke Test Report",
        "",
        f"- Ollama reachable: {report.get('ollama_reachable')}",
        f"- Model: {report.get('model')}",
        f"- Model available: {report.get('model_available')}",
        f"- Planner used: {report.get('planner_backend_used', 'not_run')}",
        f"- Planner call performed: {report.get('planner_call_performed', False)}",
        f"- Evidence grade: {report.get('evidence_grade', 'not_run')}",
        f"- Answer backend: {report.get('answer_backend_used', 'not_run')}",
        f"- Answer call performed: {report.get('answer_call_performed', False)}",
        f"- Answer: {report.get('answer_path') or 'not_written'}",
        f"- Grounding passed: {report.get('grounding_passed')}",
        f"- Grounding report: {report.get('grounding_report') or 'not_written'}",
        "",
    ]
    if report.get("ollama_error"):
        lines.extend(["## Error", "", str(report["ollama_error"]), ""])
    return "\n".join(lines)


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def display_path(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
