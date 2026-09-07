from __future__ import annotations

import hashlib
import math
import random
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from time import perf_counter_ns
from typing import Iterable

from pfempire.trace.flow import compute_evidence_flow
from pfempire.trace.robust_lineage import LineageDependenceInterval, robust_evidence_flow
from pfempire.trace.schemas import EvidenceBlueprint, TraceCandidate
from pfempire.trace.selector import SelectorConfig, select_evidence
from pfempire.trace.utility import compute_utility


METHOD_IDS = (
    "relevance_topk",
    "mmr",
    "doi_near_duplicate_dedup",
    "one_per_known_lineage",
    "facet_cover_no_lineage",
    "paper_count_threshold",
    "n_eff_threshold",
    "pfempire_full_v6",
)
LINEAGE_CONDITIONS = ("gold", "masked_50", "corrupted_independent")


@dataclass(frozen=True)
class MethodResult:
    question_id: str
    lineage_condition: str
    method_id: str
    accepted: bool
    selected_chunk_ids: tuple[str, ...]
    selected_paper_ids: tuple[str, ...]
    evidence_unit_count: int
    facet_bottleneck: float
    effective_independent_evidence: float
    token_cost: int
    exact_completed: bool | None = None
    exact_for_declared_uncertainty: bool | None = None
    component_count: int | None = None
    search_states: int | None = None
    partition_bound_method: str = "not_applicable"
    fallback_used: bool | None = None
    certificate_runtime_ms: float | None = None
    report_origin_homogeneity_verified: bool | None = None
    homogeneity_annotated_substantive_paper_count: int | None = None
    substantive_paper_count_for_homogeneity: int | None = None


def evaluate_methods(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    *,
    gold_origin_by_paper: dict[str, str],
    config: SelectorConfig,
    lineage_condition: str = "gold",
    mask_seed: int = 42,
    mask_fraction: float = 0.50,
) -> list[MethodResult]:
    """Evaluate every frozen comparator on one identical candidate pool and budget."""

    paper_ids = {candidate.paper_id for candidate in candidates}
    missing_origins = sorted(paper_ids - set(gold_origin_by_paper))
    if missing_origins:
        raise ValueError(
            "substantive candidate papers lack adjudicated origins: "
            + ", ".join(missing_origins)
        )
    intervals = declared_lineage_intervals(
        blueprint.question_id,
        gold_origin_by_paper,
        condition=lineage_condition,
        mask_seed=mask_seed,
        mask_fraction=mask_fraction,
    )
    known_units = known_lineage_units(paper_ids, intervals)
    dependence = interval_dependence(intervals)
    duplicate_units = duplicate_unit_mapping(candidates)
    results = []
    for method_id in METHOD_IDS:
        selected = select_for_method(
            method_id,
            candidates,
            blueprint,
            known_unit_by_paper=known_units,
            dependence=dependence,
            config=config,
        )
        units = units_for_method(
            method_id,
            selected,
            known_unit_by_paper=known_units,
            duplicate_units=duplicate_units,
        )
        if method_id == "pfempire_full_v6":
            selected_papers = {candidate.paper_id for candidate in selected}
            selected_intervals = [
                edge
                for edge in intervals
                if edge.paper_id_a in selected_papers and edge.paper_id_b in selected_papers
            ]
            certificate_started_ns = perf_counter_ns()
            robust = robust_evidence_flow(
                selected,
                blueprint,
                selected_intervals,
                lineage_capacity=config.flow_lineage_capacity,
                facet_threshold=config.facet_coverage_threshold,
                strict_missing_pairs=True,
            )
            certificate_runtime_ms = (
                perf_counter_ns() - certificate_started_ns
            ) / 1_000_000.0
            flow = robust.pessimistic_flow
            unit_count = robust.lineage_count_lower
            robust_n_eff = robust.effective_evidence.lower
            exact = robust.exact_partition_analysis
            exact_completed = exact.completed
            exact_for_declared_uncertainty = (
                robust.exact_for_declared_lineage_uncertainty
            )
            component_count = exact.component_count
            search_states = exact.search_states
            partition_bound_method = robust.partition_bound_method
            fallback_used = partition_bound_method == "safe_relaxation"
            report_origin_homogeneity_verified = (
                robust.report_origin_homogeneity_verified
            )
            homogeneity_annotated_substantive_paper_count = (
                robust.homogeneity_annotated_substantive_paper_count
            )
            substantive_paper_count_for_homogeneity = (
                robust.substantive_paper_count_for_homogeneity
            )
        else:
            robust = None
            flow = compute_evidence_flow(
                selected,
                blueprint,
                lineage_capacity=config.flow_lineage_capacity,
                facet_threshold=config.facet_coverage_threshold,
                unit_by_paper=units,
            )
            unit_count = len(set(units.values()))
            robust_n_eff = None
            exact_completed = None
            exact_for_declared_uncertainty = None
            component_count = None
            search_states = None
            partition_bound_method = "not_applicable"
            fallback_used = None
            certificate_runtime_ms = None
            report_origin_homogeneity_verified = None
            homogeneity_annotated_substantive_paper_count = None
            substantive_paper_count_for_homogeneity = None
        utility_dependence = (
            dependence if method_id in {"n_eff_threshold", "pfempire_full_v6"} else {}
        )
        utility = compute_utility(
            selected,
            blueprint,
            utility_dependence,
            n_eff_max=max(1, len(paper_ids)),
        )
        accepted = (
            robust.status == "robustly_sufficient"
            if robust is not None
            else decision_for_method(
                method_id,
                selected,
                blueprint,
                flow_unresolved=flow.unresolved_facets,
                unit_count=unit_count,
                n_eff=float(utility["independence"]["n_eff"]),
            )
        )
        results.append(
            MethodResult(
                question_id=blueprint.question_id,
                lineage_condition=lineage_condition,
                method_id=method_id,
                accepted=accepted,
                selected_chunk_ids=tuple(candidate.chunk_id for candidate in selected),
                selected_paper_ids=tuple(
                    sorted({candidate.paper_id for candidate in selected})
                ),
                evidence_unit_count=unit_count,
                facet_bottleneck=flow.bottleneck_coverage,
                effective_independent_evidence=(
                    float(robust_n_eff)
                    if robust_n_eff is not None
                    else float(utility["independence"]["n_eff"])
                ),
                token_cost=sum(candidate.token_cost for candidate in selected),
                exact_completed=exact_completed,
                exact_for_declared_uncertainty=exact_for_declared_uncertainty,
                component_count=component_count,
                search_states=search_states,
                partition_bound_method=partition_bound_method,
                fallback_used=fallback_used,
                certificate_runtime_ms=certificate_runtime_ms,
                report_origin_homogeneity_verified=(
                    report_origin_homogeneity_verified
                ),
                homogeneity_annotated_substantive_paper_count=(
                    homogeneity_annotated_substantive_paper_count
                ),
                substantive_paper_count_for_homogeneity=(
                    substantive_paper_count_for_homogeneity
                ),
            )
        )
    return results


def select_for_method(
    method_id: str,
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    *,
    known_unit_by_paper: dict[str, str],
    dependence: dict[tuple[str, str], float],
    config: SelectorConfig,
) -> list[TraceCandidate]:
    if method_id not in METHOD_IDS:
        raise ValueError(f"unknown comparison method: {method_id}")
    filtered = candidates
    selector = "topk_relevance"
    selector_dependence: dict[tuple[str, str], float] = {}
    if method_id == "mmr":
        selector = "mmr"
    elif method_id == "doi_near_duplicate_dedup":
        filtered = representative_paper_candidates(candidates, duplicate_unit_mapping(candidates))
    elif method_id == "one_per_known_lineage":
        filtered = representative_paper_candidates(candidates, known_unit_by_paper)
    elif method_id == "facet_cover_no_lineage":
        selector = "trace_no_lineage"
    elif method_id == "pfempire_full_v6":
        selector = "trace_flow"
        selector_dependence = dependence
    result = select_evidence(
        filtered,
        blueprint,
        selector_dependence,
        selector_name=selector,
        config=config,
    )
    return result.selected_candidates


def decision_for_method(
    method_id: str,
    selected: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    *,
    flow_unresolved: list[str],
    unit_count: int,
    n_eff: float,
) -> bool:
    if flow_unresolved:
        return False
    if method_id == "facet_cover_no_lineage":
        return True
    if method_id == "n_eff_threshold":
        return n_eff >= blueprint.minimum_independent_lineages
    required = blueprint.minimum_independent_lineages
    if method_id == "paper_count_threshold":
        return len({candidate.paper_id for candidate in selected}) >= required
    return unit_count >= required


def units_for_method(
    method_id: str,
    selected: list[TraceCandidate],
    *,
    known_unit_by_paper: dict[str, str],
    duplicate_units: dict[str, str],
) -> dict[str, str]:
    papers = {candidate.paper_id for candidate in selected}
    if method_id in {"one_per_known_lineage", "pfempire_full_v6"}:
        return {paper_id: known_unit_by_paper[paper_id] for paper_id in papers}
    if method_id == "doi_near_duplicate_dedup":
        return {paper_id: duplicate_units[paper_id] for paper_id in papers}
    return {paper_id: f"REPORT:{paper_id}" for paper_id in papers}


def representative_paper_candidates(
    candidates: list[TraceCandidate], unit_by_paper: dict[str, str]
) -> list[TraceCandidate]:
    best_by_paper: dict[str, float] = defaultdict(lambda: -math.inf)
    for candidate in candidates:
        best_by_paper[candidate.paper_id] = max(
            best_by_paper[candidate.paper_id], candidate.retrieval_score
        )
    representative_by_unit: dict[str, str] = {}
    for paper_id in sorted(best_by_paper):
        unit = unit_by_paper[paper_id]
        current = representative_by_unit.get(unit)
        if current is None or (best_by_paper[paper_id], invert(paper_id)) > (
            best_by_paper[current],
            invert(current),
        ):
            representative_by_unit[unit] = paper_id
    allowed = set(representative_by_unit.values())
    return [candidate for candidate in candidates if candidate.paper_id in allowed]


def duplicate_unit_mapping(candidates: list[TraceCandidate]) -> dict[str, str]:
    """Create conservative exact-text/DOI/near-title report clusters without gold lineage."""

    papers = sorted({candidate.paper_id for candidate in candidates})
    parent = {paper_id: paper_id for paper_id in papers}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[max(root_left, root_right)] = min(root_left, root_right)

    metadata = {paper_id: paper_metadata(candidates, paper_id) for paper_id in papers}
    text_hash_papers: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        normalized = normalize_text(candidate.text)
        if normalized:
            text_hash_papers[hashlib.sha256(normalized.encode()).hexdigest()].add(
                candidate.paper_id
            )
    for grouped in text_hash_papers.values():
        for paper_id in sorted(grouped)[1:]:
            union(sorted(grouped)[0], paper_id)
    for index, left in enumerate(papers):
        for right in papers[index + 1 :]:
            left_meta, right_meta = metadata[left], metadata[right]
            same_doi = left_meta["doi"] and left_meta["doi"] == right_meta["doi"]
            near_title = titles_are_near_duplicates(left_meta["title"], right_meta["title"])
            if same_doi or near_title:
                union(left, right)
    return {paper_id: f"DEDUP:{find(paper_id)}" for paper_id in papers}


def paper_metadata(candidates: list[TraceCandidate], paper_id: str) -> dict[str, str]:
    rows = [
        candidate.metadata.get("raw_result", {})
        for candidate in candidates
        if candidate.paper_id == paper_id
    ]
    row = next((item for item in rows if item), {})
    return {
        "doi": normalize_doi(str(row.get("doi") or "")),
        "title": normalize_title(str(row.get("title") or "")),
    }


def titles_are_near_duplicates(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    left_tokens, right_tokens = set(left.split()), set(right.split())
    if min(len(left_tokens), len(right_tokens)) < 6:
        return False
    overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    containment = len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))
    return overlap >= 0.90 or containment >= 0.95


def interval_dependence(
    intervals: list[LineageDependenceInterval],
) -> dict[tuple[str, str], float]:
    return {
        tuple(sorted((edge.paper_id_a, edge.paper_id_b))): edge.distinct_dependence_upper
        for edge in intervals
    }


def declared_lineage_intervals(
    question_id: str,
    origin_by_paper: dict[str, str],
    *,
    condition: str,
    mask_seed: int,
    mask_fraction: float,
) -> list[LineageDependenceInterval]:
    if condition not in {"gold", "masked_50", "corrupted_independent"}:
        raise ValueError(f"unknown lineage condition: {condition}")
    if not 0.0 <= mask_fraction <= 1.0:
        raise ValueError("mask_fraction must lie within [0,1]")
    papers = sorted(origin_by_paper)
    intervals = []
    for index, left in enumerate(papers):
        for right in papers[index + 1 :]:
            same_origin = origin_by_paper[left] == origin_by_paper[right]
            relation = "must_link" if same_origin else "cannot_link"
            lower = upper = 1.0 if same_origin else 0.0
            review_status = "frozen"
            source = "adjudicated_gold"
            if condition == "masked_50" and pair_is_masked(
                question_id, left, right, seed=mask_seed, fraction=mask_fraction
            ):
                relation, lower, upper = "may_link", 0.0, 1.0
                review_status = "suggested"
                source = "prospective_hash_mask"
            elif condition == "corrupted_independent":
                relation, lower, upper = "cannot_link", 0.0, 0.0
                source = "deliberately_corrupted_sensitivity"
            intervals.append(
                LineageDependenceInterval(
                    paper_id_a=left,
                    paper_id_b=right,
                    relation=relation,
                    distinct_dependence_lower=lower,
                    distinct_dependence_upper=upper,
                    review_status=review_status,
                    source=source,
                )
            )
    return intervals


def pair_is_masked(
    question_id: str, left: str, right: str, *, seed: int, fraction: float
) -> bool:
    payload = f"{seed}|{question_id}|{min(left, right)}|{max(left, right)}"
    value = int(hashlib.sha256(payload.encode()).hexdigest(), 16) / (2**256 - 1)
    return value < fraction


def known_lineage_units(
    papers: set[str], intervals: list[LineageDependenceInterval]
) -> dict[str, str]:
    parent = {paper_id: paper_id for paper_id in papers}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for edge in intervals:
        if edge.relation != "must_link":
            continue
        left, right = find(edge.paper_id_a), find(edge.paper_id_b)
        if left != right:
            parent[max(left, right)] = min(left, right)
    return {paper_id: f"KNOWN:{find(paper_id)}" for paper_id in sorted(papers)}


def summarize_results(
    rows: Iterable[MethodResult], gold_should_answer: dict[str, bool]
) -> list[dict[str, float | int | str | None]]:
    grouped: dict[tuple[str, str], list[MethodResult]] = defaultdict(list)
    for row in rows:
        if row.question_id not in gold_should_answer:
            raise ValueError(f"missing gold decision for {row.question_id}")
        grouped[(row.lineage_condition, row.method_id)].append(row)
    summaries = []
    conditions = sorted(
        {
            row.lineage_condition
            for rows_for_method in grouped.values()
            for row in rows_for_method
        }
    )
    for condition in conditions:
        for method_id in METHOD_IDS:
            method_rows = grouped.get((condition, method_id), [])
            accepted = [row for row in method_rows if row.accepted]
            false_accepts = sum(
                not gold_should_answer[row.question_id] for row in accepted
            )
            positives = [
                row for row in method_rows if gold_should_answer[row.question_id]
            ]
            false_rejects = sum(not row.accepted for row in positives)
            false_acceptance_rate = (
                false_accepts / len(accepted) if accepted else None
            )
            summaries.append(
                {
                    "method_id": method_id,
                    "lineage_condition": condition,
                    "questions": len(method_rows),
                    "accepted": len(accepted),
                    "coverage": len(accepted) / max(1, len(method_rows)),
                    "false_accepts": false_accepts,
                    "false_acceptance_rate": false_acceptance_rate,
                    "false_acceptance_upper_95": binomial_upper_bound(
                        false_accepts, len(accepted), confidence=0.95
                    ),
                    "false_rejects": false_rejects,
                    "false_rejection_rate": false_rejects / max(1, len(positives)),
                    "safe_correct_yield": sum(
                        row.accepted and gold_should_answer[row.question_id]
                        for row in method_rows
                    )
                    / max(1, len(method_rows)),
                }
            )
    return summaries


def paired_method_comparisons(
    rows: Iterable[MethodResult],
    gold_should_answer: dict[str, bool],
    *,
    reference_method: str = "pfempire_full_v6",
    iterations: int = 2000,
    seed: int = 42,
) -> list[dict[str, float | int | str]]:
    """Question-clustered paired contrasts; positive values favor OriginRAG."""

    if iterations <= 0:
        raise ValueError("iterations must be positive")
    indexed = {
        (row.lineage_condition, row.question_id, row.method_id): row
        for row in rows
    }
    conditions = sorted({key[0] for key in indexed})
    question_ids = sorted({key[1] for key in indexed})
    comparisons = []
    for condition in conditions:
        for baseline in METHOD_IDS:
            if baseline == reference_method:
                continue
            differences: dict[str, list[float]] = {
                "false_acceptance_reduction": [],
                "coverage_difference": [],
                "safe_correct_yield_difference": [],
            }
            for question_id in question_ids:
                reference = indexed.get((condition, question_id, reference_method))
                comparator = indexed.get((condition, question_id, baseline))
                if reference is None or comparator is None:
                    continue
                gold = gold_should_answer[question_id]
                differences["false_acceptance_reduction"].append(
                    float(comparator.accepted and not gold)
                    - float(reference.accepted and not gold)
                )
                differences["coverage_difference"].append(
                    float(reference.accepted) - float(comparator.accepted)
                )
                differences["safe_correct_yield_difference"].append(
                    float(reference.accepted and gold)
                    - float(comparator.accepted and gold)
                )
            for metric, values in differences.items():
                estimate, low, high = paired_bootstrap_mean(
                    values, iterations=iterations, seed=seed
                )
                comparisons.append(
                    {
                        "lineage_condition": condition,
                        "reference_method": reference_method,
                        "baseline_method": baseline,
                        "metric": metric,
                        "questions": len(values),
                        "mean_difference": estimate,
                        "ci_low": low,
                        "ci_high": high,
                    }
                )
    return comparisons


def paired_false_acceptance_rate_comparisons(
    rows: Iterable[MethodResult],
    gold_should_answer: dict[str, bool],
    *,
    reference_method: str = "pfempire_full_v6",
    condition: str = "masked_50",
    iterations: int = 2000,
    seed: int = 42,
) -> list[dict[str, float | int | str | None]]:
    """Compare false-acceptance risk among accepted questions by paired bootstrap.

    The resampling unit is the question. Replicates in which either method accepts
    no question are omitted because conditional risk is undefined there.
    Positive differences favour the reference method.
    """

    if iterations <= 0:
        raise ValueError("iterations must be positive")
    indexed = {
        (row.question_id, row.method_id): row
        for row in rows
        if row.lineage_condition == condition
    }
    question_ids = sorted(
        {
            question_id
            for question_id, method_id in indexed
            if method_id == reference_method
        }
    )
    output: list[dict[str, float | int | str | None]] = []
    for baseline in METHOD_IDS:
        if baseline == reference_method:
            continue
        complete = [
            question_id
            for question_id in question_ids
            if (question_id, baseline) in indexed
            and (question_id, reference_method) in indexed
        ]
        reference_risk = false_acceptance_risk(
            [indexed[(question_id, reference_method)] for question_id in complete],
            gold_should_answer,
        )
        baseline_risk = false_acceptance_risk(
            [indexed[(question_id, baseline)] for question_id in complete],
            gold_should_answer,
        )
        point = (
            None
            if reference_risk is None or baseline_risk is None
            else baseline_risk - reference_risk
        )
        relative = (
            None
            if point is None or baseline_risk is None or baseline_risk <= 0.0
            else point / baseline_risk
        )
        boot: list[float] = []
        if complete:
            rng = random.Random(seed)
            for _ in range(iterations):
                sampled = rng.choices(complete, k=len(complete))
                sampled_reference = false_acceptance_risk(
                    [indexed[(question_id, reference_method)] for question_id in sampled],
                    gold_should_answer,
                )
                sampled_baseline = false_acceptance_risk(
                    [indexed[(question_id, baseline)] for question_id in sampled],
                    gold_should_answer,
                )
                if sampled_reference is not None and sampled_baseline is not None:
                    boot.append(sampled_baseline - sampled_reference)
        low, high = percentile_interval(boot)
        output.append(
            {
                "lineage_condition": condition,
                "reference_method": reference_method,
                "baseline_method": baseline,
                "questions": len(complete),
                "reference_false_acceptance_rate": reference_risk,
                "baseline_false_acceptance_rate": baseline_risk,
                "absolute_risk_reduction": point,
                "relative_risk_reduction": relative,
                "ci_low": low,
                "ci_high": high,
                "valid_bootstrap_replicates": len(boot),
            }
        )
    return output


def false_acceptance_risk(
    rows: Iterable[MethodResult], gold_should_answer: dict[str, bool]
) -> float | None:
    accepted = [row for row in rows if row.accepted]
    if not accepted:
        return None
    return sum(not gold_should_answer[row.question_id] for row in accepted) / len(accepted)


def percentile_interval(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    ordered = sorted(values)
    count = len(ordered)
    return (
        ordered[int(0.025 * count)],
        ordered[min(count - 1, int(0.975 * count))],
    )


def paired_bootstrap_mean(
    values: list[float], *, iterations: int, seed: int
) -> tuple[float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    boot = sorted(
        statistics.mean(rng.choices(values, k=len(values))) for _ in range(iterations)
    )
    return (
        statistics.mean(values),
        boot[int(0.025 * iterations)],
        boot[min(iterations - 1, int(0.975 * iterations))],
    )


def binomial_upper_bound(errors: int, trials: int, *, confidence: float) -> float:
    """Exact one-sided Clopper--Pearson upper confidence bound."""

    if trials < 0 or errors < 0 or errors > trials:
        raise ValueError("binomial counts must satisfy 0 <= errors <= trials")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between zero and one")
    if trials == 0 or errors == trials:
        return 1.0
    alpha = 1.0 - confidence
    low, high = 0.0, 1.0
    for _ in range(80):
        middle = (low + high) / 2.0
        cdf = sum(
            math.comb(trials, count)
            * middle**count
            * (1.0 - middle) ** (trials - count)
            for count in range(errors + 1)
        )
        if cdf > alpha:
            low = middle
        else:
            high = middle
    return high


def normalize_doi(value: str) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip().lower())


def normalize_title(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def invert(value: str) -> tuple[int, ...]:
    return tuple(-ord(character) for character in value)
