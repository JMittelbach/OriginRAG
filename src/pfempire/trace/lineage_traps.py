from __future__ import annotations

import itertools
from dataclasses import dataclass, replace

from pfempire.trace.evidence_weights import effective_independent_evidence
from pfempire.trace.flow import compute_evidence_flow
from pfempire.trace.schemas import EvidenceBlueprint, EvidenceFacet, TraceCandidate


@dataclass(frozen=True)
class LineageTrapScenario:
    """Controlled candidate pool in which publication count can overstate evidence."""

    scenario_id: str
    trap_type: str
    description: str
    blueprint: EvidenceBlueprint
    candidates: tuple[TraceCandidate, ...]
    dependence: dict[tuple[str, str], float]
    token_budget: int
    flow_anchor_papers: int
    flow_minimum_papers: int
    expected_minimum_lineages: int
    expected_minimum_effective_evidence: float | None = None
    required_selected_polarities: tuple[str, ...] = ()
    evaluation_role: str = "primary_trap"
    ground_truth_lineages: dict[str, str] | None = None


def build_lineage_trap_scenarios() -> list[LineageTrapScenario]:
    """Return deterministic fixtures for falsifying lineage-aware selection claims."""

    return [
        _shared_cohort_relevance_trap(),
        _dataset_capacity_trap(),
        _counterevidence_trap(),
        _partial_overlap_trap(),
        _no_dependence_control(),
        _missing_lineage_labels(),
        _false_positive_lineage_labels(),
    ]


def build_annotation_perturbation_scenarios() -> list[LineageTrapScenario]:
    """Vary lineage-label precision and recall while holding candidate evidence fixed."""

    base = _shared_cohort_relevance_trap()
    gold = {
        candidate.paper_id: candidate.study_lineage_id or candidate.paper_id
        for candidate in base.candidates
    }
    shared = "L_SHARED"
    conditions = [
        (
            "EXACT",
            "Exact lineage labels.",
            {
                "P_A1": shared,
                "P_A2": shared,
                "P_A3": shared,
                "P_B1": "L_INDEPENDENT_1",
                "P_C1": "L_INDEPENDENT_2",
            },
        ),
        (
            "RECALL_033",
            "One of three shared-cohort papers is split into a false independent lineage.",
            {
                "P_A1": shared,
                "P_A2": shared,
                "P_A3": "L_MISSED_A3",
                "P_B1": "L_INDEPENDENT_1",
                "P_C1": "L_INDEPENDENT_2",
            },
        ),
        (
            "RECALL_000",
            "Every shared-cohort relationship is missed.",
            {candidate.paper_id: f"L_UNIQUE_{candidate.paper_id}" for candidate in base.candidates},
        ),
        (
            "PRECISION_050",
            "One independent paper is falsely merged into the shared cohort.",
            {
                "P_A1": shared,
                "P_A2": shared,
                "P_A3": shared,
                "P_B1": shared,
                "P_C1": "L_INDEPENDENT_2",
            },
        ),
        (
            "PRECISION_030",
            "Both independent papers are falsely merged into the shared cohort.",
            {candidate.paper_id: shared for candidate in base.candidates},
        ),
    ]
    scenarios = []
    for condition, description, observed in conditions:
        scenario_id = f"LT_ANNOTATION_{condition}"
        candidates = tuple(
            candidate.model_copy(
                update={"study_lineage_id": observed[candidate.paper_id]}
            )
            for candidate in base.candidates
        )
        scenarios.append(
            replace(
                base,
                scenario_id=scenario_id,
                trap_type=f"annotation_{condition.lower()}",
                description=description,
                blueprint=base.blueprint.model_copy(
                    update={
                        "question_id": scenario_id,
                        "required_facets": [
                            facet.model_copy(update={"question_id": scenario_id})
                            for facet in base.blueprint.required_facets
                        ],
                    }
                ),
                candidates=candidates,
                dependence=_dependence(candidates, shared_strength=1.0),
                evaluation_role="annotation_sweep",
                ground_truth_lineages=gold,
            )
        )
    return scenarios


def trap_avoided(
    scenario: LineageTrapScenario,
    selected: list[TraceCandidate],
) -> bool:
    gold_selected = [
        candidate.model_copy(
            update={"study_lineage_id": _ground_truth_lineage(scenario, candidate)}
        )
        for candidate in selected
    ]
    lineages = {candidate.study_lineage_id for candidate in gold_selected}
    flow = compute_evidence_flow(gold_selected, scenario.blueprint)
    gold_dependence = scenario.dependence
    if scenario.ground_truth_lineages is not None:
        gold_dependence = {
            (left.paper_id, right.paper_id): 1.0
            for left, right in itertools.permutations(gold_selected, 2)
            if left.study_lineage_id == right.study_lineage_id
        }
    n_eff = float(
        effective_independent_evidence(gold_selected, gold_dependence)["n_eff"]
    )
    minimum_n_eff = (
        scenario.expected_minimum_effective_evidence
        if scenario.expected_minimum_effective_evidence is not None
        else 0.90 * scenario.expected_minimum_lineages
    )
    polarities = {
        polarity
        for candidate in gold_selected
        if max(candidate.facet_scores.values(), default=0.0) >= 0.50
        for polarity, score in candidate.polarity_scores.items()
        if score >= 0.50
    }
    return (
        sum(candidate.token_cost for candidate in gold_selected)
        <= scenario.token_budget
        and not flow.unresolved_facets
        and len(lineages) >= scenario.expected_minimum_lineages
        and n_eff + 1e-9 >= minimum_n_eff
        and all(
            polarity in polarities
            for polarity in scenario.required_selected_polarities
        )
    )


def lineage_annotation_quality(scenario: LineageTrapScenario) -> dict[str, float]:
    """Pairwise precision and recall of observed lineage grouping against synthetic gold."""

    true_positive = 0
    false_positive = 0
    false_negative = 0
    for left, right in itertools.combinations(scenario.candidates, 2):
        observed_same = (left.study_lineage_id or left.paper_id) == (
            right.study_lineage_id or right.paper_id
        )
        gold_same = _ground_truth_lineage(scenario, left) == _ground_truth_lineage(
            scenario, right
        )
        if observed_same and gold_same:
            true_positive += 1
        elif observed_same:
            false_positive += 1
        elif gold_same:
            false_negative += 1
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else 1.0
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else 1.0
    )
    return {
        "lineage_annotation_precision": precision,
        "lineage_annotation_recall": recall,
    }


def selected_ground_truth_lineages(
    scenario: LineageTrapScenario,
    selected: list[TraceCandidate],
) -> list[str]:
    return [_ground_truth_lineage(scenario, candidate) for candidate in selected]


def gold_independence_metrics(
    scenario: LineageTrapScenario,
    selected: list[TraceCandidate],
) -> dict[str, float | int]:
    """Recompute independence using hidden synthetic truth instead of observed labels."""

    gold_candidates = [
        candidate.model_copy(
            update={"study_lineage_id": _ground_truth_lineage(scenario, candidate)}
        )
        for candidate in selected
    ]
    dependence = scenario.dependence
    if scenario.ground_truth_lineages is not None:
        dependence = {}
        for left, right in itertools.permutations(gold_candidates, 2):
            if _ground_truth_lineage(scenario, left) == _ground_truth_lineage(
                scenario, right
            ):
                dependence[(left.paper_id, right.paper_id)] = 1.0
    metrics = effective_independent_evidence(gold_candidates, dependence)
    raw_count = int(metrics["raw_paper_count"])
    n_eff = float(metrics["n_eff"])
    return {
        "gold_effective_independent_evidence": n_eff,
        "gold_evidence_lineage_count": len(
            {_ground_truth_lineage(scenario, candidate) for candidate in selected}
        ),
        "gold_independent_evidence_ratio": n_eff / raw_count if raw_count else 0.0,
        "gold_evidence_inflation_factor": (
            raw_count / max(n_eff, 1e-12) if raw_count else 0.0
        ),
    }


def _shared_cohort_relevance_trap() -> LineageTrapScenario:
    blueprint = _blueprint("LT_SHARED_COHORT", minimum_lineages=3)
    candidates = (
        _candidate("P_A1", "L_SHARED", 100, "paediatric vascular measurements", both_facets()),
        _candidate("P_A2", "L_SHARED", 98, "retrospective biomarker outcome model", both_facets()),
        _candidate("P_A3", "L_SHARED", 96, "secondary transcript profile analysis", both_facets()),
        _candidate("P_B1", "L_INDEPENDENT_1", 70, "independent biochemical replication", both_facets()),
        _candidate("P_C1", "L_INDEPENDENT_2", 68, "multicentre clinical validation", both_facets()),
    )
    return LineageTrapScenario(
        scenario_id=blueprint.question_id,
        trap_type="shared_cohort_relevance",
        description="Three highly ranked papers reuse one cohort; two lower-ranked papers are independent.",
        blueprint=blueprint,
        candidates=candidates,
        dependence=_dependence(candidates, shared_strength=1.0),
        token_budget=300,
        flow_anchor_papers=3,
        flow_minimum_papers=3,
        expected_minimum_lineages=3,
    )


def _dataset_capacity_trap() -> LineageTrapScenario:
    blueprint = _blueprint("LT_DATASET_CAPACITY", minimum_lineages=1)
    candidates = (
        _candidate("P_D1", "L_DATASET", 100, "molecular assay from shared dataset", {"mechanism": 1.0}),
        _candidate(
            "P_D2",
            "L_DATASET",
            99,
            "clinical analysis from shared dataset",
            {"phenotype": 1.0},
            scales={"clinical": 1.0},
            evidence_types={"observational": 1.0},
        ),
        _candidate("P_E1", "L_INDEPENDENT_1", 70, "external receptor experiment", {"mechanism": 1.0}),
        _candidate(
            "P_F1",
            "L_INDEPENDENT_2",
            69,
            "external patient cohort",
            {"phenotype": 1.0},
            scales={"clinical": 1.0},
            evidence_types={"observational": 1.0},
        ),
    )
    return LineageTrapScenario(
        scenario_id=blueprint.question_id,
        trap_type="shared_dataset_capacity",
        description="Two papers cover complementary facets but derive from one shared dataset.",
        blueprint=blueprint,
        candidates=candidates,
        dependence=_dependence(candidates, shared_strength=1.0),
        token_budget=200,
        flow_anchor_papers=0,
        flow_minimum_papers=2,
        expected_minimum_lineages=2,
    )


def _counterevidence_trap() -> LineageTrapScenario:
    blueprint = _blueprint(
        "LT_COUNTEREVIDENCE",
        minimum_lineages=2,
        required_polarities=["support", "contradiction"],
    )
    candidates = (
        _candidate("P_G1", "L_SUPPORT_SHARED", 100, "supporting hospital analysis", both_facets()),
        _candidate("P_G2", "L_SUPPORT_SHARED", 99, "supporting laboratory follow-up", both_facets()),
        _candidate("P_H1", "L_SUPPORT_EXTERNAL", 80, "independent supporting replication", both_facets()),
        _candidate(
            "P_I1",
            "L_CONTRADICTION",
            30,
            "independent contradictory result",
            both_facets(),
            polarity="contradiction",
        ),
    )
    return LineageTrapScenario(
        scenario_id=blueprint.question_id,
        trap_type="counterevidence_suppression",
        description="Highly ranked supporting papers can crowd out lower-ranked independent counterevidence.",
        blueprint=blueprint,
        candidates=candidates,
        dependence=_dependence(candidates, shared_strength=1.0),
        token_budget=300,
        flow_anchor_papers=2,
        flow_minimum_papers=3,
        expected_minimum_lineages=3,
        required_selected_polarities=("contradiction",),
    )


def _partial_overlap_trap() -> LineageTrapScenario:
    blueprint = _blueprint("LT_PARTIAL_OVERLAP", minimum_lineages=3)
    candidates = (
        _candidate("P_J1", "L_OVERLAP_1", 100, "site analysis with overlapping participants", both_facets()),
        _candidate("P_J2", "L_OVERLAP_2", 97, "follow-up analysis with reused participants", both_facets()),
        _candidate("P_J3", "L_OVERLAP_3", 95, "secondary endpoint from overlapping cohort", both_facets()),
        _candidate("P_K1", "L_INDEPENDENT_1", 75, "independent regional replication", both_facets()),
        _candidate("P_L1", "L_INDEPENDENT_2", 72, "independent prospective replication", both_facets()),
    )
    return LineageTrapScenario(
        scenario_id=blueprint.question_id,
        trap_type="partial_sample_overlap",
        description="Three publications have strong but incomplete sample dependence.",
        blueprint=blueprint,
        candidates=candidates,
        dependence={
            (left.paper_id, right.paper_id): 0.75
            for left, right in itertools.permutations(candidates[:3], 2)
        },
        token_budget=300,
        flow_anchor_papers=3,
        flow_minimum_papers=3,
        expected_minimum_lineages=3,
        expected_minimum_effective_evidence=2.5,
    )


def _no_dependence_control() -> LineageTrapScenario:
    blueprint = _blueprint("LT_NO_DEPENDENCE", minimum_lineages=3)
    candidates = (
        _candidate("P_N1", "L_N1", 100, "highest ranked independent experiment", both_facets()),
        _candidate("P_N2", "L_N2", 98, "second ranked independent cohort", both_facets()),
        _candidate("P_N3", "L_N3", 96, "third ranked independent replication", both_facets()),
        _candidate("P_N4", "L_N4", 70, "lower ranked independent evidence", both_facets()),
        _candidate("P_N5", "L_N5", 68, "additional independent evidence", both_facets()),
    )
    return LineageTrapScenario(
        scenario_id=blueprint.question_id,
        trap_type="no_dependence_control",
        description="The highest-ranked papers are genuinely independent and should be preserved.",
        blueprint=blueprint,
        candidates=candidates,
        dependence={},
        token_budget=300,
        flow_anchor_papers=3,
        flow_minimum_papers=3,
        expected_minimum_lineages=3,
        evaluation_role="negative_control",
    )


def _missing_lineage_labels() -> LineageTrapScenario:
    blueprint = _blueprint("LT_MISSING_LINEAGE_LABELS", minimum_lineages=3)
    candidates = (
        _candidate("P_M1", "L_OBSERVED_1", 100, "first hidden shared-cohort paper", both_facets()),
        _candidate("P_M2", "L_OBSERVED_2", 98, "second hidden shared-cohort paper", both_facets()),
        _candidate("P_M3", "L_OBSERVED_3", 96, "third hidden shared-cohort paper", both_facets()),
        _candidate("P_M4", "L_OBSERVED_4", 70, "independent external study", both_facets()),
        _candidate("P_M5", "L_OBSERVED_5", 68, "second independent external study", both_facets()),
    )
    return LineageTrapScenario(
        scenario_id=blueprint.question_id,
        trap_type="missing_lineage_annotations",
        description="Three dependent papers look independent because their shared cohort is unlabeled.",
        blueprint=blueprint,
        candidates=candidates,
        dependence={},
        token_budget=300,
        flow_anchor_papers=3,
        flow_minimum_papers=3,
        expected_minimum_lineages=3,
        evaluation_role="annotation_sensitivity",
        ground_truth_lineages={
            "P_M1": "L_GOLD_SHARED",
            "P_M2": "L_GOLD_SHARED",
            "P_M3": "L_GOLD_SHARED",
            "P_M4": "L_GOLD_INDEPENDENT_1",
            "P_M5": "L_GOLD_INDEPENDENT_2",
        },
    )


def _false_positive_lineage_labels() -> LineageTrapScenario:
    blueprint = _blueprint("LT_FALSE_POSITIVE_LINEAGE", minimum_lineages=3)
    candidates = (
        _candidate("P_Q1", "L_FALSE_SHARED", 100, "first truly independent top paper", both_facets()),
        _candidate("P_Q2", "L_FALSE_SHARED", 98, "second truly independent top paper", both_facets()),
        _candidate("P_Q3", "L_FALSE_SHARED", 96, "third truly independent top paper", both_facets()),
        _candidate("P_Q4", "L_OBSERVED_4", 70, "lower ranked independent paper", both_facets()),
        _candidate("P_Q5", "L_OBSERVED_5", 68, "additional lower ranked paper", both_facets()),
    )
    dependence = _dependence(candidates, shared_strength=1.0)
    return LineageTrapScenario(
        scenario_id=blueprint.question_id,
        trap_type="false_positive_lineage_annotations",
        description="Three independent top papers are incorrectly labeled as one study lineage.",
        blueprint=blueprint,
        candidates=candidates,
        dependence=dependence,
        token_budget=300,
        flow_anchor_papers=3,
        flow_minimum_papers=3,
        expected_minimum_lineages=3,
        evaluation_role="annotation_sensitivity",
        ground_truth_lineages={candidate.paper_id: f"L_GOLD_{candidate.paper_id}" for candidate in candidates},
    )


def _blueprint(
    question_id: str,
    *,
    minimum_lineages: int,
    required_polarities: list[str] | None = None,
) -> EvidenceBlueprint:
    facets = [
        EvidenceFacet(
            facet_id="mechanism",
            question_id=question_id,
            label="Molecular mechanism",
            description="Direct molecular or biochemical mechanism evidence.",
            facet_type="mechanism",
            biological_scale="molecular",
            evidence_type="biochemical",
            polarity_requirement="any",
            is_required=True,
        ),
        EvidenceFacet(
            facet_id="phenotype",
            question_id=question_id,
            label="Clinical phenotype",
            description="Evidence connecting the mechanism to a clinical phenotype.",
            facet_type="phenotype",
            biological_scale="clinical",
            evidence_type="observational",
            polarity_requirement="any",
            is_required=True,
        ),
    ]
    return EvidenceBlueprint(
        question_id=question_id,
        question="Does the selected evidence independently support mechanism and phenotype?",
        task_mode="mechanism_mapping",
        required_facets=facets,
        preferred_scales=["molecular", "clinical"],
        preferred_evidence_types=["biochemical", "observational"],
        required_polarities=required_polarities or ["support"],
        minimum_independent_lineages=minimum_lineages,
        allowed_claim_strength_if_complete="association",
        review_status="frozen",
        reviewer="synthetic_ground_truth",
        notes="Controlled lineage-trap fixture; contains no empirical scientific claim.",
    )


def _candidate(
    paper_id: str,
    lineage_id: str,
    retrieval_score: float,
    text: str,
    facet_scores: dict[str, float],
    *,
    scales: dict[str, float] | None = None,
    evidence_types: dict[str, float] | None = None,
    polarity: str = "support",
) -> TraceCandidate:
    chunk_id = f"{paper_id}_C1"
    raw_result = {
        "chunk_id": chunk_id,
        "paper_id": paper_id,
        "text": text,
        "score": retrieval_score,
        "section": "results",
        "quality_flags": [],
    }
    return TraceCandidate(
        chunk_id=chunk_id,
        paper_id=paper_id,
        text=text,
        token_cost=100,
        retrieval_score=retrieval_score,
        section="results",
        section_quality=1.0,
        facet_scores=facet_scores,
        scale_scores=scales or {"molecular": 1.0, "clinical": 1.0},
        evidence_type_scores=evidence_types or {"biochemical": 1.0, "observational": 1.0},
        polarity_scores={polarity: 1.0},
        publication_family_id=f"FAMILY_{paper_id}",
        study_lineage_id=lineage_id,
        metadata={"raw_result": raw_result},
    )


def both_facets() -> dict[str, float]:
    return {"mechanism": 1.0, "phenotype": 1.0}


def _dependence(
    candidates: tuple[TraceCandidate, ...],
    *,
    shared_strength: float,
) -> dict[tuple[str, str], float]:
    lookup: dict[tuple[str, str], float] = {}
    for left in candidates:
        for right in candidates:
            if left.paper_id == right.paper_id:
                continue
            if left.study_lineage_id == right.study_lineage_id:
                lookup[(left.paper_id, right.paper_id)] = shared_strength
    return lookup


def _ground_truth_lineage(
    scenario: LineageTrapScenario,
    candidate: TraceCandidate,
) -> str:
    if scenario.ground_truth_lineages:
        return scenario.ground_truth_lineages.get(candidate.paper_id, candidate.paper_id)
    return candidate.study_lineage_id or candidate.paper_id
