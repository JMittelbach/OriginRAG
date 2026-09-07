from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pfempire.trace.baselines import run_selector
from pfempire.trace.blueprint import safe_question_id, suggest_blueprint
from pfempire.trace.certificate import build_certificate, format_certificate_markdown
from pfempire.trace.counterfactual import paper_counterfactual_contributions, paper_flow_contributions
from pfempire.trace.dependence import dependence_lookup
from pfempire.trace.facets import (
    infer_direct_evidence_roles,
    infer_evidence_type_scores,
    infer_polarity_scores,
    infer_scale_scores,
    score_candidate_facets,
)
from pfempire.trace.io import read_json, read_yaml, write_json, write_tsv
from pfempire.trace.independence_units import paper_independence_units
from pfempire.trace.lineage import assign_lineage_ids
from pfempire.trace.method_versions import (
    CERTIFYING_EVIDENCE_STRATEGY_VERSION,
    EVIDENCE_ACCOUNTING_VERSION,
    LINEAGE_GRAPH_SCHEMA_VERSION,
    ROBUST_LINEAGE_CERTIFICATE_VERSION,
)
from pfempire.trace.robust_lineage import (
    LineageDependenceInterval,
    LineageReviewPriority,
    RobustEvidenceFlowBounds,
    complete_lineage_intervals,
    rank_lineage_reviews,
    robust_evidence_flow,
)
from pfempire.trace.schemas import EvidenceBlueprint, EvidenceLineageEdge, TraceCandidate
from pfempire.trace.selector import SelectorConfig
from pfempire.utils.normalization import finite_float, is_finite_number, normalize_string_list


SELECTOR_ALIASES = {
    "topk": "topk_relevance",
    "trace": "trace_full",
    "current": "current_pfempire",
    "mmr": "mmr",
    "trace_no_lineage": "trace_no_lineage",
    "trace_flow": "trace_flow",
    "trace_flow_no_anchor": "trace_flow_no_anchor",
    "trace_flow_unbounded": "trace_flow_unbounded",
    "trace_flow_relevance_reserve": "trace_flow_relevance_reserve",
    "trace_flow_diverse_reserve": "trace_flow_diverse_reserve",
}

def run_trace_selection(
    question: str,
    task_mode: str,
    results: list[dict],
    output_dir: Path,
    *,
    selector: str,
    config_path: Path,
    blueprint_path: Path | None = None,
    lineage_graph_path: Path | None = None,
    token_budget: int | None = None,
    selector_algorithm: str = "lazy_greedy",
    question_id: str | None = None,
) -> dict:
    generated_id = safe_question_id(hashlib.sha256(question.encode("utf-8")).hexdigest()[:12])
    requested_id = safe_question_id(question_id) if question_id else None
    if blueprint_path:
        blueprint = load_blueprint(blueprint_path)
        if requested_id and requested_id != blueprint.question_id:
            raise ValueError(
                f"question_id {requested_id!r} does not match blueprint "
                f"question_id {blueprint.question_id!r}"
            )
        if _canonical_question(blueprint.question) != _canonical_question(question):
            raise ValueError("loaded blueprint question does not match the requested question")
        if blueprint.task_mode != task_mode:
            raise ValueError(
                f"task_mode {task_mode!r} does not match blueprint task_mode "
                f"{blueprint.task_mode!r}"
            )
        question_id = blueprint.question_id
    else:
        question_id = requested_id or generated_id
        blueprint = suggest_blueprint(question_id, question, task_mode)
    config_data = read_yaml(config_path).get("trace_rag", {})
    selector_config = SelectorConfig(
        token_budget=int(
            token_budget if token_budget is not None else config_data.get("token_budget", 8000)
        ),
        max_chunks_per_paper=int(config_data.get("max_chunks_per_paper", 2)),
        max_publication_versions_per_family=int(config_data.get("max_publication_versions_per_family", 1)),
        minimum_marginal_gain=float(config_data.get("minimum_marginal_gain", 0.002)),
        minimum_selected_papers=int(config_data.get("minimum_selected_papers", 0)),
        selector_algorithm=selector_algorithm,
        facet_coverage_threshold=float(config_data.get("facet_coverage_threshold", 0.60)),
        flow_lineage_capacity=float(config_data.get("flow_lineage_capacity", 1.0)),
        flow_anchor_papers=int(config_data.get("flow_anchor_papers", 3)),
        flow_minimum_papers=int(config_data.get("flow_minimum_papers", 14)),
        flow_reserve_relevance_fraction=float(config_data.get("flow_reserve_relevance_fraction", 0.80)),
    )
    paper_ids = sorted(
        {
            str(row.get("paper_id") or "").strip()
            for row in results
            if isinstance(row, dict) and str(row.get("paper_id") or "").strip()
        }
    )
    lineage_graph_metadata = load_lineage_graph_metadata(lineage_graph_path)
    edges, lineage_graph_status = load_edges_with_status(lineage_graph_path, paper_ids)
    trusted_edges = certificate_trusted_edges(
        edges,
        artifact_status=str(lineage_graph_metadata["artifact_status"] or "unknown"),
    )
    dependence = dependence_lookup(edges)
    lineage_ids = assign_lineage_ids(paper_ids, trusted_edges)
    candidates, rejected_candidates = prepare_candidates(results, blueprint, lineage_ids)
    selector_name = SELECTOR_ALIASES.get(selector, selector)
    selection = run_selector(selector_name, candidates, blueprint, dependence, selector_config)
    certificate = build_certificate(
        question_id,
        selection.selected_candidates,
        blueprint,
        dependence,
        flow_details=selection.run_config.get("flow"),
        minimum_required_papers=(
            selector_config.flow_minimum_papers
            if selector_name.startswith("trace_flow")
            else selector_config.minimum_selected_papers
        ),
        selection_stopping_reason=selection.run_config.get("stopping_reason"),
        lineage_graph_status=lineage_graph_status,
    )
    contributions = (
        paper_flow_contributions(
            selection.selected_candidates,
            blueprint,
            lineage_capacity=selector_config.flow_lineage_capacity,
            facet_threshold=selector_config.facet_coverage_threshold,
            minimum_papers=selector_config.flow_minimum_papers,
            decision_trace=selection.run_config.get("decision_trace") or [],
        )
        if selector_name.startswith("trace_flow")
        else paper_counterfactual_contributions(selection.selected_candidates, blueprint, dependence)
    )
    lineage_intervals = lineage_intervals_from_edges(
        edges,
        selection.selected_candidates,
        graph_artifact_status=str(
            lineage_graph_metadata["artifact_status"] or "unknown"
        ),
    )
    exact_partition_max_components = int(
        config_data.get("robust_exact_partition_max_components", 9)
    )
    certifying_evidence = list(selection.selected_candidates)
    certificate_lineage_intervals, _ = complete_lineage_intervals(
        certifying_evidence,
        lineage_intervals,
        strict_missing_pairs=True,
    )
    exact_partition_max_search_states = int(
        config_data.get("robust_exact_partition_max_search_states", 100_000)
    )
    review_simulation_budget = int(
        config_data.get("robust_lineage_review_simulation_budget", 8)
    )
    robust_lineage = robust_evidence_flow(
        certifying_evidence,
        blueprint,
        lineage_intervals,
        lineage_capacity=selector_config.flow_lineage_capacity,
        facet_threshold=selector_config.facet_coverage_threshold,
        strict_missing_pairs=True,
        exact_partition_max_components=exact_partition_max_components,
        exact_partition_max_search_states=exact_partition_max_search_states,
    )
    lineage_review_priorities = rank_lineage_reviews(
        certifying_evidence,
        blueprint,
        lineage_intervals,
        lineage_capacity=selector_config.flow_lineage_capacity,
        facet_threshold=selector_config.facet_coverage_threshold,
        strict_missing_pairs=True,
        simulation_budget=review_simulation_budget,
        exact_partition_max_components=exact_partition_max_components,
        exact_partition_max_search_states=exact_partition_max_search_states,
    )
    lineage_review_screening = lineage_review_screening_summary(
        robust_lineage,
        certificate_lineage_intervals,
        lineage_review_priorities,
        simulation_budget=review_simulation_budget,
    )
    selection.run_config["robust_lineage"] = robust_lineage_run_summary(
        robust_lineage,
        review_count=len(lineage_review_priorities),
        review_screening=lineage_review_screening,
    )
    certifying_evidence_summary = {
        "strategy": CERTIFYING_EVIDENCE_STRATEGY_VERSION,
        "context_paper_count": len(
            {candidate.paper_id for candidate in selection.selected_candidates}
        ),
        "certifying_paper_count": len(
            {candidate.paper_id for candidate in certifying_evidence}
        ),

        "certification_paper_count": len(
            {candidate.paper_id for candidate in certifying_evidence}
        ),
        "maximum_certification_papers": None,
        "paper_ids": list(
            dict.fromkeys(candidate.paper_id for candidate in certifying_evidence)
        ),
        "claim_boundary": (
            "The robust certificate evaluates every report in the deterministic "
            "selected evidence set. If exact analysis does not complete, the same "
            "full set is evaluated with the declared conservative fallback; the "
            "runtime never truncates or switches evidence sets."
        ),
    }
    selection.run_config["certifying_evidence"] = certifying_evidence_summary
    selection.run_config["certification_core"] = {
        **certifying_evidence_summary,
        "compatibility_alias": True,
        "deprecated_name": "certification_core",
    }
    selection.run_config["evidence_accounting_version"] = EVIDENCE_ACCOUNTING_VERSION
    selection.run_config["robust_lineage_certificate_version"] = (
        ROBUST_LINEAGE_CERTIFICATE_VERSION
    )
    selection.run_config["lineage_graph_runtime_schema_version"] = (
        LINEAGE_GRAPH_SCHEMA_VERSION
    )
    selection.run_config["lineage_graph_input_schema_version"] = (
        lineage_graph_metadata["schema_version"]
    )
    selection.run_config["lineage_graph_input_status"] = (
        lineage_graph_metadata["artifact_status"]
    )
    selected_results = [candidate.metadata["raw_result"] for candidate in selection.selected_candidates]
    write_trace_artifacts(
        output_dir,
        blueprint,
        edges,
        candidates,
        selection,
        certificate,
        contributions,
        rejected_candidates,
        robust_lineage,
        lineage_intervals,
        certificate_lineage_intervals,
        lineage_review_priorities,
        lineage_review_screening,
        certifying_evidence,
    )
    return {
        "selected_results": selected_results,
        "blueprint": blueprint,
        "selection": selection,
        "certificate": certificate,
        "edges": edges,
        "candidates": candidates,
        "contributions": contributions,
        "rejected_candidates": rejected_candidates,
        "robust_lineage": robust_lineage,
        "certifying_evidence": certifying_evidence,

        "certification_core": certifying_evidence,
        "lineage_intervals": lineage_intervals,
        "certificate_lineage_intervals": certificate_lineage_intervals,
        "lineage_review_priorities": lineage_review_priorities,
        "lineage_review_screening": lineage_review_screening,
    }


def certification_core_candidates(
    selected_candidates: list[TraceCandidate],
    *,
    max_papers: int,
    exact_partition_max_components: int,
) -> list[TraceCandidate]:
    """Return the historical E054 selected-order prefix.

    The production runtime no longer calls this helper. It is retained so the
    frozen E054 development audit remains reproducible. Since
    ``full_selected_set_v2``, every selected report is certifying evidence and an
    incomplete exact analysis uses the conservative fallback on that same set.
    """

    if max_papers <= 0:
        raise ValueError("robust_certification_max_papers must be greater than zero")
    if max_papers > exact_partition_max_components:
        raise ValueError(
            "robust_certification_max_papers cannot exceed "
            "robust_exact_partition_max_components"
        )
    paper_order = list(dict.fromkeys(candidate.paper_id for candidate in selected_candidates))
    core_papers = set(paper_order[:max_papers])
    return [
        candidate
        for candidate in selected_candidates
        if candidate.paper_id in core_papers
    ]


def prepare_candidates(
    results: list[dict],
    blueprint: EvidenceBlueprint,
    lineage_ids: dict[str, str] | None = None,
) -> tuple[list[TraceCandidate], list[dict[str, object]]]:
    candidates = []
    rejected = []
    seen_chunk_ids: set[str] = set()
    for index, row in enumerate(results):
        try:
            candidate = candidate_from_result(row, blueprint, lineage_ids)
            if candidate.chunk_id in seen_chunk_ids:
                raise ValueError(
                    f"duplicate chunk_id {candidate.chunk_id!r}; first occurrence retained"
                )
            seen_chunk_ids.add(candidate.chunk_id)
            candidates.append(candidate)
        except (TypeError, ValueError) as exc:
            rejected.append(
                {
                    "row_index": index,
                    "paper_id": str(row.get("paper_id") or "") if isinstance(row, dict) else "",
                    "chunk_id": str(row.get("chunk_id") or "") if isinstance(row, dict) else "",
                    "reason": str(exc),
                }
            )
    return candidates, rejected


def candidate_from_result(
    row: dict,
    blueprint: EvidenceBlueprint,
    lineage_ids: dict[str, str] | None = None,
) -> TraceCandidate:
    if not isinstance(row, dict):
        raise TypeError("candidate row must be a mapping")
    text = str(row.get("text") or row.get("text_preview") or row.get("snippet") or "")
    paper_id = str(row.get("paper_id") or "").strip()
    chunk_id = str(row.get("chunk_id") or "").strip()
    if not paper_id:
        raise ValueError("candidate is missing paper_id")
    if not chunk_id:
        raise ValueError("candidate is missing chunk_id")
    if not text.strip():
        raise ValueError("candidate has no evidence text")
    doi = normalize_doi(str(row.get("doi") or ""))
    raw_score_value = row.get("score")
    if raw_score_value is None or raw_score_value == "":
        raw_score_value = row.get("raw_retrieval_score")
    raw_relevance = finite_float(raw_score_value, 0.0)
    fused_values = [
        row.get("paper_relevance_score"),
        row.get("paper_rrf_score"),
        row.get("rrf_score"),
    ]
    fused_relevance = max(
        *(finite_float(value, 0.0) for value in fused_values),
    ) * 1000.0
    quality_flags = normalize_string_list(row.get("quality_flags"))
    numeric_values = [raw_score_value, *fused_values]
    if any(
        value is not None and value != "" and not is_finite_number(value)
        for value in numeric_values
    ):
        quality_flags = [*quality_flags, "invalid_numeric_score"]
    candidate = TraceCandidate(
        chunk_id=chunk_id, paper_id=paper_id, text=text,
        token_cost=estimate_tokens(text), retrieval_score=max(raw_relevance, fused_relevance),
        section=str(row.get("section") or "unknown"), section_quality=section_quality(row),
        quality_flags=quality_flags, publication_family_id=doi or paper_id,
        study_lineage_id=(lineage_ids or {}).get(paper_id, paper_id),
        dataset_lineage_id=(
            str(row.get("dataset_lineage_id") or row.get("dataset_accession") or "").strip()
            or None
        ),
        facet_origin_ids=row.get("facet_origin_ids") or {},
        facet_origin_annotations_complete=row.get(
            "facet_origin_annotations_complete", False
        ),
        metadata={
            "author": row.get("author", ""), "year": row.get("year", ""), "title": row.get("title", ""),
            "doi": row.get("doi", ""), "page_start": row.get("page_start", ""), "page_end": row.get("page_end", ""),
            "local_pdf_relpath": row.get("local_pdf_relpath", ""), "raw_result": dict(row),
        },
    )
    if infer_direct_evidence_roles(
        title="",
        text=candidate.text,
        section=candidate.section,
        source_type=row.get("source_type_assessment") or row.get("source_type") or "",
    ):



        candidate.section_quality = max(candidate.section_quality, 0.85)
    candidate.facet_scores = score_candidate_facets(candidate, blueprint)
    candidate.scale_scores = infer_scale_scores(candidate, blueprint)
    candidate.evidence_type_scores = infer_evidence_type_scores(candidate)
    candidate.polarity_scores = infer_polarity_scores(candidate, reviewed=str(row.get("trace_polarity") or "") or None)
    return candidate


def load_blueprint(path: Path) -> EvidenceBlueprint:
    return EvidenceBlueprint.model_validate(read_json(path))


def load_edges(path: Path | None) -> list[EvidenceLineageEdge]:
    if not path:
        raise ValueError("lineage graph path was not provided")
    if not Path(path).exists():
        raise FileNotFoundError(f"lineage graph does not exist: {path}")
    payload = read_json(Path(path))
    if isinstance(payload, dict):
        if "edges" not in payload:
            raise ValueError("lineage graph object must contain an 'edges' list")
        rows = payload["edges"]
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError("lineage graph edges must be a list")
    return [EvidenceLineageEdge.model_validate(row) for row in rows]


def load_lineage_graph_metadata(path: Path | None) -> dict[str, str | None]:
    if not path:
        return {"schema_version": None, "artifact_status": "not_provided"}
    if not Path(path).exists():
        return {"schema_version": None, "artifact_status": "missing"}
    try:
        payload = read_json(Path(path))
    except (TypeError, ValueError):
        return {"schema_version": None, "artifact_status": "malformed"}
    if not isinstance(payload, dict):
        return {"schema_version": None, "artifact_status": "legacy_unversioned"}
    return {
        "schema_version": str(payload.get("schema_version") or "") or None,
        "artifact_status": str(payload.get("status") or "unknown"),
    }


def load_edges_with_status(
    path: Path | None,
    candidate_paper_ids: list[str],
) -> tuple[list[EvidenceLineageEdge], str]:
    candidate_set = set(candidate_paper_ids)
    if not path:
        return [], "not_applicable" if len(candidate_set) <= 1 else "not_provided"
    if not Path(path).exists():
        return [], "missing"
    try:
        edges = load_edges(path)
    except (TypeError, ValueError):
        return [], "malformed"
    if not edges:
        return [], "empty"
    if len(candidate_set) <= 1:
        return edges, "not_applicable"
    if _has_legacy_origin_decisions(Path(path), candidate_set):
        return edges, "legacy_origin_relation_missing"
    metadata = load_lineage_graph_metadata(path)
    if metadata["schema_version"] != LINEAGE_GRAPH_SCHEMA_VERSION:
        return edges, "schema_migration_required"
    artifact_status = str(metadata["artifact_status"] or "unknown")
    if artifact_status not in {"adjudicated", "frozen"}:
        return edges, f"artifact_{artifact_status}"
    return edges, lineage_review_status(
        edges,
        candidate_paper_ids,
        artifact_status=artifact_status,
    )


def _has_legacy_origin_decisions(path: Path, candidate_paper_ids: set[str]) -> bool:
    """Flag v1 verified scores that cannot safely be read as origin decisions."""

    payload = read_json(path)
    if isinstance(payload, dict):
        if payload.get("schema_version") == LINEAGE_GRAPH_SCHEMA_VERSION:
            return False
        rows = payload.get("edges", [])
    else:
        rows = payload
    return any(
        isinstance(row, dict)
        and row.get("paper_id_a") in candidate_paper_ids
        and row.get("paper_id_b") in candidate_paper_ids
        and bool(row.get("is_verified"))
        and not bool(row.get("is_hard_duplicate"))
        and "origin_relation" not in row
        for row in rows
    )


def lineage_review_status(
    edges: list[EvidenceLineageEdge],
    candidate_paper_ids: list[str],
    *,
    artifact_status: str = "adjudicated",
) -> str:
    candidate_set = set(candidate_paper_ids)
    if len(candidate_set) <= 1:
        return "not_applicable"
    trusted = certificate_trusted_edges(edges, artifact_status=artifact_status)
    candidate_edges = [
        edge
        for edge in trusted
        if edge.paper_id_a in candidate_set and edge.paper_id_b in candidate_set
    ]
    component_by_paper = assign_lineage_ids(sorted(candidate_set), candidate_edges)
    papers_by_component: dict[str, list[str]] = {}
    for paper_id, component in component_by_paper.items():
        papers_by_component.setdefault(component, []).append(paper_id)
    reviewed_pairs = {
        tuple(sorted((left, right)))
        for papers in papers_by_component.values()
        for index, left in enumerate(sorted(papers))
        for right in sorted(papers)[index + 1 :]
    }
    distinct_component_pairs = {
        tuple(
            sorted(
                (
                    component_by_paper[edge.paper_id_a],
                    component_by_paper[edge.paper_id_b],
                )
            )
        )
        for edge in candidate_edges
        if edge.origin_relation == "distinct_origin"
    }
    for component_a, component_b in distinct_component_pairs:
        reviewed_pairs.update(
            tuple(sorted((paper_a, paper_b)))
            for paper_a in papers_by_component[component_a]
            for paper_b in papers_by_component[component_b]
        )
    required_pairs = {
        tuple(sorted((left, right)))
        for index, left in enumerate(sorted(candidate_set))
        for right in sorted(candidate_set)[index + 1 :]
    }
    if required_pairs and required_pairs <= reviewed_pairs:
        return "reviewed"
    if reviewed_pairs:
        return "incomplete"
    return "suggested_only"


def certificate_trusted_edges(
    edges: list[EvidenceLineageEdge],
    *,
    artifact_status: str,
) -> list[EvidenceLineageEdge]:
    if artifact_status not in {"adjudicated", "frozen"}:
        return []
    return [edge for edge in edges if _edge_has_reproducible_origin_review(edge)]


def _edge_has_reproducible_origin_review(edge: EvidenceLineageEdge) -> bool:
    if edge.origin_review_status not in {"adjudicated", "frozen"}:
        return False
    if len(edge.origin_reviewers) < 2:
        return False
    if not all(
        (
            edge.origin_adjudicator,
            edge.origin_review_timestamp,
            edge.origin_evidence_locator,
            edge.origin_rationale,
        )
    ):
        return False
    if edge.origin_relation == "distinct_origin":
        return (
            edge.distinct_dependence_lower is not None
            and edge.distinct_dependence_upper is not None
        )
    return edge.origin_relation == "same_origin" or edge.is_hard_duplicate


def lineage_intervals_from_edges(
    edges: list[EvidenceLineageEdge],
    candidates: list[TraceCandidate],
    *,
    graph_artifact_status: str = "developmental",
) -> list[LineageDependenceInterval]:
    """Translate reviewed and suggested graph edges into certificate inputs.

    Suggested scores are discovery signals, not calibrated bounds, so they are
    widened to full uncertainty. Missing pairs remain absent here and are added
    as unknown by the strict robust-certificate policy.
    """

    selected_papers = {candidate.paper_id for candidate in candidates}
    hard_units = paper_independence_units(candidates)
    intervals: list[LineageDependenceInterval] = []
    seen_pairs: set[tuple[str, str]] = set()
    for edge in edges:
        if not (
            edge.paper_id_a in selected_papers
            and edge.paper_id_b in selected_papers
        ):
            continue
        paper_a, paper_b = sorted((edge.paper_id_a, edge.paper_id_b))
        pair = (paper_a, paper_b)
        if pair in seen_pairs:
            raise ValueError(
                f"duplicate lineage graph edge for selected pair {paper_a} / {paper_b}"
            )
        seen_pairs.add(pair)
        relationship = ",".join(sorted(set(edge.relationship_types))) or "unspecified"
        source = f"evidence_lineage_graph:{relationship}"
        same_hard_unit = hard_units[paper_a] == hard_units[paper_b]
        authoritative = (
            graph_artifact_status in {"adjudicated", "frozen"}
            and _edge_has_reproducible_origin_review(edge)
        )
        if authoritative and edge.origin_relation == "distinct_origin" and same_hard_unit:
            raise ValueError(
                "distinct_origin contradicts a shared study, dataset, or publication "
                f"resource for {paper_a} / {paper_b}"
            )
        if (
            (authoritative and edge.is_hard_duplicate)
            or (authoritative and edge.origin_relation == "same_origin")
            or same_hard_unit
        ):
            intervals.append(
                LineageDependenceInterval(
                    paper_id_a=paper_a,
                    paper_id_b=paper_b,
                    distinct_dependence_lower=1.0,
                    distinct_dependence_upper=1.0,
                    relation="must_link",
                    review_status=(
                        "frozen"
                        if same_hard_unit or edge.origin_review_status == "frozen"
                        else "human_reviewed"
                    ),
                    source=source,
                )
            )
        elif authoritative and edge.origin_relation == "distinct_origin":
            lower = edge.distinct_dependence_lower
            upper = edge.distinct_dependence_upper
            if lower is None or upper is None:
                raise RuntimeError("trusted distinct-origin edge has no declared interval")
            source = f"{source}:declared_conditional_interval"
            intervals.append(
                LineageDependenceInterval(
                    paper_id_a=paper_a,
                    paper_id_b=paper_b,
                    distinct_dependence_lower=lower,
                    distinct_dependence_upper=upper,
                    relation="cannot_link",
                    review_status="human_reviewed",
                    source=source,
                )
            )
        else:
            intervals.append(
                LineageDependenceInterval(
                    paper_id_a=paper_a,
                    paper_id_b=paper_b,
                    distinct_dependence_lower=0.0,
                    distinct_dependence_upper=1.0,
                    relation="may_link",
                    review_status="suggested",
                    source=source,
                )
            )
    return intervals


def write_trace_artifacts(
    output_dir: Path,
    blueprint: EvidenceBlueprint,
    edges: list[EvidenceLineageEdge],
    candidates: list[TraceCandidate],
    selection,
    certificate,
    contributions: list[dict],
    rejected_candidates: list[dict[str, object]] | None = None,
    robust_lineage: RobustEvidenceFlowBounds | None = None,
    lineage_intervals: list[LineageDependenceInterval] | None = None,
    certificate_lineage_intervals: list[LineageDependenceInterval] | None = None,
    lineage_review_priorities: list[LineageReviewPriority] | None = None,
    lineage_review_screening: dict[str, object] | None = None,
    certifying_evidence: list[TraceCandidate] | None = None,
    certification_core: list[TraceCandidate] | None = None,
) -> None:
    if certifying_evidence is None:
        certifying_evidence = certification_core
    elif certification_core is not None and certification_core != certifying_evidence:
        raise ValueError(
            "certification_core compatibility alias differs from certifying_evidence"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_ids = {candidate.chunk_id for candidate in selection.selected_candidates}
    selected_papers = {candidate.paper_id for candidate in selection.selected_candidates}
    relevant_edges = [
        edge
        for edge in edges
        if edge.paper_id_a in selected_papers and edge.paper_id_b in selected_papers
    ]
    write_json(output_dir / "trace_blueprint.json", blueprint.model_dump(mode="json"))
    write_json(output_dir / "trace_selection_result.json", selection.model_dump(mode="json"))
    write_json(output_dir / "trace_sufficiency_certificate.json", certificate.model_dump(mode="json"))
    write_tsv(output_dir / "trace_lineage_subset.tsv", [edge_row(edge) for edge in relevant_edges], edge_fields())
    write_tsv(output_dir / "trace_candidate_scores.tsv", [candidate_row(candidate, candidate.chunk_id in selected_ids) for candidate in candidates])
    write_tsv(output_dir / "trace_selected_evidence.tsv", [candidate_row(candidate, True) for candidate in selection.selected_candidates])
    certifying_rows = [
        candidate_row(candidate, True) for candidate in certifying_evidence or []
    ]
    certifying_fields = (
        list(candidate_row(selection.selected_candidates[0], True))
        if selection.selected_candidates
        else []
    )
    write_tsv(
        output_dir / "trace_certifying_evidence.tsv",
        certifying_rows,
        certifying_fields,
    )


    write_tsv(
        output_dir / "trace_certification_core.tsv",
        certifying_rows,
        certifying_fields,
    )
    write_tsv(output_dir / "trace_counterfactual_contributions.tsv", contributions)
    if robust_lineage is not None:
        write_json(
            output_dir / "trace_robust_lineage_bounds.json",
            robust_lineage.model_dump(mode="json"),
        )
    write_tsv(
        output_dir / "trace_robust_lineage_intervals.tsv",
        [lineage_interval_row(interval) for interval in lineage_intervals or []],
        lineage_interval_fields(),
    )
    write_json(
        output_dir / "trace_lineage_review_screening.json",
        lineage_review_screening or {},
    )
    write_tsv(
        output_dir / "trace_robust_lineage_complete_intervals.tsv",
        [
            lineage_interval_row(interval)
            for interval in certificate_lineage_intervals or []
        ],
        lineage_interval_fields(),
    )
    write_tsv(
        output_dir / "trace_lineage_review_priorities.tsv",
        [priority.model_dump(mode="json") for priority in lineage_review_priorities or []],
        lineage_review_priority_fields(),
    )
    write_tsv(
        output_dir / "trace_rejected_candidates.tsv",
        rejected_candidates or [],
        ["row_index", "paper_id", "chunk_id", "reason"],
    )
    decision_trace = selection.run_config.get("decision_trace") or []
    if decision_trace:
        write_tsv(output_dir / "trace_flow_decisions.tsv", decision_trace)
    report = format_trace_report(
        selection,
        certificate,
        contributions,
        robust_lineage=robust_lineage,
        lineage_review_priorities=lineage_review_priorities or [],
        lineage_review_screening=lineage_review_screening or {},
    )
    (output_dir / "trace_selection_report.md").write_text(report, encoding="utf-8")


def candidate_row(candidate: TraceCandidate, selected: bool) -> dict:
    return {
        "selected": str(selected).lower(), "chunk_id": candidate.chunk_id, "paper_id": candidate.paper_id,
        "token_cost": candidate.token_cost, "retrieval_score": candidate.retrieval_score,
        "section": candidate.section or "", "section_quality": candidate.section_quality,
        "publication_family_id": candidate.publication_family_id or "", "study_lineage_id": candidate.study_lineage_id or "",
        "facet_scores": json.dumps(candidate.facet_scores, sort_keys=True), "scale_scores": json.dumps(candidate.scale_scores, sort_keys=True),
        "evidence_type_scores": json.dumps(candidate.evidence_type_scores, sort_keys=True), "polarity_scores": json.dumps(candidate.polarity_scores, sort_keys=True),
        "quality_flags": ";".join(candidate.quality_flags),
    }


def edge_row(edge: EvidenceLineageEdge) -> dict:
    return {
        "paper_id_a": edge.paper_id_a, "paper_id_b": edge.paper_id_b, "dependence_score": edge.dependence_score,
        "relationship_types": ";".join(edge.relationship_types), "is_hard_duplicate": edge.is_hard_duplicate,
        "is_verified": edge.is_verified, "origin_relation": edge.origin_relation,
        "distinct_dependence_lower": edge.distinct_dependence_lower,
        "distinct_dependence_upper": edge.distinct_dependence_upper,
        "origin_review_status": edge.origin_review_status,
        "origin_reviewers": ";".join(edge.origin_reviewers),
        "origin_adjudicator": edge.origin_adjudicator,
        "origin_review_timestamp": edge.origin_review_timestamp,
        "origin_evidence_locator": edge.origin_evidence_locator,
        "origin_rationale": edge.origin_rationale,
        "explanation": edge.explanation,
    }


def edge_fields() -> list[str]:
    return [
        "paper_id_a",
        "paper_id_b",
        "dependence_score",
        "relationship_types",
        "is_hard_duplicate",
        "is_verified",
        "origin_relation",
        "distinct_dependence_lower",
        "distinct_dependence_upper",
        "origin_review_status",
        "origin_reviewers",
        "origin_adjudicator",
        "origin_review_timestamp",
        "origin_evidence_locator",
        "origin_rationale",
        "explanation",
    ]


def lineage_interval_row(interval: LineageDependenceInterval) -> dict:
    return interval.model_dump(mode="json")


def lineage_interval_fields() -> list[str]:
    return [
        "paper_id_a",
        "paper_id_b",
        "distinct_dependence_lower",
        "distinct_dependence_upper",
        "relation",
        "review_status",
        "source",
        "review_cost",
    ]


def lineage_review_priority_fields() -> list[str]:
    return [
        "rank",
        "paper_id_a",
        "paper_id_b",
        "source",
        "review_cost",
        "certificate_flip_possible",
        "guaranteed_gap_reduction",
        "best_case_gap_reduction",
        "outcome_separation",
        "priority_score",
        "low_outcome_status",
        "high_outcome_status",
    ]


def robust_lineage_run_summary(
    robust: RobustEvidenceFlowBounds,
    *,
    review_count: int,
    review_screening: dict[str, object],
) -> dict[str, object]:
    return {
        "evidence_accounting_version": EVIDENCE_ACCOUNTING_VERSION,
        "certificate_method_version": ROBUST_LINEAGE_CERTIFICATE_VERSION,
        "lineage_graph_schema_version": LINEAGE_GRAPH_SCHEMA_VERSION,
        "status": robust.status,
        "effective_evidence_lower": robust.effective_evidence.lower,
        "effective_evidence_upper": robust.effective_evidence.upper,
        "lineage_count_lower": robust.lineage_count_lower,
        "lineage_count_upper": robust.lineage_count_upper,
        "pessimistic_bottleneck_coverage": robust.pessimistic_flow.bottleneck_coverage,
        "pessimistic_unresolved_facets": robust.pessimistic_flow.unresolved_facets,
        "uncertain_pair_count": robust.uncertain_pair_count,
        "missing_pair_count": robust.missing_pair_count,
        "report_origin_homogeneity_verified": (
            robust.report_origin_homogeneity_verified
        ),
        "homogeneity_annotated_substantive_paper_count": (
            robust.homogeneity_annotated_substantive_paper_count
        ),
        "substantive_paper_count_for_homogeneity": (
            robust.substantive_paper_count_for_homogeneity
        ),
        "partition_bound_method": robust.partition_bound_method,
        "exact_for_declared_lineage_uncertainty": (
            robust.exact_for_declared_lineage_uncertainty
        ),
        "joint_optimistic_feasibility": robust.joint_optimistic_feasibility,
        "review_priority_count": review_count,
        "review_screening": review_screening,
    }


def lineage_review_screening_summary(
    robust: RobustEvidenceFlowBounds,
    complete_intervals: list[LineageDependenceInterval],
    priorities: list[LineageReviewPriority],
    *,
    simulation_budget: int,
) -> dict[str, object]:
    eligible_count = sum(
        interval.review_status != "frozen"
        and (
            interval.relation == "may_link"
            or abs(
                interval.distinct_dependence_upper
                - interval.distinct_dependence_lower
            )
            > 1e-12
        )
        for interval in complete_intervals
    )
    simulated_count = len(priorities)
    return {
        "evidence_accounting_version": EVIDENCE_ACCOUNTING_VERSION,
        "certificate_method_version": ROBUST_LINEAGE_CERTIFICATE_VERSION,
        "lineage_graph_schema_version": LINEAGE_GRAPH_SCHEMA_VERSION,
        "certificate_status": robust.status,
        "eligible_pair_count": eligible_count,
        "simulation_budget": simulation_budget,
        "simulated_pair_count": simulated_count,
        "screened_out_pair_count": max(0, eligible_count - simulated_count),
        "exhaustive": (
            robust.status == "lineage_review_required"
            and simulated_count == eligible_count
        ),
        "screening_method": "paper_weight_endpoint_impact_then_certificate_simulation",
    }


def format_trace_report(
    selection,
    certificate,
    contributions: list[dict],
    *,
    robust_lineage: RobustEvidenceFlowBounds | None = None,
    lineage_review_priorities: list[LineageReviewPriority] | None = None,
    lineage_review_screening: dict[str, object] | None = None,
) -> str:
    lines = [
        "# TRACE-RAG Selection Report", "", "Developmental output; suggested facets and lineages are not frozen gold.", "",
        f"- Selector: {selection.selector_name}", f"- Selected chunks: {len(selection.selected_candidates)}",
        f"- Evidence accounting: {certificate.evidence_accounting_version}",
        f"- Raw papers: {selection.raw_paper_count}",
        f"- Effective independent evidence: {selection.effective_independent_evidence:.3f}",
        f"- Independent evidence ratio: {certificate.independent_evidence_ratio:.3f}",
        f"- Evidence inflation factor: {certificate.evidence_inflation_factor:.3f}",
        f"- Facet coverage: {selection.facet_coverage:.3f}", f"- Scale triangulation: {selection.scale_triangulation:.3f}",
        f"- Token cost: {selection.token_cost}", f"- Stopping reason: {selection.run_config.get('stopping_reason')}", "",
        format_certificate_markdown(certificate), "",
    ]
    flow = selection.run_config.get("flow") or {}
    if flow:
        lines.extend(
            [
                "## TRACE-Flow",
                "",
                f"- Normalized flow coverage: {float(flow.get('normalized_coverage', 0.0)):.3f}",
                f"- Bottleneck facet coverage: {float(flow.get('bottleneck_coverage', 0.0)):.3f}",
                f"- Active lineage capacities: {len(flow.get('active_lineages', []))}",
                f"- Unresolved flow facets: {', '.join(flow.get('unresolved_facets', [])) or 'none'}",
                "",
            ]
        )
    if robust_lineage is not None:
        exact = robust_lineage.exact_partition_analysis
        lines.extend(
            [
                "## Robust lineage certificate",
                "",
                f"- Status: {robust_lineage.status}",
                (
                    "- Effective evidence interval: "
                    f"[{robust_lineage.effective_evidence.lower:.3f}, "
                    f"{robust_lineage.effective_evidence.upper:.3f}]"
                ),
                (
                    "- Lineage count interval: "
                    f"[{robust_lineage.lineage_count_lower}, "
                    f"{robust_lineage.lineage_count_upper}]"
                ),
                (
                    "- Pessimistic bottleneck coverage: "
                    f"{robust_lineage.pessimistic_flow.bottleneck_coverage:.3f}"
                ),
                (
                    "- Pessimistic unresolved facets: "
                    f"{', '.join(robust_lineage.pessimistic_flow.unresolved_facets) or 'none'}"
                ),
                f"- Missing / uncertain pairs: {robust_lineage.missing_pair_count} / {robust_lineage.uncertain_pair_count}",
                f"- Partition bound: {robust_lineage.partition_bound_method}",
                f"- Exact for declared uncertainty: {str(robust_lineage.exact_for_declared_lineage_uncertainty).lower()}",
                f"- Exact-search states: {exact.search_states} ({exact.reason})",
                (
                    "- Joint optimistic feasibility: "
                    f"{robust_lineage.joint_optimistic_feasibility}"
                ),
                f"- Joint partitions fully evaluated: {exact.evaluated_joint_partitions}",
                "",
            ]
        )
        priorities = lineage_review_priorities or []
        if priorities:
            screening = lineage_review_screening or {}
            qualifier = (
                "exhaustive candidates"
                if screening.get("exhaustive")
                else "screened candidates"
            )
            lines.extend(
                [
                    f"### Highest-value lineage reviews among {qualifier}",
                    "",
                    (
                        f"Simulated {screening.get('simulated_pair_count', len(priorities))} "
                        f"of {screening.get('eligible_pair_count', len(priorities))} "
                        "eligible pairs."
                    ),
                    "",
                ]
            )
            for priority in priorities[:5]:
                lines.append(
                    f"- #{priority.rank} {priority.paper_id_a} / {priority.paper_id_b}: "
                    f"score {priority.priority_score:.4f}; "
                    f"certificate flip {str(priority.certificate_flip_possible).lower()}"
                )
            lines.append("")
    lines.extend(["## Counterfactual paper contributions", ""])
    for row in contributions:
        if "flow_coverage_contribution" in row:
            lines.append(
                f"- {row['paper_id']}: flow {float(row['flow_coverage_contribution']):.4f}; "
                f"bottleneck {float(row['bottleneck_contribution']):.4f}; "
                f"reason {row.get('selection_reason')}; "
                f"facets lost {row.get('facets_lost_without_paper') or 'none'}"
            )
        else:
            lines.append(
                f"- {row['paper_id']}: utility {row['utility_contribution']:.4f}; "
                f"facet {row['facet_contribution']:.4f}; independence {row['independence_contribution']:.4f}"
            )
    return "\n".join(lines) + "\n"


def estimate_tokens(text: str) -> int:
    return max(1, int(len(re.findall(r"\w+|[^\w\s]", text or "")) * 1.15))


def section_quality(row: dict) -> float:
    section = str(row.get("section") or "unknown")
    base = {"results": 1.0, "discussion": 0.9, "abstract": 0.85, "methods": 0.75, "data_availability": 0.65, "unknown": 0.50, "supplement": 0.35, "table": 0.25, "figure_legend": 0.25, "references": 0.05}.get(section, 0.50)
    return max(0.0, base - 0.10 * len(normalize_string_list(row.get("quality_flags"))))


def normalize_doi(value: str) -> str:
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value.strip(), flags=re.IGNORECASE).lower()


def _canonical_question(value: str) -> str:
    return " ".join(str(value).casefold().split())
