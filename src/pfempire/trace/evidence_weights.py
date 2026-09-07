from __future__ import annotations

from collections import defaultdict

from pfempire.trace.dependence import dependence_between
from pfempire.trace.independence_units import paper_independence_units
from pfempire.trace.method_versions import EVIDENCE_ACCOUNTING_VERSION
from pfempire.trace.schemas import TraceCandidate


def chunk_evidence_quality(
    candidate: TraceCandidate,
    *,
    facet_ids: set[str] | None = None,
) -> float:
    relevance = normalized_relevance(candidate.retrieval_score)
    facet = (
        max(
            (candidate.facet_scores.get(facet_id, 0.0) for facet_id in facet_ids),
            default=0.0,
        )
        if facet_ids is not None
        else max(candidate.facet_scores.values(), default=0.0)
    )
    if facet < 0.50:
        return 0.0
    quality = min(1.0, max(0.0, candidate.section_quality))
    flag_penalty = min(0.6, 0.12 * len(candidate.quality_flags))
    substantive_quality = facet * (0.55 * relevance + 0.45 * quality)
    return min(1.0, max(0.0, substantive_quality - flag_penalty))


def aggregate_paper_weights(
    candidates: list[TraceCandidate],
    *,
    facet_ids: set[str] | None = None,
) -> dict[str, float]:
    weights: dict[str, float] = defaultdict(float)
    for candidate in candidates:
        weights[candidate.paper_id] = max(
            weights[candidate.paper_id],
            chunk_evidence_quality(candidate, facet_ids=facet_ids),
        )
    return dict(weights)


def effective_independent_evidence(
    candidates: list[TraceCandidate], dependence: dict[tuple[str, str], float]
) -> dict[str, str | float | int | dict[str, float]]:
    weights = aggregate_paper_weights(candidates)
    paper_lineages: dict[str, str] = {}
    paper_families: dict[str, str] = {}
    for candidate in candidates:
        lineage = candidate.study_lineage_id or candidate.paper_id
        previous = paper_lineages.setdefault(candidate.paper_id, lineage)
        if previous != lineage:
            raise ValueError(
                f"paper {candidate.paper_id!r} has conflicting study lineage IDs: "
                f"{previous!r} and {lineage!r}"
            )
        family = candidate.publication_family_id or candidate.paper_id
        previous_family = paper_families.setdefault(candidate.paper_id, family)
        if previous_family != family:
            raise ValueError(
                f"paper {candidate.paper_id!r} has conflicting publication family IDs: "
                f"{previous_family!r} and {family!r}"
            )
    independence_units = paper_independence_units(candidates)
    papers_by_unit: dict[str, list[str]] = defaultdict(list)
    for paper_id, unit in independence_units.items():
        papers_by_unit[unit].append(paper_id)
    unit_weights = {
        unit: max((weights[paper_id] for paper_id in papers), default=0.0)
        for unit, papers in papers_by_unit.items()
    }
    numerator = sum(unit_weights.values()) ** 2
    denominator = sum(weight * weight for weight in unit_weights.values())
    units = sorted(unit_weights)
    for index, unit_a in enumerate(units):
        for unit_b in units[index + 1 :]:
            correlation = max(
                (
                    dependence_between(paper_a, paper_b, dependence)
                    for paper_a in papers_by_unit[unit_a]
                    for paper_b in papers_by_unit[unit_b]
                ),
                default=0.0,
            )
            denominator += (
                2.0 * unit_weights[unit_a] * unit_weights[unit_b] * correlation
            )
    n_eff = numerator / denominator if denominator > 1e-12 else 0.0
    families = set(paper_families.values())
    lineages = {unit for unit, weight in unit_weights.items() if weight > 1e-12}
    raw_count = len(weights)
    evidence_paper_count = sum(weight > 1e-12 for weight in weights.values())
    duplicate_rate = 0.0 if raw_count == 0 else max(0.0, 1.0 - len(lineages) / raw_count)
    independent_ratio = 0.0 if raw_count == 0 else n_eff / raw_count
    inflation_factor = 0.0 if raw_count == 0 else raw_count / max(n_eff, 1e-12)
    n_eff = min(float(len(lineages)), max(0.0, n_eff))
    if abs(n_eff - round(n_eff)) <= 1e-12:
        n_eff = float(round(n_eff))
    return {
        "evidence_accounting_version": EVIDENCE_ACCOUNTING_VERSION,
        "n_eff": n_eff,
        "numerator": numerator,
        "denominator": denominator,
        "paper_weights": weights,
        "unit_weights": unit_weights,
        "raw_paper_count": raw_count,
        "evidence_paper_count": evidence_paper_count,
        "publication_family_count": len(families),
        "evidence_lineage_count": len(lineages),
        "duplicate_lineage_rate": duplicate_rate,
        "independent_evidence_ratio": min(1.0, max(0.0, independent_ratio)),
        "evidence_inflation_factor": max(1.0, inflation_factor) if raw_count else 0.0,
    }


def normalized_relevance(score: float) -> float:
    score = max(0.0, float(score))
    return score / (score + 10.0)
