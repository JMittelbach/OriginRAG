from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from rapidfuzz import fuzz

from pfempire.discovery.europepmc import best_pdf_url, search_one
from pfempire.provenance.library import (
    LibraryStatusResolver,
    normalize_doi,
    normalize_pmcid,
    normalize_pmid,
)
from pfempire.provenance.references import normalized_text, stable_work_id


CROSSREF_WORKS_URL = "https://api.crossref.org/works"
USER_AGENT = "OriginRAG/0.1 claim provenance resolver"


def resolve_trace_against_library(
    trace: dict[str, object],
    resolver: LibraryStatusResolver,
    *,
    allow_fuzzy_candidates: bool = True,
) -> dict[str, object]:
    """Annotate a trace without silently accepting fuzzy bibliographic matches."""

    output = dict(trace)
    resolved_mentions = []
    citing_paper_id = str(trace.get("paper_id") or "").strip()
    for raw_mention in trace.get("mentions", []):
        if not isinstance(raw_mention, dict):
            continue
        mention = dict(raw_mention)
        mention_citing_paper_id = str(
            mention.get("citing_paper_id") or citing_paper_id
        ).strip()
        reference = dict(mention.get("reference") or {})
        reference = resolve_reference_against_library(
            reference,
            resolver,
            allow_fuzzy_candidates=allow_fuzzy_candidates,
            citing_paper_id=mention_citing_paper_id,
        )
        mention["reference"] = reference
        mention["bibliographic_resolution_status"] = reference.get(
            "bibliographic_resolution_status", "unresolved"
        )
        resolved_mentions.append(mention)
    output["mentions"] = resolved_mentions
    output["n_exactly_resolved"] = sum(
        1
        for mention in resolved_mentions
        if mention.get("bibliographic_resolution_status") in {"exact_id", "exact_title"}
    )
    output["n_review_candidates"] = sum(
        1
        for mention in resolved_mentions
        if mention.get("bibliographic_resolution_status") == "candidate_match"
    )
    output["n_carrier_identity_rejections"] = sum(
        1
        for mention in resolved_mentions
        if (mention.get("reference") or {}).get("resolution_issue")
        == "carrier_identity_contamination"
    )
    return output


def resolve_reference_against_library(
    reference: dict[str, object],
    resolver: LibraryStatusResolver,
    *,
    allow_fuzzy_candidates: bool = True,
    citing_paper_id: str = "",
) -> dict[str, object]:
    result = dict(reference)
    if result.get("bibliographic_resolution_status") == "candidate_match":
        candidate_status = resolver.resolve(result)
        candidate_record = (candidate_status.get("registry") or {}).get("record") or {}
        if _is_carrier_record(candidate_record, citing_paper_id):
            return _reject_carrier_identity(result, candidate_record, resolver)
        result["verified_identifiers"] = {}
        result["library_status"] = resolver.resolve({})
        result["candidate_library_status"] = candidate_status
        result["candidate_registry_paper_id"] = (
            result.get("candidate_registry_paper_id")
            or candidate_record.get("paper_id")
            or ""
        )
        result["work_id"] = stable_work_id(
            citation_text=result.get("citation_text"),
        )
        return result
    incoming_exact_identifier = (
        result.get("bibliographic_resolution_status") == "exact_id"
        and any(
            [
                normalize_doi(result.get("doi")),
                normalize_pmid(result.get("pmid")),
                normalize_pmcid(result.get("pmcid")),
            ]
        )
    )
    exact_status = resolver.resolve(result)
    registry_info = exact_status.get("registry") or {}
    registry_record = registry_info.get("record")
    if registry_record:
        if _is_carrier_record(registry_record, citing_paper_id):
            return _reject_carrier_identity(result, registry_record, resolver)
        matched_by = set(registry_info.get("matched_by") or [])
        resolution = (
            "exact_id"
            if matched_by.intersection({"paper_id", "doi", "pmid", "pmcid"})
            or incoming_exact_identifier
            else "exact_title"
        )
        return _apply_registry_match(result, registry_record, exact_status, status=resolution)

    normalized_identifiers = {
        "doi": normalize_doi(result.get("doi")),
        "pmid": normalize_pmid(result.get("pmid")),
        "pmcid": normalize_pmcid(result.get("pmcid")),
    }
    for field, value in normalized_identifiers.items():
        if value:
            result[field] = value
    has_identifier = any(normalized_identifiers.values())
    if registry_info.get("candidate_paper_ids"):
        result["bibliographic_resolution_status"] = "conflict"
        result["verified_identifiers"] = {}
        result["resolution_note"] = str(
            exact_status.get("reason") or "Supplied metadata conflicts with the local registry."
        )
        result["library_status"] = exact_status
        result["work_id"] = stable_work_id(
            doi=result.get("doi"),
            pmcid=result.get("pmcid"),
            pmid=result.get("pmid"),
            title=result.get("title"),
            citation_text=result.get("citation_text"),
        )
        return result
    if has_identifier and result.get("bibliographic_resolution_status") == "exact_id":
        result["resolution_note"] = (
            "Persistent identifier metadata was verified by an external exact-identifier service; "
            "no local registry record matched."
        )
        result["library_status"] = exact_status
        result["verified_identifiers"] = dict(normalized_identifiers)
        result["landing_url"] = result.get("landing_url") or _doi_url(
            str(result.get("doi") or "")
        )
        result["work_id"] = stable_work_id(
            doi=result.get("doi"),
            pmcid=result.get("pmcid"),
            pmid=result.get("pmid"),
            title=result.get("title"),
            citation_text=result.get("citation_text"),
        )
        return result

    citation = normalized_text(result.get("citation_text"))
    compact_citation = citation.replace(" ", "")
    reference_year = str(result.get("year") or "").strip()
    reference_author = normalized_text(result.get("author"))
    exact_candidates = []
    fuzzy_candidates: list[tuple[float, dict[str, str]]] = []
    for record in resolver.records:
        title = normalized_text(record.get("title"))
        if len(title) < 24:
            continue
        compact_title = title.replace(" ", "")
        record_year = str(record.get("year") or "").strip()
        record_author = normalized_text(record.get("author"))
        year_agrees = not reference_year or not record_year or reference_year == record_year
        author_agrees = (
            not reference_author
            or not record_author
            or reference_author in record_author
            or record_author in reference_author
            or record_author in citation
        )
        if (
            title
            and (title in citation or compact_title in compact_citation)
            and year_agrees
            and author_agrees
        ):
            exact_candidates.append(record)
            continue
        if allow_fuzzy_candidates and year_agrees and author_agrees:
            score = fuzz.partial_ratio(title, citation)
            if score >= 94.0:
                fuzzy_candidates.append((float(score), record))

    if len(exact_candidates) == 1:
        record = exact_candidates[0]
        if _is_carrier_record(record, citing_paper_id):
            return _reject_carrier_identity(result, record, resolver)
        conflicts = _conflicting_identifiers(result, record)
        if conflicts:
            result["bibliographic_resolution_status"] = "conflict"
            result["verified_identifiers"] = {}
            result["resolution_note"] = (
                "Printed-reference identifier conflicts with the unique local title match: "
                + ", ".join(conflicts)
                + "."
            )
            result["candidate_registry_paper_id"] = record.get("paper_id") or ""
            result["library_status"] = exact_status
            return result
        status = resolver.resolve({"paper_id": record.get("paper_id")})
        return _apply_registry_match(result, record, status, status="exact_title")
    if len(exact_candidates) > 1:
        result["bibliographic_resolution_status"] = "conflict"
        result["verified_identifiers"] = {}
        result["resolution_note"] = "The printed citation contains more than one registered title."
        result["library_status"] = exact_status
        return result

    fuzzy_candidates.sort(key=lambda item: (-item[0], str(item[1].get("paper_id") or "")))
    if fuzzy_candidates:
        best_score, best_record = fuzzy_candidates[0]
        second_score = fuzzy_candidates[1][0] if len(fuzzy_candidates) > 1 else 0.0
        if best_score - second_score >= 3.0:
            if _is_carrier_record(best_record, citing_paper_id):
                return _reject_carrier_identity(result, best_record, resolver)
            conflicts = _conflicting_identifiers(result, best_record)
            if conflicts:
                result["bibliographic_resolution_status"] = "conflict"
                result["verified_identifiers"] = {}
                result["resolution_note"] = (
                    "Printed-reference identifier conflicts with the local title candidate: "
                    + ", ".join(conflicts)
                    + "."
                )
                result["candidate_registry_paper_id"] = best_record.get("paper_id") or ""
                result["library_status"] = exact_status
                return result
            result["bibliographic_resolution_status"] = "candidate_match"
            result["candidate_match_score"] = round(best_score, 3)
            result["candidate_registry_paper_id"] = best_record.get("paper_id") or ""
            result["candidate_title"] = best_record.get("title") or ""
            result["resolution_note"] = "Fuzzy title match; human review is required."
            result["verified_identifiers"] = {}
            result["library_status"] = exact_status
            result["candidate_library_status"] = resolver.resolve(
                {"paper_id": best_record.get("paper_id")}
            )
            return result

    result["bibliographic_resolution_status"] = (
        "identifier_extracted_unverified" if has_identifier else "unresolved"
    )
    result["resolution_note"] = (
        "A persistent identifier was extracted but has not been matched to local or online metadata."
        if has_identifier
        else "No unique exact identifier or title match was found locally."
    )
    result["library_status"] = exact_status
    result["verified_identifiers"] = {}
    return result


def _is_carrier_record(record: dict[str, str], citing_paper_id: str) -> bool:
    return bool(
        citing_paper_id
        and str(record.get("paper_id") or "").strip() == citing_paper_id
    )


def _reject_carrier_identity(
    reference: dict[str, object],
    record: dict[str, str],
    resolver: LibraryStatusResolver,
) -> dict[str, object]:
    """Fail closed when page furniture makes the carrier look like its own source."""

    result = dict(reference)
    result.pop("registry_paper_id", None)
    result.pop("bib_key", None)
    result["bibliographic_resolution_status"] = "conflict"
    result["resolution_issue"] = "carrier_identity_contamination"
    result["rejected_registry_paper_id"] = record.get("paper_id") or ""
    result["verified_identifiers"] = {}
    result["library_status"] = resolver.resolve({})
    result["work_id"] = stable_work_id(
        citation_text=result.get("citation_text"),
        title=result.get("title"),
    )
    result["resolution_note"] = (
        "Rejected a match to the citing paper itself. Running headers, footers, or "
        "post-reference article text may have contaminated the printed reference."
    )
    return result


def _conflicting_identifiers(
    reference: dict[str, object],
    record: dict[str, str],
) -> list[str]:
    normalizers = {
        "doi": normalize_doi,
        "pmid": normalize_pmid,
        "pmcid": normalize_pmcid,
    }
    conflicts = []
    for field, normalizer in normalizers.items():
        supplied = normalizer(reference.get(field))
        registered = normalizer(record.get(field))
        if supplied and registered and supplied != registered:
            conflicts.append(field)
    return conflicts


def _apply_registry_match(
    reference: dict[str, object],
    record: dict[str, str],
    library_status: dict[str, Any],
    *,
    status: str,
) -> dict[str, object]:
    result = dict(reference)
    incoming_exact_identifier = result.get("bibliographic_resolution_status") == "exact_id"
    verified_identifiers: dict[str, str] = {}
    identifier_normalizers = {
        "doi": normalize_doi,
        "pmid": normalize_pmid,
        "pmcid": normalize_pmcid,
    }
    for field, normalizer in identifier_normalizers.items():
        registered = normalizer(record.get(field))
        externally_verified = normalizer(result.get(field)) if incoming_exact_identifier else ""
        verified = registered or externally_verified
        if registered:
            result[field] = registered
        if verified:
            verified_identifiers[field] = verified
    for field in ["title", "author", "year", "journal"]:
        if record.get(field):
            result[field] = record[field]
    result["registry_paper_id"] = record.get("paper_id") or ""
    result["bib_key"] = record.get("bib_key") or ""
    result["citation_short"] = record.get("citation_short") or ""
    result["citation_apa"] = record.get("citation_apa") or ""
    result["citation_display"] = record.get("citation_apa") or result.get("citation_text") or ""
    result["verified_identifiers"] = verified_identifiers
    result["landing_url"] = record.get("landing_url") or _doi_url(
        verified_identifiers.get("doi", "")
    )
    result["work_id"] = (
        stable_work_id(
            doi=verified_identifiers.get("doi"),
            pmcid=verified_identifiers.get("pmcid"),
            pmid=verified_identifiers.get("pmid"),
            title=result.get("title"),
            citation_text=result.get("citation_text"),
        )
        if verified_identifiers
        else f"registry:{record.get('paper_id')}"
    )
    result["bibliographic_resolution_status"] = status
    result["resolution_note"] = (
        "Unique exact identifier match." if status == "exact_id" else "Unique title text and year match."
    )
    result["library_status"] = library_status
    return result


def enrich_reference_online(
    reference: dict[str, object],
    *,
    timeout_seconds: int = 30,
    mailto: str = "",
) -> dict[str, object]:
    """Resolve metadata conservatively; title-only results remain review candidates."""

    result = dict(reference)
    exact_identifier = any(
        [
            normalize_doi(result.get("doi")),
            normalize_pmid(result.get("pmid")),
            normalize_pmcid(result.get("pmcid")),
        ]
    )
    hit = None
    if exact_identifier:
        try:
            hit = search_one(
                doi=str(result.get("doi") or ""),
                pmid=str(result.get("pmid") or ""),
                pmcid=str(result.get("pmcid") or ""),
                timeout_seconds=timeout_seconds,
            )
        except (OSError, ValueError):
            hit = None
        if hit and _europe_pmc_identifier_matches(hit, result):
            result.update(
                {
                    "doi": hit.doi or result.get("doi") or "",
                    "pmid": hit.pmid or result.get("pmid") or "",
                    "pmcid": hit.pmcid or result.get("pmcid") or "",
                    "title": hit.title or result.get("title") or "",
                    "author": hit.author or result.get("author") or "",
                    "year": hit.year or result.get("year") or "",
                    "journal": hit.journal or result.get("journal") or "",
                    "oa_pdf_url": best_pdf_url(hit),
                    "metadata_source": "europe_pmc_exact_identifier",
                    "bibliographic_resolution_status": "exact_id",
                }
            )
        elif (
            normalize_doi(result.get("doi"))
            and not normalize_pmid(result.get("pmid"))
            and not normalize_pmcid(result.get("pmcid"))
        ):
            crossref = _crossref_exact_doi(
                normalize_doi(result.get("doi")),
                timeout_seconds=timeout_seconds,
                mailto=mailto,
            )
            if crossref:
                result.update(crossref)
                result["metadata_source"] = "crossref_exact_doi"
                result["bibliographic_resolution_status"] = "exact_id"
    if not exact_identifier:
        candidate = _crossref_bibliographic_candidate(
            str(result.get("citation_text") or ""),
            expected_year=str(result.get("year") or ""),
            expected_author=str(result.get("author") or ""),
            timeout_seconds=timeout_seconds,
            mailto=mailto,
        )
        if candidate:
            result.update(candidate)
            result["bibliographic_resolution_status"] = "candidate_match"
            result["metadata_source"] = "crossref_bibliographic_candidate"
            result["resolution_note"] = (
                "Crossref bibliographic candidate; human review is required before attribution."
            )
            if result.get("doi"):
                try:
                    hit = search_one(
                        doi=str(result["doi"]), timeout_seconds=timeout_seconds
                    )
                except (OSError, ValueError):
                    hit = None
                if hit:
                    result["oa_pdf_url"] = best_pdf_url(hit)

    result["work_id"] = stable_work_id(
        doi=result.get("doi"),
        pmcid=result.get("pmcid"),
        pmid=result.get("pmid"),
        title=result.get("title"),
        citation_text=result.get("citation_text"),
    )
    result["landing_url"] = _doi_url(str(result.get("doi") or ""))
    return result


def _europe_pmc_identifier_matches(hit: object, reference: dict[str, object]) -> bool:
    comparisons = [
        (
            normalize_doi(reference.get("doi")),
            normalize_doi(getattr(hit, "doi", "")),
        ),
        (
            normalize_pmid(reference.get("pmid")),
            normalize_pmid(getattr(hit, "pmid", "")),
        ),
        (
            normalize_pmcid(reference.get("pmcid")),
            normalize_pmcid(getattr(hit, "pmcid", "")),
        ),
    ]
    supplied = [(expected, observed) for expected, observed in comparisons if expected]
    return bool(supplied) and all(expected == observed for expected, observed in supplied)


def _crossref_exact_doi(
    doi: str,
    *,
    timeout_seconds: int,
    mailto: str,
) -> dict[str, object] | None:
    normalized = normalize_doi(doi)
    if not normalized:
        return None
    url = f"{CROSSREF_WORKS_URL}/{urllib.parse.quote(normalized, safe='')}"
    request = urllib.request.Request(url, headers={"User-Agent": _user_agent(mailto)})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    item = payload.get("message") or {}
    if not isinstance(item, dict) or normalize_doi(item.get("DOI")) != normalized:
        return None
    return {
        "doi": normalized,
        "title": _first(item.get("title")),
        "author": _crossref_author(item),
        "year": _crossref_year(item),
        "journal": _first(item.get("container-title")),
        "landing_url": _doi_url(normalized),
    }


def _user_agent(mailto: str) -> str:
    return f"{USER_AGENT} (mailto:{mailto})" if mailto else USER_AGENT


def _crossref_bibliographic_candidate(
    citation_text: str,
    *,
    expected_year: str,
    expected_author: str,
    timeout_seconds: int,
    mailto: str,
) -> dict[str, object] | None:
    if len(normalized_text(citation_text)) < 24:
        return None
    params = {"query.bibliographic": citation_text, "rows": "3"}
    if mailto:
        params["mailto"] = mailto
    url = f"{CROSSREF_WORKS_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": _user_agent(mailto)})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    items = payload.get("message", {}).get("items", [])
    ranked = []
    normalized_citation = normalized_text(citation_text)
    normalized_author = normalized_text(expected_author)
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _first(item.get("title"))
        title_score = fuzz.partial_ratio(normalized_text(title), normalized_citation)
        year = _crossref_year(item)
        author = _crossref_author(item)
        year_agrees = not expected_year or not year or expected_year == year
        author_agrees = not normalized_author or normalized_author in normalized_text(author)
        if title_score < 94.0 or not year_agrees or not author_agrees:
            continue
        ranked.append((float(title_score), item, title, author, year))
    ranked.sort(key=lambda value: -value[0])
    if not ranked:
        return None
    best = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if best[0] - second_score < 3.0:
        return None
    item, title, author, year = best[1], best[2], best[3], best[4]
    return {
        "doi": str(item.get("DOI") or ""),
        "title": title,
        "author": author,
        "year": year,
        "journal": _first(item.get("container-title")),
        "candidate_match_score": round(best[0], 3),
    }


def _crossref_year(item: dict[str, object]) -> str:
    for field in ["published-print", "published-online", "published", "issued"]:
        parts = (item.get(field) or {}).get("date-parts", []) if isinstance(item.get(field), dict) else []
        if parts and parts[0]:
            return str(parts[0][0])
    return ""


def _crossref_author(item: dict[str, object]) -> str:
    authors = item.get("author") or []
    if not isinstance(authors, list) or not authors or not isinstance(authors[0], dict):
        return ""
    return str(authors[0].get("family") or authors[0].get("name") or "")


def _first(value: object) -> str:
    if isinstance(value, list):
        return str(value[0] if value else "")
    return str(value or "")


def _doi_url(doi: str) -> str:
    return f"https://doi.org/{doi}" if doi else ""
