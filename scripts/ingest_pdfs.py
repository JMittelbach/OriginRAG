from __future__ import annotations

import csv
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from originrag.ingestion.pdf_text import extract_pdf_text  # noqa: E402
from originrag.paths import load_project_env, resolve_bib_path  # noqa: E402
from originrag.schema import PaperRecord  # noqa: E402
from originrag.utils.io import write_json  # noqa: E402


def main() -> int:
    args = parse_args()
    load_project_env(REPO_ROOT / ".env")

    metadata_path = REPO_ROOT / "data/metadata/papers.tsv"
    output_dir = REPO_ROOT / "data/extracted_text"
    output_dir.mkdir(parents=True, exist_ok=True)

    successes = 0
    failures = 0
    skipped = 0
    selected = 0
    paper_id_filter = set(args.paper_id or [])

    for row_number, row in enumerate(_read_tsv(metadata_path), start=2):
        try:
            record = PaperRecord(**row)
        except Exception as exc:
            print(f"row {row_number}: validation failed: {exc}")
            failures += 1
            continue

        if paper_id_filter and record.paper_id not in paper_id_filter:
            skipped += 1
            continue
        if args.only_status and record.fulltext_status != args.only_status:
            skipped += 1
            continue
        if not record.local_pdf_relpath:
            skipped += 1
            continue
        output_path = output_dir / f"{record.paper_id}.json"
        if output_path.exists() and not args.overwrite:
            skipped += 1
            continue
        if args.limit and selected >= args.limit:
            skipped += 1
            continue

        try:
            pdf_path = resolve_bib_path(record.local_pdf_relpath)
            pages = extract_pdf_text(pdf_path)
            write_json(
                output_path,
                {
                    "paper_id": record.paper_id,
                    "source_pdf": record.local_pdf_relpath,
                    "pages": pages,
                },
            )
            print(f"ok: {record.paper_id} -> {output_path.relative_to(REPO_ROOT)}")
            successes += 1
            selected += 1
        except Exception as exc:
            print(f"failed: {record.paper_id}: {exc}")
            failures += 1

    print()
    print(f"Extraction complete: {successes} ok, {failures} failed, {skipped} skipped.")
    return 1 if failures else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract PDF text for selected papers.tsv rows.")
    parser.add_argument("--paper-id", action="append", help="Only ingest this paper_id. Repeatable.")
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum selected PDFs to ingest. Use 0 for no limit.",
    )
    parser.add_argument(
        "--only-status",
        default="pdf_available",
        help="Only ingest papers with this fulltext_status. Use an empty string to disable.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing extracted JSON files.")
    return parser.parse_args()


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [dict(row) for row in reader]


if __name__ == "__main__":
    raise SystemExit(main())
