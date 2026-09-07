from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any

from pfempire.trace.robust_lineage import (
    LineageDependenceInterval,
    RobustEvidenceFlowBounds,
    robust_evidence_flow,
)
from pfempire.trace.schemas import EvidenceBlueprint, EvidenceFacet, TraceCandidate


EXPERIMENT_ID = "E053"
EXPERIMENT_VERSION = "controlled_component_isolation_v1"


@dataclass(frozen=True)
class ComponentIsolationCase:
    case_id: str
    isolated_component: str
    design_rationale: str
    candidates: tuple[TraceCandidate, ...]
    blueprint: EvidenceBlueprint
    intervals: tuple[LineageDependenceInterval, ...]


def build_component_isolation_cases() -> tuple[ComponentIsolationCase, ...]:
    """Return three deterministic cases, each isolating one certificate gate."""

    shared_blueprint = _blueprint("shared_capacity", ("f1", "f2"), minimum_lineages=1)
    shared_candidate = _candidate(
        "SC1",
        {"f1": 1.0, "f2": 1.0},
        scale_scores={"methodological": 1.0},
    )
    shared = ComponentIsolationCase(
        case_id="shared_capacity_double_count",
        isolated_component="shared_origin_capacity",
        design_rationale=(
            "One origin supports two required facets whose total demand exceeds one "
            "origin-capacity unit. Removing shared capacity permits double use."
        ),
        candidates=(shared_candidate,),
        blueprint=shared_blueprint,
        intervals=(),
    )

    neff_blueprint = _blueprint("effective_evidence", ("f1",), minimum_lineages=3)
    neff_candidates = tuple(
        _candidate(
            f"NE{index}",
            {"f1": 1.0},
            scale_scores={"methodological": 1.0},
        )
        for index in range(1, 4)
    )
    neff = ComponentIsolationCase(
        case_id="effective_evidence_residual_dependence",
        isolated_component="effective_evidence",
        design_rationale=(
            "Three distinct origins cover the facet but retain pairwise dependence "
            "0.8, reducing effective evidence below the prespecified floor."
        ),
        candidates=neff_candidates,
        blueprint=neff_blueprint,
        intervals=_complete_distinct_intervals(neff_candidates, dependence=0.8),
    )

    flow_blueprint = _blueprint("joint_flow", ("anchor", "missing"), minimum_lineages=3)
    flow_candidates = tuple(
        _candidate(
            f"JF{index}",
            {"anchor": 1.0, "missing": 0.0},
            scale_scores={"methodological": 1.0},
        )
        for index in range(1, 4)
    )
    joint_flow = ComponentIsolationCase(
        case_id="joint_flow_required_facet_gap",
        isolated_component="joint_facet_flow",
        design_rationale=(
            "Three independent substantive origins pass count and effective-evidence "
            "gates, but none can supply the second required facet."
        ),
        candidates=flow_candidates,
        blueprint=flow_blueprint,
        intervals=_complete_distinct_intervals(flow_candidates, dependence=0.0),
    )
    return shared, neff, joint_flow


def evaluate_component_isolation() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in build_component_isolation_cases():
        primary = robust_evidence_flow(
            list(case.candidates),
            case.blueprint,
            list(case.intervals),
        )
        ablated = _evaluate_ablation(case)
        rows.extend(
            (
                _result_row(case, "full_certificate", primary),
                _result_row(case, f"without_{case.isolated_component}", ablated),
            )
        )
    _validate_expected_isolation(rows)
    return rows


def summarize_component_isolation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    case_ids = sorted({str(row["case_id"]) for row in rows})
    return {
        "experiment_id": EXPERIMENT_ID,
        "experiment_version": EXPERIMENT_VERSION,
        "statistical_unit": "deterministic_synthetic_boundary_case",
        "case_count": len(case_ids),
        "arm_count": len(rows),
        "all_full_certificates_reject": all(
            row["certificate_status"] != "robustly_sufficient"
            for row in rows
            if row["arm"] == "full_certificate"
        ),
        "all_targeted_ablations_false_accept": all(
            row["certificate_status"] == "robustly_sufficient"
            for row in rows
            if row["arm"] != "full_certificate"
        ),
        "inference_performed": False,
        "claim_boundary": (
            "These constructed cases establish component necessity for the declared "
            "decision rule. They do not estimate real-world effect frequency, safety, "
            "utility, or superiority over another RAG system."
        ),
    }


def _evaluate_ablation(case: ComponentIsolationCase) -> RobustEvidenceFlowBounds:
    kwargs: dict[str, Any] = {}
    if case.isolated_component == "shared_origin_capacity":
        kwargs["lineage_capacity"] = sum(
            0.60 * max(1, facet.minimum_items)
            for facet in case.blueprint.required_facets
            if facet.is_required
        )
    elif case.isolated_component == "effective_evidence":
        kwargs["effective_evidence_ratio"] = 0.0
    elif case.isolated_component == "joint_facet_flow":
        kwargs["require_facet_flow"] = False
    else:
        raise RuntimeError(f"unknown isolated component: {case.isolated_component}")
    return robust_evidence_flow(
        list(case.candidates),
        case.blueprint,
        list(case.intervals),
        **kwargs,
    )


def _result_row(
    case: ComponentIsolationCase,
    arm: str,
    result: RobustEvidenceFlowBounds,
) -> dict[str, Any]:
    return {
        "experiment_id": EXPERIMENT_ID,
        "experiment_version": EXPERIMENT_VERSION,
        "case_id": case.case_id,
        "isolated_component": case.isolated_component,
        "design_rationale": case.design_rationale,
        "arm": arm,
        "paper_count": len({candidate.paper_id for candidate in case.candidates}),
        "required_lineages": result.required_lineages,
        "required_effective_evidence": result.required_effective_evidence,
        "lineage_count_lower": result.lineage_count_lower,
        "effective_evidence_lower": result.effective_evidence.lower,
        "pessimistic_flow_bottleneck": result.pessimistic_flow.bottleneck_coverage,
        "unresolved_facets": list(result.pessimistic_flow.unresolved_facets),
        "certificate_status": result.status,
        "partition_bound_method": result.partition_bound_method,
        "exact_search_completed": result.exact_partition_analysis.completed,
        "expected_sufficient_under_hidden_design": False,
        "false_sufficient_decision": result.status == "robustly_sufficient",
    }


def _validate_expected_isolation(rows: list[dict[str, Any]]) -> None:
    if len(rows) != 6:
        raise RuntimeError("E053 must contain exactly three paired component cases")
    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(str(row["case_id"]), []).append(row)
    for case_id, case_rows in by_case.items():
        primary = next(row for row in case_rows if row["arm"] == "full_certificate")
        ablated = next(row for row in case_rows if row["arm"] != "full_certificate")
        if primary["certificate_status"] == "robustly_sufficient":
            raise RuntimeError(f"full certificate failed to reject E053 case {case_id}")
        if ablated["certificate_status"] != "robustly_sufficient":
            raise RuntimeError(f"targeted ablation did not flip E053 case {case_id}")
        if not primary["exact_search_completed"] or not ablated["exact_search_completed"]:
            raise RuntimeError(f"E053 case {case_id} did not complete exact analysis")


def _blueprint(
    suffix: str,
    facet_ids: tuple[str, ...],
    *,
    minimum_lineages: int,
) -> EvidenceBlueprint:
    question_id = f"E053_{suffix}"
    return EvidenceBlueprint(
        question_id=question_id,
        question=f"Controlled component-isolation case: {suffix}",
        task_mode="controlled_evidence_accounting",
        required_facets=[
            EvidenceFacet(
                facet_id=facet_id,
                question_id=question_id,
                label=facet_id,
                description=f"Required controlled facet {facet_id}",
                facet_type="controlled",
                biological_scale="methodological",
                evidence_type="methodological",
                polarity_requirement="any",
                minimum_items=1,
                is_required=True,
            )
            for facet_id in facet_ids
        ],
        minimum_independent_lineages=minimum_lineages,
        review_status="frozen",
    )


def _candidate(
    paper_id: str,
    facet_scores: dict[str, float],
    *,
    scale_scores: dict[str, float],
) -> TraceCandidate:
    return TraceCandidate(
        chunk_id=f"{paper_id}_C1",
        paper_id=paper_id,
        text=f"Controlled evidence from {paper_id}",
        token_cost=100,
        retrieval_score=20.0,
        section="results",
        section_quality=1.0,
        facet_scores=facet_scores,
        scale_scores=scale_scores,
        evidence_type_scores={"methodological": 1.0},
        polarity_scores={"any": 1.0},
        publication_family_id=paper_id,
        study_lineage_id=paper_id,
    )


def _complete_distinct_intervals(
    candidates: tuple[TraceCandidate, ...],
    *,
    dependence: float,
) -> tuple[LineageDependenceInterval, ...]:
    paper_ids = sorted({candidate.paper_id for candidate in candidates})
    return tuple(
        LineageDependenceInterval(
            paper_id_a=paper_a,
            paper_id_b=paper_b,
            distinct_dependence_lower=dependence,
            distinct_dependence_upper=dependence,
            relation="cannot_link",
            review_status="frozen",
            source="E053_hidden_synthetic_truth",
        )
        for paper_a, paper_b in combinations(paper_ids, 2)
    )
