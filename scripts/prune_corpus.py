from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.paths import get_bib_dir, load_project_env  # noqa: E402


PAPERS_PATH = REPO_ROOT / "data/metadata/papers.tsv"
REPORT_PATH = REPO_ROOT / "outputs/curation/prune_corpus_report.tsv"
REMOVE_STATUSES = {"duplicate", "excluded_no_doi"}


def main() -> int:
    args = parse_args()
    load_project_env(REPO_ROOT / ".env")
    bib_dir = get_bib_dir().resolve()
    papers = read_tsv(PAPERS_PATH)
    fieldnames = list(papers[0].keys()) if papers else []
    explicit_paper_ids = set(args.paper_id) | set(read_paper_id_file(args.paper_id_file))

    removals: dict[str, dict[str, str]] = {}
    for row in papers:
        status = row.get("fulltext_status", "")
        if row.get("paper_id", "") in explicit_paper_ids:
            removals[row["paper_id"]] = removal_row(row, "manual_paper_id")
        elif status in REMOVE_STATUSES:
            removals[row["paper_id"]] = removal_row(row, f"status_{status}")
        elif args.remove_no_doi and not row.get("doi", "").strip():
            removals[row["paper_id"]] = removal_row(row, "missing_doi")

    if args.remove_title_year_duplicates:
        for row in title_year_duplicate_removals(papers):
            removals[row["paper_id"]] = row

    orphan_removals = no_doi_orphan_pdf_removals(bib_dir, papers) if args.remove_no_doi else []

    kept_papers = [row for row in papers if row.get("paper_id") not in removals]
    report_rows = list(removals.values()) + orphan_removals

    if args.apply:
        for row in report_rows:
            delete_pdf_if_present(bib_dir, row.get("local_pdf_relpath", ""))
            if row.get("paper_id"):
                delete_artifacts(row["paper_id"])
        write_tsv(PAPERS_PATH, kept_papers, fieldnames)
        remove_duplicate_archive_if_empty(bib_dir)
        write_tsv(REPORT_PATH, report_rows, report_fields())
    else:
        write_tsv(REPORT_PATH, report_rows, report_fields())

    print(f"Paper rows before: {len(papers)}")
    print(f"Paper rows to remove: {len(removals)}")
    print(f"No-DOI orphan PDFs to remove: {len(orphan_removals)}")
    print(f"Paper rows after: {len(kept_papers)}")
    print(f"Report: {REPORT_PATH.relative_to(REPO_ROOT)}")
    if not args.apply:
        print("Dry run: no PDFs, artifacts, or papers.tsv rows were removed. Re-run with --apply.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove unwanted local corpus rows and matching local PDF/artifact files.")
    parser.add_argument("--apply", action="store_true", help="Actually delete files and rewrite papers.tsv.")
    parser.add_argument("--paper-id", action="append", default=[], help="Explicit paper_id to remove. Can be repeated.")
    parser.add_argument("--paper-id-file", default="", help="TSV or plain text file containing paper_id values to remove.")
    parser.add_argument("--remove-no-doi", action="store_true", default=True)
    parser.add_argument("--remove-title-year-duplicates", action="store_true", default=True)
    return parser.parse_args()


def read_paper_id_file(path_text: str) -> list[str]:
    if not path_text:
        return []
    path = Path(path_text)
    path = path if path.is_absolute() else REPO_ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"paper id file not found: {path}")
    rows = read_tsv(path) if path.suffix.lower() == ".tsv" else []
    if rows and "paper_id" in rows[0]:
        return [row["paper_id"].strip() for row in rows if row.get("paper_id", "").strip()]
    paper_ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip().split("\t", 1)[0]
        if value and value != "paper_id" and not value.startswith("#"):
            paper_ids.append(value)
    return paper_ids


def title_year_duplicate_removals(papers: list[dict[str, str]]) -> list[dict[str, str]]:
    duplicate_path = REPO_ROOT / "bib/keys/duplicate_candidates.tsv"
    if not duplicate_path.exists():
        return []
    duplicates = [
        row
        for row in read_tsv(duplicate_path)
        if row.get("duplicate_type") == "title_year" and row.get("local_pdf_relpath")
    ]
    if not duplicates:
        return []
    papers_by_relpath = {row.get("local_pdf_relpath", ""): row for row in papers}
    grouped: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    for row in duplicates:
        grouped[row.get("value", "")].append(row)

    removals = []
    for group in grouped.values():
        enriched = [papers_by_relpath.get(row["local_pdf_relpath"]) for row in group]
        candidates = [row for row in enriched if row]
        if len(candidates) < 2:
            continue
        keeper = sorted(candidates, key=keeper_sort_key)[0]
        for row in candidates:
            if row.get("paper_id") == keeper.get("paper_id"):
                continue
            removals.append(removal_row(row, f"title_year_duplicate_keep:{keeper.get('paper_id', '')}"))
    return removals


def keeper_sort_key(row: dict[str, str]) -> tuple[int, int, int, str]:
    doi = normalize_doi(row.get("doi", ""))
    preprint_penalty = 1 if is_preprint_doi(doi) else 0
    version_bonus = -doi_version(doi)
    suffix_penalty = 1 if re.search(r"_\d+\.pdf$", row.get("local_pdf_relpath", "")) else 0
    return (preprint_penalty, version_bonus, suffix_penalty, row.get("paper_id", ""))


def no_doi_orphan_pdf_removals(bib_dir: Path, papers: list[dict[str, str]]) -> list[dict[str, str]]:
    registry_path = REPO_ROOT / "data/metadata/paper_registry.tsv"
    if not registry_path.exists():
        return []
    paper_paths = {row.get("local_pdf_relpath", "") for row in papers}
    removals = []
    for row in read_tsv(registry_path):
        relpath = row.get("local_pdf_relpath", "")
        if not relpath or relpath in paper_paths:
            continue
        if row.get("doi", "").strip():
            continue
        if not (bib_dir / relpath).exists():
            continue
        removals.append(
            {
                "paper_id": "",
                "reason": "no_doi_orphan_pdf_not_in_papers_tsv",
                "fulltext_status": "",
                "doi": "",
                "title": row.get("title", ""),
                "local_pdf_relpath": relpath,
            }
        )
    return removals


def removal_row(row: dict[str, str], reason: str) -> dict[str, str]:
    return {
        "paper_id": row.get("paper_id", ""),
        "reason": reason,
        "fulltext_status": row.get("fulltext_status", ""),
        "doi": row.get("doi", ""),
        "title": row.get("title", ""),
        "local_pdf_relpath": row.get("local_pdf_relpath", ""),
    }


def delete_pdf_if_present(bib_dir: Path, relpath: str) -> None:
    if not relpath:
        return
    path = (bib_dir / relpath).resolve()
    try:
        path.relative_to(bib_dir)
    except ValueError:
        raise ValueError(f"Refusing to delete outside bib dir: {path}") from None
    if path.exists() and path.is_file():
        path.unlink()


def delete_artifacts(paper_id: str) -> None:
    for path in [
        REPO_ROOT / f"data/extracted_text/{paper_id}.json",
        REPO_ROOT / f"data/chunks/{paper_id}.jsonl",
    ]:
        if path.exists() and path.is_file():
            path.unlink()


def remove_duplicate_archive_if_empty(bib_dir: Path) -> None:
    archive_dir = bib_dir / "keys/duplicate_archive"
    if archive_dir.exists() and not any(archive_dir.rglob("*.pdf")):
        shutil.rmtree(archive_dir)
    manifest = bib_dir / "keys/duplicate_archive_manifest.tsv"
    if manifest.exists():
        manifest.unlink()


def is_preprint_doi(doi: str) -> bool:
    return doi.startswith(("10.1101/", "10.21203/", "10.2139/"))


def doi_version(doi: str) -> int:
    match = re.search(r"\.(\d+)$", doi)
    return int(match.group(1)) if match else 0


def normalize_doi(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value)
    return value


def report_fields() -> list[str]:
    return ["paper_id", "reason", "fulltext_status", "doi", "title", "local_pdf_relpath"]


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def write_tsv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


if __name__ == "__main__":
    raise SystemExit(main())
