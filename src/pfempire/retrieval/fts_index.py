from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pfempire.retrieval.keyword import COMMON_TERMS, query_tokens, search_chunks
from pfempire.retrieval.metadata import load_paper_metadata


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INDEX_PATH = REPO_ROOT / "data/indexes/pfempire_fts.sqlite"
INDEX_SCHEMA_VERSION = "3"
CANDIDATE_RECORD_STORE_VERSION = "1"
SQLITE_VALUE_BATCH_SIZE = 400
STRONG_MANIFEST_MAX_WORKERS = 16
PERSISTENT_ATTESTATION_SCHEMA = "pfempire_retrieval_index_attestation_v1"
RUNTIME_ATTESTATION_FILENAME = "retrieval-index-attestation.json"
CANDIDATE_STORE_ATTESTATION_SCHEMA = "pfempire_candidate_store_attestation_v1"
CANDIDATE_STORE_ATTESTATION_FILENAME = (
    "retrieval-index-candidate-store-attestation.json"
)
QUESTION_NOISE = {
    "about",
    "any",
    "anything",
    "why",
    "might",
    "what",
    "which",
    "where",
    "when",
    "how",
    "does",
    "could",
    "would",
    "explain",
    "evidence",
    "relevant",
    "find",
    "information",
    "theses",
    "whatever",
}


class RetrievalIndexError(RuntimeError):
    pass


@dataclass(frozen=True)
class IndexBuildResult:
    index_path: Path
    chunk_count: int
    paper_count: int
    corpus_fingerprint: str
    corpus_content_digest: str | None = None


@dataclass(frozen=True)
class CorpusFileRecord:
    kind: str
    filename: str
    byte_count: int
    mtime_ns: int
    ctime_ns: int
    sha256: str
    row_count: int


@dataclass(frozen=True)
class IndexCandidates:
    chunk_ids: frozenset[str]
    paper_ids: frozenset[str]
    chunk_paper_ids: frozenset[str]
    chunk_ranks: dict[str, int]
    paper_ranks: dict[str, int]


def build_fts_index(
    chunks_dir: Path,
    papers_tsv: Path,
    index_path: Path = DEFAULT_INDEX_PATH,
) -> IndexBuildResult:
    chunks_dir = Path(chunks_dir).resolve()
    papers_tsv = Path(papers_tsv).resolve()
    index_path = Path(index_path).resolve()
    if not chunks_dir.exists():
        raise RetrievalIndexError(f"Chunks directory missing: {chunks_dir}")
    if not chunks_dir.is_dir():
        raise RetrievalIndexError(f"Chunks path is not a directory: {chunks_dir}")
    if not papers_tsv.exists() or not papers_tsv.is_file():
        raise RetrievalIndexError(f"Paper metadata TSV missing: {papers_tsv}")
    index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = index_path.with_suffix(index_path.suffix + ".building")
    temporary_path.unlink(missing_ok=True)

    strong_manifest = strong_corpus_file_manifest(chunks_dir, papers_tsv)
    content_digest = corpus_content_digest(strong_manifest)
    metadata = load_paper_metadata(papers_tsv)
    fingerprint = corpus_fingerprint(chunks_dir, papers_tsv)
    chunk_count = 0
    canonical_per_paper_files = True
    connection = sqlite3.connect(temporary_path)
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(
            "CREATE VIRTUAL TABLE chunk_fts USING fts5("
            "chunk_id UNINDEXED, paper_id UNINDEXED, title, text, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        connection.execute(
            "CREATE VIRTUAL TABLE paper_fts USING fts5("
            "paper_id UNINDEXED, title, author, topics, methods, systems, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        connection.execute(
            "CREATE TABLE chunk_records ("
            "source_filename TEXT NOT NULL, "
            "line_number INTEGER NOT NULL, "
            "chunk_id TEXT NOT NULL, "
            "paper_id TEXT NOT NULL, "
            "chunk_json TEXT NOT NULL, "
            "chunk_json_sha256 TEXT NOT NULL, "
            "PRIMARY KEY(source_filename, line_number))"
        )
        connection.execute(
            "CREATE INDEX chunk_records_chunk_id_idx ON chunk_records(chunk_id)"
        )
        connection.execute(
            "CREATE INDEX chunk_records_paper_id_idx ON chunk_records(paper_id)"
        )
        connection.execute(
            "CREATE TABLE corpus_file_manifest ("
            "kind TEXT NOT NULL, filename TEXT NOT NULL, byte_count INTEGER NOT NULL, "
            "mtime_ns INTEGER NOT NULL, ctime_ns INTEGER NOT NULL, sha256 TEXT NOT NULL, "
            "row_count INTEGER NOT NULL, PRIMARY KEY(kind, filename))"
        )
        connection.execute(
            "CREATE TABLE paper_metadata_records ("
            "paper_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL)"
        )
        paper_rows = [
            (
                paper_id,
                record.get("title", ""),
                record.get("author", ""),
                record.get("topics", ""),
                record.get("methods", ""),
                record.get("systems", ""),
            )
            for paper_id, record in sorted(metadata.items())
        ]
        connection.executemany(
            "INSERT INTO paper_fts(paper_id, title, author, topics, methods, systems) VALUES (?, ?, ?, ?, ?, ?)",
            paper_rows,
        )

        chunk_rows: list[tuple[str, str, str, str]] = []
        record_rows: list[tuple[str, int, str, str, str, str]] = []
        for chunk_file in sorted(chunks_dir.glob("*.jsonl")):
            with chunk_file.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    paper_id = str(chunk.get("paper_id") or chunk_file.stem)
                    chunk_id = str(
                        chunk.get("chunk_id") or f"{paper_id}_{chunk_count + 1}"
                    )
                    if paper_id != chunk_file.stem:
                        canonical_per_paper_files = False
                    chunk_rows.append(
                        (
                            chunk_id,
                            paper_id,
                            metadata.get(paper_id, {}).get("title", ""),
                            str(chunk.get("text") or ""),
                        )
                    )
                    record_rows.append(
                        (
                            chunk_file.name,
                            line_number,
                            chunk_id,
                            paper_id,
                            line.rstrip("\r\n"),
                            hashlib.sha256(line.rstrip("\r\n").encode("utf-8")).hexdigest(),
                        )
                    )
                    chunk_count += 1
                    if len(chunk_rows) >= 1000:
                        connection.executemany(
                            "INSERT INTO chunk_fts(chunk_id, paper_id, title, text) VALUES (?, ?, ?, ?)",
                            chunk_rows,
                        )
                        connection.executemany(
                            "INSERT INTO chunk_records("
                            "source_filename, line_number, chunk_id, paper_id, chunk_json, "
                            "chunk_json_sha256) VALUES (?, ?, ?, ?, ?, ?)",
                            record_rows,
                        )
                        chunk_rows.clear()
                        record_rows.clear()
        if chunk_rows:
            connection.executemany(
                "INSERT INTO chunk_fts(chunk_id, paper_id, title, text) VALUES (?, ?, ?, ?)",
                chunk_rows,
            )
            connection.executemany(
                "INSERT INTO chunk_records("
                "source_filename, line_number, chunk_id, paper_id, chunk_json, "
                "chunk_json_sha256) VALUES (?, ?, ?, ?, ?, ?)",
                record_rows,
            )
        if current_corpus_fast_state(chunks_dir, papers_tsv) != manifest_fast_state(
            strong_manifest
        ):
            raise RetrievalIndexError(
                "Corpus changed while retrieval-index records were constructed"
            )
        connection.executemany(
            "INSERT INTO corpus_file_manifest("
            "kind, filename, byte_count, mtime_ns, ctime_ns, sha256, row_count"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row.kind,
                    row.filename,
                    row.byte_count,
                    row.mtime_ns,
                    row.ctime_ns,
                    row.sha256,
                    row.row_count,
                )
                for row in strong_manifest
            ],
        )
        metadata_record_rows = paper_metadata_record_rows(metadata)
        connection.executemany(
            "INSERT INTO paper_metadata_records(paper_id, record_json, record_sha256) "
            "VALUES (?, ?, ?)",
            metadata_record_rows,
        )
        connection.executemany(
            "INSERT INTO index_meta(key, value) VALUES (?, ?)",
            [
                ("corpus_fingerprint", fingerprint),
                ("chunk_count", str(chunk_count)),
                ("paper_count", str(len(paper_rows))),
                ("schema_version", INDEX_SCHEMA_VERSION),
                ("candidate_record_store_version", CANDIDATE_RECORD_STORE_VERSION),
                ("candidate_record_count", str(chunk_count)),
                ("corpus_content_digest", content_digest),
                (
                    "canonical_per_paper_files",
                    "1" if canonical_per_paper_files else "0",
                ),
            ],
        )
        connection.commit()
    except sqlite3.DatabaseError as exc:
        raise RetrievalIndexError(f"Could not build SQLite FTS5 index: {exc}") from exc
    finally:
        connection.close()
    temporary_path.replace(index_path)
    _store_persistent_strong_attestation(
        index_path,
        chunks_dir,
        papers_tsv,
        content_digest,
        manifest_fast_state(strong_manifest),
    )
    _store_candidate_record_store_attestation(
        index_path,
        chunks_dir,
        papers_tsv,
        chunk_count=chunk_count,
        candidate_record_count=chunk_count,
    )
    return IndexBuildResult(
        index_path, chunk_count, len(paper_rows), fingerprint, content_digest
    )


def update_fts_index(
    chunks_dir: Path,
    papers_tsv: Path,
    paper_ids: list[str] | tuple[str, ...] | set[str],
    index_path: Path = DEFAULT_INDEX_PATH,
    *,
    expected_previous_fingerprint: str,
) -> IndexBuildResult:
    """Transactionally add or replace a small set of papers in an existing index."""
    chunks_dir = Path(chunks_dir).resolve()
    papers_tsv = Path(papers_tsv).resolve()
    index_path = Path(index_path).resolve()
    selected = sorted({str(paper_id).strip() for paper_id in paper_ids if str(paper_id).strip()})
    if not selected:
        raise RetrievalIndexError("Incremental index update requires at least one paper ID")
    if not index_path.is_file():
        raise RetrievalIndexError(f"Retrieval index missing: {index_path}")
    current_manifest = strong_corpus_file_manifest(chunks_dir, papers_tsv)
    metadata = load_paper_metadata(papers_tsv)
    missing_metadata = [paper_id for paper_id in selected if paper_id not in metadata]
    missing_chunks = [paper_id for paper_id in selected if not (chunks_dir / f"{paper_id}.jsonl").is_file()]
    if missing_metadata or missing_chunks:
        raise RetrievalIndexError(
            "Incremental index inputs are incomplete: "
            f"metadata={missing_metadata}, chunks={missing_chunks}"
        )
    fingerprint = corpus_fingerprint(chunks_dir, papers_tsv)
    content_digest = corpus_content_digest(current_manifest)
    current_metadata_rows = paper_metadata_record_rows(metadata)
    connection = sqlite3.connect(index_path, timeout=60)
    try:
        connection.execute("BEGIN IMMEDIATE")
        stored = connection.execute(
            "SELECT value FROM index_meta WHERE key='corpus_fingerprint'"
        ).fetchone()
        schema = connection.execute(
            "SELECT value FROM index_meta WHERE key='schema_version'"
        ).fetchone()
        canonical_layout_row = connection.execute(
            "SELECT value FROM index_meta "
            "WHERE key='canonical_per_paper_files'"
        ).fetchone()
        stored_content_row = connection.execute(
            "SELECT value FROM index_meta WHERE key='corpus_content_digest'"
        ).fetchone()
        stored_manifest_rows = connection.execute(
            "SELECT kind, filename, byte_count, mtime_ns, ctime_ns, sha256, row_count "
            "FROM corpus_file_manifest ORDER BY kind, filename"
        ).fetchall()
        stored_metadata_rows = connection.execute(
            "SELECT paper_id, record_json, record_sha256 FROM paper_metadata_records"
        ).fetchall()
        if not stored or str(stored[0]) != expected_previous_fingerprint:
            raise RetrievalIndexError(
                "Existing retrieval index did not match the corpus before import"
            )
        if not schema or str(schema[0]) != INDEX_SCHEMA_VERSION:
            raise RetrievalIndexError("Existing retrieval index schema is not incrementally updatable")
        stored_manifest = tuple(
            CorpusFileRecord(*row) for row in stored_manifest_rows
        )
        if (
            not stored_content_row
            or corpus_content_digest(stored_manifest) != str(stored_content_row[0])
        ):
            raise RetrievalIndexError(
                "Existing retrieval index has an invalid strong corpus manifest"
            )
        previous_canonical_layout = bool(
            canonical_layout_row and str(canonical_layout_row[0]) == "1"
        )
        if not previous_canonical_layout:
            raise RetrievalIndexError(
                "Incremental index update requires a previously verified canonical "
                "per-paper chunk layout; rebuild the complete retrieval index"
            )
        _validate_incremental_update_scope(
            selected=frozenset(selected),
            stored_manifest=stored_manifest,
            current_manifest=current_manifest,
            stored_metadata_rows=stored_metadata_rows,
            current_metadata_rows=current_metadata_rows,
        )
        for paper_id in selected:
            record = metadata[paper_id]
            connection.execute("DELETE FROM chunk_fts WHERE paper_id = ?", (paper_id,))
            connection.execute("DELETE FROM chunk_records WHERE paper_id = ?", (paper_id,))
            connection.execute("DELETE FROM paper_fts WHERE paper_id = ?", (paper_id,))
            connection.execute(
                "INSERT INTO paper_fts(paper_id, title, author, topics, methods, systems) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    paper_id,
                    record.get("title", ""),
                    record.get("author", ""),
                    record.get("topics", ""),
                    record.get("methods", ""),
                    record.get("systems", ""),
                ),
            )
            chunk_rows: list[tuple[str, str, str, str]] = []
            record_rows: list[tuple[str, int, str, str, str, str]] = []
            with (chunks_dir / f"{paper_id}.jsonl").open(encoding="utf-8") as handle:
                for position, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if str(chunk.get("paper_id") or paper_id) != paper_id:
                        raise RetrievalIndexError(
                            "Incremental chunk file contains a foreign paper_id; "
                            "rebuild the complete retrieval index"
                        )
                    chunk_id = str(chunk.get("chunk_id") or f"{paper_id}_{position}")
                    chunk_json = line.rstrip("\r\n")
                    chunk_rows.append(
                        (
                            chunk_id,
                            paper_id,
                            record.get("title", ""),
                            str(chunk.get("text") or ""),
                        )
                    )
                    record_rows.append(
                        (
                            f"{paper_id}.jsonl",
                            position,
                            chunk_id,
                            paper_id,
                            chunk_json,
                            hashlib.sha256(chunk_json.encode("utf-8")).hexdigest(),
                        )
                    )
            connection.executemany(
                "INSERT INTO chunk_fts(chunk_id, paper_id, title, text) VALUES (?, ?, ?, ?)",
                chunk_rows,
            )
            connection.executemany(
                "INSERT INTO chunk_records("
                "source_filename, line_number, chunk_id, paper_id, chunk_json, "
                "chunk_json_sha256) VALUES (?, ?, ?, ?, ?, ?)",
                record_rows,
            )
        chunk_count = int(connection.execute("SELECT count(*) FROM chunk_fts").fetchone()[0])
        record_count = int(
            connection.execute("SELECT count(*) FROM chunk_records").fetchone()[0]
        )
        if record_count != chunk_count:
            raise RetrievalIndexError(
                "Incremental retrieval index update produced an incomplete candidate record store"
            )
        paper_count = int(connection.execute("SELECT count(*) FROM paper_fts").fetchone()[0])
        if current_corpus_fast_state(chunks_dir, papers_tsv) != manifest_fast_state(
            current_manifest
        ):
            raise RetrievalIndexError(
                "Corpus changed while the incremental retrieval index was constructed"
            )
        connection.execute("DELETE FROM corpus_file_manifest")
        connection.executemany(
            "INSERT INTO corpus_file_manifest("
            "kind, filename, byte_count, mtime_ns, ctime_ns, sha256, row_count"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row.kind,
                    row.filename,
                    row.byte_count,
                    row.mtime_ns,
                    row.ctime_ns,
                    row.sha256,
                    row.row_count,
                )
                for row in current_manifest
            ],
        )
        connection.execute("DELETE FROM paper_metadata_records")
        connection.executemany(
            "INSERT INTO paper_metadata_records(paper_id, record_json, record_sha256) "
            "VALUES (?, ?, ?)",
            current_metadata_rows,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO index_meta(key, value) VALUES (?, ?)",
            [
                ("corpus_fingerprint", fingerprint),
                ("chunk_count", str(chunk_count)),
                ("paper_count", str(paper_count)),
                ("schema_version", INDEX_SCHEMA_VERSION),
                ("candidate_record_store_version", CANDIDATE_RECORD_STORE_VERSION),
                ("candidate_record_count", str(record_count)),
                ("corpus_content_digest", content_digest),
                ("canonical_per_paper_files", "1"),
            ],
        )
        candidate_store_metadata = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "candidate_record_store_version": CANDIDATE_RECORD_STORE_VERSION,
            "chunk_count": str(chunk_count),
            "candidate_record_count": str(record_count),
        }
        if not _candidate_record_store_has_full_parity(
            connection, candidate_store_metadata
        ):
            raise RetrievalIndexError(
                "Incremental retrieval index update produced mismatched FTS and "
                "candidate-record paper coverage"
            )
        connection.commit()
    except sqlite3.DatabaseError as exc:
        connection.rollback()
        raise RetrievalIndexError(f"Could not update SQLite FTS5 index: {exc}") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    _store_persistent_strong_attestation(
        index_path,
        chunks_dir,
        papers_tsv,
        content_digest,
        manifest_fast_state(current_manifest),
    )
    _store_candidate_record_store_attestation(
        index_path,
        chunks_dir,
        papers_tsv,
        chunk_count=chunk_count,
        candidate_record_count=record_count,
    )
    return IndexBuildResult(
        index_path, chunk_count, paper_count, fingerprint, content_digest
    )


def _validate_incremental_update_scope(
    *,
    selected: frozenset[str],
    stored_manifest: tuple[CorpusFileRecord, ...],
    current_manifest: tuple[CorpusFileRecord, ...],
    stored_metadata_rows: list[tuple[str, str, str]],
    current_metadata_rows: list[tuple[str, str, str]],
) -> None:
    selected_files = {f"{paper_id}.jsonl" for paper_id in selected}
    stored_chunks = {
        row.filename: row for row in stored_manifest if row.kind == "chunk"
    }
    current_chunks = {
        row.filename: row for row in current_manifest if row.kind == "chunk"
    }
    changed_inventory = set(stored_chunks) ^ set(current_chunks)
    if changed_inventory - selected_files:
        raise RetrievalIndexError(
            "Incremental index update found an added or removed unselected chunk file"
        )
    for filename in sorted(set(stored_chunks) & set(current_chunks)):
        if filename in selected_files:
            continue
        before = stored_chunks[filename]
        after = current_chunks[filename]
        if (
            before.byte_count,
            before.sha256,
            before.row_count,
        ) != (
            after.byte_count,
            after.sha256,
            after.row_count,
        ):
            raise RetrievalIndexError(
                "Incremental index update found a changed unselected chunk file: "
                f"{filename}"
            )

    stored_metadata = {paper_id: (record_json, digest) for paper_id, record_json, digest in stored_metadata_rows}
    current_metadata = {paper_id: (record_json, digest) for paper_id, record_json, digest in current_metadata_rows}
    for paper_id, (record_json, digest) in stored_metadata.items():
        if hashlib.sha256(record_json.encode("utf-8")).hexdigest() != digest:
            raise RetrievalIndexError(
                "Existing retrieval index has a corrupt paper metadata record"
            )
    unselected_metadata_ids = (set(stored_metadata) | set(current_metadata)) - selected
    if any(
        stored_metadata.get(paper_id) != current_metadata.get(paper_id)
        for paper_id in unselected_metadata_ids
    ):
        raise RetrievalIndexError(
            "Incremental index update found changed unselected paper metadata"
        )


def search_index_candidates(
    query: str,
    index_path: Path,
    *,
    chunk_limit: int = 1200,
    paper_limit: int = 30,
) -> IndexCandidates:
    index_path = Path(index_path)
    if not index_path.exists():
        raise RetrievalIndexError(
            f"Retrieval index missing: {index_path}. Run: python scripts/build_retrieval_index.py"
        )
    match_query = build_fts_query(query)
    if not match_query:
        return IndexCandidates(frozenset(), frozenset(), frozenset(), {}, {})
    connection = sqlite3.connect(f"file:{index_path.resolve()}?mode=ro", uri=True)
    try:
        chunk_rows = connection.execute(
            "SELECT chunk_id, paper_id FROM chunk_fts WHERE chunk_fts MATCH ? "
            "ORDER BY bm25(chunk_fts, 0.0, 0.0, 4.0, 1.0) LIMIT ?",
            (match_query, max(1, chunk_limit)),
        ).fetchall()
        paper_rows = connection.execute(
            "SELECT paper_id FROM paper_fts WHERE paper_fts MATCH ? "
            "ORDER BY bm25(paper_fts, 0.0, 6.0, 1.0, 1.5, 1.5, 1.5) LIMIT ?",
            (match_query, max(1, paper_limit)),
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise RetrievalIndexError(f"Could not query retrieval index {index_path}: {exc}") from exc
    finally:
        connection.close()
    chunk_ranks = {str(row[0]): rank for rank, row in enumerate(chunk_rows, start=1)}
    paper_ranks = {str(row[0]): rank for rank, row in enumerate(paper_rows, start=1)}
    return IndexCandidates(
        chunk_ids=frozenset(chunk_ranks),
        paper_ids=frozenset(paper_ranks),
        chunk_paper_ids=frozenset(str(row[1]) for row in chunk_rows),
        chunk_ranks=chunk_ranks,
        paper_ranks=paper_ranks,
    )


def search_chunks_bm25(
    query: str,
    chunks_dir: Path,
    papers_tsv: Path,
    *,
    index_path: Path = DEFAULT_INDEX_PATH,
    index_chunk_limit: int = 1200,
    index_paper_limit: int = 30,
    validate_index: bool = True,
    candidate_record_store_available: bool | None = None,
    **kwargs: object,
) -> list[dict]:
    """Retrieve FTS candidates and rerank them with the established scorer.

    ``validate_index=False`` is reserved for a bounded multi-query request that
    has already called :func:`ensure_current_index` once immediately before its
    subqueries. Direct callers retain validation by default.
    """

    if validate_index:
        ensure_current_index(index_path, chunks_dir, papers_tsv)
    if candidate_record_store_available is None:
        candidate_record_store_available = index_has_candidate_record_store(
            index_path,
            chunks_dir=chunks_dir,
            papers_tsv=papers_tsv,
        )
    candidates = search_index_candidates(
        query,
        index_path,
        chunk_limit=index_chunk_limit,
        paper_limit=index_paper_limit,
    )
    candidate_records = load_index_candidate_records(
        index_path,
        chunks_dir,
        chunk_ids=candidates.chunk_ids,
        paper_ids=candidates.paper_ids,
        record_store_available=candidate_record_store_available,
    )
    canonical_source_layout = False
    candidate_source_papers: frozenset[str] | set[str] | None = None
    if candidate_records is None:



        canonical_source_layout = index_has_canonical_per_paper_layout(index_path)
        candidate_source_papers = candidates.paper_ids | candidates.chunk_paper_ids
        if canonical_source_layout:



            candidate_source_papers = frozenset(
                set(candidates.chunk_paper_ids)
                | {
                    paper_id
                    for paper_id in candidates.paper_ids
                    if _safe_canonical_chunk_file(chunks_dir, paper_id) is not None
                }
            )
    results = search_chunks(
        query=query,
        chunks_dir=chunks_dir,
        papers_tsv=papers_tsv,
        candidate_chunk_ids=candidates.chunk_ids,
        candidate_paper_ids=candidates.paper_ids,
        candidate_source_paper_ids=candidate_source_papers,
        candidate_source_layout_verified=canonical_source_layout,
        candidate_records=candidate_records,
        **kwargs,
    )
    for result in results:
        chunk_id = str(result.get("chunk_id") or "")
        paper_id = str(result.get("paper_id") or "")
        result["retrieval_backend"] = "bm25_fts5_rerank"
        result["bm25_chunk_rank"] = candidates.chunk_ranks.get(chunk_id, "")
        result["bm25_paper_rank"] = candidates.paper_ranks.get(paper_id, "")
    return results


def _safe_canonical_chunk_file(chunks_dir: Path, paper_id: str) -> Path | None:
    """Return an existing safe per-paper chunk path, otherwise ``None``."""

    filename = f"{paper_id}.jsonl"
    if not paper_id or Path(filename).name != filename:
        return None
    chunk_file = Path(chunks_dir) / filename
    return chunk_file if chunk_file.is_file() else None


def index_has_canonical_per_paper_layout(index_path: Path) -> bool:
    """Return whether index construction verified one paper per named JSONL file."""

    connection = sqlite3.connect(
        f"file:{Path(index_path).resolve()}?mode=ro", uri=True
    )
    try:
        row = connection.execute(
            "SELECT value FROM index_meta WHERE key='canonical_per_paper_files'"
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise RetrievalIndexError(
            f"Invalid retrieval index {index_path}: {exc}"
        ) from exc
    finally:
        connection.close()
    return bool(row and str(row[0]) == "1")


def index_has_candidate_record_store(
    index_path: Path,
    *,
    chunks_dir: Path | None = None,
    papers_tsv: Path | None = None,
) -> bool:
    """Return whether the index contains a complete compatible chunk-record store.

    Full FTS/record parity is persisted only for the exact index identity.  If
    the receipt is absent, malformed, unsafe, or stale, the complete parity
    query runs before a new receipt is written atomically.
    """

    index_path = Path(index_path).resolve()
    try:
        identity_before = _index_identity(index_path)
    except OSError:
        return False
    connection = sqlite3.connect(
        f"file:{index_path}?mode=ro", uri=True
    )
    try:
        metadata = dict(
            connection.execute(
                "SELECT key, value FROM index_meta WHERE key IN ("
                "'schema_version', 'chunk_count', 'candidate_record_store_version', "
                "'candidate_record_count')"
            ).fetchall()
        )
        if (
            str(metadata.get("schema_version") or "") != INDEX_SCHEMA_VERSION
            or str(metadata.get("candidate_record_store_version") or "")
            != CANDIDATE_RECORD_STORE_VERSION
        ):
            return False
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunk_records'"
        ).fetchone()
        if not table:
            return False
        if _index_identity(index_path) != identity_before:
            return False
        if _candidate_record_store_attestation_matches(
            index_path,
            identity_before,
            metadata,
            chunks_dir=chunks_dir,
            papers_tsv=papers_tsv,
        ):
            return _index_identity(index_path) == identity_before
        complete = _candidate_record_store_has_full_parity(connection, metadata)
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError):
        return False
    finally:
        connection.close()
    try:
        identity_unchanged = _index_identity(index_path) == identity_before
    except OSError:
        identity_unchanged = False
    if not complete or not identity_unchanged:
        return False
    _store_candidate_record_store_attestation(
        index_path,
        chunks_dir,
        papers_tsv,
        chunk_count=int(metadata.get("chunk_count") or -1),
        candidate_record_count=int(metadata.get("candidate_record_count") or -1),
        expected_index_identity=identity_before,
    )
    return True


def _candidate_record_store_has_full_parity(
    connection: sqlite3.Connection,
    metadata: dict[str, str],
) -> bool:
    """Run the complete candidate-record/FTS parity validation."""

    actual_count = int(
        connection.execute("SELECT count(*) FROM chunk_records").fetchone()[0]
    )
    paper_count_mismatch = connection.execute(
        "WITH fts_counts AS ("
        "SELECT paper_id, count(*) AS n FROM chunk_fts GROUP BY paper_id"
        "), record_counts AS ("
        "SELECT paper_id, count(*) AS n FROM chunk_records GROUP BY paper_id"
        ") "
        "SELECT 1 FROM ("
        "SELECT paper_id, n FROM fts_counts EXCEPT SELECT paper_id, n FROM record_counts "
        "UNION ALL "
        "SELECT paper_id, n FROM record_counts EXCEPT SELECT paper_id, n FROM fts_counts"
        ") LIMIT 1"
    ).fetchone()
    return (
        actual_count == int(metadata.get("chunk_count") or -1)
        and actual_count == int(metadata.get("candidate_record_count") or -1)
        and paper_count_mismatch is None
    )


def load_index_candidate_records(
    index_path: Path,
    chunks_dir: Path,
    *,
    chunk_ids: frozenset[str] | set[str],
    paper_ids: frozenset[str] | set[str],
    record_store_available: bool | None = None,
) -> tuple[tuple[str, int, dict], ...] | None:
    """Load candidate JSON records from a compatible validated index.

    ``None`` means that this index predates the record store and the caller
    must use the established JSONL path. A compatible but empty candidate set
    returns an empty tuple.
    """

    if record_store_available is None:
        record_store_available = index_has_candidate_record_store(index_path)
    if not record_store_available:
        return None
    connection = sqlite3.connect(
        f"file:{Path(index_path).resolve()}?mode=ro", uri=True
    )
    try:
        rows: dict[tuple[str, int], tuple[str, int, str, str, str, str]] = {}
        for column, values in (
            ("chunk_id", sorted(set(chunk_ids))),
            ("paper_id", sorted(set(paper_ids))),
        ):
            for offset in range(0, len(values), SQLITE_VALUE_BATCH_SIZE):
                batch = values[offset : offset + SQLITE_VALUE_BATCH_SIZE]
                placeholders = ",".join("?" for _ in batch)
                query = (
                    "SELECT source_filename, line_number, chunk_id, paper_id, chunk_json, "
                    "chunk_json_sha256 "
                    f"FROM chunk_records WHERE {column} IN ({placeholders})"
                )
                for row in connection.execute(query, batch):
                    (
                        source_filename,
                        line_number,
                        chunk_id,
                        paper_id,
                        chunk_json,
                        chunk_json_sha256,
                    ) = row
                    key = (str(source_filename), int(line_number))
                    rows[key] = (
                        key[0],
                        key[1],
                        str(chunk_id),
                        str(paper_id),
                        str(chunk_json),
                        str(chunk_json_sha256),
                    )
        returned_chunk_ids = {row[2] for row in rows.values()}
        missing_chunk_ids = set(chunk_ids) - returned_chunk_ids
        if missing_chunk_ids:
            raise RetrievalIndexError(
                "Candidate record store does not cover every FTS chunk candidate"
            )
        expected_paper_counts: dict[str, int] = dict.fromkeys(paper_ids, 0)
        requested_papers = sorted(set(paper_ids))
        for offset in range(0, len(requested_papers), SQLITE_VALUE_BATCH_SIZE):
            batch = requested_papers[offset : offset + SQLITE_VALUE_BATCH_SIZE]
            placeholders = ",".join("?" for _ in batch)




            for paper_id, count in connection.execute(
                "SELECT paper_id, count(*) FROM chunk_records "
                f"WHERE paper_id IN ({placeholders}) GROUP BY paper_id",
                batch,
            ):
                expected_paper_counts[str(paper_id)] = int(count)
        returned_paper_counts = {
            paper_id: sum(row[3] == paper_id for row in rows.values())
            for paper_id in paper_ids
        }
        if returned_paper_counts != expected_paper_counts:
            raise RetrievalIndexError(
                "Candidate record store paper coverage disagrees with the FTS index"
            )
    except sqlite3.DatabaseError as exc:
        raise RetrievalIndexError(
            f"Could not load candidate records from retrieval index {index_path}: {exc}"
        ) from exc
    finally:
        connection.close()

    chunks_root = Path(chunks_dir).resolve()
    records: list[tuple[str, int, dict]] = []
    for (
        source_filename,
        line_number,
        stored_chunk_id,
        stored_paper_id,
        chunk_json,
        stored_json_sha256,
    ) in sorted(rows.values()):
        if Path(source_filename).name != source_filename or not source_filename.endswith(
            ".jsonl"
        ):
            raise RetrievalIndexError(
                f"Invalid candidate source filename in retrieval index: {source_filename!r}"
            )
        try:
            chunk = json.loads(chunk_json)
        except json.JSONDecodeError as exc:
            raise RetrievalIndexError(
                f"Invalid candidate JSON in retrieval index for {source_filename}:{line_number}"
            ) from exc
        if not isinstance(chunk, dict) or line_number < 1:
            raise RetrievalIndexError(
                f"Invalid candidate record in retrieval index for {source_filename}:{line_number}"
            )
        if hashlib.sha256(chunk_json.encode("utf-8")).hexdigest() != stored_json_sha256:
            raise RetrievalIndexError(
                "Candidate record content digest disagrees with the retrieval index for "
                f"{source_filename}:{line_number}"
            )
        raw_chunk_id = str(chunk.get("chunk_id") or "")
        raw_paper_id = str(chunk.get("paper_id") or Path(source_filename).stem)
        if (raw_chunk_id and raw_chunk_id != stored_chunk_id) or (
            raw_paper_id != stored_paper_id
        ):
            raise RetrievalIndexError(
                "Candidate record identifiers disagree with the retrieval index for "
                f"{source_filename}:{line_number}"
            )
        records.append((str(chunks_root / source_filename), line_number, chunk))
    return tuple(records)


def strong_corpus_file_manifest(
    chunks_dir: Path, papers_tsv: Path
) -> tuple[CorpusFileRecord, ...]:
    """Hash the complete retrieval corpus and reject concurrent mutation."""

    chunks_dir = Path(chunks_dir).resolve()
    papers_tsv = Path(papers_tsv).resolve()
    before = current_corpus_fast_state(chunks_dir, papers_tsv)
    paths = _corpus_file_paths(chunks_dir, papers_tsv)
    worker_count = min(STRONG_MANIFEST_MAX_WORKERS, len(paths))
    with ThreadPoolExecutor(
        max_workers=max(1, worker_count),
        thread_name_prefix="pfempire-corpus-hash",
    ) as executor:
        records = tuple(
            executor.map(_strong_file_record_from_path_entry, paths)
        )
    after = current_corpus_fast_state(chunks_dir, papers_tsv)
    if before != after or after != manifest_fast_state(records):
        raise RetrievalIndexError(
            "Corpus changed while its retrieval-index content digest was computed"
        )
    return records


def _strong_file_record_from_path_entry(
    entry: tuple[str, str, Path],
) -> CorpusFileRecord:
    """Hash one ordered manifest entry for bounded executor mapping."""

    return _strong_file_record(*entry)


def corpus_content_digest(records: tuple[CorpusFileRecord, ...]) -> str:
    payload = [
        (row.kind, row.filename, row.byte_count, row.sha256, row.row_count)
        for row in records
    ]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def current_corpus_fast_state(
    chunks_dir: Path, papers_tsv: Path
) -> tuple[tuple[str, str, int, int, int], ...]:
    return tuple(
        _fast_file_state(kind, filename, path)
        for kind, filename, path in _corpus_file_paths(
            Path(chunks_dir).resolve(), Path(papers_tsv).resolve()
        )
    )


def manifest_fast_state(
    records: tuple[CorpusFileRecord, ...],
) -> tuple[tuple[str, str, int, int, int], ...]:
    return tuple(
        (row.kind, row.filename, row.byte_count, row.mtime_ns, row.ctime_ns)
        for row in records
    )


def paper_metadata_record_rows(
    metadata: dict[str, dict[str, str]],
) -> list[tuple[str, str, str]]:
    rows = []
    for paper_id, record in sorted(metadata.items()):
        record_json = json.dumps(
            record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        rows.append(
            (
                paper_id,
                record_json,
                hashlib.sha256(record_json.encode("utf-8")).hexdigest(),
            )
        )
    return rows


def _corpus_file_paths(
    chunks_dir: Path, papers_tsv: Path
) -> tuple[tuple[str, str, Path], ...]:
    chunk_paths = [
        ("chunk", path.name, path)
        for path in sorted(chunks_dir.glob("*.jsonl"))
    ]
    metadata_paths = [
        papers_tsv,
        papers_tsv.with_name("corpus_manifest.tsv"),
        papers_tsv.with_name("paper_registry.tsv"),
        papers_tsv.with_name("metadata_corrections.tsv"),
    ]
    return tuple(
        sorted(
            chunk_paths
            + [("metadata", path.name, path) for path in metadata_paths],
            key=lambda row: (row[0], row[1]),
        )
    )


def _fast_file_state(
    kind: str, filename: str, path: Path
) -> tuple[str, str, int, int, int]:
    if not path.exists():
        return (kind, filename, -1, 0, 0)
    stat = path.stat()
    return (kind, filename, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _strong_file_record(kind: str, filename: str, path: Path) -> CorpusFileRecord:
    if not path.exists():
        return CorpusFileRecord(kind, filename, -1, 0, 0, "missing", 0)
    if path.is_symlink() or not path.is_file():
        raise RetrievalIndexError(f"Corpus input is not a regular file: {path}")
    before = path.stat()
    digest = hashlib.sha256()
    row_count = 0
    with path.open("rb") as handle:
        for line in handle:
            digest.update(line)
            if kind == "chunk" and line.strip():
                row_count += 1
    after = path.stat()
    before_state = (
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_state = (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    if before_state != after_state:
        raise RetrievalIndexError(f"Corpus input changed while hashing: {path}")
    return CorpusFileRecord(
        kind,
        filename,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        digest.hexdigest(),
        row_count,
    )


def ensure_current_index(index_path: Path, chunks_dir: Path, papers_tsv: Path) -> str:
    index_path = Path(index_path).resolve()
    chunks_dir = Path(chunks_dir).resolve()
    papers_tsv = Path(papers_tsv).resolve()
    if not index_path.exists():
        raise RetrievalIndexError(
            f"Retrieval index missing: {index_path}. Run: python scripts/build_retrieval_index.py"
        )
    identity_before = _index_identity(index_path)
    connection = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    try:
        metadata = dict(connection.execute("SELECT key, value FROM index_meta"))
        manifest_rows = connection.execute(
            "SELECT kind, filename, byte_count, mtime_ns, ctime_ns, sha256, row_count "
            "FROM corpus_file_manifest ORDER BY kind, filename"
        ).fetchall() if metadata.get("schema_version") == INDEX_SCHEMA_VERSION else []
    except sqlite3.DatabaseError as exc:
        raise RetrievalIndexError(f"Invalid retrieval index {index_path}: {exc}") from exc
    finally:
        connection.close()
    if _index_identity(index_path) != identity_before:
        raise RetrievalIndexError("Retrieval index changed while it was validated")

    if metadata.get("schema_version") == INDEX_SCHEMA_VERSION:
        stored_manifest = tuple(CorpusFileRecord(*row) for row in manifest_rows)
        stored_content_digest = str(metadata.get("corpus_content_digest") or "")
        if (
            not stored_manifest
            or not stored_content_digest
            or corpus_content_digest(stored_manifest) != stored_content_digest
        ):
            raise RetrievalIndexError(
                "Retrieval index has an invalid strong corpus manifest; rebuild it"
            )
        current_fast_state = current_corpus_fast_state(chunks_dir, papers_tsv)
        _verify_strong_corpus_state_cached(
            str(index_path),
            identity_before,
            stored_content_digest,
            tuple(
                (
                    row.kind,
                    row.filename,
                    row.byte_count,
                    row.sha256,
                    row.row_count,
                )
                for row in stored_manifest
            ),
            current_fast_state,
            str(chunks_dir),
            str(papers_tsv),
        )
        if current_corpus_fast_state(chunks_dir, papers_tsv) != current_fast_state:
            raise RetrievalIndexError(
                "Corpus changed while the retrieval index was validated"
            )
        if _index_identity(index_path) != identity_before:
            raise RetrievalIndexError("Retrieval index changed while it was validated")
        return stored_content_digest




    expected = corpus_fingerprint(chunks_dir, papers_tsv)
    if str(metadata.get("corpus_fingerprint") or "") != expected:
        raise RetrievalIndexError(
            "Retrieval index is stale for the current chunks/metadata. "
            "Run: python scripts/build_retrieval_index.py"
        )
    return expected


@lru_cache(maxsize=8)
def _verify_strong_corpus_state_cached(
    index_path_text: str,
    index_identity: tuple[int, int, int, int, int],
    expected_content_digest: str,
    expected_files: tuple[tuple[str, str, int, str, int], ...],
    current_fast_state: tuple[tuple[str, str, int, int, int], ...],
    chunks_dir_text: str,
    papers_tsv_text: str,
) -> str:
    """Strongly attest an exact corpus/index state once, including across restarts."""

    expected_inventory = tuple((row[0], row[1], row[2]) for row in expected_files)
    current_inventory = tuple((row[0], row[1], row[2]) for row in current_fast_state)
    if current_inventory != expected_inventory:
        raise RetrievalIndexError(
            "Retrieval index is stale for the current corpus inventory; rebuild it"
        )
    index_path = Path(index_path_text)
    chunks_dir = Path(chunks_dir_text)
    papers_tsv = Path(papers_tsv_text)
    if _persistent_strong_attestation_matches(
        index_path,
        index_identity,
        chunks_dir,
        papers_tsv,
        expected_content_digest,
        current_fast_state,
    ):
        return expected_content_digest
    observed = strong_corpus_file_manifest(
        chunks_dir, papers_tsv
    )
    observed_files = tuple(
        (row.kind, row.filename, row.byte_count, row.sha256, row.row_count)
        for row in observed
    )
    if (
        observed_files != expected_files
        or corpus_content_digest(observed) != expected_content_digest
    ):
        raise RetrievalIndexError(
            "Retrieval index is stale for the current corpus content; rebuild it"
        )
    _store_persistent_strong_attestation(
        index_path,
        chunks_dir,
        papers_tsv,
        expected_content_digest,
        current_fast_state,
        expected_index_identity=index_identity,
    )
    return expected_content_digest


def _persistent_attestation_path() -> Path | None:
    """Return the explicitly configured runtime cache path, if it is safe to use."""

    raw = str(os.environ.get("PFEMPIRE_INDEX_ATTESTATION_CACHE") or "").strip()
    deployment = str(os.environ.get("PFEMPIRE_DEPLOYMENT") or "").strip()
    runtime_dir = str(os.environ.get("PFEMPIRE_RUNTIME_DIR") or "").strip()
    if not raw and deployment in {"managed-server", "gaia-studio"}:
        if runtime_dir:
            raw = str(Path(runtime_dir).expanduser() / RUNTIME_ATTESTATION_FILENAME)
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return None
    if deployment in {"managed-server", "gaia-studio"}:
        if not runtime_dir or path.name != RUNTIME_ATTESTATION_FILENAME:
            return None
        try:
            runtime_root = Path(runtime_dir).expanduser().resolve(strict=False)
            if not path.resolve(strict=False).is_relative_to(runtime_root):
                return None
        except (OSError, RuntimeError):
            return None
    return path


def _candidate_store_attestation_path() -> Path | None:
    base = _persistent_attestation_path()
    if base is None:
        return None
    return base.with_name(CANDIDATE_STORE_ATTESTATION_FILENAME)


def _candidate_record_store_attestation_matches(
    index_path: Path,
    index_identity: tuple[int, int, int, int, int],
    metadata: dict[str, str],
    *,
    chunks_dir: Path | None,
    papers_tsv: Path | None,
) -> bool:
    cache_path = _candidate_store_attestation_path()
    if (
        cache_path is None
        or chunks_dir is None
        or papers_tsv is None
        or cache_path.is_symlink()
        or not _persistent_attestation_target_is_safe(
            cache_path, index_path, Path(chunks_dir), Path(papers_tsv)
        )
        or not cache_path.is_file()
    ):
        return False
    try:
        if cache_path.stat().st_size > 16 * 1024:
            return False
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    expected = _candidate_record_store_attestation_payload(
        index_path,
        index_identity,
        metadata,
    )
    return isinstance(payload, dict) and payload == expected


def _candidate_record_store_attestation_payload(
    index_path: Path,
    index_identity: tuple[int, int, int, int, int],
    metadata: dict[str, str],
) -> dict[str, object]:
    return {
        "schema_version": CANDIDATE_STORE_ATTESTATION_SCHEMA,
        "index_path": str(index_path),
        "index_identity": list(index_identity),
        "index_schema_version": str(metadata.get("schema_version") or ""),
        "candidate_record_store_version": str(
            metadata.get("candidate_record_store_version") or ""
        ),
        "chunk_count": int(metadata.get("chunk_count") or -1),
        "candidate_record_count": int(
            metadata.get("candidate_record_count") or -1
        ),
        "full_parity_validated": True,
    }


def _store_candidate_record_store_attestation(
    index_path: Path,
    chunks_dir: Path | None,
    papers_tsv: Path | None,
    *,
    chunk_count: int,
    candidate_record_count: int,
    expected_index_identity: tuple[int, int, int, int, int] | None = None,
) -> None:
    """Atomically record complete parity for one exact immutable index state."""

    cache_path = _candidate_store_attestation_path()
    if (
        cache_path is None
        or chunks_dir is None
        or papers_tsv is None
        or cache_path.is_symlink()
        or not _persistent_attestation_target_is_safe(
            cache_path, index_path, Path(chunks_dir), Path(papers_tsv)
        )
    ):
        return
    if chunk_count < 0 or candidate_record_count != chunk_count:
        return
    try:
        index_path = Path(index_path).resolve()
        index_identity = _index_identity(index_path)
        if expected_index_identity is not None and index_identity != expected_index_identity:
            return
        metadata = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "candidate_record_store_version": CANDIDATE_RECORD_STORE_VERSION,
            "chunk_count": str(chunk_count),
            "candidate_record_count": str(candidate_record_count),
        }
        payload = _candidate_record_store_attestation_payload(
            index_path,
            index_identity,
            metadata,
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_name(
            f".{cache_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, cache_path)
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        return


def _fast_state_digest(
    state: tuple[tuple[str, str, int, int, int], ...],
) -> str:
    return hashlib.sha256(
        json.dumps(state, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _persistent_strong_attestation_matches(
    index_path: Path,
    index_identity: tuple[int, int, int, int, int],
    chunks_dir: Path,
    papers_tsv: Path,
    expected_content_digest: str,
    current_fast_state: tuple[tuple[str, str, int, int, int], ...],
) -> bool:
    """Accept only a prior strong attestation for this exact filesystem state."""

    cache_path = _persistent_attestation_path()
    if (
        cache_path is None
        or not _persistent_attestation_target_is_safe(
            cache_path, index_path, chunks_dir, papers_tsv
        )
        or cache_path.is_symlink()
        or not cache_path.is_file()
    ):
        return False
    try:
        if cache_path.stat().st_size > 16 * 1024:
            return False
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    expected = {
        "schema_version": PERSISTENT_ATTESTATION_SCHEMA,
        "index_path": str(index_path),
        "index_identity": list(index_identity),
        "chunks_dir": str(chunks_dir),
        "papers_tsv": str(papers_tsv),
        "corpus_content_digest": expected_content_digest,
        "corpus_fast_state_digest": _fast_state_digest(current_fast_state),
        "corpus_file_count": len(current_fast_state),
    }
    return payload == expected


def _store_persistent_strong_attestation(
    index_path: Path,
    chunks_dir: Path,
    papers_tsv: Path,
    content_digest: str,
    fast_state: tuple[tuple[str, str, int, int, int], ...],
    *,
    expected_index_identity: tuple[int, int, int, int, int] | None = None,
) -> None:
    """Persist a receipt only when a strong check/build still matches exact state.

    This cache is only an optimization.  Any ambiguity, concurrent mutation, or
    write failure leaves the normal full-content validation path in force.
    """

    cache_path = _persistent_attestation_path()
    if cache_path is None or not _persistent_attestation_target_is_safe(
        cache_path, index_path, chunks_dir, papers_tsv
    ):
        return
    try:
        index_identity = _index_identity(index_path)
        if expected_index_identity is not None and index_identity != expected_index_identity:
            return
        if current_corpus_fast_state(chunks_dir, papers_tsv) != fast_state:
            return
        payload = {
            "schema_version": PERSISTENT_ATTESTATION_SCHEMA,
            "index_path": str(index_path),
            "index_identity": list(index_identity),
            "chunks_dir": str(chunks_dir),
            "papers_tsv": str(papers_tsv),
            "corpus_content_digest": content_digest,
            "corpus_fast_state_digest": _fast_state_digest(fast_state),
            "corpus_file_count": len(fast_state),
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_name(
            f".{cache_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, cache_path)
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        return


def _persistent_attestation_target_is_safe(
    cache_path: Path,
    index_path: Path,
    chunks_dir: Path,
    papers_tsv: Path,
) -> bool:
    """Reject configuration that could overwrite an index or corpus input."""

    try:
        target = cache_path.resolve(strict=False)
        index = index_path.resolve(strict=False)
        chunks = chunks_dir.resolve(strict=False)
        protected_inputs = {
            path.resolve(strict=False)
            for _kind, _filename, path in _corpus_file_paths(chunks, papers_tsv)
        }
    except (OSError, RuntimeError):
        return False
    if target == index or target in protected_inputs:
        return False
    return not target.is_relative_to(chunks)


def _index_identity(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def build_fts_query(query: str) -> str:
    base_terms = [
        term
        for term in query_tokens(query)
        if len(term) >= 3 and term not in QUESTION_NOISE
    ]
    terms = [term for term in base_terms if term not in COMMON_TERMS] or base_terms
    unique_terms = list(dict.fromkeys(terms))[:24]
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in unique_terms)


def corpus_fingerprint(chunks_dir: Path, papers_tsv: Path) -> str:
    chunks_dir = Path(chunks_dir).resolve()
    papers_tsv = Path(papers_tsv).resolve()
    chunk_state = []
    for path in sorted(chunks_dir.glob("*.jsonl")):
        chunk_state.append(_portable_file_state(path))
    metadata_paths = (
        papers_tsv,
        papers_tsv.with_name("corpus_manifest.tsv"),
        papers_tsv.with_name("paper_registry.tsv"),
        papers_tsv.with_name("metadata_corrections.tsv"),
    )
    payload = {
        "chunks": chunk_state,
        "metadata": [_portable_file_state(path) for path in metadata_paths],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _portable_file_state(path: Path) -> tuple[str, int, int]:
    """Return state preserved across old rsync and heterogeneous filesystems."""
    if not path.exists():
        return (path.name, 0, 0)
    stat = path.stat()
    return (path.name, stat.st_size, stat.st_mtime_ns // 1_000_000_000)
