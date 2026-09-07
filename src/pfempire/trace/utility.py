from __future__ import annotations

import math

from pfempire.trace.evidence_weights import effective_independent_evidence, normalized_relevance
from pfempire.trace.facets import facet_coverage
from pfempire.trace.schemas import EvidenceBlueprint, TraceCandidate


DEFAULT_UTILITY_WEIGHTS = {
    "alpha_facet_coverage": 0.30,
    "beta_scale_triangulation": 0.20,
    "gamma_independent_evidence": 0.25,
    "delta_polarity_coverage": 0.10,
    "eta_relevance_quality": 0.15,
}


def saturating_coverage(candidates: list[TraceCandidate], attribute: str, keys: list[str]) -> dict:
    if not keys:
        return {"total": 1.0, "per_key": {}}
    per_key = {}
    for key in keys:
        score_sum = sum(getattr(candidate, attribute).get(key, 0.0) for candidate in candidates)
        per_key[key] = 1.0 - math.exp(-score_sum)
    return {"total": sum(per_key.values()) / len(keys), "per_key": per_key}


def relevance_quality(candidates: list[TraceCandidate]) -> float:
    if not candidates:
        return 0.0
    by_paper: dict[str, float] = {}
    for candidate in candidates:
        value = normalized_relevance(candidate.retrieval_score) * candidate.section_quality
        by_paper[candidate.paper_id] = max(by_paper.get(candidate.paper_id, 0.0), value)

    return 1.0 - math.exp(-sum(by_paper.values()) / max(1.0, min(3.0, len(by_paper))))


def compute_utility(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    dependence: dict[tuple[str, str], float],
    weights: dict[str, float] | None = None,
    n_eff_max: float | None = None,
    include_lineage: bool = True,
) -> dict:
    weights = {**DEFAULT_UTILITY_WEIGHTS, **(weights or {})}
    facets = facet_coverage(candidates, blueprint.required_facets)
    scales = saturating_coverage(candidates, "scale_scores", blueprint.preferred_scales)
    polarities = saturating_coverage(candidates, "polarity_scores", blueprint.required_polarities)
    independence = effective_independent_evidence(candidates, dependence)
    maximum = max(1.0, float(n_eff_max or max(blueprint.minimum_independent_lineages, independence["raw_paper_count"])))
    independence_normalized = math.log1p(float(independence["n_eff"])) / math.log1p(maximum)
    if not include_lineage:
        independence_normalized = 0.0
    relevance = relevance_quality(candidates)
    total = (
        weights["alpha_facet_coverage"] * facets["total"]
        + weights["beta_scale_triangulation"] * scales["total"]
        + weights["gamma_independent_evidence"] * independence_normalized
        + weights["delta_polarity_coverage"] * polarities["total"]
        + weights["eta_relevance_quality"] * relevance
    )
    return {
        "total": min(1.0, max(0.0, total)),
        "facet": facets,
        "scale": scales,
        "polarity": polarities,
        "independence": independence,
        "independence_normalized": independence_normalized,
        "relevance_quality": relevance,
        "weights": weights,
    }
