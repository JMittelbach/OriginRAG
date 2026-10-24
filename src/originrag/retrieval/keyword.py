from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from originrag.curation.relevance_policy import (
    HARD_OFF_TOPIC_TERMS,
    VACCINE_TERMS,
    relevance_decision,
)
from originrag.retrieval.chunk_quality import classify_chunk_quality
from originrag.retrieval.metadata import load_paper_metadata
from originrag.retrieval.section_detection import (
    carries_forward_section,
    detect_section_for_chunk,
    stored_section_needs_redetection,
)
from originrag.retrieval.section_weights import get_section_weight


COMMON_TERMS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "malaria",
    "plasmodium",
    "falciparum",
}

RESULT_METADATA_FIELDS = [
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
]

ACTIVE_FULLTEXT_STATUSES = {"pdf_available", "extracted", "chunked"}
NO_METADATA_BOOST_SECTIONS = {
    "references",
    "table",
    "figure",
    "figure_legend",
    "supplement",
    "data_availability",
}

_NUMERIC_LITERAL = re.compile(
    r"(?<![\w./+\-−])[+\-−]?"
    r"(?:\d+\.\d+(?:[eE][+\-−]?\d+)?|\.\d+(?:[eE][+\-−]?\d+)?|\d+[eE][+\-−]?\d+)"
    r"(?![\w/]|\.\d)"
)


def search_chunks(
    query: str,
    chunks_dir: Path,
    papers_tsv: Path | None = None,
    limit: int = 20,
    include_low_quality: bool = False,
    exclude_references: bool = True,
    exclude_tables: bool = False,
    exclude_figures: bool = False,
    exclude_supplement: bool = False,
    category: str | None = None,
    paper_id: str | None = None,
    require_doi: bool = False,
    min_score: float = 0.0,
    max_per_paper: int = 2,
    query_mode: str = "evidence",
    prefer_sections: list[str] | None = None,
    exclude_sections: list[str] | None = None,
    include_sections: list[str] | None = None,
    include_inactive: bool = False,
    candidate_chunk_ids: frozenset[str] | set[str] | None = None,
    candidate_paper_ids: frozenset[str] | set[str] | None = None,
    candidate_source_paper_ids: frozenset[str] | set[str] | None = None,
    candidate_source_layout_verified: bool = False,
    candidate_records: tuple[tuple[str, int, dict], ...] | None = None,
) -> list[dict]:
    """Search JSONL chunk files with lightweight keyword scoring and evidence-aware filters.

    ``candidate_source_paper_ids`` is an I/O hint, not an eligibility filter. It is used only
    when ``candidate_source_layout_verified`` confirms that index construction observed one
    paper per canonical ``<paper_id>.jsonl`` file. Unverified, legacy, aggregate, or incomplete
    layouts retain the complete corpus scan.

    ``candidate_records`` supplies the same ``(source_file, line_number, chunk)`` records from a
    validated retrieval index. ``None`` retains JSONL loading; an empty tuple is a valid empty
    candidate set.

    Imported active full texts are searchable regardless of acquisition-time topic policy.
    Corpus eligibility, explicit user filters, and passage-quality filters still apply.
    """
    normalized_query = normalize_text(query)
    if not normalized_query:
        return []

    query_terms = _scoring_query_tokens(query)
    if not query_terms:
        return []

    metadata = load_paper_metadata(papers_tsv) if papers_tsv else {}
    category_boosts = infer_query_category_boosts(query)
    excluded_sections = _section_filter_set(exclude_sections)
    included_sections = _section_filter_set(include_sections)
    preferred_sections = _section_filter_set(prefer_sections)
    if exclude_references:
        excluded_sections.add("references")
    candidate_filter_active = candidate_chunk_ids is not None or candidate_paper_ids is not None
    allowed_chunk_ids = candidate_chunk_ids or frozenset()
    allowed_paper_ids = candidate_paper_ids or frozenset()

    candidates: list[dict] = []

    previous_sections: dict[str, str | None] = defaultdict(lambda: None)
    chunk_records = (
        candidate_records
        if candidate_records is not None
        else iter_chunk_records(
            Path(chunks_dir),
            candidate_paper_ids=candidate_source_paper_ids,
            canonical_layout_verified=candidate_source_layout_verified,
        )
    )
    for chunk_file, line_number, chunk in chunk_records:
        previous_section = previous_sections[chunk_file]
        chunk_paper_id = str(chunk.get("paper_id") or "")
        if paper_id and chunk_paper_id != paper_id:
            continue
        chunk_id = str(chunk.get("chunk_id") or "")
        if candidate_filter_active:
            if chunk_id not in allowed_chunk_ids and chunk_paper_id not in allowed_paper_ids:
                continue

        paper_meta = metadata.get(chunk_paper_id, {})
        if not include_inactive and _is_inactive_paper(paper_meta):
            continue
        if require_doi and not str(paper_meta.get("doi") or "").strip():
            continue
        if category and not _matches_category_filter(paper_meta, category):
            continue

        text = str(chunk.get("text") or "")
        section_info = _section_info(chunk, text, previous_section)
        section = str(section_info["section"])
        if carries_forward_section(section):
            previous_sections[chunk_file] = section
        if included_sections and section not in included_sections:
            continue
        if section in excluded_sections:
            continue

        quality = cached_chunk_quality(text)
        if _excluded_by_quality(
            quality,
            include_low_quality=include_low_quality,
            exclude_references=exclude_references,
            exclude_tables=exclude_tables,
            exclude_figures=exclude_figures,
            exclude_supplement=exclude_supplement,
        ):
            continue

        raw_score = _score_text(normalized_query, query_terms, text)
        metadata_score = _metadata_match_score(
            normalized_query,
            query_terms,
            paper_meta,
            section,
            raw_score,
        )
        section_weight = get_section_weight(section, query_mode=query_mode)
        if preferred_sections:
            section_weight *= 1.12 if section in preferred_sections else 0.94
        quality_penalty = float(quality["quality_penalty"])
        category_boost = _category_boost(paper_meta, category_boosts)
        source_boost = _source_lookup_boost(query, text, paper_meta)
        score = max(
            0.0,
            (raw_score * section_weight)
            + metadata_score
            + category_boost
            + source_boost
            - quality_penalty,
        )
        if score < min_score or score <= 0:
            continue

        result = {
            "rank": 0,
            "score": round(score, 3),
            "raw_score": round(raw_score, 3),
            "metadata_score": round(metadata_score, 3),
            "section_weight": round(section_weight, 3),
            "category_boost": round(category_boost, 3),
            "source_boost": round(source_boost, 3),
            "quality_penalty": round(quality_penalty, 3),
            "chunk_id": chunk.get("chunk_id", ""),
            "paper_id": chunk_paper_id,
            "chunk_index": chunk.get("chunk_index", ""),
            "page_start": chunk.get("page_start"),
            "page_end": chunk.get("page_end"),
            "section": section,
            "section_confidence": round(float(section_info["section_confidence"]), 3),
            "section_markers": list(section_info["section_markers"]),
            "snippet": "",
            "text_preview": "",
            "_snippet_normalized_query": normalized_query,
            "_snippet_terms": query_terms,
            "text": text,
            "quality_flags": list(quality["flags"]),
            "source_file": str(chunk_file),
            "line_number": line_number,
        }
        for field in RESULT_METADATA_FIELDS:
            result[field] = paper_meta.get(field, "")
        candidates.append(result)

    candidates.sort(key=lambda item: (-float(item["score"]), -float(item["raw_score"]), str(item["paper_id"]), str(item["chunk_id"])))
    results = _diversify_results(candidates, limit=limit, max_per_paper=max_per_paper)
    for rank, result in enumerate(results, start=1):
        result["rank"] = rank
    return results


def search_chunks_multi(
    queries: list[tuple[str, str]],
    chunks_dir: Path,
    papers_tsv: Path | None = None,
    limit: int = 20,
    include_low_quality: bool = False,
    exclude_references: bool = True,
    exclude_tables: bool = False,
    exclude_figures: bool = False,
    exclude_supplement: bool = False,
    category: str | None = None,
    paper_id: str | None = None,
    require_doi: bool = False,
    min_score: float = 0.0,
    max_per_paper: int = 2,
    query_mode: str = "evidence",
    prefer_sections: list[str] | None = None,
    exclude_sections: list[str] | None = None,
    include_sections: list[str] | None = None,
    include_inactive: bool = False,
) -> list[dict]:
    """Search active corpus chunks once, without applying acquisition-time topic policy."""
    query_plans = []
    for label, query in queries:
        normalized_query = normalize_text(query)
        terms = _scoring_query_tokens(query)
        if not normalized_query or not terms:
            continue
        query_plans.append(
            {
                "label": label or "query",
                "query": query,
                "normalized_query": normalized_query,
                "terms": terms,
                "category_boosts": infer_query_category_boosts(query),
            }
        )
    if not query_plans:
        return []

    metadata = load_paper_metadata(papers_tsv) if papers_tsv else {}
    excluded_sections = _section_filter_set(exclude_sections)
    included_sections = _section_filter_set(include_sections)
    preferred_sections = _section_filter_set(prefer_sections)
    if exclude_references:
        excluded_sections.add("references")

    candidates: list[dict] = []
    previous_sections: dict[str, str | None] = defaultdict(lambda: None)

    for chunk_file, line_number, chunk in iter_chunk_records(Path(chunks_dir)):
        previous_section = previous_sections[chunk_file]
        chunk_paper_id = str(chunk.get("paper_id") or "")
        if paper_id and chunk_paper_id != paper_id:
            continue

        paper_meta = metadata.get(chunk_paper_id, {})
        if not include_inactive and _is_inactive_paper(paper_meta):
            continue
        if require_doi and not str(paper_meta.get("doi") or "").strip():
            continue
        if category and not _matches_category_filter(paper_meta, category):
            continue

        text = str(chunk.get("text") or "")
        section_info = _section_info(chunk, text, previous_section)
        section = str(section_info["section"])
        if carries_forward_section(section):
            previous_sections[chunk_file] = section
        if included_sections and section not in included_sections:
            continue
        if section in excluded_sections:
            continue

        quality = cached_chunk_quality(text)
        if _excluded_by_quality(
            quality,
            include_low_quality=include_low_quality,
            exclude_references=exclude_references,
            exclude_tables=exclude_tables,
            exclude_figures=exclude_figures,
            exclude_supplement=exclude_supplement,
        ):
            continue

        normalized_text = normalize_text(text)
        section_weight = get_section_weight(section, query_mode=query_mode)
        if preferred_sections:
            section_weight *= 1.12 if section in preferred_sections else 0.94
        quality_penalty = float(quality["quality_penalty"])

        best: dict | None = None
        hit_labels: list[str] = []
        for plan in query_plans:
            raw_score = _score_normalized_text(
                plan["normalized_query"], plan["terms"], normalized_text, raw_text=text
            )
            metadata_score = _metadata_match_score(
                plan["normalized_query"],
                plan["terms"],
                paper_meta,
                section,
                raw_score,
            )
            category_boost = _category_boost(paper_meta, plan["category_boosts"])
            source_boost = _source_lookup_boost(plan["query"], text, paper_meta)
            score = max(
                0.0,
                (raw_score * section_weight)
                + metadata_score
                + category_boost
                + source_boost
                - quality_penalty,
            )
            if score < min_score or score <= 0:
                continue
            hit_labels.append(str(plan["label"]))
            if best is None or score > float(best["score"]):
                best = {
                    "score": score,
                    "raw_score": raw_score,
                    "metadata_score": metadata_score,
                    "category_boost": category_boost,
                    "source_boost": source_boost,
                    "query": plan["query"],
                    "label": plan["label"],
                    "normalized_query": plan["normalized_query"],
                    "terms": plan["terms"],
                }
        if not best:
            continue

        unique_hit_labels = list(dict.fromkeys(hit_labels))
        score = round(float(best["score"]) + min(6.0, max(0, len(unique_hit_labels) - 1) * 2.0), 3)
        result = {
            "rank": 0,
            "score": score,
            "raw_score": round(float(best["raw_score"]), 3),
            "metadata_score": round(float(best["metadata_score"]), 3),
            "section_weight": round(section_weight, 3),
            "category_boost": round(float(best["category_boost"]), 3),
            "source_boost": round(float(best["source_boost"]), 3),
            "quality_penalty": round(quality_penalty, 3),
            "chunk_id": chunk.get("chunk_id", ""),
            "paper_id": chunk_paper_id,
            "chunk_index": chunk.get("chunk_index", ""),
            "page_start": chunk.get("page_start"),
            "page_end": chunk.get("page_end"),
            "section": section,
            "section_confidence": round(float(section_info["section_confidence"]), 3),
            "section_markers": list(section_info["section_markers"]),
            "snippet": "",
            "text_preview": "",
            "_snippet_normalized_query": str(best["normalized_query"]),
            "_snippet_terms": list(best["terms"]),
            "text": text,
            "quality_flags": list(quality["flags"]),
            "source_file": str(chunk_file),
            "line_number": line_number,
            "retrieval_query": best["query"],
            "retrieval_query_labels": ";".join(unique_hit_labels),
            "retrieval_query_hits": len(unique_hit_labels),
        }
        for field in RESULT_METADATA_FIELDS:
            result[field] = paper_meta.get(field, "")
        candidates.append(result)

    candidates.sort(key=lambda item: (-float(item["score"]), -float(item["raw_score"]), str(item["paper_id"]), str(item["chunk_id"])))
    results = _diversify_results(candidates, limit=limit, max_per_paper=max_per_paper)
    for rank, result in enumerate(results, start=1):
        result["rank"] = rank
    return results


def iter_chunk_records(
    chunks_dir: Path,
    *,
    candidate_paper_ids: frozenset[str] | set[str] | None = None,
    canonical_layout_verified: bool = False,
) -> tuple[tuple[str, int, dict], ...]:
    """Return chunk records, optionally reading only canonical per-paper files.

    Narrowing requires a separately verified corpus-layout invariant. If the invariant is absent
    or any requested ID cannot be mapped to an existing ``<paper_id>.jsonl`` file without leaving
    ``chunks_dir``, the function uses the original complete scan.
    """
    path = Path(chunks_dir).resolve()
    if canonical_layout_verified and candidate_paper_ids is not None:
        chunk_files = _canonical_candidate_chunk_files(path, candidate_paper_ids)
        if chunk_files is not None:
            return _records_from_chunk_files(chunk_files)
    return _cached_chunk_records(str(path), chunk_cache_token(path))


@lru_cache(maxsize=8192)
def cached_chunk_quality(text: str) -> dict:
    """Reuse deterministic chunk QC across query fan-out in the web service."""
    return classify_chunk_quality(text)


def _chunk_file_identity(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
        stat.st_size,
    )


def chunk_cache_token(chunks_dir: Path) -> tuple[tuple[str, tuple[int, int, int, int, int]], ...]:
    token = []
    for path in sorted(Path(chunks_dir).glob("*.jsonl")):
        token.append((path.name, _chunk_file_identity(path)))
    return tuple(token)


@lru_cache(maxsize=4)
def _cached_chunk_records(
    chunks_dir_text: str,
    cache_token: tuple[tuple[str, tuple[int, int, int, int, int]], ...],
) -> tuple[tuple[str, int, dict], ...]:
    records: list[tuple[str, int, dict]] = []
    chunks_dir = Path(chunks_dir_text)
    for filename, identity in cache_token:
        records.extend(
            _cached_chunk_file_records(str(chunks_dir / filename), identity)
        )
    return tuple(records)


def _canonical_candidate_chunk_files(
    chunks_dir: Path,
    paper_ids: frozenset[str] | set[str],
) -> tuple[Path, ...] | None:
    chunk_files: list[Path] = []
    for paper_id in sorted({str(value) for value in paper_ids}):
        filename = f"{paper_id}.jsonl"
        if not paper_id or Path(filename).name != filename:
            return None
        chunk_file = chunks_dir / filename
        if not chunk_file.is_file():
            return None
        chunk_files.append(chunk_file)
    return tuple(chunk_files)


def _records_from_chunk_files(
    chunk_files: tuple[Path, ...],
) -> tuple[tuple[str, int, dict], ...]:
    records: list[tuple[str, int, dict]] = []
    for chunk_file in chunk_files:
        records.extend(
            _cached_chunk_file_records(
                str(chunk_file), _chunk_file_identity(chunk_file)
            )
        )
    return tuple(records)


@lru_cache(maxsize=4096)
def _cached_chunk_file_records(
    chunk_file_text: str,
    identity: tuple[int, int, int, int, int],
) -> tuple[tuple[str, int, dict], ...]:
    del identity
    chunk_file = Path(chunk_file_text)
    records: list[tuple[str, int, dict]] = []
    with chunk_file.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            records.append((str(chunk_file), line_number, json.loads(line)))
    return tuple(records)


def normalize_text(value: str) -> str:
    value = (value or "").lower()
    if not value.isascii():
        replacements = {
            "α": "alpha",
            "β": "beta",
            "γ": "gamma",
            "δ": "delta",
            "–": "-",
            "—": "-",
            "\u00ad": "",
            "\ufb01": "fi",
            "\ufb02": "fl",
        }
        for source, target in replacements.items():
            value = value.replace(source, target)
        value = "".join(
            character
            for character in unicodedata.normalize("NFKD", value)
            if not unicodedata.combining(character)
        )
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


@lru_cache(maxsize=512)
def _normalized_term_pattern(normalized_term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])")


def count_normalized_occurrences(normalized_text: str, term: str) -> int:
    """Count a normalized token or phrase without matching inside other words."""
    normalized_term = normalize_text(term)
    if not normalized_text or not normalized_term:
        return 0
    return len(_normalized_term_pattern(normalized_term).findall(normalized_text))


def contains_normalized_term(normalized_text: str, term: str) -> bool:
    """Return whether a normalized token or phrase occurs at word boundaries."""
    normalized_term = normalize_text(term)
    if not normalized_text or not normalized_term:
        return False
    return _normalized_term_pattern(normalized_term).search(normalized_text) is not None


def _contains_any_normalized_term(normalized_text: str, terms: list[str]) -> bool:
    return any(contains_normalized_term(normalized_text, term) for term in terms)


def query_tokens(query: str) -> list[str]:
    normalized = normalize_text(query)
    terms = [term for term in normalized.split() if term]
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        if term in {"desease", "deseases"}:
            expanded.extend(["disease", "diseases"])
        if term == "kohorte":
            expanded.append("cohort")
        if term == "kenya":
            expanded.append("kenyan")
        if "cidralpha" in term:
            expanded.append(term.replace("cidralpha", "cidra"))
        if "dblalpha" in term:
            expanded.append(term.replace("dblalpha", "dbla"))
    expanded.extend(contextual_query_expansions(normalized))
    return list(dict.fromkeys(expanded))


def _scoring_query_tokens(query: str) -> list[str]:
    """Keep numerical literals intact without changing identifier tokenization."""
    literals = [match.group() for match in _NUMERIC_LITERAL.finditer(query)]
    if not literals:
        return query_tokens(query)
    terms = query_tokens(_NUMERIC_LITERAL.sub(" ", query))
    return list(dict.fromkeys([*terms, *literals]))


@lru_cache(maxsize=1024)
def _numeric_literal_key(value: str) -> tuple | str:
    value = value.replace("−", "-")
    try:
        number = Decimal(value).as_tuple()
    except InvalidOperation:
        return value.lower()
    digits = number.digits
    exponent = number.exponent
    if not any(digits):
        return (0, (0,), 0)
    while len(digits) > 1 and digits[-1] == 0:
        digits = digits[:-1]
        exponent += 1
    return number.sign, digits, exponent


def contextual_query_expansions(normalized_query: str) -> list[str]:
    """Add domain terms for broad biological questions that users phrase loosely."""
    expansions: list[str] = []
    if _contains_any_normalized_term(
        normalized_query,
        ["virulent", "virulence", "pathogenic", "pathogenesis", "severe disease"],
    ):
        expansions.extend(
            [
                "pfemp1",
                "var",
                "cytoadherence",
                "sequestration",
                "antigenic",
                "variation",
                "rosetting",
                "epcr",
                "icam",
                "cd36",
                "endothelial",
                "severe",
            ]
        )
    if _contains_any_normalized_term(
        normalized_query,
        ["broad", "overview", "why", "warum", "mechanism", "mechanisms", "mechanismus"],
    ):
        if _contains_any_normalized_term(normalized_query, ["malaria", "plasmodium", "falciparum"]):
            expansions.extend(["pfemp1", "host", "immunity", "parasite", "severe", "infection"])
    if _contains_any_normalized_term(normalized_query, ["follow up", "previous", "vorher", "dazu"]):
        expansions.extend(["evidence", "source"])
    return expansions


def _score_text(normalized_query: str, terms: list[str], text: str) -> float:
    normalized_text = normalize_text(text)
    return _score_normalized_text(normalized_query, terms, normalized_text, raw_text=text)


def _score_normalized_text(
    normalized_query: str, terms: list[str], normalized_text: str, *, raw_text: str = ""
) -> float:
    if not normalized_text:
        return 0.0

    numeric_terms = {
        term: _numeric_literal_key(term)
        for term in terms
        if _NUMERIC_LITERAL.fullmatch(term)
    }
    numeric_counts = (
        Counter(_numeric_literal_key(match.group()) for match in _NUMERIC_LITERAL.finditer(raw_text))
        if numeric_terms
        else {}
    )
    numeric_phrase_matches = all(numeric_counts.get(key, 0) for key in numeric_terms.values())
    phrase_count = (
        min(count_normalized_occurrences(normalized_text, normalized_query), 3)
        if numeric_phrase_matches else 0
    )
    phrase_score = phrase_count * 18.0
    term_score = 0.0
    matched_terms = 0
    for term in terms:
        count = (
            numeric_counts.get(numeric_terms[term], 0)
            if term in numeric_terms
            else count_normalized_occurrences(normalized_text, term)
        )
        if count <= 0:
            continue
        matched_terms += 1
        term_score += _term_weight(term) * min(math.log1p(count) * 2.0, 5.0)

    coverage = matched_terms / max(len(terms), 1)
    coverage_bonus = 6.0 * coverage if matched_terms else 0.0
    all_terms_bonus = 5.0 if matched_terms == len(terms) else 0.0
    return phrase_score + term_score + coverage_bonus + all_terms_bonus


def _term_weight(term: str) -> float:
    if term in COMMON_TERMS:
        return 0.55
    if len(term) >= 10 or any(char.isdigit() for char in term):
        return 2.6
    if len(term) >= 7:
        return 1.8
    if len(term) >= 5:
        return 1.25
    return 0.9


def _metadata_match_score(
    normalized_query: str,
    terms: list[str],
    metadata: dict[str, str],
    section: str,
    raw_chunk_score: float,
) -> float:
    if raw_chunk_score <= 0 or section in NO_METADATA_BOOST_SECTIONS:
        return 0.0
    title = normalize_text(str(metadata.get("title") or ""))
    if not title:
        return 0.0
    title_score = _score_normalized_text(
        normalized_query, terms, title, raw_text=str(metadata.get("title") or "")
    )



    return min(24.0, title_score * 0.75)


def infer_query_category_boosts(query: str) -> dict[str, float]:
    normalized = normalize_text(query)
    boosts: dict[str, float] = {}
    if _contains_any_normalized_term(normalized, ["sex", "female", "male", "gender"]):
        boosts.update({"sex_diff": 12.0, "host_immunity": 4.0})
    if _contains_any_normalized_term(normalized, ["pfemp1", "var", "epcr", "cd36", "vsa"]):
        boosts.update({"var_pfemp1": 12.0, "host_immunity": 4.0, "general_malaria": 3.0})
    if _contains_any_normalized_term(
        normalized,
        ["virulent", "virulence", "pathogenic", "pathogenesis", "sequestration", "cytoadherence"],
    ):
        boosts.update(
            {
                "var_pfemp1": max(boosts.get("var_pfemp1", 0.0), 14.0),
                "host_immunity": max(boosts.get("host_immunity", 0.0), 7.0),
                "parasite_biology": max(boosts.get("parasite_biology", 0.0), 6.0),
                "general_malaria": max(boosts.get("general_malaria", 0.0), 4.0),
            }
        )
    if _contains_any_normalized_term(
        normalized,
        [
            "resistance",
            "diagnostic escape",
            "molecular surveillance",
            "artemisinin",
            "kelch13",
            "k13",
            "pfhrp",
            "pfhrp2",
            "pfhrp3",
        ],
    ):
        boosts.update({"resistance_dx": 12.0, "parasite_biology": 5.0, "tools_ml": 3.0})
    if _contains_any_normalized_term(
        normalized, ["single cell", "scrna", "rna seq", "omics", "transcript"]
    ):
        boosts.update({"tools_ml": max(boosts.get("tools_ml", 0.0), 8.0), "host_immunity": 4.0})
    return boosts


def _source_lookup_boost(query: str, text: str, metadata: dict[str, str]) -> float:
    normalized_query = normalize_text(query)
    if not _looks_like_source_lookup(normalized_query):
        return 0.0

    normalized_text = normalize_text(text)
    metadata_text = normalize_text(
        " ".join(
            str(metadata.get(field) or "")
            for field in ["author", "year", "title", "journal", "doi", "pmid", "pmcid"]
        )
    )
    searchable = f"{metadata_text} {normalized_text}".strip()
    if not searchable:
        return 0.0

    boost = 0.0
    if any(term in normalized_query for term in ["kenya", "kenyan", "kohorte", "cohort"]):
        location_hits = sum(
            1
            for term in ["kenya", "kenyan", "kilifi", "chonyi", "western kenya"]
            if term in searchable
        )
        if location_hits:
            boost += min(12.0, 4.0 * location_hits)
        if "cohort" in searchable:
            boost += 5.0

    threshold = _sample_size_threshold(normalized_query)
    if threshold:
        sample_numbers = _sample_size_numbers(searchable)
        if _mentions_approximate_threshold(searchable, threshold):
            boost += 24.0
        elif any(number >= threshold for number in sample_numbers):
            boost += 22.0
        elif len(sample_numbers) >= 2 and sum(sample_numbers[:4]) >= threshold:
            boost += 16.0

    query_terms = [term for term in normalized_query.split() if len(term) >= 4 and term not in COMMON_TERMS]
    title_hits = sum(1 for term in query_terms if term in metadata_text)
    if title_hits:
        boost += min(10.0, 2.5 * title_hits)

    return boost


def _looks_like_source_lookup(normalized_query: str) -> bool:
    source_markers = {
        "paper",
        "papers",
        "source",
        "sources",
        "reference",
        "references",
        "quelle",
        "quellen",
        "hieß",
        "hiess",
        "which",
        "what",
    }
    terms = set(normalized_query.split())
    return bool(terms & source_markers)


def _sample_size_threshold(normalized_query: str) -> int | None:
    patterns = [
        r"(?:over|more than|above|at least|about|around|ca|circa)\s+(\d{2,5})",
        r"(\d{2,5})\s*(?:\+|plus)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized_query)
        if match:
            return int(match.group(1))
    return None


def _mentions_approximate_threshold(normalized_text: str, threshold: int) -> bool:
    lower_bound = max(0, threshold - 50)
    upper_bound = threshold + 75
    for number in _numbers(normalized_text):
        if lower_bound <= number <= upper_bound and _near_sample_context(normalized_text, str(number)):
            return True
    return False


def _sample_size_numbers(normalized_text: str) -> list[int]:
    numbers = []
    for match in re.finditer(r"\b\d{2,5}\b", normalized_text):
        number = int(match.group(0))
        if 1900 <= number <= 2100:
            continue
        if _near_sample_context(normalized_text, match.group(0), start=match.start(), end=match.end()):
            numbers.append(number)
    return numbers


def _numbers(normalized_text: str) -> list[int]:
    return [
        int(match.group(0))
        for match in re.finditer(r"\b\d{2,5}\b", normalized_text)
        if not 1900 <= int(match.group(0)) <= 2100
    ]


def _near_sample_context(normalized_text: str, number_text: str, start: int | None = None, end: int | None = None) -> bool:
    if start is None or end is None:
        match = re.search(rf"\b{re.escape(number_text)}\b", normalized_text)
        if not match:
            return False
        start, end = match.start(), match.end()
    window = normalized_text[max(0, start - 80) : min(len(normalized_text), end + 80)]
    return any(
        term in window
        for term in [
            "case",
            "cases",
            "child",
            "children",
            "cohort",
            "participant",
            "participants",
            "patient",
            "patients",
            "sample",
            "samples",
            "severe",
            "non severe",
        ]
    )


def _category_boost(metadata: dict[str, str], boosts: dict[str, float]) -> float:
    if not boosts:
        return 0.0
    values = [
        metadata.get("category", ""),
        metadata.get("topics", ""),
        metadata.get("methods", ""),
        metadata.get("systems", ""),
    ]
    normalized_values = " ".join(normalize_text(value) for value in values if value)
    return max((boost for category, boost in boosts.items() if normalize_text(category) in normalized_values), default=0.0)


def _is_inactive_paper(metadata: dict[str, str]) -> bool:
    status = str(metadata.get("fulltext_status") or "").strip()
    if not status:
        return False
    return status not in ACTIVE_FULLTEXT_STATUSES


def _is_out_of_scope_paper(metadata: dict[str, str], *, query: str = "") -> bool:
    if not metadata:
        return False
    if not _has_scope_metadata(metadata):
        return False
    context = " ".join(
        str(metadata.get(field) or "")
        for field in ["category", "domain", "topics", "methods", "systems", "paper_type", "notes"]
    )
    if explicitly_requests_paper_scope(metadata, query):
        return False
    decision = relevance_decision(
        title=str(metadata.get("title") or ""),
        journal=str(metadata.get("journal") or ""),
        category=str(metadata.get("category") or metadata.get("domain") or ""),
        query=context,
    )
    return not decision.keep


def explicitly_requests_paper_scope(metadata: dict[str, str], query: str) -> bool:
    """Let explicit cross-domain questions retrieve matching local papers.

    The historical corpus-hygiene policy remains useful for malaria-focused
    searches, but it must not make a user-added COVID-19, cancer, or arthritis
    paper invisible when the user explicitly asks about that domain.
    """
    normalized_query = normalize_text(query)
    if not normalized_query:
        return False
    normalized_paper = normalize_text(
        " ".join(
            str(metadata.get(field) or "")
            for field in ("title", "category", "domain", "topics", "systems")
        )
    )
    if any(
        marker in normalized_query
        for marker in ("whatever", "any disease", "across diseases", "all diseases")
    ):
        return True
    if any(term in normalized_query for term in VACCINE_TERMS) and any(
        term in normalized_paper for term in VACCINE_TERMS
    ):




        return True
    return any(
        normalize_text(term) in normalized_query
        and normalize_text(term) in normalized_paper
        for term in HARD_OFF_TOPIC_TERMS
    )


def _has_scope_metadata(metadata: dict[str, str]) -> bool:
    return any(
        str(metadata.get(field) or "").strip()
        for field in ["category", "domain", "topics", "methods", "systems", "paper_type", "notes", "local_pdf_relpath"]
    )


def _excluded_by_quality(
    quality: dict,
    include_low_quality: bool,
    exclude_references: bool,
    exclude_tables: bool,
    exclude_figures: bool,
    exclude_supplement: bool,
) -> bool:
    if quality["is_empty"]:
        return True
    if not include_low_quality and quality["is_low_text_quality"] and quality["quality_penalty"] >= 20:
        return True
    if exclude_references and quality["is_reference_like"]:
        return True
    if exclude_tables and quality["is_table_like"]:
        return True
    if exclude_figures and quality["is_figure_like"]:
        return True
    if exclude_supplement and quality["is_supplement_like"]:
        return True
    return False


def _matches_category_filter(metadata: dict[str, str], category: str) -> bool:
    wanted = normalize_text(category)
    values = [
        metadata.get("category", ""),
        metadata.get("topics", ""),
        metadata.get("methods", ""),
        metadata.get("systems", ""),
    ]
    return any(wanted in normalize_text(value) for value in values if value)


def _section_info(chunk: dict, text: str, previous_section: str | None) -> dict:
    if not stored_section_needs_redetection(chunk):
        return {
            "section": str(chunk.get("section") or "unknown"),
            "section_confidence": float(chunk.get("section_confidence") or 0.0),
            "section_markers": chunk.get("section_markers") or [],
        }
    return detect_section_for_chunk(text, previous_section=previous_section)


def _section_filter_set(values: list[str] | None) -> set[str]:
    return {normalize_text(value).replace(" ", "_") for value in (values or []) if value}


def _diversify_results(candidates: list[dict], limit: int, max_per_paper: int) -> list[dict]:
    if limit <= 0:
        return []

    kept: list[dict] = []
    per_paper_counts: dict[str, int] = defaultdict(int)
    per_paper_snippets: dict[str, list[str]] = defaultdict(list)

    for candidate in candidates:
        paper_id = str(candidate.get("paper_id") or "")
        if max_per_paper > 0 and per_paper_counts[paper_id] >= max_per_paper:
            continue
        snippet = _materialize_candidate_preview(candidate)
        if _is_near_duplicate_snippet(snippet, per_paper_snippets[paper_id]):
            continue
        kept.append(candidate)
        per_paper_counts[paper_id] += 1
        per_paper_snippets[paper_id].append(snippet)
        if len(kept) >= limit:
            break
    return kept


def _materialize_candidate_preview(candidate: dict) -> str:
    text = str(candidate.get("text") or "")
    if not candidate.get("snippet"):
        candidate["snippet"] = _make_snippet(
            str(candidate.pop("_snippet_normalized_query", "")),
            list(candidate.pop("_snippet_terms", [])),
            text,
        )
    else:
        candidate.pop("_snippet_normalized_query", None)
        candidate.pop("_snippet_terms", None)
    if not candidate.get("text_preview"):
        candidate["text_preview"] = _clean_preview(text, limit=500)
    return str(candidate.get("snippet") or candidate.get("text_preview") or "")


def _is_near_duplicate_snippet(snippet: str, previous_snippets: list[str]) -> bool:
    candidate_terms = _content_term_set(snippet)
    if len(candidate_terms) < 8:
        return False
    for previous in previous_snippets:
        previous_terms = _content_term_set(previous)
        if len(previous_terms) < 8:
            continue
        overlap = len(candidate_terms & previous_terms) / max(len(candidate_terms | previous_terms), 1)
        containment = len(candidate_terms & previous_terms) / max(min(len(candidate_terms), len(previous_terms)), 1)
        if overlap >= 0.72 or containment >= 0.88:
            return True
    return False


def _content_term_set(text: str) -> set[str]:
    return {term for term in normalize_text(text).split() if len(term) >= 4 and term not in COMMON_TERMS}


def _make_snippet(normalized_query: str, terms: list[str], text: str, window: int = 260) -> str:
    normalized_text, normalized_to_original = _normalize_with_offsets(text)
    normalized_index = normalized_text.find(normalized_query)
    if normalized_index < 0:
        term_positions = [
            (position, term)
            for term in terms
            if (position := normalized_text.find(term)) >= 0
        ]
        normalized_index = min((position for position, _ in term_positions), default=0)

    original_index = normalized_to_original[min(normalized_index, len(normalized_to_original) - 1)] if normalized_to_original else 0
    start = max(0, original_index - window // 2)
    end = min(len(text), original_index + window)
    snippet = _clean_preview(text[start:end], limit=window + 80)
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


def _normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    """Normalize text and retain source offsets in one linear pass."""
    replacements = {
        "α": "alpha",
        "β": "beta",
        "γ": "gamma",
        "δ": "delta",
        "\u00ad": "",
        "\ufb01": "fi",
        "\ufb02": "fl",
    }
    normalized_chars: list[str] = []
    offsets: list[int] = []
    previous_was_space = True
    for index, char in enumerate(text):
        lowered = char.lower()
        fragment = replacements.get(lowered, lowered)
        fragment = "".join(
            value
            for value in unicodedata.normalize("NFKD", fragment)
            if not unicodedata.combining(value)
        )
        normalized = "".join(
            value
            for value in fragment
            if "a" <= value <= "z" or "0" <= value <= "9"
        )
        if normalized:
            for value in normalized:
                normalized_chars.append(value)
                offsets.append(index)
            previous_was_space = False
        elif not previous_was_space:
            normalized_chars.append(" ")
            offsets.append(index)
            previous_was_space = True
    if normalized_chars and normalized_chars[-1] == " ":
        normalized_chars.pop()
        offsets.pop()
    return "".join(normalized_chars), offsets


def _clean_preview(text: str, limit: int = 300) -> str:
    preview = " ".join((text or "").split())
    if len(preview) > limit:
        return preview[: max(0, limit - 3)].rstrip() + "..."
    return preview
