from __future__ import annotations

import html
import json
import re
import urllib.request
from typing import Any

from originrag.discovery.types import DiscoveryHit


USER_AGENT = "OriginRAG/0.1 local literature discovery; contact: local-user"
VALID_SERVERS = {"biorxiv", "medrxiv"}


def search_preprint_server(
    server: str,
    query: str,
    *,
    from_date: str,
    to_date: str,
    page_size: int = 25,
    max_records: int = 500,
    timeout_seconds: int = 45,
) -> list[DiscoveryHit]:
    """Search bioRxiv or medRxiv by scanning recent metadata and filtering locally.

    The bioRxiv/medRxiv API exposes date-based metadata batches rather than a
    full text query endpoint. We therefore retrieve recent records and keep only
    records whose title/abstract match the configured topic query.
    """
    server = server.lower().strip()
    if server not in VALID_SERVERS:
        raise ValueError(f"Unsupported preprint server: {server}")

    clauses = query_clauses(query)
    hits: list[DiscoveryHit] = []
    cursor = 0
    seen_dois: set[str] = set()
    scanned = 0
    while scanned < max_records and len(hits) < page_size:
        payload = fetch_details(server, from_date=from_date, to_date=to_date, cursor=cursor, timeout_seconds=timeout_seconds)
        collection = payload.get("collection")
        if not isinstance(collection, list) or not collection:
            break
        for item in collection:
            if not isinstance(item, dict):
                continue
            scanned += 1
            doi = normalize_doi(str(item.get("doi") or ""))
            if doi in seen_dois:
                continue
            seen_dois.add(doi)
            text = " ".join([str(item.get("title") or ""), str(item.get("abstract") or "")])
            if clauses and not matches_query_clause(text, clauses):
                continue
            hits.append(preprint_to_hit(server=server, query=query, item=item))
            if len(hits) >= page_size:
                break
            if scanned >= max_records:
                break
        if len(collection) < 100:
            break
        cursor += len(collection)
    return hits


def fetch_details(
    server: str,
    *,
    from_date: str,
    to_date: str,
    cursor: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    url = f"https://api.biorxiv.org/details/{server}/{from_date}/{to_date}/{cursor}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def preprint_to_hit(*, server: str, query: str, item: dict[str, Any]) -> DiscoveryHit:
    doi = normalize_doi(str(item.get("doi") or ""))
    version = str(item.get("version") or "").strip()
    date = str(item.get("date") or "").strip()
    landing_url = preprint_url(server, doi, version, suffix="")
    pdf_url = preprint_url(server, doi, version, suffix=".full.pdf")
    title = clean_text(item.get("title"))
    return DiscoveryHit(
        source=server,
        query=query,
        author=first_author(str(item.get("authors") or "")),
        year=date[:4],
        title=title,
        journal=server,
        doi=doi,
        first_publication_date=date,
        is_open_access=True,
        pdf_url=pdf_url,
        landing_url=landing_url,
        message=clean_text(item.get("category")),
    )


def preprint_url(server: str, doi: str, version: str, *, suffix: str) -> str:
    if not doi:
        return ""
    version_suffix = f"v{version}" if version and not doi.endswith(f"v{version}") else ""
    return f"https://www.{server}.org/content/{doi}{version_suffix}{suffix}"


def query_clauses(query: str) -> list[list[str]]:
    clauses: list[list[str]] = []
    for raw_clause in re.split(r"\bOR\b", query, flags=re.IGNORECASE):
        quoted = re.findall(r'"([^"]+)"', raw_clause)
        without_quotes = re.sub(r'"[^"]+"', " ", raw_clause)
        words = re.split(r"\bAND\b|[()]", without_quotes, flags=re.IGNORECASE)
        terms = [normalize_space(term).lower() for term in quoted if meaningful_term(term)]
        for word_group in words:
            terms.extend(unquoted_query_terms(word_group))
        if terms:
            clauses.append(dedupe_terms(terms))
    return clauses


def unquoted_query_terms(value: str) -> list[str]:
    normalized = normalize_space(value).lower()
    if not meaningful_term(normalized):
        return []
    return [term for term in re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)?", normalized) if meaningful_term(term)]


def meaningful_term(term: str) -> bool:
    value = normalize_space(term)
    if not value:
        return False
    return value.lower() not in {"or", "and", "the", "a", "an"}


def matches_query_clause(text: str, clauses: list[list[str]]) -> bool:
    normalized = normalize_space(text).lower()
    return any(all(term in normalized for term in clause) for clause in clauses)


def dedupe_terms(terms: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        deduped.append(term)
    return deduped


def first_author(authors: str) -> str:
    first = authors.split(";", 1)[0].split(",", 1)[0].strip()
    parts = first.split()
    return parts[-1] if parts else ""


def clean_text(value: Any) -> str:
    return normalize_space(html.unescape(str(value or "")))


def normalize_space(value: str) -> str:
    return " ".join(value.split())


def normalize_doi(value: str) -> str:
    doi = value.strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    return doi.lower()
