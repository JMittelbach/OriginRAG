from __future__ import annotations

from collections import defaultdict

from originrag.trace.evidence_weights import effective_independent_evidence
from originrag.trace.flow import (
    compute_evidence_flow,
    evidence_quality,
    role_compatibility,
)
from originrag.trace.independence_units import paper_independence_units
from originrag.trace.schemas import EvidenceBlueprint, EvidenceFlowResult, EvidenceSufficiencyCertificate, TraceCandidate


def build_certificate(
    question_id: str,
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    dependence: dict[tuple[str, str], float],
    flow_details: EvidenceFlowResult | dict | None = None,
    minimum_required_papers: int = 0,
    selection_stopping_reason: str | None = None,
    lineage_graph_status: str = "not_assessed",
) -> EvidenceSufficiencyCertificate:
    flow = (
        flow_details
        if isinstance(flow_details, EvidenceFlowResult)
        else EvidenceFlowResult.model_validate(flow_details)
        if flow_details
        else compute_evidence_flow(candidates, blueprint)
    )
    independence = effective_independent_evidence(candidates, dependence)
    coverage_values = flow.facet_coverage
    coverage_total = flow.normalized_coverage
    covered = [
        key for key, value in coverage_values.items() if value >= 1.0 - 1e-6
    ]
    scales = sorted({key for candidate in candidates for key, value in candidate.scale_scores.items() if value >= 0.50})
    types = sorted({key for candidate in candidates for key, value in candidate.evidence_type_scores.items() if value >= 0.50 and key != "unknown"})
    polarity_lineages: dict[str, set[str]] = defaultdict(set)
    independence_units = paper_independence_units(candidates)
    for candidate in candidates:
        if not candidate_contributes_required_evidence(candidate, blueprint):
            continue
        lineage = independence_units[candidate.paper_id]
        for polarity, score in candidate.polarity_scores.items():
            if score >= 0.50 and polarity != "unknown":
                polarity_lineages[polarity].add(lineage)
    reasons = []
    unresolved = flow.unresolved_facets
    n_eff = float(independence["n_eff"])
    direct_present = any(
        candidate.section in {"results", "abstract"}
        and candidate_contributes_required_evidence(candidate, blueprint)
        for candidate in candidates
    )
    substantive_units = {
        unit
        for unit, weight in dict(independence.get("unit_weights") or {}).items()
        if float(weight) > 1e-12
    }
    flagged_units = {
        independence_units[candidate.paper_id]
        for candidate in candidates
        if candidate.quality_flags
        and independence_units[candidate.paper_id] in substantive_units
    }
    low_quality = len(flagged_units) / max(1, len(substantive_units))
    if unresolved:
        reasons.append("unresolved_required_facets")
    evidence_lineage_count = int(independence["evidence_lineage_count"])
    required_effective_evidence = 0.90 * blueprint.minimum_independent_lineages
    if evidence_lineage_count < blueprint.minimum_independent_lineages:
        reasons.append("insufficient_independent_evidence")
    elif n_eff + 1e-9 < required_effective_evidence:
        reasons.append("insufficient_effective_independent_evidence")
    if not direct_present:
        reasons.append("direct_evidence_absent")
    if low_quality > 0.35:
        reasons.append("too_many_low_quality_items")
    required_polarities_missing = sorted(
        {
            polarity
            for polarity in blueprint.required_polarities
            if polarity not in {"any", "unknown"}
            and not any(
                candidate.polarity_scores.get(polarity, 0.0) >= 0.50
                and candidate_contributes_required_evidence(candidate, blueprint)
                for candidate in candidates
            )
        }
    )
    facet_polarities_missing = sorted(
        f"{facet.facet_id}:{facet.polarity_requirement}"
        for facet in blueprint.required_facets
        if facet.is_required
        and facet.polarity_requirement not in {None, "any", "unknown"}
        and not any(
            candidate.facet_scores.get(facet.facet_id, 0.0) >= 0.50
            and candidate.polarity_scores.get(str(facet.polarity_requirement), 0.0)
            >= 0.50
            for candidate in candidates
        )
    )
    missing_polarities = [*required_polarities_missing, *facet_polarities_missing]
    if missing_polarities:
        reasons.append("required_polarity_missing")
    paper_floor_unmet = evidence_lineage_count < max(
        0, minimum_required_papers
    )
    if paper_floor_unmet:
        reasons.append("minimum_selected_papers_unmet")



    lineage_uncertain = lineage_graph_status not in {"reviewed", "not_applicable"}
    if lineage_uncertain:
        reasons.append(f"lineage_graph_{lineage_graph_status}")
    grade, claim = grade_certificate(
        coverage_total,
        unresolved,
        n_eff,
        direct_present,
        low_quality,
        missing_polarities,
        blueprint,
        evidence_lineage_count=evidence_lineage_count,
        effective_independence_unmet=(
            n_eff + 1e-9 < required_effective_evidence
        ),
        paper_floor_unmet=paper_floor_unmet,
        lineage_uncertain=lineage_uncertain,
    )
    if blueprint.review_status == "suggested":
        if grade == "good":
            grade = "usable"
        if "blueprint_not_human_reviewed" not in reasons:
            reasons.append("blueprint_not_human_reviewed")
        if claim == "direct_fact":
            claim = "association"
    return EvidenceSufficiencyCertificate(
        question_id=question_id,
        facet_coverage=coverage_total, required_facets_covered=covered, unresolved_facets=unresolved,
        biological_scales_covered=scales, evidence_types_covered=types,
        raw_paper_count=int(independence["raw_paper_count"]),
        substantive_paper_count=int(independence["evidence_paper_count"]),
        publication_family_count=int(independence["publication_family_count"]),
        evidence_lineage_count=evidence_lineage_count,
        effective_independent_evidence=n_eff,
        supporting_lineages=len(polarity_lineages["support"]), contradictory_lineages=len(polarity_lineages["contradiction"]),
        null_result_lineages=len(polarity_lineages["null_result"]), limitation_lineages=len(polarity_lineages["limitation"]),
        duplicate_lineage_rate=float(independence["duplicate_lineage_rate"]),
        independent_evidence_ratio=float(independence["independent_evidence_ratio"]),
        evidence_inflation_factor=float(independence["evidence_inflation_factor"]),
        minimum_required_papers=max(0, minimum_required_papers),
        selection_stopping_reason=selection_stopping_reason,
        lineage_graph_status=lineage_graph_status,
        evidence_sufficiency=grade, allowed_claim_strength=claim, reasons=reasons or ["meets_trace_thresholds"],
    )


def candidate_contributes_required_evidence(
    candidate: TraceCandidate,
    blueprint: EvidenceBlueprint,
) -> bool:
    quality = evidence_quality(candidate)
    return quality > 0.0 and any(
        facet.is_required
        and candidate.facet_scores.get(facet.facet_id, 0.0) >= 0.50
        and role_compatibility(
            candidate,
            facet.biological_scale,
            facet.evidence_type,
            facet.polarity_requirement,
        )
        > 0.0
        for facet in blueprint.required_facets
    )


def grade_certificate(
    coverage: float,
    unresolved: list[str],
    n_eff: float,
    direct: bool,
    low_quality: float,
    missing_polarities: list[str],
    blueprint: EvidenceBlueprint,
    *,
    evidence_lineage_count: int,
    effective_independence_unmet: bool,
    paper_floor_unmet: bool,
    lineage_uncertain: bool,
) -> tuple[str, str]:
    if blueprint.allowed_claim_strength_if_complete == "direct_fact" and unresolved:
        return "insufficient", "insufficient_evidence"
    if not direct or coverage < 0.40 or (n_eff < 1.0 and unresolved):
        return "insufficient", "insufficient_evidence"
    if (
        evidence_lineage_count < blueprint.minimum_independent_lineages
        or effective_independence_unmet
        or paper_floor_unmet
        or lineage_uncertain
        or missing_polarities
    ):
        return "weak", "hypothesis_only"
    if not unresolved and n_eff >= 3.0 and low_quality <= 0.20 and not missing_polarities:
        return "good", blueprint.allowed_claim_strength_if_complete
    if coverage >= 0.65 and n_eff >= 2.0 and low_quality <= 0.35 and not missing_polarities:
        return "usable", "association" if blueprint.allowed_claim_strength_if_complete != "direct_fact" else "direct_fact"
    return "weak", "hypothesis_only"


def format_certificate_markdown(certificate: EvidenceSufficiencyCertificate) -> str:
    unresolved = "\n".join(f"- {item}" for item in certificate.unresolved_facets) or "- none"
    return (
        "# TRACE Evidence Sufficiency\n\n"
        f"- Evidence accounting: {certificate.evidence_accounting_version}\n"
        f"- Facet coverage: {certificate.facet_coverage:.3f}\n"
        f"- Raw papers: {certificate.raw_paper_count}\n"
        f"- Substantive papers: {certificate.substantive_paper_count}\n"
        f"- Evidence lineages: {certificate.evidence_lineage_count}\n"
        f"- Effective independent evidence: {certificate.effective_independent_evidence:.3f}\n"
        f"- Duplicate lineage rate: {certificate.duplicate_lineage_rate:.3f}\n"
        f"- Independent evidence ratio: {certificate.independent_evidence_ratio:.3f}\n"
        f"- Evidence inflation factor: {certificate.evidence_inflation_factor:.3f}\n"
        f"- Lineage graph status: {certificate.lineage_graph_status}\n"
        f"- Selection stopping reason: {certificate.selection_stopping_reason or 'unknown'}\n"
        f"- Grade: {certificate.evidence_sufficiency}\n"
        f"- Allowed claim strength: {certificate.allowed_claim_strength}\n\n"
        f"## Unresolved facets\n\n{unresolved}\n"
    )
