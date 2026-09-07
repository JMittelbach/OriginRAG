from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import fitz
from rapidfuzz import fuzz


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.discovery.europepmc import EuropePMCHit, search_one  # noqa: E402
from pfempire.discovery.openalex import search_openalex_title  # noqa: E402
from pfempire.discovery.types import DiscoveryHit  # noqa: E402


REPORT_FIELDS = [
    "bib_key",
    "local_pdf_relpath",
    "author",
    "year",
    "title",
    "match_status",
    "match_score",
    "source",
    "matched_title",
    "doi",
    "pmid",
    "pmcid",
    "message",
]
CROSSREF_WORKS_URL = "https://api.crossref.org/works"
USER_AGENT = "OriginRAG/0.1 local DOI enrichment; mailto:local-user@example.invalid"
PLACEHOLDER_AUTHOR_PATTERN = re.compile(r"(?i)^anna\s*import\d*$")


def main() -> int:
    args = parse_args()
    registry_path = resolve_repo_path(args.registry)
    inventory_rows = index_inventory(read_tsv(resolve_repo_path(args.inventory)))
    bib_dir = resolve_repo_path(args.bib_dir)
    report_path = resolve_repo_path(args.report)
    rows = read_tsv(registry_path)

    report_rows: list[dict[str, str]] = []
    checked = 0
    updated = 0
    for row in rows:
        if row.get("doi", "").strip():
            continue
        inventory_row = inventory_rows.get(row.get("local_pdf_relpath", ""), {})
        title_candidates = local_title_candidates(row, inventory_row)
        direct_doi = extract_doi_from_local_metadata(row, inventory_row)
        if not direct_doi:
            direct_doi = extract_doi_from_pdf(bib_dir, row, max_pages=args.pdf_pages)
        if direct_doi:
            report_rows.append(
                report_row(
                    row,
                    "matched",
                    "100.0",
                    "local_pdf_text",
                    first_nonempty(title_candidates),
                    direct_doi,
                    "",
                    "",
                    "DOI found directly in local PDF metadata/text",
                )
            )
            if not args.dry_run:
                apply_direct_doi(row, direct_doi)
            updated += 1
            continue
        if not title_candidates:
            report_rows.append(report_row(row, "skipped", "", "", "", "", "", "", "missing local title"))
            continue
        if args.limit > 0 and checked >= args.limit:
            break
        checked += 1

        match = best_metadata_match(row, title_candidates=title_candidates, min_score=args.min_score)
        status = "not_found"
        if match:
            status = "matched"
            report_rows.append(
                report_row(
                    row,
                    status,
                    f"{match.score:.1f}",
                    match.source,
                    match.title,
                    match.doi,
                    match.pmid,
                    match.pmcid,
                    "high-confidence title match",
                )
            )
            if not args.dry_run:
                apply_match(row, match)
            updated += 1
        else:
            report_rows.append(report_row(row, status, "", "", "", "", "", "", "no high-confidence DOI match"))
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    if not args.dry_run:
        write_tsv(registry_path, rows, list(rows[0].keys()) if rows else [])
    write_tsv(report_path, report_rows, REPORT_FIELDS)
    print(f"Checked missing-DOI rows: {checked}")
    print(f"DOI matches accepted: {updated}")
    print(f"Registry: {display_path(registry_path)}")
    print(f"Report: {display_path(report_path)}")
    if args.dry_run:
        print("Dry run: registry was not modified.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Conservatively enrich paper_registry.tsv with missing DOI metadata.")
    parser.add_argument("--registry", default="data/metadata/paper_registry.tsv")
    parser.add_argument("--inventory", default="data/metadata/local_pdf_inventory.tsv")
    parser.add_argument("--report", default="data/metadata/doi_enrichment_report.tsv")
    parser.add_argument("--min-score", type=float, default=92.0)
    parser.add_argument("--limit", type=int, default=0, help="Maximum missing-DOI rows to check. 0 means all.")
    parser.add_argument("--bib-dir", default="bib", help="Local bib directory used for optional PDF text DOI extraction.")
    parser.add_argument("--pdf-pages", type=int, default=4, help="Number of early PDF pages to scan for DOI text.")
    parser.add_argument("--sleep-seconds", type=float, default=0.1, help="Pause between records to be polite to APIs.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


class MetadataMatch:
    def __init__(
        self,
        *,
        source: str,
        title: str,
        score: float,
        doi: str,
        pmid: str = "",
        pmcid: str = "",
        author: str = "",
        year: str = "",
        journal: str = "",
        landing_url: str = "",
    ) -> None:
        self.source = source
        self.title = title
        self.score = score
        self.doi = normalize_doi(doi)
        self.pmid = pmid.strip()
        self.pmcid = normalize_pmcid(pmcid)
        self.author = author.strip()
        self.year = year.strip()
        self.journal = journal.strip()
        self.landing_url = landing_url.strip()


def best_metadata_match(row: dict[str, str], *, title_candidates: list[str], min_score: float) -> MetadataMatch | None:
    candidates: list[MetadataMatch] = []
    year = row.get("year", "").strip()

    for title in title_candidates:
        try:
            hit = search_one(title=title)
        except Exception as exc:
            hit = None
            row["notes"] = append_note(row.get("notes", ""), f"europepmc_enrichment_failed:{short_error(exc)}")
        if hit:
            candidates.append(match_from_europepmc(title_candidates, hit))

        try:
            candidates.extend(match_from_openalex(title_candidates, hit) for hit in search_openalex_title(title, year=year, page_size=5))
        except Exception as exc:
            row["notes"] = append_note(row.get("notes", ""), f"openalex_enrichment_failed:{short_error(exc)}")
        try:
            candidates.extend(match_from_crossref(title_candidates, item) for item in search_crossref_title(title, year=year, rows=5))
        except Exception as exc:
            row["notes"] = append_note(row.get("notes", ""), f"crossref_enrichment_failed:{short_error(exc)}")

    candidates = [candidate for candidate in candidates if candidate.doi and candidate.score >= min_score]
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: (candidate.score, bool(candidate.pmid), bool(candidate.pmcid)))


def match_from_europepmc(title_candidates: list[str], hit: EuropePMCHit) -> MetadataMatch:
    return MetadataMatch(
        source="europepmc",
        title=hit.title,
        score=best_title_score(title_candidates, hit.title),
        doi=hit.doi,
        pmid=hit.pmid,
        pmcid=hit.pmcid,
        author=hit.author,
        year=hit.year,
        journal=hit.journal,
        landing_url=europepmc_landing_url(hit),
    )


def match_from_openalex(title_candidates: list[str], hit: DiscoveryHit) -> MetadataMatch:
    return MetadataMatch(
        source="openalex",
        title=hit.title,
        score=best_title_score(title_candidates, hit.title),
        doi=hit.doi,
        pmid=hit.pmid,
        pmcid=hit.pmcid,
        author=hit.author,
        year=hit.year,
        journal=hit.journal,
        landing_url=hit.landing_url,
    )


def match_from_crossref(title_candidates: list[str], item: dict) -> MetadataMatch:
    title = first_list_value(item.get("title"))
    container = first_list_value(item.get("container-title"))
    year = crossref_year(item)
    author = crossref_first_author(item)
    doi = normalize_doi(str(item.get("DOI") or ""))
    return MetadataMatch(
        source="crossref",
        title=title,
        score=best_title_score(title_candidates, title),
        doi=doi,
        author=author,
        year=year,
        journal=container,
        landing_url=f"https://doi.org/{doi}" if doi else "",
    )


def title_score(left: str, right: str) -> float:
    return float(fuzz.token_set_ratio(normalize_title(left), normalize_title(right)))


def best_title_score(candidates: list[str], title: str) -> float:
    return max((title_score(candidate, title) for candidate in candidates), default=0.0)


def apply_direct_doi(row: dict[str, str], doi: str) -> None:
    doi = normalize_doi(doi)
    row["doi"] = doi
    row["metadata_source"] = append_note(row.get("metadata_source", ""), "doi_enriched_local_pdf_text")
    row["notes"] = append_note(row.get("notes", ""), "doi_found_in_local_pdf_text")
    row["citation_apa"] = append_doi_to_citation(row.get("citation_apa", ""), doi)


def apply_match(row: dict[str, str], match: MetadataMatch) -> None:
    row["doi"] = match.doi
    if match.pmid and not row.get("pmid"):
        row["pmid"] = match.pmid
    if match.pmcid and not row.get("pmcid"):
        row["pmcid"] = match.pmcid
    if match.author and is_weak_author(row.get("author", "")):
        row["author"] = match.author
    if match.year and not row.get("year"):
        row["year"] = match.year
    if match.journal and not row.get("journal"):
        row["journal"] = match.journal
    if match.landing_url and not row.get("landing_url"):
        row["landing_url"] = match.landing_url
    row["metadata_source"] = append_note(row.get("metadata_source", ""), f"doi_enriched_{match.source}")
    row["notes"] = append_note(row.get("notes", ""), f"doi_enriched_score={match.score:.1f}")
    row["citation_apa"] = append_doi_to_citation(row.get("citation_apa", ""), match.doi)


def report_row(
    row: dict[str, str],
    status: str,
    score: str,
    source: str,
    matched_title: str,
    doi: str,
    pmid: str,
    pmcid: str,
    message: str,
) -> dict[str, str]:
    return {
        "bib_key": row.get("bib_key", ""),
        "local_pdf_relpath": row.get("local_pdf_relpath", ""),
        "author": row.get("author", ""),
        "year": row.get("year", ""),
        "title": row.get("title", ""),
        "match_status": status,
        "match_score": score,
        "source": source,
        "matched_title": matched_title,
        "doi": normalize_doi(doi),
        "pmid": pmid.strip(),
        "pmcid": normalize_pmcid(pmcid),
        "message": message,
    }


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"Missing TSV: {display_path(path)}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def index_inventory(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row.get("relpath_from_bib", ""): row for row in rows if row.get("relpath_from_bib")}


def write_tsv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def resolve_repo_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def display_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def local_title_candidates(row: dict[str, str], inventory_row: dict[str, str]) -> list[str]:
    candidates: list[str] = []
    for value in [
        row.get("title", ""),
        inventory_row.get("pdf_metadata_title", ""),
        inventory_row.get("guessed_title", ""),
        inventory_row.get("stem", ""),
    ]:
        add_title_candidate(candidates, value)
    for value in sample_title_candidates(inventory_row.get("first_page_text_sample", "")):
        add_title_candidate(candidates, value)
    return candidates


def add_title_candidate(candidates: list[str], value: str) -> None:
    title = clean_title_candidate(value)
    if not title or is_weak_title_candidate(title):
        return
    key = normalize_title(title)
    if key and key not in {normalize_title(candidate) for candidate in candidates}:
        candidates.append(title)


def sample_title_candidates(sample: str) -> list[str]:
    text = clean_title_candidate(sample)
    if not text:
        return []
    text = re.sub(r"\b(?:RESEARCH ARTICLE|ARTICLE IN PRESS|ARTICLE|Articles)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\bdoi:\s*10\.\S+", " ", text, flags=re.IGNORECASE)
    text = clean_title_candidate(text)
    words = text.split()
    candidates: list[str] = []
    for length in [12, 18, 24, 32]:
        if len(words) >= length:
            candidates.append(" ".join(words[:length]))
    return candidates


def clean_title_candidate(value: str) -> str:
    title = " ".join((value or "").replace("\n", " ").split())
    title = re.sub(r"^\d+[_\s-]+", "", title)
    return title.strip(" .;:_-")


def is_weak_title_candidate(value: str) -> bool:
    normalized = normalize_title(value)
    words = re.findall(r"[a-z]{3,}", normalized)
    if len(words) < 3:
        return True
    if re.fullmatch(r"[a-z]*\d+[a-z0-9 ]*", normalized):
        return True
    if len(value) > 240:
        return True
    return False


def extract_doi_from_local_metadata(row: dict[str, str], inventory_row: dict[str, str]) -> str:
    for field in [
        row.get("doi", ""),
        row.get("citation_apa", ""),
        row.get("title", ""),
        inventory_row.get("pdf_metadata_title", ""),
        inventory_row.get("first_page_text_sample", ""),
    ]:
        doi = extract_doi(str(field or ""))
        if doi:
            return doi
    return ""


def extract_doi_from_pdf(bib_dir: Path, row: dict[str, str], *, max_pages: int) -> str:
    relpath = row.get("local_pdf_relpath", "").strip()
    if not relpath:
        return ""
    pdf_path = bib_dir / relpath
    if not pdf_path.exists() or not pdf_path.is_file():
        return ""
    try:
        with fitz.open(pdf_path) as document:
            page_count = min(max_pages, document.page_count)
            for page_index in range(page_count):
                doi = extract_doi(document.load_page(page_index).get_text("text"))
                if doi:
                    return doi
    except Exception:
        return ""
    return ""


def search_crossref_title(title: str, *, year: str = "", rows: int = 5, timeout_seconds: int = 30) -> list[dict]:
    params = {
        "query.bibliographic": title,
        "rows": str(max(1, min(rows, 20))),
    }
    if year.isdigit():
        params["filter"] = f"from-pub-date:{int(year) - 1},until-pub-date:{int(year) + 1}"
    url = f"{CROSSREF_WORKS_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    items = payload.get("message", {}).get("items", [])
    return [item for item in items if isinstance(item, dict)]


def first_list_value(value: object) -> str:
    if isinstance(value, list) and value:
        return str(value[0] or "").strip()
    return str(value or "").strip()


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


def extract_doi(value: str) -> str:
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", value or "")
    if not match:
        return ""
    doi = normalize_doi(match.group(0).rstrip(".,);:"))
    suffix = doi.split("/", 1)[1] if "/" in doi else ""


    if not any(character.isdigit() for character in suffix):
        return ""
    return doi


def normalize_doi(value: str) -> str:
    doi = (value or "").strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    return doi.lower()


def normalize_pmcid(value: str) -> str:
    pmcid = (value or "").strip().upper()
    if not pmcid:
        return ""
    return pmcid if pmcid.startswith("PMC") else f"PMC{pmcid}"


def europepmc_landing_url(hit: EuropePMCHit) -> str:
    if hit.pmcid:
        return f"https://europepmc.org/articles/{normalize_pmcid(hit.pmcid)}"
    if hit.pmid:
        return f"https://europepmc.org/article/MED/{hit.pmid}"
    if hit.doi:
        return f"https://doi.org/{normalize_doi(hit.doi)}"
    return ""


def append_note(current: str, note: str) -> str:
    values = [item for item in [current.strip(), note.strip()] if item]
    seen = set()
    deduped = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return ";".join(deduped)


def is_placeholder_author(value: str) -> bool:
    if not value:
        return True
    normalized = re.sub(r"[\s_]+", "", value.strip())
    return bool(PLACEHOLDER_AUTHOR_PATTERN.fullmatch(normalized))


def is_weak_author(value: str) -> bool:
    author = (value or "").strip()
    if not author:
        return True
    if is_placeholder_author(author):
        return True
    if re.fullmatch(r"\d+", author):
        return True
    return False


def append_doi_to_citation(citation: str, doi: str) -> str:
    doi = normalize_doi(doi)
    if not citation or not doi or "doi.org" in citation.lower() or doi in citation.lower():
        return citation
    return f"{citation.rstrip('.')} https://doi.org/{doi}"


def first_nonempty(values: list[str]) -> str:
    for value in values:
        if value:
            return value
    return ""


def short_error(exc: Exception) -> str:
    return re.sub(r"\s+", "_", str(exc))[:80]


if __name__ == "__main__":
    raise SystemExit(main())
