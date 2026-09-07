from __future__ import annotations

from collections import defaultdict

from originrag.trace.schemas import TraceCandidate


def paper_independence_units(candidates: list[TraceCandidate]) -> dict[str, str]:
    """Group papers sharing a study, dataset, or publication resource.

    The grouping is deliberately conservative: reuse along any known resource
    means the papers cannot certify independent replication on their own.
    """

    paper_ids = sorted({candidate.paper_id for candidate in candidates})
    parent = {paper_id: paper_id for paper_id in paper_ids}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    resource_owner: dict[tuple[str, str], str] = {}
    for candidate in candidates:
        resources = known_resources(candidate)
        for resource in resources:
            owner = resource_owner.setdefault(resource, candidate.paper_id)
            union(owner, candidate.paper_id)

    members: dict[str, list[str]] = defaultdict(list)
    for paper_id in paper_ids:
        members[find(paper_id)].append(paper_id)
    candidates_by_paper: dict[str, list[TraceCandidate]] = defaultdict(list)
    for candidate in candidates:
        candidates_by_paper[candidate.paper_id].append(candidate)

    labels = {
        root: component_label(
            papers,
            [candidate for paper in papers for candidate in candidates_by_paper[paper]],
        )
        for root, papers in members.items()
    }
    return {paper_id: labels[find(paper_id)] for paper_id in paper_ids}


def known_resources(candidate: TraceCandidate) -> set[tuple[str, str]]:
    resources = {("paper", candidate.paper_id)}
    study = str(candidate.study_lineage_id or "").strip()
    dataset = str(candidate.dataset_lineage_id or "").strip()
    family = str(candidate.publication_family_id or "").strip()
    if study:
        resources.add(("study", study))
    if dataset:
        resources.add(("dataset", dataset))
    if family:
        resources.add(("family", family))
    return resources


def component_label(
    papers: list[str], candidates: list[TraceCandidate]
) -> str:
    studies = sorted(
        {
            str(candidate.study_lineage_id)
            for candidate in candidates
            if candidate.study_lineage_id
            and str(candidate.study_lineage_id) != candidate.paper_id
        }
    )
    datasets = sorted(
        {
            str(candidate.dataset_lineage_id)
            for candidate in candidates
            if candidate.dataset_lineage_id
        }
    )
    families = sorted(
        {
            str(candidate.publication_family_id)
            for candidate in candidates
            if candidate.publication_family_id
            and str(candidate.publication_family_id) not in papers
        }
    )
    if len(studies) == 1:
        return studies[0]
    if datasets:
        return f"DATASET:{datasets[0]}"
    if families:
        return f"FAMILY:{families[0]}"
    return min(papers)
