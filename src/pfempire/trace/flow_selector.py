from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from pfempire.trace.evidence_weights import effective_independent_evidence
from pfempire.trace.flow import compute_evidence_flow, evidence_quality, role_compatibility
from pfempire.trace.independence_units import paper_independence_units
from pfempire.trace.schemas import EvidenceBlueprint, EvidenceFlowResult, TraceCandidate


class FlowSelectorConfig(Protocol):
    token_budget: int
    max_chunks_per_paper: int
    max_publication_versions_per_family: int
    facet_coverage_threshold: float
    flow_lineage_capacity: float
    flow_anchor_papers: int
    flow_minimum_papers: int
    flow_reserve_relevance_fraction: float


@dataclass
class FlowSelection:
    selected: list[TraceCandidate]
    stopping_reason: str
    flow: EvidenceFlowResult
    decision_trace: list[dict]


def select_flow_evidence(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    config: FlowSelectorConfig,
    *,
    dependence: dict[tuple[str, str], float] | None = None,
    anchors_enabled: bool = True,
    lineage_capacity_enabled: bool = True,
    diverse_reserve_enabled: bool = False,
) -> FlowSelection:
    """Lexicographic evidence selection with no weighted objective.

    Selection order is: retrieval anchors, hard-facet repair, required polarity,
    independent lineage count, then a minimum relevance floor. The evidence-flow
    solver shares a finite capacity across every publication in a study lineage.
    """

    selected: list[TraceCandidate] = []
    remaining = list(candidates)
    decisions: list[dict] = []
    dependence = dependence or {}
    enforce_effective_independence = lineage_capacity_enabled
    lineage_capacity = (
        config.flow_lineage_capacity
        if lineage_capacity_enabled
        else max(1.0, float(len(blueprint.required_facets) or 1))
    )

    if anchors_enabled:
        for candidate in retrieval_anchors(
            candidates,
            config.flow_anchor_papers,
            dependence=dependence,
        ):
            if candidate in remaining and can_add_flow(candidate, selected, config):
                if not preserves_repair_budget(
                    candidate,
                    selected,
                    remaining,
                    blueprint,
                    config.token_budget,
                ):
                    continue
                before = flow_for(selected, blueprint, config, lineage_capacity)
                selected.append(candidate)
                remaining.remove(candidate)
                after = flow_for(selected, blueprint, config, lineage_capacity)
                decisions.append(decision_row(candidate, "retrieval_anchor", before, after))

    current = flow_for(selected, blueprint, config, lineage_capacity)
    while remaining:
        if hard_requirements_met(
            selected,
            current,
            blueprint,
            config,
            dependence,
            enforce_effective_independence,
        ):
            return FlowSelection(selected, "flow_constraints_satisfied", current, decisions)

        valid = [candidate for candidate in remaining if can_add_flow(candidate, selected, config)]
        if not valid:
            reason = "budget_exhausted" if token_cost(selected) >= config.token_budget else "constraint_limited"
            return FlowSelection(selected, reason, current, decisions)

        repair_safe = [
            candidate
            for candidate in valid
            if preserves_repair_budget(
                candidate,
                selected,
                remaining,
                blueprint,
                config.token_budget,
            )
        ]
        if repair_safe:
            valid = repair_safe

        current_polarities = satisfied_required_polarities(selected, blueprint)
        current_lineages = substantive_lineages(selected, blueprint)
        current_neff = float(
            effective_independent_evidence(selected, dependence)["n_eff"]
        )
        scored = []
        for candidate in valid:
            proposed_candidates = [*selected, candidate]
            proposed = flow_for(proposed_candidates, blueprint, config, lineage_capacity)
            proposed_polarities = satisfied_required_polarities(proposed_candidates, blueprint)
            proposed_lineages = substantive_lineages(proposed_candidates, blueprint)
            proposed_neff = float(
                effective_independent_evidence(proposed_candidates, dependence)["n_eff"]
            )
            score = (
                len(current.unresolved_facets) - len(proposed.unresolved_facets),
                len(proposed_polarities) - len(current_polarities),
                proposed.bottleneck_coverage - current.bottleneck_coverage,
                proposed.normalized_coverage - current.normalized_coverage,
                int(
                    len(current_lineages) < blueprint.minimum_independent_lineages
                    and len(proposed_lineages) > len(current_lineages)
                ),
                proposed_neff - current_neff if enforce_effective_independence else 0.0,
                normalized_relevance(candidate),
                candidate.section_quality,
                -candidate.token_cost,
            )
            scored.append((score, candidate, proposed))
        scored.sort(
            key=lambda item: (
                *(-value for value in item[0]),
                item[1].paper_id,
                item[1].chunk_id,
            )
        )
        score, chosen, proposed = scored[0]
        constraint_progress = any(value > 1e-9 for value in score[:6])
        paper_floor_missing = len({candidate.paper_id for candidate in selected}) < config.flow_minimum_papers

        if not constraint_progress and not paper_floor_missing:
            return FlowSelection(selected, "unresolved_constraints_no_progress", current, decisions)
        if not constraint_progress and paper_floor_missing:
            chosen = reserve_candidate(
                valid,
                selected,
                blueprint,
                relevance_fraction=config.flow_reserve_relevance_fraction,
                diverse=diverse_reserve_enabled,
            )
            proposed = flow_for([*selected, chosen], blueprint, config, lineage_capacity)
        reason = (
            "constraint_repair"
            if constraint_progress
            else "diverse_reserve"
            if diverse_reserve_enabled
            else "relevance_floor"
        )
        selected.append(chosen)
        remaining.remove(chosen)
        decisions.append(decision_row(chosen, reason, current, proposed))
        current = proposed

    if hard_requirements_met(
        selected,
        current,
        blueprint,
        config,
        dependence,
        enforce_effective_independence,
    ):
        return FlowSelection(selected, "flow_constraints_satisfied", current, decisions)
    return FlowSelection(selected, "no_valid_candidates", current, decisions)


def retrieval_anchors(
    candidates: list[TraceCandidate],
    limit: int,
    *,
    dependence: dict[tuple[str, str], float] | None = None,
) -> list[TraceCandidate]:
    """Retain the strongest distinct-lineage retrieval anchors before set repair."""

    if limit <= 0:
        return []
    best_by_paper: dict[str, TraceCandidate] = {}
    for candidate in candidates:
        current = best_by_paper.get(candidate.paper_id)
        if current is None or anchor_key(candidate) < anchor_key(current):
            best_by_paper[candidate.paper_id] = candidate
    anchors: list[TraceCandidate] = []
    seen_lineages: set[str] = set()
    dependence = dependence or {}
    independence_units = paper_independence_units(candidates)
    for candidate in sorted(best_by_paper.values(), key=anchor_key):
        lineage = independence_units[candidate.paper_id]
        if lineage in seen_lineages:
            continue
        if any(
            max(
                float(dependence.get((candidate.paper_id, anchor.paper_id), 0.0)),
                float(dependence.get((anchor.paper_id, candidate.paper_id), 0.0)),
            )
            >= 0.50
            for anchor in anchors
        ):
            continue
        seen_lineages.add(lineage)
        anchors.append(candidate)
        if len(anchors) >= max(0, limit):
            break
    return anchors


def reserve_candidate(
    candidates: list[TraceCandidate],
    selected: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    *,
    relevance_fraction: float,
    diverse: bool,
) -> TraceCandidate:
    """Choose a complementary reserve paper above a strict relevance floor."""

    maximum = max((candidate.retrieval_score for candidate in candidates), default=0.0)
    threshold = maximum * min(1.0, max(0.0, relevance_fraction))
    eligible = [candidate for candidate in candidates if candidate.retrieval_score >= threshold]
    if not eligible:
        eligible = list(candidates)
    if not diverse:
        return sorted(
            eligible,
            key=lambda candidate: (
                -candidate.retrieval_score,
                -candidate.section_quality,
                candidate.token_cost,
                candidate.paper_id,
                candidate.chunk_id,
            ),
        )[0]

    covered_queries = {
        query
        for candidate in selected
        for query in candidate.metadata.get("raw_result", {}).get("contributing_queries", [])
    }
    covered_types = strong_keys(selected, "evidence_type_scores")
    covered_scales = strong_keys(selected, "scale_scores")
    preferred_types = set(blueprint.preferred_evidence_types)
    preferred_scales = set(blueprint.preferred_scales)

    def key(candidate: TraceCandidate) -> tuple:
        queries = set(candidate.metadata.get("raw_result", {}).get("contributing_queries", []))
        types = {name for name, score in candidate.evidence_type_scores.items() if score >= 0.50}
        scales = {name for name, score in candidate.scale_scores.items() if score >= 0.50}
        novelty = text_novelty(candidate, selected)
        direct_section = int(candidate.section in {"results", "discussion", "abstract"})
        return (
            -len(queries - covered_queries),
            -len((types & preferred_types) - covered_types),
            -len((scales & preferred_scales) - covered_scales),
            -direct_section,
            -novelty,
            -candidate.retrieval_score,
            -candidate.section_quality,
            candidate.token_cost,
            candidate.paper_id,
            candidate.chunk_id,
        )

    return sorted(eligible, key=key)[0]


def strong_keys(candidates: list[TraceCandidate], attribute: str) -> set[str]:
    return {
        name
        for candidate in candidates
        for name, score in getattr(candidate, attribute).items()
        if score >= 0.50 and name != "unknown"
    }


def text_novelty(candidate: TraceCandidate, selected: list[TraceCandidate]) -> float:
    candidate_tokens = set(re.findall(r"[a-z0-9]+", candidate.text.lower()))
    if not candidate_tokens or not selected:
        return 1.0
    similarities = []
    for other in selected:
        other_tokens = set(re.findall(r"[a-z0-9]+", other.text.lower()))
        union = candidate_tokens | other_tokens
        similarities.append(len(candidate_tokens & other_tokens) / max(1, len(union)))
    return 1.0 - max(similarities, default=0.0)


def anchor_key(candidate: TraceCandidate) -> tuple:
    raw = candidate.metadata.get("raw_result", {})
    query_breadth = len(raw.get("contributing_queries") or [])
    return (
        -int(bool(raw.get("explicit_named_study_anchor"))),
        -query_breadth,
        -candidate.retrieval_score,
        -candidate.section_quality,
        candidate.paper_id,
        candidate.chunk_id,
    )


def hard_requirements_met(
    selected: list[TraceCandidate],
    flow: EvidenceFlowResult,
    blueprint: EvidenceBlueprint,
    config: FlowSelectorConfig,
    dependence: dict[tuple[str, str], float],
    enforce_effective_independence: bool,
) -> bool:
    paper_count = len({candidate.paper_id for candidate in selected})
    n_eff = float(effective_independent_evidence(selected, dependence)["n_eff"])
    return (
        not flow.unresolved_facets
        and len(substantive_lineages(selected, blueprint))
        >= blueprint.minimum_independent_lineages
        and (
            not enforce_effective_independence
            or n_eff + 1e-9 >= 0.90 * blueprint.minimum_independent_lineages
        )
        and len(satisfied_required_polarities(selected, blueprint)) == len(required_polarities(blueprint))
        and paper_count >= config.flow_minimum_papers
    )


def required_polarities(blueprint: EvidenceBlueprint) -> set[str]:
    return {
        value
        for value in blueprint.required_polarities
        if value not in {"any", "unknown"}
    }


def satisfied_required_polarities(
    candidates: list[TraceCandidate], blueprint: EvidenceBlueprint
) -> set[str]:
    required = required_polarities(blueprint)
    satisfied: set[str] = set()
    for polarity in required:
        if any(
            candidate.polarity_scores.get(polarity, 0.0) >= 0.50
            and candidate_supports_required_facet(candidate, blueprint)
            for candidate in candidates
        ):
            satisfied.add(polarity)
    return satisfied


def candidate_supports_required_facet(
    candidate: TraceCandidate,
    blueprint: EvidenceBlueprint,
) -> bool:
    return any(
        facet.is_required
        and candidate.facet_scores.get(facet.facet_id, 0.0) >= 0.50
        and evidence_quality(candidate) > 0.0
        and role_compatibility(
            candidate,
            facet.biological_scale,
            facet.evidence_type,
            facet.polarity_requirement,
        )
        > 0.0
        for facet in blueprint.required_facets
    )


def substantive_lineages(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
) -> set[str]:
    independence_units = paper_independence_units(candidates)
    return {
        independence_units[candidate.paper_id]
        for candidate in candidates
        if candidate_supports_required_facet(candidate, blueprint)
    }


def preserves_repair_budget(
    candidate: TraceCandidate,
    selected: list[TraceCandidate],
    remaining: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    token_budget: int,
) -> bool:
    proposed = [*selected, candidate]
    remaining_budget = token_budget - token_cost(proposed)
    uncovered = [
        facet
        for facet in blueprint.required_facets
        if facet.is_required
        and not any(
            facet_contribution(item, facet) > 0.0
            for item in proposed
        )
    ]




    if not uncovered:
        return 0 <= remaining_budget
    required_budget = 0
    alternatives = [item for item in remaining if item != candidate]
    for facet in uncovered:
        costs = [
            item.token_cost
            for item in alternatives
            if facet_contribution(item, facet) > 0.0
        ]
        if costs:
            required_budget += min(costs)
    return required_budget <= remaining_budget


def facet_contribution(candidate: TraceCandidate, facet) -> float:
    return (
        candidate.facet_scores.get(facet.facet_id, 0.0)
        * evidence_quality(candidate)
        * role_compatibility(
            candidate,
            facet.biological_scale,
            facet.evidence_type,
            facet.polarity_requirement,
        )
    )


def flow_for(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    config: FlowSelectorConfig,
    lineage_capacity: float,
) -> EvidenceFlowResult:
    return compute_evidence_flow(
        candidates,
        blueprint,
        lineage_capacity=lineage_capacity,
        facet_threshold=config.facet_coverage_threshold,
    )


def can_add_flow(
    candidate: TraceCandidate,
    selected: list[TraceCandidate],
    config: FlowSelectorConfig,
) -> bool:
    if token_cost(selected) + candidate.token_cost > config.token_budget:
        return False
    if sum(item.paper_id == candidate.paper_id for item in selected) >= config.max_chunks_per_paper:
        return False
    family = candidate.publication_family_id
    if family:
        family_papers = {
            item.paper_id for item in selected if item.publication_family_id == family
        }
        if (
            candidate.paper_id not in family_papers
            and len(family_papers) >= config.max_publication_versions_per_family
        ):
            return False
    return True


def unique_lineages(candidates: list[TraceCandidate]) -> set[str]:
    return {candidate.study_lineage_id or candidate.paper_id for candidate in candidates}


def token_cost(candidates: list[TraceCandidate]) -> int:
    return sum(candidate.token_cost for candidate in candidates)


def normalized_relevance(candidate: TraceCandidate) -> float:
    score = max(0.0, float(candidate.retrieval_score))
    return score / (score + 10.0)


def decision_row(
    candidate: TraceCandidate,
    reason: str,
    before: EvidenceFlowResult,
    after: EvidenceFlowResult,
) -> dict:
    return {
        "step": 0,
        "chunk_id": candidate.chunk_id,
        "paper_id": candidate.paper_id,
        "study_lineage_id": candidate.study_lineage_id or candidate.paper_id,
        "reason": reason,
        "token_cost": candidate.token_cost,
        "flow_before": before.normalized_coverage,
        "flow_after": after.normalized_coverage,
        "bottleneck_before": before.bottleneck_coverage,
        "bottleneck_after": after.bottleneck_coverage,
        "resolved_facets": sorted(set(before.unresolved_facets) - set(after.unresolved_facets)),
    }
