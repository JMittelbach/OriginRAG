from __future__ import annotations

import argparse
import csv
import html
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CROSSREF_WORKS_URL = "https://api.crossref.org/works"
USER_AGENT = "OriginRAG/0.1 local metadata refresh; mailto:local-user@example.invalid"
PLACEHOLDER_AUTHOR_PATTERN = re.compile(r"(?i)^anna\s*import\d*$")
WEAK_AUTHOR_VALUES = {"administrator", "phd", "unknown", "nd"}
TITLE_ARTIFACT_PATTERNS = (
    r"^abstract\b",
    r"^research\b",
    r"blackwell publishing",
    r"blackwell science",
    r"please cite this article",
    r"published online.*doi:",
    r"https?://(?:dx\.)?doi\.org",
    r"\bet al\.\s+elife\b",
    r"^article is distributed under",
    r"^genes as well as additional",
    r"^onlineopen:",
)


def main() -> int:
    args = parse_args()
    registry_path = resolve_repo_path(args.registry)
    rows = read_tsv(registry_path)
    selected_paper_ids = {value.strip() for value in args.paper_ids if value.strip()}
    selected_dois = {normalize_doi(value) for value in args.dois if normalize_doi(value)}
    updated = 0
    checked = 0
    for row in rows:
        doi = normalize_doi(row.get("doi", ""))
        if not doi:
            continue
        if selected_paper_ids or selected_dois:
            if row.get("paper_id", "").strip() not in selected_paper_ids and doi not in selected_dois:
                continue
        if not args.all and not needs_refresh(row):
            continue
        if args.limit > 0 and checked >= args.limit:
            break
        checked += 1
        try:
            metadata = fetch_crossref_by_doi(doi)
        except Exception as exc:
            row["notes"] = append_note(row.get("notes", ""), f"crossref_doi_refresh_failed:{short_error(exc)}")
            continue
        before = dict(row)
        apply_crossref_metadata(row, metadata, overwrite=args.overwrite)
        row["notes"] = remove_note_prefix(row.get("notes", ""), "crossref_doi_refresh_failed:")
        if row != before:
            updated += 1
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    if not args.dry_run:
        write_tsv(registry_path, rows, list(rows[0].keys()) if rows else [])
    print(f"Checked DOI rows: {checked}")
    print(f"Updated registry rows: {updated}")
    if args.dry_run:
        print("Dry run: registry was not modified.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh weak registry metadata by DOI using Crossref.")
    parser.add_argument("--registry", default="data/metadata/paper_registry.tsv")
    parser.add_argument("--all", action="store_true", help="Refresh all DOI rows, not just weak rows.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep-seconds", type=float, default=0.02)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing bibliographic fields with DOI registry values for selected rows.",
    )
    parser.add_argument("--paper-ids", nargs="*", default=[], help="Only refresh these paper IDs.")
    parser.add_argument("--dois", nargs="*", default=[], help="Only refresh these DOI values.")
    return parser.parse_args()


def needs_refresh(row: dict[str, str]) -> bool:
    return (
        is_weak_author(row.get("author", ""))
        or is_weak_title(row.get("title", ""))
        or not row.get("journal", "").strip()
        or not re.fullmatch(r"(19|20)\d{2}", row.get("year", ""))
    )


def apply_crossref_metadata(row: dict[str, str], item: dict, *, overwrite: bool = False) -> None:
    title = first_list_value(item.get("title"))
    journal = first_list_value(item.get("container-title"))
    author = crossref_first_author(item)
    year = crossref_year(item)
    if title and (overwrite or is_weak_title(row.get("title", ""))):
        row["title"] = title
    if journal and (overwrite or not row.get("journal")):
        row["journal"] = journal
    if author and (overwrite or is_weak_author(row.get("author", ""))):
        row["author"] = author
    if year and (overwrite or not re.fullmatch(r"(19|20)\d{2}", row.get("year", ""))):
        row["year"] = year
    if not row.get("landing_url") and row.get("doi"):
        row["landing_url"] = f"https://doi.org/{normalize_doi(row['doi'])}"
    row["metadata_source"] = append_note(row.get("metadata_source", ""), "crossref_doi_refresh")
    row["notes"] = append_note(row.get("notes", ""), "metadata_refreshed_from_doi")
    refresh_citations(row)


def refresh_citations(row: dict[str, str]) -> None:
    author = row.get("author", "").strip()
    year = row.get("year", "").strip()
    title = row.get("title", "").strip()
    journal = row.get("journal", "").strip()
    doi = normalize_doi(row.get("doi", ""))
    if not author or not year:
        return
    row["citation_short"] = f"{author} et al., {year}"
    if title:
        journal_part = f" {journal}." if journal else ""
        doi_part = f" https://doi.org/{doi}" if doi else ""
        row["citation_apa"] = f"{author} et al. ({year}). {title}.{journal_part}{doi_part}"


def fetch_crossref_by_doi(doi: str, *, timeout_seconds: int = 30) -> dict:
    encoded = urllib.parse.quote(doi, safe="")
    request = urllib.request.Request(f"{CROSSREF_WORKS_URL}/{encoded}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    message = payload.get("message", {})
    return message if isinstance(message, dict) else {}


def is_weak_author(value: str) -> bool:
    author = (value or "").strip()
    if not author:
        return True
    if is_placeholder_author(author):
        return True
    if author.lower() in WEAK_AUTHOR_VALUES:
        return True
    if re.fullmatch(r"\d+", author):
        return True
    if "_" in author:
        return True
    return False


def is_weak_title(value: str) -> bool:
    title = (value or "").strip()
    if not title:
        return True
    if "_" in title:
        return True
    if re.fullmatch(r"[A-Za-z]*\d+[A-Za-z0-9 ._-]*", title):
        return True
    if any(re.search(pattern, title, flags=re.IGNORECASE) for pattern in TITLE_ARTIFACT_PATTERNS):
        return True
    words = re.findall(r"[A-Za-z]{3,}", title)
    return len(words) < 4


def first_list_value(value: object) -> str:
    if isinstance(value, list) and value:
        return clean_crossref_text(value[0])
    return clean_crossref_text(value)


def clean_crossref_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(text.split())


def crossref_first_author(item: dict) -> str:
    authors = item.get("author")
    if not isinstance(authors, list) or not authors:
        return ""
    first = authors[0]
    if not isinstance(first, dict):
        return ""
    family = str(first.get("family") or "").strip()
    if family:
        return family
    name = str(first.get("name") or "").strip()
    return name.split()[-1] if name else ""


def crossref_year(item: dict) -> str:
    for key in ["published-print", "published-online", "published", "created", "issued"]:
        raw = item.get(key)
        if not isinstance(raw, dict):
            continue
        date_parts = raw.get("date-parts")
        if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list) and date_parts[0]:
            year = str(date_parts[0][0] or "").strip()
            if re.fullmatch(r"(19|20)\d{2}", year):
                return year
    return ""


def normalize_doi(value: str) -> str:
    doi = (value or "").strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    return doi.lower()


def append_note(existing: str, note: str) -> str:
    values = [value for value in [existing.strip(), note.strip()] if value]
    seen = set()
    deduped = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return ";".join(deduped)


def remove_note_prefix(value: str, prefix: str) -> str:
    return ";".join(
        token
        for token in (part.strip() for part in (value or "").split(";"))
        if token and not token.startswith(prefix)
    )


def short_error(exc: Exception) -> str:
    return re.sub(r"\s+", "_", str(exc))[:80]


def is_placeholder_author(value: str) -> bool:
    if not value:
        return True
    normalized = re.sub(r"[\s_]+", "", value.strip())
    return bool(PLACEHOLDER_AUTHOR_PATTERN.fullmatch(normalized))


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
