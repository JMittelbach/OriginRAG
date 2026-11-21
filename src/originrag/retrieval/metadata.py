from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path


METADATA_FIELDS = [
    "paper_id",
    "bib_key",
    "citation_short",
    "citation_apa",
    "author",
    "year",
    "doi",
    "pmid",
    "pmcid",
    "title",
    "journal",
    "category",
    "domain",
    "topics",
    "methods",
    "systems",
    "paper_type",
    "fulltext_status",
    "local_pdf_relpath",
    "notes",
]

REGISTRY_AUTHORITATIVE_FIELDS = {
    "bib_key",
    "citation_short",
    "citation_apa",
    "author",
    "year",
    "doi",
    "pmid",
    "pmcid",
    "title",
    "journal",
    "category",
}


def load_paper_metadata(papers_tsv: Path) -> dict[str, dict[str, str]]:
    """Load paper metadata keyed by paper_id.

    The active `papers.tsv` is intentionally small and may not contain author,
    journal, or category columns. When a sibling `corpus_manifest.tsv` is present,
    missing optional values are enriched from it without making that file required.
    """
    papers_path = Path(papers_tsv).resolve()
    return {
        paper_id: dict(record)
        for paper_id, record in _load_paper_metadata_cached(
            str(papers_path),
            metadata_cache_token(papers_path),
        ).items()
    }


@lru_cache(maxsize=8)
def _load_paper_metadata_cached(
    papers_tsv_text: str,
    cache_token: tuple[tuple[str, int, int, int], ...],
) -> dict[str, dict[str, str]]:
    del cache_token
    papers_path = Path(papers_tsv_text)
    metadata: dict[str, dict[str, str]] = {}
    if not papers_path.exists():
        return metadata

    for row in _read_tsv(papers_path):
        paper_id = _clean(row.get("paper_id"))
        if not paper_id:
            continue
        metadata[paper_id] = _normalize_row(row)

    manifest_path = papers_path.with_name("corpus_manifest.tsv")
    if manifest_path.exists():
        _enrich_from_manifest(metadata, manifest_path)

    registry_path = papers_path.with_name("paper_registry.tsv")
    if registry_path.exists():
        _enrich_from_registry(metadata, registry_path)

    corrections_path = papers_path.with_name("metadata_corrections.tsv")
    if corrections_path.exists():
        _apply_metadata_corrections(metadata, corrections_path)

    return metadata


def metadata_cache_token(
    papers_path: Path,
) -> tuple[tuple[str, int, int, int], ...]:
    paths = [
        papers_path,
        papers_path.with_name("corpus_manifest.tsv"),
        papers_path.with_name("paper_registry.tsv"),
        papers_path.with_name("metadata_corrections.tsv"),
    ]
    token = []
    for path in paths:
        if not path.exists():
            token.append((path.name, 0, 0, 0))
            continue
        stat = path.stat()
        token.append((path.name, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size))
    return tuple(token)


def _normalize_row(row: dict[str, str]) -> dict[str, str]:
    normalized = {field: _clean(row.get(field)) for field in METADATA_FIELDS}
    normalized["paper_id"] = _clean(row.get("paper_id"))
    return normalized


def _enrich_from_manifest(metadata: dict[str, dict[str, str]], manifest_path: Path) -> None:
    manifest_rows = {
        row.get("paper_id", ""): row
        for row in _read_tsv(manifest_path)
        if row.get("paper_id") in metadata
    }
    for paper_id, record in metadata.items():
        manifest_row = manifest_rows.get(paper_id)
        if not manifest_row:
            continue
        for field in METADATA_FIELDS:
            if not record.get(field):
                record[field] = _clean(manifest_row.get(field))


def _enrich_from_registry(metadata: dict[str, dict[str, str]], registry_path: Path) -> None:
    rows = _read_tsv(registry_path)
    registry_by_relpath = {
        row.get("local_pdf_relpath", ""): row
        for row in rows
        if row.get("local_pdf_relpath")
    }
    registry_by_paper_id = {
        row.get("paper_id", ""): row
        for row in rows
        if row.get("paper_id")
    }
    for paper_id, record in metadata.items():
        registry_row = None
        if record.get("local_pdf_relpath"):
            registry_row = registry_by_relpath.get(record["local_pdf_relpath"])
        if not registry_row:
            registry_row = registry_by_paper_id.get(paper_id)
        if not registry_row:
            continue
        for field in METADATA_FIELDS:
            if registry_row.get(field) and field in REGISTRY_AUTHORITATIVE_FIELDS:
                record[field] = _clean(registry_row.get(field))
            elif not record.get(field):
                record[field] = _clean(registry_row.get(field))


def _apply_metadata_corrections(metadata: dict[str, dict[str, str]], corrections_path: Path) -> None:
    for row in _read_tsv(corrections_path):
        paper_id = _clean(row.get("paper_id"))
        if not paper_id or paper_id not in metadata:
            continue
        for field in METADATA_FIELDS:
            value = _clean(row.get(field))
            if value:
                metadata[paper_id][field] = value
        _refresh_stale_citation_fields(metadata[paper_id])


def _refresh_stale_citation_fields(record: dict[str, str]) -> None:
    """Replace placeholder citations after a local metadata correction."""
    author = record.get("author", "")
    year = record.get("year", "")
    title = record.get("title", "")
    journal = record.get("journal", "")
    doi = record.get("doi", "")
    citation_short = record.get("citation_short", "")
    citation_apa = record.get("citation_apa", "")

    if _is_placeholder_citation(citation_short) and author and year:
        record["citation_short"] = f"{author} et al., {year}"

    if _is_placeholder_citation(citation_apa) and author and year and title:
        journal_part = f" {journal}." if journal else ""
        doi_part = f" https://doi.org/{doi}" if doi else ""
        record["citation_apa"] = f"{author} et al. ({year}). {title}.{journal_part}{doi_part}"


def _is_placeholder_citation(value: str) -> bool:
    return not value or value.strip().lower().startswith("unknown et al.")


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def _clean(value: object) -> str:
    return str(value or "").strip()
