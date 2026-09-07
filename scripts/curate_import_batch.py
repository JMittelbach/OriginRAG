from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.retrieval.fts_index import build_fts_index, ensure_current_index  # noqa: E402

from build_paper_registry import (  # noqa: E402
    FIELDS as REGISTRY_FIELDS,
    citation_apa,
    citation_short,
    make_bib_key,
)
from organize_bib_library import format_bibtex  # noqa: E402


PAPERS_PATH = REPO_ROOT / "data/metadata/papers.tsv"
REGISTRY_PATH = REPO_ROOT / "data/metadata/paper_registry.tsv"
CORRECTIONS_PATH = REPO_ROOT / "data/metadata/metadata_corrections.tsv"
ALL_PAPERS_PATH = REPO_ROOT / "bib/keys/all_papers.tsv"
BIB_PATH = REPO_ROOT / "bib/keys/pfempire_library.bib"
CHUNKS_DIR = REPO_ROOT / "data/chunks"
INDEX_PATH = REPO_ROOT / "data/indexes/pfempire_fts.sqlite"
LOCK_PATH = REPO_ROOT / "runtime/imports/curate_import_batch.lock"

CATEGORY_DIRECTORIES = {
    "general_malaria": "papers/malaria/general_biology",
    "resistance_dx": "papers/malaria/resistance_diagnostics",
}

BIB_ENTRY_PATTERN = re.compile(
    r"(?ms)^@[A-Za-z]+\{([^,\n]+),\n.*?(?=^@[A-Za-z]+\{|\Z)"
)


class CurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PdfMove:
    paper_id: str
    source: Path
    target: Path
    sha256: str


@dataclass
class CurationPlan:
    batch_dir: Path
    report_rows: list[dict[str, str]]
    registry_rows: list[dict[str, str]]
    registry_fields: list[str]
    papers_rows: list[dict[str, str]]
    papers_fields: list[str]
    all_papers_rows: list[dict[str, str]]
    all_papers_fields: list[str]
    bib_text: str
    json_updates: dict[Path, bytes]
    moves: list[PdfMove]
    input_hashes: dict[Path, str]
    selected_ids: list[str]
    fulltext_count: int
    metadata_only_count: int


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Curate one completed inbox-import batch without touching unrelated "
            "registry rows or PDFs. The default is a read-only dry-run."
        )
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument(
        "--registry-only-paper-id",
        action="append",
        default=[],
        help="Correction-ledger paper ID to add to bibliography only (repeatable).",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CurationError("Another import curation process holds the lock") from exc

        plan = build_plan(
            batch_id=args.batch_id,
            expected_count=args.expected_count,
            registry_only_ids=args.registry_only_paper_id,
        )
        print_plan(plan)
        if not args.apply:
            print("Dry-run only; pass --apply to write this exact plan.")
            return 0
        apply_plan(plan)
    return 0


def build_plan(
    *, batch_id: str, expected_count: int, registry_only_ids: list[str]
) -> CurationPlan:
    batch_dir = REPO_ROOT / "runtime/imports" / batch_id
    import_report_path = batch_dir / "import_report.tsv"
    if not import_report_path.is_file():
        raise CurationError(f"Import report missing: {display(import_report_path)}")

    import_rows, _ = read_tsv(import_report_path)
    if any(row.get("status") != "accepted" for row in import_rows):
        raise CurationError("The import report contains a non-accepted row")
    if len(import_rows) != expected_count:
        raise CurationError(
            f"Expected {expected_count} accepted rows, found {len(import_rows)}"
        )

    imported_ids = [clean(row.get("paper_id")) for row in import_rows]
    require_unique_nonempty(imported_ids, "imported paper IDs")
    imported_dois = [normalize_doi(row.get("doi")) for row in import_rows]
    require_unique_nonempty(imported_dois, "imported DOIs")
    metadata_only_ids = [clean(value) for value in registry_only_ids]
    require_unique_nonempty(metadata_only_ids, "registry-only paper IDs")
    if set(imported_ids) & set(metadata_only_ids):
        raise CurationError("An imported paper cannot also be registry-only")

    correction_rows, _ = read_tsv(CORRECTIONS_PATH)
    corrections = select_unique_rows(
        correction_rows,
        field="paper_id",
        values=imported_ids + metadata_only_ids,
        label="metadata correction",
    )
    for report_row in import_rows:
        paper_id = report_row["paper_id"]
        if normalize_doi(corrections[paper_id].get("doi")) != normalize_doi(
            report_row.get("doi")
        ):
            raise CurationError(f"Correction DOI mismatch for {paper_id}")

    registry_rows, registry_fields = read_tsv(REGISTRY_PATH)
    papers_rows, papers_fields = read_tsv(PAPERS_PATH)
    all_papers_rows, all_papers_fields = read_tsv(ALL_PAPERS_PATH)
    if registry_fields != REGISTRY_FIELDS:
        raise CurationError("Unexpected paper_registry.tsv schema")
    if all_papers_fields != registry_fields:
        raise CurationError("all_papers.tsv no longer mirrors the registry schema")

    registry_by_id = select_unique_rows(
        registry_rows,
        field="paper_id",
        values=imported_ids,
        label="registry",
    )
    papers_by_id = select_unique_rows(
        papers_rows,
        field="paper_id",
        values=imported_ids,
        label="papers.tsv",
    )
    ensure_no_metadata_only_retrieval_rows(
        papers_rows=papers_rows,
        registry_rows=registry_rows,
        metadata_only_ids=metadata_only_ids,
        corrections=corrections,
    )

    moves: list[PdfMove] = []
    json_updates: dict[Path, bytes] = {}
    output_rows: list[dict[str, str]] = []
    final_registry_by_id: dict[str, dict[str, str]] = {}

    for report_row in import_rows:
        paper_id = report_row["paper_id"]
        correction = corrections[paper_id]
        registry_row = registry_by_id[paper_id]
        paper_row = papers_by_id[paper_id]
        if normalize_doi(registry_row.get("doi")) != normalize_doi(report_row.get("doi")):
            raise CurationError(f"Registry DOI mismatch for {paper_id}")
        if normalize_doi(paper_row.get("doi")) != normalize_doi(report_row.get("doi")):
            raise CurationError(f"papers.tsv DOI mismatch for {paper_id}")
        chunk_path = CHUNKS_DIR / f"{paper_id}.jsonl"
        if not chunk_path.is_file():
            raise CurationError(f"Chunk file missing for {paper_id}")

        old_category = clean(registry_row.get("category"))
        old_relpath = clean(registry_row.get("local_pdf_relpath"))
        category = clean(correction.get("category"))
        directory = CATEGORY_DIRECTORIES.get(category)
        if not directory:
            raise CurationError(f"No curated directory mapping for {paper_id}: {category}")
        filename = clean(registry_row.get("filename")) or Path(old_relpath).name
        target_relpath = f"{directory}/{filename}"

        old_pdf = REPO_ROOT / "bib" / old_relpath
        target_pdf = REPO_ROOT / "bib" / target_relpath
        active_pdf = target_pdf if old_relpath == target_relpath else old_pdf
        if not active_pdf.is_file():
            raise CurationError(f"Current PDF missing for {paper_id}: {display(active_pdf)}")
        pdf_sha = sha256_file(active_pdf)
        if old_relpath != target_relpath:
            if target_pdf.exists():
                raise CurationError(f"Curated PDF target already exists: {display(target_pdf)}")
            moves.append(PdfMove(paper_id, old_pdf, target_pdf, pdf_sha))

        apply_registry_correction(
            registry_row,
            correction=correction,
            target_relpath=target_relpath,
        )
        apply_papers_correction(
            paper_row,
            correction=correction,
            target_relpath=target_relpath,
        )
        final_registry_by_id[paper_id] = dict(registry_row)

        extracted_path = REPO_ROOT / clean(paper_row.get("extracted_text_relpath"))
        if not extracted_path.is_file():
            raise CurationError(f"Extracted text missing for {paper_id}")
        payload = json.loads(extracted_path.read_text(encoding="utf-8"))
        if clean(payload.get("paper_id")) != paper_id:
            raise CurationError(f"Extracted-text paper ID mismatch for {paper_id}")
        if clean(payload.get("source_pdf")) not in {old_relpath, target_relpath}:
            raise CurationError(f"Extracted-text PDF path mismatch for {paper_id}")
        if payload.get("source_pdf") != target_relpath:
            payload["source_pdf"] = target_relpath
            json_updates[extracted_path] = (
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")

        output_rows.append(
            {
                "paper_id": paper_id,
                "kind": "fulltext",
                "doi": normalize_doi(registry_row.get("doi")),
                "pmid": registry_row.get("pmid", ""),
                "pmcid": registry_row.get("pmcid", ""),
                "old_category": old_category,
                "new_category": category,
                "old_pdf_relpath": old_relpath,
                "new_pdf_relpath": target_relpath,
                "pdf_sha256": pdf_sha,
            }
        )

    existing_keys = {clean(row.get("bib_key")) for row in registry_rows}
    for paper_id in metadata_only_ids:
        correction = corrections[paper_id]
        row = find_optional_unique(registry_rows, "paper_id", paper_id, "registry-only row")
        if row is None:
            row = metadata_only_registry_row(correction, existing_keys=existing_keys)
            registry_rows.append(row)
        else:
            if clean(row.get("local_pdf_relpath")) or clean(row.get("pdf_url")):
                raise CurationError(f"Registry-only row has a PDF source: {paper_id}")
            apply_registry_correction(row, correction=correction, target_relpath="")
            row["metadata_source"] = append_token(
                row.get("metadata_source", ""), "manual_metadata_only"
            )
        final_registry_by_id[paper_id] = dict(row)
        output_rows.append(
            {
                "paper_id": paper_id,
                "kind": "metadata_only",
                "doi": normalize_doi(row.get("doi")),
                "pmid": row.get("pmid", ""),
                "pmcid": row.get("pmcid", ""),
                "old_category": "",
                "new_category": row.get("category", ""),
                "old_pdf_relpath": "",
                "new_pdf_relpath": "",
                "pdf_sha256": "",
            }
        )

    selected_ids = imported_ids + metadata_only_ids
    upsert_registry_export(all_papers_rows, final_registry_by_id)
    assert_selected_registry_export_matches(
        registry_rows=registry_rows,
        all_papers_rows=all_papers_rows,
        selected_ids=selected_ids,
    )

    bib_text = BIB_PATH.read_text(encoding="utf-8")
    replacements = {
        row["bib_key"]: format_bibtex([row])
        for row in final_registry_by_id.values()
    }
    bib_text = replace_bib_entries(
        bib_text,
        replacements=replacements,
        allow_missing={final_registry_by_id[paper_id]["bib_key"] for paper_id in metadata_only_ids},
    )
    assert_bib_dois_once(
        bib_text,
        [final_registry_by_id[paper_id]["doi"] for paper_id in selected_ids],
    )

    input_paths = [
        import_report_path,
        REGISTRY_PATH,
        PAPERS_PATH,
        ALL_PAPERS_PATH,
        BIB_PATH,
        CORRECTIONS_PATH,
        INDEX_PATH,
        *json_updates,
    ]
    input_hashes = {path: sha256_file(path) for path in input_paths}
    return CurationPlan(
        batch_dir=batch_dir,
        report_rows=output_rows,
        registry_rows=registry_rows,
        registry_fields=registry_fields,
        papers_rows=papers_rows,
        papers_fields=papers_fields,
        all_papers_rows=all_papers_rows,
        all_papers_fields=all_papers_fields,
        bib_text=bib_text,
        json_updates=json_updates,
        moves=moves,
        input_hashes=input_hashes,
        selected_ids=selected_ids,
        fulltext_count=len(imported_ids),
        metadata_only_count=len(metadata_only_ids),
    )


def ensure_no_metadata_only_retrieval_rows(
    *,
    papers_rows: list[dict[str, str]],
    registry_rows: list[dict[str, str]],
    metadata_only_ids: list[str],
    corrections: dict[str, dict[str, str]],
) -> None:
    paper_ids = Counter(clean(row.get("paper_id")) for row in papers_rows)
    paper_dois = Counter(normalize_doi(row.get("doi")) for row in papers_rows)
    for paper_id in metadata_only_ids:
        doi = normalize_doi(corrections[paper_id].get("doi"))
        if paper_ids[paper_id] or paper_dois[doi]:
            raise CurationError(f"Registry-only paper is already retrieval-backed: {paper_id}")
        conflicting_registry_rows = [
            row
            for row in registry_rows
            if normalize_doi(row.get("doi")) == doi
            and clean(row.get("paper_id")) != paper_id
        ]
        if conflicting_registry_rows:
            raise CurationError(
                f"Registry-only DOI belongs to another registry row: {doi}"
            )
        if (CHUNKS_DIR / f"{paper_id}.jsonl").exists():
            raise CurationError(f"Registry-only paper has chunks: {paper_id}")
        if (REPO_ROOT / "data/extracted_text" / f"{paper_id}.json").exists():
            raise CurationError(f"Registry-only paper has extracted text: {paper_id}")


def apply_registry_correction(
    row: dict[str, str], *, correction: dict[str, str], target_relpath: str
) -> None:
    paper_id = clean(row.get("paper_id")) or clean(correction.get("paper_id"))
    if normalize_doi(row.get("doi")) and normalize_doi(row.get("doi")) != normalize_doi(
        correction.get("doi")
    ):
        raise CurationError(f"DOI mismatch while curating {paper_id}")
    row["paper_id"] = paper_id
    for field in ("author", "year", "title", "journal", "pmid", "pmcid"):
        value = clean(correction.get(field))
        if value:
            row[field] = value
    row["doi"] = normalize_doi(correction.get("doi"))
    row["category"] = clean(correction.get("category"))
    row["collection"] = "malaria"
    row["landing_url"] = clean(row.get("landing_url")) or f"https://doi.org/{row['doi']}"
    row["local_pdf_relpath"] = target_relpath
    row["filename"] = Path(target_relpath).name if target_relpath else ""
    row["citation_short"] = citation_short(row)
    row["citation_apa"] = citation_apa(row)
    row["metadata_source"] = append_token(
        row.get("metadata_source", ""), "metadata_corrections"
    )
    row["metadata_source"] = append_token(
        row.get("metadata_source", ""), "rtm_import_batch_curation"
    )
    row["notes"] = append_note(row.get("notes", ""), correction.get("notes", ""))
    for field in REGISTRY_FIELDS:
        row.setdefault(field, "")


def apply_papers_correction(
    row: dict[str, str], *, correction: dict[str, str], target_relpath: str
) -> None:
    paper_id = clean(row.get("paper_id"))
    if normalize_doi(row.get("doi")) != normalize_doi(correction.get("doi")):
        raise CurationError(f"papers.tsv DOI mismatch while curating {paper_id}")
    title = clean(correction.get("title"))
    if title:
        row["title"] = title
    row["pmid"] = clean(correction.get("pmid")) or clean(row.get("pmid"))
    row["pmcid"] = clean(correction.get("pmcid")) or clean(row.get("pmcid"))
    row["domain"] = "malaria"
    row["topics"] = clean(correction.get("category"))
    row["local_pdf_relpath"] = target_relpath
    row["notes"] = append_note(row.get("notes", ""), "rtm_import_batch_curation")
    row["notes"] = append_note(row.get("notes", ""), correction.get("notes", ""))


def metadata_only_registry_row(
    correction: dict[str, str], *, existing_keys: set[str]
) -> dict[str, str]:
    paper_id = clean(correction.get("paper_id"))
    title = clean(correction.get("title"))
    bib_key = make_bib_key(
        clean(correction.get("author")), clean(correction.get("year")), title
    )
    if not bib_key or bib_key in existing_keys:
        raise CurationError(f"Could not assign unique registry-only BibTeX key: {paper_id}")
    existing_keys.add(bib_key)
    row = {field: "" for field in REGISTRY_FIELDS}
    row.update(
        {
            "bib_key": bib_key,
            "paper_id": paper_id,
            "collection": "malaria",
            "category": clean(correction.get("category")),
            "author": clean(correction.get("author")),
            "year": clean(correction.get("year")),
            "title": title,
            "journal": clean(correction.get("journal")),
            "doi": normalize_doi(correction.get("doi")),
            "pmid": clean(correction.get("pmid")),
            "pmcid": clean(correction.get("pmcid")),
            "landing_url": f"https://doi.org/{normalize_doi(correction.get('doi'))}",
            "metadata_source": (
                "manual_metadata_only;europepmc_exact_doi;metadata_corrections;"
                "rtm_import_batch_curation"
            ),
            "notes": append_note(
                "metadata-only; full text not locally verified; "
                "source/origin relations remain unreviewed",
                correction.get("notes", ""),
            ),
        }
    )
    row["citation_short"] = citation_short(row)
    row["citation_apa"] = citation_apa(row)
    return row


def upsert_registry_export(
    rows: list[dict[str, str]], selected: dict[str, dict[str, str]]
) -> None:
    positions: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        positions.setdefault(clean(row.get("paper_id")), []).append(index)
    for paper_id, registry_row in selected.items():
        found = positions.get(paper_id, [])
        if len(found) > 1:
            raise CurationError(f"Duplicate all_papers.tsv row for {paper_id}")
        if found:
            rows[found[0]] = dict(registry_row)
        else:
            rows.append(dict(registry_row))


def assert_selected_registry_export_matches(
    *,
    registry_rows: list[dict[str, str]],
    all_papers_rows: list[dict[str, str]],
    selected_ids: list[str],
) -> None:
    registry = select_unique_rows(
        registry_rows, "paper_id", selected_ids, "final registry"
    )
    exported = select_unique_rows(
        all_papers_rows, "paper_id", selected_ids, "final all_papers.tsv"
    )
    for paper_id in selected_ids:
        if registry[paper_id] != exported[paper_id]:
            raise CurationError(f"Registry export mismatch for {paper_id}")


def replace_bib_entries(
    text: str, *, replacements: dict[str, str], allow_missing: set[str]
) -> str:
    counts: Counter[str] = Counter()
    chunks: list[str] = []
    cursor = 0
    for match in BIB_ENTRY_PATTERN.finditer(text):
        key = match.group(1).strip()
        if key not in replacements:
            continue
        chunks.append(text[cursor : match.start()])
        chunks.append(replacements[key].rstrip() + "\n\n")
        cursor = match.end()
        counts[key] += 1
    chunks.append(text[cursor:])
    missing = set(replacements) - set(counts)
    unexpected_missing = missing - allow_missing
    if unexpected_missing:
        raise CurationError(f"Missing BibTeX entries: {sorted(unexpected_missing)}")
    duplicated = sorted(key for key, count in counts.items() if count != 1)
    if duplicated:
        raise CurationError(f"Duplicate selected BibTeX entries: {duplicated}")
    result = "".join(chunks).rstrip() + "\n"
    for key in sorted(missing):
        result = result.rstrip() + "\n\n" + replacements[key].strip() + "\n"
    return result


def assert_bib_dois_once(text: str, dois: list[str]) -> None:
    normalized = text.casefold()
    for doi in dois:
        needle = "doi = {" + normalize_doi(doi) + "}"
        if normalized.count(needle.casefold()) != 1:
            raise CurationError(f"BibTeX DOI is not unique: {doi}")


def apply_plan(plan: CurationPlan) -> None:
    for path, expected in plan.input_hashes.items():
        if sha256_file(path) != expected:
            raise CurationError(f"Input changed after preflight: {display(path)}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = plan.batch_dir / f"curation_backup_{timestamp}"
    report_path = plan.batch_dir / "curation_report.tsv"
    summary_path = plan.batch_dir / "curation_summary.json"
    output_snapshots = {
        path: path.read_bytes() if path.exists() else None
        for path in (report_path, summary_path)
    }
    backup_dir.mkdir(parents=True, exist_ok=False)
    backed_up: dict[Path, Path] = {}
    for path in plan.input_hashes:
        target = backup_dir / path.relative_to(REPO_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        backed_up[path] = target

    for path, expected in plan.input_hashes.items():
        if sha256_file(path) != expected:
            raise CurationError(f"Input changed while backing up: {display(path)}")

    completed_moves: list[PdfMove] = []
    try:
        for move in plan.moves:
            if sha256_file(move.source) != move.sha256:
                raise CurationError(f"PDF changed before move: {move.paper_id}")
            if move.target.exists():
                raise CurationError(
                    f"Curated PDF target appeared after preflight: {display(move.target)}"
                )
            move.target.parent.mkdir(parents=True, exist_ok=True)
            move.source.replace(move.target)
            completed_moves.append(move)
            if sha256_file(move.target) != move.sha256:
                raise CurationError(f"PDF hash changed after move: {move.paper_id}")

        write_tsv_atomic(REGISTRY_PATH, plan.registry_rows, plan.registry_fields)
        write_tsv_atomic(PAPERS_PATH, plan.papers_rows, plan.papers_fields)
        write_tsv_atomic(ALL_PAPERS_PATH, plan.all_papers_rows, plan.all_papers_fields)
        write_bytes_atomic(BIB_PATH, plan.bib_text.encode("utf-8"))
        for path, payload in plan.json_updates.items():
            write_bytes_atomic(path, payload)

        index_result = build_fts_index(CHUNKS_DIR, PAPERS_PATH, INDEX_PATH)
        ensure_current_index(INDEX_PATH, CHUNKS_DIR, PAPERS_PATH)
        report_fields = [
            "paper_id",
            "kind",
            "doi",
            "pmid",
            "pmcid",
            "old_category",
            "new_category",
            "old_pdf_relpath",
            "new_pdf_relpath",
            "pdf_sha256",
        ]
        write_tsv_atomic(report_path, plan.report_rows, report_fields)
        summary = {
            "fulltext_curated": plan.fulltext_count,
            "metadata_only_added": plan.metadata_only_count,
            "pdfs_moved": len(plan.moves),
            "selected_paper_ids": plan.selected_ids,
            "index_chunk_count": index_result.chunk_count,
            "index_paper_count": index_result.paper_count,
            "index_fingerprint": index_result.corpus_fingerprint,
            "backup_dir": display(backup_dir),
        }
        write_bytes_atomic(
            summary_path,
            (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
    except Exception:
        for path, backup in backed_up.items():
            shutil.copy2(backup, path)
        for move in reversed(completed_moves):
            if move.target.exists() and not move.source.exists():
                move.target.replace(move.source)
        for path, payload in output_snapshots.items():
            if payload is None:
                path.unlink(missing_ok=True)
            else:
                write_bytes_atomic(path, payload)
        INDEX_PATH.with_suffix(INDEX_PATH.suffix + ".building").unlink(missing_ok=True)
        raise

    print(
        "Applied curation: "
        f"fulltext={plan.fulltext_count}, metadata_only={plan.metadata_only_count}, "
        f"moved={len(plan.moves)}, chunks={index_result.chunk_count}, "
        f"papers={index_result.paper_count}"
    )
    print(f"Backup: {display(backup_dir)}")


def print_plan(plan: CurationPlan) -> None:
    print(
        f"Plan: fulltext={plan.fulltext_count}, "
        f"metadata_only={plan.metadata_only_count}, moves={len(plan.moves)}"
    )
    for row in plan.report_rows:
        path_change = ""
        if row["old_pdf_relpath"] != row["new_pdf_relpath"]:
            path_change = f"; move={row['old_pdf_relpath']} -> {row['new_pdf_relpath']}"
        print(
            f"- {row['paper_id']} [{row['kind']}] {row['doi']} "
            f"category={row['new_category']}{path_change}"
        )


def read_tsv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        raise CurationError(f"TSV missing: {display(path)}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = list(reader.fieldnames or [])
        return [dict(row) for row in reader], fields


def write_tsv_atomic(
    path: Path, rows: list[dict[str, str]], fields: list[str]
) -> None:
    temporary = path.with_name(path.name + ".curating")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=fields,
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def write_bytes_atomic(path: Path, payload: bytes) -> None:
    temporary = path.with_name(path.name + ".curating")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def select_unique_rows(
    rows: list[dict[str, str]],
    field: str,
    values: list[str],
    label: str,
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    wanted = set(values)
    counts: Counter[str] = Counter(
        clean(row.get(field)) for row in rows if clean(row.get(field)) in wanted
    )
    missing = sorted(value for value in wanted if counts[value] == 0)
    duplicate = sorted(value for value in wanted if counts[value] > 1)
    if missing or duplicate:
        raise CurationError(f"Invalid {label} rows: missing={missing}, duplicate={duplicate}")
    for row in rows:
        value = clean(row.get(field))
        if value in wanted:
            result[value] = row
    return result


def find_optional_unique(
    rows: list[dict[str, str]], field: str, value: str, label: str
) -> dict[str, str] | None:
    matches = [row for row in rows if clean(row.get(field)) == value]
    if len(matches) > 1:
        raise CurationError(f"Duplicate {label}: {value}")
    return matches[0] if matches else None


def require_unique_nonempty(values: list[str], label: str) -> None:
    if not values or any(not value for value in values):
        raise CurationError(f"Missing value among {label}")
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        raise CurationError(f"Duplicate {label}: {duplicates}")


def append_token(current: str, value: str) -> str:
    tokens = [token.strip() for token in clean(current).split(";") if token.strip()]
    if value and value not in tokens:
        tokens.append(value)
    return ";".join(tokens)


def append_note(current: str, value: str) -> str:
    result = clean(current)
    addition = clean(value)
    if not addition or addition in result:
        return result
    return f"{result}; {addition}" if result else addition


def normalize_doi(value: object) -> str:
    doi = clean(value).lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix) :]
    return doi.strip().rstrip(".,;)")


def clean(value: object) -> str:
    return " ".join(str(value or "").split())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def display(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CurationError as exc:
        print(f"Curation error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
