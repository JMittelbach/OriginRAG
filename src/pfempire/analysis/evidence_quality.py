from __future__ import annotations

from collections import Counter
from statistics import mean


EVIDENCE_SECTIONS = {"results", "discussion", "abstract"}
METHOD_SECTIONS = {"methods", "data_availability"}
REFERENCE_SECTIONS = {"references"}
PROBLEM_SECTIONS = {"table", "figure_legend", "supplement"}
LOW_QUALITY_FLAGS = {"reference_like", "table_like", "figure_like", "supplement_like", "low_text_quality"}
GRADE_ORDER = {"bad": 0, "weak": 1, "usable": 2, "good": 3}


def score_evidence_pack(pack: dict, expected_sources: list[str] | None = None) -> dict:
    items = evidence_items(pack)
    expected_sources = expected_sources or []
    if not items:
        return {
            "grade": "bad",
            "metrics": empty_metrics(),
            "reasons": ["no_evidence_items"],
            "recommended_action": "need_more_sources",
            "expected_sources_found": [],
            "missing_expected_sources": expected_sources,
        }

    paper_counts = Counter(str(item.get("paper_id") or "") for item in items if item.get("paper_id"))
    section_counts = Counter(str(item.get("section") or "unknown") for item in items)
    scores = [safe_float(item.get("score")) for item in items if safe_float(item.get("score")) > 0]
    low_quality = [
        item
        for item in items
        if set(item.get("quality_flags") or []) & LOW_QUALITY_FLAGS
        or str(item.get("section") or "") in PROBLEM_SECTIONS | REFERENCE_SECTIONS
    ]
    n_chunks = len(items)
    n_unique_papers = len(paper_counts)
    max_chunks_from_one_paper = max(paper_counts.values(), default=0)
    fraction_from_most_common_paper = max_chunks_from_one_paper / n_chunks
    fraction_results_discussion_abstract = section_fraction(section_counts, EVIDENCE_SECTIONS, n_chunks)
    fraction_methods = section_fraction(section_counts, METHOD_SECTIONS, n_chunks)
    fraction_references = section_fraction(section_counts, REFERENCE_SECTIONS, n_chunks)
    fraction_tables_figures_supplement = section_fraction(section_counts, PROBLEM_SECTIONS, n_chunks)
    fraction_references_tables_figures_supplement = section_fraction(
        section_counts,
        REFERENCE_SECTIONS | PROBLEM_SECTIONS,
        n_chunks,
    )
    fraction_low_quality = len(low_quality) / n_chunks
    found_sources, missing_sources = expected_source_matches(expected_sources, items)

    metrics = {
        "n_chunks": n_chunks,
        "n_unique_papers": n_unique_papers,
        "max_chunks_from_one_paper": max_chunks_from_one_paper,
        "fraction_from_most_common_paper": round(fraction_from_most_common_paper, 3),
        "fraction_results_discussion_abstract": round(fraction_results_discussion_abstract, 3),
        "fraction_methods": round(fraction_methods, 3),
        "fraction_references": round(fraction_references, 3),
        "fraction_tables_figures_supplement": round(fraction_tables_figures_supplement, 3),
        "fraction_references_tables_figures_supplement": round(
            fraction_references_tables_figures_supplement,
            3,
        ),
        "fraction_low_quality": round(fraction_low_quality, 3),
        "mean_score": round(mean(scores), 3) if scores else 0.0,
        "section_counts": dict(section_counts),
        "expected_key_papers_found": found_sources,
        "expected_key_papers_missing": missing_sources,
    }
    grade, reasons = grade_from_metrics(metrics)
    return {
        "grade": grade,
        "metrics": metrics,
        "reasons": reasons,
        "recommended_action": recommended_action(grade, reasons),
        "expected_sources_found": found_sources,
        "missing_expected_sources": missing_sources,
    }


def evidence_items(pack: dict) -> list[dict]:
    if pack.get("evidence_items"):
        return list(pack.get("evidence_items") or [])
    return list(pack.get("chunks") or [])


def grade_from_metrics(metrics: dict) -> tuple[str, list[str]]:
    reasons = []
    n_unique = int(metrics["n_unique_papers"])
    dominant = float(metrics["fraction_from_most_common_paper"])
    evidence_fraction = float(metrics["fraction_results_discussion_abstract"])
    reference_fraction = float(metrics["fraction_references"])
    problem_fraction = float(metrics["fraction_tables_figures_supplement"])
    low_quality = float(metrics["fraction_low_quality"])

    if n_unique < 2:
        reasons.append("too_few_unique_papers")
    if dominant > 0.5:
        reasons.append("dominated_by_single_paper")
    if reference_fraction > 0:
        reasons.append("contains_references")
    if problem_fraction > 0.25:
        reasons.append("too_many_tables_figures_or_supplements")
    if low_quality > 0.35:
        reasons.append("too_many_low_quality_chunks")
    if evidence_fraction < 0.5:
        reasons.append("low_preferred_section_fraction")

    if (
        n_unique >= 5
        and dominant <= 0.35
        and low_quality <= 0.20
        and problem_fraction <= 0.25
        and evidence_fraction >= 0.5
    ):
        return "good", reasons or ["meets_good_thresholds"]
    if n_unique >= 3 and dominant <= 0.50 and low_quality <= 0.35 and problem_fraction <= 0.35:
        return "usable", reasons or ["meets_usable_thresholds"]
    if n_unique >= 2 and low_quality <= 0.60:
        return "weak", reasons or ["limited_but_nonempty_evidence"]
    return "bad", reasons or ["insufficient_evidence"]


def recommended_action(grade: str, reasons: list[str]) -> str:
    if grade in {"good", "usable"}:
        return "answer"
    if "too_few_unique_papers" in reasons:
        return "retry_broaden"
    if "dominated_by_single_paper" in reasons:
        return "retry_narrow"
    if "too_many_tables_figures_or_supplements" in reasons or "contains_references" in reasons:
        return "manual_review"
    return "need_more_sources"


def expected_source_matches(expected_sources: list[str], items: list[dict]) -> tuple[list[str], list[str]]:
    haystacks = [
        " ".join(
            str(item.get(field) or "")
            for field in ["author", "year", "title", "journal", "paper_id", "source_label", "citation_label"]
        ).lower()
        for item in items
    ]
    found = []
    missing = []
    for source in expected_sources:
        normalized = str(source or "").strip().lower()
        if normalized and any(normalized in haystack for haystack in haystacks):
            found.append(source)
        elif source:
            missing.append(source)
    return found, missing


def section_fraction(counts: Counter, sections: set[str], total: int) -> float:
    return sum(counts.get(section, 0) for section in sections) / max(total, 1)


def safe_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except ValueError:
        return 0.0


def empty_metrics() -> dict:
    return {
        "n_chunks": 0,
        "n_unique_papers": 0,
        "max_chunks_from_one_paper": 0,
        "fraction_from_most_common_paper": 0.0,
        "fraction_results_discussion_abstract": 0.0,
        "fraction_methods": 0.0,
        "fraction_references": 0.0,
        "fraction_tables_figures_supplement": 0.0,
        "fraction_references_tables_figures_supplement": 0.0,
        "fraction_low_quality": 0.0,
        "mean_score": 0.0,
        "section_counts": {},
        "expected_key_papers_found": [],
        "expected_key_papers_missing": [],
    }
