from __future__ import annotations

import math
from collections import defaultdict
from typing import Protocol

from pydantic import Field

from originrag.trace.flow import compute_evidence_flow
from originrag.trace.independence_units import paper_independence_units
from originrag.trace.method_versions import (
    COMPLETE_BOX_PREFIX_EXACT_METHOD,
    COMPLETE_BOX_SUBSET_EXACT_METHOD,
    EXACT_PARTITION_ENUMERATION_METHOD,
    EXTENDED_CONSTRAINED_PARTITION_METHOD,
)
from originrag.trace.schemas import (
    EvidenceBlueprint,
    EvidenceFlowResult,
    TraceCandidate,
    TraceModel,
)


class LineageRelationLike(Protocol):
    paper_id_a: str
    paper_id_b: str
    relation: str
    distinct_dependence_lower: float
    distinct_dependence_upper: float


DEFAULT_EXACT_COMPONENT_LIMIT = 9
EXTENDED_SEARCH_COMPONENT_LIMIT = 64
EXTENDED_SEARCH_AMBIGUOUS_PAIR_LIMIT = 24


class ExactPartitionFlowAnalysis(TraceModel):
    """Exact worst partition flow when the bounded search finishes."""

    completed: bool
    component_count: int
    search_states: int = 0
    leaf_partitions: int = 0
    evaluated_maximal_partitions: int = 0
    evaluated_effective_partitions: int = 0
    evaluated_joint_partitions: int = 0
    max_components: int
    max_search_states: int
    lineage_count_lower: int | None = None
    lineage_count_partition: dict[str, str] = Field(default_factory=dict)
    pessimistic_flow: EvidenceFlowResult | None = None
    worst_partition: dict[str, str] = Field(default_factory=dict)
    effective_evidence_lower: float | None = None
    effective_evidence_upper: float | None = None
    worst_effective_partition: dict[str, str] = Field(default_factory=dict)
    best_effective_partition: dict[str, str] = Field(default_factory=dict)
    joint_partition_pass_exists: bool | None = None
    joint_passing_partition: dict[str, str] = Field(default_factory=dict)
    witnesses_verified: bool = False
    witness_verification_method: str = "not_run"
    reason: str
    method: str = EXACT_PARTITION_ENUMERATION_METHOD


def exact_partition_flow_analysis(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    relations: list[LineageRelationLike],
    *,
    lineage_capacity: float = 1.0,
    facet_threshold: float = 0.60,
    effective_evidence_ratio: float = 0.90,
    require_facet_flow: bool = True,
    force_safe_relaxation: bool = False,
    max_components: int = DEFAULT_EXACT_COMPONENT_LIMIT,
    max_search_states: int = 100_000,
    counted_paper_ids: set[str] | None = None,
    paper_weights: dict[str, float] | None = None,
    use_complete_box_special_case: bool = True,
) -> ExactPartitionFlowAnalysis:
    """Enumerate feasible partitions of must-link components exactly.

    Unresolved relations may either join or separate their endpoint components.
    Cannot-link relations forbid co-membership. Only maximal feasible coarsenings
    require flow evaluation because splitting evidence units cannot reduce flow.
    ``effective_evidence_ratio`` multiplies the blueprint's minimum lineage count
    to define the effective-evidence floor used by joint feasibility. Setting
    ``require_facet_flow`` false is a controlled component ablation: flow is still
    evaluated and reported, but it does not enter joint feasibility. Setting
    ``force_safe_relaxation`` bypasses enumeration so callers can audit the
    conservative fallback policy on otherwise exactly solvable inputs.
    ``use_complete_box_special_case`` may be disabled only to retain the
    general enumeration path as an independent comparison reference.
    """

    if max_components <= 0:
        raise ValueError("max_components must be greater than zero")
    if max_search_states <= 0:
        raise ValueError("max_search_states must be greater than zero")
    if not math.isfinite(lineage_capacity) or lineage_capacity <= 0.0:
        raise ValueError("lineage_capacity must be finite and greater than zero")
    if not math.isfinite(facet_threshold) or not 0.0 <= facet_threshold <= 1.0:
        raise ValueError("facet_threshold must be finite and between zero and one")
    if not math.isfinite(effective_evidence_ratio) or not 0.0 <= effective_evidence_ratio <= 1.0:
        raise ValueError("effective_evidence_ratio must be finite and between zero and one")
    paper_ids = sorted({candidate.paper_id for candidate in candidates})
    _validate_relation_inputs(relations, set(paper_ids))
    counted_papers = set(paper_ids) if counted_paper_ids is None else set(counted_paper_ids)
    unknown_counted = sorted(counted_papers - set(paper_ids))
    if unknown_counted:
        raise ValueError("counted_paper_ids contains unknown papers: " + ", ".join(unknown_counted))
    parsed_weights = None
    if paper_weights is not None:
        if set(paper_weights) != set(paper_ids):
            raise ValueError("paper_weights must contain exactly every candidate paper")
        parsed_weights = {paper_id: float(paper_weights[paper_id]) for paper_id in paper_ids}
        if any(not math.isfinite(weight) or weight < 0.0 for weight in parsed_weights.values()):
            raise ValueError("paper_weights must be finite and nonnegative")
    disjoint = _DisjointSet(paper_ids)
    resources = paper_independence_units(candidates)
    papers_by_resource: dict[str, list[str]] = defaultdict(list)
    for paper_id, resource_unit in resources.items():
        papers_by_resource[resource_unit].append(paper_id)
    for papers in papers_by_resource.values():
        for paper_id in papers[1:]:
            disjoint.union(papers[0], paper_id)
    for edge in relations:
        if edge.relation == "must_link":
            disjoint.union(edge.paper_id_a, edge.paper_id_b)

    members_by_root: dict[str, list[str]] = defaultdict(list)
    for paper_id in paper_ids:
        members_by_root[disjoint.find(paper_id)].append(paper_id)
    roots = sorted(members_by_root)
    cannot_pairs: set[tuple[str, str]] = set()
    relation_by_pair: dict[tuple[str, str], LineageRelationLike] = {}
    for edge in relations:
        pair = _pair_key(edge.paper_id_a, edge.paper_id_b)
        if pair in relation_by_pair:
            raise ValueError(f"duplicate relation for pair {pair[0]} / {pair[1]}")
        relation_by_pair[pair] = edge
        if edge.relation != "cannot_link":
            continue
        left = disjoint.find(edge.paper_id_a)
        right = disjoint.find(edge.paper_id_b)
        if left == right:
            raise ValueError(
                "cannot-link relation occurs inside a must-link component: "
                f"{edge.paper_id_a} / {edge.paper_id_b}"
            )
        cannot_pairs.add(_pair_key(left, right))
    if parsed_weights is not None:
        expected_pairs = len(paper_ids) * (len(paper_ids) - 1) // 2
        if len(relation_by_pair) != expected_pairs:
            raise ValueError(
                "exact effective-evidence analysis requires one relation per paper pair"
            )

    component_count = len(roots)
    fine_partition = {paper_id: f"EXACT_FINE:{disjoint.find(paper_id)}" for paper_id in paper_ids}
    fine_signature = tuple((root,) for root in sorted(roots))
    fine_flow = None
    if parsed_weights is not None:
        fine_flow = compute_evidence_flow(
            candidates,
            blueprint,
            lineage_capacity=lineage_capacity,
            facet_threshold=facet_threshold,
            unit_by_paper=fine_partition,
        )
    fine_effective_lower = (
        _effective_evidence_for_partition(
            parsed_weights,
            fine_partition,
            relation_by_pair,
            use_upper=True,
        )
        if parsed_weights is not None
        else None
    )
    fine_effective_upper = (
        _effective_evidence_for_partition(
            parsed_weights,
            fine_partition,
            relation_by_pair,
            use_upper=False,
        )
        if parsed_weights is not None
        else None
    )
    fine_lineage_count = len({fine_partition[paper_id] for paper_id in counted_papers})
    fine_joint_pass = (
        parsed_weights is not None
        and fine_flow is not None
        and fine_effective_upper is not None
        and fine_lineage_count >= blueprint.minimum_independent_lineages
        and fine_effective_upper + 1e-9
        >= effective_evidence_ratio * blueprint.minimum_independent_lineages
        and (not require_facet_flow or _flow_is_complete(fine_flow))
    )
    component_pair_count = component_count * (component_count - 1) // 2
    if force_safe_relaxation:
        return ExactPartitionFlowAnalysis(
            completed=False,
            component_count=component_count,
            evaluated_joint_partitions=(1 if parsed_weights is not None else 0),
            max_components=max_components,
            max_search_states=max_search_states,
            joint_partition_pass_exists=(True if fine_joint_pass else None),
            joint_passing_partition=(fine_partition if fine_joint_pass else {}),
            reason="forced_safe_relaxation",
        )
    if len(cannot_pairs) == component_pair_count:
        flow = fine_flow or compute_evidence_flow(
            candidates,
            blueprint,
            lineage_capacity=lineage_capacity,
            facet_threshold=facet_threshold,
            unit_by_paper=fine_partition,
        )
        analysis = ExactPartitionFlowAnalysis(
            completed=True,
            component_count=component_count,
            search_states=1,
            leaf_partitions=1,
            evaluated_maximal_partitions=1,
            evaluated_effective_partitions=(1 if parsed_weights is not None else 0),
            evaluated_joint_partitions=(1 if parsed_weights is not None else 0),
            max_components=max_components,
            max_search_states=max_search_states,
            lineage_count_lower=len({fine_partition[paper_id] for paper_id in counted_papers}),
            lineage_count_partition=fine_partition,
            pessimistic_flow=flow,
            worst_partition=fine_partition,
            effective_evidence_lower=fine_effective_lower,
            effective_evidence_upper=fine_effective_upper,
            worst_effective_partition=(fine_partition if parsed_weights is not None else {}),
            best_effective_partition=(fine_partition if parsed_weights is not None else {}),
            joint_partition_pass_exists=(
                bool(fine_joint_pass) if parsed_weights is not None else None
            ),
            joint_passing_partition=(fine_partition if fine_joint_pass else {}),
            reason=("exact_empty_partition" if not roots else "exact_unique_partition"),
        )
        verify_exact_partition_witnesses(
            candidates,
            blueprint,
            relations,
            analysis,
            lineage_capacity=lineage_capacity,
            facet_threshold=facet_threshold,
            effective_evidence_ratio=effective_evidence_ratio,
            require_facet_flow=require_facet_flow,
            counted_paper_ids=counted_papers,
            paper_weights=parsed_weights,
        )
        return analysis.model_copy(
            update={
                "witnesses_verified": True,
                "witness_verification_method": "recomputed_endpoint_witnesses_v1",
            }
        )
    if use_complete_box_special_case and parsed_weights is not None:
        complete_box = _complete_box_partition_analysis(
            candidates,
            blueprint,
            members_by_root=members_by_root,
            roots=roots,
            relation_by_pair=relation_by_pair,
            lineage_capacity=lineage_capacity,
            facet_threshold=facet_threshold,
            effective_evidence_ratio=effective_evidence_ratio,
            require_facet_flow=require_facet_flow,
            max_components=max_components,
            max_search_states=max_search_states,
            counted_paper_ids=counted_papers,
            paper_weights=parsed_weights,
            fine_partition=fine_partition,
            fine_flow=fine_flow,
            fine_effective_lower=fine_effective_lower,
            fine_effective_upper=fine_effective_upper,
            fine_lineage_count=fine_lineage_count,
            fine_joint_pass=bool(fine_joint_pass),
        )
        if complete_box is not None:
            verify_exact_partition_witnesses(
                candidates,
                blueprint,
                relations,
                complete_box,
                lineage_capacity=lineage_capacity,
                facet_threshold=facet_threshold,
                effective_evidence_ratio=effective_evidence_ratio,
                require_facet_flow=require_facet_flow,
                counted_paper_ids=counted_papers,
                paper_weights=parsed_weights,
            )
            return complete_box.model_copy(
                update={
                    "witnesses_verified": True,
                    "witness_verification_method": "recomputed_endpoint_witnesses_v1",
                }
            )
    extended_search = component_count > max_components
    if extended_search:
        incomplete_reason = _extended_search_ineligibility_reason(
            component_count=component_count,
            ambiguous_pair_count=component_pair_count - len(cannot_pairs),
            configured_component_limit=max_components,
        )
        if incomplete_reason is not None:
            return ExactPartitionFlowAnalysis(
                completed=False,
                component_count=component_count,
                evaluated_joint_partitions=(1 if parsed_weights is not None else 0),
                max_components=max_components,
                max_search_states=max_search_states,
                joint_partition_pass_exists=(True if fine_joint_pass else None),
                joint_passing_partition=(fine_partition if fine_joint_pass else {}),
                reason=incomplete_reason,
            )
    degree = {root: sum(root in pair for pair in cannot_pairs) for root in roots}
    ordered_roots = sorted(roots, key=lambda root: (-degree[root], root))
    state = _SearchState(
        candidates=candidates,
        blueprint=blueprint,
        members_by_root=members_by_root,
        cannot_pairs=cannot_pairs,
        lineage_capacity=lineage_capacity,
        facet_threshold=facet_threshold,
        effective_evidence_ratio=effective_evidence_ratio,
        require_facet_flow=require_facet_flow,
        max_search_states=max_search_states,
        counted_paper_ids=counted_papers,
        paper_weights=parsed_weights,
        relation_by_pair=relation_by_pair,
        initial_joint_pass=bool(fine_joint_pass),
        initial_joint_partition=(fine_partition if fine_joint_pass else {}),
        fine_signature=fine_signature,
        fine_flow=fine_flow,
    )
    _search_partitions(ordered_roots, 0, [], state)
    if state.aborted:
        return ExactPartitionFlowAnalysis(
            completed=False,
            component_count=component_count,
            search_states=state.search_states,
            leaf_partitions=state.leaf_partitions,
            evaluated_maximal_partitions=state.evaluated_partitions,
            evaluated_effective_partitions=state.evaluated_effective_partitions,
            evaluated_joint_partitions=state.evaluated_joint_partitions,
            max_components=max_components,
            max_search_states=max_search_states,
            joint_partition_pass_exists=(True if state.joint_partition_pass_exists else None),
            joint_passing_partition=state.joint_passing_partition,
            reason="search_state_limit_exceeded",
            method=(
                EXTENDED_CONSTRAINED_PARTITION_METHOD
                if extended_search
                else EXACT_PARTITION_ENUMERATION_METHOD
            ),
        )
    if state.worst_flow is None or state.lineage_count_lower is None:
        raise RuntimeError("exact partition search produced no feasible partition")
    analysis = ExactPartitionFlowAnalysis(
        completed=True,
        component_count=component_count,
        search_states=state.search_states,
        leaf_partitions=state.leaf_partitions,
        evaluated_maximal_partitions=state.evaluated_partitions,
        evaluated_effective_partitions=state.evaluated_effective_partitions,
        evaluated_joint_partitions=state.evaluated_joint_partitions,
        max_components=max_components,
        max_search_states=max_search_states,
        lineage_count_lower=state.lineage_count_lower,
        lineage_count_partition=state.lineage_count_partition,
        pessimistic_flow=state.worst_flow,
        worst_partition=state.worst_partition,
        effective_evidence_lower=state.effective_lower,
        effective_evidence_upper=state.effective_upper,
        worst_effective_partition=state.worst_effective_partition,
        best_effective_partition=state.best_effective_partition,
        joint_partition_pass_exists=(
            state.joint_partition_pass_exists if parsed_weights is not None else None
        ),
        joint_passing_partition=state.joint_passing_partition,
        reason=("exact_extended_search_complete" if extended_search else "exact_search_complete"),
        method=(
            EXTENDED_CONSTRAINED_PARTITION_METHOD
            if extended_search
            else EXACT_PARTITION_ENUMERATION_METHOD
        ),
    )
    verify_exact_partition_witnesses(
        candidates,
        blueprint,
        relations,
        analysis,
        lineage_capacity=lineage_capacity,
        facet_threshold=facet_threshold,
        effective_evidence_ratio=effective_evidence_ratio,
        require_facet_flow=require_facet_flow,
        counted_paper_ids=counted_papers,
        paper_weights=parsed_weights,
    )
    return analysis.model_copy(
        update={
            "witnesses_verified": True,
            "witness_verification_method": "recomputed_endpoint_witnesses_v1",
        }
    )


def _extended_search_ineligibility_reason(
    *,
    component_count: int,
    ambiguous_pair_count: int,
    configured_component_limit: int,
) -> str | None:
    """Return why a beyond-limit constrained search must remain conservative.

    Lowering ``max_components`` is used by controlled fallback tests and must
    not silently enable this path. The extension applies only at or above the
    production default. The fixed structural limits bound recursion depth and
    per-state compatibility work; ``max_search_states`` remains the primary
    deterministic run-time and memory bound once search starts.
    """

    if configured_component_limit < DEFAULT_EXACT_COMPONENT_LIMIT:
        return "component_limit_exceeded"
    if component_count > EXTENDED_SEARCH_COMPONENT_LIMIT:
        return "extended_search_component_limit_exceeded"
    if ambiguous_pair_count > EXTENDED_SEARCH_AMBIGUOUS_PAIR_LIMIT:
        return "extended_search_ambiguity_limit_exceeded"
    return None


def _complete_box_partition_analysis(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    *,
    members_by_root: dict[str, list[str]],
    roots: list[str],
    relation_by_pair: dict[tuple[str, str], LineageRelationLike],
    lineage_capacity: float,
    facet_threshold: float,
    effective_evidence_ratio: float,
    require_facet_flow: bool,
    max_components: int,
    max_search_states: int,
    counted_paper_ids: set[str],
    paper_weights: dict[str, float],
    fine_partition: dict[str, str],
    fine_flow: EvidenceFlowResult | None,
    fine_effective_lower: float | None,
    fine_effective_upper: float | None,
    fine_lineage_count: int,
    fine_joint_pass: bool,
) -> ExactPartitionFlowAnalysis | None:
    """Solve a complete ``may_link [0, 1]`` box without Bell enumeration.

    After hard-unit contraction, this special case permits every set partition.
    The one-block partition therefore gives the exact lineage, flow, and
    effective-evidence lower witnesses. At the zero-dependence endpoint, each
    partition's positive unit weights are the maxima of its blocks. Such a
    vector is exactly a subset of component maxima containing a global maximum.
    Conversely, assigning every omitted component to that global-maximum block
    realises the subset. Above the implementation's small-number clamp, an
    optimal subset consists of that anchor and an ascending prefix of the
    remaining weights. The older exhaustive subset calculation is retained for
    the numerically clamped edge case and as a comparison reference.

    Joint feasibility is returned only when the fine partition passes, a
    monotone gate rules every coarsening out, or the upper witness itself passes.
    Any remaining joint case returns ``None`` and uses the ordinary safe
    fallback. Candidate endpoint vectors are charged to the existing
    search-state budget.
    """

    if fine_flow is None or fine_effective_lower is None or fine_effective_upper is None:
        return None
    for left_index, left_root in enumerate(roots):
        for right_root in roots[left_index + 1 :]:
            for left_paper in members_by_root[left_root]:
                for right_paper in members_by_root[right_root]:
                    edge = relation_by_pair.get(_pair_key(left_paper, right_paper))
                    if edge is None or edge.relation != "may_link":
                        return None
                    if not (
                        math.isclose(float(edge.distinct_dependence_lower), 0.0)
                        and math.isclose(float(edge.distinct_dependence_upper), 1.0)
                    ):
                        return None

    root_weights = {
        root: max((paper_weights[paper_id] for paper_id in members_by_root[root]), default=0.0)
        for root in roots
    }
    nonzero_roots = sorted(root for root in roots if root_weights[root] > 0.0)
    substantive_roots = sorted(root for root in roots if root_weights[root] > 1e-12)

    all_merged_partition = {
        paper_id: "EXACT_BOX:ALL"
        for root in roots
        for paper_id in members_by_root[root]
    }
    all_merged_flow = compute_evidence_flow(
        candidates,
        blueprint,
        lineage_capacity=lineage_capacity,
        facet_threshold=facet_threshold,
        unit_by_paper=all_merged_partition,
    )
    lineage_lower = 1 if counted_paper_ids else 0
    effective_lower = _effective_evidence_for_partition(
        paper_weights,
        all_merged_partition,
        relation_by_pair,
        use_upper=True,
    )

    if not substantive_roots:
        best_effective = 0.0
        best_partition = dict(all_merged_partition)
        prefix_anchor: str | None = None
        prefix_optional_roots: list[str] = []
        endpoint_values = [best_effective]
        subset_candidates: list[tuple[float, list[str]]] | None = None
        endpoint_states = 1
        exact_method = COMPLETE_BOX_PREFIX_EXACT_METHOD
        exact_reason = "exact_complete_box_prefix"
    else:
        anchor = min(nonzero_roots, key=lambda root: (-root_weights[root], root))
        prefix_is_numerically_equivalent = (
            len(nonzero_roots) == len(substantive_roots)
            and root_weights[anchor] ** 2 > 1e-12
        )
        if prefix_is_numerically_equivalent:
            (
                best_effective,
                best_selected,
                prefix_anchor,
                prefix_optional_roots,
                endpoint_values,
            ) = _complete_box_prefix_endpoint(
                root_weights,
                substantive_roots,
            )
            subset_candidates = None
            endpoint_states = len(endpoint_values)
            exact_method = COMPLETE_BOX_PREFIX_EXACT_METHOD
            exact_reason = "exact_complete_box_prefix"
        else:
            subset_count = 1 << max(0, len(nonzero_roots) - 1)
            if subset_count > max_search_states:
                return None
            subset_candidates = _complete_box_subset_reference_candidates(
                root_weights,
                nonzero_roots,
            )
            prefix_anchor = None
            prefix_optional_roots = []
            endpoint_values = [effective for effective, _ in subset_candidates]
            endpoint_states = len(subset_candidates)
            exact_method = COMPLETE_BOX_SUBSET_EXACT_METHOD
            exact_reason = "exact_complete_box_subset_numeric_guard"
        if endpoint_states > max_search_states:
            return None
        if subset_candidates is not None:
            best_effective, best_selected = subset_candidates[0]
            for effective, selected in subset_candidates[1:]:

                if effective > best_effective:
                    best_effective = effective
                    best_selected = selected
        best_partition = _complete_box_partition(
            best_selected,
            anchor=anchor,
            roots=roots,
            members_by_root=members_by_root,
        )

    required_lineages = blueprint.minimum_independent_lineages
    required_effective = effective_evidence_ratio * required_lineages
    joint_pass = fine_joint_pass
    joint_partition = dict(fine_partition) if fine_joint_pass else {}
    evaluated_joint_partitions = 1
    if not joint_pass:
        fine_flow_passes = not require_facet_flow or _flow_is_complete(fine_flow)
        monotone_failure = (
            not fine_flow_passes
            or fine_lineage_count < required_lineages
            or best_effective + 1e-9 < required_effective
        )
        if not monotone_failure:
            if subset_candidates is not None:
                joint_candidates = subset_candidates
            elif prefix_anchor is None:
                joint_candidates = [(0.0, [])]
            else:
                joint_candidates = (
                    (
                        effective,
                        [prefix_anchor, *prefix_optional_roots[:prefix_length]],
                    )
                    for prefix_length, effective in enumerate(endpoint_values)
                )
            for effective, selected in joint_candidates:
                partition = (
                    dict(all_merged_partition)
                    if not selected
                    else _complete_box_partition(
                        selected,
                        anchor=selected[0],
                        roots=roots,
                        members_by_root=members_by_root,
                    )
                )
                lineage_count = len(
                    {partition[paper_id] for paper_id in counted_paper_ids}
                )
                if (
                    lineage_count < required_lineages
                    or effective + 1e-9 < required_effective
                ):
                    continue
                flow = compute_evidence_flow(
                    candidates,
                    blueprint,
                    lineage_capacity=lineage_capacity,
                    facet_threshold=facet_threshold,
                    unit_by_paper=partition,
                )
                evaluated_joint_partitions += 1
                if not require_facet_flow or _flow_is_complete(flow):
                    joint_pass = True
                    joint_partition = dict(partition)
                    break
            if not joint_pass:
                return None

    return ExactPartitionFlowAnalysis(
        completed=True,
        component_count=len(roots),
        search_states=endpoint_states,
        leaf_partitions=0,
        evaluated_maximal_partitions=1,
        evaluated_effective_partitions=endpoint_states,
        evaluated_joint_partitions=evaluated_joint_partitions,
        max_components=max_components,
        max_search_states=max_search_states,
        lineage_count_lower=lineage_lower,
        lineage_count_partition=all_merged_partition,
        pessimistic_flow=all_merged_flow,
        worst_partition=all_merged_partition,
        effective_evidence_lower=effective_lower,
        effective_evidence_upper=best_effective,
        worst_effective_partition=all_merged_partition,
        best_effective_partition=best_partition,
        joint_partition_pass_exists=joint_pass,
        joint_passing_partition=joint_partition,
        reason=exact_reason,
        method=exact_method,
    )


def _complete_box_prefix_endpoint(
    root_weights: dict[str, float],
    positive_roots: list[str],
) -> tuple[float, list[str], str, list[str], list[float]]:
    """Find the exact anchor-plus-prefix endpoint in ``O(m log m)`` time.

    The caller must establish that the global-maximum anchor is outside the
    small-denominator clamp. Under that condition these prefixes contain an
    exact maximiser of the zero-dependence effective-evidence endpoint. Ties
    retain the shortest prefix after sorting by ``(weight, root)``.
    """

    anchor = min(positive_roots, key=lambda root: (-root_weights[root], root))
    optional_roots = sorted(
        (root for root in positive_roots if root != anchor),
        key=lambda root: (root_weights[root], root),
    )
    total = root_weights[anchor]
    squared_total = root_weights[anchor] ** 2
    endpoint_values = [_effective_from_weight_sums(total, squared_total, 1)]
    best_effective = endpoint_values[0]
    best_prefix_length = 0
    for prefix_length, root in enumerate(optional_roots, start=1):
        weight = root_weights[root]
        total += weight
        squared_total += weight * weight
        effective = _effective_from_weight_sums(
            total,
            squared_total,
            prefix_length + 1,
        )
        endpoint_values.append(effective)
        if effective > best_effective:
            best_effective = effective
            best_prefix_length = prefix_length
    best_selected = [anchor, *optional_roots[:best_prefix_length]]
    return best_effective, best_selected, anchor, optional_roots, endpoint_values


def _complete_box_subset_reference_candidates(
    root_weights: dict[str, float],
    positive_roots: list[str],
) -> list[tuple[float, list[str]]]:
    """Retain the v1 anchored-subset enumeration as a validation reference."""

    anchor = min(positive_roots, key=lambda root: (-root_weights[root], root))
    optional_roots = [root for root in positive_roots if root != anchor]
    candidates: list[tuple[float, list[str]]] = []
    for mask in range(1 << len(optional_roots)):
        selected = [anchor]
        selected.extend(
            root
            for index, root in enumerate(optional_roots)
            if mask & (1 << index)
        )
        weights = [root_weights[root] for root in selected]
        candidates.append((_effective_from_weights(weights), selected))
    return candidates


def _complete_box_partition(
    selected: list[str],
    *,
    anchor: str,
    roots: list[str],
    members_by_root: dict[str, list[str]],
) -> dict[str, str]:
    selected_set = set(selected)
    groups = [[anchor, *(root for root in roots if root not in selected_set)]]
    groups.extend([[root] for root in selected if root != anchor])
    _, partition = _partition_mapping(groups, members_by_root)
    return partition


def _effective_from_weights(weights: list[float]) -> float:
    return _effective_from_weight_sums(
        sum(weights),
        sum(weight * weight for weight in weights),
        sum(weight > 1e-12 for weight in weights),
    )


def _effective_from_weight_sums(total: float, squared_total: float, count: int) -> float:
    numerator = total * total
    if numerator <= 1e-12:
        return 0.0
    return min(
        float(count),
        max(0.0, numerator / max(squared_total, 1e-12)),
    )


def verify_exact_partition_witnesses(
    candidates: list[TraceCandidate],
    blueprint: EvidenceBlueprint,
    relations: list[LineageRelationLike],
    analysis: ExactPartitionFlowAnalysis,
    *,
    lineage_capacity: float = 1.0,
    facet_threshold: float = 0.60,
    effective_evidence_ratio: float = 0.90,
    require_facet_flow: bool = True,
    counted_paper_ids: set[str] | None = None,
    paper_weights: dict[str, float] | None = None,
) -> None:
    """Replay every emitted endpoint witness and fail on any inconsistency.

    This verifier does not establish that the search visited every feasible
    partition. It independently checks that every reported witness is feasible
    and reproduces the endpoint or joint-pass value attributed to it.
    """

    if not analysis.completed:
        raise ValueError("cannot verify witnesses from an incomplete exact search")
    paper_ids = sorted({candidate.paper_id for candidate in candidates})
    if not math.isfinite(lineage_capacity) or lineage_capacity <= 0.0:
        raise ValueError("lineage_capacity must be finite and greater than zero")
    if not math.isfinite(facet_threshold) or not 0.0 <= facet_threshold <= 1.0:
        raise ValueError("facet_threshold must be finite and between zero and one")
    if not math.isfinite(effective_evidence_ratio) or not 0.0 <= effective_evidence_ratio <= 1.0:
        raise ValueError("effective_evidence_ratio must be finite and between zero and one")
    _validate_relation_inputs(relations, set(paper_ids))
    counted_papers = set(paper_ids) if counted_paper_ids is None else set(counted_paper_ids)
    if not counted_papers <= set(paper_ids):
        raise ValueError("counted_paper_ids contains unknown papers")
    relation_by_pair = {_pair_key(edge.paper_id_a, edge.paper_id_b): edge for edge in relations}
    if len(relation_by_pair) != len(relations):
        raise ValueError("relations contain a duplicate paper pair")
    resources = paper_independence_units(candidates)
    flow_cache: dict[tuple[tuple[str, ...], ...], EvidenceFlowResult] = {}

    def partition_signature(partition: dict[str, str]) -> tuple[tuple[str, ...], ...]:
        members: dict[str, list[str]] = defaultdict(list)
        for paper_id, unit in partition.items():
            members[unit].append(paper_id)
        return tuple(sorted(tuple(sorted(group)) for group in members.values()))

    def replay_flow(partition: dict[str, str]) -> EvidenceFlowResult:
        signature = partition_signature(partition)
        if signature not in flow_cache:
            flow_cache[signature] = compute_evidence_flow(
                candidates,
                blueprint,
                lineage_capacity=lineage_capacity,
                facet_threshold=facet_threshold,
                unit_by_paper=partition,
            )
        return flow_cache[signature]

    def verify_partition(partition: dict[str, str], name: str) -> None:
        if set(partition) != set(paper_ids):
            raise RuntimeError(f"{name} does not assign every candidate paper exactly once")
        if any(not str(unit).strip() for unit in partition.values()):
            raise RuntimeError(f"{name} contains an empty origin-unit label")
        papers_by_resource: dict[str, list[str]] = defaultdict(list)
        for paper_id, resource in resources.items():
            papers_by_resource[resource].append(paper_id)
        for members in papers_by_resource.values():
            if len({partition[paper_id] for paper_id in members}) != 1:
                raise RuntimeError(f"{name} splits one hard independence resource")
        for edge in relations:
            same_unit = partition[edge.paper_id_a] == partition[edge.paper_id_b]
            if edge.relation == "must_link" and not same_unit:
                raise RuntimeError(f"{name} violates a must-link relation")
            if edge.relation == "cannot_link" and same_unit:
                raise RuntimeError(f"{name} violates a cannot-link relation")

    verify_partition(analysis.worst_partition, "worst_partition")
    replayed_flow = replay_flow(analysis.worst_partition)
    if analysis.pessimistic_flow is None or replayed_flow != analysis.pessimistic_flow:
        raise RuntimeError("worst_partition does not reproduce pessimistic_flow")
    verify_partition(analysis.lineage_count_partition, "lineage_count_partition")
    replayed_lineage_count = len(
        {analysis.lineage_count_partition[paper_id] for paper_id in counted_papers}
    )
    if replayed_lineage_count != analysis.lineage_count_lower:
        raise RuntimeError("lineage_count_partition does not reproduce lineage_count_lower")

    if paper_weights is None:
        if any(
            value is not None
            for value in (
                analysis.effective_evidence_lower,
                analysis.effective_evidence_upper,
                analysis.joint_partition_pass_exists,
            )
        ):
            raise RuntimeError("effective-evidence witnesses require paper_weights")
        return
    parsed_weights = {paper_id: float(value) for paper_id, value in paper_weights.items()}
    if set(parsed_weights) != set(paper_ids) or any(
        not math.isfinite(value) or value < 0.0 for value in parsed_weights.values()
    ):
        raise ValueError("paper_weights must be finite, nonnegative, and complete")
    expected_pairs = len(paper_ids) * (len(paper_ids) - 1) // 2
    if len(relation_by_pair) != expected_pairs:
        raise ValueError("effective-evidence witness replay requires every paper pair")
    effective_cache: dict[tuple[tuple[tuple[str, ...], ...], bool], float] = {}

    def replay_effective(partition: dict[str, str], *, use_upper: bool) -> float:
        key = (partition_signature(partition), use_upper)
        if key not in effective_cache:
            effective_cache[key] = _effective_evidence_for_partition(
                parsed_weights,
                partition,
                relation_by_pair,
                use_upper=use_upper,
            )
        return effective_cache[key]

    verify_partition(analysis.worst_effective_partition, "worst_effective_partition")
    verify_partition(analysis.best_effective_partition, "best_effective_partition")
    replayed_lower = replay_effective(analysis.worst_effective_partition, use_upper=True)
    replayed_upper = replay_effective(analysis.best_effective_partition, use_upper=False)
    if analysis.effective_evidence_lower is None or not math.isclose(
        replayed_lower, analysis.effective_evidence_lower, rel_tol=1e-12, abs_tol=1e-12
    ):
        raise RuntimeError("worst_effective_partition does not reproduce its endpoint")
    if analysis.effective_evidence_upper is None or not math.isclose(
        replayed_upper, analysis.effective_evidence_upper, rel_tol=1e-12, abs_tol=1e-12
    ):
        raise RuntimeError("best_effective_partition does not reproduce its endpoint")

    if analysis.joint_partition_pass_exists:
        verify_partition(analysis.joint_passing_partition, "joint_passing_partition")
        joint_flow = replay_flow(analysis.joint_passing_partition)
        joint_effective_upper = replay_effective(analysis.joint_passing_partition, use_upper=False)
        joint_lineage_count = len(
            {analysis.joint_passing_partition[paper_id] for paper_id in counted_papers}
        )
        if not (
            joint_lineage_count >= blueprint.minimum_independent_lineages
            and joint_effective_upper + 1e-9
            >= effective_evidence_ratio * blueprint.minimum_independent_lineages
            and (not require_facet_flow or _flow_is_complete(joint_flow))
        ):
            raise RuntimeError("joint_passing_partition does not reproduce a joint pass")


class _SearchState:
    def __init__(
        self,
        *,
        candidates: list[TraceCandidate],
        blueprint: EvidenceBlueprint,
        members_by_root: dict[str, list[str]],
        cannot_pairs: set[tuple[str, str]],
        lineage_capacity: float,
        facet_threshold: float,
        effective_evidence_ratio: float,
        require_facet_flow: bool,
        max_search_states: int,
        counted_paper_ids: set[str],
        paper_weights: dict[str, float] | None,
        relation_by_pair: dict[tuple[str, str], LineageRelationLike],
        initial_joint_pass: bool,
        initial_joint_partition: dict[str, str],
        fine_signature: tuple[tuple[str, ...], ...],
        fine_flow: EvidenceFlowResult | None,
    ) -> None:
        self.candidates = candidates
        self.blueprint = blueprint
        self.members_by_root = members_by_root
        self.cannot_pairs = cannot_pairs
        self.lineage_capacity = lineage_capacity
        self.facet_threshold = facet_threshold
        self.effective_evidence_ratio = effective_evidence_ratio
        self.require_facet_flow = require_facet_flow
        self.max_search_states = max_search_states
        self.counted_paper_ids = counted_paper_ids
        self.paper_weights = paper_weights
        self.relation_by_pair = relation_by_pair
        self.search_states = 0
        self.leaf_partitions = 0
        self.evaluated_partitions = 0
        self.evaluated_effective_partitions = 0
        self.evaluated_joint_partitions = 1 if paper_weights is not None else 0
        self.aborted = False
        self.lineage_count_lower: int | None = None
        self.lineage_count_partition: dict[str, str] = {}
        self.worst_flow: EvidenceFlowResult | None = None
        self.worst_partition: dict[str, str] = {}
        self.effective_lower: float | None = None
        self.effective_upper: float | None = None
        self.worst_effective_partition: dict[str, str] = {}
        self.best_effective_partition: dict[str, str] = {}
        self.joint_partition_pass_exists = initial_joint_pass
        self.joint_passing_partition = dict(initial_joint_partition)
        self.joint_checked_signatures = {fine_signature} if paper_weights is not None else set()
        self.flow_cache = {fine_signature: fine_flow} if fine_flow is not None else {}
        self.worst_key: tuple[float, float, float, tuple[tuple[str, ...], ...]] | None = None


def _search_partitions(
    roots: list[str],
    index: int,
    groups: list[list[str]],
    state: _SearchState,
) -> None:
    if state.aborted:
        return
    state.search_states += 1
    if state.search_states > state.max_search_states:
        state.aborted = True
        return
    if index == len(roots):
        state.leaf_partitions += 1
        signature, unit_by_paper = _partition_mapping(groups, state.members_by_root)
        effective_upper = None
        if state.paper_weights is not None:
            _, effective_upper = _evaluate_effective_partition(unit_by_paper, state)
        joint_flow = None
        if effective_upper is not None and not state.joint_partition_pass_exists:
            joint_flow = _evaluate_joint_partition(
                signature,
                unit_by_paper,
                effective_upper,
                state,
            )
        if _has_mergeable_groups(groups, state.cannot_pairs):
            return
        _evaluate_flow_partition(signature, unit_by_paper, state, flow=joint_flow)
        return

    root = roots[index]
    for group in groups:
        if _can_join(root, group, state.cannot_pairs):
            group.append(root)
            _search_partitions(roots, index + 1, groups, state)
            group.pop()
            if state.aborted:
                return
    groups.append([root])
    _search_partitions(roots, index + 1, groups, state)
    groups.pop()


def _partition_mapping(
    groups: list[list[str]],
    members_by_root: dict[str, list[str]],
) -> tuple[tuple[tuple[str, ...], ...], dict[str, str]]:
    signature = tuple(sorted(tuple(sorted(group)) for group in groups))
    unit_by_paper: dict[str, str] = {}
    for group in signature:
        label = f"EXACT:{min(group)}"
        for root in group:
            for paper_id in members_by_root[root]:
                unit_by_paper[paper_id] = label
    return signature, unit_by_paper


def _evaluate_flow_partition(
    signature: tuple[tuple[str, ...], ...],
    unit_by_paper: dict[str, str],
    state: _SearchState,
    *,
    flow: EvidenceFlowResult | None = None,
) -> None:
    flow = flow or state.flow_cache.get(signature)
    if flow is None:
        flow = compute_evidence_flow(
            state.candidates,
            state.blueprint,
            lineage_capacity=state.lineage_capacity,
            facet_threshold=state.facet_threshold,
            unit_by_paper=unit_by_paper,
        )
    state.evaluated_partitions += 1
    active_lineage_count = len({unit_by_paper[paper_id] for paper_id in state.counted_paper_ids})
    if state.lineage_count_lower is None or active_lineage_count < state.lineage_count_lower:
        state.lineage_count_lower = active_lineage_count
        state.lineage_count_partition = dict(unit_by_paper)



    key = (
        flow.bottleneck_coverage,
        flow.normalized_coverage,
        flow.total_flow,
        signature,
    )
    if state.worst_key is None or key < state.worst_key:
        state.worst_key = key
        state.worst_flow = flow
        state.worst_partition = unit_by_paper


def _evaluate_effective_partition(
    unit_by_paper: dict[str, str],
    state: _SearchState,
) -> tuple[float | None, float | None]:
    if state.paper_weights is None:
        return None, None
    lower = _effective_evidence_for_partition(
        state.paper_weights,
        unit_by_paper,
        state.relation_by_pair,
        use_upper=True,
    )
    upper = _effective_evidence_for_partition(
        state.paper_weights,
        unit_by_paper,
        state.relation_by_pair,
        use_upper=False,
    )
    state.evaluated_effective_partitions += 1
    if state.effective_lower is None or lower < state.effective_lower:
        state.effective_lower = lower
        state.worst_effective_partition = dict(unit_by_paper)
    if state.effective_upper is None or upper > state.effective_upper:
        state.effective_upper = upper
        state.best_effective_partition = dict(unit_by_paper)
    return lower, upper


def _evaluate_joint_partition(
    signature: tuple[tuple[str, ...], ...],
    unit_by_paper: dict[str, str],
    effective_upper: float,
    state: _SearchState,
) -> EvidenceFlowResult | None:
    if signature in state.joint_checked_signatures:
        return state.flow_cache.get(signature)
    active_lineage_count = len({unit_by_paper[paper_id] for paper_id in state.counted_paper_ids})
    required_lineages = state.blueprint.minimum_independent_lineages
    if (
        active_lineage_count < required_lineages
        or effective_upper + 1e-9 < state.effective_evidence_ratio * required_lineages
    ):
        return None
    flow = compute_evidence_flow(
        state.candidates,
        state.blueprint,
        lineage_capacity=state.lineage_capacity,
        facet_threshold=state.facet_threshold,
        unit_by_paper=unit_by_paper,
    )
    state.evaluated_joint_partitions += 1
    state.joint_checked_signatures.add(signature)
    state.flow_cache[signature] = flow
    if not state.require_facet_flow or _flow_is_complete(flow):
        state.joint_partition_pass_exists = True
        state.joint_passing_partition = dict(unit_by_paper)
    return flow


def _has_mergeable_groups(
    groups: list[list[str]],
    cannot_pairs: set[tuple[str, str]],
) -> bool:
    return any(
        all(_pair_key(left, right) not in cannot_pairs for left in first for right in second)
        for first_index, first in enumerate(groups)
        for second in groups[first_index + 1 :]
    )


def _effective_evidence_for_partition(
    paper_weights: dict[str, float],
    unit_by_paper: dict[str, str],
    relation_by_pair: dict[tuple[str, str], LineageRelationLike],
    *,
    use_upper: bool,
) -> float:
    papers_by_unit: dict[str, list[str]] = defaultdict(list)
    for paper_id, unit in unit_by_paper.items():
        papers_by_unit[unit].append(paper_id)
    unit_weights = {
        unit: max((paper_weights[paper_id] for paper_id in papers), default=0.0)
        for unit, papers in papers_by_unit.items()
    }
    numerator = sum(unit_weights.values()) ** 2
    denominator = sum(weight * weight for weight in unit_weights.values())
    units = sorted(unit_weights)
    for index, unit_a in enumerate(units):
        for unit_b in units[index + 1 :]:
            endpoints = [
                (
                    relation_by_pair[_pair_key(paper_a, paper_b)].distinct_dependence_upper
                    if use_upper
                    else relation_by_pair[_pair_key(paper_a, paper_b)].distinct_dependence_lower
                )
                for paper_a in papers_by_unit[unit_a]
                for paper_b in papers_by_unit[unit_b]
            ]
            dependence = max(endpoints) if use_upper else min(endpoints)
            denominator += 2.0 * unit_weights[unit_a] * unit_weights[unit_b] * dependence
    if numerator <= 1e-12:
        return 0.0
    substantive_count = sum(weight > 1e-12 for weight in unit_weights.values())
    return min(float(substantive_count), max(0.0, numerator / max(denominator, 1e-12)))


def _can_join(
    root: str,
    group: list[str],
    cannot_pairs: set[tuple[str, str]],
) -> bool:
    return all(_pair_key(root, member) not in cannot_pairs for member in group)


def _pair_key(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left < right else (right, left)


def _validate_relation_inputs(relations: list[LineageRelationLike], paper_ids: set[str]) -> None:
    allowed = {"must_link", "cannot_link", "may_link"}
    for edge in relations:
        if edge.paper_id_a == edge.paper_id_b:
            raise ValueError("a lineage relation must connect two distinct papers")
        unknown = sorted({edge.paper_id_a, edge.paper_id_b} - paper_ids)
        if unknown:
            raise ValueError("lineage relation contains unknown papers: " + ", ".join(unknown))
        if edge.relation not in allowed:
            raise ValueError(f"unknown lineage relation: {edge.relation}")
        lower = float(edge.distinct_dependence_lower)
        upper = float(edge.distinct_dependence_upper)
        if not (math.isfinite(lower) and math.isfinite(upper) and 0.0 <= lower <= upper <= 1.0):
            raise ValueError("lineage dependence bounds must be finite and ordered in [0, 1]")
        if edge.relation == "must_link" and not (
            math.isclose(lower, 1.0) and math.isclose(upper, 1.0)
        ):
            raise ValueError("must_link requires dependence bounds [1, 1]")


def _flow_is_complete(flow: EvidenceFlowResult) -> bool:
    return not flow.unresolved_facets and flow.bottleneck_coverage >= 1.0 - 1e-6


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
