from __future__ import annotations

import math
from collections import defaultdict, deque

from pfempire.trace.schemas import EvidenceBlueprint, EvidenceFlowResult, TraceCandidate
from pfempire.trace.independence_units import paper_independence_units


EPSILON = 1e-9
MAX_EXACT_SUBSET_FACETS = 12


def compute_evidence_flow(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    *,
    lineage_capacity: float = 1.0,
    facet_threshold: float = 0.60,
    unit_by_paper: dict[str, str] | None = None,
) -> EvidenceFlowResult:
    """Compute a fair, lineage-capacitated flow to required scientific facets.

    Each study lineage receives one shared capacity budget. Evidence from several
    papers or chunks in that lineage therefore cannot be counted independently.
    A max-min feasibility search prevents easy facets from consuming all flow
    while a critical facet remains uncovered.
    """

    required = [facet for facet in blueprint.required_facets if facet.is_required]
    demands = {
        facet.facet_id: facet_threshold * max(1, int(facet.minimum_items))
        for facet in required
    }
    total_demand = sum(demands.values())
    if not demands:
        return EvidenceFlowResult(
            total_flow=0.0,
            total_demand=0.0,
            normalized_coverage=1.0,
            bottleneck_coverage=1.0,
            lineage_capacity=lineage_capacity,
        )

    facets_by_id = {facet.facet_id: facet for facet in required}
    independence_units = (
        _validate_unit_mapping(candidates, unit_by_paper)
        if unit_by_paper is not None
        else paper_independence_units(candidates)
    )
    edge_capacities: dict[tuple[str, str], float] = defaultdict(float)
    for candidate in candidates:
        lineage = independence_units[candidate.paper_id]
        quality = evidence_quality(candidate)
        for facet_id in demands:
            facet = facets_by_id[facet_id]
            contribution = (
                bounded(candidate.facet_scores.get(facet_id, 0.0))
                * quality
                * role_compatibility(
                    candidate,
                    facet.biological_scale,
                    facet.evidence_type,
                    facet.polarity_requirement,
                )
            )

            edge_capacities[(lineage, facet_id)] = max(
                edge_capacities[(lineage, facet_id)], contribution
            )

    lineages = sorted(
        {
            lineage
            for (lineage, _), capacity in edge_capacities.items()
            if capacity > EPSILON
        }
    )
    effective_lineage_capacity = max(EPSILON, lineage_capacity)
    if len(demands) <= MAX_EXACT_SUBSET_FACETS:
        ratio = _max_min_ratio_by_subsets(
            lineages,
            edge_capacities,
            demands,
            effective_lineage_capacity,
        )
    else:
        ratio = _max_min_ratio_by_flow_search(
            lineages,
            edge_capacities,
            demands,
            effective_lineage_capacity,
        )
    value, facet_flow, lineage_flow = _run_flow(
        lineages,
        edge_capacities,
        demands,
        effective_lineage_capacity,
        ratio=ratio,
        maximize_total=ratio < 1.0 - EPSILON,
    )

    per_facet = {
        facet_id: bounded(facet_flow.get(facet_id, 0.0) / max(EPSILON, demand))
        for facet_id, demand in demands.items()
    }
    unresolved = sorted(
        facet_id for facet_id, coverage in per_facet.items() if coverage < 1.0 - 1e-6
    )
    return EvidenceFlowResult(
        total_flow=value,
        total_demand=total_demand,
        normalized_coverage=bounded(value / max(EPSILON, total_demand)),
        bottleneck_coverage=min(per_facet.values(), default=1.0),
        facet_flow=facet_flow,
        facet_demand=demands,
        facet_coverage=per_facet,
        lineage_outflow=lineage_flow,
        active_lineages=sorted(lineage for lineage, value in lineage_flow.items() if value > EPSILON),
        unresolved_facets=unresolved,
        lineage_capacity=lineage_capacity,
    )


def evidence_quality(candidate: TraceCandidate) -> float:
    """Quality gates flow without allowing retrieval rank to certify a claim."""

    section = bounded(candidate.section_quality)
    flag_penalty = min(0.60, 0.12 * len(candidate.quality_flags))
    return bounded(section * (1.0 - flag_penalty))


def _max_min_ratio_by_subsets(
    lineages: list[str],
    edge_capacities: dict[tuple[str, str], float],
    demands: dict[str, float],
    lineage_capacity: float,
) -> float:
    """Solve max-min facet satisfaction via the generalized Hall inequalities."""

    facet_ids = sorted(demands)
    subset_count = 1 << len(facet_ids)
    subset_demand = [0.0] * subset_count
    for mask in range(1, subset_count):
        least_bit = mask & -mask
        facet_index = least_bit.bit_length() - 1
        subset_demand[mask] = (
            subset_demand[mask ^ least_bit] + demands[facet_ids[facet_index]]
        )

    subset_supply = [0.0] * subset_count
    for lineage in lineages:
        subset_edge_capacity = [0.0] * subset_count
        capacities = [
            edge_capacities.get((lineage, facet_id), 0.0)
            for facet_id in facet_ids
        ]
        for mask in range(1, subset_count):
            least_bit = mask & -mask
            facet_index = least_bit.bit_length() - 1
            subset_edge_capacity[mask] = (
                subset_edge_capacity[mask ^ least_bit] + capacities[facet_index]
            )
            subset_supply[mask] += min(
                lineage_capacity,
                subset_edge_capacity[mask],
            )

    ratio = 1.0
    for mask in range(1, subset_count):
        if subset_demand[mask] > EPSILON:
            ratio = min(ratio, subset_supply[mask] / subset_demand[mask])
    return bounded(ratio)


def _max_min_ratio_by_flow_search(
    lineages: list[str],
    edge_capacities: dict[tuple[str, str], float],
    demands: dict[str, float],
    lineage_capacity: float,
) -> float:
    """Fallback for blueprints too wide for exact facet-subset enumeration."""

    total_demand = sum(demands.values())
    low, high = 0.0, 1.0
    for _ in range(36):
        middle = (low + high) / 2.0
        value, _, _ = _run_flow(
            lineages,
            edge_capacities,
            demands,
            lineage_capacity,
            ratio=middle,
        )
        if value + EPSILON >= total_demand * middle:
            low = middle
        else:
            high = middle
    return low


def role_compatibility(
    candidate: TraceCandidate,
    biological_scale: str,
    evidence_type: str | None,
    polarity_requirement: str | None = None,
) -> float:
    """Strictly gate sufficiency flow by scientific role and required polarity."""

    scale = bounded(candidate.scale_scores.get(biological_scale, 0.0))
    kind = 1.0 if evidence_type in {None, "unknown"} else bounded(
        candidate.evidence_type_scores.get(str(evidence_type), 0.0)
    )
    polarity = (
        1.0
        if polarity_requirement in {None, "any", "unknown"}
        else bounded(candidate.polarity_scores.get(str(polarity_requirement), 0.0))
    )
    return math.sqrt(scale * kind) * polarity


def _run_flow(
    lineages: list[str],
    edge_capacities: dict[tuple[str, str], float],
    demands: dict[str, float],
    lineage_capacity: float,
    *,
    ratio: float,
    maximize_total: bool = False,
) -> tuple[float, dict[str, float], dict[str, float]]:
    source, sink = "__source__", "__sink__"
    capacity: dict[tuple[str, str], float] = {}
    for lineage in lineages:
        capacity[(source, f"L:{lineage}")] = lineage_capacity
    for (lineage, facet_id), value in edge_capacities.items():
        if value > EPSILON:
            capacity[(f"L:{lineage}", f"F:{facet_id}")] = value
    for facet_id, demand in demands.items():
        capacity[(f"F:{facet_id}", sink)] = demand * ratio

    value, residual = _edmonds_karp(source, sink, capacity)
    if maximize_total and ratio < 1.0:
        residual = defaultdict(float, residual)
        for facet_id, demand in demands.items():
            edge = (f"F:{facet_id}", sink)
            residual[edge] += demand * (1.0 - ratio)
            capacity[edge] = demand
        value += _augment_residual(source, sink, residual, capacity)
    facet_flow = {
        facet_id: capacity.get((f"F:{facet_id}", sink), 0.0)
        - residual.get((f"F:{facet_id}", sink), 0.0)
        for facet_id in demands
    }
    lineage_flow = {
        lineage: capacity.get((source, f"L:{lineage}"), 0.0)
        - residual.get((source, f"L:{lineage}"), 0.0)
        for lineage in lineages
    }
    return value, facet_flow, lineage_flow


def _edmonds_karp(
    source: str, sink: str, capacity: dict[tuple[str, str], float]
) -> tuple[float, dict[tuple[str, str], float]]:
    residual: dict[tuple[str, str], float] = defaultdict(float)
    for (start, end), value in capacity.items():
        residual[(start, end)] += max(0.0, value)

    total = _augment_residual(source, sink, residual, capacity)
    return total, dict(residual)


def _augment_residual(
    source: str,
    sink: str,
    residual: dict[tuple[str, str], float],
    capacity: dict[tuple[str, str], float],
) -> float:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for start, end in capacity:
        adjacency[start].add(end)
        adjacency[end].add(start)

    total = 0.0
    while True:
        parent: dict[str, str | None] = {source: None}
        queue = deque([source])
        while queue and sink not in parent:
            node = queue.popleft()
            for neighbor in sorted(adjacency[node]):
                if neighbor not in parent and residual[(node, neighbor)] > EPSILON:
                    parent[neighbor] = node
                    queue.append(neighbor)
        if sink not in parent:
            break
        increment = float("inf")
        node = sink
        while parent[node] is not None:
            previous = parent[node]
            increment = min(increment, residual[(previous, node)])
            node = previous
        node = sink
        while parent[node] is not None:
            previous = parent[node]
            residual[(previous, node)] -= increment
            residual[(node, previous)] += increment
            node = previous
        total += increment
    return total


def bounded(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _validate_unit_mapping(
    candidates: list[TraceCandidate],
    unit_by_paper: dict[str, str],
) -> dict[str, str]:
    """Validate an explicit evidence-unit partition used by robust analyses."""

    paper_ids = {candidate.paper_id for candidate in candidates}
    missing = sorted(paper_ids - set(unit_by_paper))
    if missing:
        preview = ", ".join(missing[:5])
        raise ValueError(f"unit_by_paper is missing candidate papers: {preview}")
    parsed = {
        paper_id: str(unit_by_paper[paper_id]).strip()
        for paper_id in paper_ids
    }
    empty = sorted(paper_id for paper_id, unit in parsed.items() if not unit)
    if empty:
        preview = ", ".join(empty[:5])
        raise ValueError(f"unit_by_paper contains empty unit IDs for: {preview}")
    return parsed
