from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.retrieval.metadata import load_paper_metadata  # noqa: E402
from pfempire.utils.io import write_jsonl  # noqa: E402


def main() -> int:
    args = parse_args()
    chunks_dir = resolve_repo_path(args.chunks_dir)
    papers_path = resolve_repo_path(args.papers)
    out_path = resolve_repo_path(args.out)
    report_path = out_path.with_name("claim_extraction_job_report.md")
    metadata = load_paper_metadata(papers_path)
    preferred_sections = {section.strip().lower() for section in args.prefer_sections if section.strip()}

    jobs = []
    skipped_section = 0
    for chunk in iter_chunks(chunks_dir):
        section = str(chunk.get("section") or "unknown").strip().lower()
        if preferred_sections and section not in preferred_sections:
            skipped_section += 1
            continue
        paper_id = str(chunk.get("paper_id") or "")
        paper_meta = metadata.get(paper_id, {})
        jobs.append(
            {
                "job_id": f"CLAIM_JOB_{len(jobs) + 1:06d}",
                "paper_id": paper_id,
                "chunk_id": str(chunk.get("chunk_id") or ""),
                "section": section,
                "source_label": source_label(paper_meta, paper_id),
                "text": str(chunk.get("text") or ""),
                "prompt_template": "extract_claims.md",
            }
        )
        if args.limit and len(jobs) >= args.limit:
            break

    write_jsonl(out_path, jobs)
    write_report(report_path, jobs=jobs, skipped_section=skipped_section, args=args)
    print(f"Wrote {len(jobs)} claim extraction jobs to {display_path(out_path)}")
    print(f"Wrote report to {display_path(report_path)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare no-model claim extraction JSONL jobs.")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--prefer-sections", nargs="+", default=["results", "discussion", "abstract"])
    parser.add_argument("--out", default="outputs/claim_extraction/jobs/claim_extraction_jobs.jsonl")
    return parser.parse_args()


def iter_chunks(chunks_dir: Path) -> list[dict]:
    chunks = []
    if not chunks_dir.exists():
        return chunks
    for path in sorted(chunks_dir.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    chunks.append(json.loads(line))
    return chunks


def source_label(metadata: dict[str, str], paper_id: str) -> str:
    author = metadata.get("author", "")
    year = metadata.get("year", "")
    if author and year:
        return f"{author} {year}"
    return paper_id


def write_report(path: Path, *, jobs: list[dict], skipped_section: int, args: argparse.Namespace) -> None:
    unique_papers = {job["paper_id"] for job in jobs if job.get("paper_id")}
    lines = [
        "# Claim Extraction Job Report",
        "",
        f"- Jobs prepared: {len(jobs)}",
        f"- Unique papers: {len(unique_papers)}",
        f"- Preferred sections: {', '.join(args.prefer_sections)}",
        f"- Chunks skipped by section filter before limit: {skipped_section}",
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
