from __future__ import annotations

import argparse
import csv
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial
from pathlib import Path
from threading import Lock

from rapidfuzz import fuzz


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from originrag.discovery.europepmc import (  # noqa: E402
    download_pdf,
    download_pmc_oa_pdf,
    pdf_url_candidates,
    search_one,
)
from originrag.paths import OriginRAGConfigError, get_bib_dir, load_project_env  # noqa: E402
from originrag.provenance.library import (  # noqa: E402
    normalize_doi,
    normalize_pmcid,
    normalize_pmid,
)


REQUEST_FIELDS = [
    "doi",
    "pmid",
    "pmcid",
    "author",
    "year",
    "title",
    "journal",
    "category",
    "pdf_url",
    "notes",
]
REPORT_FIELDS = [
    "imported_at",
    "status",
    "author",
    "year",
    "title",
    "journal",
    "doi",
    "pmid",
    "pmcid",
    "category",
    "pdf_url",
    "local_pdf_relpath",
    "message",
]


def main() -> int:
    args = parse_args()
    load_project_env(REPO_ROOT / ".env")
    requests_path = resolve_repo_path(args.requests)
    if not requests_path.exists():
        create_request_template(requests_path)
        print(f"Created empty request template: {requests_path.relative_to(REPO_ROOT)}")
        print("Fill it with DOI/PMID/PMCID/title rows, then rerun:")
        print(f"  ./terminal import-papers --requests {requests_path.relative_to(REPO_ROOT)}")
        return 0

    try:
        bib_dir = get_bib_dir()
    except OriginRAGConfigError:
        bib_dir = REPO_ROOT / "bib"
    registry_path = resolve_repo_path(args.registry)
    inventory_titles = load_existing_titles(
        resolve_repo_path(args.inventory),
        bib_dir=bib_dir,
        registry_path=registry_path,
    )
    existing_identifiers = load_existing_identifiers(
        registry_path,
        bib_dir=bib_dir,
    )
    rows = read_requests(requests_path)
    reserved_relpaths: set[Path] = set()
    allocation_lock = Lock()
    process = partial(
        process_request,
        bib_dir=bib_dir,
        inventory_titles=inventory_titles,
        existing_identifiers=existing_identifiers,
        dry_run=args.dry_run,
        download_timeout_seconds=args.download_timeout_seconds,
        reserved_relpaths=reserved_relpaths,
        allocation_lock=allocation_lock,
    )
    if args.workers == 1:
        report_rows = [process(row) for row in rows]
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            report_rows = list(executor.map(process, rows))

    out_path = resolve_repo_path(args.out)
    write_tsv(out_path, REPORT_FIELDS, report_rows)
    print(f"Wrote import report to {out_path.relative_to(REPO_ROOT)}")
    print("Refresh inventory after successful imports:")
    print("  python scripts/inventory_bib_pdfs.py --out data/metadata/local_pdf_inventory.tsv")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import requested open-access papers into the local bib directory.")
    parser.add_argument("--requests", default="data/metadata/paper_requests.tsv")
    parser.add_argument("--inventory", default="data/metadata/local_pdf_inventory.tsv")
    parser.add_argument("--registry", default="data/metadata/paper_registry.tsv")
    parser.add_argument("--out", default="data/metadata/paper_import_report.tsv")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--workers",
        type=int,
        choices=range(1, 9),
        default=1,
        metavar="1-8",
        help="Concurrent metadata/download workers. The conservative default is 1.",
    )
    parser.add_argument(
        "--download-timeout-seconds",
        type=int,
        default=45,
        help="Timeout for each candidate PDF URL (default: 45 seconds).",
    )
    return parser.parse_args()


def process_request(
    row: dict[str, str],
    *,
    bib_dir: Path,
    inventory_titles: list[dict[str, str]],
    existing_identifiers: dict[str, str],
    dry_run: bool,
    download_timeout_seconds: int = 45,
    reserved_relpaths: set[Path] | None = None,
    allocation_lock: Lock | None = None,
) -> dict[str, str]:
    category = row.get("category") or "general_malaria"
    requested_identifier = first_nonempty(
        identifier_for("doi", row.get("doi", "")),
        identifier_for("pmcid", row.get("pmcid", "")),
        identifier_for("pmid", row.get("pmid", "")),
    )
    if requested_identifier and requested_identifier in existing_identifiers:
        return report_row(
            status="already_present",
            author="",
            year="",
            title=row.get("title", ""),
            journal="",
            doi=row.get("doi", ""),
            pmid=row.get("pmid", ""),
            pmcid=row.get("pmcid", ""),
            category=category,
            pdf_url=row.get("pdf_url", ""),
            relpath=existing_identifiers[requested_identifier],
            message=f"same identifier already in local registry: {requested_identifier}",
        )
    direct_pdf_url = (row.get("pdf_url") or "").strip()
    candidate_urls: list[str] = [direct_pdf_url] if direct_pdf_url else []
    hit = None
    lookup_error = ""
    if not direct_pdf_url:
        try:
            hit = search_one(
                doi=row.get("doi", ""),
                pmid=row.get("pmid", ""),
                pmcid=row.get("pmcid", ""),
                title=row.get("title", ""),
            )
        except (OSError, TimeoutError, ValueError) as exc:
            lookup_error = f"Europe PMC lookup failed: {type(exc).__name__}: {exc}"
        if hit and not hit_matches_request(row, hit):
            hit = None
        if hit:
            candidate_urls.extend(pdf_url_candidates(hit))
    elif row.get("doi") or row.get("pmid") or row.get("pmcid") or row.get("title"):
        try:
            hit = search_one(
                doi=row.get("doi", ""),
                pmid=row.get("pmid", ""),
                pmcid=row.get("pmcid", ""),
                title=row.get("title", ""),
            )
        except (OSError, TimeoutError, ValueError) as exc:
            lookup_error = f"Europe PMC lookup failed: {type(exc).__name__}: {exc}"
        if hit and not hit_matches_request(row, hit):
            hit = None
        if hit:
            candidate_urls.extend(pdf_url_candidates(hit))

    candidate_urls = list(dict.fromkeys(url for url in candidate_urls if url))
    direct_pdf_url = candidate_urls[0] if candidate_urls else ""
    download_pmcid = hit.pmcid if hit else ""

    title = hit.title if hit else row.get("title", "")
    author = hit.author if hit else row.get("author", "")
    year = hit.year if hit else row.get("year", "")
    journal = hit.journal if hit else row.get("journal", "")
    doi = hit.doi if hit and hit.doi else row.get("doi", "")
    pmid = hit.pmid if hit and hit.pmid else row.get("pmid", "")
    pmcid = hit.pmcid if hit and hit.pmcid else row.get("pmcid", "")
    resolved_identifier = first_nonempty(
        identifier_for("doi", doi),
        identifier_for("pmcid", pmcid),
        identifier_for("pmid", pmid),
    )
    if resolved_identifier and resolved_identifier in existing_identifiers:
        return report_row(
            status="already_present",
            author=author,
            year=year,
            title=title,
            journal=journal,
            doi=doi,
            pmid=pmid,
            pmcid=pmcid,
            category=category,
            pdf_url=direct_pdf_url,
            relpath=existing_identifiers[resolved_identifier],
            message=f"same identifier already in local registry: {resolved_identifier}",
        )

    duplicate = best_existing_match(
        title,
        inventory_titles,
        requested_doi=doi,
    )
    if duplicate:
        return report_row(
            status="already_present",
            author=author,
            year=year,
            title=title,
            journal=journal,
            doi=doi,
            pmid=pmid,
            pmcid=pmcid,
            category=category,
            pdf_url=direct_pdf_url,
            relpath=duplicate["relpath"],
            message=f"similar local title score={duplicate['score']}",
        )
    if not candidate_urls and not download_pmcid:
        return report_row(
            status="no_oa_pdf_found",
            author=author,
            year=year,
            title=title,
            journal=journal,
            doi=doi,
            pmid=pmid,
            pmcid=pmcid,
            category=category,
            pdf_url="",
            relpath="",
            message=(
                lookup_error
                or "No open-access PDF URL found via Europe PMC and no direct pdf_url provided."
            ),
        )

    if allocation_lock is None:
        relpath = unique_pdf_relpath(
            bib_dir,
            category,
            author or first_author_from_title(title),
            year,
            reserved_relpaths=reserved_relpaths,
        )
    else:
        with allocation_lock:
            relpath = unique_pdf_relpath(
                bib_dir,
                category,
                author or first_author_from_title(title),
                year,
                reserved_relpaths=reserved_relpaths,
            )
    target_path = bib_dir / relpath
    if dry_run:
        status = "dry_run"
        message = "; ".join(candidate_urls)
    else:
        status, direct_pdf_url, message = download_first_available(
            candidate_urls,
            target_path,
            timeout_seconds=download_timeout_seconds,
            pmcid=download_pmcid,
        )
    return report_row(
        status=status,
        author=author,
        year=year,
        title=title,
        journal=journal,
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        category=category,
        pdf_url=direct_pdf_url,
        relpath=relpath.as_posix(),
        message=message,
    )


def read_requests(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in reader
            if any((value or "").strip() for value in row.values())
        ]


def hit_matches_request(row: dict[str, str], hit: object) -> bool:
    """Require every requested persistent identifier to match returned metadata."""

    comparisons = [
        (normalize_doi(row.get("doi")), normalize_doi(getattr(hit, "doi", ""))),
        (normalize_pmid(row.get("pmid")), normalize_pmid(getattr(hit, "pmid", ""))),
        (normalize_pmcid(row.get("pmcid")), normalize_pmcid(getattr(hit, "pmcid", ""))),
    ]
    requested = [(expected, observed) for expected, observed in comparisons if expected]
    return not requested or all(expected == observed for expected, observed in requested)


def create_request_template(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(path, REQUEST_FIELDS, [])


def load_existing_titles(
    inventory_path: Path,
    *,
    bib_dir: Path,
    registry_path: Path | None = None,
) -> list[dict[str, str]]:
    if not inventory_path.exists():
        return []
    with inventory_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        doi_by_relpath = load_dois_by_relpath(registry_path)
        rows = []
        for row in reader:
            relpath = (row.get("relpath_from_bib") or "").strip()
            if not local_pdf_exists(bib_dir, relpath):
                continue
            title = " ".join(
                value
                for value in [
                    row.get("pdf_metadata_title", ""),
                    row.get("guessed_title", ""),
                    row.get("stem", ""),
                    row.get("first_page_text_sample", "")[:250],
                ]
                if value
            )
            if title:
                rows.append(
                    {
                        "title": title,
                        "relpath": relpath,
                        "doi": doi_by_relpath.get(relpath, ""),
                    }
                )
        return rows


def load_dois_by_relpath(registry_path: Path | None) -> dict[str, str]:
    if registry_path is None or not registry_path.exists():
        return {}
    with registry_path.open("r", encoding="utf-8", newline="") as handle:
        return {
            (row.get("local_pdf_relpath") or "").strip(): normalize_doi(row.get("doi"))
            for row in csv.DictReader(handle, delimiter="\t")
            if (row.get("local_pdf_relpath") or "").strip() and normalize_doi(row.get("doi"))
        }


def best_existing_match(
    title: str,
    existing_titles: list[dict[str, str]],
    *,
    requested_doi: str = "",
) -> dict[str, str] | None:
    if not title:
        return None
    requested_doi = normalize_doi(requested_doi)
    best: dict[str, str] | None = None
    for row in existing_titles:
        existing_doi = normalize_doi(row.get("doi"))
        if requested_doi and existing_doi and requested_doi != existing_doi:
            continue
        score = fuzz.token_set_ratio(title, row["title"])
        if score >= 93 and (best is None or score > float(best["score"])):
            best = {"score": f"{score:.1f}", "relpath": row["relpath"]}
    return best


def download_first_available(
    candidate_urls: list[str],
    target_path: Path,
    *,
    timeout_seconds: int = 45,
    pmcid: str = "",
) -> tuple[str, str, str]:
    errors = []
    if pmcid:
        try:
            package_url = download_pmc_oa_pdf(
                pmcid,
                target_path,
                timeout_seconds=timeout_seconds,
            )
            return "downloaded", package_url, package_url
        except Exception as exc:
            errors.append(f"NCBI OA package {pmcid}: {short_error(exc)}")
    for url in candidate_urls:
        try:
            download_pdf(url, target_path, timeout_seconds=timeout_seconds)
            return "downloaded", url, url
        except Exception as exc:
            errors.append(f"{url}: {short_error(exc)}")
    return "download_failed", candidate_urls[0] if candidate_urls else "", " | ".join(errors)


def short_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:240]


def load_existing_identifiers(
    registry_path: Path,
    *,
    bib_dir: Path,
) -> dict[str, str]:
    if not registry_path.exists():
        return {}
    identifiers: dict[str, str] = {}
    with registry_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            relpath = (row.get("local_pdf_relpath") or "").strip()
            if not local_pdf_exists(bib_dir, relpath):
                continue
            for key, value in [("doi", row.get("doi", "")), ("pmcid", row.get("pmcid", "")), ("pmid", row.get("pmid", ""))]:
                identifier = identifier_for(key, value)
                if identifier:
                    identifiers.setdefault(identifier, relpath)
    return identifiers


def local_pdf_exists(bib_dir: Path, relpath: str) -> bool:
    """Return whether *relpath* is a real file inside the current bibliography root."""
    relative = Path((relpath or "").strip())
    if not relpath or relative.is_absolute():
        return False

    try:
        root = bib_dir.expanduser().resolve()
        candidate = (root / relative).resolve()
        candidate.relative_to(root)
        return candidate.is_file()
    except (OSError, RuntimeError, ValueError):
        return False


def identifier_for(kind: str, value: str) -> str:
    clean_value = (value or "").strip()
    if not clean_value:
        return ""
    if kind == "doi":
        normalized = normalize_doi(clean_value)
        return f"doi:{normalized}" if normalized else ""
    if kind == "pmcid":
        normalized = normalize_pmcid(clean_value)
        return f"pmcid:{normalized}" if normalized else ""
    if kind == "pmid":
        normalized = normalize_pmid(clean_value)
        return f"pmid:{normalized}" if normalized else ""
    return ""


def first_nonempty(*values: str) -> str:
    for value in values:
        if value:
            return value
    return ""


def unique_pdf_relpath(
    bib_dir: Path,
    category: str,
    author: str,
    year: str,
    *,
    reserved_relpaths: set[Path] | None = None,
) -> Path:
    safe_category = sanitize_path_part(category or "general_malaria")
    safe_author = sanitize_path_part(author or "Unknown")
    safe_year = year if re.fullmatch(r"\d{4}", year or "") else "unknown"
    base = Path(safe_category) / f"{safe_author}_{safe_year}.pdf"
    candidate = base
    suffix = 2
    while (bib_dir / candidate).exists() or (
        reserved_relpaths is not None and candidate in reserved_relpaths
    ):
        candidate = Path(safe_category) / f"{safe_author}_{safe_year}_{suffix}.pdf"
        suffix += 1
    if reserved_relpaths is not None:
        reserved_relpaths.add(candidate)
    return candidate


def first_author_from_title(title: str) -> str:
    words = re.findall(r"[A-Za-z]{3,}", title or "")
    return words[0] if words else "Unknown"


def sanitize_path_part(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_")
    return clean or "unknown"


def report_row(
    *,
    status: str,
    author: str,
    year: str,
    title: str,
    journal: str,
    doi: str,
    pmid: str,
    pmcid: str,
    category: str,
    pdf_url: str,
    relpath: str,
    message: str,
) -> dict[str, str]:
    return {
        "imported_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "author": author,
        "year": year,
        "title": title,
        "journal": journal,
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "category": category,
        "pdf_url": pdf_url,
        "local_pdf_relpath": relpath,
        "message": message,
    }


def write_tsv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
