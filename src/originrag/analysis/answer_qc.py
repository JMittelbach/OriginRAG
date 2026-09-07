from __future__ import annotations

import re

from originrag.analysis.comparison_coverage import (
    direct_comparison_candidate_groups,
    explicit_evidence_strength_question,
)
from originrag.analysis.evidence_pack_grading import (
    epistemic_causal_comparison_question,
)


REQUIRED_SECTIONS = {
    "answer": [
        "## Evidence-backed statements",
        "## Cautious inference",
        "## Limitations / uncertainty",
        "## Missing evidence",
        "## References",
    ],
    "sources": [
        "## Best match",
        "## Why this looks like the source",
        "## Other possible matches",
    ],
    "gaps": [
        "## Short Answer",
        "## Supported Research Gaps",
        "## Candidate Testable Questions",
        "## Best Next Data / Analysis",
        "## Key Sources",
    ],
    "hypothesis": [
        "## Hypothesis Card",
        "### hypothesis_id",
        "### title",
        "### short_summary",
        "### evidence_backed_statements",
        "### inference",
        "### speculation",
        "### testable_prediction",
        "### required_data",
        "### analysis_plan",
        "### risk_plan_b",
        "### novelty",
        "### impact",
        "### key_references",
    ],
}

WRAPPER_SECTIONS = {"## Hypothesis Card"}

ANSWER_PROSE_SECTIONS = (
    "## Evidence-backed statements",
    "## Cautious inference",
    "## Limitations / uncertainty",
    "## Missing evidence",
)

PROSE_VERB_PATTERN = re.compile(
    r"\b(?:"
    r"am|are|is|was|were|be|been|being|"
    r"can|could|do|does|did|had|has|have|may|might|must|shall|should|will|would|"
    r"associate(?:d|s)?|demonstrate(?:d|s)?|establish(?:ed|es)?|"
    r"find(?:s)?|found|indicate(?:d|s)?|remain(?:ed|s)?|report(?:ed|s)?|"
    r"show(?:ed|s)?|suggest(?:ed|s)?|support(?:ed|s)?|"
    r"belegt|berichtet|bleibt|hat|haben|ist|kann|könnte|sind|war|waren|zeigt"
    r")\b",
    flags=re.IGNORECASE,
)

COMMON_TECHNICAL_WORDS = {
    "APC",
    "BNI",
    "CIDR",
    "CIDRa",
    "CIDRalpha",
    "DBL",
    "DNA",
    "EPCR",
    "GAP",
    "IgG",
    "ICAM",
    "ICAM1",
    "OriginRAG",
    "MVP",
    "PfEMP1",
    "RNA",
    "RAG",
    "T-cell",
    "VSA",
    "VSAs",
    "WHO",
    "var",
}

OVER_SPECIFIC_PHRASES = {
    "meta-analysis": [
        "meta-analysis",
        "meta-analyses",
        "meta analysis",
        "meta analyses",
    ],
    "randomized controlled trial": [
        "randomized controlled trial",
        "randomized controlled trials",
        "randomised controlled trial",
        "randomised controlled trials",
    ],
    "randomized clinical trial": [
        "randomized clinical trial",
        "randomized clinical trials",
        "randomised clinical trial",
        "randomised clinical trials",
    ],
}

UNSUPPORTED_COOCCURRENCE_PAIRS = [
    ("SERM", "malaria-naive"),
    ("SERM", "CHMI"),
    ("SERM", "controlled human malaria infection"),
]

INTERVENTION_OVERCLAIM_PATTERNS = [
    r"\bSERMs?\b[^.\n]*\bcould address\b",
    r"\bSERMs?\b[^.\n]*\bmay leverage\b",
    r"\bSERMs?\b[^.\n]*\bcan improve\b",
    r"\bSERMs?\b[^.\n]*\bimprove outcomes\b",
]

INDEPENDENT_REPLICATION_ABSENCE_PATTERNS = (
    re.compile(
        r"\bno\s+(?:(?:clear|documented|confirmed)\s+)?independent\s+"
        r"(?:replication|replications|support)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\bindependent\s+(?:replication|replications|support)\s+"
        r"(?:is|are|was|were|remains?)\s+"
        r"(?:absent|lacking|missing|nonexistent|not present|unavailable)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:evidence|support)\s+(?:for|of)\s+independent\s+replication\s+"
        r"(?:is|was|remains?)\s+(?:absent|lacking|missing|unavailable)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:the\s+)?(?:evidence|evidence pack|retrieved (?:evidence|set)|literature)\s+"
        r"lacks?\s+(?:any\s+)?independent\s+(?:replication|support)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:a|the)\s+lack\s+of\s+independent\s+(?:replication|support)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:this|these|the|our)\s+"
        r"(?:findings?|results?|evidence)\s+(?:has|have)\s+not\s+been\s+"
        r"independently\s+(?:confirmed|replicated)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:this|these|the|our)\s+(?:findings?|results?|evidence)\s+"
        r"lacks?\s+independent\s+(?:confirmation|replication)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\bindependent\s+(?:replication|replications)\s+"
        r"(?:does|do|did)\s+not\s+exist\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\bkeine\s+unabh[aä]ngige\s+replikation\b|"
        r"\bunabh[aä]ngige\s+replikation\s+"
        r"(?:fehlt|ist\s+nicht\s+vorhanden)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:(?:ein|der)\s+mangel\s+an|(?:das\s+)?fehlen)\s+"
        r"(?:einer\s+)?unabh[aä]ngig(?:er|en)\s+replikation\b",
        flags=re.IGNORECASE,
    ),
)

INDEPENDENT_REPLICATION_EPISTEMIC_PREDICATE = re.compile(
    r"(?:"
    r"^\s+(?:that\s+)?(?:(?:has|have|had|is|are|was|were|can|could)\s+)?"
    r"(?:not\s+)?(?:yet\s+)?(?:been\s+)?"
    r"(?:adjudicated|certified|demonstrated|established|shown|verified)\b"
    r"|"
    r"^\s+(?:(?:ist|war|wurde|kann|konnte)\s+)?(?:noch\s+)?(?:nicht\s+)?"
    r"(?:belegt|best[aä]tigt|etabliert|gezeigt|verifiziert|zertifiziert)\b"
    r")",
    flags=re.IGNORECASE,
)

MECHANISM_ABSENCE_PATTERNS = [
    r"\bno direct experimental evidence\b",
    r"\bno direct experimental proof\b",
    r"\bdoes not provide direct experimental proof\b",
    r"\bno clear mechanistic (?:understanding|evidence)\b",
    r"\bdoes not provide (?:a )?(?:clear )?mechanistic\b",
    r"\bkeine direkten experimentellen belege\b",
    r"\bkeine klare mechanistische evidenz\b",
]

DIRECT_MECHANISM_EVIDENCE_TERMS = [
    "binding assay",
    "binds epcr",
    "bound epcr",
    "blockade",
    "blocking antibody",
    "competition assay",
    "competed for",
    "elisa",
    "endothelial cells",
    "inhibited binding",
    "reversal of binding",
    "surface plasmon resonance",
]

STRONG_CAUSAL_CLAIM_PATTERNS = (
    r"\bcauses?\b",
    r"\bcaused by\b",
    r"\bdrives?\b",
    r"\binduces?\b",
    r"\bleads? to\b",
    r"\b(?:may |might |could )?contributes? to\b",
    r"\bproves? (?:a |the |direct )?causal(?:ity| relationship)\b",
    r"\bverursacht\b",
    r"\bführt (?:direkt )?zu\b",
    r"\bfuhrt (?:direkt )?zu\b",
    r"\bbeweist (?:eine |die |direkte )?kausalität\b",
    r"\bbeweist (?:eine |die |direkte )?kausalitat\b",
)

CAUSAL_EVIDENCE_MARKERS = (
    "caused",
    "causes",
    "causal effect",
    "causal relationship",
    "demonstrated causality",
    "drives",
    "functional assay",
    "induced",
    "induces",
    "knockdown",
    "knockout",
    "randomized intervention",
    "randomised intervention",
    "prevented",
    "promotes",
    "rescue experiment",
    "verursacht",
    "kausaler effekt",
)

MARKER_POSITIVE_ENUMERATION_PATTERN = re.compile(
    r"(?P<marker>[A-Za-z][A-Za-z0-9]{1,15})"
    r"(?:\+|[- ]positive|[- ]positiv(?:e|en|er|es)?)\s+"
    r"(?:(?:macrophages?|cells?|myeloid)\s+)?"
    r"(?:subtypes?|groups?|states?|clusters?|subtypen?|gruppen?|zustande|zustände)"
    r"(?:\s+(?:in|among|from|across)\s+[^:.\n]{1,100}?)?\s*"
    r"(?::|,\s*(?:including|comprising|namely)\b|"
    r"\b(?:including|comprising|namely)\b)"
    r"\s*(?P<members>[^.\n]+)",
    flags=re.IGNORECASE,
)
MARKER_POSITIVE_QUESTION_PATTERN = re.compile(
    r"(?P<marker>[A-Za-z][A-Za-z0-9]{1,15})"
    r"(?:\+|[- ]positive|[- ]positiv(?:e|en|er|es)?)",
    flags=re.IGNORECASE,
)

CAUSAL_NEGATION_MARKERS = (
    "cannot establish",
    "cannot determine whether",
    "cannot distinguish whether",
    "does not establish",
    "does not determine whether",
    "does not demonstrate",
    "does not prove",
    "does not specify whether",
    "did not prove",
    "did not demonstrate",
    "insufficient to establish",
    "no causal",
    "no direct causal",
    "neither paper demonstrates",
    "neither report demonstrates",
    "neither study demonstrates",
    "not causal",
    "not established",
    "not possible to determine whether",
    "not proven",
    "uncertain whether",
    "unklar",
    "fehlen direkte kausale",
    "fehlen direkten kausalen",
    "keine kausale",
    "keine direkten kausalen",
    "nicht kausal",
    "nicht bewiesen",
    "nicht belegt",
)




CAUSAL_COMPLEMENT_NEGATION_MARKERS = (
    "cannot establish",
    "cannot determine",
    "cannot distinguish",
    "does not establish",
    "does not determine",
    "does not demonstrate",
    "does not prove",
    "does not specify",
    "did not prove",
    "did not demonstrate",
    "insufficient to establish",
    "not possible to determine",
    "uncertain",
    "unclear",
    "unklar",
    "nicht bewiesen",
    "nicht belegt",
)

NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

REPOSITORY_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:GSE\d+|PRJNA\d+|PRJEB\d+|ERP\d+|PXD\d+|MTBLS\d+)\b",
    flags=re.IGNORECASE,
)


def assess_answer_quality(answer: str | None, evidence_pack: dict, task: str) -> dict:
    """Return a small, transparent QC report for a model answer."""
    text = (answer or "").strip()
    normalized_task = (task or "answer").strip().lower()
    evidence_ids = {
        str(chunk.get("evidence_id"))
        for chunk in evidence_pack.get("chunks", [])
        if chunk.get("evidence_id")
    }
    cited_ids = set(re.findall(r"\bE\d{3}\b", text))
    citation_like_ids = set(re.findall(r"\bE\d{1,4}\b", text))
    malformed_citations = sorted(
        citation_id
        for citation_id in citation_like_ids
        if not re.fullmatch(r"E\d{3}", citation_id)
    )
    required_sections = REQUIRED_SECTIONS.get(normalized_task, [])
    missing_sections = [section for section in required_sections if section not in text]
    empty_sections = [
        section
        for section in required_sections
        if section not in WRAPPER_SECTIONS and section in text and not section_text(text, section)
    ]
    unknown_citations = sorted(cited_ids - evidence_ids)
    uncited_evidence_ids = sorted(evidence_ids - cited_ids)
    cited_paper_ids = {
        str(chunk.get("paper_id") or "")
        for chunk in evidence_pack.get("chunks", [])
        if chunk.get("evidence_id") in cited_ids and chunk.get("paper_id")
    }
    available_paper_ids = {
        str(chunk.get("paper_id") or "")
        for chunk in evidence_pack.get("chunks", [])
        if chunk.get("paper_id")
    }
    warnings: list[str] = []

    if not text:
        warnings.append("no_answer")
    if required_sections and missing_sections:
        warnings.append("missing_required_sections")
    if empty_sections:
        warnings.append("empty_required_sections")
    incomplete_sections = incomplete_answer_prose_sections(text, normalized_task)
    if incomplete_sections:
        warnings.append("incomplete_prose_section")
    if evidence_ids and not cited_ids and normalized_task != "sources":
        warnings.append("no_evidence_citations")
    if unknown_citations:
        warnings.append("unknown_evidence_citations")
    if malformed_citations:
        warnings.append("malformed_evidence_citations")
    citation_breadth_target = (
        8 if len(available_paper_ids) >= 12 else 4 if len(available_paper_ids) >= 5 else 0
    )
    citation_breadth_advisory = bool(
        normalized_task == "answer"
        and citation_breadth_target
        and len(cited_paper_ids) < citation_breadth_target
    )

    if normalized_task == "hypothesis" and novelty_overclaimed(text):
        warnings.append("novelty_overclaim")

    delimiter_issues = unbalanced_delimiters(text)
    if delimiter_issues:
        warnings.append("unbalanced_delimiters")

    incomplete_key_sources = (
        incomplete_gap_key_sources(text, evidence_pack) if normalized_task == "gaps" else []
    )
    if incomplete_key_sources:
        warnings.append("incomplete_key_sources")

    new_terms = unsupported_technical_terms(text, evidence_pack)
    if new_terms:
        warnings.append("possible_unsupported_terms")

    unsupported_phrases = unsupported_specific_phrases(text, evidence_pack)
    if unsupported_phrases:
        warnings.append("possible_unsupported_phrases")

    unsupported_combinations = unsupported_cross_source_combinations(text, evidence_pack)
    if unsupported_combinations:
        warnings.append("possible_cross_source_conflation")

    intervention_overclaims = unsupported_intervention_overclaims(text)
    if intervention_overclaims:
        warnings.append("possible_intervention_overclaim")

    mechanism_absence_overclaims = unsupported_mechanism_absence_claims(text, evidence_pack)
    if mechanism_absence_overclaims:
        warnings.append("mechanism_evidence_contradicted")

    causal_overclaims = unsupported_causal_claims(text, evidence_pack)
    if causal_overclaims:
        warnings.append("causal_claim_not_supported")

    epistemic_causal_issues = epistemic_causal_comparison_issues(
        text, evidence_pack
    )
    if epistemic_causal_issues:
        warnings.append("causal_evidence_question_not_resolved")

    marker_memberships = unsupported_marker_memberships(text, evidence_pack)
    if marker_memberships:
        warnings.append("marker_membership_not_supported")

    relation_upgrades = unsupported_relation_upgrades(text, evidence_pack)
    if relation_upgrades:
        warnings.append("relation_level_upgraded")

    relation_direction_inversions = unsupported_relation_directions(text, evidence_pack)
    if relation_direction_inversions:
        warnings.append("relation_direction_inverted")

    missing_marker_labels = omitted_marker_positive_labels(text, evidence_pack)
    if missing_marker_labels:
        warnings.append("marker_positive_labels_omitted")

    missing_direct_comparison_groups = omitted_direct_comparison_candidate_groups(
        text, evidence_pack, normalized_task
    )
    if missing_direct_comparison_groups:
        warnings.append("direct_comparison_evidence_omitted")

    scope_drift = question_scope_drift(text, str(evidence_pack.get("question") or ""))
    if scope_drift:
        warnings.append("possible_question_scope_drift")

    origin_count_contradiction = origin_count_same_study_contradiction(
        text,
        evidence_pack,
        available_paper_count=len(available_paper_ids),
    )
    if origin_count_contradiction:
        warnings.append("origin_count_ignores_same_study_relation")

    unsupported_independence = unsupported_independence_claims(text, evidence_pack)
    if unsupported_independence:
        warnings.append("independent_support_not_certified")

    unsupported_independence_absence = unsupported_independence_absence_claims(
        text, evidence_pack
    )
    if unsupported_independence_absence:
        warnings.append("independent_replication_absence_not_established")

    lineage_caveat_missing = missing_required_lineage_caveat(text, evidence_pack)
    if lineage_caveat_missing:
        warnings.append("lineage_uncertainty_not_disclosed")

    identifier_contradictions = contradicted_evidence_identifiers(text, evidence_pack)
    if identifier_contradictions:
        warnings.append("evidence_identifier_contradicted")

    report_chunk_conflation = report_chunk_unit_conflation(text, evidence_pack)
    if report_chunk_conflation:
        warnings.append("evidence_chunks_misreported_as_reports")

    report_count_mismatch = retrieved_report_count_mismatch(text, evidence_pack)
    if report_count_mismatch:
        warnings.append("retrieved_report_count_mismatch")

    unlabeled_secondary = unlabeled_secondary_citations(text, evidence_pack)
    if unlabeled_secondary:
        warnings.append("secondary_source_role_unlabeled")

    unlabeled_publication_families = unlabeled_publication_family_citations(
        text, evidence_pack, cited_ids
    )
    if unlabeled_publication_families:
        warnings.append("publication_family_cited_as_separate_support")

    var_unit_conflations = var_gene_subclass_conflations(text)
    if var_unit_conflations:
        warnings.append("var_gene_subclass_conflation")

    missing_repository_identifiers = omitted_repository_identifiers(text, evidence_pack)
    if missing_repository_identifiers:
        warnings.append("repository_identifier_omitted")

    passed = not warnings
    return {
        "passed": passed,
        "task": normalized_task,
        "warnings": warnings,
        "missing_sections": missing_sections,
        "empty_sections": empty_sections,
        "incomplete_prose_sections": incomplete_sections,
        "required_sections": required_sections,
        "evidence_ids_available": sorted(evidence_ids),
        "evidence_ids_cited": sorted(cited_ids),
        "unknown_citations": unknown_citations,
        "malformed_citations": malformed_citations,
        "uncited_evidence_ids": uncited_evidence_ids,
        "available_paper_count": len(available_paper_ids),
        "cited_paper_count": len(cited_paper_ids),
        "citation_breadth_target": citation_breadth_target,
        "citation_breadth_advisory": citation_breadth_advisory,
        "citation_breadth_semantics": (
            "reporting diagnostic only; paper count is not evidence sufficiency or "
            "independent-origin count"
        ),
        "possible_unsupported_terms": new_terms,
        "unbalanced_delimiters": delimiter_issues,
        "incomplete_key_sources": incomplete_key_sources,
        "possible_unsupported_phrases": unsupported_phrases,
        "possible_cross_source_conflations": unsupported_combinations,
        "possible_intervention_overclaims": intervention_overclaims,
        "possible_mechanism_absence_overclaims": mechanism_absence_overclaims,
        "unsupported_causal_claims": causal_overclaims,
        "epistemic_causal_comparison_issues": epistemic_causal_issues,
        "unsupported_marker_memberships": marker_memberships,
        "unsupported_relation_upgrades": relation_upgrades,
        "unsupported_relation_directions": relation_direction_inversions,
        "missing_marker_positive_labels": missing_marker_labels,
        "missing_direct_comparison_candidate_groups": missing_direct_comparison_groups,
        "possible_question_scope_drift": scope_drift,
        "origin_count_same_study_contradiction": origin_count_contradiction,
        "unsupported_independence_claims": unsupported_independence,
        "unsupported_independence_absence_claims": unsupported_independence_absence,
        "lineage_uncertainty_not_disclosed": lineage_caveat_missing,
        "contradicted_evidence_identifiers": identifier_contradictions,
        "report_chunk_unit_conflation": report_chunk_conflation,
        "retrieved_report_count_mismatch": report_count_mismatch,
        "unlabeled_secondary_citations": unlabeled_secondary,
        "unlabeled_publication_family_citations": unlabeled_publication_families,
        "var_gene_subclass_conflations": var_unit_conflations,
        "missing_repository_identifiers": missing_repository_identifiers,
        "answer_char_count": len(text),
    }


def omitted_direct_comparison_candidate_groups(
    answer: str, evidence_pack: dict, task: str
) -> list[dict]:
    """Find uncited direct comparisons for a bounded evidence-strength answer."""
    if task != "answer":
        return []
    question = str(evidence_pack.get("question") or "")
    if not explicit_evidence_strength_question(question):
        return []

    groups = direct_comparison_candidate_groups(question, evidence_pack, strict=True)


    if not 1 <= len(groups) <= 6:
        return []

    prose = re.split(r"(?im)^## References\b", answer or "", maxsplit=1)[0]
    cited_ids = set(re.findall(r"\bE\d{3}\b", prose))
    return [
        group
        for group in groups
        if cited_ids.isdisjoint(set(group.get("evidence_ids") or []))
    ]


def omitted_repository_identifiers(answer: str, evidence_pack: dict) -> list[dict[str, str]]:
    question = " ".join(str(evidence_pack.get("question") or "").lower().split())
    if not any(
        marker in question
        for marker in (
            "accessible",
            "accession",
            "data availability",
            "downloadable",
            "reanalysis",
            "repository",
        )
    ):
        return []
    cited_ids = set(re.findall(r"\bE\d{3}\b", answer or ""))
    normalized_answer = str(answer or "").upper()
    missing = []
    for chunk in evidence_pack.get("chunks", []):
        evidence_id = str(chunk.get("evidence_id") or "")
        identifiers = list(
            dict.fromkeys(
                match.group(0).upper()
                for match in REPOSITORY_IDENTIFIER_PATTERN.finditer(
                    str(chunk.get("text") or "")
                )
            )
        )
        if not identifiers:
            continue
        mentioned = [identifier for identifier in identifiers if identifier in normalized_answer]
        if mentioned and evidence_id in cited_ids:
            continue
        identifier = mentioned[0] if mentioned else identifiers[0]
        missing.append(
            {
                "identifier": identifier,
                "evidence_id": evidence_id,
                "reason": (
                    "identifier_not_named"
                    if not mentioned
                    else "supporting_evidence_not_cited"
                ),
            }
        )
    return missing


def unlabeled_secondary_citations(answer: str, evidence_pack: dict) -> list[str]:
    """Reject silent use of a secondary document as an empirical primary report."""
    secondary_types = {
        "systematic_review_meta_analysis",
        "narrative_review",
        "editorial_commentary",
        "reanalysis_secondary_data",
        "protocol",
    }
    source_type_by_id = {
        str(chunk.get("evidence_id") or ""): str(
            (chunk.get("source_type_assessment") or {}).get("source_type") or ""
        )
        for chunk in evidence_pack.get("chunks", [])
    }
    body = re.split(r"(?im)^## References\b", answer or "", maxsplit=1)[0]
    flagged = []
    for claim in re.split(r"(?<!et al\.)(?<=[.!?])\s+|\n+", body):
        evidence_ids = re.findall(r"\bE\d{3}\b", claim)
        secondary_ids = [
            evidence_id
            for evidence_id in evidence_ids
            if source_type_by_id.get(evidence_id) in secondary_types
        ]
        if not secondary_ids:
            continue
        if re.search(
            r"\b(?:systematic review|review|meta[- ]analysis|secondary (?:source|analysis|synthesis)|"
            r"reanalysis|protocol|commentary|synthesi[sz](?:es|ed|ing)?)\b",
            claim,
            flags=re.IGNORECASE,
        ):
            continue
        flagged.extend(secondary_ids)
    return sorted(set(flagged))


def unlabeled_publication_family_citations(
    answer: str, evidence_pack: dict, cited_ids: set[str]
) -> list[str]:
    """Flag two versions of one publication presented without their relationship."""
    evidence_by_paper: dict[str, set[str]] = {}
    for chunk in evidence_pack.get("chunks", []):
        paper_id = str(chunk.get("paper_id") or "")
        evidence_id = str(chunk.get("evidence_id") or "")
        if paper_id and evidence_id:
            evidence_by_paper.setdefault(paper_id, set()).add(evidence_id)
    normalized = " ".join(str(answer or "").lower().split())
    relationship_labeled = bool(
        re.search(
            r"\b(?:journal version|preprint version|related preprint|same (?:work|publication)|"
            r"publication family|version of (?:record|the same)|preprint and journal)\b",
            normalized,
        )
    )
    if relationship_labeled:
        return []
    accepted = {
        "same_doi_family",
        "same_preprint_family",
        "same_publication_version",
    }
    flagged = []
    for signal in evidence_pack.get("automatic_lineage_signals", []):
        if str(signal.get("signal_type") or "") not in accepted:
            continue
        left = str(signal.get("paper_id_a") or "")
        right = str(signal.get("paper_id_b") or "")
        if cited_ids.intersection(evidence_by_paper.get(left, set())) and cited_ids.intersection(
            evidence_by_paper.get(right, set())
        ):
            flagged.append("|".join(sorted((left, right))))
    return sorted(set(flagged))


def annotate_publication_family_relationships(answer: str | None, evidence_pack: dict) -> str | None:
    """Add deterministic version notes when a draft cites both sides of a family."""
    if not answer:
        return answer
    cited_ids = set(re.findall(r"\bE\d{3}\b", answer))
    if not unlabeled_publication_family_citations(answer, evidence_pack, cited_ids):
        return answer

    chunks_by_paper: dict[str, list[dict]] = {}
    for chunk in evidence_pack.get("chunks", []):
        paper_id = str(chunk.get("paper_id") or "")
        if paper_id:
            chunks_by_paper.setdefault(paper_id, []).append(chunk)

    notes = []
    seen_pairs = set()
    accepted = {"same_doi_family", "same_preprint_family", "same_publication_version"}
    for signal in evidence_pack.get("automatic_lineage_signals", []):
        signal_type = str(signal.get("signal_type") or "")
        if signal_type not in accepted:
            continue
        left = str(signal.get("paper_id_a") or "")
        right = str(signal.get("paper_id_b") or "")
        pair = tuple(sorted((left, right)))
        if pair in seen_pairs:
            continue
        left_ids = [
            str(chunk.get("evidence_id"))
            for chunk in chunks_by_paper.get(left, [])
            if str(chunk.get("evidence_id") or "") in cited_ids
        ]
        right_ids = [
            str(chunk.get("evidence_id"))
            for chunk in chunks_by_paper.get(right, [])
            if str(chunk.get("evidence_id") or "") in cited_ids
        ]
        if not left_ids or not right_ids:
            continue
        seen_pairs.add(pair)
        left_label = _paper_answer_label(chunks_by_paper.get(left, []), left)
        right_label = _paper_answer_label(chunks_by_paper.get(right, []), right)
        relation = (
            "related preprint and journal versions"
            if signal_type == "same_preprint_family"
            else "related versions of one publication"
        )
        notes.append(
            f"- {left_label} ({', '.join(left_ids)}) and {right_label} "
            f"({', '.join(right_ids)}) are automatically linked as {relation}. "
            "They count as one publication family, not independent support."
        )
    if not notes:
        return answer

    note_section = "## Publication-version note\n" + "\n".join(notes)
    references = re.search(r"(?im)^## References\b", answer)
    if references:
        body = answer[: references.start()].rstrip()
        tail = answer[references.start() :].lstrip()
        return f"{body}\n\n{note_section}\n\n{tail}".strip()
    return f"{answer.rstrip()}\n\n{note_section}".strip()


def normalize_inline_evidence_labels(answer: str | None, evidence_pack: dict) -> str | None:
    """Replace a one-paper inline author-year prefix with the corpus citation label."""
    if not answer:
        return answer
    chunk_by_id = {
        str(chunk.get("evidence_id")): chunk
        for chunk in evidence_pack.get("chunks", [])
        if chunk.get("evidence_id")
    }

    def replace(match: re.Match[str]) -> str:
        inner = match.group(1)
        ids = re.findall(r"\bE\d{3}\b", inner)
        known = [evidence_id for evidence_id in ids if evidence_id in chunk_by_id]
        papers = {str(chunk_by_id[evidence_id].get("paper_id") or "") for evidence_id in known}
        if not known or len(papers) != 1:
            return match.group(0)
        text_without_ids = re.sub(r"\bE\d{3}\b", "", inner)
        has_author_year = re.search(
            r"(?:\b(?:19|20)\d{2}[a-z]?\b|\bn\.?\s*d\.?\b)",
            text_without_ids,
            flags=re.IGNORECASE,
        )
        if not has_author_year:



            return match.group(0)
        label = _paper_answer_label([chunk_by_id[known[0]]], "")
        if not label:
            return match.group(0)
        return f"({label}; {'; '.join(dict.fromkeys(known))})"

    return re.sub(r"\(([^()]*\bE\d{3}\b[^()]*)\)", replace, answer)


def var_gene_subclass_conflations(answer: str) -> list[str]:
    """Catch the specific substitution of dominant genes with dominant subclasses."""
    body = re.split(r"(?im)^## References\b", answer or "", maxsplit=1)[0]
    return sorted(
        set(
            match.group(0)
            for match in re.finditer(
                r"\b(?:one|a) or (?:a )?few dominant var (?:subclasses|groups)\b",
                body,
                flags=re.IGNORECASE,
            )
        )
    )


def _paper_answer_label(chunks: list[dict], fallback: str) -> str:
    for chunk in chunks:
        citation = str(chunk.get("citation_label") or "").strip().strip("[]")
        if citation:
            return citation.split(",", maxsplit=1)[0].strip()
    return fallback


def report_chunk_unit_conflation(answer: str, evidence_pack: dict) -> dict[str, object]:
    """Flag a numbered report set represented only by chunk IDs from fewer papers."""
    evidence_to_paper = {
        str(chunk.get("evidence_id")): str(chunk.get("paper_id") or "")
        for chunk in evidence_pack.get("chunks", [])
        if chunk.get("evidence_id") and chunk.get("paper_id")
    }
    normalized = " ".join(str(answer or "").split())
    number_tokens = "|".join(NUMBER_WORDS)
    pattern = re.compile(
        rf"\b(?P<count>\d+|{number_tokens})\s+(?:retrieved\s+)?reports?\s*"
        r"\((?P<citations>[^)]{1,240})\)",
        re.I,
    )
    for match in pattern.finditer(normalized):
        token = match.group("count").lower()
        claimed = int(token) if token.isdigit() else NUMBER_WORDS[token]
        cited_ids = set(re.findall(r"\bE\d{3}\b", match.group("citations")))
        cited_papers = {evidence_to_paper[evidence_id] for evidence_id in cited_ids if evidence_id in evidence_to_paper}
        if claimed >= 2 and cited_ids and len(cited_papers) < claimed:
            return {
                "claimed_report_count": claimed,
                "cited_evidence_ids": sorted(cited_ids),
                "represented_paper_count": len(cited_papers),
                "represented_paper_ids": sorted(cited_papers),
                "remedy": "Count paper/report records by paper_id; Evidence IDs identify chunks, not reports.",
            }
    return {}


def retrieved_report_count_mismatch(answer: str, evidence_pack: dict) -> dict[str, object]:
    """Reject explicit counts of the retrieved paper set that disagree with metadata."""
    actual = int(evidence_pack.get("n_unique_papers") or 0)
    if actual <= 0:
        actual = len(
            {
                str(chunk.get("paper_id") or "")
                for chunk in evidence_pack.get("chunks", [])
                if str(chunk.get("paper_id") or "")
            }
        )
    normalized = " ".join(str(answer or "").lower().split())
    number_tokens = "|".join(NUMBER_WORDS)
    patterns = (
        rf"(?:retrieved evidence|evidence pack|retrieved set) "
        rf"(?:consists of|contains exactly|has exactly) "
        rf"(?P<count>\d+|{number_tokens}) "
        r"(?:papers|reports)",
        rf"(?:we|the system) retrieved (?P<count>\d+|{number_tokens}) "
        r"(?:papers|reports)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        token = match.group("count").lower()
        claimed = int(token) if token.isdigit() else NUMBER_WORDS[token]
        if claimed != actual:
            return {
                "claimed_report_count": claimed,
                "actual_unique_paper_count": actual,
                "matched_text": match.group(0),
                "remedy": "Use the structured unique-paper count; do not echo a count from the question.",
            }
    return {}


def contradicted_evidence_identifiers(answer: str, evidence_pack: dict) -> list[str]:
    """Flag claims that a DOI is absent when it is present in structured evidence metadata."""
    dois = {
        normalize_doi_for_qc(str(chunk.get("doi") or ""))
        for chunk in evidence_pack.get("chunks", [])
        if normalize_doi_for_qc(str(chunk.get("doi") or ""))
    }
    normalized = " ".join(str(answer or "").lower().split())
    contradicted = []
    absence_phrases = (
        "is not referenced",
        "is not mentioned",
        "is absent",
        "does not contain",
        "not present",
        "no evidence item",
        "not establish any connection",
    )
    for doi in sorted(dois):
        for match in re.finditer(re.escape(doi), normalized):
            context = normalized[max(0, match.start() - 80) : match.end() + 140]
            if any(phrase in context for phrase in absence_phrases):
                contradicted.append(doi)
                break
    return contradicted


def normalize_doi_for_qc(value: str) -> str:
    normalized = str(value or "").strip().lower()
    normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized)
    return normalized.rstrip(".,;)")


def origin_count_same_study_contradiction(
    answer: str,
    evidence_pack: dict,
    *,
    available_paper_count: int | None = None,
) -> dict[str, object]:
    """Catch report-count answers that also acknowledge a shared study/version."""
    question = " ".join(str(evidence_pack.get("question") or "").lower().split())
    if "independent" not in question or not any(
        marker in question for marker in ("origin", "study", "studies")
    ):
        return {}
    normalized = " ".join(str(answer or "").lower().split())
    relationship_patterns = (
        r"same (?:core )?study",
        r"same publication family",
        r"peer[- ]reviewed (?:publication|version) of",
        r"direct (?:revision|version) of",
    )
    matched_relationship = next(
        (pattern for pattern in relationship_patterns if re.search(pattern, normalized)),
        None,
    )
    if not matched_relationship:
        return {}
    count = explicit_independent_origin_count(answer)
    raw_count = int(
        available_paper_count
        if available_paper_count is not None
        else evidence_pack.get("n_unique_papers") or 0
    )
    if count is None or raw_count < 2 or count < raw_count:
        return {}
    return {
        "claimed_independent_origin_count": count,
        "raw_report_count": raw_count,
        "matched_relationship_pattern": matched_relationship,
        "remedy": (
            "Do not count both related reports as independent origins; give a defensible upper "
            "bound or state that the exact count remains unresolved."
        ),
    }


def unsupported_independence_claims(answer: str, evidence_pack: dict) -> list[str]:
    """Reject positive independence claims when lineage certification is unresolved."""
    robust = evidence_pack.get("trace_robust_lineage") or {}
    if lineage_independence_certified(robust):
        return []
    patterns = (
        r"(?:across|from) (?:multiple |several )?independent (?:studies|origins|cohorts|datasets)",
        r"(?:multiple|several|distinct) independent (?:studies|origins|cohorts|datasets)",
        r"independent primary studies",
        r"(?:is|are|was|were) independently (?:replicated|confirmed|validated|supported)",
    )
    negative_markers = (
        "not certified",
        "cannot be certified",
        "not verified",
        "unverified",
        "unresolved",
        "unknown",
        "cannot establish",
        "does not establish",
    )
    findings: list[str] = []
    normalized = " ".join(str(answer or "").lower().split())
    for pattern in patterns:
        for match in re.finditer(pattern, normalized):
            context = sentence_containing(normalized, match.start(), match.end())
            if any(marker in context for marker in negative_markers):
                continue
            findings.append(match.group(0))
    unique = sorted(set(findings), key=lambda finding: (-len(finding), finding))
    return sorted(
        finding
        for index, finding in enumerate(unique)
        if not any(finding in longer for longer in unique[:index])
    )


def unsupported_independence_absence_claims(
    answer: str, evidence_pack: dict
) -> list[str]:
    """Reject absence claims that unresolved study lineage cannot establish.

    Missing or uncertain origin relations justify saying that independent
    replication is not established or certified. They do not justify saying
    that independent replication does not exist. A short, exact quotation is
    exempt only when it occurs verbatim in a cited evidence item.
    """

    robust = evidence_pack.get("trace_robust_lineage") or {}
    if lineage_independence_certified(robust):
        return []

    body = re.split(r"(?im)^## References\b", answer or "", maxsplit=1)[0]
    findings: list[str] = []
    clauses = re.split(r"(?<!et al\.)(?<=[.!?])\s+|\n+", body)
    for clause in clauses:
        normalized = " ".join(clause.split())
        if not normalized:
            continue
        flagged = False
        for pattern in INDEPENDENT_REPLICATION_ABSENCE_PATTERNS:
            for match in pattern.finditer(clause):
                if INDEPENDENT_REPLICATION_EPISTEMIC_PREDICATE.match(
                    clause[match.end() :]
                ):
                    continue
                if cited_source_contains_exact_independence_absence_quote(
                    clause,
                    evidence_pack,
                    claim_span=match.span(),
                ):
                    continue
                findings.append(_clean_warning_text(normalized))
                flagged = True
                break
            if flagged:
                break
    return list(dict.fromkeys(findings))


def cited_source_contains_exact_independence_absence_quote(
    clause: str,
    evidence_pack: dict,
    *,
    claim_span: tuple[int, int],
) -> bool:
    """Return true when the matched claim is inside an exact cited quotation."""

    cited_ids = set(re.findall(r"\bE\d{3}\b", clause))
    if not cited_ids:
        return False
    quoted_spans: list[str] = []
    for pattern in (r'"([^"\n]+)"', r"“([^”\n]+)”", r"„([^“\n]+)“"):
        for quote_match in re.finditer(pattern, clause):
            quote_start, quote_end = quote_match.span(1)
            if quote_start <= claim_span[0] and claim_span[1] <= quote_end:
                quoted_spans.append(quote_match.group(1))
    negative_quotes = [
        " ".join(quote.lower().split())
        for quote in quoted_spans
        if any(
            pattern.search(quote)
            for pattern in INDEPENDENT_REPLICATION_ABSENCE_PATTERNS
        )
    ]
    if not negative_quotes:
        return False

    cited_source_text = [
        " ".join(str(chunk.get("text") or "").lower().split())
        for chunk in evidence_pack.get("chunks", [])
        if str(chunk.get("evidence_id") or "") in cited_ids
    ]
    return any(
        quote in source_text
        for quote in negative_quotes
        for source_text in cited_source_text
    )


def unsupported_causal_claims(answer: str, evidence_pack: dict) -> list[str]:
    """Reject explicit causal assertions that are backed only by associative evidence."""
    body = re.split(r"(?im)^## References\b", answer or "", maxsplit=1)[0]
    chunks_by_id = {
        str(chunk.get("evidence_id") or ""): chunk
        for chunk in evidence_pack.get("chunks", [])
        if chunk.get("evidence_id")
    }
    findings: list[str] = []
    for line in body.splitlines():
        normalized = " ".join(line.lower().split())
        if not normalized:
            continue
        for pattern in STRONG_CAUSAL_CLAIM_PATTERNS:
            for match in re.finditer(pattern, normalized):
                claim = sentence_containing(normalized, match.start(), match.end())
                if causal_match_is_negated(
                    normalized,
                    match.start(),
                    match.end(),
                ):
                    continue
                cited_ids = re.findall(r"\bE\d{3}\b", line)
                cited_text = " ".join(
                    str(chunks_by_id.get(evidence_id, {}).get("text") or "").lower()
                    for evidence_id in cited_ids
                )
                evidence_is_causal = bool(cited_ids) and any(
                    marker in cited_text for marker in CAUSAL_EVIDENCE_MARKERS
                )
                universal = bool(
                    re.search(
                        r"\b(?:all|always|every|sole(?:ly)?|only cause|alle|immer|jede[rsnm]?)\b",
                        claim,
                    )
                )
                evidence_is_universal = bool(
                    re.search(
                        r"\b(?:all|always|every|sole(?:ly)?|only cause|alle|immer|jede[rsnm]?)\b",
                        cited_text,
                    )
                )
                if not evidence_is_causal or (universal and not evidence_is_universal):
                    findings.append(_clean_warning_text(claim))
    return list(dict.fromkeys(findings))


def epistemic_causal_comparison_issues(answer: str, evidence_pack: dict) -> list[str]:
    """Require a direct, non-upgraded answer to a causal-evidence question."""

    question = str(evidence_pack.get("question") or "")
    if not epistemic_causal_comparison_question(question):
        return []
    body = re.split(r"(?im)^## References\b", answer or "", maxsplit=1)[0]
    normalized = " ".join(body.lower().split())
    endpoint_pattern = (
        r"(?:incidence|risk|severe[- ]malaria|clinical (?:outcome|protection))"
    )
    causal_subject_pattern = r"(?:binding|receptor|causal|caus(?:e|es|ed|ality))"
    explicit_limit_patterns = (
        rf"\bneither\s+(?:paper|study|report)\b.{{0,140}}"
        rf"\b(?:demonstrate|establish|prove|show)\w*\b.{{0,180}}"
        rf"{causal_subject_pattern}.{{0,120}}{endpoint_pattern}",
        rf"\b(?:does|do|did|can|cannot)\s+not?\s*"
        rf"(?:demonstrate|establish|prove|show)\w*\b.{{0,180}}"
        rf"{causal_subject_pattern}.{{0,120}}{endpoint_pattern}",
        rf"\b(?:cannot|does not|do not|did not)\s+"
        rf"(?:establish|demonstrate|prove|show)\w*\b.{{0,180}}"
        rf"{endpoint_pattern}",
    )
    issues: list[str] = []
    if not any(re.search(pattern, normalized) for pattern in explicit_limit_patterns):
        issues.append("missing_explicit_causal_evidence_limit")

    chunks_by_id = {
        str(chunk.get("evidence_id") or ""): chunk
        for chunk in evidence_pack.get("chunks", [])
        if chunk.get("evidence_id")
    }
    positive_pattern = re.compile(
        rf"\b(?:demonstrates?|establishes?|proves?|shows?)\b"
        rf"[^.\n]{{0,180}}(?:caus(?:e|es|ed|al|ality)|reduc(?:e|es|ed|tion))"
        rf"[^.\n]{{0,100}}{endpoint_pattern}",
        flags=re.IGNORECASE,
    )
    for line in body.splitlines():
        normalized_line = " ".join(line.lower().split())
        for match in positive_pattern.finditer(line):
            if any(marker in normalized_line for marker in CAUSAL_NEGATION_MARKERS):
                continue
            cited_ids = re.findall(r"\bE\d{3}\b", line)
            if not any(
                direct_clinical_causal_evidence(
                    str(chunks_by_id.get(evidence_id, {}).get("text") or "")
                )
                for evidence_id in cited_ids
            ):
                issues.append(
                    "positive_clinical_causality_not_supported: "
                    + _clean_warning_text(match.group(0))
                )
    return list(dict.fromkeys(issues))


def direct_clinical_causal_evidence(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    has_manipulation = any(
        marker in normalized
        for marker in (
            "blocking intervention",
            "binding blockade",
            "inhibited binding",
            "knockdown",
            "knockout",
            "randomized intervention",
            "randomised intervention",
            "receptor blockade",
        )
    )
    has_clinical_endpoint = bool(
        re.search(
            r"\b(?:incidence|risk|severe[- ]malaria|clinical (?:outcome|protection))\b",
            normalized,
        )
    )
    has_causal_result = any(marker in normalized for marker in CAUSAL_EVIDENCE_MARKERS)
    return bool(has_manipulation and has_clinical_endpoint and has_causal_result)


def unsupported_marker_memberships(answer: str, evidence_pack: dict) -> list[dict]:
    """Reject colon lists that incorrectly apply one marker to every member."""

    body = re.split(r"(?im)^## References\b", answer or "", maxsplit=1)[0]
    chunks_by_id = {
        str(chunk.get("evidence_id") or ""): chunk
        for chunk in evidence_pack.get("chunks", [])
        if chunk.get("evidence_id")
    }
    findings = []
    for match in MARKER_POSITIVE_ENUMERATION_PATTERN.finditer(body):
        marker = match.group("marker")
        members = []
        for member in re.split(
            r"\s*(?:,|;|\band\b|\bund\b)\s*", match.group("members")
        ):
            clean = re.sub(
                r"^(?:and|und)\s+", "", member.strip(), flags=re.IGNORECASE
            )
            clean = re.sub(r"\s*\([^)]*\bE\d{3}\b[^)]*\)\s*$", "", clean)
            if clean:
                members.append(clean)
        cited_ids = set(re.findall(r"\bE\d{3}\b", match.group(0)))
        support_chunks = (
            [chunks_by_id[evidence_id] for evidence_id in cited_ids if evidence_id in chunks_by_id]
            if cited_ids
            else list(chunks_by_id.values())
        )
        support_text = " ".join(str(chunk.get("text") or "") for chunk in support_chunks)
        unsupported = [
            member
            for member in members
            if not marker_membership_supported(member, marker, support_text)
        ]
        if unsupported:
            findings.append(
                {
                    "claim": _clean_warning_text(match.group(0)),
                    "marker": marker,
                    "unsupported_members": unsupported,
                    "repair": (
                        "Do not apply the shared marker-positive heading to these contrast "
                        "members; preserve the evidence's exact group labels."
                    ),
                }
            )

    question_marker = MARKER_POSITIVE_QUESTION_PATTERN.search(
        str(evidence_pack.get("question") or "")
    )
    if question_marker:
        marker = question_marker.group("marker")
        cited_ids = set(re.findall(r"\bE\d{3}\b", body))
        support_chunks = (
            [chunks_by_id[evidence_id] for evidence_id in cited_ids if evidence_id in chunks_by_id]
            if cited_ids
            else list(chunks_by_id.values())
        )
        support_text = " ".join(
            str(chunk.get("text") or "") for chunk in support_chunks
        )
        allowed_numbers = {
            group.group(1)
            for label in marker_positive_group_labels(support_text, marker)
            if (
                group := re.search(
                    r"\b(?:group|state|subtype|cluster|gruppe|zustand|subtyp)\s*"
                    r"[-#]?\s*(\d+|[IVX]+)\b",
                    label,
                    flags=re.IGNORECASE,
                )
            )
        }
        number_list = r"\d+(?:\s*(?:,|and|und)\s*\d+)*"
        assignment_patterns = (
            rf"\b{re.escape(marker)}\s+expression\s+(?:in|within|by)\s+"
            rf"(?:[A-Za-z-]+\s+){{0,3}}groups?\s+(?P<numbers>{number_list})",
            rf"\bgroups?\s+(?P<numbers>{number_list})\s+"
            rf"(?:were|was|are|is)?\s*(?:{re.escape(marker)}\s*(?:\+|[- ]positive)|"
            rf"express(?:ed|ing)\s+{re.escape(marker)})",
        )
        for pattern in assignment_patterns:
            for match in re.finditer(pattern, body, flags=re.IGNORECASE):
                unsupported_numbers = [
                    number
                    for number in re.findall(r"\d+", match.group("numbers"))
                    if number not in allowed_numbers
                ]
                if unsupported_numbers:
                    findings.append(
                        {
                            "claim": _clean_warning_text(
                                sentence_containing(body, match.start(), match.end())
                            ),
                            "marker": marker,
                            "unsupported_members": [
                                f"group {number}" for number in unsupported_numbers
                            ],
                            "repair": (
                                "Remove the unsupported marker-expression assignment; "
                                "preserve the evidence's exact group labels."
                            ),
                        }
                    )
    return findings


def omitted_marker_positive_labels(answer: str, evidence_pack: dict) -> list[dict]:
    """Require all explicit marker-bearing labels from cited evidence in a which-question."""

    question = str(evidence_pack.get("question") or "")
    normalized_question = " ".join(question.lower().split())
    asks_which = bool(
        re.search(r"\b(?:which|what|welche[rsnm]?|quels?|quelles?)\b", normalized_question)
    )
    asks_for_groups = bool(
        re.search(
            r"\b(?:states?|groups?|subtypes?|clusters?|signatures?|"
            r"zustande|zustände|gruppen?|subtypen?|signaturen?)\b",
            normalized_question,
        )
    )
    marker_match = MARKER_POSITIVE_QUESTION_PATTERN.search(question)
    if not (asks_which and asks_for_groups and marker_match):
        return []

    marker = marker_match.group("marker")
    cited_ids = set(re.findall(r"\bE\d{3}\b", answer or ""))
    if not cited_ids:
        return []
    labels_to_sources: dict[str, set[str]] = {}
    for chunk in evidence_pack.get("chunks", []):
        evidence_id = str(chunk.get("evidence_id") or "")
        if evidence_id not in cited_ids:
            continue
        for label in marker_positive_group_labels(str(chunk.get("text") or ""), marker):
            labels_to_sources.setdefault(label, set()).add(evidence_id)
    missing = [
        label
        for label in labels_to_sources
        if not answer_contains_marker_group_label(answer, label)
    ]
    if not missing:
        return []
    return [
        {
            "marker": marker,
            "missing_labels": missing,
            "required_labels": list(labels_to_sources),
            "label_sources": {
                label: sorted(labels_to_sources[label]) for label in missing
            },
        }
    ]


def marker_positive_group_labels(text: str, marker: str) -> list[str]:
    """Extract explicit labels such as ``FCN1loSPP1+ group 2`` from evidence."""

    marker_label = (
        rf"(?<![A-Za-z0-9])(?:[A-Za-z][A-Za-z0-9-]*?)?{re.escape(marker)}"
        rf"(?:\+|[- ]positive|[- ]positiv\w*)"
    )
    kind = r"(?:group|state|subtype|cluster|gruppe|zustand|subtyp)"
    number = r"(?:\d+|[IVX]+)"
    labels = []
    for match in re.finditer(
        rf"(?P<label>{marker_label})\s+(?P<kind>{kind})\s*[-#]?\s*(?P<number>{number})\b",
        text,
        flags=re.IGNORECASE,
    ):
        labels.append(
            f"{match.group('label')} {match.group('kind')} {match.group('number')}"
        )
    for match in re.finditer(
        rf"(?P<kind>{kind})\s*[-#]?\s*(?P<number>{number})"
        rf"\s*(?:[:=(-]\s*)?(?P<label>{marker_label})",
        text,
        flags=re.IGNORECASE,
    ):
        labels.append(
            f"{match.group('label')} {match.group('kind')} {match.group('number')}"
        )
    return list(dict.fromkeys(" ".join(label.split()) for label in labels))


def answer_contains_marker_group_label(answer: str, label: str) -> bool:
    match = re.fullmatch(
        r"(?P<label>.+?)\s+(?P<kind>group|state|subtype|cluster|gruppe|zustand|subtyp)"
        r"\s+(?P<number>\d+|[IVX]+)",
        label,
        flags=re.IGNORECASE,
    )
    if not match:
        return label.lower() in (answer or "").lower()
    label_pattern = re.escape(match.group("label"))
    kind_pattern = re.escape(match.group("kind"))
    number_pattern = re.escape(match.group("number"))
    for clause in re.split(r"[,;\n]|(?<=[.!?])\s+", answer or ""):
        if re.search(label_pattern, clause, flags=re.IGNORECASE) and re.search(
            rf"\b{kind_pattern}\s*[-#]?\s*{number_pattern}\b",
            clause,
            flags=re.IGNORECASE,
        ):
            return True
    return False


def marker_membership_supported(member: str, marker: str, evidence_text: str) -> bool:
    """Return whether evidence explicitly assigns one enumerated member to a marker."""

    marker_pattern = rf"{re.escape(marker)}\s*(?:\+|[- ]positive|[- ]positiv\w*)"
    group_match = re.search(
        r"\b(?:group|gruppe|cluster|state|subtype)\s*[-#]?\s*(\d+)\b",
        member,
        flags=re.IGNORECASE,
    )
    clauses = [
        clause.strip()
        for clause in re.split(
            r"[,;\n]|(?<=[.!?])\s+|"
            r"\s+(?:and|und)\s+(?=[A-Za-z][A-Za-z0-9-]*"
            r"(?:\+|hi\b|lo\b|high\b|low\b|[- ]positive))",
            evidence_text,
            flags=re.IGNORECASE,
        )
        if clause.strip()
    ]
    if group_match:
        number = group_match.group(1)
        group_pattern = rf"\b(?:group|gruppe|cluster|state|subtype)\s*[-#]?\s*{number}\b"
        if any(
            re.search(group_pattern, clause, flags=re.IGNORECASE)
            and re.search(marker_pattern, clause, flags=re.IGNORECASE)
            for clause in clauses
        ):
            return True
        coordinated = re.compile(
            rf"\bgroups?\s+(?P<numbers>(?:\d+\s*(?:(?:,|and|und)\s*)?)+)"
            rf"\s+(?:are|were|was|showed|expressed|labelled|labeled|marked)"
            rf"[^.!?]{{0,100}}{marker_pattern}",
            flags=re.IGNORECASE,
        )
        if any(
            number in re.findall(r"\d+", relation.group("numbers"))
            for relation in coordinated.finditer(evidence_text)
        ):
            return True
        marker_first = re.compile(
            rf"{marker_pattern}\s+(?:(?:macrophage|cell|myeloid)\s+)?groups?\s+"
            rf"(?P<numbers>(?:\d+\s*(?:(?:,|and|und)\s*)?)+)",
            flags=re.IGNORECASE,
        )
        if any(
            number in re.findall(r"\d+", relation.group("numbers"))
            for relation in marker_first.finditer(evidence_text)
        ):
            return True




        return False

    ignored = {
        marker.lower(),
        "and",
        "cluster",
        "group",
        "gruppe",
        "state",
        "subtype",
        "und",
    }
    labels = [
        token
        for token in re.findall(r"\b[A-Za-z][A-Za-z0-9+]*\b", member)
        if token.lower() not in ignored
        and not token.isdigit()
        and (any(char.isdigit() for char in token) or any(char.isupper() for char in token[1:]))
    ]
    return any(
        re.search(rf"\b{re.escape(label)}\b", clause, flags=re.IGNORECASE)
        and re.search(marker_pattern, clause, flags=re.IGNORECASE)
        for label in labels
        for clause in clauses
    )


def unsupported_relation_upgrades(answer: str, evidence_pack: dict) -> list[dict]:
    """Reject upgrading a cited comparative expression result to strong association."""

    chunks_by_id = {
        str(chunk.get("evidence_id") or ""): chunk
        for chunk in evidence_pack.get("chunks", [])
        if chunk.get("evidence_id")
    }
    findings = []
    for line in re.split(r"[\n]", answer or ""):
        awkward_expression = re.search(
            r"\b(?P<marker>[A-Za-z][A-Za-z0-9+-]*)\s+expression\s+"
            r"(?:was|were|is|are)\s+(?:highly\s+)?expressed\b",
            line,
            flags=re.IGNORECASE,
        )
        if awkward_expression:
            marker = awkward_expression.group("marker")
            findings.append(
                {
                    "claim": _clean_warning_text(line),
                    "evidence_ids": re.findall(r"\bE\d{3}\b", line),
                    "required_relation": (
                        f"say '{marker} was highly expressed' or "
                        f"'{marker} expression was higher', matching the evidence"
                    ),
                }
            )
        if not re.search(r"\b(?:highly|strongly|significantly)\s+associated\b", line, re.I):
            continue
        cited_ids = re.findall(r"\bE\d{3}\b", line)
        cited_text = " ".join(
            str(chunks_by_id.get(evidence_id, {}).get("text") or "")
            for evidence_id in cited_ids
        ).lower()
        reports_association = bool(re.search(r"\b(?:associat|correlat)", cited_text))
        reports_comparative_expression = bool(
            re.search(r"\b(?:express|enrich|abundan)", cited_text)
            and re.search(r"\b(?:higher|lower|highly|severe|moderate|versus|vs\.?|compared)\b", cited_text)
        )
        if cited_ids and reports_comparative_expression and not reports_association:
            findings.append(
                {
                    "claim": _clean_warning_text(line),
                    "evidence_ids": cited_ids,
                    "required_relation": "comparative expression or enrichment",
                }
            )
    return findings


_DIRECTION_WORDS = {
    "up": (
        "higher",
        "greater",
        "increased",
        "elevated",
        "enriched",
        "overexpressed",
        "upregulated",
        "more abundant",
        "more frequent",
        "more prevalent",
    ),
    "down": (
        "lower",
        "decreased",
        "reduced",
        "depleted",
        "underexpressed",
        "downregulated",
        "less abundant",
        "less frequent",
        "less prevalent",
    ),
}
_DIRECTION_TOKEN_PATTERN = "|".join(
    re.escape(term)
    for terms in _DIRECTION_WORDS.values()
    for term in sorted(terms, key=len, reverse=True)
)
_COMPARATIVE_RELATION_PATTERN = re.compile(
    rf"(?P<subject>[A-Za-z0-9][^.!?\n;:]{{1,100}}?)\s+"
    rf"(?:was|were|is|are|was found to be|were found to be)\s+"
    rf"(?:statistically\s+|significantly\s+)?"
    rf"(?P<direction>{_DIRECTION_TOKEN_PATTERN})\s+"
    rf"(?:[^.!?\n;:]{{0,40}}?\s+)?(?:in|among)\s+"
    rf"(?P<focus>[^.!?\n;:]{{2,80}}?)\s+"
    rf"(?:than|versus|vs\.?|compared\s+(?:with|to))\s+"
    rf"(?:in\s+|among\s+)?(?P<comparator>[^.!?\n;:()]{{2,80}})",
    flags=re.IGNORECASE,
)
_SINGLE_GROUP_RELATION_PATTERN = re.compile(
    rf"(?P<subject>[A-Za-z0-9][^.!?\n;:,]{{1,100}}?)\s+"
    rf"(?:was|were|is|are|was found to be|were found to be)\s+"
    rf"(?:statistically\s+|significantly\s+)?"
    rf"(?P<direction>{_DIRECTION_TOKEN_PATTERN})\s+"
    rf"(?:[^.!?\n;:,]{{0,40}}?\s+)?(?:in|among)\s+"
    rf"(?P<focus>[^.!?\n;:,()]{{2,80}}?)"
    rf"(?=\s*(?:\([^)]*\)|[.!?;,:]|$))",
    flags=re.IGNORECASE,
)
_GROUP_FOCUSED_UP_RELATION_PATTERN = re.compile(
    r"(?P<focus>[A-Za-z0-9][^.!?\n;:,]{1,100}?)\s+"
    r"(?:was|were|is|are)\s+"
    r"(?:marked|characterized|characterised)\s+by\s+"
    r"(?:a|an|the)?\s*(?:dominance|enrichment|prevalence)\s+of\s+"
    r"(?P<subject>[^.!?\n;:,()]{2,100}?)"
    r"(?=\s*(?:\([^)]*\)|[.!?;,:]|$))",
    flags=re.IGNORECASE,
)
_GROUP_FOCUSED_POSSESSION_UP_RELATION_PATTERN = re.compile(
    r"(?P<focus>[A-Za-z0-9][^.!?\n;:,]{1,100}?)\s+"
    r"(?:show(?:s|ed)?|ha(?:s|ve|d)|present(?:s|ed)?|display(?:s|ed)?|"
    r"exhibit(?:s|ed)?)\s+"
    r"(?:(?!\b(?:no|not|without)\b)[^.!?\n;:,]){0,140}?"
    r"(?:a|an|the)?\s*(?:dominance|enrichment|prevalence)\s+of\s+"
    r"(?P<subject>[^.!?\n;:,()]{2,100}?)"
    r"(?=\s*(?:\([^)]*\)|[.!?;,:]|$))",
    flags=re.IGNORECASE,
)
_DID_NOT_DIFFER_PATTERN = re.compile(
    r"(?P<subject>[A-Za-z0-9][^.!?\n;:]{1,100}?)\s+"
    r"(?:did|does|do)\s+not\s+(?:significantly\s+)?differ\s+between\s+"
    r"(?P<focus>[^.!?\n;:]{2,80}?)\s+and\s+"
    r"(?P<comparator>[^.!?\n;:()]{2,80})",
    flags=re.IGNORECASE,
)
_NO_DIFFERENCE_PATTERN = re.compile(
    r"(?:no|not\s+a)\s+"
    r"(?:statistically\s+significant\s+|statistically\s+|significant\s+)?"
    r"difference\s+"
    r"(?:was\s+(?:observed|found)\s+)?(?:in|for)\s+"
    r"(?P<subject>[^.!?\n;:]{1,100}?)\s+between\s+"
    r"(?P<focus>[^.!?\n;:]{2,80}?)\s+and\s+"
    r"(?P<comparator>[^.!?\n;:()]{2,80})",
    flags=re.IGNORECASE,
)
_SIMILAR_PAIR_PATTERN = re.compile(
    r"(?P<subject>[A-Za-z0-9][^.!?\n;:]{1,100}?)\s+"
    r"(?:was|were|is|are|was found to be|were found to be)\s+"
    r"(?:statistically\s+)?(?:similar|comparable)\s+(?:between|across)\s+"
    r"(?P<focus>[^.!?\n;:]{2,80}?)\s+and\s+"
    r"(?P<comparator>[^.!?\n;:()]{2,80})",
    flags=re.IGNORECASE,
)
_UNIVERSAL_GROUP_NULL_PATTERN = re.compile(
    r"(?P<subject>[A-Za-z0-9][^.!?\n;:]{1,120}?)\s+"
    r"(?:was|were|is|are|was found to be|were found to be)\s+"
    r"(?:statistically\s+)?(?:similar|comparable|(?:transcribed\s+)?at\s+the\s+same\s+level)\s+"
    r"(?:between|across|in|among)\s+"
    r"(?P<scope>(?:(?:all|both|the|these|those|two|three|four|five|different)\s+){1,3}"
    r"(?:sample|clinical|study|patient)?\s*groups?)",
    flags=re.IGNORECASE,
)
_NEGATED_COMPARATIVE_FRAME_PATTERN = re.compile(
    r"\b(?:did|does|do)\s+not\s+"
    r"(?:clearly\s+)?"
    r"(?:show|find|demonstrate|indicate|report|establish|support|confirm|reveal)\s+"
    r"that\b",
    flags=re.IGNORECASE,
)
_RELATION_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "or",
    "the",
    "to",
    "was",
    "were",
    "with",
}
_GROUP_STOPWORDS = _RELATION_STOPWORDS | {
    "case",
    "cases",
    "child",
    "children",
    "group",
    "groups",
    "individual",
    "individuals",
    "malaria",
    "patient",
    "patients",
    "participant",
    "participants",
    "people",
    "subjects",
}
_ENDPOINT_DISCRIMINATOR_CATEGORIES = {
    "presence": {
        "detection",
        "detections",
        "frequency",
        "frequencies",
        "occurrence",
        "occurrences",
        "percentage",
        "percentages",
        "positivity",
        "prevalence",
        "prevalences",
        "proportion",
        "proportions",
    },
    "amount": {
        "abundance",
        "abundances",
        "amount",
        "amounts",
        "concentration",
        "concentrations",
        "copy",
        "copies",
        "count",
        "counts",
        "expression",
        "expressions",
        "level",
        "levels",
        "quantity",
        "quantities",
        "titer",
        "titers",
        "titre",
        "titres",
        "transcription",
        "transcriptional",
    },
}


def unsupported_relation_directions(answer: str, evidence_pack: dict) -> list[dict]:
    """Flag source-local higher/lower/null conflicts for a named feature.

    This handles explicit pairwise comparisons and narrow single-group claims.
    A null across all named sample groups can conflict with the latter. Ambiguous
    prose or mixed directions within one excerpt remains unassessed.
    """

    chunks_by_id = {
        str(chunk.get("evidence_id") or ""): chunk
        for chunk in evidence_pack.get("chunks", [])
        if chunk.get("evidence_id")
    }
    findings: list[dict] = []
    for line in (answer or "").splitlines():
        for sentence in _comparison_sentence_units(line):
            sentence_cited_ids = list(
                dict.fromkeys(
                    evidence_id
                    for evidence_id in re.findall(r"\bE\d{3}\b", sentence)
                    if evidence_id in chunks_by_id
                )
            )
            if not sentence_cited_ids:
                continue
            units = re.split(
                r"(?:,|;)\s*(?=(?:but|while|whereas)\b)|\s+(?=(?:but|whereas)\b)",
                sentence,
                flags=re.IGNORECASE,
            )
            for unit in units:
                unit_cited_ids = [
                    evidence_id
                    for evidence_id in sentence_cited_ids
                    if re.search(rf"\b{re.escape(evidence_id)}\b", unit)
                ]
                cited_ids = unit_cited_ids or sentence_cited_ids
                claim_relations = _explicit_comparative_relations(unit)
                for claim in claim_relations:
                    conflicts_by_state: dict[str, list[str]] = {}
                    for evidence_id in cited_ids:
                        evidence_relations = _explicit_comparative_relations(
                            str(chunks_by_id[evidence_id].get("text") or "")
                        )
                        aligned_states = {
                            aligned
                            for relation in evidence_relations
                            if (
                                aligned := _relation_state_in_claim_orientation(
                                    claim, relation
                                )
                            )
                        }

                        if len(aligned_states) != 1:
                            continue
                        reported = next(iter(aligned_states))
                        if reported != claim["state"]:
                            conflicts_by_state.setdefault(reported, []).append(evidence_id)
                    for reported, evidence_ids in conflicts_by_state.items():
                        findings.append(
                            {
                                "claim": _clean_warning_text(sentence),
                                "evidence_ids": evidence_ids,
                                "answer_direction": claim["state"],
                                "reported_direction": reported,
                                "required_relation": (
                                    f"do not attach {', '.join(evidence_ids)} to this "
                                    f"directional claim; the excerpt reports {reported} "
                                    f"for {claim['subject']}"
                                ),
                            }
                        )
    return findings


def _comparison_sentence_units(line: str) -> list[str]:
    """Split prose sentences while preserving common scholarly abbreviations."""

    sentinel = "\u2024"
    protected = re.sub(
        r"\b(?:et\s+al|e\.g|i\.e|fig|eq|ref|dr|mr|mrs|ms|prof)\.",
        lambda match: match.group(0).replace(".", sentinel),
        line,
        flags=re.IGNORECASE,
    )
    sentences = re.split(r"(?<=[.!?])\s+(?=\S)", protected)
    return [sentence.replace(sentinel, ".") for sentence in sentences if sentence.strip()]


def _explicit_comparative_relations(text: str) -> list[dict[str, object]]:
    clean = _normalize_comparative_text(
        re.sub(r"\bE\d{3}\b", "", str(text or ""))
    )
    relations: list[dict[str, object]] = []
    occupied_spans: list[tuple[int, int]] = []
    for match in _COMPARATIVE_RELATION_PATTERN.finditer(clean):
        if _comparative_match_is_negated(match):
            continue
        direction = match.group("direction").casefold()
        state = next(
            name
            for name, terms in _DIRECTION_WORDS.items()
            if direction in terms
        )
        relations.append(_relation_record(match, state))
        occupied_spans.append(match.span())
    for match in _SINGLE_GROUP_RELATION_PATTERN.finditer(clean):
        if _comparative_match_is_negated(match) or any(
            match.start() >= start and match.end() <= end for start, end in occupied_spans
        ):
            continue
        direction = match.group("direction").casefold()
        state = next(
            name for name, terms in _DIRECTION_WORDS.items() if direction in terms
        )
        relations.append(_single_group_relation_record(match, state))
    relations.extend(
        _single_group_relation_record(match, "up")
        for match in _GROUP_FOCUSED_UP_RELATION_PATTERN.finditer(clean)
    )
    relations.extend(
        _single_group_relation_record(match, "up")
        for match in _GROUP_FOCUSED_POSSESSION_UP_RELATION_PATTERN.finditer(clean)
        if not re.search(
            r"\b(?:(?:do|does|did)\s+not(?:\s+\w+){0,3}|never|failed\s+to)\s*$",
            match.group("focus"),
            flags=re.IGNORECASE,
        )
    )
    for pattern in (_DID_NOT_DIFFER_PATTERN, _NO_DIFFERENCE_PATTERN):
        relations.extend(_relation_record(match, "null") for match in pattern.finditer(clean))
    relations.extend(
        _relation_record(match, "null") for match in _SIMILAR_PAIR_PATTERN.finditer(clean)
    )
    relations.extend(
        _universal_null_relation_record(match)
        for match in _UNIVERSAL_GROUP_NULL_PATTERN.finditer(clean)
    )
    return relations


def _normalize_comparative_text(text: str) -> str:
    """Join likely PDF line wraps without erasing real clause boundaries."""

    clean = re.sub(r"(?<=[A-Za-z])-[\t ]*\n[\t ]*(?=[a-z])", "", text)
    return re.sub(
        r"(?<![.!?;:])[\t ]*\n[\t ]*(?!(?:#{1,6}|[>*+-]|\d+[.)])\s)(?=\S)",
        " ",
        clean,
    )


def _comparative_match_is_negated(match: re.Match[str]) -> bool:
    """Reject a comparative embedded in an explicit negated report frame."""

    return bool(_NEGATED_COMPARATIVE_FRAME_PATTERN.search(match.group("subject")))


def _relation_record(match: re.Match[str], state: str) -> dict[str, object]:
    subject = " ".join(match.group("subject").split())
    focus = " ".join(match.group("focus").split())
    comparator = " ".join(match.group("comparator").split())
    focus_terms = _relation_terms(focus, group=True)
    comparator_terms = _relation_terms(comparator, group=True)
    shared_group_terms = focus_terms & comparator_terms
    return {
        "subject": subject,
        "subject_terms": _relation_terms(subject),
        "focus_terms": focus_terms - shared_group_terms or focus_terms,
        "comparator_terms": comparator_terms - shared_group_terms or comparator_terms,
        "state": state,
        "universal_group_scope": False,
    }


def _single_group_relation_record(
    match: re.Match[str], state: str
) -> dict[str, object]:
    subject = _clean_relation_subject(match.group("subject"))
    focus = " ".join(match.group("focus").split())
    return {
        "subject": subject,
        "subject_terms": _relation_terms(subject),
        "focus_terms": _relation_terms(focus, group=True),
        "comparator_terms": set(),
        "state": state,
        "universal_group_scope": False,
    }


def _universal_null_relation_record(match: re.Match[str]) -> dict[str, object]:
    subject = _clean_relation_subject(match.group("subject"))
    return {
        "subject": subject,
        "subject_terms": _relation_terms(subject),
        "focus_terms": set(),
        "comparator_terms": set(),
        "state": "null",
        "universal_group_scope": True,
    }


def _clean_relation_subject(subject: str) -> str:
    return re.sub(
        r"^(?:additionally|but|however|while|whereas)\b[,\s]*",
        "",
        " ".join(subject.split()),
        flags=re.IGNORECASE,
    )


def _relation_terms(text: str, *, group: bool = False) -> set[str]:
    stopwords = _GROUP_STOPWORDS if group else _RELATION_STOPWORDS
    tokens = re.findall(r"[A-Za-z0-9]+", text.casefold())
    terms = {
        token
        for token in tokens
        if len(token) > 1 and token not in stopwords and not re.fullmatch(r"e\d+", token)
    }
    for index, token in enumerate(tokens):
        if (
            len(token) == 1
            and index
            and tokens[index - 1] in {"class", "group", "subgroup", "type"}
        ):
            terms.add(f"{tokens[index - 1]}:{token}")
    return terms


def _relation_state_in_claim_orientation(
    claim: dict[str, object], evidence: dict[str, object]
) -> str:
    if not _term_sets_match(claim["subject_terms"], evidence["subject_terms"], minimum=0.45):
        return ""
    if evidence.get("universal_group_scope"):
        if not _endpoint_discriminators_compatible(
            claim["subject_terms"], evidence["subject_terms"]
        ):
            return ""
        return str(evidence["state"])
    claim_has_comparator = bool(claim["comparator_terms"])
    evidence_has_comparator = bool(evidence["comparator_terms"])
    if not claim_has_comparator:
        if not _term_sets_match(claim["focus_terms"], evidence["focus_terms"]):
            return ""
        return str(evidence["state"])
    if not evidence_has_comparator:
        return ""
    same_order = _term_sets_match(claim["focus_terms"], evidence["focus_terms"]) and _term_sets_match(
        claim["comparator_terms"], evidence["comparator_terms"]
    )
    swapped_order = _term_sets_match(
        claim["focus_terms"], evidence["comparator_terms"]
    ) and _term_sets_match(claim["comparator_terms"], evidence["focus_terms"])
    if not same_order and not swapped_order:
        return ""
    state = str(evidence["state"])
    if swapped_order and state in {"up", "down"}:
        return "down" if state == "up" else "up"
    return state


def _term_sets_match(left: object, right: object, *, minimum: float = 0.5) -> bool:
    left_terms = set(left) if isinstance(left, (set, frozenset)) else set()
    right_terms = set(right) if isinstance(right, (set, frozenset)) else set()
    if not left_terms or not right_terms:
        return False
    left_labels = {term for term in left_terms if ":" in term}
    right_labels = {term for term in right_terms if ":" in term}
    if left_labels and right_labels and left_labels.isdisjoint(right_labels):
        return False
    overlap = left_terms & right_terms
    return bool(overlap) and len(overlap) / min(len(left_terms), len(right_terms)) >= minimum


def _endpoint_discriminators_compatible(left: object, right: object) -> bool:
    """Reject universal-null alignment across explicitly different endpoints."""

    left_terms = set(left) if isinstance(left, (set, frozenset)) else set()
    right_terms = set(right) if isinstance(right, (set, frozenset)) else set()
    left_categories = {
        category
        for category, terms in _ENDPOINT_DISCRIMINATOR_CATEGORIES.items()
        if left_terms & terms
    }
    right_categories = {
        category
        for category, terms in _ENDPOINT_DISCRIMINATOR_CATEGORIES.items()
        if right_terms & terms
    }
    return not left_categories or not right_categories or bool(
        left_categories & right_categories
    )


def causal_match_is_negated(text: str, start: int, end: int) -> bool:
    """Return whether a causal match is inside the same negated clause.

    A later caveat about a different uncertainty must not excuse an earlier
    causal assertion in the same sentence. Restrict the check to negation that
    precedes the causal predicate after the latest clause boundary.
    """

    sentence_left = max(text.rfind(mark, 0, start) for mark in (".", "?", "!", "\n")) + 1
    conjunction_boundaries = [
        match.end()
        for match in re.finditer(
            r"\b(?:but|however|whereas|yet)\b",
            text[sentence_left:start],
        )
    ]
    comma_boundaries = [
        position
        for position in range(sentence_left, start)
        if text[position] == ","
        and not comma_introduces_negated_causal_complement(
            text,
            sentence_left=sentence_left,
            comma_position=position,
            causal_start=start,
        )
    ]
    clause_left = max(
        sentence_left,
        max(comma_boundaries, default=-1) + 1,
        max(text.rfind(mark, sentence_left, start) for mark in (";", ":")) + 1,
        sentence_left + max(conjunction_boundaries, default=0),
    )
    prefix_through_match = text[clause_left:end]
    normalized_prefix = re.sub(r",\s*", " ", prefix_through_match)
    return any(marker in normalized_prefix for marker in CAUSAL_NEGATION_MARKERS)


def comma_introduces_negated_causal_complement(
    text: str,
    *,
    sentence_left: int,
    comma_position: int,
    causal_start: int,
) -> bool:
    """Return whether a comma joins an uncertainty marker to its complement."""

    complement = text[comma_position + 1 : causal_start].lstrip()
    if not re.match(r"(?:whether|if|that|ob|dass)\b", complement):
        return False
    uncertainty_prefix = text[sentence_left:comma_position]
    return any(
        marker in uncertainty_prefix for marker in CAUSAL_COMPLEMENT_NEGATION_MARKERS
    )


def missing_required_lineage_caveat(answer: str, evidence_pack: dict) -> bool:
    """Require a direct uncertainty disclosure when the user asks about dependence."""
    question = " ".join(str(evidence_pack.get("question") or "").lower().split())
    asks_about_lineage = any(
        marker in question
        for marker in (
            "independent",
            "same study",
            "same cohort",
            "same dataset",
            "copying",
            "copied",
            "duplicate",
            "publication family",
            "preprint",
            "journal version",
        )
    )
    if not asks_about_lineage:
        return False
    robust = evidence_pack.get("trace_robust_lineage") or {}
    if lineage_independence_certified(robust):
        return False
    normalized = " ".join(str(answer or "").lower().split())
    has_lineage_subject = any(
        marker in normalized
        for marker in (
            "independence",
            "independent support",
            "study origins",
            "origin relations",
            "cohort relations",
            "dataset relations",
            "publication families",
            "publication-family",
        )
    )
    has_uncertainty = any(
        marker in normalized
        for marker in (
            "not certified",
            "not verified",
            "unverified",
            "unresolved",
            "unknown",
            "cannot establish",
            "does not establish",
            "unreviewed",
        )
    )
    return not (has_lineage_subject and has_uncertainty)


def lineage_independence_certified(robust: dict) -> bool:
    """Return true only for sufficient evidence with a complete lineage graph.

    ``robustly_sufficient`` certifies the downstream facet/evidence threshold
    under every declared partition. It does not by itself assert that every
    report pair has a reviewed origin relation.
    """
    status = str(robust.get("status") or "").strip().lower()
    if status not in {"sufficient", "certified", "robustly_sufficient"}:
        return False
    try:
        missing = int(robust["missing_pair_count"])
        uncertain = int(robust["uncertain_pair_count"])
    except (KeyError, TypeError, ValueError):
        return False
    return missing == 0 and uncertain == 0


def sentence_containing(text: str, start: int, end: int) -> str:
    """Return the sentence/clause containing a matched claim."""
    left_boundaries = [text.rfind(mark, 0, start) for mark in (".", "?", "!", "\n")]
    left = max(left_boundaries) + 1
    right_candidates = [
        position
        for mark in (".", "?", "!", "\n")
        if (position := text.find(mark, end)) >= 0
    ]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left:right]


def ensure_required_lineage_caveat(answer: str | None, evidence_pack: dict) -> str | None:
    """Insert the certificate's unresolved-origin fact when the model omits it."""
    if not answer or not missing_required_lineage_caveat(answer, evidence_pack):
        return answer
    caveat = (
        "Study-origin independence among the retrieved reports is not certified; "
        "publication-family, cohort, and dataset relations remain unresolved."
    )
    heading = "## Limitations / uncertainty"
    if heading in answer:
        return answer.replace(heading, f"{heading}\n{caveat}", 1)
    return f"{answer.rstrip()}\n\n{caveat}"


def explicit_independent_origin_count(answer: str) -> int | None:
    normalized = " ".join(str(answer or "").lower().split())
    patterns = (
        r"(\d+)\s+independent (?:evidence )?(?:origins|studies)",
        r"(one|two|three|four|five|six|seven|eight|nine|ten)\s+independent (?:evidence )?(?:origins|studies)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            token = match.group(1)
            return int(token) if token.isdigit() else NUMBER_WORDS[token]
    list_match = re.search(
        r"independent (?:evidence )?origins are:(.*?)(?:\n\n|##|$)",
        answer,
        re.I | re.S,
    )
    if not list_match:
        return None
    indices = [int(value) for value in re.findall(r"\((\d+)\)", list_match.group(1))]
    return max(indices) if indices else None


def unsupported_mechanism_absence_claims(answer: str, evidence_pack: dict) -> list[str]:
    """Flag universal mechanism gaps contradicted by direct assays in the pack."""
    answer_lower = answer.lower()
    claims = [pattern for pattern in MECHANISM_ABSENCE_PATTERNS if re.search(pattern, answer_lower)]
    if not claims:
        return []
    evidence_text = " ".join(
        str(chunk.get("text") or "") for chunk in evidence_pack.get("chunks", [])
    ).lower()
    mechanism_terms = [term for term in DIRECT_MECHANISM_EVIDENCE_TERMS if term in evidence_text]
    return sorted(mechanism_terms) if mechanism_terms else []


def question_scope_drift(answer: str, question: str) -> list[str]:
    answer_lower = answer.lower()
    question_lower = question.lower()
    inherited_terms = ["hereditary", "inherited", "inheritance", "erblich", "vererbt", "vererbung"]
    if any(term in question_lower for term in inherited_terms):
        return []
    return [term for term in inherited_terms if term in answer_lower]


def novelty_overclaimed(answer: str) -> bool:
    novelty_text = section_text(answer, "### novelty")
    if not novelty_text:
        return False
    lowered = novelty_text.lower()
    claims_novel = any(term in lowered for term in ["novel", "new ", "hasn't been", "has not been"])
    hedged = any(
        term in lowered
        for term in [
            "not supported",
            "must be checked",
            "needs to be checked",
            "cannot be claimed",
            "retrieved evidence does not",
        ]
    )
    return claims_novel and not hedged


def section_text(answer: str, heading: str) -> str:
    start = answer.find(heading)
    if start < 0:
        return ""
    remainder = answer[start + len(heading) :]
    next_heading = re.search(r"\n#{2,3}\s+", remainder)
    if not next_heading:
        return remainder.strip()
    return remainder[: next_heading.start()].strip()


def incomplete_answer_prose_sections(answer: str, task: str) -> list[str]:
    """Find answer-section endings that look like clipped prose.

    The check is intentionally narrow. It examines only prose-oriented sections
    in the standard answer contract, ignores lists and tables, and requires a
    sentence-like block of at least eight words with a finite verb. A terminal
    citation parenthesis is accepted even when the author omitted a full stop.
    """
    if task != "answer":
        return []

    incomplete = []
    for heading in ANSWER_PROSE_SECTIONS:
        body = section_text(answer, heading)
        if not body:
            continue
        blocks = [block.strip() for block in re.split(r"\n\s*\n", body) if block.strip()]
        if not blocks:
            continue
        trailing_block = blocks[-1]
        lines = [line.strip() for line in trailing_block.splitlines() if line.strip()]
        if not lines or re.match(r"^(?:[-*+]\s+|\d+[.)]\s+|>\s*|\|)", lines[0]):
            continue
        if lines[0].startswith("```") or lines[-1].endswith("```"):
            continue

        prose = " ".join(lines).strip().rstrip("*_`")
        if re.search(r"[.!?…][\"'’”)\]]*$", prose):
            continue
        if re.search(r"\([^()]*\bE\d{3}\b[^()]*\)$", prose):
            continue
        if len(re.findall(r"\b[^\W\d_][\w'-]*\b", prose, flags=re.UNICODE)) < 8:
            continue
        if not PROSE_VERB_PATTERN.search(prose):
            continue
        incomplete.append(heading)
    return incomplete


def unbalanced_delimiters(answer: str) -> list[str]:
    stripped = re.sub(r"https?://\S+", "", answer or "")
    issues = []
    for opening, closing, label in [("(", ")", "parentheses"), ("[", "]", "brackets")]:
        depth = 0
        for character in stripped:
            if character == opening:
                depth += 1
            elif character == closing:
                depth -= 1
                if depth < 0:
                    break
        if depth != 0:
            issues.append(label)
    return issues


def incomplete_gap_key_sources(answer: str, evidence_pack: dict) -> list[str]:
    key_sources = section_text(answer, "## Key Sources")
    if not key_sources:
        return []
    labels = {
        str(chunk.get("evidence_id") or ""): str(chunk.get("citation_label") or "")
        for chunk in evidence_pack.get("chunks", [])
        if chunk.get("evidence_id")
    }
    incomplete = []
    for line in key_sources.splitlines():
        clean = line.strip()
        if not clean.startswith("-"):
            continue
        evidence_ids = re.findall(r"\bE\d{3}\b", clean)
        if not evidence_ids:
            incomplete.append(clean)
            continue
        if not any(labels.get(evidence_id) and labels[evidence_id] in clean for evidence_id in evidence_ids):
            incomplete.append(clean)
    return incomplete


def unsupported_technical_terms(answer: str, evidence_pack: dict, limit: int = 20) -> list[str]:
    evidence_text = " ".join(
        str(value or "")
        for chunk in evidence_pack.get("chunks", [])
        for value in [
            chunk.get("citation_label"),
            chunk.get("paper_id"),
            chunk.get("title"),
            chunk.get("journal"),
            chunk.get("doi"),
            chunk.get("pmid"),
            chunk.get("pmcid"),
            chunk.get("section"),
            chunk.get("text"),
        ]
    )
    lineage_signal_text = " ".join(
        str(value or "")
        for signal in evidence_pack.get("automatic_lineage_signals", [])
        for value in [signal.get("signal_type"), signal.get("details")]
    )
    support_text = f"{evidence_pack.get('question', '')} {evidence_text} {lineage_signal_text}"
    support_terms = {_normalize_term(term) for term in _technical_terms(support_text)}
    support_words = {_normalize_term(term) for term in re.findall(r"\b[A-Za-z][A-Za-z0-9αβγδκλ]+\b", support_text)}
    unsupported = []
    seen = set()
    for term in _technical_terms(_strip_markdown_headings(answer)):
        normalized = _normalize_term(term)
        if (
            normalized in seen
            or normalized in support_terms
            or _plural_variant_supported(normalized, support_terms)
            or _compound_term_supported(term, support_words)
            or term in COMMON_TECHNICAL_WORDS
        ):
            continue
        if len(normalized) < 3:
            continue
        unsupported.append(term)
        seen.add(normalized)
        if len(unsupported) >= limit:
            break
    return unsupported


def _plural_variant_supported(normalized: str, support_terms: set[str]) -> bool:
    if normalized.endswith("s") and normalized[:-1] in support_terms:
        return True
    return f"{normalized}s" in support_terms


def _compound_term_supported(term: str, support_words: set[str]) -> bool:
    if "-" not in term:
        return False
    parts = [_normalize_term(part) for part in term.split("-") if part]
    if parts and all(part in support_words for part in parts):
        return True


    generic_parts = {
        "associated",
        "based",
        "binding",
        "blocking",
        "expressing",
        "linked",
        "mediated",
        "related",
        "specific",
    }
    generic_prefixes = (
        "bind",
        "exprimier",
        "positiv",
        "subtyp",
        "variant",
    )
    informative_parts = [
        part
        for part in parts
        if part not in generic_parts
        and not part.isdigit()
        and not any(part.startswith(prefix) for prefix in generic_prefixes)
    ]
    return bool(informative_parts) and all(part in support_words for part in informative_parts)


def unsupported_specific_phrases(answer: str, evidence_pack: dict) -> list[str]:
    """Flag concrete study designs that are more specific than the retrieved evidence."""
    evidence_text = " ".join(
        str(value or "")
        for chunk in evidence_pack.get("chunks", [])
        for value in [
            chunk.get("citation_label"),
            chunk.get("paper_id"),
            chunk.get("title"),
            chunk.get("journal"),
            chunk.get("section"),
            chunk.get("text"),
        ]
    )
    answer_norm = _normalize_phrase_text(answer)
    support_norm = _normalize_phrase_text(f"{evidence_pack.get('question', '')} {evidence_text}")
    unsupported = []
    for label, variants in OVER_SPECIFIC_PHRASES.items():
        if any(_contains_phrase(answer_norm, variant) for variant in variants) and not any(
            _contains_phrase(support_norm, variant) for variant in variants
        ):
            unsupported.append(label)
    if _contains_phrase(answer_norm, "rct") and not _contains_phrase(support_norm, "rct"):
        unsupported.append("RCT")
    return unsupported


def unsupported_cross_source_combinations(answer: str, evidence_pack: dict) -> list[str]:
    answer_norm = _normalize_phrase_text(answer)
    unsupported = []
    for left, right in UNSUPPORTED_COOCCURRENCE_PAIRS:
        if not (_contains_phrase(answer_norm, left) and _contains_phrase(answer_norm, right)):
            continue
        if not any(_chunk_supports_pair(chunk, left, right) for chunk in evidence_pack.get("chunks", [])):
            unsupported.append(f"{left} + {right}")
    return unsupported


def unsupported_intervention_overclaims(answer: str) -> list[str]:
    unsupported = []
    for line in answer.splitlines():
        if "?" in line:
            continue
        lowered = line.lower()
        if any(term in lowered for term in ["missing", "needed", "need", "assess", "test", "trial"]):
            continue
        for pattern in INTERVENTION_OVERCLAIM_PATTERNS:
            if re.search(pattern, line, flags=re.IGNORECASE):
                unsupported.append(_clean_warning_text(line))
                break
    return unsupported


def _chunk_supports_pair(chunk: dict, left: str, right: str) -> bool:
    text = _normalize_phrase_text(
        " ".join(
            str(chunk.get(key) or "")
            for key in ["citation_label", "title", "journal", "section", "text"]
        )
    )
    return _contains_phrase(text, left) and _contains_phrase(text, right)


def _strip_markdown_headings(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _technical_terms(text: str) -> list[str]:
    terms = []
    for match in re.finditer(r"\b[A-Za-z][A-Za-z0-9αβγδκλ\-]{1,}\b", text):
        value = match.group(0)
        if value.startswith("E") and value[1:].isdigit():
            continue
        if _looks_technical(value):
            terms.append(value)
    return terms


def _looks_technical(value: str) -> bool:
    if value in COMMON_TECHNICAL_WORDS:
        return True
    has_upper_after_first = any(char.isupper() for char in value[1:])
    has_digit = any(char.isdigit() for char in value)
    has_greek = any(char in value for char in "αβγδκλ")
    has_hyphen = "-" in value
    if has_hyphen and not has_digit and not has_greek and _looks_like_plain_hyphen_word(value):
        return False
    return has_upper_after_first or has_digit or has_greek or has_hyphen


def _looks_like_plain_hyphen_word(value: str) -> bool:
    parts = [part for part in value.split("-") if part]
    if not parts:
        return False
    return not any(part.isupper() for part in parts)


def _normalize_term(value: str) -> str:
    normalized = value.lower().replace("-", "")
    return (
        normalized.replace("α", "alpha")
        .replace("β", "beta")
        .replace("γ", "gamma")
        .replace("δ", "delta")
        .replace("κ", "kappa")
        .replace("λ", "lambda")
    )


def _normalize_phrase_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower().replace("-", " ")).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_phrase = _normalize_phrase_text(phrase)
    return re.search(rf"(?<!\w){re.escape(normalized_phrase)}(?!\w)", text) is not None


def _clean_warning_text(text: str) -> str:
    clean = re.sub(r"[*_`]+", "", text)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()[:180]
