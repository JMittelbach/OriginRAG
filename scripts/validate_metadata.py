from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.paths import OriginRAGConfigError, load_project_env, resolve_bib_path  # noqa: E402
from pfempire.schema import PaperRecord  # noqa: E402


def main() -> int:
    load_project_env(REPO_ROOT / ".env")

    metadata_path = REPO_ROOT / "data/metadata/papers.tsv"
    if not metadata_path.exists():
        print(f"Missing metadata file: {metadata_path}")
        return 1

    rows = _read_tsv(metadata_path)
    valid_records: list[PaperRecord] = []
    validation_errors: list[str] = []
    missing_pdfs: list[str] = []
    unchecked_pdf_count = 0

    for row_number, row in enumerate(rows, start=2):
        try:
            record = PaperRecord(**row)
            valid_records.append(record)
        except Exception as exc:
            validation_errors.append(f"row {row_number}: {exc}")
            continue

        if record.local_pdf_relpath:
            try:
                pdf_path = resolve_bib_path(record.local_pdf_relpath)
            except OriginRAGConfigError:
                unchecked_pdf_count += 1
                continue
            except ValueError as exc:
                missing_pdfs.append(f"{record.paper_id}: {record.local_pdf_relpath} ({exc})")
                continue

            if not pdf_path.exists():
                missing_pdfs.append(f"{record.paper_id}: {record.local_pdf_relpath}")

    print("Metadata validation summary")
    print(f"- Records: {len(rows)}")
    print(f"- Valid records: {len(valid_records)}")
    print(f"- Validation errors: {len(validation_errors)}")
    print(f"- Missing referenced PDFs: {len(missing_pdfs)}")
    if unchecked_pdf_count:
        print(f"- PDF paths not checked because PFEMPIRE_BIB_DIR is unset: {unchecked_pdf_count}")

    if validation_errors:
        print()
        print("Validation errors:")
        for error in validation_errors:
            print(f"- {error}")

    if missing_pdfs:
        print()
        print("Missing referenced PDFs:")
        for missing_pdf in missing_pdfs:
            print(f"- {missing_pdf}")

    return 1 if validation_errors else 0


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [dict(row) for row in reader]


if __name__ == "__main__":
    raise SystemExit(main())
