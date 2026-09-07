from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from pfempire.trace.lineage_trap_v1 import (
    EXACT_PARTITION_MAX_COMPONENTS,
    EXACT_PARTITION_MAX_SEARCH_STATES,
    FACET_THRESHOLD,
    REQUIRED_LINEAGES,
    LineageTrapV1Scenario,
    scenario_input_payload,
)
from pfempire.trace.robust_lineage import (
    LineageDependenceInterval,
    RobustEvidenceFlowBounds,
    robust_evidence_flow,
)


EXPERIMENT_ID = "E051"
EXPERIMENT_VERSION = "controlled_policy_sensitivity_v2"
RESULTS_SCHEMA_VERSION = "controlled_policy_sensitivity_results_v2"
STATISTICAL_UNIT = "hidden_synthetic_scenario_template"
PRIMARY_EFFECTIVE_EVIDENCE_RATIO = 0.90
PRIMARY_LINEAGE_CAPACITY = 1.0
TAU_GRID = (0.50, 0.55, 0.60, 0.65, 0.70)
K_GRID = (2, 3, 4)
EFFECTIVE_EVIDENCE_RATIO_GRID = (0.75, 0.90, 1.00)
LINEAGE_CAPACITY_GRID = (0.75, 1.00, 1.25)
DISTINCT_DEPENDENCE_RHO_GRID = (0.00, 0.10, 0.25, 0.50, 0.75)
EXACT_COMPONENT_LIMIT_GRID = (4, 5, 9)
COMPONENT_ABLATIONS = (
    "no_Neff",
    "no_facet_flow",
    "no_shared_capacity",
    "fail_open_unresolved",
    "forced_fallback",
)


@dataclass(frozen=True)
class PolicySetting:
    """One deterministic one-at-a-time policy or component-ablation arm."""

    setting_id: str
    axis: str
    axis_value: float | int | str | bool | None = None
    facet_threshold: float = FACET_THRESHOLD
    minimum_independent_lineages: int = REQUIRED_LINEAGES
    effective_evidence_ratio: float = PRIMARY_EFFECTIVE_EVIDENCE_RATIO
    lineage_capacity: float = PRIMARY_LINEAGE_CAPACITY
    distinct_dependence_rho: float = 0.0
    exact_partition_max_components: int = EXACT_PARTITION_MAX_COMPONENTS
    exact_partition_max_search_states: int = EXACT_PARTITION_MAX_SEARCH_STATES
    strict_missing_pairs: bool = True
    require_effective_evidence: bool = True
    require_facet_flow: bool = True
    share_lineage_capacity: bool = True
    force_safe_relaxation: bool = False

    @property
    def is_primary(self) -> bool:
        return self.axis == "primary"

    @property
    def is_component_ablation(self) -> bool:
        return self.axis == "component_ablation"


def build_policy_settings() -> tuple[PolicySetting, ...]:
    """Return the preregisterable primary, OAT, and component-ablation matrix."""

    settings = [PolicySetting(setting_id="primary", axis="primary")]
    settings.extend(
        PolicySetting(
            setting_id=f"tau_{_float_id(value)}",
            axis="facet_threshold",
            axis_value=value,
            facet_threshold=value,
        )
        for value in TAU_GRID
        if not math.isclose(value, FACET_THRESHOLD)
    )
    settings.extend(
        PolicySetting(
            setting_id=f"K_{value}",
            axis="minimum_independent_lineages",
            axis_value=value,
            minimum_independent_lineages=value,
        )
        for value in K_GRID
        if value != REQUIRED_LINEAGES
    )
    settings.extend(
        PolicySetting(
            setting_id=f"effective_ratio_{_float_id(value)}",
            axis="effective_evidence_ratio",
            axis_value=value,
            effective_evidence_ratio=value,
        )
        for value in EFFECTIVE_EVIDENCE_RATIO_GRID
        if not math.isclose(value, PRIMARY_EFFECTIVE_EVIDENCE_RATIO)
    )
    settings.extend(
        PolicySetting(
            setting_id=f"capacity_{_float_id(value)}",
            axis="lineage_capacity",
            axis_value=value,
            lineage_capacity=value,
        )
        for value in LINEAGE_CAPACITY_GRID
        if not math.isclose(value, PRIMARY_LINEAGE_CAPACITY)
    )
    settings.extend(
        PolicySetting(
            setting_id=f"rho_{_float_id(value)}",
            axis="distinct_dependence_rho",
            axis_value=value,
            distinct_dependence_rho=value,
        )
        for value in DISTINCT_DEPENDENCE_RHO_GRID
        if not math.isclose(value, 0.0)
    )
    settings.extend(
        PolicySetting(
            setting_id=f"exact_components_{value}",
            axis="exact_partition_max_components",
            axis_value=value,
            exact_partition_max_components=value,
        )
        for value in EXACT_COMPONENT_LIMIT_GRID
        if value != EXACT_PARTITION_MAX_COMPONENTS
    )
    settings.extend(
        (
            PolicySetting(
                setting_id="ablation_no_Neff",
                axis="component_ablation",
                axis_value="no_Neff",
                require_effective_evidence=False,
            ),
            PolicySetting(
                setting_id="ablation_no_facet_flow",
                axis="component_ablation",
                axis_value="no_facet_flow",
                require_facet_flow=False,
            ),
            PolicySetting(
                setting_id="ablation_no_shared_capacity",
                axis="component_ablation",
                axis_value="no_shared_capacity",
                share_lineage_capacity=False,
            ),
            PolicySetting(
                setting_id="ablation_fail_open_unresolved",
                axis="component_ablation",
                axis_value="fail_open_unresolved",
                strict_missing_pairs=False,
            ),
            PolicySetting(
                setting_id="ablation_forced_fallback",
                axis="component_ablation",
                axis_value="forced_fallback",
                force_safe_relaxation=True,
            ),
        )
    )
    parsed = tuple(settings)
    if len({setting.setting_id for setting in parsed}) != len(parsed):
        raise RuntimeError("E051 policy setting IDs are not unique")
    return parsed


def evaluate_policy_sensitivity(
    scenarios: Iterable[LineageTrapV1Scenario],
    *,
    settings: Iterable[PolicySetting] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate fixed synthetic scenario templates without sampling or inference."""

    scenario_list = list(scenarios)
    setting_list = list(settings or build_policy_settings())
    if len({scenario.scenario_id for scenario in scenario_list}) != len(scenario_list):
        raise ValueError("E051 scenarios must have unique scenario IDs")
    if len({setting.setting_id for setting in setting_list}) != len(setting_list):
        raise ValueError("E051 settings must have unique setting IDs")
    rows = [
        _evaluate_setting(scenario, setting)
        for scenario in scenario_list
        for setting in setting_list
    ]
    return sorted(rows, key=lambda row: (row["scenario_id"], row["setting_id"]))


def summarize_policy_sensitivity(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Summarize exact controlled counts; no sampling distribution is implied."""

    row_list = list(rows)
    by_setting: dict[str, dict[str, Any]] = {}
    for setting_id in sorted({str(row["setting_id"]) for row in row_list}):
        selected = [row for row in row_list if row["setting_id"] == setting_id]
        statuses = Counter(str(row["certificate_status"]) for row in selected)
        false_certificates = [row for row in selected if row["false_certificate_acceptance"]]
        corrupted_false_certificates = [
            row for row in false_certificates if row["origin_condition"] == "corrupted_independent"
        ]
        by_setting[setting_id] = {
            "axis": selected[0]["policy_axis"],
            "axis_value": selected[0]["policy_axis_value"],
            "scenario_template_count": len(selected),
            "sufficient_count": sum(bool(row["is_sufficient"]) for row in selected),
            "false_certificate_count": len(false_certificates),
            "false_certificate_count_excluding_corrupted_labels": (
                len(false_certificates) - len(corrupted_false_certificates)
            ),
            "corrupted_label_false_certificate_count": len(corrupted_false_certificates),
            "sufficient_control_not_certified_count": sum(
                bool(row["sufficient_control_not_certified"]) for row in selected
            ),
            "status_counts": dict(sorted(statuses.items())),
            "safe_relaxation_count": sum(
                row["partition_bound_method"] == "safe_relaxation" for row in selected
            ),
        }
    scenario_ids = {str(row["scenario_id"]) for row in row_list}
    setting_ids = {str(row["setting_id"]) for row in row_list}
    return {
        "statistical_unit": STATISTICAL_UNIT,
        "inference_performed": False,
        "randomness_used": False,
        "scenario_template_count": len(scenario_ids),
        "policy_setting_count": len(setting_ids),
        "result_row_count": len(row_list),
        "complete_cartesian_matrix": len(row_list) == len(scenario_ids) * len(setting_ids),
        "by_setting": by_setting,
    }


def validate_unchanged_e034_scenarios(
    scenarios: Iterable[LineageTrapV1Scenario],
    frozen_inputs: dict[str, Any],
) -> None:
    """Refuse E051 if its generated scenario payloads differ from frozen E034."""

    observed = [
        {**scenario_input_payload(scenario), "input_hash": scenario.input_hash}
        for scenario in scenarios
    ]
    expected = frozen_inputs.get("scenarios")
    if frozen_inputs.get("experiment_id") != "E034" or not isinstance(expected, list):
        raise ValueError("source inputs are not the frozen E034 scenario artifact")
    if observed != expected:
        observed_by_id = {item["scenario_id"]: item for item in observed}
        expected_by_id = {item["scenario_id"]: item for item in expected}
        differing = sorted(
            scenario_id
            for scenario_id in set(observed_by_id) | set(expected_by_id)
            if observed_by_id.get(scenario_id) != expected_by_id.get(scenario_id)
        )
        preview = ", ".join(differing[:5]) or "unknown ordering/content delta"
        raise ValueError(f"E051 scenario inputs differ from frozen E034: {preview}")


def nonshared_lineage_capacity(
    scenario: LineageTrapV1Scenario,
    *,
    facet_threshold: float,
) -> float:
    """Return a nonbinding per-lineage source cap equal to total required demand."""

    return sum(
        facet_threshold * max(1, int(facet.minimum_items))
        for facet in scenario.blueprint.required_facets
        if facet.is_required
    )


def _evaluate_setting(
    scenario: LineageTrapV1Scenario,
    setting: PolicySetting,
) -> dict[str, Any]:
    blueprint = scenario.blueprint.model_copy(
        update={
            "minimum_independent_lineages": setting.minimum_independent_lineages,
        }
    )
    intervals = _override_distinct_dependence(
        scenario.intervals,
        setting.distinct_dependence_rho,
    )
    actual_capacity = (
        setting.lineage_capacity
        if setting.share_lineage_capacity
        else nonshared_lineage_capacity(
            scenario,
            facet_threshold=setting.facet_threshold,
        )
    )
    solver_ratio = setting.effective_evidence_ratio if setting.require_effective_evidence else 0.0
    result = robust_evidence_flow(
        list(scenario.candidates),
        blueprint,
        list(intervals),
        lineage_capacity=actual_capacity,
        facet_threshold=setting.facet_threshold,
        effective_evidence_ratio=solver_ratio,
        require_facet_flow=setting.require_facet_flow,
        strict_missing_pairs=setting.strict_missing_pairs,
        force_safe_relaxation=setting.force_safe_relaxation,
        exact_partition_max_components=setting.exact_partition_max_components,
        exact_partition_max_search_states=setting.exact_partition_max_search_states,
    )
    return _result_row(
        scenario,
        setting,
        result,
        actual_capacity=actual_capacity,
        solver_ratio=solver_ratio,
    )


def _override_distinct_dependence(
    intervals: tuple[LineageDependenceInterval, ...],
    rho: float,
) -> tuple[LineageDependenceInterval, ...]:
    if not math.isfinite(rho) or not 0.0 <= rho <= 1.0:
        raise ValueError("distinct dependence rho must be finite and between zero and one")
    if math.isclose(rho, 0.0):
        return intervals
    return tuple(
        edge
        if edge.relation == "must_link"
        else edge.model_copy(
            update={
                "distinct_dependence_lower": rho,
                "distinct_dependence_upper": rho,
                "source": f"{edge.source}:E051_rho_{rho:.2f}",
            }
        )
        for edge in intervals
    )


def _result_row(
    scenario: LineageTrapV1Scenario,
    setting: PolicySetting,
    result: RobustEvidenceFlowBounds,
    *,
    actual_capacity: float,
    solver_ratio: float,
) -> dict[str, Any]:
    exact = result.exact_partition_analysis
    sufficient = result.status == "robustly_sufficient"
    return {
        "experiment_id": EXPERIMENT_ID,
        "experiment_version": EXPERIMENT_VERSION,
        "results_schema_version": RESULTS_SCHEMA_VERSION,
        "statistical_unit": STATISTICAL_UNIT,
        "scenario_id": scenario.scenario_id,
        "scenario_input_hash": scenario.input_hash,
        "evidence_family": scenario.evidence_family,
        "origin_condition": scenario.origin_condition,
        "evaluation_role": scenario.evaluation_role,
        "replication_factor": scenario.replication_factor,
        "hidden_gold_origin_count": len(set(scenario.hidden_gold_origin_by_paper.values())),
        "expected_gold_sufficient": scenario.expected_gold_sufficient,
        "setting_id": setting.setting_id,
        "policy_axis": setting.axis,
        "policy_axis_value": setting.axis_value,
        "is_primary": setting.is_primary,
        "is_component_ablation": setting.is_component_ablation,
        "facet_threshold": setting.facet_threshold,
        "minimum_independent_lineages": setting.minimum_independent_lineages,
        "effective_evidence_ratio": setting.effective_evidence_ratio,
        "solver_effective_evidence_ratio": solver_ratio,
        "lineage_capacity": actual_capacity,
        "nominal_lineage_capacity": setting.lineage_capacity,
        "distinct_dependence_rho": setting.distinct_dependence_rho,
        "exact_partition_max_components": setting.exact_partition_max_components,
        "exact_partition_max_search_states": setting.exact_partition_max_search_states,
        "strict_missing_pairs": setting.strict_missing_pairs,
        "effective_evidence_gate_enabled": setting.require_effective_evidence,
        "facet_flow_gate_enabled": setting.require_facet_flow,
        "shared_lineage_capacity_enabled": setting.share_lineage_capacity,
        "forced_safe_relaxation": setting.force_safe_relaxation,
        "certificate_status": result.status,
        "is_sufficient": sufficient,
        "false_certificate_acceptance": bool(sufficient and not scenario.expected_gold_sufficient),
        "sufficient_control_not_certified": bool(
            not sufficient and scenario.expected_gold_sufficient
        ),
        "effective_evidence_lower": result.effective_evidence.lower,
        "effective_evidence_upper": result.effective_evidence.upper,
        "required_effective_evidence": result.required_effective_evidence,
        "lineage_count_lower": result.lineage_count_lower,
        "lineage_count_upper": result.lineage_count_upper,
        "pessimistic_flow_bottleneck": result.pessimistic_flow.bottleneck_coverage,
        "optimistic_flow_bottleneck": result.optimistic_flow.bottleneck_coverage,
        "pessimistic_unresolved_facet_count": len(result.pessimistic_flow.unresolved_facets),
        "missing_pair_count": result.missing_pair_count,
        "uncertain_pair_count": result.uncertain_pair_count,
        "partition_bound_method": result.partition_bound_method,
        "exact_search_completed": exact.completed,
        "exact_search_reason": exact.reason,
        "exact_component_count": exact.component_count,
        "exact_search_states": exact.search_states,
        "joint_optimistic_feasibility": result.joint_optimistic_feasibility,
    }


def _float_id(value: float) -> str:
    return f"{value:.2f}".replace(".", "p")
