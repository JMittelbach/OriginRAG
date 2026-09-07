from __future__ import annotations

from collections import Counter
from pathlib import Path

from pfempire.claims.store import DEFAULT_DB_PATH, read_claims


def audit_claim_database(db_path: Path = DEFAULT_DB_PATH) -> dict:
    claims = read_claims(db_path)
    total = len(claims)
    if not claims:
        return {
            "total_claims": 0,
            "unique_papers": 0,
            "rates": {},
            "counts": {},
            "warnings": ["no_claims_in_database"],
            "recommendations": ["Build the claim database after chunk generation."],
        }

    counts = {
        "missing_doi": count_if(claims, lambda claim: not str(claim.get("doi") or "").strip()),
        "no_entities": count_if(claims, lambda claim: not claim.get("entities")),
        "weak_or_unclear": count_if(
            claims,
            lambda claim: str(claim.get("evidence_strength") or "") in {"weak", "unclear", ""},
        ),
        "hedged": count_if(
            claims,
            lambda claim: "hedged_language" in set(claim.get("claim_quality_flags") or []),
        ),
        "duplicate_sentences": count_if(
            claims,
            lambda claim: int(claim.get("duplicate_count") or 1) > 1,
        ),
        "unknown_section": count_if(
            claims,
            lambda claim: str(claim.get("source_section") or "") == "unknown",
        ),
        "reviewed": count_if(
            claims,
            lambda claim: str(claim.get("claim_status") or "").startswith("reviewed_"),
        ),
    }
    rates = {key: round(value / total, 3) for key, value in counts.items()}
    warnings = audit_warnings(rates=rates, total=total)
    return {
        "total_claims": total,
        "unique_papers": len({claim.get("source_paper_id") for claim in claims if claim.get("source_paper_id")}),
        "rates": rates,
        "counts": counts,
        "by_claim_type": dict(Counter(str(claim.get("claim_type") or "unknown") for claim in claims)),
        "by_evidence_strength": dict(Counter(str(claim.get("evidence_strength") or "unknown") for claim in claims)),
        "top_quality_flags": dict(
            Counter(
                flag
                for claim in claims
                for flag in list(claim.get("claim_quality_flags") or [])
            ).most_common(20)
        ),
        "warnings": warnings,
        "recommendations": audit_recommendations(warnings),
    }


def audit_warnings(*, rates: dict[str, float], total: int) -> list[str]:
    warnings = []
    if total < 100:
        warnings.append("small_claim_database")
    if rates.get("missing_doi", 0.0) > 0.1:
        warnings.append("high_missing_doi_rate")
    if rates.get("no_entities", 0.0) > 0.2:
        warnings.append("high_no_entity_rate")
    if rates.get("weak_or_unclear", 0.0) > 0.5:
        warnings.append("weak_evidence_strength_mix")
    if rates.get("duplicate_sentences", 0.0) > 0.2:
        warnings.append("high_duplicate_sentence_rate")
    if rates.get("unknown_section", 0.0) > 0.2:
        warnings.append("high_unknown_section_rate")
    if rates.get("reviewed", 0.0) < 0.05:
        warnings.append("low_reviewed_claim_rate")
    return warnings or ["claim_database_passes_current_audit_thresholds"]


def audit_recommendations(warnings: list[str]) -> list[str]:
    mapping = {
        "no_claims_in_database": "Build chunks and run scripts/build_claim_database.py.",
        "small_claim_database": "Treat evaluation as a smoke test until more active papers are chunked.",
        "high_missing_doi_rate": "Refresh paper metadata before using citation-sensitive answers.",
        "high_no_entity_rate": "Expand concept lexicons or lower extraction confidence for generic claims.",
        "weak_evidence_strength_mix": "Prefer Results/Abstract claims and review hedged Discussion claims.",
        "high_duplicate_sentence_rate": "Check chunk overlap and consider lowering overlap or dedupe by paper.",
        "high_unknown_section_rate": "Run or improve section annotation before claim extraction.",
        "low_reviewed_claim_rate": "Export a review set and import labels before judging model quality.",
    }
    return [mapping[warning] for warning in warnings if warning in mapping] or [
        "Proceed with claim search and hypothesis evaluation."
    ]


def format_claim_audit_markdown(audit: dict) -> str:
    lines = [
        "# Claim Database Audit",
        "",
        f"- Total claims: {audit.get('total_claims', 0)}",
        f"- Unique papers: {audit.get('unique_papers', 0)}",
        "",
        "## Rates",
        "",
    ]
    for key, value in sorted(dict(audit.get("rates") or {}).items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {warning}" for warning in audit.get("warnings", []))
    lines.extend(["", "## Recommendations", ""])
    lines.extend(f"- {item}" for item in audit.get("recommendations", []))
    lines.extend(["", "## Claim Types", ""])
    lines.extend(
        f"- {key}: {value}"
        for key, value in sorted(dict(audit.get("by_claim_type") or {}).items())
    )
    lines.extend(["", "## Evidence Strength", ""])
    lines.extend(
        f"- {key}: {value}"
        for key, value in sorted(dict(audit.get("by_evidence_strength") or {}).items())
    )
    lines.extend(["", "## Quality Flags", ""])
    lines.extend(
        f"- {key}: {value}"
        for key, value in sorted(dict(audit.get("top_quality_flags") or {}).items())
    )
    lines.append("")
    return "\n".join(lines)


def count_if(claims: list[dict], predicate) -> int:
    return sum(1 for claim in claims if predicate(claim))
