from __future__ import annotations

from pfempire.trace.schemas import EvidenceBlueprint, TraceCandidate
from pfempire.trace.flow import compute_evidence_flow
from pfempire.trace.utility import compute_utility


COMPONENTS = ["facet", "scale", "independence_normalized", "polarity", "relevance_quality"]


def paper_counterfactual_contributions(
    candidates: list[TraceCandidate], blueprint: EvidenceBlueprint, dependence: dict[tuple[str, str], float], weights: dict[str, float] | None = None
) -> list[dict]:
    fixed_n_eff_max = max(1, len({candidate.paper_id for candidate in candidates}))
    full = compute_utility(candidates, blueprint, dependence, weights, n_eff_max=fixed_n_eff_max)
    rows = []
    for paper_id in sorted({candidate.paper_id for candidate in candidates}):
        reduced = [candidate for candidate in candidates if candidate.paper_id != paper_id]
        without = compute_utility(reduced, blueprint, dependence, weights, n_eff_max=fixed_n_eff_max)
        rows.append({
            "paper_id": paper_id,
            "utility_contribution": full["total"] - without["total"],
            "facet_contribution": full["facet"]["total"] - without["facet"]["total"],
            "scale_contribution": full["scale"]["total"] - without["scale"]["total"],
            "independence_contribution": full["independence_normalized"] - without["independence_normalized"],
            "polarity_contribution": full["polarity"]["total"] - without["polarity"]["total"],
            "relevance_contribution": full["relevance_quality"] - without["relevance_quality"],
        })
    return rows


def paper_flow_contributions(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    *,
    lineage_capacity: float = 1.0,
    facet_threshold: float = 0.60,
    minimum_papers: int = 0,
    decision_trace: list[dict] | None = None,
) -> list[dict]:
    """Explain TRACE-Flow selections in the same constraint space used to select them."""

    full = compute_evidence_flow(
        candidates,
        blueprint,
        lineage_capacity=lineage_capacity,
        facet_threshold=facet_threshold,
    )
    reasons: dict[str, list[str]] = {}
    for decision in decision_trace or []:
        reasons.setdefault(str(decision.get("paper_id")), []).append(str(decision.get("reason")))
    rows = []
    full_paper_count = len({candidate.paper_id for candidate in candidates})
    for paper_id in sorted({candidate.paper_id for candidate in candidates}):
        paper_candidates = [candidate for candidate in candidates if candidate.paper_id == paper_id]
        reduced = [candidate for candidate in candidates if candidate.paper_id != paper_id]
        without = compute_evidence_flow(
            reduced,
            blueprint,
            lineage_capacity=lineage_capacity,
            facet_threshold=facet_threshold,
        )
        rows.append(
            {
                "paper_id": paper_id,
                "flow_coverage_contribution": full.normalized_coverage - without.normalized_coverage,
                "bottleneck_contribution": full.bottleneck_coverage - without.bottleneck_coverage,
                "facets_lost_without_paper": ";".join(
                    sorted(set(without.unresolved_facets) - set(full.unresolved_facets))
                ),
                "active_lineage_contribution": len(full.active_lineages) - len(without.active_lineages),
                "breadth_constraint_contribution": int(
                    full_paper_count >= minimum_papers > full_paper_count - 1
                ),
                "selection_reason": ";".join(sorted(set(reasons.get(paper_id, [])))) or "counterfactual_only",
                "token_cost": sum(candidate.token_cost for candidate in paper_candidates),
            }
        )
    return rows
