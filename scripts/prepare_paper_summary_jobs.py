from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from originrag.retrieval.metadata import load_paper_metadata  # noqa: E402
from originrag.utils.io import write_jsonl  # noqa: E402


SECTION_PRIORITY = ["abstract", "methods", "results", "discussion", "data_availability", "unknown"]


def main() -> int:
    args = parse_args()
    papers_path = resolve_repo_path(args.papers)
    chunks_dir = resolve_repo_path(args.chunks_dir)
    out_path = resolve_repo_path(args.out)
    report_path = out_path.with_name("paper_summary_job_report.md")
    metadata = load_paper_metadata(papers_path)
    active_paper_ids = read_active_paper_ids(papers_path)
    chunks_by_paper = load_chunks_by_paper(chunks_dir)

    jobs = []
    for paper_id in active_paper_ids:
        chunks = chunks_by_paper.get(paper_id, [])
        if not chunks:
            continue
        selected = select_representative_chunks(chunks, max_chars=args.max_chars)
        jobs.append(
            {
                "job_id": f"PAPER_SUMMARY_JOB_{len(jobs) + 1:06d}",
                "paper_id": paper_id,
                "metadata": metadata.get(paper_id, {"paper_id": paper_id}),
                "selected_chunks": selected,
                "prompt_template": "structured_paper_summary.md",
            }
        )
        if args.limit and len(jobs) >= args.limit:
            break

    write_jsonl(out_path, jobs)
    write_report(report_path, jobs=jobs, active_count=len(active_paper_ids), args=args)
    print(f"Wrote {len(jobs)} paper summary jobs to {display_path(out_path)}")
    print(f"Wrote report to {display_path(report_path)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare no-model structured paper summary jobs.")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--out", default="outputs/paper_summaries/jobs/paper_summary_jobs.jsonl")
    parser.add_argument("--max-chars", type=int, default=18000)
    parser.add_argument("--limit", type=int, default=0, help="Maximum papers. Use 0 for no limit.")
    return parser.parse_args()


def read_active_paper_ids(path: Path) -> list[str]:
    if not path.exists():
        return []
    active_statuses = {"pdf_available", "extracted", "chunked"}
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [
            row.get("paper_id", "")
            for row in csv.DictReader(handle, delimiter="\t")
            if row.get("paper_id") and row.get("fulltext_status", "") in active_statuses
        ]


def load_chunks_by_paper(chunks_dir: Path) -> dict[str, list[dict]]:
    chunks_by_paper: dict[str, list[dict]] = defaultdict(list)
    if not chunks_dir.exists():
        return chunks_by_paper
    for path in sorted(chunks_dir.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                paper_id = str(chunk.get("paper_id") or "")
                if paper_id:
                    chunks_by_paper[paper_id].append(chunk)
    return chunks_by_paper


def select_representative_chunks(chunks: list[dict], *, max_chars: int) -> list[dict]:
    ordered = sorted(chunks, key=chunk_sort_key)
    selected = []
    total_chars = 0
    for chunk in ordered:
        text = str(chunk.get("text") or "")
        if not text.strip():
            continue
        if selected and total_chars + len(text) > max_chars:
            continue
        selected.append(
            {
                "chunk_id": chunk.get("chunk_id", ""),
                "section": chunk.get("section", "unknown"),
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "text": text,
            }
        )
        total_chars += len(text)
    return selected


def chunk_sort_key(chunk: dict) -> tuple[int, int]:
    section = str(chunk.get("section") or "unknown")
    try:
        section_rank = SECTION_PRIORITY.index(section)
    except ValueError:
        section_rank = len(SECTION_PRIORITY)
    return section_rank, int(chunk.get("chunk_index") or 0)


def write_report(path: Path, *, jobs: list[dict], active_count: int, args: argparse.Namespace) -> None:
    lines = [
        "# Paper Summary Job Report",
        "",
        f"- Active papers seen: {active_count}",
        f"- Jobs prepared: {len(jobs)}",
        f"- Max chars per job: {args.max_chars}",
        "- Model calls performed: 0",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


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
