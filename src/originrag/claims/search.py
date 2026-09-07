from __future__ import annotations

from pathlib import Path

from originrag.claims.store import DEFAULT_DB_PATH, read_claims
from originrag.retrieval.keyword import normalize_text, query_tokens


def search_claims(
    query: str,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    limit: int = 20,
    claim_type: str | None = None,
    evidence_strength: str | None = None,
    require_doi: bool = False,
    min_confidence: float = 0.0,
    claim_status: str | None = None,
    source_paper_id: str | None = None,
) -> list[dict]:
    tokens = query_tokens(query)
    normalized_query = normalize_text(query)
    if not tokens and not normalized_query:
        return []

    scored = []
    for claim in read_claims(db_path):
        if claim_type and str(claim.get("claim_type") or "") != claim_type:
            continue
        if evidence_strength and str(claim.get("evidence_strength") or "") != evidence_strength:
            continue
        if require_doi and not str(claim.get("doi") or "").strip():
            continue
        if min_confidence and float(claim.get("confidence") or 0.0) < min_confidence:
            continue
        if claim_status and str(claim.get("claim_status") or "") != claim_status:
            continue
        if source_paper_id and str(claim.get("source_paper_id") or "") != source_paper_id:
            continue
        score = score_claim(query=normalized_query, tokens=tokens, claim=claim)
        if score <= 0:
            continue
        ranked = dict(claim)
        ranked["score"] = round(score, 3)
        ranked["matched_query_terms"] = matched_query_terms(tokens, claim)
        scored.append(ranked)

    scored.sort(
        key=lambda item: (
            -float(item["score"]),
            -float(item.get("confidence") or 0.0),
            str(item.get("source_paper_id") or ""),
            str(item.get("claim_id") or ""),
        )
    )
    return scored[: max(0, limit)]


def score_claim(*, query: str, tokens: list[str], claim: dict) -> float:
    core_fields = [
        "claim_text",
        "claim_type",
        "evidence_type",
        "evidence_strength",
        "direction",
        "organism",
        "pathogen",
        "host_system",
        "disease_context",
        "phenotype",
        "parasite_feature",
        "host_feature",
        "method",
        "dataset_or_cohort",
    ]
    weak_fields = [
        "title",
        "citation_short",
        "doi",
    ]
    core_haystack = normalize_text(" ".join(str(claim.get(field) or "") for field in core_fields))
    weak_haystack = normalize_text(" ".join(str(claim.get(field) or "") for field in weak_fields))
    haystack = " ".join([core_haystack, weak_haystack]).strip()
    if not haystack:
        return 0.0
    score = 0.0
    if query and query in core_haystack:
        score += 12.0
    elif query and query in haystack:
        score += 6.0
    for token in tokens:
        if token in core_haystack:
            score += 1.8
        elif token in weak_haystack:
            score += 0.6
    for entity in claim.get("entities") or []:
        entity_name = normalize_text(str(entity.get("name") or ""))
        if entity_name and any(token in entity_name or entity_name in token for token in tokens):
            score += 2.2
    strength = str(claim.get("evidence_strength") or "")
    if strength == "strong":
        score += 1.2
    elif strength == "moderate":
        score += 0.6
    if str(claim.get("claim_type") or "") in {"finding", "negative_result"}:
        score += 0.5
    if claim.get("doi"):
        score += 0.2
    score -= 0.15 * len(claim.get("claim_quality_flags") or [])
    return score


def matched_query_terms(tokens: list[str], claim: dict) -> list[str]:
    haystack = normalize_text(
        " ".join(
            str(claim.get(field) or "")
            for field in [
                "claim_text",
                "claim_type",
                "evidence_type",
                "disease_context",
                "phenotype",
                "parasite_feature",
                "host_feature",
                "method",
                "dataset_or_cohort",
                "title",
            ]
        )
    )
    matched = []
    for token in tokens:
        if token in haystack and token not in matched:
            matched.append(token)
    return matched
