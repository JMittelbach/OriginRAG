from __future__ import annotations

import argparse
import csv
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROTECTED_STATUSES = {"excluded_no_doi", "duplicate", "archived", "failed"}


def main() -> int:
    args = parse_args()
    papers_path = resolve_repo_path(args.papers)
    extracted_dir = resolve_repo_path(args.extracted_dir)
    chunks_dir = resolve_repo_path(args.chunks_dir)
    rows = read_tsv(papers_path)
    fieldnames = list(rows[0].keys()) if rows else []

    updated = 0
    for row in rows:
        if row.get("fulltext_status") in PROTECTED_STATUSES:
            continue
        paper_id = row.get("paper_id", "")
        if not paper_id:
            continue
        before = row.get("fulltext_status", "")
        chunk_path = chunks_dir / f"{paper_id}.jsonl"
        if chunk_path.exists() and has_nonempty_line(chunk_path):
            row["fulltext_status"] = "chunked"
            row["extracted_text_relpath"] = row.get("extracted_text_relpath") or f"data/extracted_text/{paper_id}.json"
        elif (extracted_dir / f"{paper_id}.json").exists():
            row["fulltext_status"] = "extracted"
            row["extracted_text_relpath"] = row.get("extracted_text_relpath") or f"data/extracted_text/{paper_id}.json"
        if row.get("fulltext_status", "") != before:
            updated += 1

    if not args.dry_run:
        write_tsv(papers_path, rows, fieldnames)
    print(f"Updated fulltext_status rows: {updated}")
    if args.dry_run:
        print("Dry run: papers.tsv was not modified.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update papers.tsv fulltext_status from extracted/chunk artifacts.")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--extracted-dir", default="data/extracted_text")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def has_nonempty_line(path: Path) -> bool:
    with path.open("r", encoding="utf-8") as handle:
        return any(line.strip() for line in handle)


if __name__ == "__main__":
    raise SystemExit(main())
