from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from originrag.db.database import DEFAULT_DB_PATH, initialize_database
from originrag.retrieval.keyword import normalize_text


CLAIM_EXTRA_COLUMNS = {
    "claim_text": "TEXT",
    "claim_type": "TEXT",
    "evidence_type": "TEXT",
    "evidence_strength": "TEXT",
    "direction": "TEXT",
    "uncertainty": "TEXT",
    "claim_hash": "TEXT",
    "organism": "TEXT",
    "pathogen": "TEXT",
    "host_system": "TEXT",
    "disease_context": "TEXT",
    "phenotype": "TEXT",
    "parasite_feature": "TEXT",
    "host_feature": "TEXT",
    "method": "TEXT",
    "dataset_or_cohort": "TEXT",
    "source_paper_id": "TEXT",
    "source_chunk_id": "TEXT",
    "source_section": "TEXT",
    "source_pages": "TEXT",
    "source_text": "TEXT",
    "entities_json": "TEXT",
    "entity_ids": "TEXT",
    "claim_quality_flags": "TEXT",
    "citation_short": "TEXT",
    "title": "TEXT",
    "year": "TEXT",
    "doi": "TEXT",
    "confidence": "REAL",
    "duplicate_count": "INTEGER DEFAULT 1",
    "claim_status": "TEXT DEFAULT 'auto_extracted'",
}

CLAIM_INSERT_FIELDS = [
    "claim_id",
    "claim_hash",
    "claim_text",
    "claim_type",
    "evidence_type",
    "evidence_strength",
    "direction",
    "uncertainty",
    "organism",
    "pathogen",
    "host_system",
    "disease_context",
    "phenotype",
    "parasite_feature",
    "host_feature",
    "method",
    "dataset_or_cohort",
    "source_paper_id",
    "source_chunk_id",
    "source_section",
    "source_pages",
    "source_text",
    "entities_json",
    "entity_ids",
    "claim_quality_flags",
    "citation_short",
    "title",
    "year",
    "doi",
    "confidence",
    "duplicate_count",
    "claim_status",
]


def initialize_claim_store(db_path: Path = DEFAULT_DB_PATH) -> list[str]:
    tables = initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        ensure_claim_schema(connection)
        connection.commit()
    return tables


def ensure_claim_schema(connection: sqlite3.Connection) -> None:
    existing_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(claims)").fetchall()
    }
    for column, column_type in CLAIM_EXTRA_COLUMNS.items():
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE claims ADD COLUMN {column} {column_type}")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_claims_hash ON claims (claim_hash)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_claims_source_chunk ON claims (source_chunk_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_claims_doi ON claims (doi)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_claims_status ON claims (claim_status)")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS claim_entities (
            claim_id TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            entity_name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            role TEXT,
            matched_text TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (claim_id, entity_id),
            FOREIGN KEY (claim_id) REFERENCES claims (claim_id)
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_claim_entities_entity ON claim_entities (entity_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_claim_entities_type ON claim_entities (entity_type)")


def upsert_claims(db_path: Path, claims: Iterable[dict]) -> int:
    prepared = [prepare_claim_for_db(claim) for claim in claims]
    if not prepared:
        initialize_claim_store(db_path)
        return 0
    placeholders = ", ".join("?" for _ in CLAIM_INSERT_FIELDS)
    assignments = ", ".join(
        f"{field}=excluded.{field}" for field in CLAIM_INSERT_FIELDS if field != "claim_id"
    )
    sql = (
        f"INSERT INTO claims ({', '.join(CLAIM_INSERT_FIELDS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(claim_id) DO UPDATE SET {assignments}"
    )
    initialize_claim_store(db_path)
    with sqlite3.connect(db_path) as connection:
        ensure_claim_schema(connection)
        connection.executemany(sql, [[claim.get(field) for field in CLAIM_INSERT_FIELDS] for claim in prepared])
        upsert_claim_entities(connection, prepared)
        connection.commit()
    return len(prepared)


def read_claims(db_path: Path = DEFAULT_DB_PATH, *, limit: int | None = None) -> list[dict]:
    initialize_claim_store(db_path)
    sql = "SELECT * FROM claims ORDER BY source_paper_id, source_chunk_id, claim_id"
    params: list[object] = []
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        ensure_claim_schema(connection)
        rows = connection.execute(sql, params).fetchall()
    return [row_to_claim(dict(row)) for row in rows]


def claim_counts(db_path: Path = DEFAULT_DB_PATH) -> dict[str, object]:
    initialize_claim_store(db_path)
    with sqlite3.connect(db_path) as connection:
        ensure_claim_schema(connection)
        total = connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        papers = connection.execute("SELECT COUNT(DISTINCT source_paper_id) FROM claims").fetchone()[0]
        by_type = {
            row[0]: row[1]
            for row in connection.execute("SELECT claim_type, COUNT(*) FROM claims GROUP BY claim_type ORDER BY claim_type").fetchall()
        }
        by_strength = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT evidence_strength, COUNT(*) FROM claims GROUP BY evidence_strength ORDER BY evidence_strength"
            ).fetchall()
        }
        missing_doi = connection.execute(
            "SELECT COUNT(*) FROM claims WHERE doi IS NULL OR TRIM(doi) = ''"
        ).fetchone()[0]
        no_entities = connection.execute(
            "SELECT COUNT(*) FROM claims WHERE entity_ids IS NULL OR TRIM(entity_ids) = ''"
        ).fetchone()[0]
        reviewed = connection.execute(
            "SELECT COUNT(*) FROM claims WHERE claim_status LIKE 'reviewed_%'"
        ).fetchone()[0]
    return {
        "total_claims": total,
        "unique_papers": papers,
        "by_claim_type": by_type,
        "by_evidence_strength": by_strength,
        "missing_doi_claims": missing_doi,
        "no_entity_claims": no_entities,
        "reviewed_claims": reviewed,
    }


def prepare_claim_for_db(claim: dict) -> dict:
    prepared = dict(claim)
    prepared["_entities"] = list(prepared.get("entities") or [])
    prepared["entities_json"] = json.dumps(prepared.pop("entities", []), ensure_ascii=False)
    prepared["claim_quality_flags"] = json.dumps(prepared.get("claim_quality_flags") or [], ensure_ascii=False)
    prepared.setdefault("entity_ids", ";".join(entity_id for entity_id in entity_ids(prepared["_entities"])))
    prepared.setdefault("duplicate_count", 1)
    prepared.setdefault("claim_status", "auto_extracted")
    for field in CLAIM_INSERT_FIELDS:
        prepared.setdefault(field, None)
    return prepared


def row_to_claim(row: dict) -> dict:
    claim = dict(row)
    entities_json = claim.pop("entities_json", None)
    try:
        claim["entities"] = json.loads(entities_json) if entities_json else []
    except json.JSONDecodeError:
        claim["entities"] = []
    flags_json = claim.get("claim_quality_flags")
    try:
        claim["claim_quality_flags"] = json.loads(flags_json) if flags_json else []
    except json.JSONDecodeError:
        claim["claim_quality_flags"] = []
    return claim


def upsert_claim_entities(connection: sqlite3.Connection, claims: list[dict]) -> None:
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        if not claim_id:
            continue
        entities = list(claim.get("_entities") or [])
        connection.execute("DELETE FROM claim_entities WHERE claim_id = ?", (claim_id,))
        for entity in entities:
            entity_id = str(entity.get("concept_id") or "").strip()
            entity_name = str(entity.get("name") or "").strip()
            entity_type = str(entity.get("entity_type") or "").strip()
            if not entity_id or not entity_name or not entity_type:
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO entities
                (entity_id, name, normalized_name, entity_type, aliases, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entity_id,
                    entity_name,
                    normalize_text(entity_name),
                    entity_type,
                    "",
                    "claim_extraction",
                ),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO claim_entities
                (claim_id, entity_id, entity_name, entity_type, role, matched_text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    claim_id,
                    entity_id,
                    entity_name,
                    entity_type,
                    entity_role(entity_type),
                    str(entity.get("matched_text") or ""),
                ),
            )


def entity_role(entity_type: str) -> str:
    if entity_type in {"gene", "protein", "domain"}:
        return "parasite_feature"
    if entity_type in {"receptor", "cell_type", "immune_state", "pathway"}:
        return "host_feature"
    if entity_type in {"disease", "phenotype"}:
        return "context"
    if entity_type in {"method", "assay", "tool", "dataset"}:
        return "method_or_dataset"
    return "entity"


def entity_ids(entities: list[dict]) -> list[str]:
    seen = set()
    ids = []
    for entity in entities:
        entity_id = str(entity.get("concept_id") or "").strip()
        if not entity_id or entity_id in seen:
            continue
        seen.add(entity_id)
        ids.append(entity_id)
    return ids
