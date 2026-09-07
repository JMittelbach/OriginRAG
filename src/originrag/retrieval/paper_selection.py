from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence

from originrag.retrieval.keyword import contains_normalized_term, normalize_text
from originrag.trace.lineage import suggest_lineage_signals


EXPRESSION_QUERY_TERMS = (
    "expression",
    "expressed",
    "transcription",
    "transcript",
    "transcripts",
    "transcriptome",
    "transcriptomic",
    "rna seq",
    "rna sequencing",
)

CLINICAL_QUERY_TERMS = (
    "clinical phenotype",
    "clinical phenotypes",
    "clinical presentation",
    "clinical presentations",
    "clinical syndrome",
    "clinical syndromes",
    "clinical outcome",
    "clinical outcomes",
    "disease severity",
    "severe malaria",
    "cerebral malaria",
    "severe anemia",
    "severe anaemia",
    "hyperlactatemia",
    "hyperlactataemia",
    "acidosis",
    "respiratory distress",
    "uncomplicated malaria",
    "asymptomatic infection",
    "asymptomatic malaria",
)

RELATION_QUERY_TERMS = (
    "association",
    "associations",
    "associated",
    "linking",
    "linked",
    "correlation",
    "correlated",
    "compared",
    "comparison",
    "differences between",
    "differential",
    "direction",
    "versus",
)

PARASITE_QUERY_TERMS = (
    "parasite",
    "plasmodium",
    "falciparum",
    "var gene",
    "var genes",
    "pfemp1",
    "pfemp1s",
    "rifin",
    "rifins",
    "stevor",
    "stevors",
)

PARASITE_EXPRESSION_TERMS = (
    "parasite expression",
    "parasite gene expression",
    "parasite transcript",
    "parasite transcripts",
    "parasite transcription",
    "parasite transcriptome",
    "parasite transcriptomic",
    "plasmodium expression",
    "plasmodium gene expression",
    "plasmodium transcription",
    "plasmodium transcriptome",
    "falciparum expression",
    "falciparum transcription",
    "falciparum transcriptome",
    "var expression",
    "var gene expression",
    "var transcription",
    "var transcript",
    "var transcripts",
    "pfemp1 expression",
    "pfemp1 transcript",
    "pfemp1 transcripts",
    "rifin expression",
    "rifin transcript",
    "rifin transcripts",
    "stevor expression",
    "stevor transcript",
    "stevor transcripts",
    "parasite genes differentially expressed",
    "parasite differential gene expression",
)

HOST_EXPRESSION_TERMS = (
    "host expression",
    "host gene expression",
    "host genes",
    "host transcriptome",
    "host transcriptomic",
    "human expression",
    "human gene expression",
    "human genes",
    "human transcriptome",
    "human transcriptomic",
    "immune cell expression",
    "immune cell transcriptome",
    "leukocyte expression",
    "leukocyte transcriptome",
)

TECHNICAL_ASSOCIATION_TERMS = (
    "batch",
    "batch effect",
    "library batch",
    "library size",
    "read depth",
    "sequencing batch",
    "sequencing depth",
    "sequencing lane",
    "sequencing platform",
)

EXPRESSION_TERMS = (
    "expression",
    "expressed",
    "expresses",
    "expressing",
    "transcription",
    "transcriptional",
    "transcript",
    "transcripts",
    "transcriptome",
    "transcriptomic",
    "rna seq",
    "rna sequencing",
    "overexpressed",
    "underexpressed",
    "upregulated",
    "downregulated",
)

PARASITE_EXPRESSION_TITLE_CONTEXT_TERMS = (
    "var",
    "pfemp1",
    "rifin",
    "stevor",
    "parasite transcriptome",
    "plasmodium transcriptome",
    "falciparum transcriptome",
)

PARASITE_DOMAIN_SUBJECT_TERMS = (
    "domain",
    "domains",
    "domain cassette",
    "domain cassettes",
    "surface antigen",
    "surface antigens",
)

PARASITE_FEATURE_TERMS = (
    "parasite",
    "plasmodium",
    "falciparum",
    "var",
    "pfemp1",
    "pfemp1s",
    "rifin",
    "rifins",
    "stevor",
    "stevors",
)

CLINICAL_RESULT_TERMS = (
    "severe",
    "severity",
    "cerebral",
    "brain swelling",
    "anemia",
    "anaemia",
    "hyperlactatemia",
    "hyperlactataemia",
    "lactate",
    "acidosis",
    "respiratory distress",
    "uncomplicated",
    "asymptomatic",
    "clinical malaria",
    "clinical disease",
    "clinical outcome",
    "clinical outcomes",
    "clinical phenotype",
    "clinical presentation",
    "clinical presentations",
    "malaria syndrome",
    "malaria syndromes",
    "coma",
    "retinopathy",
    "mortality",
    "disease severity",
    "cm cases",
    "um cases",
    "cm isolates",
    "um isolates",
)

RESULT_RELATION_TERMS = (
    "associated with",
    "associates with",
    "association between",
    "association with",
    "differentially associated",
    "severity associated",
    "differential expression",
    "differentially expressed",
    "compared with",
    "compared to",
    "compared",
    "between severe",
    "between clinical",
    "between asymptomatic",
    "higher in",
    "lower in",
    "more highly",
    "less highly",
    "fewer",
    "greater",
    "increased",
    "decreased",
    "elevated",
    "enriched",
    "upregulated",
    "up regulation",
    "downregulated",
    "down regulation",
    "overexpressed",
    "underexpressed",
    "no difference",
    "not associated",
    "correlated with",
    "correlation between",
    "linked to",
    "reveals",
    "revealed",
    "showed",
    "shows",
    "indicator of",
    "indicators of",
    "strong indicator",
    "strong indicators",
    "with increasing",
    "risk of",
)

CURRENT_STUDY_MARKERS = (
    "we found",
    "we observed",
    "we report",
    "we show",
    "we showed",
    "we identified",
    "we demonstrate",
    "we demonstrated",
    "we analyzed",
    "we analysed",
    "we compared",
    "we measured",
    "we quantified",
    "we performed",
    "we explored",
    "here we",
    "our analysis",
    "our data",
    "our results",
    "our findings",
    "our study",
    "this study",
    "this analysis",
    "analysis revealed",
    "combined analysis",
)

BACKGROUND_MARKERS = (
    "previously",
    "previous study",
    "previous studies",
    "prior study",
    "prior studies",
    "has been shown",
    "have been shown",
    "was shown",
    "were shown",
    "reported by",
    "known to",
    "these studies",
    "other investigators",
    "in one study",
)

PASSIVE_CURRENT_RESULT_MARKERS = (
    "statistically significant",
    "statistically significantly",
    "significantly higher",
    "significantly lower",
    "overexpressed in",
    "underexpressed in",
    "increased transcript",
    "decreased transcript",
    "evidence presented shows",
)

ABSTRACT_START_RE = re.compile(r"\b(?:abstract|summary|highlights)\b", re.IGNORECASE)
ABSTRACT_END_RE = re.compile(r"\b(?:introduction|background)\b", re.IGNORECASE)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")

PUBLICATION_VERSION_SIGNAL_TYPES = frozenset(
    {"same_doi_family", "same_preprint_family", "same_publication_version"}
)


def requests_direct_clinical_parasite_expression(question: str) -> bool:
    """Detect source requests that explicitly couple parasite expression and phenotype."""

    normalized = normalize_text(question)
    return all(
        _has_any(normalized, terms)
        for terms in (
            EXPRESSION_QUERY_TERMS,
            CLINICAL_QUERY_TERMS,
            RELATION_QUERY_TERMS,
            PARASITE_QUERY_TERMS,
        )
    )


def retain_direct_clinical_expression_papers(
    question: str,
    papers: list[dict],
    document_context: Mapping[str, Sequence[dict]],
) -> list[dict]:
    """Keep reports that make a direct parasite-expression/phenotype comparison.

    This filter is deliberately narrow and only activates for an explicit source
    request that couples parasite expression with clinical phenotypes. A paper is
    retained when its title states that relation or its abstract reports the
    relation as a result of the current study. Expression and phenotype mentions
    distributed across background and mechanistic passages are insufficient.
    """

    if not requests_direct_clinical_parasite_expression(question):
        return [dict(paper) for paper in papers]

    retained: list[dict] = []
    for paper in papers:
        paper_id = str(paper.get("paper_id") or "")
        assessment = clinical_expression_directness(
            title=str(paper.get("title") or ""),
            chunks=document_context.get(paper_id, ()),
        )
        if not assessment["direct"]:
            continue
        retained.append(
            {
                **paper,
                "clinical_expression_directness": assessment,
            }
        )
    return retained


def clinical_expression_directness(*, title: str, chunks: Sequence[dict]) -> dict:
    """Assess whether a report itself links parasite expression to a phenotype."""

    if _is_direct_claim_text(title):
        return {
            "direct": True,
            "basis": "title",
            "reason": "title states a parasite-expression/clinical-phenotype relation",
        }

    abstract_text = _abstract_claim_text(chunks)
    current_scope_remaining = 0
    for sentence in _sentences(abstract_text):
        normalized = normalize_text(sentence)
        sentence_has_current_marker = _has_any(normalized, CURRENT_STUDY_MARKERS)
        sentence_has_passive_result = _has_any(
            normalized,
            PASSIVE_CURRENT_RESULT_MARKERS,
        )
        is_background = _has_any(normalized, BACKGROUND_MARKERS)
        is_external = _looks_like_external_attribution(sentence)
        if sentence_has_current_marker and not is_background and not is_external:



            current_scope_remaining = 5
        if not _is_direct_claim_text(sentence, subject_context=title):
            current_scope_remaining = max(0, current_scope_remaining - 1)
            continue
        if is_background and not sentence_has_current_marker:
            current_scope_remaining = max(0, current_scope_remaining - 1)
            continue
        if is_external:
            current_scope_remaining = max(0, current_scope_remaining - 1)
            continue
        if (
            sentence_has_current_marker
            or sentence_has_passive_result
            or current_scope_remaining > 0
        ):
            return {
                "direct": True,
                "basis": "abstract_current_result",
                "reason": "abstract reports a parasite-expression/clinical-phenotype relation",
                "evidence_excerpt": " ".join(sentence.split())[:500],
            }
        current_scope_remaining = max(0, current_scope_remaining - 1)

    return {
        "direct": False,
        "basis": "not_demonstrated",
        "reason": (
            "no title or current-study abstract statement jointly reports parasite expression "
            "and a clinical-phenotype association"
        ),
    }


def collapse_detected_publication_versions(
    papers: list[dict],
    document_context: Mapping[str, Sequence[dict]],
) -> list[dict]:
    """Display one representative for deterministically detected report versions.

    Only existing automatic version-family signals are used. Similar titles alone,
    shared cohorts, and shared datasets do not collapse records. The selected row
    retains explicit metadata for every hidden report version so no identity is
    invented or lost from the audit trail.
    """

    if len(papers) < 2:
        return [dict(paper) for paper in papers]

    ids = [str(paper.get("paper_id") or "") for paper in papers]
    parent = {paper_id: paper_id for paper_id in ids if paper_id}

    def find(paper_id: str) -> str:
        while parent[paper_id] != paper_id:
            parent[paper_id] = parent[parent[paper_id]]
            paper_id = parent[paper_id]
        return paper_id

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    accepted_signals = []
    for signal in suggest_lineage_signals(papers, dict(document_context)):
        if signal.signal_type not in PUBLICATION_VERSION_SIGNAL_TYPES:
            continue
        if signal.paper_id_a not in parent or signal.paper_id_b not in parent:
            continue
        union(signal.paper_id_a, signal.paper_id_b)
        accepted_signals.append(signal)

    groups: defaultdict[str, list[dict]] = defaultdict(list)
    for row_index, paper in enumerate(papers):
        paper_id = str(paper.get("paper_id") or "")
        group_key = find(paper_id) if paper_id in parent else f"__row_{row_index}"
        groups[group_key].append(paper)

    selected: list[dict] = []
    for group in groups.values():
        representative = max(group, key=_publication_version_preference)
        if len(group) == 1:
            selected.append(dict(representative))
            continue
        group_ids = {str(paper.get("paper_id") or "") for paper in group}
        relation_types = sorted(
            {
                signal.signal_type
                for signal in accepted_signals
                if signal.paper_id_a in group_ids and signal.paper_id_b in group_ids
            }
        )
        versions = [
            {
                key: paper.get(key)
                for key in ("paper_id", "title", "year", "doi", "pmid", "pmcid", "journal")
                if paper.get(key) not in (None, "")
            }
            for paper in sorted(group, key=lambda row: int(row.get("rank") or 10**9))
        ]
        selected.append(
            {
                **representative,
                "publication_version_display": "representative_of_detected_family",
                "publication_version_relationship_types": relation_types,
                "publication_family_reports": versions,
                "publication_family_report_count": len(versions),
            }
        )

    original_order = {
        str(paper.get("paper_id") or ""): index for index, paper in enumerate(papers)
    }
    selected.sort(
        key=lambda paper: min(
            original_order.get(str(version.get("paper_id") or ""), 10**9)
            for version in paper.get("publication_family_reports") or [paper]
        )
    )
    return [
        {**paper, "rank": rank}
        for rank, paper in enumerate(selected, start=1)
    ]


def _is_direct_claim_text(text: str, *, subject_context: str = "") -> bool:



    clauses = re.split(
        r"(?:;|\bwhereas\b|\bwhile\b|\balthough\b|\bhowever\b)",
        text,
        flags=re.IGNORECASE,
    )
    for clause in clauses:
        normalized = normalize_text(clause)
        if not (
            _has_parasite_expression(
                normalized,
                subject_context=normalize_text(subject_context),
            )
            and _has_any(normalized, CLINICAL_RESULT_TERMS)
            and _has_any(normalized, RESULT_RELATION_TERMS)
        ):
            continue
        if _is_technical_association_without_phenotype_comparison(normalized):
            continue
        return True
    return False


def _has_parasite_expression(
    normalized: str,
    *,
    subject_context: str = "",
) -> bool:

    if _has_any(normalized, HOST_EXPRESSION_TERMS):
        return False
    if _has_any(normalized, PARASITE_EXPRESSION_TERMS):
        return True
    if re.search(
        r"\bparasite genes?\b.{0,60}\b(?:differentially )?express(?:ed|ion)\b|"
        r"\b(?:differentially )?express(?:ed|ion)\b.{0,60}\bparasite genes?\b",
        normalized,
    ):
        return True
    has_expression = _has_any(normalized, EXPRESSION_TERMS)
    has_parasite_feature = _has_any(normalized, PARASITE_FEATURE_TERMS)
    if has_expression and has_parasite_feature:
        return True



    return (
        has_expression
        and _has_any(normalized, PARASITE_DOMAIN_SUBJECT_TERMS)
        and _has_any(subject_context, PARASITE_EXPRESSION_TITLE_CONTEXT_TERMS)
        and _has_explicit_phenotype_comparison(normalized)
    )


def _is_technical_association_without_phenotype_comparison(normalized: str) -> bool:
    if not _has_any(normalized, TECHNICAL_ASSOCIATION_TERMS):
        return False
    technical_relation = bool(
        re.search(
            r"\b(?:associated|correlated|linked) (?:with|to)\b.{0,60}"
            r"\b(?:batch|library size|read depth|sequencing (?:depth|lane|platform))\b",
            normalized,
        )
    )
    return technical_relation and not _has_explicit_phenotype_comparison(normalized)


def _has_explicit_phenotype_comparison(normalized: str) -> bool:
    phenotype = (
        r"(?:severity|severe malaria|cerebral malaria|severe (?:anemia|anaemia)|"
        r"hyperlactat(?:emia|aemia)|acidosis|respiratory distress|uncomplicated malaria|"
        r"asymptomatic (?:infection|malaria)|clinical (?:outcome|phenotype|presentation))"
    )
    association = r"(?:associated with|correlated with|linked to)"
    directional = (
        r"(?:higher in|lower in|increased in|decreased in|elevated in|enriched in|"
        r"upregulated in|downregulated in|overexpressed in|underexpressed in|"
        r"differentially expressed (?:between|in))"
    )
    return bool(
        re.search(rf"\b{association}\b (?:the )?\b{phenotype}\b", normalized)
        or re.search(rf"\b{directional}\b.{{0,30}}\b{phenotype}\b", normalized)
        or re.search(
            rf"\b{phenotype}\b.{{0,80}}\b(?:associated|correlated|linked|"
            r"higher|lower|increased|decreased|differential|variation)\b."
            r"{0,80}\b(?:expression|expressed|transcription|transcriptome)\b",
            normalized,
        )
        or re.search(
            r"\b(?:between|versus)\b.{0,30}\bsevere\b.{0,40}"
            r"\buncomplicated\b",
            normalized,
        )
        or re.search(
            r"\b(?:cm|cerebral malaria)(?: patients?| cases?| isolates?)?\b.{0,80}"
            r"\b(?:compared (?:with|to)|versus|than)\b.{0,30}"
            r"\b(?:um|uncomplicated malaria)(?: patients?| cases?| isolates?)?\b",
            normalized,
        )
        or re.search(
            r"\b(?:um|uncomplicated malaria)(?: patients?| cases?| isolates?)?\b.{0,80}"
            r"\b(?:compared (?:with|to)|versus|than)\b.{0,30}"
            r"\b(?:cm|cerebral malaria)(?: patients?| cases?| isolates?)?\b",
            normalized,
        )
    )


def _abstract_claim_text(chunks: Sequence[dict], *, max_chars: int = 24_000) -> str:
    parts: list[str] = []
    for chunk in chunks[:8]:
        section = normalize_text(str(chunk.get("section") or ""))
        if section in {"references", "bibliography"}:
            break
        text = " ".join(str(chunk.get("text") or "").split())
        text = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", text)
        if text:
            parts.append(text)
        if sum(len(part) for part in parts) >= max_chars:
            break
    combined = " ".join(parts)[:max_chars]
    start_match = ABSTRACT_START_RE.search(combined)
    if start_match:
        combined = combined[start_match.end() :]
    end_match = ABSTRACT_END_RE.search(combined)
    if end_match and end_match.start() >= 250:
        combined = combined[: end_match.start()]
    return combined


def _sentences(text: str) -> Iterable[str]:
    return (sentence.strip() for sentence in SENTENCE_RE.split(text) if sentence.strip())


def _looks_like_external_attribution(sentence: str) -> bool:
    return bool(
        re.search(r"\[[0-9,;\-\s]+\]", sentence)




        or re.search(r"[A-Za-z)]\d{1,3}(?:,\d{1,3})+[.]?$", sentence)
        or re.search(
            r"\([A-Z][A-Za-z'’\-]+(?:\s+et\s+al\.?)?[,]?\s+(?:19|20)\d{2}\)",
            sentence,
        )
        or re.search(r"\bet\s+al\.?[,]?\s+(?:19|20)\d{2}\b", sentence, re.IGNORECASE)
    )


def _has_any(normalized: str, terms: Sequence[str]) -> bool:
    return any(contains_normalized_term(normalized, term) for term in terms)


def _publication_version_preference(paper: dict) -> tuple[int, int, int, int, int]:
    doi = normalize_text(str(paper.get("doi") or ""))
    journal = str(paper.get("journal") or "").strip()
    is_preprint = doi.startswith("10 1101 ") or "biorxiv" in journal.lower() or "medrxiv" in journal.lower()
    is_reviewed_preprint = bool(re.search(r"\.\d+$", str(paper.get("doi") or "")))
    try:
        year = int(paper.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    rank = int(paper.get("rank") or 10**9)
    return (
        int(not is_preprint),
        int(bool(journal)),
        int(not is_reviewed_preprint),
        year,
        -rank,
    )
