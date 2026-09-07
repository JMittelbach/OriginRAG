from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.curation.corpus_hygiene import (  # noqa: E402
    KEEP_ACTIONS,
    QUARANTINE_ACTIONS,
    assess_paper,
    collect_chunk_stats,
    find_exact_duplicates,
)
from pfempire.retrieval.metadata import load_paper_metadata  # noqa: E402


FIELDS = [
    "paper_id",
    "recommended_action",
    "confidence",
    "reasons",
    "duplicate_of",
    "domain",
    "topics",
    "fulltext_status",
    "title",
    "doi",
    "pmid",
    "pmcid",
    "local_pdf_relpath",
    "total_chunks",
    "useful_fraction",
    "problem_fraction",
    "unknown_fraction",
    "low_quality_fraction",
    "median_word_count",
]


def main() -> int:
    args = parse_args()
    papers_path = resolve(args.papers)
    chunks_dir = resolve(args.chunks_dir)
    out_dir = resolve(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    metadata = load_paper_metadata(papers_path)
    papers = [metadata[paper_id] for paper_id in sorted(metadata)]
    chunk_stats = collect_chunk_stats(chunks_dir)
    duplicate_map = find_exact_duplicates(papers, chunk_stats)
    assessments = [
        assess_paper(row, chunk_stats.get(row["paper_id"]), duplicate_map.get(row["paper_id"]))
        for row in papers
    ]
    rows = [assessment.to_row() for assessment in assessments]
    quarantine = [row for row in rows if row["recommended_action"] in QUARANTINE_ACTIONS]
    review = [row for row in rows if row["recommended_action"].startswith("review_")]

    write_tsv(out_dir / "paper_hygiene_audit.tsv", rows)
    write_tsv(out_dir / "quarantine_candidates.tsv", quarantine)
    write_tsv(out_dir / "manual_review.tsv", review)
    write_tsv(out_dir / "keep_inventory.tsv", [row for row in rows if row["recommended_action"] in KEEP_ACTIONS])
    (out_dir / "corpus_hygiene_report.md").write_text(
        render_report(rows, quarantine, review), encoding="utf-8"
    )

    print(f"Papers audited: {len(rows)}")
    print(f"Quarantine candidates: {len(quarantine)}")
    print(f"Manual review candidates: {len(review)}")
    print(f"Report: {display(out_dir / 'corpus_hygiene_report.md')}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit corpus duplicates, extraction quality, and scope without deleting PDFs."
    )
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--out-dir", default="outputs/corpus_hygiene")
    return parser.parse_args()


def render_report(
    rows: list[dict[str, str]],
    quarantine: list[dict[str, str]],
    review: list[dict[str, str]],
) -> str:
    actions = Counter(row["recommended_action"] for row in rows)
    domains = Counter(row["domain"] or "unknown" for row in rows)
    lines = [
        "# Corpus Hygiene Audit",
        "",
        "This is a non-destructive audit. Quarantine entries are proposals; no PDF, chunk, or metadata row was removed.",
        "Scope flags require human review and are not treated as technical junk.",
        "",
        "## Summary",
        "",
        f"- Papers audited: {len(rows)}",
        f"- Proposed technical quarantine: {len(quarantine)}",
        f"- Manual scope/quality review: {len(review)}",
        f"- Kept automatically: {sum(actions[action] for action in KEEP_ACTIONS)}",
        "",
        "## Actions",
        "",
        "| action | papers |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {action} | {count} |" for action, count in sorted(actions.items()))
    lines.extend(["", "## Collections", "", "| domain | papers |", "| --- | ---: |"]) 
    lines.extend(f"| {domain} | {count} |" for domain, count in sorted(domains.items()))
    lines.extend(
        [
            "",
            "## Quarantine Proposals",
            "",
            "| paper_id | reason | duplicate_of | title |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in quarantine[:100]:
        lines.append(
            f"| {escape(row['paper_id'])} | {escape(row['reasons'])} | {escape(row['duplicate_of'])} | {escape(row['title'])} |"
        )
    if not quarantine:
        lines.append("| - | No deterministic quarantine candidate found. | - | - |")
    lines.extend(
        [
            "",
            "## Review Queue",
            "",
            "| paper_id | action | reason | chunks | title |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in review[:150]:
        lines.append(
            f"| {escape(row['paper_id'])} | {row['recommended_action']} | {escape(row['reasons'])} | {row['total_chunks']} | {escape(row['title'])} |"
        )
    lines.extend(
        [
            "",
            "## Safe Application Rule",
            "",
            "Only exact duplicates or demonstrably corrupt extraction should be quarantined automatically. Scope decisions need review, and a proposed filter must pass the frozen benchmark recall audit before activation.",
            "",
        ]
    )
    return "\n".join(lines)


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def display(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def escape(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
