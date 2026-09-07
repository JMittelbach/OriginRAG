from __future__ import annotations

import sqlite3
from pathlib import Path

from pfempire.claims.search import search_claims
from pfempire.claims.store import claim_counts, initialize_claim_store, read_claims, upsert_claims


def test_claim_store_upserts_and_searches_claims(tmp_path: Path) -> None:
    db_path = tmp_path / "claims.sqlite"
    initialize_claim_store(db_path)
    claims = [
        {
            "claim_id": "P1_CLM_001",
            "claim_hash": "hash1",
            "claim_text": "PfEMP1 CIDRalpha1 domains are associated with EPCR binding in severe malaria.",
            "claim_type": "finding",
            "evidence_type": "association",
            "evidence_strength": "moderate",
            "direction": "positive",
            "uncertainty": "medium",
            "source_paper_id": "P1",
            "source_chunk_id": "P1_CHUNK_0001",
            "source_section": "results",
            "source_pages": "2",
            "source_text": "PfEMP1 CIDRalpha1 domains are associated with EPCR binding in severe malaria.",
            "entities": [{"concept_id": "pfemp1", "name": "PfEMP1", "entity_type": "protein"}],
            "claim_quality_flags": ["missing_doi"],
            "citation_short": "Smith 2024",
            "title": "EPCR binding paper",
            "year": "2024",
            "doi": "10.1/test",
            "confidence": 0.8,
        }
    ]

    assert upsert_claims(db_path, claims) == 1
    assert upsert_claims(db_path, claims) == 1

    stored = read_claims(db_path)
    results = search_claims("PfEMP1 EPCR severe malaria", db_path, limit=5)
    counts = claim_counts(db_path)

    assert len(stored) == 1
    assert stored[0]["entities"][0]["concept_id"] == "pfemp1"
    assert stored[0]["claim_quality_flags"] == ["missing_doi"]
    assert results[0]["claim_id"] == "P1_CLM_001"
    assert results[0]["score"] > 0
    assert "pfemp1" in results[0]["matched_query_terms"]
    assert counts["total_claims"] == 1
    assert counts["unique_papers"] == 1
    assert counts["missing_doi_claims"] == 0
    with sqlite3.connect(db_path) as connection:
        claim_entity = connection.execute(
            "SELECT entity_id, role FROM claim_entities WHERE claim_id = ?",
            ("P1_CLM_001",),
        ).fetchone()
    assert claim_entity == ("pfemp1", "parasite_feature")


def test_claim_search_filters_by_doi_and_confidence(tmp_path: Path) -> None:
    db_path = tmp_path / "claims.sqlite"
    initialize_claim_store(db_path)
    upsert_claims(
        db_path,
        [
            {
                "claim_id": "P1_CLM_001",
                "claim_hash": "hash1",
                "claim_text": "PfEMP1 is associated with EPCR binding.",
                "claim_type": "finding",
                "evidence_strength": "moderate",
                "source_paper_id": "P1",
                "source_chunk_id": "C1",
                "doi": "10.1/test",
                "confidence": 0.9,
                "entities": [],
            },
            {
                "claim_id": "P2_CLM_001",
                "claim_hash": "hash2",
                "claim_text": "PfEMP1 is mentioned in background text.",
                "claim_type": "background",
                "evidence_strength": "weak",
                "source_paper_id": "P2",
                "source_chunk_id": "C2",
                "doi": "",
                "confidence": 0.2,
                "entities": [],
            },
        ],
    )

    results = search_claims(
        "PfEMP1 EPCR",
        db_path,
        require_doi=True,
        min_confidence=0.5,
    )

    assert [result["claim_id"] for result in results] == ["P1_CLM_001"]
