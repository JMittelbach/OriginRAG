from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

PLACEHOLDER_AUTHOR_PATTERN = re.compile(r"(?i)^anna\s*import\d*$")


FIELDS = [
    "bib_key",
    "paper_id",
    "collection",
    "category",
    "author",
    "year",
    "title",
    "journal",
    "doi",
    "pmid",
    "pmcid",
    "landing_url",
    "pdf_url",
    "local_pdf_relpath",
    "filename",
    "citation_short",
    "citation_apa",
    "metadata_source",
    "quality_score",
    "quality_tier",
    "notes",
]


def main() -> int:
    args = parse_args()
    inventory_rows = read_tsv(resolve_repo_path(args.inventory))
    discovery_rows = read_tsv(resolve_repo_path(args.discovery))
    papers_rows = read_tsv(resolve_repo_path(args.papers))
    manifest_rows = read_tsv(resolve_repo_path(args.manifest))
    correction_rows = read_tsv(resolve_repo_path(args.corrections))
    local_import_paths = args.local_imports or ["data/metadata/local_paper_imports.tsv"]
    local_import_rows = [
        row
        for path in local_import_paths
        for row in read_tsv(resolve_repo_path(path))
    ]
    out_path = resolve_repo_path(args.out)
    existing_registry_rows = read_tsv(out_path)
    existing_registry_paths = {
        clean(row.get("local_pdf_relpath"))
        for row in existing_registry_rows
        if clean(row.get("local_pdf_relpath"))
    }

    records: dict[str, dict[str, str]] = {}
    for row in inventory_rows:
        relpath = clean(row.get("relpath_from_bib"))
        if not relpath:
            continue
        records[relpath] = registry_base_from_inventory(row)
    inventory_paths = set(records)

    preserve_existing_registry_paths(records, existing_registry_rows)
    seed_local_import_paths(records, local_import_rows)
    seed_paper_paths(records, papers_rows)

    merge_discovery(records, discovery_rows)
    merge_manifest(records, manifest_rows)
    merge_papers(records, papers_rows)
    merge_local_imports(records, local_import_rows)
    merge_existing_registry(records, existing_registry_rows)


    preserve_existing_registry_paths(records, existing_registry_rows)
    apply_path_identity_resolutions(
        records,
        local_import_rows,
        existing_local_paths=inventory_paths,
    )
    merge_paper_ids(records, papers_rows)
    merge_corrections(records, correction_rows)
    drop_unidentified_missing_records(records, inventory_paths=inventory_paths)
    finalize_records(records, preserve_author_paths=existing_registry_paths)

    write_tsv(out_path, sorted(records.values(), key=registry_sort_key))
    print(f"Wrote {len(records)} registry rows to {display_path(out_path)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a citation-ready paper registry for local PDFs."
    )
    parser.add_argument("--inventory", default="data/metadata/local_pdf_inventory.tsv")
    parser.add_argument("--discovery", default="data/metadata/open_access_discovery.tsv")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--manifest", default="data/metadata/corpus_manifest.tsv")
    parser.add_argument("--corrections", default="data/metadata/metadata_corrections.tsv")
    parser.add_argument(
        "--local-imports",
        action="append",
        default=None,
        help=(
            "Local-import TSV. Repeat to merge multiple import batches; defaults to "
            "data/metadata/local_paper_imports.tsv."
        ),
    )
    parser.add_argument("--out", default="data/metadata/paper_registry.tsv")
    return parser.parse_args()


def registry_base_from_inventory(row: dict[str, str]) -> dict[str, str]:
    relpath = clean(row.get("relpath_from_bib"))
    author = clean(first_nonempty(row.get("pdf_metadata_author"), row.get("guessed_author")))
    year = clean(row.get("guessed_year"))
    title = first_nonempty(row.get("pdf_metadata_title"), row.get("guessed_title"), row.get("stem"))
    return {
        "bib_key": "",
        "paper_id": "",
        "collection": collection_from_relpath(relpath),
        "category": category_from_relpath(relpath),
        "author": author,
        "year": year,
        "title": title,
        "journal": "",
        "doi": "",
        "pmid": "",
        "pmcid": "",
        "landing_url": "",
        "pdf_url": "",
        "local_pdf_relpath": relpath,
        "filename": clean(row.get("filename")),
        "citation_short": "",
        "citation_apa": "",
        "metadata_source": "inventory",
        "quality_score": "",
        "quality_tier": "",
        "notes": "",
    }


def preserve_existing_registry_paths(
    records: dict[str, dict[str, str]],
    existing_rows: list[dict[str, str]],
) -> None:
    """Keep authoritative rows even when the local PDF mirror is partial.

    Identified bibliography-only records intentionally have no PDF path.  They
    use an internal dictionary key so a registry rebuild preserves them without
    ever leaking that sentinel into ``local_pdf_relpath``.
    """

    path_paper_ids = {
        clean(row.get("paper_id"))
        for row in existing_rows
        if clean(row.get("local_pdf_relpath")) and clean(row.get("paper_id"))
    }
    path_dois = {
        normalize_doi(row.get("doi", ""))
        for row in existing_rows
        if clean(row.get("local_pdf_relpath")) and normalize_doi(row.get("doi", ""))
    }

    for row in existing_rows:
        relpath = clean(row.get("local_pdf_relpath"))
        if relpath:
            records[relpath] = dict(row)
            continue
        paper_id = clean(row.get("paper_id"))
        doi = normalize_doi(row.get("doi", ""))
        if paper_id in path_paper_ids or doi in path_dois:
            continue
        identity = registry_only_identity(row)
        if identity:
            records[f"__registry_only__:{identity}"] = dict(row)


def registry_only_identity(row: dict[str, str]) -> str:
    for field in ("paper_id", "doi", "pmid", "pmcid", "landing_url"):
        value = clean(row.get(field))
        if field == "doi":
            value = normalize_doi(value)
        if value:
            return f"{field}:{value}"
    return ""


def drop_unidentified_missing_records(
    records: dict[str, dict[str, str]],
    *,
    inventory_paths: set[str],
) -> None:
    """Remove stale inventory-only ghosts without a stable scientific identity.

    A registry row with a DOI, PMID, PMCID, paper ID, stable landing page, or
    curated import provenance is retained when a workstation has only a partial
    PDF mirror.  In contrast, a missing path whose only provenance is a past
    inventory scan and which has no stable identifier cannot be resolved or
    cited; retaining it creates phantom duplicate and missing-PDF errors.
    """

    stable_fields = ("paper_id", "doi", "pmid", "pmcid", "landing_url")
    for relpath, row in list(records.items()):
        if relpath in inventory_paths:
            continue
        if any(clean(row.get(field)) for field in stable_fields):
            continue
        sources = {
            token.strip()
            for token in clean(row.get("metadata_source")).split(";")
            if token.strip()
        }
        if sources - {"inventory", "existing_registry"}:
            continue
        del records[relpath]


def apply_path_identity_resolutions(
    records: dict[str, dict[str, str]],
    rows: list[dict[str, str]],
    *,
    existing_local_paths: set[str] | None = None,
) -> None:
    """Apply explicit or exact-identifier-backed corrections to known paths.

    Ordinary local imports must not silently rewrite curated registry records.
    Legacy rows without paper IDs cannot use paper-ID keyed metadata corrections,
    so their deliberately reviewed path resolutions use a narrow note marker.
    The request importer is also safe here after it has returned a downloaded or
    already-present result for an exact DOI, PMID, or PMCID.
    """

    existing_local_paths = existing_local_paths or set()
    resolutions = [
        row
        for row in rows
        if "legacy_identity_resolved_" in clean(row.get("notes"))
        or (
            (
                clean(row.get("status")).lower() in {"already_present", "downloaded"}
                or clean(row.get("local_pdf_relpath")) in existing_local_paths
            )
            and any(clean(row.get(field)) for field in ("doi", "pmid", "pmcid"))
        )
    ]
    if resolutions:
        merge_local_imports(records, resolutions)


def seed_local_import_paths(
    records: dict[str, dict[str, str]],
    rows: list[dict[str, str]],
) -> None:
    """Retain curated local imports when a partial mirror cannot refresh the full inventory."""

    for row in rows:
        relpath = clean(row.get("local_pdf_relpath"))
        status = clean(row.get("status")).lower()
        if status and status not in {"already_present", "downloaded"} and relpath not in records:
            continue
        if relpath:
            records.setdefault(relpath, registry_base_from_relpath(relpath))


def seed_paper_paths(
    records: dict[str, dict[str, str]],
    rows: list[dict[str, str]],
) -> None:
    """Retain active paper rows when the current machine has only a partial PDF mirror."""

    for row in rows:
        if clean(row.get("fulltext_status")).lower() == "duplicate":
            continue
        relpath = clean(row.get("local_pdf_relpath"))
        if relpath:
            records.setdefault(relpath, registry_base_from_relpath(relpath))


def merge_discovery(records: dict[str, dict[str, str]], rows: list[dict[str, str]]) -> None:
    for row in rows:
        relpath = clean(row.get("local_pdf_relpath"))
        if not relpath or relpath not in records:
            continue
        record = records[relpath]
        prefer_source_fields(
            record,
            row,
            fields=[
                "author",
                "year",
                "title",
                "journal",
                "doi",
                "pmid",
                "pmcid",
                "landing_url",
                "pdf_url",
            ],
        )
        merge_fields(
            record, row, fields=["collection", "category", "quality_score", "quality_tier"]
        )
        record["metadata_source"] = append_token(record.get("metadata_source", ""), "discovery")


def merge_manifest(records: dict[str, dict[str, str]], rows: list[dict[str, str]]) -> None:
    for row in rows:
        relpath = clean(row.get("local_pdf_relpath"))
        if not relpath or relpath not in records:
            continue
        record = records[relpath]
        merge_fields(
            record,
            row,
            fields=["collection", "category", "author", "year", "title", "journal"],
        )
        record["metadata_source"] = append_token(record.get("metadata_source", ""), "manifest")
        if clean(row.get("notes")):
            record["notes"] = append_note(record.get("notes", ""), clean(row.get("notes")))


def merge_papers(records: dict[str, dict[str, str]], rows: list[dict[str, str]]) -> None:
    for row in rows:
        if clean(row.get("fulltext_status")).lower() == "duplicate":
            continue
        relpath = clean(row.get("local_pdf_relpath"))
        if not relpath or relpath not in records:
            continue
        record = records[relpath]
        paper_id = clean(row.get("paper_id"))
        if paper_id:
            record["paper_id"] = paper_id



        title = clean(row.get("title"))
        if title and not is_weak_title_value(title):
            record["title"] = title
        for field in ["year", "doi", "pmid", "pmcid"]:
            value = clean(row.get(field))
            if value:
                record[field] = value
        topic_category = clean(row.get("topics"))
        if topic_category and is_weak_registry_value(
            "category", clean(record.get("category")), topic_category
        ):
            record["category"] = topic_category.split(";", 1)[0]
            record["collection"] = collection_from_category(record["category"])
        record["metadata_source"] = append_token(record.get("metadata_source", ""), "papers")
        if clean(row.get("notes")):
            record["notes"] = append_note(record.get("notes", ""), clean(row.get("notes")))


def merge_paper_ids(records: dict[str, dict[str, str]], rows: list[dict[str, str]]) -> None:
    """Apply stable curator-facing IDs without rewriting bibliography fields."""

    for row in rows:
        if clean(row.get("fulltext_status")).lower() == "duplicate":
            continue
        relpath = clean(row.get("local_pdf_relpath"))
        paper_id = clean(row.get("paper_id"))
        if relpath in records and paper_id:
            records[relpath]["paper_id"] = paper_id


def merge_local_imports(records: dict[str, dict[str, str]], rows: list[dict[str, str]]) -> None:
    for row in rows:
        relpath = clean(row.get("local_pdf_relpath"))
        if not relpath or relpath not in records:
            continue
        record = records[relpath]
        prefer_source_fields(
            record,
            row,
            fields=["author", "year", "title", "journal", "doi", "pmid", "pmcid", "landing_url"],
        )
        notes = clean(row.get("notes"))
        if "metadata_verified_" in notes or "legacy_identity_resolved_" in notes:
            for field in [
                "author",
                "year",
                "title",
                "journal",
                "doi",
                "pmid",
                "pmcid",
                "landing_url",
            ]:
                value = clean(row.get(field))
                if value:
                    record[field] = value
        for field in ["collection", "category"]:
            value = clean(row.get(field))
            if value:
                record[field] = value
        record["metadata_source"] = append_token(
            record.get("metadata_source", ""), "local_paper_import"
        )
        if notes:
            record["notes"] = append_note(record.get("notes", ""), notes)


def merge_existing_registry(records: dict[str, dict[str, str]], rows: list[dict[str, str]]) -> None:
    existing_by_relpath = {
        clean(row.get("local_pdf_relpath")): row
        for row in rows
        if clean(row.get("local_pdf_relpath"))
    }
    existing_by_doi: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        doi = normalize_doi(row.get("doi", ""))
        if doi:
            existing_by_doi.setdefault(doi, []).append(row)

    for relpath, record in records.items():
        row = existing_by_relpath.get(relpath)
        if not row:
            doi_matches = existing_by_doi.get(normalize_doi(record.get("doi", "")), [])
            if len(doi_matches) == 1:
                row = doi_matches[0]
        if not row:
            continue
        bibliographic_fields = [
            "author",
            "year",
            "title",
            "journal",
            "doi",
            "pmid",
            "pmcid",
            "landing_url",
            "pdf_url",
        ]
        doi_refreshed = "crossref_doi_refresh" in clean(
            row.get("metadata_source")
        ) or "metadata_refreshed_from_doi" in clean(row.get("notes"))
        if doi_refreshed:
            for field in bibliographic_fields:
                value = clean(row.get(field))
                if value:
                    record[field] = value
        else:
            prefer_source_fields(record, row, fields=bibliographic_fields)
        merge_fields(
            record, row, fields=["collection", "category", "quality_score", "quality_tier"]
        )
        for source in clean(row.get("metadata_source")).split(";"):
            if source.strip():
                record["metadata_source"] = append_token(
                    record.get("metadata_source", ""), source.strip()
                )
        record["metadata_source"] = append_token(
            record.get("metadata_source", ""), "existing_registry"
        )
        if clean(row.get("notes")):
            record["notes"] = append_note(record.get("notes", ""), clean(row.get("notes")))


def merge_corrections(records: dict[str, dict[str, str]], rows: list[dict[str, str]]) -> None:
    corrections_by_paper_id = {
        clean(row.get("paper_id")): row for row in rows if clean(row.get("paper_id"))
    }
    if not corrections_by_paper_id:
        return
    for record in records.values():
        row = corrections_by_paper_id.get(clean(record.get("paper_id")))
        if not row:
            continue
        for field in [
            "collection",
            "category",
            "author",
            "year",
            "title",
            "journal",
            "doi",
            "pmid",
            "pmcid",
            "landing_url",
            "pdf_url",
        ]:
            value = clean(row.get(field))
            if value == "__blank__":
                record[field] = ""
                continue
            if value:
                record[field] = value
        corrected_category = clean(row.get("category"))
        if corrected_category:
            record["collection"] = collection_from_category(corrected_category)
        record["metadata_source"] = append_token(
            record.get("metadata_source", ""), "metadata_corrections"
        )
        if clean(row.get("notes")):
            record["notes"] = append_note(record.get("notes", ""), clean(row.get("notes")))


def finalize_records(
    records: dict[str, dict[str, str]],
    *,
    preserve_author_paths: set[str] | None = None,
) -> None:
    preserve_author_paths = preserve_author_paths or set()
    used_keys: dict[str, int] = {}
    for relpath, record in records.items():
        if not record.get("collection"):
            record["collection"] = collection_from_relpath(record.get("local_pdf_relpath", ""))
        if not record.get("category"):
            record["category"] = category_from_relpath(record.get("local_pdf_relpath", ""))
        if relpath in preserve_author_paths:
            record["author"] = clean(record.get("author", ""))
        else:
            record["author"] = clean_author(record.get("author", ""))
        record["year"] = normalize_year(record.get("year", ""))
        record["title"] = clean_title(record.get("title", ""))
        base_key = make_bib_key(
            record.get("author", ""), record.get("year", ""), record.get("title", "")
        )
        record["bib_key"] = unique_key(base_key, used_keys)
        record["citation_short"] = citation_short(record)
        record["citation_apa"] = citation_apa(record)
        for field in FIELDS:
            record.setdefault(field, "")


def registry_base_from_relpath(relpath: str) -> dict[str, str]:
    return {field: "" for field in FIELDS} | {
        "local_pdf_relpath": relpath,
        "filename": Path(relpath).name,
        "collection": collection_from_relpath(relpath),
        "category": category_from_relpath(relpath),
    }


def merge_fields(record: dict[str, str], source: dict[str, str], *, fields: list[str]) -> None:
    for field in fields:
        value = clean(source.get(field))
        if field in {"category", "collection"} and value in {"papers", "keys"}:
            continue
        if value and not clean(record.get(field)):
            record[field] = value
        elif value and is_weak_registry_value(field, clean(record.get(field)), value):
            record[field] = value


def prefer_source_fields(
    record: dict[str, str], source: dict[str, str], *, fields: list[str]
) -> None:
    for field in fields:
        value = clean(source.get(field))
        if not value:
            continue
        current = clean(record.get(field))
        if not current or is_weak_inventory_value(field, current, value):
            record[field] = value


def is_weak_inventory_value(field: str, current: str, source_value: str) -> bool:
    if field == "author":
        if is_placeholder_author(current):
            return True
        if is_placeholder_author(source_value):
            return False
        if "_" in current or current.lower() == current or len(current) <= 3:
            return True
        return is_placeholder_author(current)
    if field == "title":
        if is_weak_title_value(source_value):
            return False
        if is_weak_title_value(current):
            return True
        current_words = len(re.findall(r"[A-Za-z]{3,}", current))
        source_words = len(re.findall(r"[A-Za-z]{3,}", source_value))
        return "_" in current or current_words < 4 or source_words > current_words + 3
    return False


def is_weak_title_value(value: str) -> bool:
    title = clean(value)
    if not title:
        return True
    if re.fullmatch(
        r"(?i)(abstract|introduction|discussion|references|untitled|pdf update)", title
    ):
        return True
    if re.search(r"(?i)(^|[/\\])[^/\\]+\.(pdf|dvi)$", title):
        return True
    if re.match(r"(?i)^[a-z]:\\", title):
        return True
    if re.fullmatch(r"(?i)[a-z]+[_-]?\d{4}", title):
        return True
    words = re.findall(r"[A-Za-z]{3,}", title)
    return len(words) < 3


def is_weak_registry_value(field: str, current: str, source_value: str) -> bool:
    if not current:
        return True
    if field == "category":
        return current in {"papers", "keys", "unknown"} and source_value not in {
            "papers",
            "keys",
            "unknown",
        }
    if field == "collection":
        return current in {"papers", "keys", "unknown"} and source_value not in {
            "papers",
            "keys",
            "unknown",
        }
    return False


def collection_from_relpath(relpath: str) -> str:
    category = category_from_relpath(relpath)
    return collection_from_category(category) if category else ""


def collection_from_category(category: str) -> str:
    if category == "other" or category.startswith("topic_"):
        return "general_literature"
    if category in {"scrna_annotation", "sc_harmonization", "sc_tools"}:
        return "scrna_annotation"
    if category in {"tools_ml", "foundation_models", "evidence_synthesis"}:
        return "bioinformatics_tools"
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
    if category:
        return "malaria"
    return ""


def category_from_relpath(relpath: str) -> str:
    parts = Path(relpath).parts
    if parts and parts[0] in {"papers", "keys"}:
        return ""
    return parts[0] if len(parts) > 1 else ""


def make_bib_key(author: str, year: str, title: str) -> str:
    author_part = normalize_key_part(author or "unknown")
    year_part = year if re.fullmatch(r"\d{4}", year or "") else "nd"
    title_terms = [
        term for term in re.findall(r"[A-Za-z0-9]+", title) if term.lower() not in STOPWORDS
    ]
    suffix = "".join(term[:1].upper() + term[1:18] for term in title_terms[:2])
    return f"{author_part}{year_part}{suffix}"[:80] or "unknown"


def unique_key(base_key: str, used_keys: dict[str, int]) -> str:
    count = used_keys.get(base_key, 0) + 1
    used_keys[base_key] = count
    return base_key if count == 1 else f"{base_key}_{count}"


def citation_short(record: dict[str, str]) -> str:
    author = record.get("author") or "Unknown"
    year = record.get("year") or "n.d."
    return f"{author} et al., {year}"


def citation_apa(record: dict[str, str]) -> str:
    author = record.get("author") or "Unknown"
    year = record.get("year") or "n.d."
    title = record.get("title") or "Untitled"
    journal = record.get("journal") or ""
    doi = record.get("doi") or ""
    parts = [f"{author} et al. ({year}).", ensure_period(title)]
    if journal:
        parts.append(ensure_period(journal))
    if doi:
        parts.append(f"https://doi.org/{doi}")
    return " ".join(parts)


def registry_sort_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        row.get("collection", ""),
        row.get("category", ""),
        row.get("author", ""),
        row.get("year", ""),
    )


def clean_author(value: str) -> str:
    author = clean(value)
    if is_placeholder_author(author):
        return ""
    author = re.sub(r"<[^>]+>", "", author)
    author = author.replace("_", " ").strip()
    if not author:
        return ""
    if "," in author:
        return author.split(",", 1)[0].strip()
    parts = author.split()
    if len(parts) == 1:
        return author
    if len(parts) == 2:
        if looks_like_initials(parts[0]) and not looks_like_initials(parts[1]):
            return parts[1]
        if looks_like_initials(parts[1]):
            return parts[0]
    if len(parts) >= 3 and any(looks_like_initials(part) for part in parts[1:-1]):
        return parts[-1]
    return parts[0]


def looks_like_initials(value: str) -> bool:
    cleaned = re.sub(r"[^A-Za-z]", "", value)
    return bool(cleaned and len(cleaned) <= 4 and cleaned.upper() == cleaned)


def is_placeholder_author(value: str) -> bool:
    if not value:
        return True
    normalized = re.sub(r"[\s_]+", "", value.strip())
    return bool(PLACEHOLDER_AUTHOR_PATTERN.fullmatch(normalized))


def clean_title(value: str) -> str:
    title = re.sub(r"<[^>]+>", "", clean(value))
    return " ".join(title.replace("\n", " ").split())


def normalize_year(value: str) -> str:
    match = re.search(r"(19|20)\d{2}", value or "")
    return match.group(0) if match else ""


def normalize_doi(value: object) -> str:
    doi = clean(value).lower()
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)


def ensure_period(value: str) -> str:
    text = clean(value)
    return text if text.endswith((".", "?", "!")) else f"{text}."


def normalize_key_part(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    clean_value = re.sub(r"[^A-Za-z0-9]+", "", ascii_value)
    if not clean_value:
        return "unknown"
    return clean_value[:1].upper() + clean_value[1:]


def append_token(existing: object, token: object) -> str:
    tokens = [item for item in clean(existing).split(";") if item]
    clean_token = clean(token)
    if clean_token and clean_token not in tokens:
        tokens.append(clean_token)
    return ";".join(tokens)


def append_note(existing: object, note: object) -> str:
    parts: list[str] = []
    for value in [existing, note]:
        parts.extend(part.strip() for part in clean(value).split(";") if part.strip())
    seen: set[str] = set()
    deduped: list[str] = []
    for part in parts:
        key = re.sub(r"\s+", " ", part).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(part)
    return "; ".join(deduped)


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=FIELDS,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def first_nonempty(*values: object) -> str:
    for value in values:
        text = clean(value)
        if text:
            return text
    return ""


def clean(value: object) -> str:
    return str(value or "").strip()


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def display_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
}


if __name__ == "__main__":
    raise SystemExit(main())
