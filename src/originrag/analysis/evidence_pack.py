from __future__ import annotations

import math
import re
from collections import Counter

from originrag.analysis.template_answers import clean_evidence_text
from originrag.retrieval.text_cleaning import evidence_window
from originrag.utils.normalization import (
    finite_float,
    is_finite_number,
    normalize_string_list,
    safe_int,
)


PREFERRED_EVIDENCE_SECTIONS = {"results", "discussion", "abstract", "methods"}
NOISY_SECTIONS = {"references", "table", "figure_legend", "supplement", "data_availability"}
NOISY_FLAGS = {"reference_like", "table_like", "figure_like", "supplement_like", "low_text_quality"}
HARD_ANCHOR_BLOCK_FLAGS = {"reference_like", "low_text_quality"}
PROMPT_VISIBLE_TEXT_FROZEN = "_pfempire_prompt_visible_text_frozen"
NAMED_STUDY_SUMMARY_MIN_CHARS = 1800
NAMED_STUDY_SUPPORT_MIN_CHARS = 2500
TITLE_ANCHOR_STOPWORDS = {
    "about", "among", "especially", "evidence", "explain", "from", "have", "including",
    "into", "malaria", "might", "paper", "papers", "pfemp1", "plasmodium", "relevant",
    "that", "their", "these", "this", "those", "what", "when", "where", "which", "with",
    "would", "why",
}


def build_evidence_pack(
    question: str,
    results: list[dict],
    max_chunks: int = 12,
    max_chars_per_chunk: int = 1800,
) -> dict:
    chunks = []
    selected_results = select_evidence_results(results, max_chunks=max_chunks, question=question)
    for index, result in enumerate(selected_results, start=1):
        clean_text = evidence_text_for_pack(question, result, max_chars_per_chunk)
        chunks.append(
            {
                "evidence_id": f"E{index:03d}",
                "paper_id": str(result.get("paper_id") or ""),
                "citation_label": citation_label(result),
                "citation_short": str(result.get("citation_short") or ""),
                "citation_apa": str(result.get("citation_apa") or ""),
                "bib_key": str(result.get("bib_key") or ""),
                "author": str(result.get("author") or ""),
                "year": str(result.get("year") or ""),
                "doi": str(result.get("doi") or ""),
                "pmid": str(result.get("pmid") or ""),
                "pmcid": str(result.get("pmcid") or ""),
                "title": str(result.get("title") or ""),
                "journal": str(result.get("journal") or ""),
                "page_range": page_range(result.get("page_start"), result.get("page_end")),
                "chunk_id": str(result.get("chunk_id") or ""),
                "score": result.get("score", ""),
                "section": str(result.get("section") or "unknown"),
                "section_confidence": result.get("section_confidence", ""),
                "section_weight": result.get("section_weight", ""),
                "quality_flags": result_quality_flags(result),
                "selection_reason": str(result.get("evidence_selection_reason") or "selected"),
                "prompt_context_only": bool(result.get("prompt_context_only")),
                "explicit_named_study_anchor": bool(
                    result.get("explicit_named_study_anchor")
                ),
                "explicit_named_study_support": bool(
                    result.get("explicit_named_study_support")
                ),
                "named_study_surname": str(
                    result.get("named_study_surname") or ""
                ),
                "named_study_year": str(result.get("named_study_year") or ""),
                "named_study_source_text_scope": str(
                    result.get("named_study_source_text_scope") or ""
                ),
                "text": clean_text,
            }
        )

    return {
        "question": question,
        "n_chunks": len(chunks),
        "n_unique_papers": len({chunk["paper_id"] for chunk in chunks if chunk["paper_id"]}),
        "chunks": chunks,
    }


def apply_certification_core_roles(pack: dict) -> None:
    """Mark prompt-visible sources as certifying or contextual.

    The function name is retained for compatibility. Current runtime packs use
    ``trace_certifying_evidence`` and include every selected report.
    """

    certifying = pack.get("trace_certifying_evidence")
    if not isinstance(certifying, dict):
        certifying = pack.get("trace_certification_core")
    if not isinstance(certifying, dict):
        return
    certifying_papers = {
        str(paper_id)
        for paper_id in certifying.get("paper_ids", [])
        if str(paper_id).strip()
    }
    for chunk in pack.get("chunks", []):
        if not isinstance(chunk, dict):
            continue
        chunk["certification_role"] = "context_only" if chunk.get(
            "prompt_context_only"
        ) else (
            "certifying"
            if str(chunk.get("paper_id") or "") in certifying_papers
            else "context_only"
        )


def freeze_prompt_visible_result(
    question: str,
    result: dict,
    max_chars_per_chunk: int,
) -> dict:
    """Return a copy whose text is exactly what the answer prompt will expose."""

    frozen = dict(result)
    frozen["text"] = evidence_text_for_pack(question, result, max_chars_per_chunk)
    frozen[PROMPT_VISIBLE_TEXT_FROZEN] = True
    return frozen


def evidence_text_for_pack(
    question: str,
    result: dict,
    max_chars_per_chunk: int,
) -> str:
    if result.get(PROMPT_VISIBLE_TEXT_FROZEN):
        return str(result.get("text") or "")
    text = str(result.get("text") or result.get("text_preview") or result.get("snippet") or "")
    if (
        (
            result.get("explicit_named_study_anchor")
            and result.get("named_study_source_text_scope")
            == "front_summary_excerpt"
        )
        or result.get("explicit_named_study_support")
    ):







        named_limit = (
            NAMED_STUDY_SUPPORT_MIN_CHARS
            if result.get("explicit_named_study_support")
            else NAMED_STUDY_SUMMARY_MIN_CHARS
        )
        return truncate_text(clean_evidence_text(text), max(max_chars_per_chunk, named_limit))
    window_question = question
    required_anchor_groups = result.get("required_entity_anchors") or []
    if (
        result.get("required_entity_anchors_verified")
        and required_anchor_groups
        and result.get("anchor_only_evidence_window", True)
    ):
        window_question = " ".join(
            str(alias)
            for group in required_anchor_groups
            if isinstance(group, (list, tuple))
            for alias in group
            if str(alias).strip()
        )
    elif result.get("evidence_window_intent") == "data_accessibility":
        window_question = (
            f"{question} data availability data access repository accession "
            "deposited uploaded GEO SRA ENA BioProject"
        )
    clean_text = evidence_window(
        question=window_question,
        text=text,
        snippet=str(result.get("snippet") or ""),
        max_chars=max_chars_per_chunk,
    )
    clean_text = clean_evidence_text(clean_text)
    return truncate_text(clean_text, max_chars_per_chunk)


def select_evidence_results(results: list[dict], max_chunks: int = 12, question: str = "") -> list[dict]:
    """Prefer high-scoring, low-noise chunks for answer evidence packs."""
    if max_chunks <= 0:
        return []
    if len(results) <= max_chunks:
        return list(results)

    ranked = sorted(
        enumerate(results),
        key=lambda item: (_evidence_sort_score(item[1]), -item[0]),
        reverse=True,
    )
    unique_paper_budget = min(max_chunks, max(1, math.ceil(max_chunks * 0.70)))
    selected_pairs: list[tuple[int, dict]] = []
    selected_indexes: set[int] = set()
    selected_papers: set[str] = set()
    selection_reasons: dict[int, str] = {}
    title_anchor_budget = min(unique_paper_budget, max(1, max_chunks // 6))
    for original_index, result, specificity in title_anchor_candidates(results, question):
        if len(selected_pairs) >= title_anchor_budget:
            break
        paper_id = str(result.get("paper_id") or "")
        if paper_id in selected_papers:
            continue
        selected_pairs.append((original_index, result))
        selected_indexes.add(original_index)
        selected_papers.add(paper_id)
        selection_reasons[original_index] = f"specific_title_anchor:{specificity:.3f}"
    for original_index, result in ranked:
        if len(selected_pairs) >= unique_paper_budget:
            break
        paper_id = str(result.get("paper_id") or "")
        if paper_id in selected_papers:
            continue
        selected_pairs.append((original_index, result))
        selected_indexes.add(original_index)
        selected_papers.add(paper_id)
        selection_reasons[original_index] = "diverse_paper_relevance"
    for original_index, result in ranked:
        if len(selected_pairs) >= max_chunks:
            break
        if original_index in selected_indexes:
            continue
        selected_pairs.append((original_index, result))
        selected_indexes.add(original_index)
        selection_reasons[original_index] = "relevance_fill"
    selected = [
        {**result, "evidence_selection_reason": selection_reasons.get(original_index, "selected")}
        for original_index, result in selected_pairs
    ]
    selected.sort(key=lambda result: safe_int(result.get("rank"), 999999))
    return selected


def title_anchor_candidates(results: list[dict], question: str) -> list[tuple[int, dict, float]]:
    """Rank papers that uniquely name specific concepts from the user question."""
    if not results:
        return []
    terms = title_anchor_terms(question)
    if not terms:
        return []
    paper_titles: dict[str, set[str]] = {}
    for index, result in enumerate(results):
        paper_id = str(result.get("paper_id") or f"__row_{index}")
        paper_titles.setdefault(paper_id, normalized_tokens(str(result.get("title") or "")))
    document_frequency = Counter(
        term for title_terms in paper_titles.values() for term in terms if term in title_terms
    )
    n_papers = max(1, len(paper_titles))
    scored = []
    seen_papers = set()
    for index, result in enumerate(results):
        paper_id = str(result.get("paper_id") or f"__row_{index}")
        if paper_id in seen_papers or not clean_anchor_candidate(result):
            continue
        seen_papers.add(paper_id)
        title_terms = paper_titles[paper_id]
        matched = [term for term in terms if term in title_terms]
        if not matched:
            continue
        score = sum(math.log((n_papers + 1) / (document_frequency[term] + 1)) + 1.0 for term in matched)
        score *= len(matched) / len(terms)
        scored.append((index, result, score))
    return sorted(
        scored,
        key=lambda item: (
            -item[2],
            -_evidence_sort_score(item[1]),
            str(item[1].get("paper_id") or ""),
            str(item[1].get("chunk_id") or ""),
        ),
    )


def title_anchor_terms(question: str) -> list[str]:
    terms = normalized_tokens(question)
    return sorted(term for term in terms if len(term) >= 4 and term not in TITLE_ANCHOR_STOPWORDS)


def normalized_tokens(text: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    return {term for term in normalized.split() if term}


def clean_anchor_candidate(result: dict) -> bool:
    section = str(result.get("section") or "unknown")
    flags = set(result_quality_flags(result))




    return section not in NOISY_SECTIONS and not (flags & HARD_ANCHOR_BLOCK_FLAGS)


def _evidence_sort_score(result: dict) -> float:
    raw_score = finite_float(result.get("score"), 0.0)
    rrf_score = finite_float(result.get("rrf_score"), 0.0)
    paper_rrf_score = finite_float(result.get("paper_rrf_score"), 0.0)
    paper_relevance_score = finite_float(result.get("paper_relevance_score"), 0.0)


    if paper_relevance_score > 0:
        score = (paper_relevance_score * 10000.0) + (rrf_score * 100.0) + (raw_score * 0.001)
    elif paper_rrf_score > 0:
        score = (paper_rrf_score * 10000.0) + (rrf_score * 100.0) + (raw_score * 0.001)
    elif rrf_score > 0:
        score = (rrf_score * 1000.0) + (raw_score * 0.001)
    else:
        score = raw_score
    section = str(result.get("section") or "unknown")
    flags = set(result_quality_flags(result))
    if section in PREFERRED_EVIDENCE_SECTIONS:
        score += 6.0
    if section in NOISY_SECTIONS:
        score -= 6.0
    score -= 4.0 * len(flags & NOISY_FLAGS)
    return score


def result_quality_flags(result: dict) -> list[str]:
    flags = normalize_string_list(result.get("quality_flags"))
    numeric_fields = ["score", "rrf_score", "paper_rrf_score", "paper_relevance_score"]
    if any(
        result.get(field) is not None
        and result.get(field) != ""
        and not is_finite_number(result.get(field))
        for field in numeric_fields
    ):
        flags = [*flags, "invalid_numeric_score"]
    return list(dict.fromkeys(flags))


def format_evidence_pack_markdown(pack: dict) -> str:
    lines = [
        "# Evidence Pack",
        "",
        f"Question: {pack.get('question', '')}",
        "",
        f"- Chunks: {pack.get('n_chunks', 0)}",
        f"- Unique papers: {pack.get('n_unique_papers', 0)}",
        "",
    ]
    grade = pack.get("grade") or {}
    if grade:
        lines.extend(
            [
                "## Grade",
                "",
                f"- Grade: {grade.get('grade', 'unknown')}",
                f"- Score: {grade.get('score', 0)}",
                f"- Reasons: {', '.join(grade.get('reasons', []))}",
                "",
            ]
        )
    trace_certificate = pack.get("trace_sufficiency_certificate") or {}
    if trace_certificate:
        lines.extend(
            [
                "## TRACE Evidence Sufficiency",
                "",
                f"- Grade: {trace_certificate.get('evidence_sufficiency', 'unknown')}",
                f"- Facet coverage: {trace_certificate.get('facet_coverage', 0)}",
                f"- Effective independent evidence: {trace_certificate.get('effective_independent_evidence', 0)}",
                f"- Independent evidence ratio: {trace_certificate.get('independent_evidence_ratio', 0)}",
                f"- Evidence inflation factor: {trace_certificate.get('evidence_inflation_factor', 0)}",
                f"- Evidence lineages: {trace_certificate.get('evidence_lineage_count', 0)}",
                f"- Allowed claim strength: {trace_certificate.get('allowed_claim_strength', 'insufficient_evidence')}",
                f"- Unresolved facets: {', '.join(trace_certificate.get('unresolved_facets', [])) or 'none'}",
                "",
            ]
        )
    robust_lineage = pack.get("trace_robust_lineage") or {}
    if robust_lineage:
        certifying_evidence = (
            pack.get("trace_certifying_evidence")
            or pack.get("trace_certification_core")
            or {}
        )
        current_strategy = (
            certifying_evidence.get("strategy") == "full_selected_set_v2"
        )
        certifying_count = certifying_evidence.get(
            "certifying_paper_count",
            certifying_evidence.get("certification_paper_count", 0),
        )
        effective = robust_lineage.get("effective_evidence") or {}
        pessimistic = robust_lineage.get("pessimistic_flow") or {}
        lines.extend(
            [
                "## Robust Lineage Certificate",
                "",
                f"- Status: {robust_lineage.get('status', 'unknown')}",
                (
                    "- Effective evidence interval: "
                    f"[{effective.get('lower', 0)}, {effective.get('upper', 0)}]"
                ),
                (
                    "- Lineage count interval: "
                    f"[{robust_lineage.get('lineage_count_lower', 0)}, "
                    f"{robust_lineage.get('lineage_count_upper', 0)}]"
                ),
                (
                    "- Pessimistic unresolved facets: "
                    f"{', '.join(pessimistic.get('unresolved_facets', [])) or 'none'}"
                ),
                (
                    "- Missing / uncertain lineage pairs: "
                    f"{robust_lineage.get('missing_pair_count', 0)} / "
                    f"{robust_lineage.get('uncertain_pair_count', 0)}"
                ),
                (
                    "- Exact for declared uncertainty: "
                    f"{robust_lineage.get('exact_for_declared_lineage_uncertainty', False)}"
                ),
                (
                    "- Certifying evidence / selected context reports: "
                    f"{certifying_count} / "
                    f"{certifying_evidence.get('context_paper_count', 0)}"
                    if current_strategy
                    else "- Certification core / synthesis context reports: "
                    f"{certifying_evidence.get('certification_paper_count', 0)} / "
                    f"{certifying_evidence.get('context_paper_count', 0)}"
                ),
                (
                    "- Certifying-evidence paper IDs: "
                    f"{', '.join(certifying_evidence.get('paper_ids', [])) or 'none'}"
                    if current_strategy
                    else "- Certification-core paper IDs: "
                    f"{', '.join(certifying_evidence.get('paper_ids', [])) or 'none'}"
                ),
                "",
            ]
        )
    for chunk in pack.get("chunks", []):
        flags = ";".join(chunk.get("quality_flags") or []) or "none"
        lines.extend(
            [
                f"## {chunk.get('evidence_id')}: {chunk.get('citation_label')}",
                "",
                f"- Paper ID: {chunk.get('paper_id', '')}",
                f"- Bib key: {chunk.get('bib_key', '')}",
                f"- Title: {chunk.get('title', '')}",
                f"- Journal: {chunk.get('journal', '')}",
                f"- DOI: {chunk.get('doi', '')}",
                f"- Section: {chunk.get('section', 'unknown')} (confidence={chunk.get('section_confidence', '')}, weight={chunk.get('section_weight', '')})",
                f"- Score: {chunk.get('score', '')}",
                f"- Selection reason: {chunk.get('selection_reason', 'selected')}",
                f"- Certification role: {chunk.get('certification_role', 'not_applicable')}",
                f"- Quality flags: {flags}",
                "",
                "```text",
                str(chunk.get("text", "")),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def citation_label(result: dict) -> str:
    citation_short = str(result.get("citation_short") or "").strip()
    author = str(result.get("author") or result.get("paper_id") or "Unknown").strip()
    year = str(result.get("year") or "").strip()
    section = str(result.get("section") or "").strip()
    pages = page_range(result.get("page_start"), result.get("page_end"))
    author_year = citation_short or " ".join(part for part in [author, year] if part)
    section_part = f", {section.replace('_', ' ').title()}" if section and section != "unknown" else ""
    page_part = f", pages {pages}" if pages != "?" else ""
    return f"[{author_year}{section_part}{page_part}]"


def page_range(page_start: object, page_end: object) -> str:
    if page_start in ("", None) and page_end in ("", None):
        return "?"
    if page_start == page_end:
        return str(page_start)
    return f"{page_start}-{page_end}"


def truncate_text(text: str, max_chars: int) -> str:
    clean = (text or "").strip()
    if max_chars <= 0 or len(clean) <= max_chars:
        return clean
    return clean[: max(0, max_chars - 20)].rstrip() + "\n[truncated]"
