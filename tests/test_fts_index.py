from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import originrag.retrieval.fts_index as fts_index_module
from originrag.retrieval.fts_index import (
    RetrievalIndexError,
    build_fts_index,
    build_fts_query,
    corpus_fingerprint,
    ensure_current_index,
    search_chunks_bm25,
    search_index_candidates,
    update_fts_index,
)


def make_corpus(tmp_path: Path) -> tuple[Path, Path, Path]:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tdomain\ttopics\tfulltext_status\n"
        "P1\tEPCR binding in cerebral malaria\t2020\t10.1/p1\tmalaria\tvar_pfemp1\tchunked\n"
        "P2\tUnrelated immune study\t2021\t10.1/p2\tmalaria\thost_immunity\tchunked\n",
        encoding="utf-8",
    )
    rows = [
        {
            "chunk_id": "P1_C1",
            "paper_id": "P1",
            "section": "results",
            "text": "PfEMP1 binds EPCR on brain endothelial cells in cerebral malaria. " * 12,
        },
        {
            "chunk_id": "P2_C1",
            "paper_id": "P2",
            "section": "results",
            "text": "General immune observations in a longitudinal cohort. " * 12,
        },
    ]
    (chunks_dir / "chunks.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return chunks_dir, papers, tmp_path / "retrieval.sqlite"


def make_canonical_corpus(tmp_path: Path) -> tuple[Path, Path, Path]:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tdomain\ttopics\tfulltext_status\n"
        "P1\tEPCR binding in cerebral malaria\t2020\t10.1/p1\tmalaria\tvar_pfemp1\tchunked\n"
        "P2\tUnrelated immune study\t2021\t10.1/p2\tmalaria\thost_immunity\tchunked\n",
        encoding="utf-8",
    )
    for paper_id, text in (
        ("P1", "PfEMP1 binds EPCR on brain endothelial cells in cerebral malaria. " * 12),
        ("P2", "General immune observations in a longitudinal cohort. " * 12),
    ):
        (chunks_dir / f"{paper_id}.jsonl").write_text(
            json.dumps(
                {
                    "chunk_id": f"{paper_id}_C1",
                    "paper_id": paper_id,
                    "section": "results",
                    "text": text,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    return chunks_dir, papers, tmp_path / "retrieval.sqlite"


def test_parallel_strong_manifest_matches_sequential_order(tmp_path: Path) -> None:
    chunks_dir, papers, _ = make_canonical_corpus(tmp_path)
    for index in range(3, 9):
        paper_id = f"P{index}"
        (chunks_dir / f"{paper_id}.jsonl").write_text(
            json.dumps(
                {
                    "chunk_id": f"{paper_id}_C1",
                    "paper_id": paper_id,
                    "text": f"Evidence record {index}",
                }
            )
            + "\n",
            encoding="utf-8",
        )
    paths = fts_index_module._corpus_file_paths(chunks_dir, papers)
    expected = tuple(
        fts_index_module._strong_file_record(kind, filename, path)
        for kind, filename, path in paths
    )

    observed = fts_index_module.strong_corpus_file_manifest(chunks_dir, papers)

    assert observed == expected
    assert [(row.kind, row.filename) for row in observed] == sorted(
        (row.kind, row.filename) for row in observed
    )


def test_parallel_strong_manifest_uses_bounded_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, _ = make_canonical_corpus(tmp_path)
    for index in range(3, 25):
        paper_id = f"P{index:02d}"
        (chunks_dir / f"{paper_id}.jsonl").write_text(
            json.dumps(
                {
                    "chunk_id": f"{paper_id}_C1",
                    "paper_id": paper_id,
                    "text": f"Evidence record {index}",
                }
            )
            + "\n",
            encoding="utf-8",
        )

    real_executor = fts_index_module.ThreadPoolExecutor
    real_hash = fts_index_module._strong_file_record_from_path_entry
    configured_workers: list[int] = []
    active = 0
    peak = 0
    lock = threading.Lock()

    def recording_executor(*, max_workers: int, thread_name_prefix: str):
        configured_workers.append(max_workers)
        return real_executor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )

    def observed_hash(entry: tuple[str, str, Path]):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.02)
            return real_hash(entry)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(fts_index_module, "ThreadPoolExecutor", recording_executor)
    monkeypatch.setattr(
        fts_index_module,
        "_strong_file_record_from_path_entry",
        observed_hash,
    )

    fts_index_module.strong_corpus_file_manifest(chunks_dir, papers)

    assert configured_workers == [fts_index_module.STRONG_MANIFEST_MAX_WORKERS]
    assert 1 < peak <= fts_index_module.STRONG_MANIFEST_MAX_WORKERS


@pytest.mark.parametrize("operation", ["mutate", "add", "remove"])
def test_parallel_strong_manifest_rejects_inventory_or_content_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    chunks_dir, papers, _ = make_canonical_corpus(tmp_path)
    real_hash = fts_index_module._strong_file_record_from_path_entry
    changed = False
    lock = threading.Lock()

    def hash_then_change(entry: tuple[str, str, Path]):
        nonlocal changed
        result = real_hash(entry)
        if entry[1] != "P1.jsonl":
            return result
        with lock:
            if changed:
                return result
            changed = True
            if operation == "mutate":
                entry[2].write_text(entry[2].read_text() + "\n", encoding="utf-8")
            elif operation == "add":
                (chunks_dir / "P3.jsonl").write_text("{}\n", encoding="utf-8")
            else:
                entry[2].unlink()
        return result

    monkeypatch.setattr(
        fts_index_module,
        "_strong_file_record_from_path_entry",
        hash_then_change,
    )

    with pytest.raises(RetrievalIndexError, match="Corpus changed while"):
        fts_index_module.strong_corpus_file_manifest(chunks_dir, papers)


def test_parallel_strong_manifest_propagates_worker_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, _ = make_canonical_corpus(tmp_path)
    real_hash = fts_index_module._strong_file_record_from_path_entry

    def failing_hash(entry: tuple[str, str, Path]):
        if entry[1] == "P1.jsonl":
            raise OSError("synthetic worker read failure")
        return real_hash(entry)

    monkeypatch.setattr(
        fts_index_module,
        "_strong_file_record_from_path_entry",
        failing_hash,
    )

    with pytest.raises(OSError, match="synthetic worker read failure"):
        fts_index_module.strong_corpus_file_manifest(chunks_dir, papers)


def test_build_and_query_fts_index(tmp_path: Path) -> None:
    chunks_dir, papers, index = make_corpus(tmp_path)
    result = build_fts_index(chunks_dir, papers, index)

    candidates = search_index_candidates("PfEMP1 EPCR cerebral malaria", index)

    assert result.chunk_count == 2
    assert result.paper_count == 2
    assert "P1_C1" in candidates.chunk_ids
    assert "P1" in candidates.paper_ids
    with sqlite3.connect(index) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM index_meta"))
        stored_records = int(
            connection.execute("SELECT count(*) FROM chunk_records").fetchone()[0]
        )
    assert metadata["schema_version"] == "3"
    assert metadata["candidate_record_store_version"] == "1"
    assert metadata["candidate_record_count"] == "2"
    assert stored_records == 2


def test_bm25_candidates_are_reranked_with_existing_scoring(tmp_path: Path) -> None:
    chunks_dir, papers, index = make_corpus(tmp_path)
    build_fts_index(chunks_dir, papers, index)

    results = search_chunks_bm25(
        "PfEMP1 EPCR cerebral malaria",
        chunks_dir,
        papers,
        index_path=index,
        limit=2,
        max_per_paper=1,
        require_doi=True,
    )

    assert results[0]["paper_id"] == "P1"
    assert results[0]["retrieval_backend"] == "bm25_fts5_rerank"
    assert results[0]["bm25_chunk_rank"] == 1


def test_bm25_rerank_opens_only_candidate_paper_chunk_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tfulltext_status\n"
        "P1\tUnique target study\t2024\t10.1/p1\tchunked\n"
        "P2\tUnrelated control study\t2024\t10.1/p2\tchunked\n",
        encoding="utf-8",
    )
    for paper_id, text in (
        ("P1", "uniquetargettoken mechanistic result " * 12),
        ("P2", "unrelated control observation " * 12),
    ):
        (chunks_dir / f"{paper_id}.jsonl").write_text(
            json.dumps(
                {
                    "chunk_id": f"{paper_id}_C1",
                    "paper_id": paper_id,
                    "section": "results",
                    "text": text,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    index = tmp_path / "retrieval.sqlite"
    build_fts_index(chunks_dir, papers, index)
    ensure_current_index(index, chunks_dir, papers)

    opened_jsonl: list[str] = []
    original_open = Path.open

    def tracked_open(path: Path, *args: object, **kwargs: object):
        if path.suffix == ".jsonl":
            opened_jsonl.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)

    results = search_chunks_bm25(
        "uniquetargettoken",
        chunks_dir,
        papers,
        index_path=index,
        limit=2,
        max_per_paper=1,
    )

    assert [result["paper_id"] for result in results] == ["P1"]
    assert opened_jsonl == []


def test_bm25_metadata_only_hit_does_not_force_full_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tfulltext_status\n"
        "P1\tMetadata target experiment\t2024\t10.1/p1\tchunked\n"
        "P2\tUnrelated local experiment\t2024\t10.1/p2\tchunked\n"
        "META\tMetadata target historical record\t1997\t10.1/meta\tarchived\n",
        encoding="utf-8",
    )
    for paper_id, text in (
        ("P1", "metadatatargettoken direct experimental result " * 12),
        ("P2", "unrelated local control observation " * 12),
    ):
        (chunks_dir / f"{paper_id}.jsonl").write_text(
            json.dumps(
                {
                    "chunk_id": f"{paper_id}_C1",
                    "paper_id": paper_id,
                    "section": "results",
                    "text": text,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    index = tmp_path / "retrieval.sqlite"
    build_fts_index(chunks_dir, papers, index)
    candidates = search_index_candidates("metadatatargettoken metadata target", index)
    assert "META" in candidates.paper_ids
    assert "META" not in candidates.chunk_paper_ids
    ensure_current_index(index, chunks_dir, papers)

    opened_jsonl: list[str] = []
    original_open = Path.open

    def tracked_open(path: Path, *args: object, **kwargs: object):
        if path.suffix == ".jsonl":
            opened_jsonl.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)
    optimized = search_chunks_bm25(
        "metadatatargettoken metadata target",
        chunks_dir,
        papers,
        index_path=index,
        limit=2,
        max_per_paper=1,
    )
    optimized_opened = list(opened_jsonl)

    monkeypatch.setattr(
        fts_index_module,
        "index_has_candidate_record_store",
        lambda _index_path, **_kwargs: False,
    )
    monkeypatch.setattr(
        fts_index_module,
        "index_has_canonical_per_paper_layout",
        lambda _index_path: False,
    )
    full_scan = search_chunks_bm25(
        "metadatatargettoken metadata target",
        chunks_dir,
        papers,
        index_path=index,
        limit=2,
        max_per_paper=1,
    )

    assert optimized == full_scan
    assert [result["paper_id"] for result in optimized] == ["P1"]
    assert optimized_opened == []


def test_bm25_record_store_avoids_jsonl_reads_for_mixed_chunk_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tfulltext_status\n"
        "P1\tTarget study\t2024\t10.1/p1\tchunked\n",
        encoding="utf-8",
    )
    (chunks_dir / "P1.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "P1_C1",
                "paper_id": "P1",
                "section": "results",
                "text": "uniquetargettoken first result " * 12,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (chunks_dir / "aggregate.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "P1_C2",
                "paper_id": "P1",
                "section": "results",
                "text": "uniquetargettoken second result " * 12,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    index = tmp_path / "retrieval.sqlite"
    build_fts_index(chunks_dir, papers, index)
    ensure_current_index(index, chunks_dir, papers)

    opened_jsonl: list[str] = []
    original_open = Path.open

    def tracked_open(path: Path, *args: object, **kwargs: object):
        if path.suffix == ".jsonl":
            opened_jsonl.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)

    results = search_chunks_bm25(
        "uniquetargettoken",
        chunks_dir,
        papers,
        index_path=index,
        limit=2,
        max_per_paper=2,
    )

    assert {result["chunk_id"] for result in results} == {"P1_C1", "P1_C2"}
    assert opened_jsonl == []


def test_candidate_paper_coverage_uses_covering_record_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_corpus(tmp_path)
    build_fts_index(chunks_dir, papers, index)
    assert fts_index_module.index_has_candidate_record_store(index)

    statements: list[str] = []
    real_connect = sqlite3.connect

    class RecordingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def execute(
            self, statement: str, parameters: tuple | list = ()
        ) -> sqlite3.Cursor:
            statements.append(statement)
            return self.connection.execute(statement, parameters)

        def close(self) -> None:
            self.connection.close()

    def recording_connect(*args: object, **kwargs: object) -> RecordingConnection:
        return RecordingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(fts_index_module.sqlite3, "connect", recording_connect)
    records = fts_index_module.load_index_candidate_records(
        index,
        chunks_dir,
        chunk_ids={"P1_C1"},
        paper_ids={"P2"},
        record_store_available=True,
    )

    assert records is not None
    assert {record[2]["chunk_id"] for record in records} == {"P1_C1", "P2_C1"}
    coverage_statements = [
        statement
        for statement in statements
        if "count(*)" in statement and "GROUP BY paper_id" in statement
    ]
    assert len(coverage_statements) == 1
    assert "FROM chunk_records" in coverage_statements[0]
    assert "chunk_fts" not in coverage_statements[0]

    with real_connect(index) as connection:
        query_plan = connection.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT paper_id, count(*) FROM chunk_records "
            "WHERE paper_id IN (?) GROUP BY paper_id",
            ("P2",),
        ).fetchall()
    assert any(
        "USING COVERING INDEX chunk_records_paper_id_idx" in str(row[3])
        for row in query_plan
    )


def test_bm25_record_store_matches_legacy_mixed_layout_results_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tfulltext_status\n"
        "P1\tTarget study\t2024\t10.1/p1\tchunked\n"
        "P2\tContext study\t2023\t10.1/p2\tchunked\n",
        encoding="utf-8",
    )
    rows = {
        "P1.jsonl": [
            {
                "chunk_id": "P1_C1",
                "paper_id": "P1",
                "section": "results",
                "text": "uniquetargettoken direct result " * 12,
            }
        ],
        "aggregate.jsonl": [
            {
                "chunk_id": "P1_C2",
                "paper_id": "P1",
                "section": "discussion",
                "text": "uniquetargettoken interpreted result " * 12,
            },
            {
                "chunk_id": "P2_C1",
                "paper_id": "P2",
                "section": "results",
                "text": "uniquetargettoken context result " * 12,
            },
        ],
    }
    for filename, records in rows.items():
        (chunks_dir / filename).write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
    index = tmp_path / "retrieval.sqlite"
    build_fts_index(chunks_dir, papers, index)

    indexed = search_chunks_bm25(
        "uniquetargettoken",
        chunks_dir,
        papers,
        index_path=index,
        limit=5,
        max_per_paper=2,
    )
    monkeypatch.setattr(
        fts_index_module,
        "index_has_candidate_record_store",
        lambda _index_path, **_kwargs: False,
    )
    legacy = search_chunks_bm25(
        "uniquetargettoken",
        chunks_dir,
        papers,
        index_path=index,
        limit=5,
        max_per_paper=2,
    )

    assert indexed == legacy


def test_legacy_index_without_candidate_record_store_uses_jsonl_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_corpus(tmp_path)
    build_fts_index(chunks_dir, papers, index)
    with sqlite3.connect(index) as connection:
        connection.execute("DROP TABLE chunk_records")
        connection.execute(
            "DELETE FROM index_meta WHERE key IN ("
            "'candidate_record_store_version', 'candidate_record_count')"
        )
        connection.execute(
            "UPDATE index_meta SET value='1' WHERE key='schema_version'"
        )

    opened_jsonl: list[str] = []
    original_open = Path.open

    def tracked_open(path: Path, *args: object, **kwargs: object):
        if path.suffix == ".jsonl":
            opened_jsonl.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)
    results = search_chunks_bm25(
        "PfEMP1 EPCR cerebral malaria",
        chunks_dir,
        papers,
        index_path=index,
        limit=2,
        max_per_paper=1,
        require_doi=True,
    )

    assert [result["paper_id"] for result in results] == ["P1"]
    assert opened_jsonl == ["chunks.jsonl"]


def test_incomplete_candidate_record_store_falls_back_to_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_corpus(tmp_path)
    build_fts_index(chunks_dir, papers, index)
    with sqlite3.connect(index) as connection:
        connection.execute("DELETE FROM chunk_records WHERE chunk_id='P2_C1'")
    ensure_current_index(index, chunks_dir, papers)

    opened_jsonl: list[str] = []
    original_open = Path.open

    def tracked_open(path: Path, *args: object, **kwargs: object):
        if path.suffix == ".jsonl":
            opened_jsonl.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)
    results = search_chunks_bm25(
        "PfEMP1 EPCR cerebral malaria",
        chunks_dir,
        papers,
        index_path=index,
        limit=2,
        max_per_paper=1,
        require_doi=True,
    )

    assert [result["paper_id"] for result in results] == ["P1"]
    assert opened_jsonl == ["chunks.jsonl"]


def test_candidate_record_identifier_mismatch_fails_closed(tmp_path: Path) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    build_fts_index(chunks_dir, papers, index)
    with sqlite3.connect(index) as connection:
        stored = json.loads(
            connection.execute(
                "SELECT chunk_json FROM chunk_records WHERE chunk_id='P1_C1'"
            ).fetchone()[0]
        )
        stored["chunk_id"] = "DIFFERENT_CHUNK"
        serialized = json.dumps(stored)
        connection.execute(
            "UPDATE chunk_records SET chunk_json=?, chunk_json_sha256=? "
            "WHERE chunk_id='P1_C1'",
            (serialized, hashlib.sha256(serialized.encode()).hexdigest()),
        )

    with pytest.raises(RetrievalIndexError, match="identifiers disagree"):
        search_chunks_bm25(
            "PfEMP1 EPCR cerebral malaria",
            chunks_dir,
            papers,
            index_path=index,
            limit=2,
            max_per_paper=1,
            require_doi=True,
        )


def test_incremental_index_update_adds_only_new_paper_and_refreshes_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    original = build_fts_index(chunks_dir, papers, index)
    with papers.open("a", encoding="utf-8") as handle:
        handle.write(
            "P3\tMyeloid responses in severe infection\t2024\t10.1/p3\t"
            "immunology\tmyeloid_cells\tchunked\n"
        )
    (chunks_dir / "P3.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "P3_C1",
                "paper_id": "P3",
                "text": "Dysregulated myeloid cells in severe disease.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = update_fts_index(
        chunks_dir,
        papers,
        ["P3"],
        index,
        expected_previous_fingerprint=original.corpus_fingerprint,
    )

    ensure_current_index(index, chunks_dir, papers)
    candidates = search_index_candidates("dysregulated myeloid", index)
    assert result.paper_count == 3
    assert result.chunk_count == 3
    assert result.corpus_fingerprint == corpus_fingerprint(chunks_dir, papers)
    assert "P3" in candidates.paper_ids
    assert "P3_C1" in candidates.chunk_ids
    ensure_current_index(index, chunks_dir, papers)

    opened_jsonl: list[str] = []
    original_open = Path.open

    def tracked_open(path: Path, *args: object, **kwargs: object):
        if path.suffix == ".jsonl":
            opened_jsonl.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)
    retrieved = search_chunks_bm25(
        "dysregulated myeloid",
        chunks_dir,
        papers,
        index_path=index,
        limit=2,
        max_per_paper=1,
    )
    assert [row["paper_id"] for row in retrieved] == ["P3"]
    assert opened_jsonl == []


def test_incremental_index_update_rejects_unrelated_stale_index(tmp_path: Path) -> None:
    chunks_dir, papers, index = make_corpus(tmp_path)
    build_fts_index(chunks_dir, papers, index)
    (chunks_dir / "P1.jsonl").write_text(
        json.dumps({"chunk_id": "P1_C1", "paper_id": "P1", "text": "EPCR"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RetrievalIndexError, match="did not match"):
        update_fts_index(
            chunks_dir,
            papers,
            ["P1"],
            index,
            expected_previous_fingerprint="not-the-index-fingerprint",
        )


def test_incremental_update_rejects_new_noncanonical_chunk_file(
    tmp_path: Path,
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tfulltext_status\n"
        "P1\tFirst study\t2024\t10.1/p1\tchunked\n",
        encoding="utf-8",
    )
    (chunks_dir / "P1.jsonl").write_text(
        json.dumps({"chunk_id": "P1_C1", "paper_id": "P1", "text": "first"})
        + "\n",
        encoding="utf-8",
    )
    index = tmp_path / "retrieval.sqlite"
    original = build_fts_index(chunks_dir, papers, index)
    with papers.open("a", encoding="utf-8") as handle:
        handle.write("P2\tSecond study\t2025\t10.1/p2\tchunked\n")
    (chunks_dir / "P2.jsonl").write_text(
        json.dumps({"chunk_id": "P2_C1", "paper_id": "P2", "text": "second"})
        + "\n",
        encoding="utf-8",
    )
    (chunks_dir / "aggregate.jsonl").write_text(
        json.dumps({"chunk_id": "P1_C2", "paper_id": "P1", "text": "extra"})
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RetrievalIndexError, match="unselected chunk file|canonical"):
        update_fts_index(
            chunks_dir,
            papers,
            ["P2"],
            index,
            expected_previous_fingerprint=original.corpus_fingerprint,
        )


def test_stale_index_is_rejected(tmp_path: Path) -> None:
    chunks_dir, papers, index = make_corpus(tmp_path)
    build_fts_index(chunks_dir, papers, index)
    with (chunks_dir / "chunks.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"chunk_id": "P3_C1", "paper_id": "P3", "text": "new"}) + "\n")

    with pytest.raises(RetrievalIndexError, match="stale"):
        ensure_current_index(index, chunks_dir, papers)


def test_direct_bm25_search_still_rejects_stale_index(tmp_path: Path) -> None:
    chunks_dir, papers, index = make_corpus(tmp_path)
    build_fts_index(chunks_dir, papers, index)
    with (chunks_dir / "chunks.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"chunk_id": "P3_C1", "paper_id": "P3", "text": "new"})
            + "\n"
        )

    with pytest.raises(RetrievalIndexError, match="stale"):
        search_chunks_bm25(
            "PfEMP1 EPCR",
            chunks_dir,
            papers,
            index_path=index,
        )


def test_fingerprint_ignores_subsecond_mtime_differences(tmp_path: Path) -> None:
    chunks_dir, papers, index = make_corpus(tmp_path)
    chunk = chunks_dir / "chunks.jsonl"
    whole_second = chunk.stat().st_mtime_ns // 1_000_000_000
    os.utime(chunk, ns=(whole_second * 1_000_000_000, whole_second * 1_000_000_000 + 1))
    built = build_fts_index(chunks_dir, papers, index)

    os.utime(
        chunk,
        ns=(whole_second * 1_000_000_000, whole_second * 1_000_000_000 + 999_999_999),
    )
    assert ensure_current_index(index, chunks_dir, papers) == built.corpus_content_digest


def test_same_size_mutation_with_restored_mtime_is_rejected(tmp_path: Path) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    build_fts_index(chunks_dir, papers, index)
    ensure_current_index(index, chunks_dir, papers)
    chunk = chunks_dir / "P1.jsonl"
    before = chunk.stat()
    original = chunk.read_bytes()
    mutated = original.replace(b"EPCR", b"XXXX", 1)
    assert len(mutated) == len(original)
    chunk.write_bytes(mutated)
    os.utime(chunk, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = chunk.stat()
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns

    with pytest.raises(RetrievalIndexError, match="stale.*content"):
        ensure_current_index(index, chunks_dir, papers)


def test_primed_cache_rejects_mutation_after_fast_state_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    build_fts_index(chunks_dir, papers, index)
    ensure_current_index(index, chunks_dir, papers)
    real_fast_state = fts_index_module.current_corpus_fast_state
    state_calls = 0

    def snapshot_then_mutate(chunk_path: Path, paper_path: Path):
        nonlocal state_calls
        state = real_fast_state(chunk_path, paper_path)
        state_calls += 1
        if state_calls == 1:
            chunk = chunks_dir / "P1.jsonl"
            before = chunk.stat()
            original = chunk.read_bytes()
            chunk.write_bytes(original.replace(b"EPCR", b"XXXX", 1))
            os.utime(chunk, ns=(before.st_atime_ns, before.st_mtime_ns))
        return state

    monkeypatch.setattr(
        fts_index_module, "current_corpus_fast_state", snapshot_then_mutate
    )

    with pytest.raises(RetrievalIndexError, match="changed while.*validated"):
        ensure_current_index(index, chunks_dir, papers)
    assert state_calls == 2


def test_persistent_attestation_reuses_strong_build_across_process_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    receipt = tmp_path / "runtime/retrieval-attestation.json"
    monkeypatch.setenv("PFEMPIRE_INDEX_ATTESTATION_CACHE", str(receipt))
    built = build_fts_index(chunks_dir, papers, index)
    assert receipt.is_file()
    fts_index_module._verify_strong_corpus_state_cached.cache_clear()

    def unexpected_strong_hash(*_args: object, **_kwargs: object):
        raise AssertionError("exact attested state should not be rehashed")

    monkeypatch.setattr(
        fts_index_module, "strong_corpus_file_manifest", unexpected_strong_hash
    )

    assert ensure_current_index(index, chunks_dir, papers) == built.corpus_content_digest


def test_candidate_store_attestation_reuses_full_parity_for_exact_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    receipt = tmp_path / "runtime/retrieval-attestation.json"
    candidate_receipt = receipt.with_name(
        fts_index_module.CANDIDATE_STORE_ATTESTATION_FILENAME
    )
    monkeypatch.setenv("PFEMPIRE_INDEX_ATTESTATION_CACHE", str(receipt))
    build_fts_index(chunks_dir, papers, index)
    assert candidate_receipt.is_file()

    def unexpected_full_parity(*_args: object, **_kwargs: object):
        raise AssertionError("exact attested index should not be scanned again")

    monkeypatch.setattr(
        fts_index_module,
        "_candidate_record_store_has_full_parity",
        unexpected_full_parity,
    )

    assert fts_index_module.index_has_candidate_record_store(
        index,
        chunks_dir=chunks_dir,
        papers_tsv=papers,
    )


def test_corrupt_candidate_store_attestation_falls_back_to_full_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    receipt = tmp_path / "runtime/retrieval-attestation.json"
    candidate_receipt = receipt.with_name(
        fts_index_module.CANDIDATE_STORE_ATTESTATION_FILENAME
    )
    monkeypatch.setenv("PFEMPIRE_INDEX_ATTESTATION_CACHE", str(receipt))
    build_fts_index(chunks_dir, papers, index)
    candidate_receipt.write_text("not json\n", encoding="utf-8")
    real_full_parity = fts_index_module._candidate_record_store_has_full_parity
    calls = 0

    def counted_full_parity(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return real_full_parity(*args, **kwargs)

    monkeypatch.setattr(
        fts_index_module,
        "_candidate_record_store_has_full_parity",
        counted_full_parity,
    )

    assert fts_index_module.index_has_candidate_record_store(
        index,
        chunks_dir=chunks_dir,
        papers_tsv=papers,
    )
    assert calls == 1
    assert json.loads(candidate_receipt.read_text(encoding="utf-8"))[
        "full_parity_validated"
    ] is True


def test_candidate_store_attestation_is_invalidated_by_index_identity_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    receipt = tmp_path / "runtime/retrieval-attestation.json"
    candidate_receipt = receipt.with_name(
        fts_index_module.CANDIDATE_STORE_ATTESTATION_FILENAME
    )
    monkeypatch.setenv("PFEMPIRE_INDEX_ATTESTATION_CACHE", str(receipt))
    build_fts_index(chunks_dir, papers, index)
    before = candidate_receipt.read_text(encoding="utf-8")
    with sqlite3.connect(index) as connection:
        connection.execute("CREATE TABLE identity_change_marker (value TEXT)")
    real_full_parity = fts_index_module._candidate_record_store_has_full_parity
    calls = 0

    def counted_full_parity(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return real_full_parity(*args, **kwargs)

    monkeypatch.setattr(
        fts_index_module,
        "_candidate_record_store_has_full_parity",
        counted_full_parity,
    )

    assert fts_index_module.index_has_candidate_record_store(
        index,
        chunks_dir=chunks_dir,
        papers_tsv=papers,
    )
    assert calls == 1
    assert candidate_receipt.read_text(encoding="utf-8") != before


def test_failed_candidate_store_parity_never_writes_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    receipt = tmp_path / "runtime/retrieval-attestation.json"
    candidate_receipt = receipt.with_name(
        fts_index_module.CANDIDATE_STORE_ATTESTATION_FILENAME
    )
    monkeypatch.setenv("PFEMPIRE_INDEX_ATTESTATION_CACHE", str(receipt))
    build_fts_index(chunks_dir, papers, index)
    candidate_receipt.unlink()
    with sqlite3.connect(index) as connection:
        connection.execute(
            "DELETE FROM chunk_records WHERE chunk_id = (SELECT chunk_id FROM chunk_records LIMIT 1)"
        )

    assert not fts_index_module.index_has_candidate_record_store(
        index,
        chunks_dir=chunks_dir,
        papers_tsv=papers,
    )
    assert not candidate_receipt.exists()


def test_candidate_store_attestation_never_writes_inside_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    unsafe_base = chunks_dir / "configured-attestation.json"
    candidate_receipt = chunks_dir / (
        fts_index_module.CANDIDATE_STORE_ATTESTATION_FILENAME
    )
    monkeypatch.setenv("PFEMPIRE_INDEX_ATTESTATION_CACHE", str(unsafe_base))

    build_fts_index(chunks_dir, papers, index)

    assert not unsafe_base.exists()
    assert not candidate_receipt.exists()


def test_candidate_store_attestation_never_replaces_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    receipt = tmp_path / "runtime/retrieval-attestation.json"
    receipt.parent.mkdir()
    candidate_receipt = receipt.with_name(
        fts_index_module.CANDIDATE_STORE_ATTESTATION_FILENAME
    )
    sentinel = tmp_path / "sentinel.json"
    sentinel.write_text("preserve me\n", encoding="utf-8")
    candidate_receipt.symlink_to(sentinel)
    monkeypatch.setenv("PFEMPIRE_INDEX_ATTESTATION_CACHE", str(receipt))

    build_fts_index(chunks_dir, papers, index)

    assert candidate_receipt.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "preserve me\n"


def test_persistent_attestation_never_masks_same_size_corpus_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    receipt = tmp_path / "runtime/retrieval-attestation.json"
    monkeypatch.setenv("PFEMPIRE_INDEX_ATTESTATION_CACHE", str(receipt))
    build_fts_index(chunks_dir, papers, index)
    fts_index_module._verify_strong_corpus_state_cached.cache_clear()
    chunk = chunks_dir / "P1.jsonl"
    before = chunk.stat()
    original = chunk.read_bytes()
    mutated = original.replace(b"EPCR", b"XXXX", 1)
    assert len(mutated) == len(original)
    chunk.write_bytes(mutated)
    os.utime(chunk, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert chunk.stat().st_ctime_ns != before.st_ctime_ns

    with pytest.raises(RetrievalIndexError, match="stale.*content"):
        ensure_current_index(index, chunks_dir, papers)


def test_incremental_import_refreshes_persistent_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    receipt = tmp_path / "runtime/retrieval-attestation.json"
    monkeypatch.setenv("PFEMPIRE_INDEX_ATTESTATION_CACHE", str(receipt))
    built = build_fts_index(chunks_dir, papers, index)
    first_receipt = receipt.read_text(encoding="utf-8")
    candidate_receipt = receipt.with_name(
        fts_index_module.CANDIDATE_STORE_ATTESTATION_FILENAME
    )
    first_candidate_receipt = candidate_receipt.read_text(encoding="utf-8")
    p1 = chunks_dir / "P1.jsonl"
    row = json.loads(p1.read_text(encoding="utf-8"))
    row["text"] += " Imported follow-up result."
    p1.write_text(json.dumps(row) + "\n", encoding="utf-8")
    real_full_parity = fts_index_module._candidate_record_store_has_full_parity
    parity_calls = 0

    def counted_full_parity(*args: object, **kwargs: object):
        nonlocal parity_calls
        parity_calls += 1
        return real_full_parity(*args, **kwargs)

    monkeypatch.setattr(
        fts_index_module,
        "_candidate_record_store_has_full_parity",
        counted_full_parity,
    )

    updated = update_fts_index(
        chunks_dir,
        papers,
        ["P1"],
        index,
        expected_previous_fingerprint=built.corpus_fingerprint,
    )
    assert parity_calls == 1
    assert receipt.read_text(encoding="utf-8") != first_receipt
    assert candidate_receipt.read_text(encoding="utf-8") != first_candidate_receipt
    fts_index_module._verify_strong_corpus_state_cached.cache_clear()

    def unexpected_strong_hash(*_args: object, **_kwargs: object):
        raise AssertionError("strong import validation should prime the new exact state")

    monkeypatch.setattr(
        fts_index_module, "strong_corpus_file_manifest", unexpected_strong_hash
    )

    assert ensure_current_index(index, chunks_dir, papers) == updated.corpus_content_digest


def test_corrupt_persistent_attestation_falls_back_to_strong_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    receipt = tmp_path / "runtime/retrieval-attestation.json"
    monkeypatch.setenv("PFEMPIRE_INDEX_ATTESTATION_CACHE", str(receipt))
    built = build_fts_index(chunks_dir, papers, index)
    receipt.write_text("not json\n", encoding="utf-8")
    fts_index_module._verify_strong_corpus_state_cached.cache_clear()
    real_strong_hash = fts_index_module.strong_corpus_file_manifest
    calls = 0

    def counted_strong_hash(chunk_path: Path, paper_path: Path):
        nonlocal calls
        calls += 1
        return real_strong_hash(chunk_path, paper_path)

    monkeypatch.setattr(
        fts_index_module, "strong_corpus_file_manifest", counted_strong_hash
    )

    assert ensure_current_index(index, chunks_dir, papers) == built.corpus_content_digest
    assert calls == 1
    assert json.loads(receipt.read_text(encoding="utf-8"))["schema_version"] == (
        fts_index_module.PERSISTENT_ATTESTATION_SCHEMA
    )


@pytest.mark.parametrize("protected_target", ("index", "chunk", "metadata"))
def test_persistent_attestation_never_overwrites_corpus_or_index_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    protected_target: str,
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    target = {
        "index": index,
        "chunk": chunks_dir / "P1.jsonl",
        "metadata": papers,
    }[protected_target]
    before = target.read_bytes() if target.exists() else None
    monkeypatch.setenv("PFEMPIRE_INDEX_ATTESTATION_CACHE", str(target))

    built = build_fts_index(chunks_dir, papers, index)

    if protected_target == "index":
        with sqlite3.connect(index) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert ensure_current_index(index, chunks_dir, papers) == built.corpus_content_digest
    else:
        assert target.read_bytes() == before


@pytest.mark.parametrize(
    "unsafe_target",
    ("outside_runtime", "wrong_runtime_filename"),
)
@pytest.mark.parametrize("deployment", ["managed-server", "gaia-studio"])
def test_managed_server_attestation_never_replaces_arbitrary_configured_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_target: str,
    deployment: str,
) -> None:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    chunks_dir, papers, index = make_canonical_corpus(corpus_root)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    target = (
        tmp_path / "auth.sqlite"
        if unsafe_target == "outside_runtime"
        else runtime / "web-chats.json"
    )
    target.write_bytes(b"protected runtime state")
    monkeypatch.setenv("PFEMPIRE_DEPLOYMENT", deployment)
    monkeypatch.setenv("PFEMPIRE_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("PFEMPIRE_INDEX_ATTESTATION_CACHE", str(target))

    build_fts_index(chunks_dir, papers, index)

    assert target.read_bytes() == b"protected runtime state"
    assert not (runtime / f".{target.name}.tmp").exists()


def test_incremental_update_rejects_changed_unselected_chunk(tmp_path: Path) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    built = build_fts_index(chunks_dir, papers, index)
    for paper_id in ("P1", "P2"):
        path = chunks_dir / f"{paper_id}.jsonl"
        row = json.loads(path.read_text())
        row["text"] += " changed"
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(RetrievalIndexError, match="changed unselected chunk file: P2"):
        update_fts_index(
            chunks_dir,
            papers,
            ["P1"],
            index,
            expected_previous_fingerprint=built.corpus_fingerprint,
        )
    with sqlite3.connect(index) as connection:
        stored_digest = connection.execute(
            "SELECT value FROM index_meta WHERE key='corpus_content_digest'"
        ).fetchone()[0]
    assert stored_digest == built.corpus_content_digest


def test_incremental_update_rejects_preexisting_mixed_layout(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tfulltext_status\n"
        "P1\tTarget study\t2024\t10.1/p1\tchunked\n",
        encoding="utf-8",
    )
    row = {"chunk_id": "P1_C1", "paper_id": "P1", "text": "target"}
    (chunks_dir / "P1.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    row["chunk_id"] = "P1_C2"
    (chunks_dir / "aggregate.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )
    index = tmp_path / "retrieval.sqlite"
    built = build_fts_index(chunks_dir, papers, index)

    with pytest.raises(RetrievalIndexError, match="previously verified canonical"):
        update_fts_index(
            chunks_dir,
            papers,
            ["P1"],
            index,
            expected_previous_fingerprint=built.corpus_fingerprint,
        )


def test_incremental_update_rejects_foreign_selected_row(tmp_path: Path) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    built = build_fts_index(chunks_dir, papers, index)
    path = chunks_dir / "P1.jsonl"
    row = json.loads(path.read_text())
    row["paper_id"] = "P2"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(RetrievalIndexError, match="foreign paper_id"):
        update_fts_index(
            chunks_dir,
            papers,
            ["P1"],
            index,
            expected_previous_fingerprint=built.corpus_fingerprint,
        )


def test_count_preserving_store_substitution_fails_candidate_coverage(
    tmp_path: Path,
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    build_fts_index(chunks_dir, papers, index)
    with sqlite3.connect(index) as connection:
        connection.execute(
            "UPDATE chunk_records SET chunk_id='SUBSTITUTED' WHERE chunk_id='P1_C1'"
        )

    with pytest.raises(RetrievalIndexError, match="cover every FTS chunk candidate"):
        search_chunks_bm25(
            "PfEMP1 EPCR cerebral malaria",
            chunks_dir,
            papers,
            index_path=index,
            limit=2,
            max_per_paper=1,
        )


def test_candidate_record_content_substitution_fails_digest(tmp_path: Path) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    build_fts_index(chunks_dir, papers, index)
    with sqlite3.connect(index) as connection:
        stored = json.loads(
            connection.execute(
                "SELECT chunk_json FROM chunk_records WHERE chunk_id='P1_C1'"
            ).fetchone()[0]
        )
        stored["text"] = "Substituted text with the same identifiers"
        connection.execute(
            "UPDATE chunk_records SET chunk_json=? WHERE chunk_id='P1_C1'",
            (json.dumps(stored),),
        )

    with pytest.raises(RetrievalIndexError, match="content digest disagrees"):
        search_chunks_bm25(
            "PfEMP1 EPCR cerebral malaria",
            chunks_dir,
            papers,
            index_path=index,
            limit=2,
            max_per_paper=1,
        )


def test_physically_invalid_database_is_reported_as_retrieval_error(
    tmp_path: Path,
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    build_fts_index(chunks_dir, papers, index)
    index.write_bytes(b"not a sqlite database")

    with pytest.raises(RetrievalIndexError, match="Invalid retrieval index"):
        ensure_current_index(index, chunks_dir, papers)


def test_index_build_rejects_chunk_change_after_manifest_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_dir, papers, index = make_canonical_corpus(tmp_path)
    real_load_metadata = fts_index_module.load_paper_metadata
    changed = False

    def mutate_then_load(path: Path):
        nonlocal changed
        if not changed:
            changed = True
            chunk = chunks_dir / "P1.jsonl"
            chunk.write_text(chunk.read_text() + "\n", encoding="utf-8")
        return real_load_metadata(path)

    monkeypatch.setattr(fts_index_module, "load_paper_metadata", mutate_then_load)

    with pytest.raises(RetrievalIndexError, match="changed while retrieval-index records"):
        build_fts_index(chunks_dir, papers, index)


def test_fts_query_removes_generic_noise() -> None:
    query = build_fts_query("Why might PfEMP1 bind EPCR in malaria?")

    assert '"pfemp1"' in query
    assert '"epcr"' in query
    assert '"why"' not in query


def test_fts_query_keeps_specific_gene_and_removes_vague_request_words() -> None:
    query = build_fts_query("Do you find any information about SPP1 gene signatures in whatever?")

    assert '"spp1"' in query
    assert '"gene"' in query
    assert '"signatures"' in query
    assert '"find"' not in query
    assert '"information"' not in query
    assert '"whatever"' not in query


def test_fts_query_removes_typo_for_demonstrative_from_paper_request() -> None:
    query = build_fts_query("list papers that discuss theses SPP1 signatures")

    assert '"spp1"' in query
    assert '"theses"' not in query


def test_index_build_rejects_missing_corpus_inputs(tmp_path: Path) -> None:
    with pytest.raises(RetrievalIndexError, match="Chunks directory missing"):
        build_fts_index(
            tmp_path / "missing_chunks",
            tmp_path / "missing_papers.tsv",
            tmp_path / "index.sqlite",
        )
