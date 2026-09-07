from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.evaluation.malaria_evidence_bench import (  # noqa: E402
    accepted_rows,
    empty_item,
    read_tsv,
    validate_benchmark,
    write_tsv,
    write_validation_report,
)


def main() -> int:
    args = parse_args()
    candidates_path = resolve(args.candidates)
    if args.seed:
        rows = seed_rows()
        write_tsv(candidates_path, rows)
        print(f"Wrote {len(rows)} benchmark candidates to {display(candidates_path)}")
    elif not candidates_path.exists():
        raise FileNotFoundError(f"Candidate benchmark does not exist: {candidates_path}")

    rows = read_tsv(candidates_path)
    report = validate_benchmark(
        rows,
        papers_tsv=resolve(args.papers),
        chunks_dir=resolve(args.chunks_dir),
    )
    write_validation_report(resolve(args.report), report)
    print(f"Candidate validation: valid={report['valid']} items={report['n_items']}")

    if args.export_gold:
        gold_rows = accepted_rows(rows)
        gold_report = validate_benchmark(
            gold_rows,
            papers_tsv=resolve(args.papers),
            chunks_dir=resolve(args.chunks_dir),
            require_gold=True,
        )
        write_tsv(resolve(args.gold), gold_rows)
        write_validation_report(resolve(args.gold_report), gold_report)
        print(f"Gold export: valid={gold_report['valid']} accepted_items={len(gold_rows)}")
        if not gold_report["valid"]:
            return 2
    return 0 if report["valid"] else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and validate MalariaEvidenceBench.")
    parser.add_argument("--seed", action="store_true", help="Rebuild candidates from existing eval TSV files.")
    parser.add_argument("--export-gold", action="store_true")
    parser.add_argument("--candidates", default="data/benchmarks/malaria_evidence_bench_candidates.tsv")
    parser.add_argument("--gold", default="data/benchmarks/malaria_evidence_bench_gold.tsv")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--report", default="outputs/benchmarks/malaria_evidence_bench_validation.md")
    parser.add_argument("--gold-report", default="outputs/benchmarks/malaria_evidence_bench_gold_validation.md")
    return parser.parse_args()


def seed_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    rows.extend(seed_adaptive())
    rows.extend(seed_research_tasks())
    rows.extend(seed_claim_eval())
    rows.extend(seed_chat_eval())
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        question_key = " ".join(row["question"].lower().split())
        if question_key in seen:
            continue
        seen.add(question_key)
        row["item_id"] = f"MEB{len(deduped) + 1:04d}"
        deduped.append(row)
    return deduped


def seed_adaptive() -> list[dict[str, str]]:
    result = []
    for row in read_source("data/metadata/adaptive_retrieval_benchmark.tsv"):
        item = base_item(row.get("query", ""), "evidence_qa", "adaptive_retrieval_benchmark")
        item["expected_answer_nuggets"] = row.get("intent", "")
        item["expected_facets"] = row.get("expected_topics", "")
        item["review_notes"] = f"Legacy expected papers need mapping to paper IDs: {row.get('expected_key_papers', '')}"
        result.append(item)
    return result


def seed_research_tasks() -> list[dict[str, str]]:
    result = []
    for row in read_source("data/metadata/research_task_benchmark.tsv"):
        mode = row.get("task_mode", "evidence_qa")
        item = base_item(row.get("question", ""), mode, "research_task_benchmark")
        item["expected_answer_nuggets"] = row.get("expected_entity_terms", "")
        item["expected_facets"] = row.get("expected_topics", "")
        item["review_notes"] = row.get("notes", "")
        result.append(item)
    return result


def seed_claim_eval() -> list[dict[str, str]]:
    result = []
    for row in read_source("data/metadata/claim_eval_benchmark.tsv"):
        item = base_item(row.get("question", ""), "evidence_qa", "claim_eval_benchmark")
        item["expected_answer_nuggets"] = row.get("expected_terms", "")
        item["review_notes"] = row.get("notes", "")
        result.append(item)
    return result


def seed_chat_eval() -> list[dict[str, str]]:
    result = []
    for row in read_source("data/metadata/chat_eval_questions.tsv"):
        question = row.get("question", "")
        mode = infer_chat_mode(row.get("question_id", ""), question)
        item = base_item(question, mode, "chat_eval_questions")
        item["expected_answer_nuggets"] = row.get("expected_focus", "")
        item["review_notes"] = row.get("notes", "")
        result.append(item)
    return result


def base_item(question: str, task_mode: str, provenance: str) -> dict[str, str]:
    item = empty_item()
    item.update(
        {
            "split": "candidate",
            "review_status": "candidate",
            "task_mode": task_mode,
            "difficulty": "medium",
            "question": question,
            "expected_stance": "not_applicable",
            "abstain_expected": "false",
            "provenance": provenance,
        }
    )
    return item


def infer_chat_mode(question_id: str, question: str) -> str:
    key = f"{question_id} {question}".lower()
    if "source" in key or "which paper" in key:
        return "source_lookup"
    if "gap" in key:
        return "gap_finder"
    if "hyp" in key:
        return "hypothesis_generator"
    return "evidence_qa"


def read_source(path_text: str) -> list[dict[str, str]]:
    path = resolve(path_text)
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def resolve(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def display(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())

