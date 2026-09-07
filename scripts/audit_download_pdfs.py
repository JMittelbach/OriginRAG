from __future__ import annotations

import argparse
import csv
import hashlib
import re
from pathlib import Path

try:
    import fitz
except ImportError:
    fitz = None


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOWNLOADS = Path.home() / "Downloads"
DEFAULT_BIB = REPO_ROOT / "bib/papers"
FIELDNAMES = [
    "source_path",
    "filename",
    "sha256",
    "status",
    "pdf_title",
    "pdf_author",
    "doi",
    "pages",
    "reason",
]


def main() -> int:
    args = parse_args()
    if fitz is None:
        print("PyMuPDF is required. Activate the pfempire environment first.")
        return 2
    source_dir = Path(args.downloads).expanduser().resolve()
    bib_dir = Path(args.bib_dir).expanduser().resolve()
    pdfs = sorted(source_dir.glob("*.pdf"))
    known_hashes = {sha256(path) for path in bib_dir.glob("*.pdf")}
    rows = [audit_pdf(path, known_hashes) for path in pdfs]
    output = Path(args.out).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Audited {len(rows)} PDFs from {source_dir}")
    for status in sorted({row["status"] for row in rows}):
        print(f"- {status}: {sum(row['status'] == status for row in rows)}")
    print(f"Report: {output}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit downloaded PDFs before corpus import.")
    parser.add_argument("--downloads", default=str(DEFAULT_DOWNLOADS))
    parser.add_argument("--bib-dir", default=str(DEFAULT_BIB))
    parser.add_argument("--out", default="outputs/pdf_import_audit/downloads.tsv")
    return parser.parse_args()


def audit_pdf(path: Path, known_hashes: set[str]) -> dict[str, str]:
    digest = sha256(path)
    row = {field: "" for field in FIELDNAMES}
    row.update({"source_path": str(path), "filename": path.name, "sha256": digest})
    if digest in known_hashes:
        row.update(status="duplicate", reason="same file content already exists in bib/papers")
        return row
    try:
        with fitz.open(path) as document:
            metadata = document.metadata or {}
            title = clean(metadata.get("title"))
            author = clean(metadata.get("author"))
            opening_text = clean(" ".join(document[index].get_text("text") for index in range(min(3, document.page_count))))
            combined = f"{title} {author} {opening_text[:12000]}"
            doi = extract_doi(combined)
            row.update(
                pdf_title=title,
                pdf_author=author,
                doi=doi,
                pages=str(document.page_count),
            )
            row["status"], row["reason"] = classify_document(path.name, title, author, opening_text, doi)
    except Exception as exc:  # noqa: BLE001 - audit should continue across bad files
        row.update(status="unreadable", reason=str(exc))
    return row


def classify_document(filename: str, title: str, author: str, first_page: str, doi: str) -> tuple[str, str]:
    haystack = f"{filename} {title} {author} {first_page}".lower()
    if any(term in haystack for term in ["mietvertrag", "wohnungsmietvertrag", "form leave"]):
        return "exclude_non_paper", "administrative or unrelated document"
    if "chatgpt" in haystack or "deep research" in haystack or "prepared as" in haystack:
        return "exclude_research_memo", "generated research memo, not a primary paper"
    if not any(term in haystack for term in ["malaria", "plasmodium", "pfemp", "var gene", "cytoadherence"]):
        return "exclude_out_of_scope", "scholarly-looking document is outside the malaria corpus scope"
    if not doi and len(first_page) < 180:
        return "review", "no DOI and too little first-page text for automatic classification"
    if doi:
        return "new_scholarly_candidate", "new DOI-bearing malaria PDF; review metadata before import"
    return "new_scholarly_candidate", "new malaria paper; DOI not present in the opening pages"


def extract_doi(text: str) -> str:
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, flags=re.IGNORECASE)
    return match.group(0).rstrip(".,;)") if match else ""


def clean(value: object) -> str:
    return " ".join(str(value or "").split())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
