from __future__ import annotations

import argparse
import csv
import hashlib
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_PLACEHOLDER_AUTHOR_PATTERN = re.compile(r"(?i)^anna\s*import\d*$")


def is_placeholder_author(value: str) -> bool:
    if not value:
        return False
    normalized = re.sub(r"[\s_]+", "", value.strip())
    return bool(_PLACEHOLDER_AUTHOR_PATTERN.fullmatch(normalized))

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    import fitz
except ImportError:
    fitz = None

from originrag.paths import OriginRAGConfigError, get_bib_dir, load_project_env  # noqa: E402


FIELDNAMES = [
    "pdf_id",
    "abs_path",
    "relpath_from_bib",
    "filename",
    "stem",
    "size_mb",
    "modified_time",
    "guessed_author",
    "guessed_year",
    "guessed_title",
    "pdf_page_count",
    "pdf_metadata_title",
    "pdf_metadata_author",
    "first_page_text_sample",
    "text_sample_char_count",
    "pdf_read_status",
    "message",
]

EXCLUDED_INVENTORY_DIRS = {"keys", "_archive", "archive", "duplicate_archive"}


def main() -> int:
    args = parse_args()
    load_project_env(REPO_ROOT / ".env")

    try:
        bib_dir = get_bib_dir().resolve()
    except OriginRAGConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if not bib_dir.exists() or not bib_dir.is_dir():
        print(f"Configured PFEMPIRE_BIB_DIR is not a directory: {bib_dir}", file=sys.stderr)
        return 2

    output_path = resolve_repo_path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = [inventory_pdf(path, bib_dir) for path in iter_inventory_pdfs(bib_dir, include_archived=args.include_archived)]
    write_tsv(output_path, rows)
    print(f"Wrote {len(rows)} PDF inventory rows to {relative_output(output_path)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inventory local PDFs under PFEMPIRE_BIB_DIR.")
    parser.add_argument(
        "--out",
        default="data/metadata/local_pdf_inventory.tsv",
        help="Output TSV path.",
    )
    parser.add_argument(
        "--include-archived",
        action="store_true",
        help="Include PDFs under bib/keys or archive directories. Default is active library only.",
    )
    return parser.parse_args()


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def relative_output(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def iter_inventory_pdfs(bib_dir: Path, *, include_archived: bool) -> list[Path]:
    pdfs: list[Path] = []
    for path in sorted(bib_dir.rglob("*.pdf")):
        rel_parts = path.relative_to(bib_dir).parts
        if not include_archived and any(part in EXCLUDED_INVENTORY_DIRS for part in rel_parts[:-1]):
            continue
        pdfs.append(path)
    return pdfs


def inventory_pdf(path: Path, bib_dir: Path) -> dict[str, str]:
    relpath = path.resolve().relative_to(bib_dir).as_posix()
    stat = path.stat()
    guessed_author, guessed_year, guessed_title = guess_from_stem(path.stem)
    row = {
        "pdf_id": make_pdf_id(relpath),
        "abs_path": str(path.resolve()),
        "relpath_from_bib": relpath,
        "filename": path.name,
        "stem": path.stem,
        "size_mb": f"{stat.st_size / (1024 * 1024):.3f}",
        "modified_time": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "guessed_author": guessed_author,
        "guessed_year": guessed_year,
        "guessed_title": guessed_title,
        "pdf_page_count": "",
        "pdf_metadata_title": "",
        "pdf_metadata_author": "",
        "first_page_text_sample": "",
        "text_sample_char_count": "0",
        "pdf_read_status": "pdf_read_failed",
        "message": "",
    }

    if fitz is None:
        return inventory_pdf_with_cli_fallback(path, row)

    try:
        with fitz.open(path) as document:
            metadata = document.metadata or {}
            first_page_text = ""
            if document.page_count:
                first_page_text = clean_text(document.load_page(0).get_text("text"))
            sample = first_page_text[:2000]
            row.update(
                {
                    "pdf_page_count": str(document.page_count),
                    "pdf_metadata_title": clean_text(str(metadata.get("title") or "")),
                    "pdf_metadata_author": clean_text(str(metadata.get("author") or "")),
                    "first_page_text_sample": sample,
                    "text_sample_char_count": str(len(first_page_text)),
                    "pdf_read_status": "ok",
                    "message": "",
                }
            )
    except Exception as exc:  # noqa: BLE001 - inventory should keep failed PDFs
        row["message"] = str(exc)

    return row


def inventory_pdf_with_cli_fallback(path: Path, row: dict[str, str]) -> dict[str, str]:
    try:
        info = subprocess.run(
            ["pdfinfo", str(path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        text = subprocess.run(
            ["pdftotext", "-q", "-f", "1", "-l", "1", str(path), "-"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        metadata = parse_pdfinfo(info.stdout)
        first_page_text = clean_text(text.stdout or "")
        row.update(
            {
                "pdf_page_count": metadata.get("Pages", ""),
                "pdf_metadata_title": clean_text(metadata.get("Title", "")),
                "pdf_metadata_author": clean_text(metadata.get("Author", "")),
                "first_page_text_sample": first_page_text[:2000],
                "text_sample_char_count": str(len(first_page_text)),
                "pdf_read_status": "ok",
                "message": "PyMuPDF unavailable; used pdfinfo/pdftotext fallback.",
            }
        )
    except Exception as exc:  # noqa: BLE001 - inventory should keep failed PDFs
        row["message"] = str(exc)
    return row


def parse_pdfinfo(output: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata


def guess_from_stem(stem: str) -> tuple[str, str, str]:
    match = re.match(r"^(?P<author>.+?)_(?P<year>\d{4})(?:_(?P<rest>.+))?$", stem)
    if not match:
        return "", "", stem.replace("_", " ")
    author = match.group("author")
    if is_placeholder_author(author):
        author = ""
    year = match.group("year")
    title = (match.group("rest") or "").replace("_", " ")
    return author, year, title


def make_pdf_id(relpath: str) -> str:
    digest = hashlib.sha1(relpath.encode("utf-8")).hexdigest()[:12]
    return f"PDF_{digest}"


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
