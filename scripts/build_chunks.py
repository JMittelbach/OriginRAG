from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.ingestion.chunking import chunk_pages  # noqa: E402
from pfempire.utils.io import read_json, write_jsonl  # noqa: E402


def main() -> int:
    args = parse_args()
    extracted_dir = REPO_ROOT / "data/extracted_text"
    chunks_dir = REPO_ROOT / "data/chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    if not extracted_dir.exists():
        print(f"Missing extracted text directory: {extracted_dir}")
        return 1

    processed = 0
    total_chunks = 0
    eligible_ids = eligible_paper_ids(args)
    paper_id_filter = set(args.paper_id or [])

    for input_path in sorted(extracted_dir.glob("*.json")):
        payload = read_json(input_path)
        if isinstance(payload, dict):
            paper_id = str(payload.get("paper_id") or input_path.stem)
            pages = payload.get("pages") or []
        else:
            paper_id = input_path.stem
            pages = payload

        if paper_id_filter and paper_id not in paper_id_filter:
            continue
        if eligible_ids is not None and paper_id not in eligible_ids:
            continue
        if args.limit and processed >= args.limit:
            continue
        output_path = chunks_dir / f"{paper_id}.jsonl"
        if output_path.exists() and not args.overwrite:
            continue
        if not isinstance(pages, list):
            print(f"failed: {input_path.name}: pages must be a list")
            continue

        chunks = chunk_pages(paper_id=paper_id, pages=pages)
        write_jsonl(output_path, chunks)
        print(f"ok: {paper_id}: {len(chunks)} chunks -> {output_path.relative_to(REPO_ROOT)}")
        processed += 1
        total_chunks += len(chunks)

    print()
    print(f"Chunking complete: {processed} papers, {total_chunks} chunks.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build chunks for selected extracted-text files.")
    parser.add_argument("--paper-id", action="append", help="Only chunk this paper_id. Repeatable.")
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum selected papers to chunk. Use 0 for no limit.",
    )
    parser.add_argument(
        "--only-status",
        default="extracted",
        help="Only chunk paper IDs with this fulltext_status in papers.tsv. Use empty string to disable.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing chunk JSONL files.")
    return parser.parse_args()


def eligible_paper_ids(args: argparse.Namespace) -> set[str] | None:
    if not args.only_status:
        return None
    papers_path = REPO_ROOT / "data/metadata/papers.tsv"
    if not papers_path.exists():
        return set()
    with papers_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return {
            row.get("paper_id", "")
            for row in reader
            if row.get("paper_id") and row.get("fulltext_status") == args.only_status
        }


if __name__ == "__main__":
    raise SystemExit(main())
