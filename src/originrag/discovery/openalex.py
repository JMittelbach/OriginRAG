from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from typing import Any

from originrag.discovery.types import DiscoveryHit


OPENALEX_WORKS_URL = "https://api.openalex.org/works"
USER_AGENT = "OriginRAG/0.1 local literature discovery; mailto:local-user@example.invalid"


def search_openalex(
    query: str,
    *,
    from_date: str,
    to_date: str,
    page_size: int = 25,
    timeout_seconds: int = 45,
) -> list[DiscoveryHit]:
    """Search OpenAlex works and return normalized discovery hits.

    OpenAlex is used for broad literature discovery across publishers and
    journals. It is metadata-first; downloads are attempted only if OpenAlex
    exposes an explicit open PDF URL.
    """
    params = {
        "search": query,
        "filter": f"from_publication_date:{from_date},to_publication_date:{to_date}",
        "per-page": str(max(1, min(page_size, 200))),
        "sort": "publication_date:desc",
    }
    url = f"{OPENALEX_WORKS_URL}?{urllib.parse.urlencode(params)}"
    payload = get_json(url, timeout_seconds=timeout_seconds)
    results = payload.get("results", [])
    return [work_to_hit(query=query, work=item) for item in results if isinstance(item, dict)]


def search_openalex_title(
    title: str,
    *,
    year: str = "",
    page_size: int = 5,
    timeout_seconds: int = 45,
) -> list[DiscoveryHit]:
    """Search OpenAlex by title for metadata enrichment."""
    params = {
        "search": title,
        "per-page": str(max(1, min(page_size, 25))),
        "sort": "relevance_score:desc",
    }
    if year.isdigit():
        start_year = max(1800, int(year) - 1)
        end_year = int(year) + 1
        params["filter"] = f"from_publication_date:{start_year}-01-01,to_publication_date:{end_year}-12-31"
    url = f"{OPENALEX_WORKS_URL}?{urllib.parse.urlencode(params)}"
    payload = get_json(url, timeout_seconds=timeout_seconds)
    results = payload.get("results", [])
    return [work_to_hit(query=title, work=item) for item in results if isinstance(item, dict)]


def work_to_hit(*, query: str, work: dict[str, Any]) -> DiscoveryHit:
    title = clean_text(work.get("title") or work.get("display_name"))
    publication_date = str(work.get("publication_date") or "").strip()
    year = str(work.get("publication_year") or publication_date or "").strip()[:4]
    doi = normalize_doi(str(work.get("doi") or ""))
    ids = work.get("ids") if isinstance(work.get("ids"), dict) else {}
    pmid = identifier_suffix(str(ids.get("pmid") or ""), "pubmed.ncbi.nlm.nih.gov/")
    pmcid = identifier_suffix(str(ids.get("pmcid") or ""), "pmc.ncbi.nlm.nih.gov/articles/")
    primary_location = work.get("primary_location") if isinstance(work.get("primary_location"), dict) else {}
    source = primary_location.get("source") if isinstance(primary_location.get("source"), dict) else {}
    journal = clean_text(source.get("display_name") if isinstance(source, dict) else "")
    landing_url = str(primary_location.get("landing_page_url") or work.get("doi") or "").strip()
    pdf_url = best_openalex_pdf_url(work)
    open_access = work.get("open_access") if isinstance(work.get("open_access"), dict) else {}
    is_open_access = bool(open_access.get("is_oa") or pdf_url)
    return DiscoveryHit(
        source="openalex",
        query=query,
        author=first_author(work),
        year=year,
        title=title,
        journal=journal,
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        first_publication_date=publication_date,
        is_open_access=is_open_access,
        pdf_url=pdf_url,
        landing_url=landing_url,
    )


def best_openalex_pdf_url(work: dict[str, Any]) -> str:
    locations = []
    primary_location = work.get("primary_location")
    if isinstance(primary_location, dict):
        locations.append(primary_location)
    raw_locations = work.get("locations")
    if isinstance(raw_locations, list):
        locations.extend(item for item in raw_locations if isinstance(item, dict))

    for location in locations:
        pdf_url = str(location.get("pdf_url") or "").strip()
        if pdf_url:
            return pdf_url
    open_access = work.get("open_access") if isinstance(work.get("open_access"), dict) else {}
    oa_url = str(open_access.get("oa_url") or "").strip()
    if oa_url.lower().split("?", 1)[0].endswith(".pdf"):
        return oa_url
    return ""


def first_author(work: dict[str, Any]) -> str:
    authorships = work.get("authorships")
    if not isinstance(authorships, list):
        return ""
    for authorship in authorships:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author")
        if isinstance(author, dict):
            name = clean_text(author.get("display_name"))
            if name:
                return name.split()[-1]
    return ""


def get_json(url: str, *, timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def clean_text(value: Any) -> str:
    return " ".join(html.unescape(str(value or "")).split())


def normalize_doi(value: str) -> str:
    doi = value.strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    return doi.lower()


def identifier_suffix(value: str, marker: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if marker in text:
        return text.rsplit(marker, 1)[-1].strip("/")
    return text
