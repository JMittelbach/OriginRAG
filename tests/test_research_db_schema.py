from __future__ import annotations

import sqlite3

from pfempire.claims.store import initialize_claim_store, upsert_claims
from pfempire.db import initialize_database, list_tables


def test_initialize_research_db_creates_expected_tables(tmp_path) -> None:
    db_path = tmp_path / "pfempire.sqlite"

    tables = initialize_database(db_path)

    assert "papers" in tables
    assert "claims" in tables
    assert "training_examples" in tables
    assert set(tables) == set(list_tables(db_path))


def test_claim_store_upgrades_legacy_claim_table_before_index_creation(tmp_path) -> None:
    db_path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE claims (
                claim_id TEXT PRIMARY KEY,
                claim_text TEXT NOT NULL,
                claim_type TEXT,
                source_paper_id TEXT
            )
            """
        )

    initialize_claim_store(db_path)

    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(claims)")}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(claims)")}
    assert {"claim_hash", "source_chunk_id", "doi", "claim_status"} <= columns
    assert {"idx_claims_hash", "idx_claims_source_chunk", "idx_claims_doi"} <= indexes

    count = upsert_claims(
        db_path,
        [
            {
                "claim_id": "C1",
                "claim_text": "Migrated claim",
                "claim_type": "finding",
                "evidence_type": "observational",
                "source_paper_id": "P1",
                "source_chunk_id": "K1",
            }
        ],
    )

    assert count == 1
