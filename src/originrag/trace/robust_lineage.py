from __future__ import annotations

import math
from collections import defaultdict
from itertools import combinations
from typing import Literal

from pydantic import Field, field_validator, model_validator

from originrag.trace.evidence_weights import aggregate_paper_weights
from originrag.trace.exact_lineage import (
    ExactPartitionFlowAnalysis,
    exact_partition_flow_analysis,
)
from originrag.trace.flow import (
    EPSILON,
    compute_evidence_flow,
    evidence_quality,
    role_compatibility,
)
from originrag.trace.independence_units import paper_independence_units
from originrag.trace.method_versions import (
    EVIDENCE_ACCOUNTING_VERSION,
    ROBUST_LINEAGE_CERTIFICATE_VERSION,
)
from originrag.trace.schemas import (
    EvidenceBlueprint,
    EvidenceFacet,
    EvidenceFlowResult,
    TraceCandidate,
    TraceModel,
)


LineageRelation = Literal["must_link", "cannot_link", "may_link"]
RobustEvidenceStatus = Literal[
    "robustly_sufficient",
    "lineage_review_required",
    "insufficient_even_optimistically",
]
CERTIFIABLE_FACET_THRESHOLD = 0.50


class LineageDependenceInterval(TraceModel):
    """An auditable uncertainty statement for one unordered paper pair."""

    paper_id_a: str
    paper_id_b: str
    distinct_dependence_lower: float = 0.0
    distinct_dependence_upper: float = 1.0
    relation: LineageRelation = "may_link"
    review_status: Literal["suggested", "human_reviewed", "frozen"] = "suggested"
    source: str = "unspecified"
    review_cost: float = 1.0

    @field_validator("paper_id_a", "paper_id_b", "source")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        parsed = str(value).strip()
        if not parsed:
            raise ValueError("paper IDs and source must be nonempty")
        return parsed

    @field_validator("distinct_dependence_lower", "distinct_dependence_upper")
    @classmethod
    def bounded_dependence(cls, value: float) -> float:
        parsed = float(value)
        if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
            raise ValueError("dependence bounds must be finite and between zero and one")
        return parsed

    @field_validator("review_cost")
    @classmethod
    def positive_review_cost(cls, value: float) -> float:
        parsed = float(value)
        if not math.isfinite(parsed) or parsed <= 0.0:
            raise ValueError("review_cost must be finite and greater than zero")
        return parsed

    @model_validator(mode="after")
    def valid_interval_and_relation(self) -> "LineageDependenceInterval":
        if self.paper_id_a == self.paper_id_b:
            raise ValueError("a lineage interval must connect two distinct papers")
        if self.distinct_dependence_lower > self.distinct_dependence_upper:
            raise ValueError("distinct_dependence_lower cannot exceed distinct_dependence_upper")
        if self.relation == "must_link" and not (
            _close(self.distinct_dependence_lower, 1.0)
            and _close(self.distinct_dependence_upper, 1.0)
        ):
            raise ValueError("must_link requires dependence interval [1, 1]")
        if self.relation == "cannot_link" and not (
            0.0 <= self.distinct_dependence_lower <= self.distinct_dependence_upper <= 1.0
        ):
            raise ValueError("cannot_link requires a valid dependence interval")
        return self


class EffectiveEvidenceBounds(TraceModel):
    """Bounds for the correlation-adjusted effective evidence count."""

    lower: float
    upper: float
    numerator: float | None = None
    minimum_denominator: float | None = None
    maximum_denominator: float | None = None
    paper_weights: dict[str, float] = Field(default_factory=dict)
    raw_paper_count: int
    substantive_paper_count: int
    relaxed_box_lower: float | None = None
    bound_method: str = "universal_unit_partition_bounds"
    exact_for_box_uncertainty: bool = False
    weight_aggregation: str = "max_chunk_then_max_lineage_unit"
    evidence_accounting_version: str = EVIDENCE_ACCOUNTING_VERSION


class RobustEvidenceFlowBounds(TraceModel):
    """Answer-specific evidence bounds under unresolved lineage relations."""

    question_id: str
    certificate_method_version: str = ROBUST_LINEAGE_CERTIFICATE_VERSION
    effective_evidence: EffectiveEvidenceBounds
    pessimistic_flow: EvidenceFlowResult
    relaxed_pessimistic_flow: EvidenceFlowResult
    optimistic_flow: EvidenceFlowResult
    lineage_count_lower: int
    relaxed_lineage_count_lower: int
    lineage_count_upper: int
    required_lineages: int
    required_effective_evidence: float
    status: RobustEvidenceStatus
    certificate_gap: float
    total_pair_count: int
    uncertain_pair_count: int
    missing_pair_count: int
    report_origin_homogeneity_verified: bool = False
    homogeneity_annotated_substantive_paper_count: int = 0
    substantive_paper_count_for_homogeneity: int = 0
    required_polarities: list[str] = Field(default_factory=list)
    missing_required_polarities: list[str] = Field(default_factory=list)
    strict_missing_pair_policy: bool
    exact_for_declared_lineage_uncertainty: bool
    exact_partition_analysis: ExactPartitionFlowAnalysis
    partition_bound_method: Literal["exact_search", "safe_relaxation"]
    joint_optimistic_feasibility: Literal["exists", "does_not_exist", "not_computed"] = (
        "not_computed"
    )
    joint_optimistic_partition: dict[str, str] = Field(default_factory=dict)
    partition_relaxation_bottleneck_gap: float | None = None
    bound_method: str = "must_possible_partition_relaxation"
    notes: list[str] = Field(default_factory=list)


class LineageReviewPriority(TraceModel):
    """Distribution-free priority for manually resolving a lineage relation."""

    rank: int = 0
    paper_id_a: str
    paper_id_b: str
    source: str
    review_cost: float
    certificate_flip_possible: bool
    guaranteed_gap_reduction: float
    best_case_gap_reduction: float
    outcome_separation: float
    priority_score: float
    low_outcome_status: RobustEvidenceStatus
    high_outcome_status: RobustEvidenceStatus


def effective_evidence_bounds(
    candidates: list[TraceCandidate],
    intervals: list[LineageDependenceInterval] | None = None,
    *,
    strict_missing_pairs: bool = True,
    facet_ids: set[str] | None = None,
    blueprint: EvidenceBlueprint | None = None,
    exact_partition_max_components: int = 9,
    exact_partition_max_search_states: int = 100_000,
) -> EffectiveEvidenceBounds:
    """Return replication-invariant effective-evidence bounds.

    Unit weights are maxima over their member reports. Dependence endpoints are
    exact for a fixed partition, but extrema can occur at non-maximal partitions,
    so bounded instances enumerate every feasible partition.
    """

    complete, _ = _complete_intervals(
        candidates,
        intervals or [],
        strict_missing_pairs=strict_missing_pairs,
    )
    if blueprint is not None:
        _validate_single_origin_per_report(candidates, blueprint, complete)
    weighted_candidates = (
        _answer_weighted_candidates(candidates, blueprint) if blueprint is not None else candidates
    )
    bounds = _effective_evidence_bounds_from_complete(
        weighted_candidates,
        complete,
        facet_ids=(
            {facet.facet_id for facet in blueprint.required_facets if facet.is_required}
            if blueprint is not None
            else facet_ids
        ),
    )
    analysis_blueprint = blueprint or EvidenceBlueprint(
        question_id="effective_evidence_bounds",
        question="Effective evidence bounds",
        task_mode="evidence_accounting",
        required_facets=[],
        minimum_independent_lineages=0,
    )
    exact = exact_partition_flow_analysis(
        weighted_candidates,
        analysis_blueprint,
        complete,
        max_components=exact_partition_max_components,
        max_search_states=exact_partition_max_search_states,
        paper_weights=bounds.paper_weights,
    )
    if (
        exact.completed
        and exact.effective_evidence_lower is not None
        and exact.effective_evidence_upper is not None
    ):
        return bounds.model_copy(
            update={
                "lower": exact.effective_evidence_lower,
                "upper": exact.effective_evidence_upper,
                "relaxed_box_lower": bounds.lower,
                "bound_method": "exact_partition_unit_max_conditional_box_intervals",
                "exact_for_box_uncertainty": True,
            }
        )
    return bounds


def robust_evidence_flow(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    intervals: list[LineageDependenceInterval] | None = None,
    *,
    lineage_capacity: float = 1.0,
    facet_threshold: float = 0.60,
    effective_evidence_ratio: float = 0.90,
    require_facet_flow: bool = True,
    strict_missing_pairs: bool = True,
    force_safe_relaxation: bool = False,
    exact_partition_max_components: int = 9,
    exact_partition_max_search_states: int = 100_000,
) -> RobustEvidenceFlowBounds:
    """Bound facet flow and independent evidence over lineage uncertainty.

    The pessimistic partition merges every relation that may represent one
    evidence unit. This can merge mutually incompatible possibilities, so it is
    deliberately a safe lower relaxation. The optimistic partition merges only
    hard resource and must-link relations. ``effective_evidence_ratio`` multiplies
    the blueprint's minimum lineage count to define the effective-evidence floor.
    ``require_facet_flow=False`` and ``force_safe_relaxation=True`` are explicit
    controlled-policy switches: the former reports but does not gate on facet
    flow, while the latter uses the conservative relaxation without enumeration.
    """

    complete, missing_count = _complete_intervals(
        candidates,
        intervals or [],
        strict_missing_pairs=strict_missing_pairs,
    )
    return _robust_evidence_flow_from_complete(
        candidates,
        blueprint,
        complete,
        missing_count=missing_count,
        strict_missing_pairs=strict_missing_pairs,
        lineage_capacity=lineage_capacity,
        facet_threshold=facet_threshold,
        effective_evidence_ratio=effective_evidence_ratio,
        require_facet_flow=require_facet_flow,
        force_safe_relaxation=force_safe_relaxation,
        exact_partition_max_components=exact_partition_max_components,
        exact_partition_max_search_states=exact_partition_max_search_states,
    )


def complete_lineage_intervals(
    candidates: list[TraceCandidate],
    intervals: list[LineageDependenceInterval] | None = None,
    *,
    strict_missing_pairs: bool = True,
) -> tuple[list[LineageDependenceInterval], int]:
    """Return the exact widened and closure-completed certificate constraints."""

    return _complete_intervals(
        candidates,
        intervals or [],
        strict_missing_pairs=strict_missing_pairs,
    )


def rank_lineage_reviews(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    intervals: list[LineageDependenceInterval] | None = None,
    *,
    lineage_capacity: float = 1.0,
    facet_threshold: float = 0.60,
    effective_evidence_ratio: float = 0.90,
    require_facet_flow: bool = True,
    strict_missing_pairs: bool = True,
    force_safe_relaxation: bool = False,
    simulation_budget: int | None = 32,
    exact_partition_max_components: int = 9,
    exact_partition_max_search_states: int = 100_000,
) -> list[LineageReviewPriority]:
    """Rank unresolved relations by certificate impact per review cost.

    This is a myopic worst-case-aware audit rule, not expected value of
    information: no outcome probabilities are invented. To stay tractable on
    dense graphs, an O(p^2) endpoint-impact screen is followed by configured
    exact-or-fallback certificate recomputation for at most
    ``simulation_budget`` pairs. Pass ``None`` to simulate every eligible pair.
    """

    if simulation_budget is not None and simulation_budget <= 0:
        raise ValueError("simulation_budget must be positive or None")
    complete, missing_count = _complete_intervals(
        candidates,
        intervals or [],
        strict_missing_pairs=strict_missing_pairs,
    )
    current = _robust_evidence_flow_from_complete(
        candidates,
        blueprint,
        complete,
        missing_count=missing_count,
        strict_missing_pairs=strict_missing_pairs,
        lineage_capacity=lineage_capacity,
        facet_threshold=facet_threshold,
        effective_evidence_ratio=effective_evidence_ratio,
        require_facet_flow=require_facet_flow,
        force_safe_relaxation=force_safe_relaxation,
        exact_partition_max_components=exact_partition_max_components,
        exact_partition_max_search_states=exact_partition_max_search_states,
    )
    if current.status != "lineage_review_required":
        return []
    weights = current.effective_evidence.paper_weights
    eligible = [
        edge
        for edge in complete
        if edge.review_status != "frozen"
        and (
            edge.relation == "may_link"
            or not _close(
                edge.distinct_dependence_lower,
                edge.distinct_dependence_upper,
            )
        )
    ]
    eligible.sort(
        key=lambda edge: (
            -_screening_impact(edge, weights),
            edge.review_cost,
            edge.paper_id_a,
            edge.paper_id_b,
        )
    )
    screened = eligible if simulation_budget is None else eligible[:simulation_budget]
    priorities: list[LineageReviewPriority] = []
    for edge in screened:
        pair = _pair_key(edge.paper_id_a, edge.paper_id_b)
        low_edge = _review_outcome(edge, high=False)
        high_edge = _review_outcome(edge, high=True)
        low_edges = [low_edge if _edge_key(item) == pair else item for item in complete]
        high_edges = [high_edge if _edge_key(item) == pair else item for item in complete]
        low_edges, _ = _complete_intervals(
            candidates,
            low_edges,
            strict_missing_pairs=strict_missing_pairs,
        )
        high_edges, _ = _complete_intervals(
            candidates,
            high_edges,
            strict_missing_pairs=strict_missing_pairs,
        )
        low = _robust_evidence_flow_from_complete(
            candidates,
            blueprint,
            low_edges,
            missing_count=max(0, missing_count - int(edge.source == "strict_missing_pair")),
            strict_missing_pairs=strict_missing_pairs,
            lineage_capacity=lineage_capacity,
            facet_threshold=facet_threshold,
            effective_evidence_ratio=effective_evidence_ratio,
            require_facet_flow=require_facet_flow,
            force_safe_relaxation=force_safe_relaxation,
            exact_partition_max_components=exact_partition_max_components,
            exact_partition_max_search_states=exact_partition_max_search_states,
        )
        high = _robust_evidence_flow_from_complete(
            candidates,
            blueprint,
            high_edges,
            missing_count=max(0, missing_count - int(edge.source == "strict_missing_pair")),
            strict_missing_pairs=strict_missing_pairs,
            lineage_capacity=lineage_capacity,
            facet_threshold=facet_threshold,
            effective_evidence_ratio=effective_evidence_ratio,
            require_facet_flow=require_facet_flow,
            force_safe_relaxation=force_safe_relaxation,
            exact_partition_max_components=exact_partition_max_components,
            exact_partition_max_search_states=exact_partition_max_search_states,
        )
        worst_after = max(low.certificate_gap, high.certificate_gap)
        best_after = min(low.certificate_gap, high.certificate_gap)
        guaranteed = max(0.0, current.certificate_gap - worst_after)
        best = max(0.0, current.certificate_gap - best_after)
        separation = _outcome_separation(low, high)
        flip = low.status != high.status
        score = (2.0 * float(flip) + guaranteed + 0.5 * best + 0.25 * separation) / edge.review_cost
        priorities.append(
            LineageReviewPriority(
                paper_id_a=edge.paper_id_a,
                paper_id_b=edge.paper_id_b,
                source=edge.source,
                review_cost=edge.review_cost,
                certificate_flip_possible=flip,
                guaranteed_gap_reduction=guaranteed,
                best_case_gap_reduction=best,
                outcome_separation=separation,
                priority_score=score,
                low_outcome_status=low.status,
                high_outcome_status=high.status,
            )
        )
    priorities.sort(
        key=lambda item: (
            -int(item.certificate_flip_possible),
            -item.guaranteed_gap_reduction / item.review_cost,
            -item.best_case_gap_reduction / item.review_cost,
            -item.outcome_separation / item.review_cost,
            item.review_cost,
            item.paper_id_a,
            item.paper_id_b,
        )
    )
    return [item.model_copy(update={"rank": rank}) for rank, item in enumerate(priorities, 1)]


def _robust_evidence_flow_from_complete(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    complete: list[LineageDependenceInterval],
    *,
    missing_count: int,
    strict_missing_pairs: bool,
    lineage_capacity: float,
    facet_threshold: float,
    effective_evidence_ratio: float,
    require_facet_flow: bool,
    force_safe_relaxation: bool,
    exact_partition_max_components: int,
    exact_partition_max_search_states: int,
) -> RobustEvidenceFlowBounds:
    _validate_certificate_blueprint(blueprint)
    if not math.isfinite(effective_evidence_ratio) or not 0.0 <= effective_evidence_ratio <= 1.0:
        raise ValueError("effective_evidence_ratio must be finite and between zero and one")
    _validate_single_origin_per_report(candidates, blueprint, complete)
    required_facet_ids = {
        facet.facet_id for facet in blueprint.required_facets if facet.is_required
    }
    weighted_candidates = _answer_weighted_candidates(candidates, blueprint)
    effective = _effective_evidence_bounds_from_complete(
        weighted_candidates,
        complete,
        facet_ids=required_facet_ids,
    )
    optimistic_units = _partition_units(candidates, complete, include_may_links=False)
    pessimistic_units = _partition_units(candidates, complete, include_may_links=True)
    optimistic_flow = compute_evidence_flow(
        candidates,
        blueprint,
        lineage_capacity=lineage_capacity,
        facet_threshold=facet_threshold,
        unit_by_paper=optimistic_units,
    )
    relaxed_pessimistic_flow = compute_evidence_flow(
        candidates,
        blueprint,
        lineage_capacity=lineage_capacity,
        facet_threshold=facet_threshold,
        unit_by_paper=pessimistic_units,
    )
    substantive = {
        paper_id for paper_id, weight in effective.paper_weights.items() if weight > EPSILON
    }
    homogeneity_annotated_papers = {
        candidate.paper_id
        for candidate in candidates
        if candidate.facet_origin_annotations_complete
    } & substantive
    homogeneity_verified = bool(substantive) and substantive.issubset(homogeneity_annotated_papers)
    relaxed_lineage_lower = len({pessimistic_units[paper_id] for paper_id in substantive})
    lineage_upper = len({optimistic_units[paper_id] for paper_id in substantive})
    exact_partition = exact_partition_flow_analysis(
        candidates,
        blueprint,
        complete,
        lineage_capacity=lineage_capacity,
        facet_threshold=facet_threshold,
        effective_evidence_ratio=effective_evidence_ratio,
        require_facet_flow=require_facet_flow,
        force_safe_relaxation=force_safe_relaxation,
        max_components=exact_partition_max_components,
        max_search_states=exact_partition_max_search_states,
        counted_paper_ids=substantive,
        paper_weights=effective.paper_weights,
    )
    if exact_partition.completed:
        if (
            exact_partition.pessimistic_flow is None
            or exact_partition.lineage_count_lower is None
            or exact_partition.effective_evidence_lower is None
            or exact_partition.effective_evidence_upper is None
        ):
            raise RuntimeError("completed exact partition analysis has no lower bound")
        relaxed_effective_lower = effective.lower
        effective = effective.model_copy(
            update={
                "lower": exact_partition.effective_evidence_lower,
                "upper": exact_partition.effective_evidence_upper,
                "relaxed_box_lower": relaxed_effective_lower,
                "bound_method": "exact_partition_unit_max_conditional_box_intervals",
                "exact_for_box_uncertainty": True,
            }
        )
        pessimistic_flow = exact_partition.pessimistic_flow
        lineage_lower = exact_partition.lineage_count_lower
        partition_method: Literal["exact_search", "safe_relaxation"] = "exact_search"
        relaxation_gap: float | None = max(
            0.0,
            pessimistic_flow.bottleneck_coverage - relaxed_pessimistic_flow.bottleneck_coverage,
        )
    else:
        pessimistic_flow = relaxed_pessimistic_flow
        lineage_lower = relaxed_lineage_lower
        partition_method = "safe_relaxation"
        relaxation_gap = None
    required_lineages = blueprint.minimum_independent_lineages
    required_effective = effective_evidence_ratio * required_lineages
    required_polarities = sorted(
        {
            polarity
            for polarity in blueprint.required_polarities
            if polarity not in {"any", "unknown"}
        }
    )
    missing_polarities = _missing_required_polarities(candidates, blueprint)
    lower_passes = (
        effective.lower + EPSILON >= required_effective
        and lineage_lower >= required_lineages
        and (not require_facet_flow or _flow_is_complete(pessimistic_flow))
        and not missing_polarities
    )
    component_upper_passes = (
        effective.upper + EPSILON >= required_effective
        and lineage_upper >= required_lineages
        and (not require_facet_flow or _flow_is_complete(optimistic_flow))
    )
    if exact_partition.joint_partition_pass_exists is True:
        joint_feasibility: Literal["exists", "does_not_exist", "not_computed"] = "exists"
        partition_upper_passes = True
    elif exact_partition.completed and exact_partition.joint_partition_pass_exists is False:
        partition_upper_passes = False
        joint_feasibility = "does_not_exist"
    else:
        partition_upper_passes = component_upper_passes
        joint_feasibility = "not_computed"
    overall_upper_passes = partition_upper_passes and not missing_polarities
    if lower_passes:
        status: RobustEvidenceStatus = "robustly_sufficient"
    elif not overall_upper_passes:
        status = "insufficient_even_optimistically"
    else:
        status = "lineage_review_required"
    uncertain_count = sum(
        edge.relation == "may_link"
        or not _close(
            edge.distinct_dependence_lower,
            edge.distinct_dependence_upper,
        )
        for edge in complete
    )
    exact = uncertain_count == 0 or exact_partition.completed
    notes = [
        (
            "This certifies lineage-aware facet sufficiency, not factual truth or causal validity."
            if require_facet_flow
            else "Controlled ablation: facet flow is reported but does not gate the certificate."
        )
    ]
    if not homogeneity_verified:
        notes.append(
            "Report-origin homogeneity was not completely annotated; this report-level "
            "result is conditional on the unverified homogeneity assumption."
        )
    if uncertain_count and not exact_partition.completed:
        notes.append(
            "The pessimistic partition is a safe relaxation and may combine mutually "
            "incompatible may-link relations."
        )
    elif uncertain_count:
        notes.append(
            "The pessimistic partition flow is exact over every feasible declared "
            "must/cannot/may unit assignment within the configured search limit."
        )
    if joint_feasibility == "exists":
        notes.append(
            "One feasible partition jointly satisfies the partition-dependent "
            "optimistic lineage and effective-evidence requirements"
            + (", including facet flow." if require_facet_flow else ".")
        )
    elif joint_feasibility == "does_not_exist":
        notes.append(
            "No feasible partition jointly satisfies the optimistic lineage, "
            "effective-evidence"
            + (", and facet-flow requirements." if require_facet_flow else " requirements.")
        )
    if missing_polarities:
        notes.append("A fixed required-polarity gate fails independently of the lineage partition.")
    return RobustEvidenceFlowBounds(
        question_id=blueprint.question_id,
        effective_evidence=effective,
        pessimistic_flow=pessimistic_flow,
        relaxed_pessimistic_flow=relaxed_pessimistic_flow,
        optimistic_flow=optimistic_flow,
        lineage_count_lower=lineage_lower,
        relaxed_lineage_count_lower=relaxed_lineage_lower,
        lineage_count_upper=lineage_upper,
        required_lineages=required_lineages,
        required_effective_evidence=required_effective,
        status=status,
        certificate_gap=_certificate_gap(
            effective,
            pessimistic_flow,
            optimistic_flow,
            lineage_lower,
            lineage_upper,
            required_effective,
            required_lineages,
            include_flow=require_facet_flow,
        ),
        total_pair_count=len(complete),
        uncertain_pair_count=uncertain_count,
        missing_pair_count=missing_count,
        report_origin_homogeneity_verified=homogeneity_verified,
        homogeneity_annotated_substantive_paper_count=len(homogeneity_annotated_papers),
        substantive_paper_count_for_homogeneity=len(substantive),
        required_polarities=required_polarities,
        missing_required_polarities=missing_polarities,
        strict_missing_pair_policy=strict_missing_pairs,
        exact_for_declared_lineage_uncertainty=exact,
        exact_partition_analysis=exact_partition,
        partition_bound_method=partition_method,
        joint_optimistic_feasibility=joint_feasibility,
        joint_optimistic_partition=exact_partition.joint_passing_partition,
        partition_relaxation_bottleneck_gap=relaxation_gap,
        bound_method=(
            "exact_partition_flow_with_box_dependence_bounds"
            if exact_partition.completed
            else "must_possible_partition_relaxation"
        ),
        notes=notes,
    )


def _validate_certificate_blueprint(blueprint: EvidenceBlueprint) -> None:
    required = [facet for facet in blueprint.required_facets if facet.is_required]
    if not required:
        raise ValueError("robust evidence certification requires at least one required facet")
    if blueprint.minimum_independent_lineages < 1:
        raise ValueError("robust evidence certification requires minimum_independent_lineages >= 1")


def _validate_single_origin_per_report(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    complete: list[LineageDependenceInterval],
) -> None:
    """Validate explicit report-origin annotations against report-level semantics.

    Certificate v6 partitions whole reports. A report may therefore have only one
    declared scientific origin across the required facets to which it contributes
    within one blueprint. This guard catches explicit contradictions; absent or
    incomplete annotations do not establish report-origin homogeneity.
    """

    required_facet_ids = {
        facet.facet_id for facet in blueprint.required_facets if facet.is_required
    }
    substantive_facets_by_paper: dict[str, set[str]] = defaultdict(set)
    origins_by_paper_facet: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    complete_papers: set[str] = set()
    for candidate in candidates:
        unknown = set(candidate.facet_origin_ids) - required_facet_ids
        if unknown:
            raise ValueError(
                "facet_origin_ids contains facets outside the required blueprint: "
                f"{candidate.paper_id}={','.join(sorted(unknown))}"
            )
        substantive_facets_by_paper[candidate.paper_id].update(
            facet_id
            for facet_id in required_facet_ids
            if candidate.facet_scores.get(facet_id, 0.0) > EPSILON
        )
        if candidate.facet_origin_annotations_complete:
            complete_papers.add(candidate.paper_id)
        for facet_id, origin_id in candidate.facet_origin_ids.items():
            if (
                facet_id in required_facet_ids
                and candidate.facet_scores.get(facet_id, 0.0) > EPSILON
            ):
                origins_by_paper_facet[candidate.paper_id][facet_id].add(origin_id)

    for paper_id in sorted(complete_papers):
        substantive = substantive_facets_by_paper[paper_id]
        annotated = set(origins_by_paper_facet[paper_id])
        missing = substantive - annotated
        if missing:
            raise ValueError(
                "complete facet-origin annotation is missing substantive required facets: "
                f"{paper_id}={','.join(sorted(missing))}"
            )

    conflicting_facets = {
        (paper_id, facet_id): sorted(origin_ids)
        for paper_id, by_facet in origins_by_paper_facet.items()
        for facet_id, origin_ids in by_facet.items()
        if len(origin_ids) > 1
    }
    if conflicting_facets:
        details = "; ".join(
            f"{paper_id}/{facet_id}={','.join(origin_ids)}"
            for (paper_id, facet_id), origin_ids in sorted(conflicting_facets.items())
        )
        raise ValueError("conflicting facet-origin annotations: " + details)

    origins_by_paper = {
        paper_id: {next(iter(origin_ids)) for origin_ids in by_facet.values() if origin_ids}
        for paper_id, by_facet in origins_by_paper_facet.items()
    }
    mixed = {
        paper_id: sorted(origin_ids)
        for paper_id, origin_ids in origins_by_paper.items()
        if len(origin_ids) > 1
    }
    if mixed:
        details = "; ".join(
            f"{paper_id}={','.join(origin_ids)}" for paper_id, origin_ids in sorted(mixed.items())
        )
        raise ValueError(
            "report-level certificate cannot represent facet-dependent mixed origins; "
            "joint v6 certification is unavailable: " + details
        )

    relation_by_pair = {
        tuple(sorted((edge.paper_id_a, edge.paper_id_b))): edge.relation for edge in complete
    }
    annotated_papers = sorted(
        paper_id for paper_id, origin_ids in origins_by_paper.items() if origin_ids
    )
    for paper_id_a, paper_id_b in combinations(annotated_papers, 2):
        origin_a = next(iter(origins_by_paper[paper_id_a]))
        origin_b = next(iter(origins_by_paper[paper_id_b]))
        relation = relation_by_pair[(paper_id_a, paper_id_b)]
        if origin_a == origin_b and relation != "must_link":
            raise ValueError(
                "identical explicit origin IDs require a must_link relation: "
                f"{paper_id_a},{paper_id_b}={origin_a}"
            )
        if origin_a != origin_b and relation == "must_link":
            raise ValueError(
                "distinct explicit origin IDs contradict a must_link relation: "
                f"{paper_id_a}={origin_a}; {paper_id_b}={origin_b}"
            )


def _effective_evidence_bounds_from_complete(
    candidates: list[TraceCandidate],
    complete: list[LineageDependenceInterval],
    *,
    facet_ids: set[str] | None = None,
) -> EffectiveEvidenceBounds:
    weights = aggregate_paper_weights(candidates, facet_ids=facet_ids)
    hard_units = _partition_units(candidates, complete, include_may_links=False)
    substantive_papers = {paper_id for paper_id, weight in weights.items() if weight > EPSILON}
    substantive_hard_units = {hard_units[paper_id] for paper_id in substantive_papers}
    if not substantive_hard_units:
        lower = upper = 0.0
    else:
        lower = 1.0
        upper = float(len(substantive_hard_units))
    return EffectiveEvidenceBounds(
        lower=_clean_float(lower),
        upper=_clean_float(upper),
        paper_weights=weights,
        raw_paper_count=len(weights),
        substantive_paper_count=len(substantive_papers),
        bound_method="universal_unit_partition_bounds",
        exact_for_box_uncertainty=False,
    )


def _answer_weighted_candidates(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
) -> list[TraceCandidate]:
    """Project facet scores onto roles that can support this answer blueprint."""

    required = [facet for facet in blueprint.required_facets if facet.is_required]
    weighted: list[TraceCandidate] = []
    for candidate in candidates:
        compatible_scores = {
            facet.facet_id: _certifiable_facet_score(candidate, facet) for facet in required
        }
        weighted.append(candidate.model_copy(update={"facet_scores": compatible_scores}))
    return weighted


def _missing_required_polarities(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
) -> list[str]:
    required = [facet for facet in blueprint.required_facets if facet.is_required]
    return sorted(
        polarity
        for polarity in {
            value for value in blueprint.required_polarities if value not in {"any", "unknown"}
        }
        if not any(
            candidate.polarity_scores.get(polarity, 0.0) >= 0.50
            and any(
                _certifiable_facet_score(candidate, facet) >= CERTIFIABLE_FACET_THRESHOLD
                for facet in required
            )
            for candidate in candidates
        )
    )


def _certifiable_facet_score(
    candidate: TraceCandidate,
    facet: EvidenceFacet,
) -> float:
    return (
        evidence_quality(candidate)
        * candidate.facet_scores.get(facet.facet_id, 0.0)
        * role_compatibility(
            candidate,
            facet.biological_scale,
            facet.evidence_type,
            facet.polarity_requirement,
        )
    )


def _complete_intervals(
    candidates: list[TraceCandidate],
    supplied: list[LineageDependenceInterval],
    *,
    strict_missing_pairs: bool,
) -> tuple[list[LineageDependenceInterval], int]:
    paper_ids = sorted({candidate.paper_id for candidate in candidates})
    known_papers = set(paper_ids)
    supplied_by_pair: dict[tuple[str, str], LineageDependenceInterval] = {}
    for edge in supplied:
        certification_edge = _edge_for_certificate(edge)
        key = _edge_key(certification_edge)
        unknown = sorted(set(key) - known_papers)
        if unknown:
            raise ValueError(f"lineage interval references unknown papers: {', '.join(unknown)}")
        if key in supplied_by_pair:
            raise ValueError(f"duplicate lineage interval for pair {key[0]} / {key[1]}")
        supplied_by_pair[key] = certification_edge.model_copy(
            update={"paper_id_a": key[0], "paper_id_b": key[1]}
        )

    base_units = paper_independence_units(candidates)
    must_union = _DisjointSet(paper_ids)
    by_base_unit: dict[str, list[str]] = defaultdict(list)
    for paper_id, unit in base_units.items():
        by_base_unit[unit].append(paper_id)
    for papers in by_base_unit.values():
        for paper_id in papers[1:]:
            must_union.union(papers[0], paper_id)
    for edge in supplied_by_pair.values():
        if edge.relation == "must_link":
            must_union.union(edge.paper_id_a, edge.paper_id_b)
    for edge in supplied_by_pair.values():
        if edge.relation == "cannot_link" and must_union.find(edge.paper_id_a) == must_union.find(
            edge.paper_id_b
        ):
            raise ValueError(
                "cannot_link contradicts a shared resource or transitive must_link for "
                f"{edge.paper_id_a} / {edge.paper_id_b}"
            )

    component_by_paper = {paper_id: must_union.find(paper_id) for paper_id in paper_ids}
    cannot_by_component_pair: dict[tuple[str, str], list[LineageDependenceInterval]] = defaultdict(
        list
    )
    must_by_component: dict[str, list[LineageDependenceInterval]] = defaultdict(list)
    for edge in supplied_by_pair.values():
        if edge.relation == "cannot_link":
            component_pair = _pair_key(
                component_by_paper[edge.paper_id_a],
                component_by_paper[edge.paper_id_b],
            )
            cannot_by_component_pair[component_pair].append(edge)
        elif edge.relation == "must_link":
            must_by_component[component_by_paper[edge.paper_id_a]].append(edge)

    base_by_pair: dict[tuple[str, str], LineageDependenceInterval] = {}
    missing_count = 0
    for paper_a, paper_b in combinations(paper_ids, 2):
        key = (paper_a, paper_b)
        supplied_edge = supplied_by_pair.get(key)
        if component_by_paper[paper_a] == component_by_paper[paper_b]:
            if supplied_edge is not None and supplied_edge.relation == "cannot_link":
                raise ValueError(
                    f"cannot_link contradicts a must-link component for {paper_a} / {paper_b}"
                )
            component_must_edges = must_by_component[component_by_paper[paper_a]]
            source = (
                f"{supplied_edge.source}+hard_resource_closure"
                if supplied_edge is not None
                else "hard_resource_or_must_link_closure"
            )
            base_by_pair[key] = LineageDependenceInterval(
                paper_id_a=paper_a,
                paper_id_b=paper_b,
                distinct_dependence_lower=1.0,
                distinct_dependence_upper=1.0,
                relation="must_link",
                review_status=(
                    "frozen"
                    if all(edge.review_status == "frozen" for edge in component_must_edges)
                    else "human_reviewed"
                ),
                source=source,
                review_cost=supplied_edge.review_cost if supplied_edge else 1.0,
            )
        elif supplied_edge is not None:
            base_by_pair[key] = supplied_edge
        else:
            missing_count += 1
            if strict_missing_pairs:
                base_by_pair[key] = LineageDependenceInterval(
                    paper_id_a=paper_a,
                    paper_id_b=paper_b,
                    source="strict_missing_pair",
                )
            else:
                base_by_pair[key] = LineageDependenceInterval(
                    paper_id_a=paper_a,
                    paper_id_b=paper_b,
                    distinct_dependence_lower=0.0,
                    distinct_dependence_upper=0.0,
                    relation="cannot_link",
                    source="missing_pair_assumed_independent",
                )

    cross_edges_by_component_pair: dict[tuple[str, str], list[LineageDependenceInterval]] = (
        defaultdict(list)
    )
    for edge in base_by_pair.values():
        left_component = component_by_paper[edge.paper_id_a]
        right_component = component_by_paper[edge.paper_id_b]
        if left_component == right_component:
            continue
        cross_edges_by_component_pair[_pair_key(left_component, right_component)].append(edge)

    component_envelopes = {
        component_pair: (
            min(edge.distinct_dependence_lower for edge in edges),
            max(edge.distinct_dependence_upper for edge in edges),
            all(edge.review_status == "frozen" for edge in edges),
        )
        for component_pair, edges in cross_edges_by_component_pair.items()
        if component_pair in cannot_by_component_pair
    }

    complete: list[LineageDependenceInterval] = []
    for key in sorted(base_by_pair):
        base_edge = base_by_pair[key]
        component_pair = _pair_key(
            component_by_paper[base_edge.paper_id_a],
            component_by_paper[base_edge.paper_id_b],
        )
        envelope = component_envelopes.get(component_pair)
        if envelope is None or base_edge.relation == "must_link":
            complete.append(base_edge)
            continue
        lower, upper, all_intervals_frozen = envelope
        relation_constraints = cannot_by_component_pair[component_pair]
        complete.append(
            LineageDependenceInterval(
                paper_id_a=base_edge.paper_id_a,
                paper_id_b=base_edge.paper_id_b,
                distinct_dependence_lower=lower,
                distinct_dependence_upper=upper,
                relation="cannot_link",
                review_status=(
                    "frozen"
                    if all_intervals_frozen
                    and all(edge.review_status == "frozen" for edge in relation_constraints)
                    else "human_reviewed"
                ),
                source=f"{base_edge.source}+cannot_link_component_envelope",
                review_cost=base_edge.review_cost,
            )
        )
    return complete, missing_count


def _edge_for_certificate(
    edge: LineageDependenceInterval,
) -> LineageDependenceInterval:
    """Widen unreviewed suggestions so they cannot prove either certificate bound."""

    if edge.review_status != "suggested":
        return edge
    return edge.model_copy(
        update={
            "distinct_dependence_lower": 0.0,
            "distinct_dependence_upper": 1.0,
            "relation": "may_link",
            "source": f"{edge.source}:unreviewed_widened",
        }
    )


def _partition_units(
    candidates: list[TraceCandidate],
    complete: list[LineageDependenceInterval],
    *,
    include_may_links: bool,
) -> dict[str, str]:
    paper_ids = sorted({candidate.paper_id for candidate in candidates})
    units = _DisjointSet(paper_ids)
    base = paper_independence_units(candidates)
    by_unit: dict[str, list[str]] = defaultdict(list)
    for paper_id, unit in base.items():
        by_unit[unit].append(paper_id)
    for papers in by_unit.values():
        for paper_id in papers[1:]:
            units.union(papers[0], paper_id)
    for edge in complete:
        if edge.relation == "must_link" or (include_may_links and edge.relation == "may_link"):
            units.union(edge.paper_id_a, edge.paper_id_b)
    return {paper_id: f"ROBUST:{units.find(paper_id)}" for paper_id in paper_ids}


def _review_outcome(
    edge: LineageDependenceInterval,
    *,
    high: bool,
) -> LineageDependenceInterval:
    relation: LineageRelation
    if edge.relation == "may_link":
        relation = "must_link" if high else "cannot_link"
        if high:
            lower = upper = 1.0
        else:
            lower = edge.distinct_dependence_lower
            upper = edge.distinct_dependence_upper
    else:
        relation = edge.relation
        endpoint = edge.distinct_dependence_upper if high else edge.distinct_dependence_lower
        lower = upper = endpoint
    return edge.model_copy(
        update={
            "distinct_dependence_lower": lower,
            "distinct_dependence_upper": upper,
            "relation": relation,
            "review_status": "human_reviewed",
            "source": f"{edge.source}:reviewed_{'high' if high else 'low'}",
        }
    )


def _screening_impact(
    edge: LineageDependenceInterval,
    weights: dict[str, float],
) -> float:
    interval_width = (
        1.0 - edge.distinct_dependence_lower
        if edge.relation == "may_link"
        else edge.distinct_dependence_upper - edge.distinct_dependence_lower
    )
    denominator_impact = (
        2.0 * weights.get(edge.paper_id_a, 0.0) * weights.get(edge.paper_id_b, 0.0) * interval_width
    )
    binary_link_bonus = (
        min(
            weights.get(edge.paper_id_a, 0.0),
            weights.get(edge.paper_id_b, 0.0),
        )
        if edge.relation == "may_link"
        else 0.0
    )
    return (denominator_impact + binary_link_bonus) / edge.review_cost


def _certificate_gap(
    effective: EffectiveEvidenceBounds,
    pessimistic_flow: EvidenceFlowResult,
    optimistic_flow: EvidenceFlowResult,
    lineage_lower: int,
    lineage_upper: int,
    required_effective: float,
    required_lineages: int,
    *,
    include_flow: bool = True,
) -> float:
    effective_gap = (effective.upper - effective.lower) / max(1.0, required_effective)
    lineage_gap = (lineage_upper - lineage_lower) / max(1, required_lineages)
    flow_gap = (
        max(
            0.0,
            optimistic_flow.bottleneck_coverage - pessimistic_flow.bottleneck_coverage,
        )
        if include_flow
        else 0.0
    )
    return _clean_float(effective_gap + lineage_gap + flow_gap)


def _outcome_separation(
    low: RobustEvidenceFlowBounds,
    high: RobustEvidenceFlowBounds,
) -> float:
    required_effective = max(1.0, low.required_effective_evidence)
    required_lineages = max(1, low.required_lineages)
    low_effective = (low.effective_evidence.lower + low.effective_evidence.upper) / 2.0
    high_effective = (high.effective_evidence.lower + high.effective_evidence.upper) / 2.0
    effective_distance = abs(low_effective - high_effective) / required_effective
    lineage_distance = abs(
        (low.lineage_count_lower + low.lineage_count_upper)
        - (high.lineage_count_lower + high.lineage_count_upper)
    ) / (2.0 * required_lineages)
    flow_distance = abs(
        low.pessimistic_flow.bottleneck_coverage - high.pessimistic_flow.bottleneck_coverage
    )
    return _clean_float(effective_distance + lineage_distance + flow_distance)


def _flow_is_complete(flow: EvidenceFlowResult) -> bool:
    return not flow.unresolved_facets and flow.bottleneck_coverage >= 1.0 - 1e-6


def _edge_key(edge: LineageDependenceInterval) -> tuple[str, str]:
    return _pair_key(edge.paper_id_a, edge.paper_id_b)


def _pair_key(paper_a: str, paper_b: str) -> tuple[str, str]:
    return (paper_a, paper_b) if paper_a < paper_b else (paper_b, paper_a)


def _clean_float(value: float) -> float:
    if abs(value - round(value)) <= 1e-12:
        return float(round(value))
    return float(value)


def _close(left: float, right: float) -> bool:
    return abs(float(left) - float(right)) <= 1e-12


class _DisjointSet:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        smaller, larger = sorted((left_root, right_root))
        self.parent[larger] = smaller
