from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from pfempire.trace.flow import compute_evidence_flow
from pfempire.trace.independence_units import paper_independence_units
from pfempire.trace.method_versions import (
    EVIDENCE_ACCOUNTING_VERSION,
    LINEAGE_GRAPH_SCHEMA_VERSION,
    ROBUST_LINEAGE_CERTIFICATE_VERSION,
)
from pfempire.trace.robust_lineage import (
    LineageDependenceInterval,
    RobustEvidenceFlowBounds,
    robust_evidence_flow,
)
from pfempire.trace.schemas import EvidenceBlueprint, EvidenceFacet, TraceCandidate


EXPERIMENT_ID = "E028"
EXPERIMENT_VERSION = "lineage_trap_v1"
SCENARIO_SCHEMA_VERSION = "lineage_trap_scenario_v1"
RESULTS_SCHEMA_VERSION = "lineage_trap_results_v1"
DEFAULT_SEED = 20260721
REPLICATION_FACTORS = (1, 2, 4, 8)
PRIMARY_FAMILIES = (
    "exact_clone",
    "paraphrased_same_study",
    "shared_cohort_dataset",
)
INDEPENDENT_CONTROL = "independent_origin_control"
PARTIAL_UNCERTAINTY_CONTROL = "partially_resolved_independent_control"
ORIGIN_CONDITIONS = (
    "adjudicated_gold",
    "unresolved",
    "corrupted_independent",
)
METHOD_IDS = (
    "raw_source_count",
    "exact_text_deduplication",
    "one_per_known_lineage",
    "facet_cover_no_lineage_uncertainty",
    ROBUST_LINEAGE_CERTIFICATE_VERSION,
)
FACET_THRESHOLD = 0.60
MINIMUM_ITEMS_PER_FACET = 3
REQUIRED_LINEAGES = 3
REQUIRED_EFFECTIVE_EVIDENCE = 0.90 * REQUIRED_LINEAGES
FIXED_TOKEN_BUDGET = 1_600
EXACT_PARTITION_MAX_COMPONENTS = 9
EXACT_PARTITION_MAX_SEARCH_STATES = 100_000

BASELINE_DEFINITIONS = {
    "raw_source_count": (
        "Treat every report as an independent unit; require at least three units and ordinary "
        "per-report facet flow to satisfy all frozen demands."
    ),
    "exact_text_deduplication": (
        "Collapse reports only when their exact UTF-8 text bytes match; require at least three "
        "remaining units and ordinary facet flow."
    ),
    "one_per_known_lineage": (
        "Collapse hard metadata resources and adjudicated must-link components, with no "
        "may-link uncertainty semantics; require at least three units and ordinary facet flow."
    ),
    "facet_cover_no_lineage_uncertainty": (
        "Treat every report as a separate capacity unit and require only complete frozen facet "
        "flow, without uncertain-origin analysis."
    ),
}
ALLOWED_CLAIM_STRENGTH = {
    "robustly_sufficient": "association",
    "lineage_review_required": "hypothesis_only",
    "insufficient_even_optimistically": "insufficient_evidence",
    "sufficient": "association",
    "insufficient": "insufficient_evidence",
}

PARAPHRASES = (
    "A controlled assay connected receptor engagement with the observed clinical phenotype.",
    "Experimental receptor binding was associated with the same measured patient phenotype.",
    "The study linked molecular receptor activity to the corresponding clinical observation.",
    "Biochemical engagement and the matched clinical phenotype were reported together.",
    "The analysis related receptor-level activity to the prespecified patient outcome.",
    "Molecular binding evidence was paired with the same clinical phenotype signal.",
    "The report connected the receptor mechanism with its observed clinical correlate.",
    "Assay evidence and the corresponding patient phenotype supported the same conclusion.",
)
SHARED_MECHANISM_TEXTS = (
    "Mechanism analysis A from the fixed shared cohort and dataset.",
    "Mechanism analysis B reusing the fixed shared cohort and dataset.",
    "Mechanism analysis C reusing the fixed shared cohort and dataset.",
    "Mechanism analysis D reusing the fixed shared cohort and dataset.",
    "Mechanism analysis E reusing the fixed shared cohort and dataset.",
    "Mechanism analysis F reusing the fixed shared cohort and dataset.",
    "Mechanism analysis G reusing the fixed shared cohort and dataset.",
    "Mechanism analysis H reusing the fixed shared cohort and dataset.",
)
SHARED_PHENOTYPE_TEXTS = (
    "Phenotype analysis A from the fixed shared cohort and dataset.",
    "Phenotype analysis B reusing the fixed shared cohort and dataset.",
    "Phenotype analysis C reusing the fixed shared cohort and dataset.",
    "Phenotype analysis D reusing the fixed shared cohort and dataset.",
    "Phenotype analysis E reusing the fixed shared cohort and dataset.",
    "Phenotype analysis F reusing the fixed shared cohort and dataset.",
    "Phenotype analysis G reusing the fixed shared cohort and dataset.",
    "Phenotype analysis H reusing the fixed shared cohort and dataset.",
)


@dataclass(frozen=True)
class LineageTrapV1Scenario:
    """A fixed evidence set for testing report-multiplicity certification failures."""

    scenario_id: str
    evidence_family: str
    origin_condition: str
    evaluation_role: str
    replication_factor: int
    blueprint: EvidenceBlueprint
    candidates: tuple[TraceCandidate, ...]
    intervals: tuple[LineageDependenceInterval, ...]
    hidden_gold_origin_by_paper: dict[str, str]
    scientific_information_units_by_paper: dict[str, tuple[str, ...]]
    expected_gold_sufficient: bool
    token_budget: int = FIXED_TOKEN_BUDGET
    facet_threshold: float = FACET_THRESHOLD
    seed: int = DEFAULT_SEED

    @property
    def input_hash(self) -> str:
        return sha256_json(scenario_input_payload(self))


def build_lineage_trap_v1_scenarios(
    *, seed: int = DEFAULT_SEED
) -> list[LineageTrapV1Scenario]:
    """Build the frozen 44-scenario E028 matrix deterministically."""

    scenarios = []
    for family in PRIMARY_FAMILIES:
        for condition in ORIGIN_CONDITIONS:
            for replication_factor in REPLICATION_FACTORS:
                scenarios.append(
                    _build_scenario(
                        family,
                        condition,
                        replication_factor,
                        seed=seed,
                    )
                )
    for replication_factor in REPLICATION_FACTORS:
        scenarios.append(
            _build_scenario(
                INDEPENDENT_CONTROL,
                "adjudicated_gold",
                replication_factor,
                seed=seed,
            )
        )
        scenarios.append(
            _build_scenario(
                PARTIAL_UNCERTAINTY_CONTROL,
                "partially_resolved",
                replication_factor,
                seed=seed,
            )
        )
    return scenarios


def scenario_input_payload(scenario: LineageTrapV1Scenario) -> dict[str, Any]:
    """Return the canonical, hashable scientific input for one scenario."""

    candidates = []
    for candidate in scenario.candidates:
        payload = candidate.model_dump(mode="json")



        if not payload.get("facet_origin_ids"):
            payload.pop("facet_origin_ids", None)
        if not payload.get("facet_origin_annotations_complete"):
            payload.pop("facet_origin_annotations_complete", None)
        candidates.append(payload)

    return {
        "experiment_id": EXPERIMENT_ID,
        "experiment_version": EXPERIMENT_VERSION,
        "scenario_schema_version": SCENARIO_SCHEMA_VERSION,
        "scenario_id": scenario.scenario_id,
        "evidence_family": scenario.evidence_family,
        "origin_condition": scenario.origin_condition,
        "evaluation_role": scenario.evaluation_role,
        "replication_factor": scenario.replication_factor,
        "seed": scenario.seed,
        "token_budget": scenario.token_budget,
        "selection_performed": False,
        "facet_threshold": scenario.facet_threshold,
        "blueprint": scenario.blueprint.model_dump(mode="json"),
        "candidates": candidates,
        "lineage_intervals": [edge.model_dump(mode="json") for edge in scenario.intervals],
        "hidden_gold_origin_by_paper": dict(sorted(scenario.hidden_gold_origin_by_paper.items())),
        "scientific_information_units_by_paper": {
            paper_id: list(units)
            for paper_id, units in sorted(
                scenario.scientific_information_units_by_paper.items()
            )
        },
        "expected_gold_sufficient": scenario.expected_gold_sufficient,
    }


def evaluate_lineage_trap_v1(
    scenarios: list[LineageTrapV1Scenario],
) -> list[dict[str, Any]]:
    """Evaluate all frozen baselines and the current certificate on fixed evidence sets."""

    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        robust = robust_evidence_flow(
            list(scenario.candidates),
            scenario.blueprint,
            list(scenario.intervals),
            facet_threshold=scenario.facet_threshold,
            strict_missing_pairs=True,
            exact_partition_max_components=EXACT_PARTITION_MAX_COMPONENTS,
            exact_partition_max_search_states=EXACT_PARTITION_MAX_SEARCH_STATES,
        )
        for method_id in METHOD_IDS:
            if method_id == ROBUST_LINEAGE_CERTIFICATE_VERSION:
                row = _evaluate_robust_method(scenario, robust)
            else:
                row = _evaluate_baseline(scenario, method_id)
            rows.append(row)
    _add_multiplicity_comparisons(rows)
    return sorted(
        rows,
        key=lambda row: (
            str(row["evidence_family"]),
            str(row["origin_condition"]),
            int(row["replication_factor"]),
            str(row["method_id"]),
        ),
    )


def summarize_lineage_trap_v1(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply the frozen H1-H5 rules without statistical inference."""

    h1_by_family: dict[str, Any] = {}
    h2_by_family: dict[str, Any] = {}
    h3_by_family: dict[str, Any] = {}
    corrupted_by_family: dict[str, Any] = {}
    for family in PRIMARY_FAMILIES:
        gold_baselines = [
            row
            for row in rows
            if row["evidence_family"] == family
            and row["origin_condition"] == "adjudicated_gold"
            and row["method_id"] != ROBUST_LINEAGE_CERTIFICATE_VERSION
        ]
        failing_methods = sorted(
            {
                str(row["method_id"])
                for row in gold_baselines
                if row["threshold_flip"] and row["false_certificate_acceptance"]
            }
        )
        first_false_acceptance = {
            method: min(
                int(row["replication_factor"])
                for row in gold_baselines
                if row["method_id"] == method
                and row["false_certificate_acceptance"]
            )
            for method in failing_methods
        }
        unresolved_hard_lineage = _method_rows(
            rows,
            family=family,
            condition="unresolved",
            method="one_per_known_lineage",
        )
        hard_lineage_false_acceptances = [
            int(row["replication_factor"])
            for row in unresolved_hard_lineage
            if row["threshold_flip"] and row["false_certificate_acceptance"]
        ]
        h1_by_family[family] = {
            "passed": bool(failing_methods) and bool(hard_lineage_false_acceptances),
            "failing_baselines": failing_methods,
            "first_false_acceptance_replication_factor": first_false_acceptance,
            "hard_lineage_unresolved_first_false_acceptance_factor": (
                min(hard_lineage_false_acceptances)
                if hard_lineage_false_acceptances
                else None
            ),
        }

        gold_robust = _method_rows(
            rows,
            family=family,
            condition="adjudicated_gold",
            method=ROBUST_LINEAGE_CERTIFICATE_VERSION,
        )
        decision_violations = [
            str(row["scenario_id"])
            for row in gold_robust
            if row["decision_invariance_violation"]
            or row["false_certificate_acceptance"]
        ]
        endpoint_violations = [
            str(row["scenario_id"])
            for row in gold_robust
            if row["core_endpoint_invariance_violation"]
        ]
        h2_by_family[family] = {
            "passed": not decision_violations,
            "decision_violation_scenarios": decision_violations,
            "core_endpoint_violation_scenarios": endpoint_violations,
        }

        unresolved_robust = _method_rows(
            rows,
            family=family,
            condition="unresolved",
            method=ROBUST_LINEAGE_CERTIFICATE_VERSION,
        )
        robust_false_acceptances = [
            str(row["scenario_id"])
            for row in unresolved_robust
            if row["is_sufficient"]
        ]
        h3_by_family[family] = {
            "passed": not robust_false_acceptances,
            "robust_false_acceptance_scenarios": robust_false_acceptances,
            "statuses": {
                str(row["replication_factor"]): row["certificate_status"]
                for row in unresolved_robust
            },
        }

        corrupted_robust = _method_rows(
            rows,
            family=family,
            condition="corrupted_independent",
            method=ROBUST_LINEAGE_CERTIFICATE_VERSION,
        )
        corrupted_false_acceptances = [
            str(row["scenario_id"])
            for row in corrupted_robust
            if row["false_certificate_acceptance"]
        ]
        corrupted_by_family[family] = {
            "sensitivity_observed": bool(corrupted_false_acceptances),
            "false_acceptance_scenarios": corrupted_false_acceptances,
        }

    independent = _method_rows(
        rows,
        family=INDEPENDENT_CONTROL,
        condition="adjudicated_gold",
        method=ROBUST_LINEAGE_CERTIFICATE_VERSION,
    )
    independent_first = next(
        (
            int(row["replication_factor"])
            for row in independent
            if row["is_sufficient"]
        ),
        None,
    )
    h4 = {
        "passed": independent_first == 4
        and all(
            bool(row["is_sufficient"])
            == bool(row["expected_gold_sufficient"])
            for row in independent
        ),
        "first_sufficient_replication_factor": independent_first,
        "statuses": {
            str(row["replication_factor"]): row["certificate_status"]
            for row in independent
        },
    }
    partial = _method_rows(
        rows,
        family=PARTIAL_UNCERTAINTY_CONTROL,
        condition="partially_resolved",
        method=ROBUST_LINEAGE_CERTIFICATE_VERSION,
    )
    h5 = {
        "passed": bool(partial)
        and all(bool(row["is_sufficient"]) for row in partial)
        and all(int(row["uncertain_pair_count"]) > 0 for row in partial)
        and all(bool(row["exact_search_completed"]) for row in partial)
        and any(int(row["declared_must_link_count"]) > 0 for row in partial)
        and all(int(row["declared_cannot_link_count"]) > 0 for row in partial),
        "statuses": {
            str(row["replication_factor"]): row["certificate_status"]
            for row in partial
        },
        "uncertain_pair_counts": {
            str(row["replication_factor"]): row["uncertain_pair_count"]
            for row in partial
        },
    }
    h1 = all(bool(value["passed"]) for value in h1_by_family.values())
    h2 = all(bool(value["passed"]) for value in h2_by_family.values())
    h3 = all(bool(value["passed"]) for value in h3_by_family.values())
    h4_passed = bool(h4["passed"])
    h5_passed = bool(h5["passed"])
    failed_cases = []
    if not h1:
        failed_cases.append("H1_multiplicity_failure")
    if not h2:
        failed_cases.append("H2_known_origin_invariance")
    if not h3:
        failed_cases.append("H3_fail_closed_uncertainty")
    if not h4_passed:
        failed_cases.append("H4_independent_evidence_responsiveness")
    if not h5_passed:
        failed_cases.append("H5_constrained_uncertainty_responsiveness")
    return {
        "overall_passed": h1 and h2 and h3 and h4_passed and h5_passed,
        "hypotheses": {
            "H1_multiplicity_failure": {
                "passed": h1,
                "by_family": h1_by_family,
            },
            "H2_known_origin_invariance": {
                "passed": h2,
                "by_family": h2_by_family,
            },
            "H3_fail_closed_uncertainty": {
                "passed": h3,
                "by_family": h3_by_family,
            },
            "H4_independent_evidence_responsiveness": h4,
            "H5_constrained_uncertainty_responsiveness": h5,
        },
        "corrupted_label_sensitivity": {
            "primary_success_criterion": False,
            "by_family": corrupted_by_family,
        },
        "scenario_count": len({str(row["scenario_id"]) for row in rows}),
        "result_row_count": len(rows),
        "failed_cases": failed_cases,
    }


def render_lineage_trap_v1_report(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    experiment_id: str | None = None,
    overall_passed: bool | None = None,
) -> str:
    """Generate a manuscript-facing LineageTrap report from machine-readable rows."""

    experiment_id = experiment_id or str(rows[0]["experiment_id"])
    certificate_version = ROBUST_LINEAGE_CERTIFICATE_VERSION
    passed = summary["overall_passed"] if overall_passed is None else overall_passed
    outcome = "PASS" if passed else "FAIL"
    lines = [
        f"# {experiment_id} — LineageTrap v1",
        "",
        f"**Frozen outcome: {outcome}.**",
        "",
        "This deterministic synthetic experiment isolates answer-sufficiency certification from "
        "retrieval and selection. It asks whether multiplying reports can create apparent "
        "independent support while the underlying scientific origins and information stay fixed.",
        "",
        "## Frozen hypothesis outcomes",
        "",
        "| hypothesis | outcome | diagnostic |",
        "| --- | --- | --- |",
    ]
    hypotheses = summary["hypotheses"]
    h1 = hypotheses["H1_multiplicity_failure"]
    h2 = hypotheses["H2_known_origin_invariance"]
    h3 = hypotheses["H3_fail_closed_uncertainty"]
    h4 = hypotheses["H4_independent_evidence_responsiveness"]
    h5 = hypotheses["H5_constrained_uncertainty_responsiveness"]
    lines.extend(
        [
            _hypothesis_row(
                "H1 — conventional multiplicity failure",
                bool(h1["passed"]),
                "gold report-level and unresolved hard-lineage failures in every primary family",
            ),
            _hypothesis_row(
                "H2 — known-origin invariance",
                bool(h2["passed"]),
                f"zero {certificate_version} decision-level violations under adjudicated "
                "same-origin relations",
            ),
            _hypothesis_row(
                "H3 — fail-closed unresolved origin",
                bool(h3["passed"]),
                "no unresolved same-origin family became robustly sufficient",
            ),
            _hypothesis_row(
                "H4 — responsiveness to independent evidence",
                bool(h4["passed"]),
                f"first robust certificate at replication factor {h4['first_sufficient_replication_factor']}",
            ),
            _hypothesis_row(
                "H5 — constrained-uncertainty responsiveness",
                bool(h5["passed"]),
                "robust certification with mixed must/cannot/may relations",
            ),
        ]
    )
    lines.extend(
        [
            "",
            "## Frozen design",
            "",
            f"- Replication factors: {', '.join(map(str, REPLICATION_FACTORS))}.",
            f"- Required independent origins: {REQUIRED_LINEAGES}.",
            f"- Required effective evidence: {REQUIRED_EFFECTIVE_EVIDENCE:.1f}.",
            f"- Two required facets, each with minimum items {MINIMUM_ITEMS_PER_FACET} and "
            f"flow threshold {FACET_THRESHOLD:.2f}.",
            f"- Fixed nonbinding token budget: {FIXED_TOKEN_BUDGET}; no selection was performed.",
            f"- Matrix: {summary['scenario_count']} scenarios and {summary['result_row_count']} "
            "method rows.",
            "",
            "The shared-cohort/dataset family replicates a fixed two-report complementary bundle; "
            "the other same-origin families replicate one fixed evidence bundle. The independent "
            "control adds scientific origins. A separate partially resolved control fixes four "
            "independent anchors while replicating an unresolved same-information report family. "
            "Its may-links have conditional distinct-origin dependence [0, 0], so uncertainty "
            "concerns origin assignment rather than residual overlap.",
            "",
            "## Conventional baseline failures under adjudicated gold origins",
            "",
            "| evidence family | gold false-accepting baselines | first gold factor | hard-lineage "
            "failure with unresolved origins |",
            "| --- | --- | --- | ---: |",
        ]
    )
    for family in PRIMARY_FAMILIES:
        result = h1["by_family"][family]
        methods = list(result["failing_baselines"])
        factors = result["first_false_acceptance_replication_factor"]
        lines.append(
            f"| {family} | {', '.join(methods) or 'none'} | "
            + ", ".join(f"{method}: {factors[method]}" for method in methods)
            + f" | {result['hard_lineage_unresolved_first_false_acceptance_factor']} |"
        )
    lines.extend(
        [
            "",
            f"## Full {certificate_version} certificate trajectories",
            "",
            "| family | origin condition | factor | reports | status | N_eff lower/upper | "
            "lineages lower/upper | pessimistic bottleneck | exact/fallback |",
            "| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | --- |",
        ]
    )
    robust_rows = [
        row for row in rows if row["method_id"] == ROBUST_LINEAGE_CERTIFICATE_VERSION
    ]
    for row in robust_rows:
        lines.append(
            f"| {row['evidence_family']} | {row['origin_condition']} | "
            f"{row['replication_factor']} | {row['raw_report_count']} | "
            f"{row['certificate_status']} | "
            f"{float(row['effective_evidence_lower']):.3f}/"
            f"{float(row['effective_evidence_upper']):.3f} | "
            f"{row['lineage_count_lower']}/{row['lineage_count_upper']} | "
            f"{float(row['pessimistic_flow_bottleneck']):.3f} | "
            f"{row['partition_bound_method']} |"
        )
    lines.extend(
        [
            "",
            "## Deliberately corrupted origin labels",
            "",
            "The corrupted-independent condition knowingly declares same-origin reports "
            "independent. It is a sensitivity analysis, not a primary success condition. A false "
            "acceptance here demonstrates the declared annotation dependency rather than a "
            "lineage-inference claim.",
            "",
            "| family | sensitivity observed | false-acceptance scenarios |",
            "| --- | ---: | --- |",
        ]
    )
    for family, result in summary["corrupted_label_sensitivity"]["by_family"].items():
        lines.append(
            f"| {family} | {int(bool(result['sensitivity_observed']))} | "
            f"{', '.join(result['false_acceptance_scenarios']) or 'none'} |"
        )
    exact_rows = [row for row in robust_rows if row["exact_search_completed"]]
    fallback_rows = [row for row in robust_rows if not row["exact_search_completed"]]
    lines.extend(
        [
            "",
            "## Search diagnostics",
            "",
            f"Exact bounded analysis completed for {len(exact_rows)}/{len(robust_rows)} "
            f"{certificate_version} rows; "
            f"{len(fallback_rows)} used the explicit safe relaxation. The largest completed "
            f"search visited {max((int(row['exact_search_states']) for row in exact_rows), default=0)} "
            "states.",
            "",
            "## Artifact links",
            "",
            "- [Manifest](lineage_trap_v1_manifest.json)",
            "- [Canonical inputs](lineage_trap_v1_inputs.json)",
            "- [JSON results](lineage_trap_v1_results.json)",
            "- [TSV results](lineage_trap_v1_results.tsv)",
            "",
            "## Interpretation boundary",
            "",
            f"Passing {experiment_id} demonstrates the controlled publication-multiplicity "
            "failure and the "
            "implemented fixed-set certificate behavior. It does not establish real-world "
            "prevalence, lineage-annotation accuracy, improved biomedical answers, causal truth, "
            "or cross-domain utility. Those claims require frozen expert-reviewed candidate pools "
            "and held-out end-to-end evaluation.",
            "",
        ]
    )
    if summary["failed_cases"]:
        lines.extend(
            [
                "## Failed frozen cases",
                "",
                *[f"- {case}" for case in summary["failed_cases"]],
                "",
            ]
        )
    return "\n".join(lines)


def canonical_json_bytes(payload: Any) -> bytes:
    """Serialize a payload deterministically for hashing and reproducibility tests."""

    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _build_scenario(
    family: str,
    condition: str,
    replication_factor: int,
    *,
    seed: int,
) -> LineageTrapV1Scenario:
    scenario_id = f"E028_{family}_{condition}_r{replication_factor}"
    blueprint = _blueprint(scenario_id)
    candidates, information_units = _family_candidates(
        family,
        replication_factor,
    )
    if family == INDEPENDENT_CONTROL:
        hidden_gold = {
            candidate.paper_id: f"GOLD_INDEPENDENT_{index:02d}"
            for index, candidate in enumerate(candidates, start=1)
        }
    elif family == PARTIAL_UNCERTAINTY_CONTROL:
        hidden_gold = {
            candidate.paper_id: (
                f"GOLD_ORIGIN_{candidate.paper_id.rsplit('_', 1)[-1]}"
                if "_ANCHOR_" in candidate.paper_id
                else "GOLD_ORIGIN_A"
            )
            for candidate in candidates
        }
    else:
        hidden_gold = {
            candidate.paper_id: "GOLD_SHARED_ORIGIN" for candidate in candidates
        }
    if family == PARTIAL_UNCERTAINTY_CONTROL:
        intervals = _partial_uncertainty_intervals(candidates)
    elif condition == "adjudicated_gold" and family in PRIMARY_FAMILIES:
        intervals = _pair_intervals(candidates, relation="must_link")
    elif condition in {"adjudicated_gold", "corrupted_independent"}:
        intervals = _pair_intervals(candidates, relation="cannot_link")
    else:
        intervals = ()
    expected_gold_sufficient = (
        family == INDEPENDENT_CONTROL and replication_factor >= REQUIRED_LINEAGES
    ) or family == PARTIAL_UNCERTAINTY_CONTROL
    return LineageTrapV1Scenario(
        scenario_id=scenario_id,
        evidence_family=family,
        origin_condition=condition,
        evaluation_role=(
            "partially_resolved_positive_control"
            if family == PARTIAL_UNCERTAINTY_CONTROL
            else "independent_positive_control"
            if family == INDEPENDENT_CONTROL
            else "primary_same_origin_trap"
        ),
        replication_factor=replication_factor,
        blueprint=blueprint,
        candidates=candidates,
        intervals=intervals,
        hidden_gold_origin_by_paper=hidden_gold,
        scientific_information_units_by_paper=information_units,
        expected_gold_sufficient=expected_gold_sufficient,
        seed=seed,
    )


def _blueprint(question_id: str) -> EvidenceBlueprint:
    facets = [
        EvidenceFacet(
            facet_id="mechanism",
            question_id=question_id,
            label="Molecular mechanism",
            description="Direct molecular or biochemical support for the mechanism.",
            facet_type="mechanism",
            biological_scale="molecular",
            evidence_type="biochemical",
            polarity_requirement="support",
            minimum_items=MINIMUM_ITEMS_PER_FACET,
        ),
        EvidenceFacet(
            facet_id="phenotype",
            question_id=question_id,
            label="Clinical phenotype",
            description="Support connecting the mechanism to the clinical phenotype.",
            facet_type="phenotype",
            biological_scale="clinical",
            evidence_type="observational",
            polarity_requirement="support",
            minimum_items=MINIMUM_ITEMS_PER_FACET,
        ),
    ]
    return EvidenceBlueprint(
        question_id=question_id,
        question=(
            "Is the fixed evidence set sufficient for an answer requiring mechanism and phenotype?"
        ),
        task_mode="evidence_synthesis",
        required_facets=facets,
        preferred_scales=["molecular", "clinical"],
        preferred_evidence_types=["biochemical", "observational"],
        required_polarities=["support"],
        minimum_independent_lineages=REQUIRED_LINEAGES,
        allowed_claim_strength_if_complete="association",
        review_status="frozen",
        reviewer="synthetic_ground_truth",
        notes="E028 controlled synthetic input; no empirical biomedical claim.",
    )


def _family_candidates(
    family: str,
    replication_factor: int,
) -> tuple[tuple[TraceCandidate, ...], dict[str, tuple[str, ...]]]:
    if family == PARTIAL_UNCERTAINTY_CONTROL:
        return _partial_uncertainty_candidates(replication_factor)
    candidates = []
    information: dict[str, tuple[str, ...]] = {}
    for index in range(replication_factor):
        if family == "exact_clone":
            candidate = _candidate(
                family,
                paper_id=f"E028_EXACT_{index + 1:02d}",
                text=(
                    "A controlled assay connected receptor engagement with the observed clinical "
                    "phenotype."
                ),
                facet_scores={"mechanism": 1.0, "phenotype": 1.0},
                scales={"molecular": 1.0, "clinical": 1.0},
                evidence_types={"biochemical": 1.0, "observational": 1.0},
            )
            candidates.append(candidate)
            information[candidate.paper_id] = (
                "FIXED_MECHANISM_SIGNAL",
                "FIXED_PHENOTYPE_SIGNAL",
            )
        elif family == "paraphrased_same_study":
            candidate = _candidate(
                family,
                paper_id=f"E028_PARAPHRASE_{index + 1:02d}",
                text=PARAPHRASES[index],
                facet_scores={"mechanism": 1.0, "phenotype": 1.0},
                scales={"molecular": 1.0, "clinical": 1.0},
                evidence_types={"biochemical": 1.0, "observational": 1.0},
            )
            candidates.append(candidate)
            information[candidate.paper_id] = (
                "FIXED_MECHANISM_SIGNAL",
                "FIXED_PHENOTYPE_SIGNAL",
            )
        elif family == "shared_cohort_dataset":
            mechanism = _candidate(
                family,
                paper_id=f"E028_SHARED_MECHANISM_{index + 1:02d}",
                text=SHARED_MECHANISM_TEXTS[index],
                facet_scores={"mechanism": 1.0},
                scales={"molecular": 1.0},
                evidence_types={"biochemical": 1.0},
            )
            phenotype = _candidate(
                family,
                paper_id=f"E028_SHARED_PHENOTYPE_{index + 1:02d}",
                text=SHARED_PHENOTYPE_TEXTS[index],
                facet_scores={"phenotype": 1.0},
                scales={"clinical": 1.0},
                evidence_types={"observational": 1.0},
            )
            candidates.extend([mechanism, phenotype])
            information[mechanism.paper_id] = ("FIXED_MECHANISM_SIGNAL",)
            information[phenotype.paper_id] = ("FIXED_PHENOTYPE_SIGNAL",)
        elif family == INDEPENDENT_CONTROL:
            candidate = _candidate(
                family,
                paper_id=f"E028_INDEPENDENT_{index + 1:02d}",
                text=(
                    f"Independent experiment {index + 1} supports the matched mechanism and "
                    "phenotype."
                ),
                facet_scores={"mechanism": 1.0, "phenotype": 1.0},
                scales={"molecular": 1.0, "clinical": 1.0},
                evidence_types={"biochemical": 1.0, "observational": 1.0},
            )
            candidates.append(candidate)
            information[candidate.paper_id] = (
                f"INDEPENDENT_MECHANISM_SIGNAL_{index + 1:02d}",
                f"INDEPENDENT_PHENOTYPE_SIGNAL_{index + 1:02d}",
            )
        else:
            raise ValueError(f"unknown E028 evidence family: {family}")
    return tuple(candidates), information


def _partial_uncertainty_candidates(
    replication_factor: int,
) -> tuple[tuple[TraceCandidate, ...], dict[str, tuple[str, ...]]]:
    candidates = [
        _candidate(
            PARTIAL_UNCERTAINTY_CONTROL,
            paper_id=f"E028_PARTIAL_ANCHOR_{label}",
            text=f"Adjudicated independent anchor experiment {label} supports both facets.",
            facet_scores={"mechanism": 1.0, "phenotype": 1.0},
            scales={"molecular": 1.0, "clinical": 1.0},
            evidence_types={"biochemical": 1.0, "observational": 1.0},
        )
        for label in ("A", "B", "C", "D")
    ]
    information = {
        candidate.paper_id: (
            f"{candidate.paper_id}_MECHANISM_SIGNAL",
            f"{candidate.paper_id}_PHENOTYPE_SIGNAL",
        )
        for candidate in candidates
    }
    for index in range(replication_factor):
        ambiguous = _candidate(
            PARTIAL_UNCERTAINTY_CONTROL,
            paper_id=f"E028_PARTIAL_AMBIGUOUS_{index + 1:02d}",
            text=(
                f"Unresolved report {index + 1} repeats the fixed anchor-A evidence bundle."
            ),
            facet_scores={"mechanism": 1.0, "phenotype": 1.0},
            scales={"molecular": 1.0, "clinical": 1.0},
            evidence_types={"biochemical": 1.0, "observational": 1.0},
        )
        candidates.append(ambiguous)
        information[ambiguous.paper_id] = (
            "E028_PARTIAL_ANCHOR_A_MECHANISM_SIGNAL",
            "E028_PARTIAL_ANCHOR_A_PHENOTYPE_SIGNAL",
        )
    return tuple(candidates), information


def _candidate(
    family: str,
    *,
    paper_id: str,
    text: str,
    facet_scores: dict[str, float],
    scales: dict[str, float],
    evidence_types: dict[str, float],
) -> TraceCandidate:
    chunk_id = f"{paper_id}_C1"
    return TraceCandidate(
        chunk_id=chunk_id,
        paper_id=paper_id,
        text=text,
        token_cost=100,
        retrieval_score=1.0,
        section="results",
        section_quality=1.0,
        facet_scores=facet_scores,
        scale_scores=scales,
        evidence_type_scores=evidence_types,
        polarity_scores={"support": 1.0},
        publication_family_id=f"FAMILY_{paper_id}",
        study_lineage_id=None,
        dataset_lineage_id=None,
        metadata={
            "synthetic": True,
            "experiment_id": EXPERIMENT_ID,
            "evidence_family": family,
        },
    )


def _pair_intervals(
    candidates: tuple[TraceCandidate, ...],
    *,
    relation: str,
) -> tuple[LineageDependenceInterval, ...]:
    dependence = 1.0 if relation == "must_link" else 0.0
    return tuple(
        LineageDependenceInterval(
            paper_id_a=left.paper_id,
            paper_id_b=right.paper_id,
            distinct_dependence_lower=dependence,
            distinct_dependence_upper=dependence,
            relation=relation,
            review_status="frozen",
            source=(
                "synthetic_adjudicated_same_origin"
                if relation == "must_link"
                else "synthetic_adjudicated_distinct_origin"
            ),
        )
        for left, right in combinations(candidates, 2)
    )


def _partial_uncertainty_intervals(
    candidates: tuple[TraceCandidate, ...],
) -> tuple[LineageDependenceInterval, ...]:
    edges = []
    for left, right in combinations(candidates, 2):
        left_anchor = "_ANCHOR_" in left.paper_id
        right_anchor = "_ANCHOR_" in right.paper_id
        if left_anchor and right_anchor:
            relation = "cannot_link"
            lower = upper = 0.0
            source = "synthetic_adjudicated_anchor_independence"
        elif not left_anchor and not right_anchor:
            relation = "must_link"
            lower = upper = 1.0
            source = "synthetic_adjudicated_ambiguous_report_family"
        else:
            relation = "may_link"
            lower = upper = 0.0
            source = "synthetic_partially_resolved_origin"
        edges.append(
            LineageDependenceInterval(
                paper_id_a=left.paper_id,
                paper_id_b=right.paper_id,
                distinct_dependence_lower=lower,
                distinct_dependence_upper=upper,
                relation=relation,
                review_status="frozen",
                source=source,
            )
        )
    return tuple(edges)


def _common_row(scenario: LineageTrapV1Scenario, method_id: str) -> dict[str, Any]:
    texts = {hashlib.sha256(candidate.text.encode("utf-8")).hexdigest() for candidate in scenario.candidates}
    information_units = {
        unit
        for units in scenario.scientific_information_units_by_paper.values()
        for unit in units
    }
    known_units = paper_independence_units(list(scenario.candidates))
    return {
        "experiment_id": EXPERIMENT_ID,
        "experiment_version": EXPERIMENT_VERSION,
        "scenario_schema_version": SCENARIO_SCHEMA_VERSION,
        "results_schema_version": RESULTS_SCHEMA_VERSION,
        "scenario_id": scenario.scenario_id,
        "evidence_family": scenario.evidence_family,
        "origin_condition": scenario.origin_condition,
        "evaluation_role": scenario.evaluation_role,
        "replication_factor": scenario.replication_factor,
        "method_id": method_id,
        "seed": scenario.seed,
        "input_hash": scenario.input_hash,
        "raw_report_count": len(scenario.candidates),
        "unique_exact_text_count": len(texts),
        "canonical_scientific_information_unit_count": len(information_units),
        "known_resource_unit_count": len(set(known_units.values())),
        "hidden_gold_origin_count": len(set(scenario.hidden_gold_origin_by_paper.values())),
        "declared_must_link_count": sum(
            edge.relation == "must_link" for edge in scenario.intervals
        ),
        "declared_cannot_link_count": sum(
            edge.relation == "cannot_link" for edge in scenario.intervals
        ),
        "declared_may_link_count": sum(
            edge.relation == "may_link" for edge in scenario.intervals
        ),
        "selection_performed": False,
        "fixed_token_budget": scenario.token_budget,
        "total_token_cost": sum(candidate.token_cost for candidate in scenario.candidates),
        "facet_threshold": scenario.facet_threshold,
        "minimum_items_per_facet": MINIMUM_ITEMS_PER_FACET,
        "required_lineages": REQUIRED_LINEAGES,
        "required_effective_evidence": REQUIRED_EFFECTIVE_EVIDENCE,
        "expected_gold_sufficient": scenario.expected_gold_sufficient,
        "evidence_accounting_version": EVIDENCE_ACCOUNTING_VERSION,
        "lineage_graph_schema_version": LINEAGE_GRAPH_SCHEMA_VERSION,
    }


def _evaluate_baseline(
    scenario: LineageTrapV1Scenario,
    method_id: str,
) -> dict[str, Any]:
    candidates = list(scenario.candidates)
    if method_id in {"raw_source_count", "facet_cover_no_lineage_uncertainty"}:
        unit_by_paper = {candidate.paper_id: f"REPORT:{candidate.paper_id}" for candidate in candidates}
    elif method_id == "exact_text_deduplication":
        unit_by_paper = {
            candidate.paper_id: "TEXT:" + hashlib.sha256(candidate.text.encode("utf-8")).hexdigest()
            for candidate in candidates
        }
    elif method_id == "one_per_known_lineage":
        unit_by_paper = _known_lineage_units(candidates, scenario.intervals)
    else:
        raise ValueError(f"unknown E028 baseline: {method_id}")
    flow = compute_evidence_flow(
        candidates,
        scenario.blueprint,
        facet_threshold=scenario.facet_threshold,
        unit_by_paper=unit_by_paper,
    )
    method_unit_count = len(set(unit_by_paper.values()))
    count_gate = method_id != "facet_cover_no_lineage_uncertainty"
    is_sufficient = not flow.unresolved_facets and (
        not count_gate or method_unit_count >= REQUIRED_LINEAGES
    )
    status = "sufficient" if is_sufficient else "insufficient"
    score_terms = [flow.bottleneck_coverage]
    if count_gate:
        score_terms.append(method_unit_count / REQUIRED_LINEAGES)
    row = {
        **_common_row(scenario, method_id),
        "method_version": "lineage_trap_baseline_v1",
        "baseline_definition": BASELINE_DEFINITIONS[method_id],
        "method_unit_count": method_unit_count,
        "certificate_method_version": None,
        "certificate_status": status,
        "is_sufficient": is_sufficient,
        "allowed_claim_strength": ALLOWED_CLAIM_STRENGTH[status],
        "decision_score": min(score_terms),
        "baseline_flow_normalized": flow.normalized_coverage,
        "baseline_flow_bottleneck": flow.bottleneck_coverage,
        "baseline_facet_flow": flow.facet_flow,
        "baseline_unresolved_facets": flow.unresolved_facets,
        "robust_certificate": None,
        **_empty_robust_fields(),
    }
    row["false_certificate_acceptance"] = bool(
        row["is_sufficient"] and not row["expected_gold_sufficient"]
    )
    row["false_certificate_rejection"] = bool(
        not row["is_sufficient"] and row["expected_gold_sufficient"]
    )
    return row


def _known_lineage_units(
    candidates: list[TraceCandidate],
    intervals: tuple[LineageDependenceInterval, ...],
) -> dict[str, str]:
    """Collapse hard resources and adjudicated same-origin edges, but ignore may-links."""

    paper_ids = sorted({candidate.paper_id for candidate in candidates})
    adjacency = {paper_id: set() for paper_id in paper_ids}
    hard_units = paper_independence_units(candidates)
    for left, right in combinations(paper_ids, 2):
        if hard_units[left] == hard_units[right]:
            adjacency[left].add(right)
            adjacency[right].add(left)
    for edge in intervals:
        if edge.relation != "must_link":
            continue
        adjacency[edge.paper_id_a].add(edge.paper_id_b)
        adjacency[edge.paper_id_b].add(edge.paper_id_a)
    unit_by_paper = {}
    unseen = set(paper_ids)
    while unseen:
        root = min(unseen)
        stack = [root]
        members = []
        while stack:
            paper_id = stack.pop()
            if paper_id not in unseen:
                continue
            unseen.remove(paper_id)
            members.append(paper_id)
            stack.extend(sorted(adjacency[paper_id], reverse=True))
        unit = f"KNOWN:{min(members)}"
        unit_by_paper.update({paper_id: unit for paper_id in members})
    return unit_by_paper


def _evaluate_robust_method(
    scenario: LineageTrapV1Scenario,
    result: RobustEvidenceFlowBounds,
) -> dict[str, Any]:
    exact = result.exact_partition_analysis
    is_sufficient = result.status == "robustly_sufficient"
    decision_score = min(
        result.effective_evidence.lower / max(REQUIRED_EFFECTIVE_EVIDENCE, 1e-12),
        result.lineage_count_lower / max(REQUIRED_LINEAGES, 1),
        result.pessimistic_flow.bottleneck_coverage,
    )
    row = {
        **_common_row(scenario, ROBUST_LINEAGE_CERTIFICATE_VERSION),
        "method_version": ROBUST_LINEAGE_CERTIFICATE_VERSION,
        "baseline_definition": None,
        "method_unit_count": result.lineage_count_lower,
        "certificate_method_version": result.certificate_method_version,
        "certificate_status": result.status,
        "is_sufficient": is_sufficient,
        "allowed_claim_strength": ALLOWED_CLAIM_STRENGTH[result.status],
        "decision_score": decision_score,
        "baseline_flow_normalized": None,
        "baseline_flow_bottleneck": None,
        "baseline_facet_flow": None,
        "baseline_unresolved_facets": None,
        "effective_evidence_lower": result.effective_evidence.lower,
        "effective_evidence_upper": result.effective_evidence.upper,
        "lineage_count_lower": result.lineage_count_lower,
        "relaxed_lineage_count_lower": result.relaxed_lineage_count_lower,
        "lineage_count_upper": result.lineage_count_upper,
        "pessimistic_flow_normalized": result.pessimistic_flow.normalized_coverage,
        "pessimistic_flow_bottleneck": result.pessimistic_flow.bottleneck_coverage,
        "pessimistic_facet_flow": result.pessimistic_flow.facet_flow,
        "pessimistic_unresolved_facets": result.pessimistic_flow.unresolved_facets,
        "relaxed_pessimistic_flow_normalized": (
            result.relaxed_pessimistic_flow.normalized_coverage
        ),
        "relaxed_pessimistic_flow_bottleneck": (
            result.relaxed_pessimistic_flow.bottleneck_coverage
        ),
        "optimistic_flow_normalized": result.optimistic_flow.normalized_coverage,
        "optimistic_flow_bottleneck": result.optimistic_flow.bottleneck_coverage,
        "optimistic_facet_flow": result.optimistic_flow.facet_flow,
        "optimistic_unresolved_facets": result.optimistic_flow.unresolved_facets,
        "total_pair_count": result.total_pair_count,
        "uncertain_pair_count": result.uncertain_pair_count,
        "missing_pair_count": result.missing_pair_count,
        "exact_for_declared_lineage_uncertainty": (
            result.exact_for_declared_lineage_uncertainty
        ),
        "partition_bound_method": result.partition_bound_method,
        "bound_method": result.bound_method,
        "certificate_gap": result.certificate_gap,
        "joint_optimistic_feasibility": result.joint_optimistic_feasibility,
        "joint_optimistic_partition": result.joint_optimistic_partition,
        "partition_relaxation_bottleneck_gap": (
            result.partition_relaxation_bottleneck_gap
        ),
        "exact_search_completed": exact.completed,
        "exact_search_reason": exact.reason,
        "exact_search_method": exact.method,
        "exact_component_count": exact.component_count,
        "exact_search_states": exact.search_states,
        "exact_leaf_partitions": exact.leaf_partitions,
        "exact_evaluated_maximal_partitions": exact.evaluated_maximal_partitions,
        "exact_evaluated_effective_partitions": exact.evaluated_effective_partitions,
        "exact_evaluated_joint_partitions": exact.evaluated_joint_partitions,
        "exact_max_components": exact.max_components,
        "exact_max_search_states": exact.max_search_states,
        "exact_worst_partition": exact.worst_partition,
        "exact_worst_effective_partition": exact.worst_effective_partition,
        "exact_best_effective_partition": exact.best_effective_partition,
        "robust_certificate": result.model_dump(mode="json"),
    }
    row["false_certificate_acceptance"] = bool(
        row["is_sufficient"] and not row["expected_gold_sufficient"]
    )
    row["false_certificate_rejection"] = bool(
        not row["is_sufficient"] and row["expected_gold_sufficient"]
    )
    return row


def _empty_robust_fields() -> dict[str, None]:
    return {
        "effective_evidence_lower": None,
        "effective_evidence_upper": None,
        "lineage_count_lower": None,
        "relaxed_lineage_count_lower": None,
        "lineage_count_upper": None,
        "pessimistic_flow_normalized": None,
        "pessimistic_flow_bottleneck": None,
        "pessimistic_facet_flow": None,
        "pessimistic_unresolved_facets": None,
        "relaxed_pessimistic_flow_normalized": None,
        "relaxed_pessimistic_flow_bottleneck": None,
        "optimistic_flow_normalized": None,
        "optimistic_flow_bottleneck": None,
        "optimistic_facet_flow": None,
        "optimistic_unresolved_facets": None,
        "total_pair_count": None,
        "uncertain_pair_count": None,
        "missing_pair_count": None,
        "exact_for_declared_lineage_uncertainty": None,
        "partition_bound_method": None,
        "bound_method": None,
        "certificate_gap": None,
        "joint_optimistic_feasibility": None,
        "joint_optimistic_partition": None,
        "partition_relaxation_bottleneck_gap": None,
        "exact_search_completed": None,
        "exact_search_reason": None,
        "exact_search_method": None,
        "exact_component_count": None,
        "exact_search_states": None,
        "exact_leaf_partitions": None,
        "exact_evaluated_maximal_partitions": None,
        "exact_evaluated_effective_partitions": None,
        "exact_evaluated_joint_partitions": None,
        "exact_max_components": None,
        "exact_max_search_states": None,
        "exact_worst_partition": None,
        "exact_worst_effective_partition": None,
        "exact_best_effective_partition": None,
    }


def _add_multiplicity_comparisons(rows: list[dict[str, Any]]) -> None:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[
            (
                str(row["evidence_family"]),
                str(row["origin_condition"]),
                str(row["method_id"]),
            )
        ].append(row)
    invariant_fields = (
        "effective_evidence_lower",
        "effective_evidence_upper",
        "lineage_count_lower",
        "lineage_count_upper",
        "pessimistic_flow_normalized",
        "pessimistic_flow_bottleneck",
        "optimistic_flow_normalized",
        "optimistic_flow_bottleneck",
    )
    for group_rows in grouped.values():
        group_rows.sort(key=lambda row: int(row["replication_factor"]))
        base = group_rows[0]
        first_sufficient = next(
            (
                int(row["replication_factor"])
                for row in group_rows
                if row["is_sufficient"]
            ),
            None,
        )
        for row in group_rows:
            row["decision_at_replication_factor_one"] = bool(base["is_sufficient"])
            row["score_at_replication_factor_one"] = float(base["decision_score"])
            row["score_gain_from_replication_factor_one"] = (
                float(row["decision_score"]) - float(base["decision_score"])
            )
            row["threshold_flip"] = bool(
                row["is_sufficient"] and not base["is_sufficient"]
            )
            row["first_sufficient_replication_factor"] = first_sufficient
            invariance_expected = (
                row["method_id"] == ROBUST_LINEAGE_CERTIFICATE_VERSION
                and row["evidence_family"] in PRIMARY_FAMILIES
                and row["origin_condition"] == "adjudicated_gold"
            )
            row["decision_invariance_expected"] = invariance_expected
            row["decision_invariance_violation"] = bool(
                invariance_expected and row["is_sufficient"] != base["is_sufficient"]
            )
            row["core_endpoint_invariance_violation"] = bool(
                invariance_expected
                and any(
                    not _values_equal(row[field], base[field])
                    for field in invariant_fields
                )
            )
            row["positive_control_strengthening"] = bool(
                row["method_id"] == ROBUST_LINEAGE_CERTIFICATE_VERSION
                and row["evidence_family"] == INDEPENDENT_CONTROL
                and row["is_sufficient"]
                and not base["is_sufficient"]
            )


def _method_rows(
    rows: list[dict[str, Any]],
    *,
    family: str,
    condition: str,
    method: str,
) -> list[dict[str, Any]]:
    return sorted(
        [
            row
            for row in rows
            if row["evidence_family"] == family
            and row["origin_condition"] == condition
            and row["method_id"] == method
        ],
        key=lambda row: int(row["replication_factor"]),
    )


def _values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and not isinstance(left, bool):
        if not isinstance(right, (int, float)) or isinstance(right, bool):
            return False
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-9)
    return left == right


def _hypothesis_row(label: str, passed: bool, diagnostic: str) -> str:
    return f"| {label} | {'PASS' if passed else 'FAIL'} | {diagnostic} |"
