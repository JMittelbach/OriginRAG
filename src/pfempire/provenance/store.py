from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pfempire.db.database import DEFAULT_DB_PATH, initialize_database
from pfempire.provenance.library import normalize_doi, normalize_pmcid, normalize_pmid


PROVENANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS cited_works (
    work_id TEXT PRIMARY KEY,
    doi TEXT,
    pmid TEXT,
    pmcid TEXT,
    title TEXT,
    author TEXT,
    year TEXT,
    journal TEXT,
    citation_text TEXT NOT NULL,
    registry_paper_id TEXT,
    bibliographic_resolution_status TEXT NOT NULL,
    metadata_source TEXT,
    oa_pdf_url TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS citation_mentions (
    mention_id TEXT PRIMARY KEY,
    citing_paper_id TEXT NOT NULL,
    source_chunk_id TEXT,
    cited_work_id TEXT NOT NULL,
    reference_index INTEGER,
    marker_text TEXT,
    printed_reference TEXT,
    context_text TEXT NOT NULL,
    source_page INTEGER,
    source_rect_json TEXT,
    citation_link_status TEXT NOT NULL,
    scope_status TEXT NOT NULL,
    bibliographic_resolution_status TEXT NOT NULL DEFAULT 'unresolved',
    parser_version TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cited_work_id) REFERENCES cited_works (work_id)
);

CREATE TABLE IF NOT EXISTS claim_sources (
    claim_id TEXT NOT NULL,
    mention_id TEXT NOT NULL,
    cited_work_id TEXT NOT NULL,
    source_role TEXT NOT NULL,
    support_relation TEXT NOT NULL,
    origin_review_status TEXT NOT NULL,
    confidence REAL,
    reviewer TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (claim_id, mention_id, cited_work_id),
    FOREIGN KEY (claim_id) REFERENCES claims (claim_id),
    FOREIGN KEY (mention_id) REFERENCES citation_mentions (mention_id),
    FOREIGN KEY (cited_work_id) REFERENCES cited_works (work_id)
);

CREATE TABLE IF NOT EXISTS scientific_origins (
    origin_id TEXT PRIMARY KEY,
    origin_type TEXT NOT NULL,
    label TEXT NOT NULL,
    accession TEXT,
    review_status TEXT NOT NULL DEFAULT 'review_required',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS work_origins (
    work_id TEXT NOT NULL,
    origin_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    evidence_locator TEXT,
    review_status TEXT NOT NULL DEFAULT 'review_required',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (work_id, origin_id, relation_type),
    FOREIGN KEY (work_id) REFERENCES cited_works (work_id),
    FOREIGN KEY (origin_id) REFERENCES scientific_origins (origin_id)
);

CREATE INDEX IF NOT EXISTS idx_cited_works_doi ON cited_works (doi);
CREATE INDEX IF NOT EXISTS idx_cited_works_pmid ON cited_works (pmid);
CREATE INDEX IF NOT EXISTS idx_cited_works_pmcid ON cited_works (pmcid);
CREATE INDEX IF NOT EXISTS idx_citation_mentions_paper ON citation_mentions (citing_paper_id);
CREATE INDEX IF NOT EXISTS idx_citation_mentions_work ON citation_mentions (cited_work_id);
CREATE INDEX IF NOT EXISTS idx_claim_sources_claim ON claim_sources (claim_id);
CREATE INDEX IF NOT EXISTS idx_work_origins_origin ON work_origins (origin_id);
"""


def initialize_provenance_store(db_path: Path = DEFAULT_DB_PATH) -> None:
    db_path = Path(db_path)
    if not db_path.exists():
        initialize_database(db_path)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(PROVENANCE_SCHEMA)
        mention_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(citation_mentions)")
        }
        if "printed_reference" not in mention_columns:
            connection.execute("ALTER TABLE citation_mentions ADD COLUMN printed_reference TEXT")
        if "bibliographic_resolution_status" not in mention_columns:
            connection.execute(
                "ALTER TABLE citation_mentions ADD COLUMN "
                "bibliographic_resolution_status TEXT NOT NULL DEFAULT 'unresolved'"
            )
        connection.commit()


def upsert_source_trace(
    trace: dict[str, object],
    db_path: Path = DEFAULT_DB_PATH,
    *,
    claim_id: str = "",
    source_chunk_id: str = "",
) -> dict[str, int]:
    initialize_provenance_store(db_path)
    work_count = 0
    mention_count = 0
    claim_edge_count = 0
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for mention in trace.get("mentions", []):
            if not isinstance(mention, dict):
                continue
            reference = mention.get("reference") or {}
            if not isinstance(reference, dict):
                continue
            work_id = str(reference.get("work_id") or "").strip()
            mention_id = str(mention.get("mention_id") or "").strip()
            if not work_id or not mention_id:
                continue
            prepared_work = _prepare_cited_work(connection, work_id, reference)
            connection.execute(
                """
                INSERT INTO cited_works (
                    work_id, doi, pmid, pmcid, title, author, year, journal,
                    citation_text, registry_paper_id, bibliographic_resolution_status,
                    metadata_source, oa_pdf_url, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(work_id) DO UPDATE SET
                    doi=COALESCE(NULLIF(TRIM(excluded.doi), ''), cited_works.doi),
                    pmid=COALESCE(NULLIF(TRIM(excluded.pmid), ''), cited_works.pmid),
                    pmcid=COALESCE(NULLIF(TRIM(excluded.pmcid), ''), cited_works.pmcid),
                    title=COALESCE(NULLIF(TRIM(excluded.title), ''), cited_works.title),
                    author=COALESCE(NULLIF(TRIM(excluded.author), ''), cited_works.author),
                    year=COALESCE(NULLIF(TRIM(excluded.year), ''), cited_works.year),
                    journal=COALESCE(NULLIF(TRIM(excluded.journal), ''), cited_works.journal),
                    citation_text=COALESCE(
                        NULLIF(TRIM(excluded.citation_text), ''),
                        cited_works.citation_text
                    ),
                    registry_paper_id=COALESCE(
                        NULLIF(TRIM(excluded.registry_paper_id), ''),
                        cited_works.registry_paper_id
                    ),
                    bibliographic_resolution_status=excluded.bibliographic_resolution_status,
                    metadata_source=COALESCE(
                        NULLIF(TRIM(excluded.metadata_source), ''),
                        cited_works.metadata_source
                    ),
                    oa_pdf_url=COALESCE(
                        NULLIF(TRIM(excluded.oa_pdf_url), ''),
                        cited_works.oa_pdf_url
                    ),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    work_id,
                    prepared_work["doi"],
                    prepared_work["pmid"],
                    prepared_work["pmcid"],
                    prepared_work["title"],
                    prepared_work["author"],
                    prepared_work["year"],
                    prepared_work["journal"],
                    prepared_work["citation_text"],
                    prepared_work["registry_paper_id"],
                    prepared_work["bibliographic_resolution_status"],
                    prepared_work["metadata_source"],
                    prepared_work["oa_pdf_url"],
                ),
            )
            work_count += 1
            previous_work_id = _previous_mention_work_id(connection, mention_id)
            connection.execute(
                """
                INSERT INTO citation_mentions (
                    mention_id, citing_paper_id, source_chunk_id, cited_work_id,
                    reference_index, marker_text, printed_reference, context_text, source_page,
                    source_rect_json, citation_link_status, scope_status,
                    bibliographic_resolution_status, parser_version, source_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mention_id) DO UPDATE SET
                    source_chunk_id=COALESCE(
                        NULLIF(TRIM(excluded.source_chunk_id), ''),
                        citation_mentions.source_chunk_id
                    ),
                    cited_work_id=excluded.cited_work_id,
                    marker_text=excluded.marker_text,
                    printed_reference=excluded.printed_reference,
                    context_text=excluded.context_text,
                    source_rect_json=excluded.source_rect_json,
                    citation_link_status=excluded.citation_link_status,
                    scope_status=excluded.scope_status,
                    bibliographic_resolution_status=excluded.bibliographic_resolution_status,
                    parser_version=excluded.parser_version,
                    source_sha256=excluded.source_sha256
                """,
                (
                    mention_id,
                    str(mention.get("citing_paper_id") or trace.get("paper_id") or ""),
                    source_chunk_id,
                    work_id,
                    mention.get("reference_index"),
                    str(mention.get("marker_text") or ""),
                    str(reference.get("citation_text") or ""),
                    str(mention.get("context_text") or ""),
                    mention.get("source_page"),
                    json.dumps(mention.get("source_rect"), ensure_ascii=False),
                    str(mention.get("citation_link_status") or "ambiguous"),
                    str(mention.get("scope_status") or "ambiguous"),
                    str(reference.get("bibliographic_resolution_status") or "unresolved"),
                    str(mention.get("parser_version") or trace.get("parser_version") or ""),
                    str(mention.get("source_sha256") or trace.get("source_sha256") or ""),
                ),
            )
            stale_work_ids = _align_claim_source_edges(
                connection,
                mention_id=mention_id,
                cited_work_id=work_id,
            )
            if previous_work_id:
                stale_work_ids.add(previous_work_id)
            for stale_work_id in stale_work_ids:
                if _is_identity_enrichment(stale_work_id, work_id):
                    _migrate_work_origins(
                        connection,
                        old_work_id=stale_work_id,
                        canonical_work_id=work_id,
                    )
            mention_count += 1
            if claim_id and _claim_exists(connection, claim_id):
                connection.execute(
                    """
                    INSERT INTO claim_sources (
                        claim_id, mention_id, cited_work_id, source_role,
                        support_relation, origin_review_status, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(claim_id, mention_id, cited_work_id) DO UPDATE SET
                        source_role=CASE
                            WHEN claim_sources.origin_review_status = 'review_required'
                            THEN excluded.source_role
                            ELSE claim_sources.source_role
                        END,
                        support_relation=CASE
                            WHEN claim_sources.origin_review_status = 'review_required'
                            THEN excluded.support_relation
                            ELSE claim_sources.support_relation
                        END,
                        origin_review_status=CASE
                            WHEN claim_sources.origin_review_status = 'review_required'
                            THEN excluded.origin_review_status
                            ELSE claim_sources.origin_review_status
                        END,
                        confidence=CASE
                            WHEN claim_sources.origin_review_status = 'review_required'
                            THEN excluded.confidence
                            ELSE claim_sources.confidence
                        END
                    """,
                    (
                        claim_id,
                        mention_id,
                        work_id,
                        str(mention.get("support_role") or "unresolved"),
                        str(mention.get("support_relation") or "unclear"),
                        str(mention.get("origin_review_status") or "review_required"),
                        _optional_float(mention.get("claim_match_score")),
                    ),
                )
                claim_edge_count += 1
        connection.commit()
    return {
        "cited_works_upserted": work_count,
        "citation_mentions_upserted": mention_count,
        "claim_source_edges_upserted": claim_edge_count,
    }


def source_chain_for_claim(claim_id: str, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, object]]:
    initialize_provenance_store(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT cs.*, cm.citing_paper_id, cm.source_chunk_id, cm.reference_index,
                   cm.marker_text, cm.context_text, cm.source_page, cm.source_rect_json,
                   cm.printed_reference,
                   cm.citation_link_status, cm.scope_status,
                   cm.bibliographic_resolution_status,
                   cm.parser_version,
                   cw.doi, cw.pmid, cw.pmcid, cw.title, cw.author, cw.year, cw.journal,
                   cw.citation_text, cw.registry_paper_id,
                   cw.bibliographic_resolution_status AS work_bibliographic_resolution_status,
                   cw.oa_pdf_url
            FROM claim_sources cs
            JOIN citation_mentions cm ON cm.mention_id = cs.mention_id
            JOIN cited_works cw ON cw.work_id = cs.cited_work_id
            WHERE cs.claim_id = ?
            ORDER BY cm.source_page, cm.reference_index, cm.mention_id
            """,
            (claim_id,),
        ).fetchall()
    return [dict(row) for row in rows]


_WORK_METADATA_FIELDS = (
    "doi",
    "pmid",
    "pmcid",
    "title",
    "author",
    "year",
    "journal",
    "citation_text",
    "registry_paper_id",
    "bibliographic_resolution_status",
    "metadata_source",
    "oa_pdf_url",
)

_RESOLUTION_TRUST = {
    "conflict": -1,
    "unresolved": 0,
    "candidate_match": 1,
    "identifier_extracted_unverified": 2,
    "exact_title": 3,
    "exact_id": 4,
}


def _prepare_cited_work(
    connection: sqlite3.Connection,
    work_id: str,
    reference: dict[str, object],
) -> dict[str, str]:
    """Prepare canonical work metadata without letting a weak rerun replace a strong one."""
    status = str(reference.get("bibliographic_resolution_status") or "unresolved")
    prepared = {
        field: str(reference.get(field) or "").strip() for field in _WORK_METADATA_FIELDS
    }
    prepared["bibliographic_resolution_status"] = status

    normalizers = {
        "doi": normalize_doi,
        "pmid": normalize_pmid,
        "pmcid": normalize_pmcid,
    }
    if "verified_identifiers" in reference and status in {
        "exact_id",
        "exact_title",
        "candidate_match",
        "conflict",
    }:
        verified = reference.get("verified_identifiers")
        verified = verified if isinstance(verified, dict) else {}
        for field, normalizer in normalizers.items():
            prepared[field] = normalizer(verified.get(field))
    else:
        for field, normalizer in normalizers.items():
            prepared[field] = normalizer(reference.get(field))

    existing = connection.execute(
        """
        SELECT doi, pmid, pmcid, title, author, year, journal, citation_text,
               registry_paper_id, bibliographic_resolution_status,
               metadata_source, oa_pdf_url
        FROM cited_works
        WHERE work_id = ?
        """,
        (work_id,),
    ).fetchone()
    if not existing:
        return prepared

    existing_values = {
        field: str(value or "").strip()
        for field, value in zip(_WORK_METADATA_FIELDS, existing, strict=True)
    }
    existing_status = existing_values["bibliographic_resolution_status"] or "unresolved"
    if _RESOLUTION_TRUST.get(status, 0) < _RESOLUTION_TRUST.get(existing_status, 0):
        return existing_values
    return prepared


def _claim_exists(connection: sqlite3.Connection, claim_id: str) -> bool:
    return connection.execute("SELECT 1 FROM claims WHERE claim_id = ?", (claim_id,)).fetchone() is not None


def _previous_mention_work_id(connection: sqlite3.Connection, mention_id: str) -> str:
    row = connection.execute(
        "SELECT cited_work_id FROM citation_mentions WHERE mention_id = ?",
        (mention_id,),
    ).fetchone()
    return str(row[0]) if row else ""


def _is_identity_enrichment(old_work_id: str, canonical_work_id: str) -> bool:
    """Return whether the new ID outranks the old ID in stable_work_id preference."""
    prefixes = {"citation": 0, "registry": 1, "pmid": 2, "pmcid": 3, "doi": 4}
    old_prefix = old_work_id.partition(":")[0].lower()
    canonical_prefix = canonical_work_id.partition(":")[0].lower()
    return prefixes.get(canonical_prefix, -1) > prefixes.get(old_prefix, -1)


def _migrate_work_origins(
    connection: sqlite3.Connection,
    *,
    old_work_id: str,
    canonical_work_id: str,
) -> None:
    """Move reviewed origin edges to an enriched work ID, merging conflicts conservatively."""
    origin_edges = connection.execute(
        """
        SELECT origin_id, relation_type, evidence_locator, review_status, created_at
        FROM work_origins
        WHERE work_id = ?
        """,
        (old_work_id,),
    ).fetchall()
    for edge in origin_edges:
        connection.execute(
            """
            INSERT INTO work_origins (
                work_id, origin_id, relation_type, evidence_locator,
                review_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(work_id, origin_id, relation_type) DO UPDATE SET
                evidence_locator=CASE
                    WHEN work_origins.review_status = 'review_required'
                         AND excluded.review_status <> 'review_required'
                    THEN excluded.evidence_locator
                    ELSE COALESCE(
                        NULLIF(TRIM(work_origins.evidence_locator), ''),
                        excluded.evidence_locator
                    )
                END,
                review_status=CASE
                    WHEN work_origins.review_status = 'review_required'
                         AND excluded.review_status <> 'review_required'
                    THEN excluded.review_status
                    ELSE work_origins.review_status
                END
            """,
            (
                canonical_work_id,
                edge[0],
                edge[1],
                edge[2],
                edge[3],
                edge[4],
            ),
        )
    if origin_edges:
        connection.execute("DELETE FROM work_origins WHERE work_id = ?", (old_work_id,))


def _align_claim_source_edges(
    connection: sqlite3.Connection,
    *,
    mention_id: str,
    cited_work_id: str,
) -> set[str]:
    """Move claim edges to a mention's current work identity without losing review data."""
    stale_edges = connection.execute(
        """
        SELECT claim_id, cited_work_id, source_role, support_relation, origin_review_status,
               confidence, reviewer, created_at
        FROM claim_sources
        WHERE mention_id = ? AND cited_work_id <> ?
        """,
        (mention_id, cited_work_id),
    ).fetchall()
    for edge in stale_edges:
        connection.execute(
            """
            INSERT INTO claim_sources (
                claim_id, mention_id, cited_work_id, source_role,
                support_relation, origin_review_status, confidence,
                reviewer, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(claim_id, mention_id, cited_work_id) DO UPDATE SET
                source_role=CASE
                    WHEN claim_sources.origin_review_status = 'review_required'
                         AND excluded.origin_review_status <> 'review_required'
                    THEN excluded.source_role
                    ELSE claim_sources.source_role
                END,
                support_relation=CASE
                    WHEN claim_sources.origin_review_status = 'review_required'
                         AND excluded.origin_review_status <> 'review_required'
                    THEN excluded.support_relation
                    ELSE claim_sources.support_relation
                END,
                origin_review_status=CASE
                    WHEN claim_sources.origin_review_status = 'review_required'
                         AND excluded.origin_review_status <> 'review_required'
                    THEN excluded.origin_review_status
                    ELSE claim_sources.origin_review_status
                END,
                confidence=COALESCE(claim_sources.confidence, excluded.confidence),
                reviewer=COALESCE(NULLIF(claim_sources.reviewer, ''), excluded.reviewer)
            """,
            (
                edge[0],
                mention_id,
                cited_work_id,
                edge[2],
                edge[3],
                edge[4],
                edge[5],
                edge[6],
                edge[7],
            ),
        )
    if stale_edges:
        connection.execute(
            "DELETE FROM claim_sources WHERE mention_id = ? AND cited_work_id <> ?",
            (mention_id, cited_work_id),
        )
    return {str(edge[1]) for edge in stale_edges}


def _optional_float(value: object) -> float | None:
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return None
