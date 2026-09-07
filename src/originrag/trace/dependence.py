from __future__ import annotations

from originrag.trace.schemas import EvidenceLineageEdge, LineageSignal


DEFAULT_SIGNAL_WEIGHTS = {
    "same_publication_version": 1.00,
    "same_doi_family": 1.00,
    "same_preprint_family": 0.98,
    "same_trial_id": 0.95,
    "same_dataset_accession": 0.90,
    "overlapping_samples": 0.90,
    "same_cohort": 0.85,
    "same_parasite_isolates": 0.85,
    "follow_up_analysis": 0.80,
    "secondary_analysis": 0.80,
    "shared_atlas": 0.70,
    "same_site_and_period": 0.45,
    "high_author_overlap": 0.30,
    "high_title_similarity": 0.25,
    "citation_relationship": 0.05,
    "same_method_only": 0.02,
}


def combine_signal_dependence(
    signals: list[LineageSignal], weights: dict[str, float] | None = None
) -> float:
    weights = weights or DEFAULT_SIGNAL_WEIGHTS
    survival = 1.0
    for signal in signals:
        if signal.status == "rejected":
            continue
        theta = min(1.0, max(0.0, float(weights.get(signal.signal_type, 0.0))))
        survival *= 1.0 - theta * signal.signal_strength
    return min(1.0, max(0.0, 1.0 - survival))


def build_edge(
    paper_id_a: str,
    paper_id_b: str,
    signals: list[LineageSignal],
    weights: dict[str, float] | None = None,
) -> EvidenceLineageEdge:
    score = 1.0 if paper_id_a == paper_id_b else combine_signal_dependence(signals, weights)
    relationships = sorted({signal.signal_type for signal in signals if signal.status != "rejected"})
    hard = any(
        kind in {"same_publication_version", "same_doi_family", "same_preprint_family"}
        for kind in relationships
    )
    verified = any(signal.status == "verified" for signal in signals)
    explanation = "; ".join(
        f"{signal.signal_type}={signal.signal_strength:.2f}" for signal in signals if signal.status != "rejected"
    ) or "no dependence signal"
    return EvidenceLineageEdge(
        paper_id_a=paper_id_a,
        paper_id_b=paper_id_b,
        dependence_score=score,
        relationship_types=relationships,
        signals=signals,
        is_hard_duplicate=hard,
        is_verified=verified,
        explanation=explanation,
    )


def dependence_lookup(edges: list[EvidenceLineageEdge]) -> dict[tuple[str, str], float]:
    lookup: dict[tuple[str, str], float] = {}
    for edge in edges:
        value = min(1.0, max(0.0, edge.dependence_score))
        pair = tuple(sorted((edge.paper_id_a, edge.paper_id_b)))
        previous = lookup.get(pair)
        if previous is not None and abs(previous - value) > 1e-9:
            raise ValueError(
                f"conflicting duplicate dependence edge for {pair}: {previous} versus {value}"
            )
        lookup[pair] = value
        lookup[(pair[1], pair[0])] = value
    return lookup


def dependence_between(a: str, b: str, lookup: dict[tuple[str, str], float]) -> float:
    if a == b:
        return 1.0
    value = max(float(lookup.get((a, b), 0.0)), float(lookup.get((b, a), 0.0)))
    return min(1.0, max(0.0, value))
