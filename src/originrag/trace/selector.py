from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from originrag.trace.schemas import EvidenceBlueprint, TraceCandidate, TraceSelectionResult
from originrag.trace.utility import compute_utility


@dataclass(frozen=True)
class SelectorConfig:
    token_budget: int = 8000
    max_chunks_per_paper: int = 2
    max_publication_versions_per_family: int = 1
    minimum_marginal_gain: float = 0.002
    minimum_selected_papers: int = 0
    selector_algorithm: str = "lazy_greedy"
    facet_coverage_threshold: float = 0.60
    flow_lineage_capacity: float = 1.0
    flow_anchor_papers: int = 3
    flow_minimum_papers: int = 14
    flow_reserve_relevance_fraction: float = 0.80

    def __post_init__(self) -> None:
        integer_fields = {
            "token_budget": self.token_budget,
            "max_chunks_per_paper": self.max_chunks_per_paper,
            "max_publication_versions_per_family": self.max_publication_versions_per_family,
            "minimum_selected_papers": self.minimum_selected_papers,
            "flow_anchor_papers": self.flow_anchor_papers,
            "flow_minimum_papers": self.flow_minimum_papers,
        }
        for name, value in integer_fields.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        float_fields = {
            "minimum_marginal_gain": self.minimum_marginal_gain,
            "facet_coverage_threshold": self.facet_coverage_threshold,
            "flow_lineage_capacity": self.flow_lineage_capacity,
            "flow_reserve_relevance_fraction": self.flow_reserve_relevance_fraction,
        }
        for name, value in float_fields.items():
            try:
                finite = math.isfinite(float(value))
            except (TypeError, ValueError):
                finite = False
            if not finite:
                raise ValueError(f"{name} must be finite")
        if self.token_budget <= 0:
            raise ValueError("token_budget must be greater than zero")
        if self.max_chunks_per_paper <= 0:
            raise ValueError("max_chunks_per_paper must be greater than zero")
        if self.max_publication_versions_per_family <= 0:
            raise ValueError("max_publication_versions_per_family must be greater than zero")
        if self.minimum_marginal_gain < 0:
            raise ValueError("minimum_marginal_gain must be nonnegative")
        if self.minimum_selected_papers < 0:
            raise ValueError("minimum_selected_papers must be nonnegative")
        if self.selector_algorithm not in {"lazy_greedy", "beam_search"}:
            raise ValueError("selector_algorithm must be 'lazy_greedy' or 'beam_search'")
        if not 0.0 <= self.facet_coverage_threshold <= 1.0:
            raise ValueError("facet_coverage_threshold must be between zero and one")
        if self.flow_lineage_capacity <= 0:
            raise ValueError("flow_lineage_capacity must be greater than zero")
        if self.flow_anchor_papers < 0:
            raise ValueError("flow_anchor_papers must be nonnegative")
        if self.flow_minimum_papers < 0:
            raise ValueError("flow_minimum_papers must be nonnegative")
        if not 0.0 <= self.flow_reserve_relevance_fraction <= 1.0:
            raise ValueError("flow_reserve_relevance_fraction must be between zero and one")


def select_evidence(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    dependence: dict[tuple[str, str], float] | None = None,
    *,
    selector_name: str = "trace_full",
    config: SelectorConfig | None = None,
    utility_weights: dict[str, float] | None = None,
) -> TraceSelectionResult:
    validate_candidate_identity(candidates)
    config = config or SelectorConfig()
    dependence = dependence or {}
    if selector_name == "topk_relevance":
        selected, reason = topk(candidates, config)
    elif selector_name == "mmr":
        selected, reason = mmr(candidates, config)
    elif selector_name in {"trace_flow", "trace_flow_no_anchor", "trace_flow_unbounded", "trace_flow_relevance_reserve", "trace_flow_diverse_reserve"}:
        from originrag.trace.flow_selector import select_flow_evidence

        flow_selection = select_flow_evidence(
            candidates,
            blueprint,
            config,
            dependence=dependence,
            anchors_enabled=selector_name != "trace_flow_no_anchor",
            lineage_capacity_enabled=selector_name != "trace_flow_unbounded",
            diverse_reserve_enabled=selector_name == "trace_flow_diverse_reserve",
        )
        selected, reason = flow_selection.selected, flow_selection.stopping_reason
    elif selector_name in {"trace_full", "trace_no_lineage"}:
        if config.selector_algorithm == "beam_search":
            selected, reason = beam_search_trace(
                candidates, blueprint, dependence, config, utility_weights,
                include_lineage=selector_name == "trace_full",
            )
        else:
            selected, reason = greedy_trace(
                candidates, blueprint, dependence, config, utility_weights,
                include_lineage=selector_name == "trace_full",
            )
    else:
        raise ValueError(f"Unsupported TRACE selector: {selector_name}")
    details = compute_utility(
        selected, blueprint, dependence, utility_weights,
        n_eff_max=max(1, len({candidate.paper_id for candidate in candidates})),
        include_lineage=selector_name != "trace_no_lineage",
    )
    independence = details["independence"]
    run_config = {
        "stopping_reason": reason,
        "token_budget": config.token_budget,
        "max_chunks_per_paper": config.max_chunks_per_paper,
        "max_publication_versions_per_family": config.max_publication_versions_per_family,
        "minimum_marginal_gain": config.minimum_marginal_gain,
        "minimum_selected_papers": config.minimum_selected_papers,
        "algorithm": config.selector_algorithm,
        "utility_details": details,
    }
    if selector_name in {"trace_flow", "trace_flow_no_anchor", "trace_flow_unbounded", "trace_flow_relevance_reserve", "trace_flow_diverse_reserve"}:
        for index, row in enumerate(flow_selection.decision_trace, 1):
            row["step"] = index
        run_config.update(
            {
                "algorithm": "lineage_capacitated_max_min_flow",
                "selection_objective": "lexicographic_constraints_not_weighted_utility",
                "flow": flow_selection.flow.model_dump(mode="json"),
                "decision_trace": flow_selection.decision_trace,
                "flow_anchor_papers": config.flow_anchor_papers,
                "flow_minimum_papers": config.flow_minimum_papers,
                "flow_reserve_strategy": (
                    "complementarity" if selector_name == "trace_flow_diverse_reserve" else "relevance"
                ),
            }
        )
    return TraceSelectionResult(
        question_id=blueprint.question_id,
        selector_name=selector_name,
        selected_candidates=selected,
        utility_total=details["total"],
        facet_coverage=details["facet"]["total"],
        scale_triangulation=details["scale"]["total"],
        polarity_coverage=details["polarity"]["total"],
        relevance_quality=details["relevance_quality"],
        effective_independent_evidence=independence["n_eff"],
        raw_paper_count=independence["raw_paper_count"],
        lineage_count=independence["evidence_lineage_count"],
        token_cost=sum(candidate.token_cost for candidate in selected),
        unresolved_facets=(
            flow_selection.flow.unresolved_facets
            if selector_name in {"trace_flow", "trace_flow_no_anchor", "trace_flow_unbounded", "trace_flow_relevance_reserve", "trace_flow_diverse_reserve"}
            else details["facet"]["unresolved_required"]
        ),
        constraint_violations=constraint_violations(selected, config),
        run_config=run_config,
    )


def greedy_trace(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    dependence: dict[tuple[str, str], float],
    config: SelectorConfig,
    utility_weights: dict[str, float] | None,
    *,
    include_lineage: bool,
) -> tuple[list[TraceCandidate], str]:
    selected: list[TraceCandidate] = []
    remaining = list(candidates)
    current = compute_utility(selected, blueprint, dependence, utility_weights, include_lineage=include_lineage)
    while remaining:
        valid = [candidate for candidate in remaining if can_add(candidate, selected, config)]
        if not valid:
            return selected, "constraint_limited" if selected else "no_valid_candidates"
        scored = []
        for candidate in valid:
            proposed = compute_utility(
                [*selected, candidate], blueprint, dependence, utility_weights,
                n_eff_max=max(1, len({c.paper_id for c in candidates})), include_lineage=include_lineage,
            )
            marginal = proposed["total"] - current["total"]
            facet_gain = proposed["facet"]["total"] - current["facet"]["total"]
            neff_gain = (
                proposed["independence"]["n_eff"] - current["independence"]["n_eff"]
                if include_lineage
                else 0.0
            )
            scored.append((marginal / candidate.token_cost, marginal, facet_gain, neff_gain, candidate, proposed))
        scored.sort(key=lambda item: candidate_tie_key(*item), reverse=True)
        _, marginal, _, _, candidate, proposed = scored[0]
        if marginal < config.minimum_marginal_gain:
            return selected, "marginal_gain_below_threshold"
        selected.append(candidate)
        remaining.remove(candidate)
        current = proposed
        if selection_is_sufficient(
            selected,
            current,
            blueprint,
            config,
            include_lineage=include_lineage,
        ):
            return selected, "all_required_facets_covered"
        if sum(item.token_cost for item in selected) >= config.token_budget:
            return selected, "budget_exhausted"
    return selected, "no_valid_candidates"


def topk(candidates: list[TraceCandidate], config: SelectorConfig) -> tuple[list[TraceCandidate], str]:
    ordered = sorted(candidates, key=lambda c: (-c.retrieval_score, c.paper_id, c.chunk_id))
    selected = []
    for candidate in ordered:
        if can_add(candidate, selected, config, enforce_family=False):
            selected.append(candidate)
    reason = "budget_exhausted" if sum(c.token_cost for c in selected) >= config.token_budget else "no_valid_candidates"
    return selected, reason


def beam_search_trace(
    candidates: list[TraceCandidate], blueprint: EvidenceBlueprint,
    dependence: dict[tuple[str, str], float], config: SelectorConfig,
    utility_weights: dict[str, float] | None, *, include_lineage: bool, beam_width: int = 4,
) -> tuple[list[TraceCandidate], str]:
    ordered = sorted(candidates, key=lambda c: (-c.retrieval_score, c.paper_id, c.chunk_id))
    beam: list[list[TraceCandidate]] = [[]]
    best: list[TraceCandidate] = []
    best_score = -1.0
    for _ in range(len(ordered)):
        expansions = []
        for state in beam:
            used = {candidate.chunk_id for candidate in state}
            for candidate in ordered:
                if candidate.chunk_id in used or not can_add(candidate, state, config):
                    continue
                proposed = [*state, candidate]
                details = compute_utility(
                    proposed, blueprint, dependence, utility_weights,
                    n_eff_max=max(1, len({c.paper_id for c in candidates})), include_lineage=include_lineage,
                )
                expansions.append((details["total"], proposed, details))
        if not expansions:
            break
        expansions.sort(key=lambda item: (item[0], tuple(invert_string(c.chunk_id) for c in item[1])), reverse=True)
        beam = [state for _, state, _ in expansions[:beam_width]]
        score, state, details = expansions[0]
        if score > best_score:
            best_score, best = score, state
        if selection_is_sufficient(
            best,
            details,
            blueprint,
            config,
            include_lineage=include_lineage,
        ):
            return best, "all_required_facets_covered"
    return best, "budget_exhausted" if sum(c.token_cost for c in best) >= config.token_budget else "no_valid_candidates"


def exhaustive_trace_for_tiny_fixture(
    candidates: list[TraceCandidate], blueprint: EvidenceBlueprint,
    dependence: dict[tuple[str, str], float], config: SelectorConfig,
) -> list[TraceCandidate]:
    if len(candidates) > 20:
        raise ValueError("Exhaustive TRACE search is restricted to at most 20 synthetic candidates.")
    import itertools
    best: list[TraceCandidate] = []
    best_score = -1.0
    for size in range(len(candidates) + 1):
        for subset in itertools.combinations(candidates, size):
            selected = list(subset)
            if constraint_violations(selected, config):
                continue
            score = compute_utility(selected, blueprint, dependence)["total"]
            if score > best_score:
                best_score, best = score, selected
    return best


def mmr(candidates: list[TraceCandidate], config: SelectorConfig, lambda_relevance: float = 0.70) -> tuple[list[TraceCandidate], str]:
    selected: list[TraceCandidate] = []
    remaining = list(candidates)
    max_score = max((candidate.retrieval_score for candidate in candidates), default=1.0) or 1.0
    while remaining:
        valid = [candidate for candidate in remaining if can_add(candidate, selected, config)]
        if not valid:
            break
        ranked = []
        for candidate in valid:
            redundancy = max((candidate_similarity(candidate, other) for other in selected), default=0.0)
            score = lambda_relevance * candidate.retrieval_score / max_score - (1.0 - lambda_relevance) * redundancy
            ranked.append((score, candidate))
        ranked.sort(key=lambda item: (item[0], item[1].retrieval_score, invert_string(item[1].paper_id), invert_string(item[1].chunk_id)), reverse=True)
        chosen = ranked[0][1]
        selected.append(chosen)
        remaining.remove(chosen)
    return selected, "budget_exhausted" if sum(c.token_cost for c in selected) >= config.token_budget else "no_valid_candidates"


def can_add(candidate: TraceCandidate, selected: list[TraceCandidate], config: SelectorConfig, *, enforce_family: bool = True) -> bool:
    if sum(item.token_cost for item in selected) + candidate.token_cost > config.token_budget:
        return False
    if sum(item.paper_id == candidate.paper_id for item in selected) >= config.max_chunks_per_paper:
        return False
    family = candidate.publication_family_id
    if enforce_family and family:
        family_papers = {
            item.paper_id for item in selected if item.publication_family_id == family
        }
        if (
            candidate.paper_id not in family_papers
            and len(family_papers) >= config.max_publication_versions_per_family
        ):
            return False
    return True


def all_required_covered(details: dict, blueprint: EvidenceBlueprint, config: SelectorConfig) -> bool:
    required = [facet.facet_id for facet in blueprint.required_facets if facet.is_required]
    return bool(required) and all(details["facet"]["per_facet"].get(facet_id, 0.0) >= config.facet_coverage_threshold for facet_id in required)


def selection_is_sufficient(
    selected: list[TraceCandidate],
    details: dict,
    blueprint: EvidenceBlueprint,
    config: SelectorConfig,
    *,
    include_lineage: bool = True,
) -> bool:
    paper_count = len({candidate.paper_id for candidate in selected})
    return (
        paper_count >= max(0, config.minimum_selected_papers)
        and all_required_covered(details, blueprint, config)
        and (
            not include_lineage
            or details["independence"]["n_eff"] >= blueprint.minimum_independent_lineages
        )
    )


def constraint_violations(candidates: list[TraceCandidate], config: SelectorConfig) -> list[str]:
    violations = []
    if sum(candidate.token_cost for candidate in candidates) > config.token_budget:
        violations.append("token_budget_exceeded")
    counts = Counter(candidate.paper_id for candidate in candidates)
    if any(count > config.max_chunks_per_paper for count in counts.values()):
        violations.append("max_chunks_per_paper_exceeded")
    return violations


def validate_candidate_identity(candidates: list[TraceCandidate]) -> None:
    chunk_ids = [candidate.chunk_id for candidate in candidates]
    duplicate_chunk_ids = sorted(
        chunk_id for chunk_id, count in Counter(chunk_ids).items() if count > 1
    )
    if duplicate_chunk_ids:
        preview = ", ".join(duplicate_chunk_ids[:5])
        raise ValueError(f"candidate chunk_id values must be unique; duplicates: {preview}")


def candidate_tie_key(rate: float, marginal: float, facet: float, neff: float, candidate: TraceCandidate, _: dict) -> tuple:
    return (rate, marginal, facet, neff, candidate.retrieval_score, invert_string(candidate.paper_id), invert_string(candidate.chunk_id))


def candidate_similarity(a: TraceCandidate, b: TraceCandidate) -> float:
    if a.paper_id == b.paper_id:
        return 1.0
    tokens_a, tokens_b = set(a.text.lower().split()), set(b.text.lower().split())
    lexical = len(tokens_a & tokens_b) / max(1, len(tokens_a | tokens_b))
    if a.publication_family_id and a.publication_family_id == b.publication_family_id:
        return max(0.95, lexical)
    return lexical


def invert_string(value: str) -> tuple[int, ...]:
    return tuple(-ord(char) for char in value)
