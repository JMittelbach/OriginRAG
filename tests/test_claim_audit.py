from __future__ import annotations

from pathlib import Path

from originrag.claims.audit import audit_claim_database, format_claim_audit_markdown
from originrag.claims.store import initialize_claim_store, upsert_claims


def test_empty_claim_audit_reports_no_claims(tmp_path: Path) -> None:
    db_path = tmp_path / "claims.sqlite"
    initialize_claim_store(db_path)

    audit = audit_claim_database(db_path)

    assert audit["total_claims"] == 0
    assert "no_claims_in_database" in audit["warnings"]


def test_claim_audit_reports_quality_rates(tmp_path: Path) -> None:
    db_path = tmp_path / "claims.sqlite"
    initialize_claim_store(db_path)
    upsert_claims(
        db_path,
        [
            {
                "claim_id": "P1_CLM_001",
                "claim_hash": "hash1",
                "claim_text": "PfEMP1 may be associated with EPCR binding.",
                "claim_type": "speculation",
                "evidence_strength": "weak",
                "source_paper_id": "P1",
                "source_chunk_id": "C1",
                "claim_quality_flags": ["missing_doi", "hedged_language"],
                "entities": [],
                "duplicate_count": 2,
            }
        ],
    )

    audit = audit_claim_database(db_path)
    markdown = format_claim_audit_markdown(audit)

    assert audit["total_claims"] == 1
    assert audit["rates"]["missing_doi"] == 1.0
    assert audit["rates"]["no_entities"] == 1.0
    assert "high_missing_doi_rate" in audit["warnings"]
    assert "Claim Database Audit" in markdown
