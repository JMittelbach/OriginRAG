from __future__ import annotations

import random
import statistics
import math
from collections import Counter

from originrag.trace.baselines import run_selector
from originrag.trace.certificate import build_certificate
from originrag.trace.flow import compute_evidence_flow
from originrag.trace.schemas import EvidenceBlueprint, TraceCandidate
from originrag.trace.selector import SelectorConfig


def evaluate_selector(
    selector_name: str,
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    dependence: dict[tuple[str, str], float],
    gold_papers: list[dict] | None = None,
    should_answer: bool | None = None,
    config: SelectorConfig | None = None,
    utility_weights: dict[str, float] | None = None,
    lineage_graph_status: str = "not_assessed",
) -> dict:
    config = config or SelectorConfig()
    selection = run_selector(selector_name, candidates, blueprint, dependence, config, utility_weights)
    flow = compute_evidence_flow(
        selection.selected_candidates,
        blueprint,
        lineage_capacity=config.flow_lineage_capacity,
        facet_threshold=config.facet_coverage_threshold,
    )
    certificate = build_certificate(
        blueprint.question_id,
        selection.selected_candidates,
        blueprint,
        dependence,
        flow_details=flow,
        minimum_required_papers=(
            config.flow_minimum_papers
            if selector_name.startswith("trace_flow")
            else config.minimum_selected_papers
        ),
        selection_stopping_reason=selection.run_config.get("stopping_reason"),
        lineage_graph_status=lineage_graph_status,
    )
    selected_papers = {candidate.paper_id for candidate in selection.selected_candidates}
    gold_papers = gold_papers or []
    must = [row for row in gold_papers if truthy(row.get("must_include", True))]
    weighted_total = sum(float(row.get("importance_weight") or 1.0) for row in gold_papers)
    weighted_found = sum(float(row.get("importance_weight") or 1.0) for row in gold_papers if row.get("paper_id") in selected_papers)
    return {
        "question_id": blueprint.question_id,
        "selector": selector_name,
        "gold_paper_recall": (
            sum(row.get("paper_id") in selected_papers for row in must) / len(must)
            if must
            else None
        ),
        "weighted_gold_paper_recall": (
            weighted_found / weighted_total if weighted_total > 0 else None
        ),
        "required_facet_coverage": selection.facet_coverage,
        "lineage_flow_coverage": flow.normalized_coverage,
        "lineage_flow_bottleneck": flow.bottleneck_coverage,
        "biological_scale_coverage": selection.scale_triangulation,
        "polarity_coverage": selection.polarity_coverage,
        "effective_independent_evidence": selection.effective_independent_evidence,
        "raw_paper_count": selection.raw_paper_count,
        "evidence_lineage_count": selection.lineage_count,
        "duplicate_lineage_rate": certificate.duplicate_lineage_rate,
        "independent_evidence_ratio": certificate.independent_evidence_ratio,
        "evidence_inflation_factor": certificate.evidence_inflation_factor,
        "token_cost": selection.token_cost,
        "unresolved_critical_facets": len(selection.unresolved_facets),
        "abstention_correctness": "" if should_answer is None else int(
            (certificate.evidence_sufficiency in {"usable", "good"}) == should_answer
        ),
        "mean_retrieval_relevance": statistics.mean([c.retrieval_score for c in selection.selected_candidates]) if selection.selected_candidates else 0.0,
        "low_quality_fraction": sum(bool(c.quality_flags) for c in selection.selected_candidates) / max(1, len(selection.selected_candidates)),
        "max_paper_dominance": max(Counter(c.paper_id for c in selection.selected_candidates).values(), default=0) / max(1, len(selection.selected_candidates)),
        "publication_version_duplication": max(0, selection.raw_paper_count - certificate.publication_family_count),
        "evidence_sufficiency": certificate.evidence_sufficiency,
        "allowed_claim_strength": certificate.allowed_claim_strength,
        "selection": selection,
        "certificate": certificate,
    }


def paired_bootstrap(
    rows: list[dict], selector_a: str, selector_b: str, metric: str,
    iterations: int = 2000, seed: int = 42, confidence: float = 0.95,
) -> dict:
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations <= 0:
        raise ValueError("iterations must be a positive integer")
    if not math.isfinite(float(confidence)) or not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be finite and between zero and one")
    by_question: dict[str, dict[str, float]] = {}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        value = row.get(metric)
        if value in {None, ""}:
            continue
        question_id = str(row["question_id"])
        selector = str(row["selector"])
        key = (question_id, selector)
        if key in seen:
            raise ValueError(
                f"duplicate evaluation row for question_id={question_id!r}, "
                f"selector={selector!r}"
            )
        seen.add(key)
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"metric {metric!r} must contain only finite values")
        by_question.setdefault(question_id, {})[selector] = parsed
    pairs = [
        by_question[question_id][selector_a] - by_question[question_id][selector_b]
        for question_id in sorted(by_question)
        if selector_a in by_question[question_id]
        and selector_b in by_question[question_id]
    ]
    if not pairs:
        return {"metric": metric, "selector_a": selector_a, "selector_b": selector_b, "n": 0}
    rng = random.Random(seed)
    boot = [statistics.mean(rng.choices(pairs, k=len(pairs))) for _ in range(iterations)]
    boot.sort()
    alpha = (1.0 - confidence) / 2.0
    lo = boot[min(len(boot) - 1, int(alpha * len(boot)))]
    hi = boot[min(len(boot) - 1, int((1.0 - alpha) * len(boot)))]
    return {
        "metric": metric, "selector_a": selector_a, "selector_b": selector_b, "n": len(pairs),
        "mean_difference": statistics.mean(pairs), "median_difference": statistics.median(pairs),
        "ci_low": lo, "ci_high": hi,
        "wins": sum(value > 0 for value in pairs), "ties": sum(value == 0 for value in pairs), "losses": sum(value < 0 for value in pairs),
    }


def truthy(value: object) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "no", "none"}
