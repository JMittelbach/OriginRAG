from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import shutil
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import fitz  # noqa: E402

from originrag.ingestion.chunking import chunk_pages  # noqa: E402
from originrag.ingestion.pdf_text import extract_pdf_text  # noqa: E402
from originrag.provenance.library import normalize_doi, normalize_title  # noqa: E402
from originrag.retrieval.fts_index import (  # noqa: E402
    RetrievalIndexError,
    build_fts_index,
    corpus_fingerprint,
    ensure_current_index,
    index_has_candidate_record_store,
    update_fts_index,
)
from originrag.utils.io import write_json, write_jsonl  # noqa: E402


MAX_PDF_BYTES = 250 * 1024 * 1024
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
CROSSREF_URL = "https://api.crossref.org/works"
USER_AGENT = "OriginRAG/0.1 (local literature import)"
REPORT_FIELDS = (
    "source_file",
    "status",
    "reason",
    "paper_id",
    "bib_key",
    "doi",
    "title",
    "author",
    "year",
    "journal",
    "category",
    "local_pdf_relpath",
    "chunk_count",
)


@dataclass(frozen=True)
class Candidate:
    source: Path
    sha256: str
    doi: str
    title: str
    author: str
    year: str
    journal: str
    category: str
    paper_id: str
    bib_key: str
    target_relpath: str
    pages: list[dict]
    chunks: list[dict]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate, deduplicate, name, ingest, and index DOI-identified PDFs from "
            "bib/inbox. Stop the web application before running this command."
        )
    )
    parser.add_argument("--inbox", type=Path, default=REPO_ROOT / "bib/inbox")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--recover-imports-only",
        action="store_true",
        help=(
            "Restore metadata for accepted web imports whose PDFs/chunks survived a "
            "product update. This is idempotent and does not process the inbox."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.recover_imports_only:
        result = recover_imported_metadata(timeout=max(1, args.timeout))
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return 0 if not result["conflicts"] else 2
    result = import_inbox(args.inbox.resolve(), timeout=max(1, args.timeout))
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result["imported"] or result["scanned"] == 0 else 2


def import_inbox(
    inbox: Path,
    *,
    timeout: int = 30,
    progress: Callable[[dict[str, object]], None] | None = None,
) -> dict:
    inbox.mkdir(parents=True, exist_ok=True)
    if any(path.is_symlink() for path in inbox.iterdir()):
        raise ValueError("Symlinks are not allowed in the paper inbox.")
    sources = sorted(path for path in inbox.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
    batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    batch_dir = REPO_ROOT / "runtime/imports" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)

    papers_path = REPO_ROOT / "data/metadata/papers.tsv"
    registry_path = REPO_ROOT / "data/metadata/paper_registry.tsv"
    chunks_dir = REPO_ROOT / "data/chunks"
    extracted_dir = REPO_ROOT / "data/extracted_text"
    index_path = REPO_ROOT / "data/indexes/pfempire_fts.sqlite"
    bib_dir = REPO_ROOT / "bib"
    previous_corpus_fingerprint = corpus_fingerprint(chunks_dir, papers_path)
    paper_rows, paper_fields = read_tsv(papers_path)
    registry_rows, registry_fields = read_tsv(registry_path)
    metadata_only = metadata_only_records(paper_rows + registry_rows)
    upgrade_ids = {row["paper_id"] for row in metadata_only.values()}
    known_dois = {normalize_doi(row.get("doi")) for row in registry_rows if normalize_doi(row.get("doi"))} - metadata_only.keys()
    known_titles = {normalize_title(row.get("title")) for row in registry_rows if row.get("title") and row.get("paper_id") not in upgrade_ids}
    known_ids = {row.get("paper_id", "") for row in paper_rows + registry_rows}
    known_keys = {row.get("bib_key", "") for row in registry_rows}
    known_hashes = load_known_pdf_hashes()
    reports: list[dict[str, str]] = []
    accepted: list[Candidate] = []
    duplicates: list[tuple[Path, str]] = []
    review_files: list[tuple[Path, str]] = []
    rejected_files: list[tuple[Path, str]] = []

    notify_progress(
        progress,
        stage="Checking uploaded papers",
        total=len(sources),
        processed=0,
        imported=0,
        duplicates=0,
    )

    for position, source in enumerate(sources, start=1):
        display_name = display_source_name(source)
        notify_progress(
            progress,
            stage="Checking PDF metadata and duplicates",
            total=len(sources),
            processed=position - 1,
            current_file=display_name,
            imported=len(accepted),
            duplicates=len(duplicates),
        )
        try:
            candidate = inspect_candidate(
                source,
                timeout=timeout,
                known_hashes=known_hashes,
                known_dois=known_dois,
                known_titles=known_titles,
                known_ids=known_ids,
                known_keys=known_keys,
                metadata_only=metadata_only,
            )
        except DuplicatePaper as exc:
            duplicates.append((source, str(exc)))
            reports.append(report_row(source, status="duplicate", reason=str(exc)))
            continue
        except ReviewRequired as exc:
            review_files.append((source, str(exc)))
            reports.append(report_row(source, status="review_required", reason=str(exc)))
            continue
        except Exception as exc:
            rejected_files.append((source, str(exc)))
            reports.append(report_row(source, status="rejected", reason=str(exc)))
            continue
        accepted.append(candidate)
        known_hashes.add(candidate.sha256)
        known_dois.add(candidate.doi)
        known_titles.add(normalize_title(candidate.title))
        known_ids.add(candidate.paper_id)
        known_keys.add(candidate.bib_key)
        reports.append(
            report_row(
                source,
                status="accepted",
                paper_id=candidate.paper_id,
                bib_key=candidate.bib_key,
                doi=candidate.doi,
                title=candidate.title,
                author=candidate.author,
                year=candidate.year,
                journal=candidate.journal,
                category=candidate.category,
                local_pdf_relpath=candidate.target_relpath,
                chunk_count=str(len(candidate.chunks)),
            )
        )
        notify_progress(
            progress,
            stage="Prepared paper for library import",
            total=len(sources),
            processed=position,
            current_file=display_name,
            imported=len(accepted),
            duplicates=len(duplicates),
        )

    for source, _reason in duplicates:
        archive_submission(source, batch_dir, "duplicates")
    for source, _reason in review_files:
        archive_submission(source, batch_dir, "review_required")
    for source, _reason in rejected_files:
        archive_submission(source, batch_dir, "rejected")

    if accepted:
        original_papers = papers_path.read_bytes()
        original_registry = registry_path.read_bytes()
        bibtex_path = resolve_bibtex_path(bib_dir)
        original_bibtex = bibtex_path.read_bytes() if bibtex_path.exists() else None
        installed_paths: list[Path] = []
        moved_sources: list[tuple[Path, Path]] = []
        try:
            for candidate in accepted:
                upsert_import_row(paper_rows, paper_metadata_row(candidate, paper_fields))
                upsert_import_row(registry_rows, registry_metadata_row(candidate, registry_fields))
            write_tsv_atomic(papers_path, paper_rows, paper_fields)
            write_tsv_atomic(registry_path, registry_rows, registry_fields)
            write_bibtex_atomic(
                bibtex_path,
                original_bibtex.decode("utf-8") if original_bibtex is not None else "",
                accepted,
            )
            for candidate in accepted:
                pdf_target = bib_dir / candidate.target_relpath
                extracted_target = extracted_dir / f"{candidate.paper_id}.json"
                chunks_target = chunks_dir / f"{candidate.paper_id}.jsonl"
                for target in (pdf_target, extracted_target, chunks_target):
                    if target.exists():
                        raise FileExistsError(f"Refusing to overwrite product asset: {target}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(candidate.source), str(pdf_target))
                moved_sources.append((pdf_target, candidate.source))
                write_json(
                    extracted_target,
                    {
                        "paper_id": candidate.paper_id,
                        "source_pdf": candidate.target_relpath,
                        "pages": candidate.pages,
                    },
                )
                write_jsonl(chunks_target, candidate.chunks)
                installed_paths.extend([extracted_target, chunks_target])
            notify_progress(
                progress,
                stage="Adding papers to the searchable index",
                total=len(sources),
                processed=len(sources),
                imported=len(accepted),
                duplicates=len(duplicates),
            )
            try:
                update_fts_index(
                    chunks_dir,
                    papers_path,
                    [candidate.paper_id for candidate in accepted],
                    index_path,
                    expected_previous_fingerprint=previous_corpus_fingerprint,
                )
            except RetrievalIndexError:


                notify_progress(
                    progress,
                    stage="Rebuilding the searchable library index",
                    total=len(sources),
                    processed=len(sources),
                    imported=len(accepted),
                    duplicates=len(duplicates),
                )
                build_fts_index(chunks_dir, papers_path, index_path)
            try:
                save_known_pdf_hashes(known_hashes)
                for candidate in accepted:
                    archive_sidecar(candidate.source, batch_dir)
            except OSError as exc:
                print(f"Warning: non-critical import bookkeeping failed: {exc}", file=sys.stderr)
        except Exception:
            papers_path.write_bytes(original_papers)
            registry_path.write_bytes(original_registry)
            if original_bibtex is None:
                bibtex_path.unlink(missing_ok=True)
            else:
                bibtex_path.write_bytes(original_bibtex)
            for path in reversed(installed_paths):
                path.unlink(missing_ok=True)
            for installed, original in reversed(moved_sources):
                if installed.exists():
                    original.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(installed), str(original))
            raise

    report_path = batch_dir / "import_report.tsv"
    write_tsv_atomic(report_path, reports, list(REPORT_FIELDS))
    summary = {
        "schema_version": "pfempire_paper_inbox_v1",
        "batch_id": batch_id,
        "inbox": str(inbox),
        "scanned": len(sources),
        "imported": len(accepted),
        "duplicates": len(duplicates),
        "review_required": sum(row["status"] == "review_required" for row in reports),
        "rejected": sum(row["status"] == "rejected" for row in reports),
        "report": str(report_path),
        "results": [
            {
                key: row.get(key, "")
                for key in (
                    "source_file",
                    "status",
                    "reason",
                    "paper_id",
                    "bib_key",
                    "doi",
                    "title",
                    "author",
                    "year",
                    "journal",
                    "category",
                    "local_pdf_relpath",
                )
            }
            for row in reports
        ],
    }
    (batch_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    notify_progress(
        progress,
        stage="Library update complete",
        total=len(sources),
        processed=len(sources),
        imported=len(accepted),
        duplicates=len(duplicates),
        review_required=summary["review_required"],
        rejected=summary["rejected"],
        batch_id=batch_id,
    )
    return summary


def recover_imported_metadata(*, timeout: int = 5) -> dict:
    """Recover accepted web imports after an in-place product update.

    Upload audit reports below ``runtime/imports`` are preserved across
    deployments. A product rsync can replace the aggregate metadata tables
    while leaving user PDFs and chunks intact. Extracted-text JSON files are
    optional because the deployed product intentionally omits them. Restore
    only accepted identities with a PDF and searchable chunks; conflicts remain
    visible and cause a non-zero CLI result.
    """

    papers_path = REPO_ROOT / "data/metadata/papers.tsv"
    registry_path = REPO_ROOT / "data/metadata/paper_registry.tsv"
    chunks_dir = REPO_ROOT / "data/chunks"
    index_path = REPO_ROOT / "data/indexes/pfempire_fts.sqlite"
    bib_dir = REPO_ROOT / "bib"
    bibtex_path = resolve_bibtex_path(bib_dir)
    report_paths = sorted((REPO_ROOT / "runtime/imports").glob("*/import_report.tsv"))

    original_papers = papers_path.read_bytes()
    original_registry = registry_path.read_bytes()
    original_bibtex = bibtex_path.read_bytes() if bibtex_path.exists() else None
    paper_rows, paper_fields = read_tsv(papers_path)
    registry_rows, registry_fields = read_tsv(registry_path)
    papers_by_id = {str(row.get("paper_id") or ""): row for row in paper_rows}
    registry_by_id = {str(row.get("paper_id") or ""): row for row in registry_rows}
    doi_to_id = {}
    for row in paper_rows + registry_rows:
        normalized = normalize_doi(row.get("doi"))
        if normalized:
            doi_to_id.setdefault(normalized, str(row.get("paper_id") or ""))
    known_keys = {str(row.get("bib_key") or "") for row in registry_rows}
    accepted_by_id: dict[str, dict[str, str]] = {}
    for report_path in report_paths:
        rows, _fields = read_tsv(report_path)
        for row in rows:
            paper_id = str(row.get("paper_id") or "").strip()
            if row.get("status") == "accepted" and paper_id:
                accepted_by_id[paper_id] = row

    recovered: list[dict[str, str]] = []
    skipped: list[str] = []
    conflicts: list[dict[str, str]] = []
    bibtex_candidates: list[Candidate] = []
    metadata_changed = False
    for paper_id, report in sorted(accepted_by_id.items()):
        existing_paper = papers_by_id.get(paper_id)
        existing_registry = registry_by_id.get(paper_id)

        identity_dois = {
            normalized
            for value in (
                (existing_registry or {}).get("doi"),
                (existing_paper or {}).get("doi"),
                report.get("doi"),
            )
            if (normalized := normalize_doi(value))
        }
        if len(identity_dois) > 1:
            conflicts.append(
                {
                    "paper_id": paper_id,
                    "reason": (
                        "inconsistent DOI identity for the same paper_id: "
                        + ", ".join(sorted(identity_dois))
                    ),
                }
            )
            continue
        doi = next(iter(identity_dois), "")
        conflicting_id = doi_to_id.get(doi, "") if doi else ""
        if conflicting_id and conflicting_id != paper_id:
            conflicts.append(
                {
                    "paper_id": paper_id,
                    "reason": f"DOI already belongs to {conflicting_id}",
                }
            )
            continue

        local_pdf_relpath = str(
            (existing_registry or {}).get("local_pdf_relpath")
            or (existing_paper or {}).get("local_pdf_relpath")
            or report.get("local_pdf_relpath")
            or ""
        ).strip()
        pdf_path = bib_dir / local_pdf_relpath
        chunk_path = chunks_dir / f"{paper_id}.jsonl"
        missing = [
            label
            for label, path in (
                ("PDF", pdf_path),
                ("chunks", chunk_path),
            )
            if not path.is_file()
        ]
        if not local_pdf_relpath or missing:
            reason = (
                "missing local_pdf_relpath"
                if not local_pdf_relpath
                else "missing assets: " + ", ".join(missing)
            )
            conflicts.append({"paper_id": paper_id, "reason": reason})
            continue

        title = clean_text(
            (existing_registry or {}).get("title")
            or (existing_paper or {}).get("title")
            or report.get("title")
        )
        author = clean_text(
            (existing_registry or {}).get("author")
            or (existing_paper or {}).get("author")
            or report.get("author")
        )
        year = clean_year(
            (existing_registry or {}).get("year")
            or (existing_paper or {}).get("year")
            or report.get("year")
        )
        journal = clean_text(
            (existing_registry or {}).get("journal")
            or (existing_paper or {}).get("journal")
            or report.get("journal")
        )
        if doi and not all((title, author, year, journal)):
            crossref = fetch_crossref(doi, timeout=timeout) or {}
            title = title or clean_text(crossref.get("title"))
            author = author or clean_text(crossref.get("author"))
            year = year or clean_year(crossref.get("year"))
            journal = journal or clean_text(crossref.get("journal"))
        author = author or "Unknown"
        year = year or "nd"
        category = clean_text(
            (existing_registry or {}).get("category")
            or (existing_paper or {}).get("topics")
            or report.get("category")
        ) or category_from_import_path(local_pdf_relpath)
        bib_key = clean_text(
            (existing_registry or {}).get("bib_key")
            or (existing_paper or {}).get("bib_key")
            or report.get("bib_key")
        )
        if not bib_key:
            bib_key = unique_token(make_bib_key(author, year, title), known_keys)
        known_keys.add(bib_key)

        candidate = Candidate(
            source=pdf_path,
            sha256=sha256_file(pdf_path),
            doi=doi,
            title=title,
            author=author,
            year=year,
            journal=journal,
            category=category,
            paper_id=paper_id,
            bib_key=bib_key,
            target_relpath=local_pdf_relpath,
            pages=[],
            chunks=[],
        )
        if not existing_paper:
            new_paper = paper_metadata_row(candidate, paper_fields)
            paper_rows.append(new_paper)
            papers_by_id[paper_id] = new_paper
            metadata_changed = True
        elif "bib_key" in paper_fields and not existing_paper.get("bib_key"):
            existing_paper["bib_key"] = bib_key
            metadata_changed = True
        if not existing_registry:
            new_registry = registry_metadata_row(candidate, registry_fields)
            registry_rows.append(new_registry)
            registry_by_id[paper_id] = new_registry
            if doi:
                doi_to_id[doi] = paper_id
            metadata_changed = True
        elif "bib_key" in registry_fields and not existing_registry.get("bib_key"):
            existing_registry["bib_key"] = bib_key
            metadata_changed = True
        if existing_paper and existing_registry:
            skipped.append(paper_id)
        else:
            recovered.append(
                {
                    "paper_id": paper_id,
                    "doi": doi,
                    "title": title,
                    "category": category,
                    "local_pdf_relpath": local_pdf_relpath,
                }
            )
        bibtex_candidates.append(candidate)

    existing_bibtex = (
        original_bibtex.decode("utf-8") if original_bibtex is not None else ""
    )
    missing_bibtex = [
        candidate
        for candidate in bibtex_candidates
        if not bibtex_contains_candidate(existing_bibtex, candidate)
    ]
    index_rebuilt = False
    try:
        if metadata_changed:
            write_tsv_atomic(papers_path, paper_rows, paper_fields)
            write_tsv_atomic(registry_path, registry_rows, registry_fields)
        write_bibtex_atomic(bibtex_path, existing_bibtex, missing_bibtex)
        if metadata_changed:
            build_fts_index(chunks_dir, papers_path, index_path)
            index_rebuilt = True
        else:
            try:
                ensure_current_index(index_path, chunks_dir, papers_path)
            except RetrievalIndexError:
                build_fts_index(chunks_dir, papers_path, index_path)
                index_rebuilt = True
        candidate_record_store_ready = bool(
            index_rebuilt
            or (
                index_path.is_file()
                and index_has_candidate_record_store(
                    index_path,
                    chunks_dir=chunks_dir,
                    papers_tsv=papers_path,
                )
            )
        )
    except Exception:
        restore_file_atomic(papers_path, original_papers)
        restore_file_atomic(registry_path, original_registry)
        restore_file_atomic(bibtex_path, original_bibtex)
        index_path.with_suffix(index_path.suffix + ".building").unlink(missing_ok=True)
        raise

    result = {
        "schema_version": "pfempire_import_recovery_v1",
        "reports_scanned": len(report_paths),
        "accepted_imports_seen": len(accepted_by_id),
        "recovered": recovered,
        "recovered_count": len(recovered),
        "already_present_count": len(skipped),
        "bibtex_recovered": [candidate.paper_id for candidate in missing_bibtex],
        "bibtex_recovered_count": len(missing_bibtex),
        "index_rebuilt": index_rebuilt,
        "candidate_record_store_ready": candidate_record_store_ready,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
    }
    summary_path = REPO_ROOT / "runtime/imports/recovery_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def category_from_import_path(local_pdf_relpath: str) -> str:
    """Recover the importer taxonomy from its deterministic target folder."""

    normalized = Path(local_pdf_relpath).as_posix().lower()
    prefixes = (
        ("papers/malaria/var_genes/", "var_pfemp1"),
        ("papers/malaria/host_omics/", "malaria_host_omics"),
        ("papers/malaria/host_genetics/", "malaria_host_genetics"),
        ("papers/malaria/resistance_diagnostics/", "resistance_dx"),
        ("papers/malaria/general_biology/", "general_malaria"),
        ("papers/immunology/", "host_immunity"),
        ("papers/single_cell/annotation/", "scrna_annotation"),
        ("papers/single_cell/harmonization_integration/", "sc_harmonization"),
        ("papers/single_cell/tools_methods/", "sc_tools"),
        ("papers/host_biology/covid_19/", "covid_host_response"),
        ("papers/host_biology/tissue_atlases/", "host_tissue_atlas"),
        ("papers/host_biology/immunology/", "host_immunity_general"),
        ("papers/host_biology/infectious_diseases/", "infectious_disease_host_response"),
        ("papers/host_biology/noninfectious_diseases/", "noninfectious_disease_biology"),
        ("papers/methods/general/", "general_methods"),
        ("papers/other/", "other"),
    )
    for prefix, category in prefixes:
        if normalized.startswith(prefix):
            return category
    topic_match = re.match(r"papers/topics/([^/]+)/", normalized)
    return f"topic_{topic_match.group(1)}" if topic_match else "other"


def metadata_only_records(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    by_doi: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        doi = normalize_doi(row.get("doi"))
        if doi:
            by_doi.setdefault(doi, []).append(row)
    eligible = {}
    for doi, entries in by_doi.items():
        ids = {row.get("paper_id", "") for row in entries}
        if len(ids) != 1:
            continue
        paper_id = next(iter(ids))
        if not re.fullmatch(r"[A-Za-z0-9_-]+", paper_id):
            continue
        assets = [REPO_ROOT / "data/chunks" / f"{paper_id}.jsonl", REPO_ROOT / "data/extracted_text" / f"{paper_id}.json"]
        assets.extend(REPO_ROOT / "bib" / row["local_pdf_relpath"] for row in entries if row.get("local_pdf_relpath"))
        if any(path.exists() for path in assets):
            continue
        eligible[doi] = {key: value for row in entries for key, value in row.items() if value}
    return eligible


def upsert_import_row(rows: list[dict[str, str]], update: dict[str, str]) -> None:
    existing = next((row for row in rows if row.get("paper_id") == update["paper_id"]), None)
    if existing is None:
        rows.append(update)
    else:
        for key, value in update.items():
            if value:
                if key == "notes" and existing.get(key):
                    value = existing[key] + "; " + value
                existing[key] = value


def inspect_candidate(
    source: Path,
    *,
    timeout: int,
    known_hashes: set[str],
    known_dois: set[str],
    known_titles: set[str],
    known_ids: set[str],
    known_keys: set[str],
    metadata_only: dict[str, dict[str, str]] | None = None,
) -> Candidate:
    if source.stat().st_size > MAX_PDF_BYTES:
        raise ValueError("PDF exceeds the 250 MB inbox limit")
    with source.open("rb") as handle:
        signature = handle.read(5)
    if signature != b"%PDF-":
        raise ValueError("file does not have a PDF signature")
    digest = sha256_file(source)
    if digest in known_hashes:
        raise DuplicatePaper("identical PDF content already exists in the product library")
    with fitz.open(source) as document:
        if document.needs_pass:
            raise ValueError("password-protected PDFs cannot be ingested")
        if document.page_count < 1:
            raise ValueError("PDF has no pages")
        metadata = document.metadata or {}
        sample = "\n".join(
            document[index].get_text("text") for index in range(min(4, document.page_count))
        )
    if len(sample.strip()) < 500:
        raise ReviewRequired("too little machine-readable article text; OCR or manual review is required")
    sidecar_doi = read_doi_sidecar(source)
    doi = normalize_doi(sidecar_doi or first_doi(f"{metadata.get('subject', '')}\n{sample}"))
    if not doi:
        raise ReviewRequired(
            "no DOI was found; add a sidecar named PAPER.pdf.doi.txt containing the DOI"
        )
    if doi in known_dois:
        raise DuplicatePaper(f"DOI already exists in the product registry: {doi}")
    crossref = fetch_crossref(doi, timeout=timeout)
    if not crossref:
        raise ReviewRequired("exact DOI metadata could not be verified through Crossref")
    title = clean_text(crossref.get("title"))
    author = clean_text(crossref.get("author")) or "Unknown"
    year = clean_year(crossref.get("year")) or "nd"
    journal = clean_text(crossref.get("journal"))
    if not title:
        raise ReviewRequired("verified DOI record has no usable title")
    normalized_title = normalize_title(title)
    if normalized_title in known_titles:
        raise DuplicatePaper("normalized title already exists in the product registry")
    category = classify_category(
        f"{title} {sample[:8000]}",
        subjects=list(crossref.get("subjects") or []),
    )
    paper_id = unique_token(f"USR_{digest[:12].upper()}", known_ids)
    bib_key = unique_token(make_bib_key(author, year, title), known_keys)
    existing = (metadata_only or {}).get(doi)
    if existing:
        paper_id = existing["paper_id"]
        bib_key = existing.get("bib_key") or bib_key
    filename = safe_filename(f"{author}{year}{' '.join(title.split()[:6])}") + ".pdf"
    target_relpath = unique_pdf_relpath(category, filename)
    pages = extract_pdf_text(source)
    chunks = chunk_pages(paper_id, pages)
    if not chunks:
        raise ReviewRequired("no searchable text chunks could be produced")
    return Candidate(
        source=source,
        sha256=digest,
        doi=doi,
        title=title,
        author=author,
        year=year,
        journal=journal,
        category=category,
        paper_id=paper_id,
        bib_key=bib_key,
        target_relpath=target_relpath,
        pages=pages,
        chunks=chunks,
    )


class ReviewRequired(ValueError):
    pass


class DuplicatePaper(ReviewRequired):
    """The uploaded work is already represented in the active library."""

    pass


def fetch_crossref(doi: str, *, timeout: int) -> dict[str, object] | None:
    url = f"{CROSSREF_URL}/{urllib.parse.quote(doi, safe='')}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    item = payload.get("message") or {}
    if not isinstance(item, dict) or normalize_doi(item.get("DOI")) != doi:
        return None
    authors = item.get("author") or []
    first_author = authors[0] if isinstance(authors, list) and authors else {}
    author = first_author.get("family") or first_author.get("name") if isinstance(first_author, dict) else ""
    return {
        "title": first_list_value(item.get("title")),
        "author": str(author or ""),
        "year": crossref_year(item),
        "journal": first_list_value(item.get("container-title")),
        "subjects": [clean_text(value) for value in item.get("subject") or [] if clean_text(value)],
    }


def paper_metadata_row(candidate: Candidate, fields: list[str]) -> dict[str, str]:
    values = {
        "paper_id": candidate.paper_id,
        "bib_key": candidate.bib_key,
        "author": candidate.author,
        "title": candidate.title,
        "year": candidate.year if candidate.year != "nd" else "",
        "journal": candidate.journal,
        "doi": candidate.doi,
        "domain": paper_domain(candidate.category),
        "topics": candidate.category,
        "paper_type": "journal article",
        "fulltext_status": "chunked",
        "local_pdf_relpath": candidate.target_relpath,
        "extracted_text_relpath": f"data/extracted_text/{candidate.paper_id}.json",
        "notes": "local_inbox; DOI metadata verified through Crossref",
    }
    return {field: values.get(field, "") for field in fields}


def registry_metadata_row(candidate: Candidate, fields: list[str]) -> dict[str, str]:
    values = {
        "bib_key": candidate.bib_key,
        "paper_id": candidate.paper_id,
        "collection": paper_collection(candidate.category),
        "category": candidate.category,
        "author": candidate.author,
        "year": candidate.year if candidate.year != "nd" else "",
        "title": candidate.title,
        "journal": candidate.journal,
        "doi": candidate.doi,
        "landing_url": f"https://doi.org/{candidate.doi}",
        "local_pdf_relpath": candidate.target_relpath,
        "filename": Path(candidate.target_relpath).name,
        "citation_short": f"{candidate.author} et al., {candidate.year}",
        "citation_apa": f"{candidate.author} et al. ({candidate.year}). {candidate.title}. {candidate.journal}. https://doi.org/{candidate.doi}",
        "metadata_source": "local_inbox;crossref_exact_doi",
        "notes": "DOI identity verified automatically; source/origin relations remain unreviewed",
    }
    return {field: values.get(field, "") for field in fields}


def classify_category(text: str, *, subjects: list[str] | None = None) -> str:
    value = clean_text(text).lower()
    malaria = any(term in value for term in ("malaria", "plasmodium", "pfemp1", "var gene"))
    host_context = any(
        term in value
        for term in (
            "host response",
            "host inflammatory",
            "host transcript",
            "blood transcript",
            "patient",
            "children",
            "adult cohort",
            "pbmc",
            "whole blood",
            "whole-blood",
            "immune cell",
            "monocyte",
            "lymphocyte",
            "host genetic",
            "human genetic",
        )
    )
    if malaria and any(term in value for term in ("pfemp1", "var gene", "cytoadherence")):
        return "var_pfemp1"
    if malaria and any(
        term in value
        for term in (
            "antimalarial",
            "anti-malarial",
            "artemisinin",
            "cipargamin",
            "drug resistance",
            "pfcrt",
            "pfhrp",
            "piperaquine",
            "diagnostic",
        )
    ):
        return "resistance_dx"
    if malaria and host_context and any(
        term in value
        for term in (
            "transcriptom",
            "single-cell",
            "single cell",
            "proteom",
            "metabolom",
            "multi-omic",
            "rna-seq",
        )
    ):
        return "malaria_host_omics"
    if malaria and host_context and any(
        term in value
        for term in (
            "genome-wide association",
            "host genetic",
            "human genetic",
            "host polymorphism",
            "human polymorphism",
            "sickle",
            "thalassaemia",
            "susceptibility locus",
        )
    ):
        return "malaria_host_genetics"
    if malaria and any(term in value for term in ("immune", "immunity", "antibody", "vaccine")):
        return "host_immunity"
    if malaria:
        return "general_malaria"
    if any(term in value for term in ("harmonization", "data integration", "label transfer")):
        return "sc_harmonization"
    if any(term in value for term in ("cell type annotation", "cell-type annotation", "cell classification")):
        return "scrna_annotation"
    single_cell_context = any(
        term in value
        for term in (
            "single-cell",
            "single cell",
            "spatial transcriptom",
            "scrna-seq",
        )
    )
    single_cell_tool = any(
        term in value
        for term in (
            "computational platform",
            "computational tool",
            "search algorithm",
            "search engine",
            "searchable corpus",
            "searchable index",
            "sequence search",
            "software platform",
        )
    )
    if single_cell_context and single_cell_tool:
        return "sc_tools"
    if any(
        term in value
        for term in (
            "benchmarking methods",
            "computational method",
            "experimental protocol",
            "analysis pipeline",
            "software framework",
            "bayesian model for",
            "algorithm for",
            "tool for",
            "workflow for",
            "assay for",
        )
    ):
        return "general_methods"
    covid = any(term in value for term in ("covid-19", "covid 19", "sars-cov-2"))
    host_immune = any(
        term in value
        for term in (
            "immune",
            "immunity",
            "myeloid",
            "monocyte",
            "lymphocyte",
            "inflammation",
            "host response",
        )
    )
    if covid:
        return "covid_host_response"
    if "tissue atlas" in value and any(
        term in value for term in ("fibroblast", "myeloid", "immune", "cell atlas")
    ):
        return "host_tissue_atlas"
    if (
        "human" in value
        and any(term in value for term in ("single-cell", "single cell", "scrna-seq"))
        and any(
            term in value
            for term in (
                "multi-tissue",
                "multi tissue",
                "multiple tissue",
                "reference atlas",
                "reference map",
            )
        )
    ):
        return "host_tissue_atlas"
    if host_immune and any(
        term in value
        for term in (
            "influenza",
            "tuberculosis",
            "hiv",
            "dengue",
            "ebola",
            "bacterial infection",
            "viral infection",
            "fungal infection",
            "infectious disease",
            "pathogen",
        )
    ):
        return "infectious_disease_host_response"
    if any(
        term in value
        for term in (
            "cancer",
            "tumor",
            "tumour",
            "arthritis",
            "autoimmune",
            "cardiovascular disease",
            "neurodegenerative",
        )
    ):
        return "noninfectious_disease_biology"
    if host_immune and any(term in value for term in ("disease", "clinical", "inflammation")):
        return "host_immunity_general"
    for subject in subjects or []:
        slug = safe_category_slug(subject)
        if slug:
            return f"topic_{slug}"
    return "other"


def paper_domain(category: str) -> str:
    if category in {
        "var_pfemp1",
        "host_immunity",
        "resistance_dx",
        "general_malaria",
        "malaria_host_omics",
        "malaria_host_genetics",
    }:
        return "malaria"
    if category in {"scrna_annotation", "sc_harmonization", "sc_tools"}:
        return "bioinformatics"
    if category in {
        "covid_host_response",
        "host_tissue_atlas",
        "host_immunity_general",
        "infectious_disease_host_response",
        "noninfectious_disease_biology",
    }:
        return "host_biology"
    if category == "general_methods":
        return "methods"
    return "general"


def paper_collection(category: str) -> str:
    if paper_domain(category) == "malaria":
        return "malaria"
    if paper_domain(category) == "bioinformatics":
        return "bioinformatics_tools"
    if paper_domain(category) == "host_biology":
        return "host_biology"
    if paper_domain(category) == "methods":
        return "methods"
    return "general_literature"


def unique_pdf_relpath(category: str, filename: str) -> str:
    directory = {
        "var_pfemp1": "papers/malaria/var_genes",
        "host_immunity": "papers/immunology",
        "resistance_dx": "papers/malaria/resistance_diagnostics",
        "general_malaria": "papers/malaria/general_biology",
        "malaria_host_omics": "papers/malaria/host_omics",
        "malaria_host_genetics": "papers/malaria/host_genetics",
        "scrna_annotation": "papers/single_cell/annotation",
        "sc_harmonization": "papers/single_cell/harmonization_integration",
        "sc_tools": "papers/single_cell/tools_methods",
        "covid_host_response": "papers/host_biology/covid_19",
        "host_tissue_atlas": "papers/host_biology/tissue_atlases",
        "host_immunity_general": "papers/host_biology/immunology",
        "infectious_disease_host_response": "papers/host_biology/infectious_diseases",
        "noninfectious_disease_biology": "papers/host_biology/noninfectious_diseases",
        "general_methods": "papers/methods/general",
    }.get(
        category,
        (
            f"papers/topics/{safe_category_slug(category.removeprefix('topic_'))}"
            if category.startswith("topic_")
            else "papers/other"
        ),
    )
    candidate = Path(directory) / filename
    counter = 2
    while (REPO_ROOT / "bib" / candidate).exists():
        candidate = Path(directory) / f"{Path(filename).stem}_{counter}.pdf"
        counter += 1
    return candidate.as_posix()


def load_known_pdf_hashes() -> set[str]:
    hashes: set[str] = set()
    manifest_path = REPO_ROOT / "product_manifest.json"
    if manifest_path.exists():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = payload.get("files") or []
        if isinstance(files, dict):
            files = [
                {"path": path, "sha256": value}
                for path, value in files.items()
                if isinstance(value, str)
            ]
        hashes.update(
            item.get("sha256", "")
            for item in files
            if isinstance(item, dict)
            and str(item.get("path") or "").startswith("bib/papers/")
            and isinstance(item.get("sha256"), str)
        )
    cache_path = REPO_ROOT / "runtime/imports/pdf_hashes.json"
    if cache_path.exists():
        hashes.update(json.loads(cache_path.read_text(encoding="utf-8")))
    return {value for value in hashes if value}


def save_known_pdf_hashes(hashes: set[str]) -> None:
    path = REPO_ROOT / "runtime/imports/pdf_hashes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(hashes), indent=2) + "\n", encoding="utf-8")


def read_doi_sidecar(source: Path) -> str:
    for path in (source.with_suffix(source.suffix + ".doi.txt"), source.with_suffix(".doi.txt")):
        if path.exists() and path.is_file() and not path.is_symlink():
            return path.read_text(encoding="utf-8").strip()
    return ""


def display_source_name(source: Path) -> str:
    metadata_path = source.with_suffix(source.suffix + ".upload.json")
    if metadata_path.exists() and metadata_path.is_file() and not metadata_path.is_symlink():
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            name = Path(str(payload.get("original_filename") or "")).name
            if name.lower().endswith(".pdf"):
                return name[:240]
        except (OSError, ValueError):
            pass
    return source.name


def archive_sidecar(source: Path, batch_dir: Path) -> None:
    for path in (
        source.with_suffix(source.suffix + ".doi.txt"),
        source.with_suffix(".doi.txt"),
        source.with_suffix(source.suffix + ".upload.json"),
    ):
        if path.exists() and path.is_file():
            target = batch_dir / "used_sidecars" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))


def archive_submission(source: Path, batch_dir: Path, status: str) -> None:
    """Remove a processed upload from the inbox while retaining an audit copy."""
    target = batch_dir / (safe_category_slug(status) or "review") / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        shutil.move(str(source), str(target))
    archive_sidecar(source, target.parent)


def resolve_bibtex_path(bib_dir: Path) -> Path:
    product_path = bib_dir / "pfempire_library.bib"
    legacy_path = bib_dir / "keys" / "pfempire_library.bib"
    if product_path.exists() or not legacy_path.exists():
        return product_path
    return legacy_path


def write_bibtex_atomic(path: Path, existing: str, candidates: list[Candidate]) -> None:
    additions = "\n\n".join(bibtex_entry(candidate) for candidate in candidates)
    if not additions:
        return
    content = existing.rstrip() + ("\n\n" if existing.strip() else "") + additions + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def bibtex_contains_candidate(existing: str, candidate: Candidate) -> bool:
    key_pattern = re.compile(
        rf"@[A-Za-z]+\s*\{{\s*{re.escape(candidate.bib_key)}\s*,",
        re.IGNORECASE,
    )
    if key_pattern.search(existing):
        return True
    normalized_doi = normalize_doi(candidate.doi)
    return bool(
        normalized_doi
        and any(
            normalize_doi(match.group(0).rstrip(".,;)")) == normalized_doi
            for match in DOI_PATTERN.finditer(existing)
        )
    )


def restore_file_atomic(path: Path, content: bytes | None) -> None:
    """Restore one transaction input without exposing partially written bytes."""

    temporary = path.with_suffix(path.suffix + ".rollback")
    temporary.unlink(missing_ok=True)
    if content is None:
        path.unlink(missing_ok=True)
        return
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(content)
    temporary.replace(path)


def bibtex_entry(candidate: Candidate) -> str:
    fields = [
        ("author", candidate.author),
        ("title", candidate.title),
        ("journal", candidate.journal),
        ("year", "" if candidate.year == "nd" else candidate.year),
        ("doi", candidate.doi),
        ("url", f"https://doi.org/{candidate.doi}"),
    ]
    lines = [f"@article{{{candidate.bib_key},"]
    lines.extend(
        f"  {name} = {{{bibtex_value(value)}}},"
        for name, value in fields
        if clean_text(value)
    )
    lines.append("}")
    return "\n".join(lines)


def bibtex_value(value: object) -> str:
    return (
        clean_text(value)
        .replace("\\", "\\textbackslash{}")
        .replace("{", "\\{")
        .replace("}", "\\}")
    )


def notify_progress(progress: Callable[[dict[str, object]], None] | None, **values: object) -> None:
    if progress is not None:
        try:
            progress(dict(values))
        except Exception:

            pass


def first_doi(text: str) -> str:
    match = DOI_PATTERN.search(text or "")
    return match.group(0).rstrip(".,;)") if match else ""


def first_list_value(value: object) -> str:
    if isinstance(value, list):
        return str(value[0] if value else "")
    return str(value or "")


def crossref_year(item: dict) -> str:
    for field in ("published-print", "published-online", "published", "issued"):
        value = item.get(field)
        parts = value.get("date-parts", []) if isinstance(value, dict) else []
        if parts and parts[0]:
            return str(parts[0][0])
    return ""


def make_bib_key(author: str, year: str, title: str) -> str:
    first_title = next((word for word in title.split() if len(word) >= 4), "Paper")
    return safe_filename(f"{author}{year}{first_title}").replace("_", "") or "ImportedPaper"


def unique_token(base: str, existing: set[str]) -> str:
    candidate = base
    counter = 2
    while candidate in existing:
        candidate = f"{base}_{counter}"
        counter += 1
    return candidate


def safe_filename(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", clean_text(value)).strip("_")
    return normalized[:140] or "ImportedPaper"


def safe_category_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", clean_text(value).lower()).strip("_")
    generic = {"article", "general", "medicine", "science", "research", "other"}
    return "" if not slug or slug in generic else slug[:64]


def clean_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"</?[A-Za-z][^>]*>", " ", text)
    return " ".join(text.split())


def clean_year(value: object) -> str:
    match = re.search(r"\b(?:19|20)\d{2}\b", str(value or ""))
    return match.group(0) if match else ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_tsv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = list(reader.fieldnames or [])
        return [dict(row) for row in reader], fields


def write_tsv_atomic(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=fields,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def report_row(source: Path, *, status: str, reason: str = "", **values: str) -> dict[str, str]:
    row = {field: "" for field in REPORT_FIELDS}
    row.update(source_file=display_source_name(source), status=status, reason=reason, **values)
    return row


if __name__ == "__main__":
    raise SystemExit(main())
