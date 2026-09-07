from __future__ import annotations

import re
from collections import Counter

from originrag.retrieval.keyword import (
    COMMON_TERMS,
    contains_normalized_term,
    normalize_text,
)


EVIDENCE_SECTIONS = {"results", "discussion", "abstract", "methods", "data_availability"}
PROBLEM_SECTIONS = {"references", "table", "figure_legend", "supplement"}
PROBLEM_FLAGS = {
    "reference_like",
    "table_like",
    "figure_like",
    "supplement_like",
    "low_text_quality",
}
DIRECT_REPORT_SECTIONS = {"results", "abstract"}
DIRECT_REPORT_ATTRIBUTION_SECTIONS = {"discussion", "conclusion", "unknown"}
DIRECT_REPORT_SOURCE_TYPES = {"primary_research", "reanalysis_secondary_data"}
DIRECT_REPORT_BLOCK_FLAGS = {
    "reference_like",
    "table_like",
    "supplement_like",
    "low_text_quality",
}
DIRECT_REPORT_BLOCK_QUESTION_TERMS = {
    "agreement",
    "causal",
    "causality",
    "cause",
    "causes",
    "consensus",
    "core",
    "independent",
    "replicated",
    "replication",
    "universal",
}
DIRECT_REPORT_BLOCK_QUESTION_PREFIXES = (
    "agree",
    "bestimm",
    "causal",
    "determin",
    "driv",
    "independ",
    "kausal",
    "konsens",
    "mediat",
    "replic",
    "repliz",
    "treib",
    "unabhang",
    "ubereinstimm",
    "vermittel",
    "verursach",
)
DIRECT_REPORT_BLOCK_QUESTION_PATTERNS = (
    r"\b(?:all|every) (?:retrieved )?(?:studies|papers|reports)\b",
    r"\b(?:alle|sämtliche|samtliche) (?:studien|arbeiten|berichte)\b",
    r"\bacross (?:all )?(?:studies|papers|reports|cohorts|datasets)\b",
    r"\b(?:agreement|concordance|consistency) (?:among|between|across)\b",
    r"\b(?:agree|agreement|consistent|consistency) across\b",
)
DIRECT_REPORT_OWN_RESULT_PATTERNS = (
    r"\bwe (?:find|found|show|showed|observe|observed|identify|identified|"
    r"detect|detected|demonstrate|demonstrated|present|report|prove|proved)\b",
    r"\bwe (?:re-?clustered|clustered|conducted|performed|analysed|analyzed|"
    r"classified|grouped|quantified)\b",
    r"\bour (?:study|analysis|data|results|findings) "
    r"(?:show|shows|showed|indicate|indicates|demonstrate|demonstrates|revealed)\b",
    r"\b(?:in this study|here,? we)\b",
)
DIRECT_REPORT_PRIOR_CLAIM_PATTERNS = (
    r"\b(?:previous|prior|earlier) (?:study|studies|work|reports?)\b",
    r"\b(?:has|have|had) been (?:reported|shown|described|associated)\b",
    r"\baccording to\b",
    r"\b(?:others|other studies) (?:found|showed|reported|described)\b",
)
DIRECT_REPORT_CITATION_PATTERNS = (
    r"\([A-Z][A-Za-z'’-]+(?: et al\.)?,? \d{4}[a-z]?\)",
    r"\[(?:\d+\s*(?:,|-)\s*)*\d+\]",
)
MIN_JOINT_QUESTION_TERM_COVERAGE = 0.5
MIN_JOINT_QUESTION_TERM_HITS = 2
MIN_COMPARISON_QUESTION_TERM_COVERAGE = 0.3
COMPARISON_SCOPES = {
    "named_report_comparison_only",
    "resolved_report_comparison_only",
}
COMPARISON_DESCRIPTOR_PATTERN = re.compile(
    r"\bcompare\s+(?P<left>[^?.:]{3,180}?\b(?:paper|study|report))\s+"
    r"(?:with|to|against|and)\s+"
    r"(?P<right>[^?.:]{3,180}?\b(?:paper|study|report))(?=[?.:,]|$)",
    flags=re.IGNORECASE,
)
COMPARISON_DESCRIPTOR_STOP_TERMS = {
    "a",
    "an",
    "and",
    "article",
    "original",
    "paper",
    "report",
    "study",
    "the",
    "with",
}
EPISTEMIC_CAUSAL_COMPARISON_PATTERN = re.compile(
    r"\b(?:does|do|did)\s+(?:either|any|one|both)\s+"
    r"(?:of\s+the\s+)?(?:papers?|studies|reports?)\b"
    r"[^?]{0,160}\b(?:demonstrate|establish|prove|show)\b"
    r"[^?]{0,200}\b(?:caus(?:e|es|ed|al|ality)|driv(?:e|es|en)|lead(?:s)?\s+to)\b",
    flags=re.IGNORECASE,
)
QUESTION_STOP_TERMS = COMMON_TERMS | {
    "according",
    "about",
    "answer",
    "assay",
    "assays",
    "associated",
    "beantworten",
    "berichtet",
    "between",
    "background",
    "biological",
    "beschreiben",
    "does",
    "compare",
    "direction",
    "directions",
    "disease",
    "evidence",
    "experiment",
    "experiments",
    "erklart",
    "erklaren",
    "find",
    "finding",
    "findings",
    "finden",
    "forschung",
    "frage",
    "gaps",
    "group",
    "groups",
    "gibt",
    "hypothesis",
    "identify",
    "ist",
    "know",
    "known",
    "link",
    "linked",
    "linking",
    "links",
    "lucken",
    "make",
    "mechanism",
    "mechanisms",
    "literature",
    "paper",
    "papers",
    "please",
    "positive",
    "positiv",
    "positiven",
    "provide",
    "research",
    "report",
    "reported",
    "result",
    "results",
    "rapportent",
    "reporte",
    "reportent",
    "several",
    "role",
    "show",
    "study",
    "studies",
    "states",
    "support",
    "supported",
    "supports",
    "synthesize",
    "testable",
    "they",
    "welche",
    "welcher",
    "welches",
    "what",
    "want",
    "which",
    "warum",
    "wieso",
    "zusammenhang",


    "aber",
    "als",
    "contre",
    "cual",
    "cuales",
    "dans",
    "das",
    "dass",
    "der",
    "die",
    "ein",
    "eine",
    "einen",
    "einem",
    "einer",
    "eines",
    "entre",
    "est",
    "fur",
    "gegen",
    "hay",
    "kausal",
    "kausale",
    "kausalen",
    "kausaler",
    "montrent",
    "montre",
    "oder",
    "pour",
    "quelle",
    "quelles",
    "quels",
    "sind",
    "sont",
    "spricht",
    "und",
    "was",
    "were",
    "werden",
    "wird",
    "zwischen",


    "case",
    "contrasting",
    "discussion",
    "each",
    "happened",
    "into",
    "input",
    "keep",
    "main",
    "method",
    "methods",
    "numerical",
    "never",
    "occurs",
    "publication",
    "status",
    "target",
    "separate",
    "that",
    "turning",
    "without",
}
SHORT_SCIENTIFIC_TERMS = {"age", "sex", "var", "vsa"}




QUESTION_TERM_PREFIX_EXPANSIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("makrophagensignatur", ("macrophage", "signature")),
    ("macrophagensignatur", ("macrophage", "signature")),
    ("macrofagosignatur", ("macrophage", "signature")),
    ("zerebral", ("cerebral",)),
    ("cerebral", ("cerebral",)),
    ("bindend", ("binding",)),
    ("schwer", ("severe",)),
    ("sever", ("severe",)),
    ("makrophag", ("macrophage",)),
    ("macrofag", ("macrophage",)),
    ("signatur", ("signature",)),
)


def grade_evidence_pack(
    pack: dict,
    *,
    expected_terms: list[str] | None = None,
    preferred_sections: set[str] | None = None,
    question: str | None = None,
) -> dict:
    """Grade whether an evidence pack is good enough for scientific synthesis."""
    chunks = list(pack.get("chunks") or [])
    expected_terms = expected_terms or []
    preferred_sections = preferred_sections or EVIDENCE_SECTIONS
    question = str(pack.get("question") or "") if question is None else question
    question_terms = question_relevance_terms(question)

    if not chunks:
        return {
            "grade": "bad",
            "score": 0,
            "reasons": ["no_evidence_chunks"],
            "metrics": empty_metrics(expected_terms, question_terms),
            "bounded_direct_report": bounded_direct_report_evidence(
                pack, question_terms=question_terms
            ),
            "recommendations": [
                "Run retrieval with broader query expansion.",
                "Check whether local chunks are available.",
            ],
        }

    n_chunks = len(chunks)
    paper_counts = Counter(
        str(chunk.get("paper_id") or "") for chunk in chunks if chunk.get("paper_id")
    )
    section_counts = Counter(str(chunk.get("section") or "unknown") for chunk in chunks)
    n_unique_papers = len(paper_counts)
    max_chunks_one_paper = max(paper_counts.values(), default=0)
    dominant_paper_fraction = max_chunks_one_paper / max(n_chunks, 1)
    evidence_section_fraction = sum(
        section_counts.get(section, 0) for section in preferred_sections
    ) / max(n_chunks, 1)
    problem_section_fraction = sum(
        section_counts.get(section, 0) for section in PROBLEM_SECTIONS
    ) / max(n_chunks, 1)
    flagged_chunks = [
        chunk for chunk in chunks if set(chunk.get("quality_flags") or []) & PROBLEM_FLAGS
    ]
    flagged_fraction = len(flagged_chunks) / max(n_chunks, 1)
    doi_fraction = sum(1 for chunk in chunks if str(chunk.get("doi") or "").strip()) / max(
        n_chunks, 1
    )
    expected_hits = expected_term_hits(expected_terms, chunks)
    expected_term_coverage = (
        len(expected_hits) / max(len(expected_terms), 1) if expected_terms else None
    )
    explicit_report = explicit_report_reference(question)
    named_comparison = named_report_references(question)
    named_multi_report = len(named_comparison) >= 2
    singular_report = (
        explicit_report
        or (named_comparison[0] if len(named_comparison) == 1 else None)
    )
    target_chunks = chunks_matching_explicit_report(chunks, singular_report)
    named_anchor_chunks = [
        chunk for chunk in chunks if chunk.get("explicit_named_study_anchor")
    ]
    bounded_direct_report = bounded_direct_report_evidence(
        pack,
        question_terms=question_terms,
    )
    bounded_comparison = bool(
        bounded_direct_report.get("supported")
        and bounded_direct_report.get("scope") in COMPARISON_SCOPES
    )
    bounded_comparison_ids = {
        str(item.get("evidence_id") or "")
        for item in bounded_direct_report.get("evidence", [])
        if str(item.get("evidence_id") or "")
    }
    question_scope_chunks = (
        [
            chunk
            for chunk in chunks
            if str(chunk.get("evidence_id") or "") in bounded_comparison_ids
        ]
        if bounded_comparison
        else named_anchor_chunks
        if named_multi_report
        else (target_chunks if singular_report else chunks)
    )
    question_hits, relevant_chunk_fraction = question_relevance(
        question_terms, question_scope_chunks
    )
    question_term_coverage = (
        len(question_hits) / max(len(question_terms), 1) if question_terms else None
    )
    joint_question_relevance = (
        question_comparison_relevance(
            question_terms,
            question_scope_chunks,
            include_title=True,
        )
        if bounded_comparison
        else question_joint_relevance(
            question_terms,
            question_scope_chunks,
            include_title=not bool(singular_report or named_comparison),
        )
    )

    score = 100.0
    reasons: list[str] = []
    recommendations: list[str] = []

    if n_chunks < 4:
        score -= 20
        reasons.append("too_few_chunks")
        recommendations.append("Retrieve more candidate chunks before synthesis.")
    if n_unique_papers < min(4, n_chunks):
        score -= 16
        reasons.append("low_paper_diversity")
        recommendations.append("Increase paper diversity or reduce max chunks per paper.")
    if dominant_paper_fraction > 0.5:
        score -= 14
        reasons.append("dominant_single_paper")
        recommendations.append("Use paper-level diversity before final evidence selection.")
    if evidence_section_fraction < 0.5:
        score -= 14
        reasons.append("low_evidence_section_fraction")
        recommendations.append(
            "Prefer Results, Discussion, Abstract, Methods, and Data Availability sections."
        )
    if problem_section_fraction > 0.25:
        score -= 18
        reasons.append("too_many_problem_sections")
        recommendations.append(
            "Exclude references, tables, figure legends, and supplement-heavy chunks."
        )
    if flagged_fraction > 0.25:
        score -= 12
        reasons.append("too_many_quality_flags")
        recommendations.append("Filter low-quality OCR/table/reference-like chunks.")
    if doi_fraction < 0.8:
        score -= 8
        reasons.append("low_citation_metadata_coverage")
        recommendations.append("Refresh DOI metadata before relying on citation output.")
    if expected_terms and (expected_term_coverage or 0.0) < 0.5:
        score -= 18
        reasons.append("low_expected_term_coverage")
        recommendations.append("Add query expansion terms or inspect benchmark expectations.")
    if question_terms:
        if (question_term_coverage or 0.0) < 0.25:
            score -= 45
            reasons.append("very_low_question_term_coverage")
            recommendations.append(
                "Retrieve evidence that directly names the question's key concepts."
            )
        elif (question_term_coverage or 0.0) < 0.5:
            score -= 30
            reasons.append("low_question_term_coverage")
            recommendations.append("Broaden retrieval while retaining the question's key concepts.")
        elif (question_term_coverage or 0.0) < 0.75:
            score -= 15
            reasons.append("partial_question_term_coverage")
            recommendations.append("Add evidence for the question concepts that are still missing.")

        if relevant_chunk_fraction < 0.25:
            score -= 25
            reasons.append("very_low_question_relevant_chunk_fraction")
            recommendations.append("Remove off-topic chunks before synthesis.")
        elif relevant_chunk_fraction < 0.5:
            score -= 20
            reasons.append("low_question_relevant_chunk_fraction")
            recommendations.append("Replace weakly related chunks with direct evidence.")
        elif relevant_chunk_fraction < 0.75:
            score -= 10
            reasons.append("partial_question_relevant_chunk_fraction")
            recommendations.append(
                "Increase the share of chunks that address the question directly."
            )

        if not joint_question_relevance["supported"]:




            score -= 45
            reasons.append("fragmented_question_term_coverage")
            recommendations.append(
                "Retrieve at least one passage that jointly addresses the question's "
                "central concepts."
            )

    if (
        singular_report
        and not named_multi_report
        and not bounded_direct_report["supported"]
    ):




        score = min(score, 59.0)
        reasons.append("named_report_direct_passage_absent")
        recommendations.append(
            "Retrieve a direct Results or Abstract passage from the named report."
        )
    elif (
        singular_report
        and not named_multi_report
        and bounded_direct_report["supported"]
        and joint_question_relevance["max_term_coverage"] < 0.75
    ):




        score = min(score, 59.0)
        reasons.append("named_report_partial_direct_passage")
        recommendations.append(
            "Report only the named study details present in its direct passage."
        )

    score = round(max(0.0, min(100.0, score)), 1)
    grade = grade_from_score(score)
    if not reasons:
        reasons.append("evidence_pack_meets_current_thresholds")
    if not recommendations:
        recommendations.append("Evidence pack is suitable for cautious synthesis.")

    return {
        "grade": grade,
        "score": score,
        "reasons": reasons,
        "metrics": {
            "n_chunks": n_chunks,
            "n_unique_papers": n_unique_papers,
            "max_chunks_one_paper": max_chunks_one_paper,
            "dominant_paper_fraction": round(dominant_paper_fraction, 3),
            "evidence_section_fraction": round(evidence_section_fraction, 3),
            "problem_section_fraction": round(problem_section_fraction, 3),
            "flagged_fraction": round(flagged_fraction, 3),
            "doi_fraction": round(doi_fraction, 3),
            "section_counts": dict(section_counts),
            "expected_terms": expected_terms,
            "expected_term_hits": expected_hits,
            "expected_term_coverage": None
            if expected_term_coverage is None
            else round(expected_term_coverage, 3),
            "question_terms": question_terms,
            "question_term_hits": question_hits,
            "question_term_coverage": (
                None if question_term_coverage is None else round(question_term_coverage, 3)
            ),
            "question_relevant_chunk_fraction": (
                None if not question_terms else round(relevant_chunk_fraction, 3)
            ),
            "max_passage_question_term_hits": joint_question_relevance[
                "max_term_hits"
            ],
            "max_passage_question_term_coverage": joint_question_relevance[
                "max_term_coverage"
            ],
            "joint_question_coverage_supported": joint_question_relevance[
                "supported"
            ],
            "comparison_question_term_coverage": joint_question_relevance.get(
                "comparison_term_coverage"
            ),
            "comparison_target_count": (
                len(question_scope_chunks) if bounded_comparison else 0
            ),
            "explicit_report_reference": singular_report,
            "explicit_report_match_count": len(target_chunks),
            "named_report_reference_count": len(named_comparison),
            "named_report_anchor_count": len(named_anchor_chunks),
        },
        "bounded_direct_report": bounded_direct_report,
        "recommendations": dedupe(recommendations),
    }


def epistemic_causal_comparison_question(question: str) -> bool:
    """Return whether a two-report question asks if causal evidence exists.

    This is intentionally narrower than any question containing ``cause``. It
    only recognizes an explicit interrogation of whether either named/resolved
    report demonstrates causality, so an affirmative causal prompt does not
    gain access to the bounded report path.
    """

    has_comparison_targets = bool(
        len(named_report_references(question)) >= 2
        or comparison_report_descriptors(question)
    )
    return bool(
        has_comparison_targets
        and EPISTEMIC_CAUSAL_COMPARISON_PATTERN.search(question or "")
    )


def comparison_report_descriptors(question: str) -> list[str]:
    """Return the two paper noun phrases in a strict comparison construction."""

    match = COMPARISON_DESCRIPTOR_PATTERN.search(question or "")
    if not match:
        return []
    descriptors = [
        " ".join(str(match.group(name) or "").split())
        for name in ("left", "right")
    ]
    return descriptors if all(descriptors) else []


def resolved_report_comparison_chunks(question: str, chunks: list[dict]) -> list[dict]:
    """Resolve two descriptive paper nicknames against prompt-visible metadata.

    Passage text is deliberately excluded from identity matching: an unrelated
    paper that merely cites a target method must not become a comparison target.
    Every descriptor needs one unique metadata winner, and both winners must be
    different clean DOI-backed direct reports.
    """

    descriptors = comparison_report_descriptors(question)
    if len(descriptors) != 2:
        return []

    candidates_by_paper: dict[str, list[dict]] = {}
    for chunk in chunks:
        paper_id = str(chunk.get("paper_id") or "")
        if not paper_id or not comparison_resolution_passage_is_eligible(chunk):
            continue
        candidates_by_paper.setdefault(paper_id, []).append(chunk)

    resolved: list[dict] = []
    used_papers: set[str] = set()
    for descriptor in descriptors:
        ranked: list[tuple[int, str, dict]] = []
        for paper_id, paper_chunks in candidates_by_paper.items():
            representative = max(
                paper_chunks,
                key=lambda chunk: comparison_resolution_chunk_priority(
                    chunk, question
                ),
            )
            score = comparison_descriptor_identity_score(descriptor, representative)
            if score:
                ranked.append((score, paper_id, representative))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        if not ranked or ranked[0][0] < 4:
            return []
        runner_up = ranked[1][0] if len(ranked) > 1 else 0
        if ranked[0][0] - runner_up < 2:
            return []
        _, paper_id, chunk = ranked[0]
        if paper_id in used_papers:
            return []
        used_papers.add(paper_id)
        resolved.append(chunk)
    return resolved if len(resolved) == 2 else []


def comparison_descriptor_identity_score(descriptor: str, chunk: dict) -> int:
    """Score only author/title identity, including spelled-out acronym aliases."""

    normalized_descriptor = normalize_text(descriptor)
    descriptor_dois = {
        match.lower().rstrip(".,;)")
        for match in re.findall(r"10\.\d{4,9}/[^\s]+", descriptor or "", re.I)
    }
    chunk_doi = str(chunk.get("doi") or "").lower().strip()
    if descriptor_dois and chunk_doi in descriptor_dois:
        return 100

    descriptor_tokens = [
        token
        for token in normalized_descriptor.split()
        if len(token) >= 3 and token not in COMPARISON_DESCRIPTOR_STOP_TERMS
    ]
    identity = normalize_text(
        " ".join(
            str(chunk.get(field) or "")
            for field in ("author", "title", "citation_short", "citation_label")
        )
    )
    identity_tokens = set(identity.split())
    token_hits = {token for token in descriptor_tokens if token in identity_tokens}
    acronym = "".join(token[0] for token in descriptor_tokens)
    acronym_hit = bool(2 <= len(acronym) <= 8 and acronym in identity_tokens)
    if len(token_hits) < 2 and not acronym_hit:
        return 0
    return 2 * len(token_hits) + (8 if acronym_hit else 0)


def comparison_resolution_passage_is_eligible(chunk: dict) -> bool:
    flags = set(chunk.get("quality_flags") or [])
    source_type = str(
        (chunk.get("source_type_assessment") or {}).get("source_type") or ""
    )
    narrative_words = len(normalize_text(str(chunk.get("text") or "")).split())
    return bool(
        str(chunk.get("doi") or "").strip()
        and not flags.intersection(DIRECT_REPORT_BLOCK_FLAGS)
        and source_type in DIRECT_REPORT_SOURCE_TYPES | {"uncertain"}
        and narrative_words >= 30
        and direct_report_passage_is_eligible(chunk)
    )


def comparison_resolution_chunk_priority(chunk: dict, question: str) -> tuple[int, int, int]:
    section = str(chunk.get("section") or "unknown").lower()
    section_score = 2 if section in DIRECT_REPORT_SECTIONS else 1
    searchable = normalize_text(
        " ".join(str(chunk.get(field) or "") for field in ("title", "text"))
    )
    term_hits = sum(
        contains_normalized_term(searchable, term)
        for term in question_relevance_terms(question)
    )
    narrative_words = len(normalize_text(str(chunk.get("text") or "")).split())
    return section_score, term_hits, narrative_words


def bounded_direct_report_evidence(
    pack: dict, *, question_terms: list[str] | None = None
) -> dict:
    """Identify clean prompt-visible passages fit only for a bounded paper report."""

    chunks = list(pack.get("chunks") or [])
    terms = question_terms or question_relevance_terms(str(pack.get("question") or ""))
    raw_question = str(pack.get("question") or "")
    named_references = named_report_references(raw_question)
    named_comparison = len(named_references) >= 2
    explicit_report = explicit_report_reference(raw_question)
    descriptor_chunks = (
        resolved_report_comparison_chunks(raw_question, chunks)
        if not named_comparison and explicit_report is None
        else []
    )
    descriptor_comparison = len(descriptor_chunks) == 2
    comparison = named_comparison or descriptor_comparison
    singular_report = (
        explicit_report
        or (named_references[0] if len(named_references) == 1 else None)
    )
    eligible_chunks = (
        [
            chunk
            for chunk in chunks
            if chunk.get("explicit_named_study_anchor")
            or chunk.get("explicit_named_study_support")
        ]
        if named_comparison
        else descriptor_chunks
        if descriptor_comparison
        else (
            chunks_matching_explicit_report(chunks, singular_report)
            if singular_report
            else chunks
        )
    )
    normalized_question_text = normalize_text(raw_question)
    normalized_question = set(normalized_question_text.split())
    blocked_scope = direct_report_question_scope_is_blocked(
        normalized_question,
        normalized_question_text,
        allow_reported_replication=named_comparison,
        allow_epistemic_causal_interrogation=(
            comparison and epistemic_causal_comparison_question(raw_question)
        ),
    )
    direct: list[dict] = []
    exact_named_singular = bool(
        singular_report
        and not named_comparison
        and any(chunk.get("explicit_named_study_anchor") for chunk in eligible_chunks)
    )
    if terms and not blocked_scope:
        direct = [
            item
            for chunk in eligible_chunks
            if (
                item := direct_report_evidence_item(
                    chunk,
                    terms,
                    require_own_result=bool(
                        singular_report or named_comparison
                    ),
                    allow_uncertain_source=(
                        comparison
                        or bool(
                            singular_report
                            and chunk.get("explicit_named_study_anchor")
                        )
                    ),
                    allow_title_support=bool(singular_report or comparison),
                    minimum_text_hits=(
                        1
                        if comparison
                        else (2 if singular_report else min(3, len(terms)))
                    ),
                    minimum_combined_hits=2 if comparison else min(3, len(terms)),
                    minimum_combined_coverage=(
                        0.0
                        if comparison
                        else (0.2 if exact_named_singular else 0.5)
                    ),
                    minimum_narrative_words=(
                        30 if comparison else (20 if singular_report else 0)
                    ),
                )
            )
        ]
    matched_named_reports = (
        matched_named_report_evidence(named_references, direct, eligible_chunks)
        if named_comparison
        else []
    )
    supported = bool(direct)
    scope = "individual_paper_report_only"
    if named_comparison:
        aggregate_hits = {
            hit for item in matched_named_reports for hit in item["question_term_hits"]
        }
        aggregate_coverage = len(aggregate_hits) / max(len(terms), 1)
        supported = (
            len(matched_named_reports) == len(named_references)
            and aggregate_coverage >= 0.3
        )
        direct = matched_named_reports
        scope = "named_report_comparison_only"
    elif descriptor_comparison:
        direct_ids = {str(item.get("paper_id") or "") for item in direct}
        expected_ids = {
            str(chunk.get("paper_id") or "") for chunk in descriptor_chunks
        }
        aggregate_hits = {hit for item in direct for hit in item["question_term_hits"]}
        aggregate_coverage = len(aggregate_hits) / max(len(terms), 1)
        supported = bool(
            len(direct) == 2
            and direct_ids == expected_ids
            and aggregate_coverage >= MIN_COMPARISON_QUESTION_TERM_COVERAGE
        )
        scope = "resolved_report_comparison_only"
    return {
        "supported": supported,
        "scope": scope,
        "independence_certified": False,
        "blocked_question_scope": blocked_scope,
        "explicit_report_reference": singular_report,
        "explicit_report_match_count": len(eligible_chunks) if singular_report else None,
        "named_report_references": named_references,
        "named_report_match_count": len(matched_named_reports),
        "evidence": direct,
    }


def direct_report_question_scope_is_blocked(
    normalized_question: set[str],
    normalized_question_text: str,
    *,
    allow_reported_replication: bool,
    allow_epistemic_causal_interrogation: bool = False,
) -> bool:
    blocked_terms = set(DIRECT_REPORT_BLOCK_QUESTION_TERMS)
    blocked_prefixes = DIRECT_REPORT_BLOCK_QUESTION_PREFIXES
    if allow_reported_replication:
        blocked_terms.difference_update({"replicated", "replication"})
        blocked_prefixes = tuple(
            prefix
            for prefix in blocked_prefixes
            if prefix not in {"replic", "repliz"}
        )
    if allow_epistemic_causal_interrogation:
        blocked_terms.difference_update(
            {"causal", "causality", "cause", "causes", "caused"}
        )
        blocked_prefixes = tuple(
            prefix
            for prefix in blocked_prefixes
            if prefix
            not in {
                "causal",
                "driv",
                "kausal",
                "mediat",
                "vermittel",
                "verursach",
            }
        )
    return bool(
        normalized_question.intersection(blocked_terms)
        or any(term.startswith(blocked_prefixes) for term in normalized_question)
        or any(
            re.search(pattern, normalized_question_text)
            for pattern in DIRECT_REPORT_BLOCK_QUESTION_PATTERNS
        )
    )


def direct_report_evidence_item(
    chunk: dict,
    terms: list[str],
    *,
    require_own_result: bool,
    allow_uncertain_source: bool,
    allow_title_support: bool,
    minimum_text_hits: int,
    minimum_combined_hits: int,
    minimum_combined_coverage: float,
    minimum_narrative_words: int,
) -> dict | None:
    """Validate one directly reported prompt passage without title-only support."""

    flags = set(chunk.get("quality_flags") or [])
    assessment = dict(chunk.get("source_type_assessment") or {})
    source_type = str(assessment.get("source_type") or "")
    text = str(chunk.get("text") or "")
    narrative_words = len(normalize_text(text).split())
    own_result = direct_report_passage_has_own_result(chunk)
    text_searchable = normalize_text(text)
    combined_searchable = normalize_text(
        " ".join((str(chunk.get("title") or ""), text))
        if allow_title_support
        else text
    )
    text_hits = [
        term for term in terms if contains_normalized_term(text_searchable, term)
    ]
    combined_hits = [
        term for term in terms if contains_normalized_term(combined_searchable, term)
    ]
    coverage = len(combined_hits) / max(len(terms), 1)
    source_is_allowed = source_type in DIRECT_REPORT_SOURCE_TYPES or (
        allow_uncertain_source and source_type == "uncertain"
    )
    if not (
        direct_report_passage_is_eligible(chunk)
        and (not require_own_result or own_result)
        and not flags.intersection(DIRECT_REPORT_BLOCK_FLAGS)
        and ("figure_like" not in flags or narrative_words >= 40)
        and narrative_words >= minimum_narrative_words
        and str(chunk.get("doi") or "").strip()
        and source_is_allowed
        and len(text_hits) >= minimum_text_hits
        and len(combined_hits) >= minimum_combined_hits
        and coverage >= minimum_combined_coverage
    ):
        return None
    surname_tokens = normalize_text(
        str(chunk.get("named_study_surname") or chunk.get("author") or "")
    ).split()
    return {
        "evidence_id": str(chunk.get("evidence_id") or ""),
        "paper_id": str(chunk.get("paper_id") or ""),
        "explicit_named_study_anchor": bool(
            chunk.get("explicit_named_study_anchor")
        ),
        "explicit_named_study_support": bool(
            chunk.get("explicit_named_study_support")
        ),
        "named_study_surname": surname_tokens[0] if surname_tokens else "",
        "named_study_year": str(
            chunk.get("named_study_year") or chunk.get("year") or ""
        ),
        "question_term_hits": combined_hits,
        "question_text_term_hits": text_hits,
        "question_term_coverage": round(coverage, 3),
    }


def matched_named_report_evidence(
    references: list[dict[str, str]],
    direct: list[dict],
    eligible_chunks: list[dict],
) -> list[dict]:
    """Return one unambiguous direct item for every explicitly named report."""

    eligible_ids = {
        str(chunk.get("paper_id") or ""): chunk for chunk in eligible_chunks
    }
    matched: list[dict] = []
    used_papers: set[str] = set()
    for reference in references:
        candidates_by_paper: dict[str, list[dict]] = {}
        for item in direct:
            paper_id = str(item.get("paper_id") or "")
            chunk = eligible_ids.get(paper_id, {})
            surname = normalize_text(
                str(
                    chunk.get("named_study_surname")
                    or item.get("named_study_surname")
                    or chunk.get("author")
                    or ""
                )
            ).split()
            year = str(
                chunk.get("named_study_year")
                or item.get("named_study_year")
                or chunk.get("year")
                or ""
            )
            if not surname or reference["author"] != surname[0]:
                continue
            if reference["year"] and reference["year"] != year:
                continue
            if paper_id in used_papers:
                continue
            candidates_by_paper.setdefault(paper_id, []).append(item)
        if len(candidates_by_paper) != 1:
            return []
        paper_id, candidates = next(iter(candidates_by_paper.items()))
        selected = max(
            candidates,
            key=lambda item: (
                len(item.get("question_text_term_hits") or []),
                len(item.get("question_term_hits") or []),
                float(item.get("question_term_coverage") or 0.0),
                bool(item.get("explicit_named_study_support")),
            ),
        )
        matched.append(selected)
        used_papers.add(paper_id)
    return matched


def direct_report_passage_is_eligible(chunk: dict) -> bool:
    """Require direct section provenance or an unambiguous own-result attribution."""

    section = str(chunk.get("section") or "unknown").lower()
    text = str(chunk.get("text") or "")
    own_result = direct_report_passage_has_own_result(chunk)
    prior_claim = any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in DIRECT_REPORT_PRIOR_CLAIM_PATTERNS
    )
    has_citation = any(
        re.search(pattern, text) for pattern in DIRECT_REPORT_CITATION_PATTERNS
    )
    if section in DIRECT_REPORT_SECTIONS:




        return own_result or (not prior_claim and not has_citation)
    if prior_claim:
        return False
    return bool(
        section in DIRECT_REPORT_ATTRIBUTION_SECTIONS
        and own_result
        and not has_citation
    )


def direct_report_passage_has_own_result(chunk: dict) -> bool:
    text = str(chunk.get("text") or "")
    return any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in DIRECT_REPORT_OWN_RESULT_PATTERNS
    )


def format_evidence_pack_grade_markdown(grade: dict) -> str:
    metrics = dict(grade.get("metrics") or {})
    lines = [
        "# Evidence Pack Grade",
        "",
        f"- Grade: {grade.get('grade', 'unknown')}",
        f"- Score: {grade.get('score', 0)}",
        f"- Chunks: {metrics.get('n_chunks', 0)}",
        f"- Unique papers: {metrics.get('n_unique_papers', 0)}",
        f"- Dominant paper fraction: {metrics.get('dominant_paper_fraction', 0)}",
        f"- Evidence-section fraction: {metrics.get('evidence_section_fraction', 0)}",
        f"- Problem-section fraction: {metrics.get('problem_section_fraction', 0)}",
        f"- Flagged fraction: {metrics.get('flagged_fraction', 0)}",
        f"- DOI fraction: {metrics.get('doi_fraction', 0)}",
        f"- Question-term coverage: {metrics.get('question_term_coverage', 'n/a')}",
        (
            "- Question-relevant chunk fraction: "
            f"{metrics.get('question_relevant_chunk_fraction', 'n/a')}"
        ),
        (
            "- Maximum single-passage question-term coverage: "
            f"{metrics.get('max_passage_question_term_coverage', 'n/a')}"
        ),
        "",
        "Reasons:",
        *[f"- {reason}" for reason in grade.get("reasons", [])],
        "",
        "Recommendations:",
        *[f"- {item}" for item in grade.get("recommendations", [])],
        "",
    ]
    return "\n".join(lines)


def expected_term_hits(expected_terms: list[str], chunks: list[dict]) -> list[str]:
    if not expected_terms:
        return []
    haystack = normalize_text(
        " ".join(
            str(chunk.get(field) or "")
            for chunk in chunks
            for field in ["title", "journal", "section", "text"]
        )
    )
    hits = []
    for term in expected_terms:
        normalized = normalize_text(term)
        if normalized and contains_normalized_term(haystack, normalized):
            hits.append(term)
    return hits


def question_relevance_terms(question: str) -> list[str]:
    """Extract specific question concepts, excluding generic task wording."""
    terms = []
    question_without_report_reference = strip_named_report_attributions(
        strip_explicit_report_references(question)
    )
    for term in normalize_text(question_without_report_reference).split():
        if term in QUESTION_STOP_TERMS:
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", term):
            continue
        expansions = next(
            (
                canonical
                for prefix, canonical in QUESTION_TERM_PREFIX_EXPANSIONS
                if term.startswith(prefix)
            ),
            (),
        )
        if expansions:
            terms.extend(expansions)
        elif len(term) >= 4 or term in SHORT_SCIENTIFIC_TERMS:
            terms.append(term)
    return list(dict.fromkeys(terms))


EXPLICIT_REPORT_REFERENCE_PATTERN = re.compile(
    r"\b(?:according\s+to|in)\s+"
    r"(?P<author>[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+)"
    r"(?:\s+et\s+al\.?)?\s*"
    r"(?P<citation>"
    r"\([^)]*\b(?P<paren_year>(?:19|20)\d{2})\b[^)]*\)"
    r"|,\s*(?:[^,?]{0,60},\s*)?(?P<comma_year>(?:19|20)\d{2})\b"
    r")",
    flags=re.IGNORECASE,
)

NAMED_REPORT_ATTRIBUTION_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<author>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]{1,40})"
    r"\s+et\s+al\.?"
    r"(?P<citation>\s*(?:"
    r"\([^)]*?\b(?P<paren_year>(?:19|20)\d{2})\b[^)]*\)"
    r"|,?\s*(?P<plain_year>(?:19|20)\d{2})\b"
    r"))?",
)


def explicit_report_reference(question: str) -> dict[str, str] | None:
    """Return a structurally explicit author-year report reference, if present."""

    match = EXPLICIT_REPORT_REFERENCE_PATTERN.search(question)
    if not match:
        return None
    author = normalize_text(match.group("author")).strip()
    year = str(match.group("paren_year") or match.group("comma_year") or "")
    if not author or not year:
        return None
    return {"author": author, "year": year}


def strip_explicit_report_references(question: str) -> str:
    """Remove bibliographic attribution from scientific coverage terms."""

    return EXPLICIT_REPORT_REFERENCE_PATTERN.sub(" ", question)


def named_report_references(question: str) -> list[dict[str, str]]:
    """Extract explicit surname/year report mentions without resolving metadata."""

    references: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in NAMED_REPORT_ATTRIBUTION_PATTERN.finditer(question or ""):
        author = normalize_text(match.group("author")).strip()
        year = str(match.group("paren_year") or match.group("plain_year") or "")
        key = (author, year)
        if not author or key in seen:
            continue
        seen.add(key)
        references.append({"author": author, "year": year})
    return references


def strip_named_report_attributions(question: str) -> str:
    """Remove explicit author-year identity tokens from scientific coverage terms."""

    return NAMED_REPORT_ATTRIBUTION_PATTERN.sub(" ", question or "")


def chunks_matching_explicit_report(
    chunks: list[dict], reference: dict[str, str] | None
) -> list[dict]:
    """Restrict a named-report question to the matching author-year document."""

    if not reference:
        return []
    target_author = str(reference.get("author") or "")
    target_year = str(reference.get("year") or "")
    anchored = []
    for chunk in chunks:
        if not chunk.get("explicit_named_study_anchor"):
            continue
        author_text = normalize_text(
            " ".join(
                str(chunk.get(field) or "")
                for field in (
                    "named_study_surname",
                    "author",
                    "citation_label",
                    "citation_short",
                )
            )
        )
        year = str(
            chunk.get("named_study_year") or chunk.get("year") or ""
        ).strip()
        if (
            target_author
            and target_author in author_text.split()
            and (not target_year or year == target_year)
        ):
            anchored.append(chunk)
    if anchored:
        return anchored
    if not target_year:



        return []

    matches = []
    for chunk in chunks:
        author_text = normalize_text(
            " ".join(
                str(chunk.get(field) or "")
                for field in ("author", "citation_label", "citation_short")
            )
        )
        year = str(chunk.get("year") or "").strip()
        if target_author in author_text.split() and year == target_year:
            matches.append(chunk)
    return matches


def question_relevance(question_terms: list[str], chunks: list[dict]) -> tuple[list[str], float]:
    """Return globally covered question terms and fraction of chunks with a direct match."""
    if not question_terms or not chunks:
        return [], 0.0

    hits: list[str] = []
    relevant_chunks = 0
    for chunk in chunks:
        searchable = normalize_text(
            " ".join(str(chunk.get(field) or "") for field in ["title", "section", "text"])
        )
        chunk_hits = [term for term in question_terms if contains_normalized_term(searchable, term)]
        if chunk_hits:
            relevant_chunks += 1
            hits.extend(chunk_hits)
    return list(dict.fromkeys(hits)), relevant_chunks / len(chunks)


def question_joint_relevance(
    question_terms: list[str],
    chunks: list[dict],
    *,
    include_title: bool = True,
) -> dict:
    """Measure whether one prompt-visible passage jointly covers question concepts.

    This is deliberately weaker than requiring one paper to answer the complete
    question. It only prevents a pack from becoming synthesis-eligible when its
    apparent coverage is assembled one isolated term at a time across unrelated
    passages.
    """

    if not question_terms or not chunks:
        return {
            "max_term_hits": 0,
            "max_term_coverage": 0.0,
            "supported": False,
        }

    max_hits = 0
    for chunk in chunks:
        fields = ["title", "text"] if include_title else ["text"]
        searchable = normalize_text(
            " ".join(str(chunk.get(field) or "") for field in fields)
        )
        chunk_hits = sum(
            contains_normalized_term(searchable, term) for term in question_terms
        )
        max_hits = max(max_hits, chunk_hits)

    coverage = max_hits / len(question_terms)
    required_hits = min(MIN_JOINT_QUESTION_TERM_HITS, len(question_terms))
    return {
        "max_term_hits": max_hits,
        "max_term_coverage": round(coverage, 3),
        "supported": bool(
            max_hits >= required_hits
            and coverage >= MIN_JOINT_QUESTION_TERM_COVERAGE
        ),
    }


def question_comparison_relevance(
    question_terms: list[str],
    chunks: list[dict],
    *,
    include_title: bool = True,
) -> dict:
    """Measure complementary coverage across exactly two resolved reports."""

    if not question_terms or len(chunks) != 2:
        return {
            "max_term_hits": 0,
            "max_term_coverage": 0.0,
            "comparison_term_coverage": 0.0,
            "supported": False,
        }
    per_chunk_hits: list[set[str]] = []
    for chunk in chunks:
        fields = ["title", "text"] if include_title else ["text"]
        searchable = normalize_text(
            " ".join(str(chunk.get(field) or "") for field in fields)
        )
        per_chunk_hits.append(
            {
                term
                for term in question_terms
                if contains_normalized_term(searchable, term)
            }
        )
    aggregate_hits = set().union(*per_chunk_hits)
    max_hits = max((len(hits) for hits in per_chunk_hits), default=0)
    max_coverage = max_hits / len(question_terms)
    aggregate_coverage = len(aggregate_hits) / len(question_terms)
    return {
        "max_term_hits": max_hits,
        "max_term_coverage": round(max_coverage, 3),
        "comparison_term_coverage": round(aggregate_coverage, 3),
        "supported": bool(
            all(len(hits) >= MIN_JOINT_QUESTION_TERM_HITS for hits in per_chunk_hits)
            and aggregate_coverage >= MIN_COMPARISON_QUESTION_TERM_COVERAGE
        ),
    }


def grade_from_score(score: float) -> str:
    if score >= 80:
        return "good"
    if score >= 60:
        return "usable"
    if score >= 35:
        return "weak"
    return "bad"


def empty_metrics(expected_terms: list[str], question_terms: list[str] | None = None) -> dict:
    return {
        "n_chunks": 0,
        "n_unique_papers": 0,
        "max_chunks_one_paper": 0,
        "dominant_paper_fraction": 0.0,
        "evidence_section_fraction": 0.0,
        "problem_section_fraction": 0.0,
        "flagged_fraction": 0.0,
        "doi_fraction": 0.0,
        "section_counts": {},
        "expected_terms": expected_terms,
        "expected_term_hits": [],
        "expected_term_coverage": 0.0 if expected_terms else None,
        "question_terms": question_terms or [],
        "question_term_hits": [],
        "question_term_coverage": 0.0 if question_terms else None,
        "question_relevant_chunk_fraction": 0.0 if question_terms else None,
        "max_passage_question_term_hits": 0,
        "max_passage_question_term_coverage": 0.0 if question_terms else None,
        "joint_question_coverage_supported": False if question_terms else None,
        "explicit_report_reference": None,
        "explicit_report_match_count": 0,
    }


def dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
