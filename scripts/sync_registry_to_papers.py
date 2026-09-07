from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PAPERS_FIELDS = [
    "paper_id",
    "title",
    "year",
    "doi",
    "pmid",
    "pmcid",
    "domain",
    "topics",
    "methods",
    "systems",
    "paper_type",
    "fulltext_status",
    "local_pdf_relpath",
    "extracted_text_relpath",
    "notes",
]

PREFIX_BY_CATEGORY = {
    "var_pfemp1": "MAL_VAR",
    "malaria_host_genetics": "MAL_HGEN",
    "malaria_host_omics": "MAL_HOMX",
    "host_immunity": "MAL_HOST",
    "sex_diff": "MAL_SEX",
    "tools_ml": "BIO_ML",
    "malaria_tools": "MAL_TOOL",
    "resistance_dx": "MAL_RES",
    "parasite_biology": "MAL_PAR",
    "malaria_omics": "MAL_OMX",
    "general_malaria": "MAL_GEN",
    "scrna_annotation": "SCRNA_ANN",
    "sc_harmonization": "SCRNA_HARM",
    "sc_tools": "SCRNA_TOOL",
    "foundation_models": "BIO_FM",
    "evidence_synthesis": "BIO_ML",
    "covid_host_response": "HOST_COV",
    "host_immunity_general": "HOST_IMM",
    "host_tissue_atlas": "HOST_ATL",
    "infectious_disease_host_response": "HOST_INF",
    "noninfectious_disease_biology": "HOST_DIS",
    "general_methods": "METHOD",
}


def main() -> int:
    args = parse_args()
    papers_path = resolve_repo_path(args.papers)
    registry_path = resolve_repo_path(args.registry)
    papers = read_tsv(papers_path)
    registry = read_tsv(registry_path)

    existing_ids = {row.get("paper_id", "") for row in papers if row.get("paper_id")}
    next_numbers = next_numbers_by_prefix(existing_ids)

    updated_existing = update_existing_papers(papers, registry)
    existing_relpaths = {row.get("local_pdf_relpath", "") for row in papers if row.get("local_pdf_relpath")}
    existing_dois = {normalize_doi(row.get("doi", "")) for row in papers if normalize_doi(row.get("doi", ""))}
    appended_doi = 0
    appended_stable_url = 0
    for row in sorted(registry, key=registry_sort_key):
        relpath = row.get("local_pdf_relpath", "").strip()
        if not relpath or relpath in existing_relpaths:
            continue
        doi = normalize_doi(row.get("doi", ""))
        if not doi and not stable_landing_url(row):
            continue
        if doi and doi in existing_dois:
            continue
        paper_id = next_paper_id(row.get("category", ""), next_numbers, existing_ids)
        papers.append(paper_row_from_registry(row, paper_id))
        existing_relpaths.add(relpath)
        existing_ids.add(paper_id)
        if doi:
            existing_dois.add(doi)
            appended_doi += 1
        else:
            appended_stable_url += 1

    if not args.dry_run:
        write_tsv(papers_path, papers)
    print(f"Updated existing papers: {updated_existing}")
    print(f"Appended DOI-backed registry papers: {appended_doi}")
    print(f"Appended stable-URL registry papers without DOI: {appended_stable_url}")
    print(
        "Skipped registry rows without DOI or stable URL: "
        f"{sum(1 for row in registry if not normalize_doi(row.get('doi', '')) and not stable_landing_url(row))}"
    )
    if args.dry_run:
        print("Dry run: papers.tsv was not modified.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync DOI-backed paper_registry.tsv rows into papers.tsv.")
    parser.add_argument("--registry", default="data/metadata/paper_registry.tsv")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def update_existing_papers(papers: list[dict[str, str]], registry: list[dict[str, str]]) -> int:
    by_relpath = {
        row.get("local_pdf_relpath", ""): row
        for row in registry
        if row.get("local_pdf_relpath")
    }
    by_doi: dict[str, list[dict[str, str]]] = {}
    for row in registry:
        doi = normalize_doi(row.get("doi", ""))
        if doi:
            by_doi.setdefault(doi, []).append(row)
    updated = 0
    for paper in papers:
        if paper.get("fulltext_status", "").strip().lower() == "duplicate":
            continue
        registry_row = by_relpath.get(paper.get("local_pdf_relpath", ""))
        if not registry_row:
            doi_matches = by_doi.get(normalize_doi(paper.get("doi", "")), [])
            if len(doi_matches) == 1:
                registry_row = doi_matches[0]
        if not registry_row:
            continue
        before = dict(paper)
        incoming_relpath = registry_row.get("local_pdf_relpath", "").strip()
        if incoming_relpath and paper.get("local_pdf_relpath", "") != incoming_relpath:
            paper["local_pdf_relpath"] = incoming_relpath
        has_metadata_correction = "metadata_corrections" in registry_row.get("metadata_source", "")
        for field in ["title", "year", "doi", "pmid", "pmcid"]:
            incoming = registry_row.get(field, "")
            if has_metadata_correction and incoming.strip() and paper.get(field, "") != incoming:
                paper[field] = incoming
            elif should_update_field(field, paper.get(field, ""), incoming):
                paper[field] = registry_row[field]
        if has_metadata_correction and registry_row.get("category", "").strip():
            category = registry_row["category"].strip()
            if paper.get("topics", "") != category:
                paper["topics"] = category
            domain = domain_for_category(category)
            if paper.get("domain", "") != domain:
                paper["domain"] = domain
        if registry_row.get("doi"):
            paper["notes"] = append_note(paper.get("notes", ""), "doi_verified_from_registry")
            if paper.get("fulltext_status") == "excluded_no_doi":
                paper["fulltext_status"] = "pdf_available"
        else:
            paper["notes"] = append_note(paper.get("notes", ""), "no_doi_excluded_from_default_retrieval")
            paper["fulltext_status"] = "excluded_no_doi"
        if paper != before:
            updated += 1
    return updated


def paper_row_from_registry(row: dict[str, str], paper_id: str) -> dict[str, str]:
    category = row.get("category", "").strip()
    has_doi = bool(normalize_doi(row.get("doi", "")))
    return {
        "paper_id": paper_id,
        "title": row.get("title", ""),
        "year": row.get("year", ""),
        "doi": row.get("doi", ""),
        "pmid": row.get("pmid", ""),
        "pmcid": row.get("pmcid", ""),
        "domain": domain_for_category(category),
        "topics": category,
        "methods": "",
        "systems": "",
        "paper_type": "",
        "fulltext_status": "pdf_available" if has_doi else "excluded_no_doi",
        "local_pdf_relpath": row.get("local_pdf_relpath", ""),
        "extracted_text_relpath": "",
        "notes": (
            "registry_doi_sync"
            if has_doi
            else "registry_stable_url_sync; no_doi_excluded_from_default_retrieval"
        ),
    }


def stable_landing_url(row: dict[str, str]) -> bool:
    return bool(re.match(r"^https?://", row.get("landing_url", "").strip(), flags=re.IGNORECASE))


def next_numbers_by_prefix(existing_ids: set[str]) -> dict[str, int]:
    next_numbers: dict[str, int] = {}
    for paper_id in existing_ids:
        match = re.match(r"^([A-Z_]+)_(\d+)$", paper_id)
        if not match:
            continue
        prefix, number = match.group(1), int(match.group(2))
        next_numbers[prefix] = max(next_numbers.get(prefix, 1), number + 1)
    return next_numbers


def next_paper_id(category: str, next_numbers: dict[str, int], existing_ids: set[str]) -> str:
    prefix = PREFIX_BY_CATEGORY.get(category.strip() or "general_malaria", "UNK")
    number = next_numbers.get(prefix, 1)
    while True:
        paper_id = f"{prefix}_{number:04d}"
        number += 1
        if paper_id not in existing_ids:
            next_numbers[prefix] = number
            return paper_id


def domain_for_category(category: str) -> str:
    if category == "other" or category.startswith("topic_"):
        return "general"
    if category in {"scrna_annotation", "sc_harmonization", "sc_tools"}:
        return "single_cell_bioinformatics"
    if category in {"tools_ml", "foundation_models", "evidence_synthesis"}:
        return "bioinformatics"
    if category in {
        "covid_host_response",
        "host_immunity_general",
        "host_tissue_atlas",
        "infectious_disease_host_response",
        "noninfectious_disease_biology",
    }:
        return "host_biology"
    if category == "general_methods":
        return "methods"
    return "malaria"


def registry_sort_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        row.get("category", ""),
        row.get("author", ""),
        row.get("year", ""),
        row.get("title", ""),
    )


def append_note(existing: str, note: str) -> str:
    values = []
    for raw_value in [existing, note]:
        values.extend(value.strip() for value in raw_value.split(";") if value.strip())
    seen = set()
    deduped = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return "; ".join(deduped)


def should_update_field(field: str, current: str, incoming: str) -> bool:
    incoming = (incoming or "").strip()
    if not incoming:
        return False
    current = (current or "").strip()
    if not current:
        return True
    if field == "title":
        return is_weak_title(current) and not is_weak_title(incoming)
    if field == "year":
        return not re.fullmatch(r"(19|20)\d{2}", current)
    return False


def is_weak_title(value: str) -> bool:
    title = (value or "").strip()
    normalized = re.sub(r"\s+", " ", title).lower()
    if normalized in {"europe pmc funders group", "unknown", "untitled"}:
        return True
    if "_" in title:
        return True
    words = re.findall(r"[A-Za-z]{3,}", title)
    return len(words) < 4


def normalize_doi(value: str) -> str:
    doi = (value or "").strip().lower()
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=PAPERS_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in PAPERS_FIELDS})


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
