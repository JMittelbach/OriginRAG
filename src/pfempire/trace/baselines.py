from __future__ import annotations

from pfempire.analysis.evidence_pack import select_evidence_results
from pfempire.trace.schemas import EvidenceBlueprint, TraceCandidate, TraceSelectionResult
from pfempire.trace.selector import SelectorConfig, select_evidence, validate_candidate_identity
from pfempire.trace.utility import compute_utility


def current_pfempire_select(candidates: list[TraceCandidate], blueprint: EvidenceBlueprint, dependence: dict[tuple[str, str], float], config: SelectorConfig) -> TraceSelectionResult:
    raw_rows = [candidate.metadata.get("raw_result", {}) for candidate in candidates]
    max_chunks = max(1, config.token_budget // max(1, int(sum(c.token_cost for c in candidates) / max(1, len(candidates)))))
    selected_rows = select_evidence_results(raw_rows, max_chunks=max_chunks)
    selected_ids = {str(row.get("chunk_id")) for row in selected_rows}
    selected = [candidate for candidate in candidates if candidate.chunk_id in selected_ids]
    while sum(candidate.token_cost for candidate in selected) > config.token_budget:
        selected.pop()
    details = compute_utility(selected, blueprint, dependence, n_eff_max=max(1, len({c.paper_id for c in candidates})))
    independent = details["independence"]
    return TraceSelectionResult(
        question_id=blueprint.question_id, selector_name="current_pfempire", selected_candidates=selected,
        utility_total=details["total"], facet_coverage=details["facet"]["total"], scale_triangulation=details["scale"]["total"],
        polarity_coverage=details["polarity"]["total"], relevance_quality=details["relevance_quality"],
        effective_independent_evidence=independent["n_eff"], raw_paper_count=independent["raw_paper_count"],
        lineage_count=independent["evidence_lineage_count"], token_cost=sum(c.token_cost for c in selected),
        unresolved_facets=details["facet"]["unresolved_required"], run_config={"stopping_reason": "current_pack_limit"},
    )


def run_selector(name: str, candidates: list[TraceCandidate], blueprint: EvidenceBlueprint, dependence: dict[tuple[str, str], float], config: SelectorConfig, utility_weights: dict[str, float] | None = None) -> TraceSelectionResult:
    validate_candidate_identity(candidates)
    if name == "current_pfempire":
        return current_pfempire_select(candidates, blueprint, dependence, config)
    return select_evidence(candidates, blueprint, dependence, selector_name=name, config=config, utility_weights=utility_weights)
