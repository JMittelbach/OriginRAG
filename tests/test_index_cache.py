from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

import originrag.retrieval.index_cache as cache_module
from originrag.retrieval.fts_index import build_fts_index
from originrag.retrieval.index_cache import (
    READ_CACHE_ENV,
    RetrievalIndexCacheError,
    cache_generations_path,
    configured_cache_path,
    prime_read_index_cache,
    prune_read_bundle_cache,
    read_cache_receipt_path,
    resolve_read_bundle,
    resolve_read_index,
    resolve_read_index_state,
    verify_read_bundle_cache,
)


def _make_index(tmp_path: Path) -> tuple[Path, Path, Path]:
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tfulltext_status\n"
        "P1\tEPCR binding\t2024\t10.1/p1\tchunked\n"
        "P2\tRIFIN receptor binding\t2025\t10.1/p2\tchunked\n",
        encoding="utf-8",
    )
    for paper_id, text in (("P1", "EPCR PfEMP1"), ("P2", "RIFIN KIR2DL1")):
        (chunks / f"{paper_id}.jsonl").write_text(
            json.dumps(
                {
                    "chunk_id": f"{paper_id}_C1",
                    "paper_id": paper_id,
                    "text": text,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    source = tmp_path / "source.sqlite"
    build_fts_index(chunks, papers, source)
    return chunks, papers, source


def _prime(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    chunks, papers, source = _make_index(tmp_path)
    cache = tmp_path / "local" / "retrieval.sqlite"
    prime_read_index_cache(
        source,
        chunks,
        papers,
        environ={READ_CACHE_ENV: str(cache)},
    )
    return chunks, papers, source, cache, read_cache_receipt_path(cache)


def test_disabled_cache_preserves_source_argument_without_io() -> None:
    source = Path("relative/source.sqlite")

    assert configured_cache_path(environ={}) is None
    assert resolve_read_index(source, environ={}) == source
    state = resolve_read_index_state(source, environ={})
    assert state.index_path == source
    assert state.chunks_dir is None
    assert state.papers_tsv is None
    assert state.corpus_content_digest is None
    assert state.candidate_record_store_validated is False


def test_prime_and_resolve_owner_only_atomic_cache(tmp_path: Path) -> None:
    chunks, papers, source, cache, receipt = _prime(tmp_path)

    state = resolve_read_bundle(
        source,
        chunks,
        papers,
        environ={READ_CACHE_ENV: str(cache)},
    )
    assert resolve_read_index(source, environ={READ_CACHE_ENV: str(cache)}) == state.index_path
    assert state.cache_generation_path is not None
    assert state.index_path == state.cache_generation_path / "index.sqlite"
    assert state.chunks_dir == state.cache_generation_path / "chunks"
    assert state.papers_tsv == state.cache_generation_path / "metadata/papers.tsv"
    assert (
        state.corpus_content_digest
        == json.loads(receipt.read_text(encoding="utf-8"))["corpus_content_digest"]
    )
    assert state.candidate_record_store_validated is True
    assert state.index_path.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(state.index_path.stat().st_mode) == 0o400
    assert stat.S_IMODE(state.cache_generation_path.stat().st_mode) == 0o500
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["source_index_sha256"] == payload["index_sha256"]
    assert payload["index_schema_version"] == "3"
    assert payload["candidate_record_store_version"] == "1"
    assert payload["chunk_count"] == payload["candidate_record_count"] == 2
    assert payload["candidate_parity_validated"] is True
    assert payload["sqlite_quick_check"] == "ok"
    assert not list(cache_generations_path(cache).glob("*.staging-*"))


@pytest.mark.parametrize("missing", ["generation", "receipt"])
def test_missing_configured_cache_fails_closed(tmp_path: Path, missing: str) -> None:
    chunks, papers, source, cache, receipt = _prime(tmp_path)
    state = resolve_read_bundle(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})
    if missing == "generation":
        assert state.cache_generation_path is not None
        os.chmod(state.cache_generation_path, 0o700)
        state.index_path.unlink()
        os.chmod(state.cache_generation_path, 0o500)
    else:
        receipt.unlink()

    with pytest.raises(RetrievalIndexCacheError, match="missing|partial|changed"):
        resolve_read_index(source, environ={READ_CACHE_ENV: str(cache)})


def test_cache_tamper_fails_closed(tmp_path: Path) -> None:
    chunks, papers, source, cache, _ = _prime(tmp_path)
    state = resolve_read_bundle(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})
    os.chmod(state.index_path, 0o600)
    with state.index_path.open("ab") as handle:
        handle.write(b"tamper")
    os.chmod(state.index_path, 0o400)

    with pytest.raises(RetrievalIndexCacheError, match="changed"):
        resolve_read_index(source, environ={READ_CACHE_ENV: str(cache)})


def test_corrupt_or_tampered_receipt_fails_closed(tmp_path: Path) -> None:
    _, _, source, cache, receipt = _prime(tmp_path)
    receipt.write_text("not json\n", encoding="utf-8")
    os.chmod(receipt, 0o600)
    with pytest.raises(RetrievalIndexCacheError, match="malformed"):
        resolve_read_index(source, environ={READ_CACHE_ENV: str(cache)})

    prime_read_index_cache(
        source,
        tmp_path / "chunks",
        tmp_path / "papers.tsv",
        environ={READ_CACHE_ENV: str(cache)},
    )
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["source_index_sha256"] = "0" * 64
    payload["index_sha256"] = "0" * 64
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(receipt, 0o600)
    with pytest.raises(RetrievalIndexCacheError, match="stale or inconsistent"):
        resolve_read_index(source, environ={READ_CACHE_ENV: str(cache)})


def test_source_identity_change_invalidates_cache_without_source_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, source, cache, _ = _prime(tmp_path)
    source.touch()

    monkeypatch.setattr(
        cache_module,
        "_copy_source",
        lambda *_args, **_kwargs: pytest.fail("resolve must not read the source body"),
    )
    with pytest.raises(RetrievalIndexCacheError, match="stale or inconsistent"):
        resolve_read_index(source, environ={READ_CACHE_ENV: str(cache)})


def test_runtime_resolution_is_bounded_and_skips_full_generation_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks, papers, source, cache, _ = _prime(tmp_path)
    cache_module._release_process_read_locks()
    identity_calls = 0
    real_identity = cache_module._path_identity

    def counted_identity(path: Path):
        nonlocal identity_calls
        identity_calls += 1
        return real_identity(path)

    monkeypatch.setattr(cache_module, "_path_identity", counted_identity)
    monkeypatch.setattr(
        cache_module,
        "_validate_generation_files",
        lambda *_args, **_kwargs: pytest.fail("runtime must not walk the manifest"),
    )
    monkeypatch.setattr(
        cache_module,
        "_sha256_file",
        lambda *_args, **_kwargs: pytest.fail("runtime must not hash bundle files"),
    )

    resolved = resolve_read_bundle(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})

    assert resolved.cache_generation
    assert identity_calls <= 20


def test_strong_check_hashes_files_and_detects_same_size_chunk_tamper(
    tmp_path: Path,
) -> None:
    chunks, papers, source, cache, _ = _prime(tmp_path)
    state = verify_read_bundle_cache(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})
    assert state.chunks_dir is not None
    chunk = state.chunks_dir / "P1.jsonl"
    original = chunk.read_bytes()
    replacement = original.replace(b"EPCR PfEMP1", b"XXXX XXXXXX")
    assert len(replacement) == len(original) and replacement != original
    os.chmod(chunk, 0o600)
    chunk.write_bytes(replacement)
    os.chmod(chunk, 0o400)


    assert (
        resolve_read_bundle(
            source, chunks, papers, environ={READ_CACHE_ENV: str(cache)}
        ).cache_generation
        == state.cache_generation
    )
    with pytest.raises(RetrievalIndexCacheError, match="generation file"):
        verify_read_bundle_cache(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})


def test_strong_check_reruns_sqlite_quick_check_and_candidate_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks, papers, source, cache, _ = _prime(tmp_path)
    calls: list[Path] = []
    real_validate = cache_module._validate_copied_index

    def validate(path: Path, digest: str):
        calls.append(path)
        return real_validate(path, digest)

    monkeypatch.setattr(cache_module, "_validate_copied_index", validate)

    state = verify_read_bundle_cache(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})

    assert calls == [state.index_path]


def test_strong_check_rejects_same_size_sqlite_corruption(tmp_path: Path) -> None:
    chunks, papers, source, cache, _ = _prime(tmp_path)
    state = resolve_read_bundle(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})
    original_size = state.index_path.stat().st_size

    os.chmod(state.index_path, 0o600)
    with state.index_path.open("r+b") as handle:
        handle.seek(0)
        handle.write(b"not-a-sqlite-db!")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(state.index_path, 0o400)

    assert state.index_path.stat().st_size == original_size
    with pytest.raises(RetrievalIndexCacheError, match="index|metadata|SQLite"):
        verify_read_bundle_cache(
            source,
            chunks,
            papers,
            environ={READ_CACHE_ENV: str(cache)},
        )


def test_canonical_corpus_mutation_cannot_mix_with_captured_generation(
    tmp_path: Path,
) -> None:
    chunks, papers, source, cache, _ = _prime(tmp_path)
    captured = resolve_read_bundle(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})
    assert captured.chunks_dir is not None
    assert captured.papers_tsv is not None
    old_chunk = (captured.chunks_dir / "P1.jsonl").read_text(encoding="utf-8")
    old_papers = captured.papers_tsv.read_text(encoding="utf-8")

    (chunks / "P1.jsonl").write_text(
        json.dumps({"chunk_id": "P1_C1", "paper_id": "P1", "text": "new canonical text"}) + "\n",
        encoding="utf-8",
    )
    papers.write_text(old_papers.replace("10.1/p1", "10.1/changed"), encoding="utf-8")

    resolved = resolve_read_bundle(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})

    assert resolved.cache_generation == captured.cache_generation
    assert (resolved.chunks_dir / "P1.jsonl").read_text(encoding="utf-8") == old_chunk
    assert resolved.papers_tsv.read_text(encoding="utf-8") == old_papers
    assert "new canonical text" not in old_chunk


def test_bundle_cannot_be_reused_for_different_corpus_paths(tmp_path: Path) -> None:
    chunks, papers, source, cache, _ = _prime(tmp_path)
    other_chunks = tmp_path / "other-chunks"
    other_chunks.mkdir()
    other_papers = tmp_path / "other-papers.tsv"
    other_papers.write_text(papers.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(RetrievalIndexCacheError, match="different corpus paths"):
        resolve_read_bundle(
            source,
            other_chunks,
            other_papers,
            environ={READ_CACHE_ENV: str(cache)},
        )


def test_rebuild_invalidates_then_reprime_publishes_all_new_generation(
    tmp_path: Path,
) -> None:
    chunks, papers, source, cache, _ = _prime(tmp_path)
    old = resolve_read_bundle(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})
    assert old.chunks_dir is not None
    old_text = (old.chunks_dir / "P1.jsonl").read_text(encoding="utf-8")

    (chunks / "P1.jsonl").write_text(
        json.dumps({"chunk_id": "P1_C1", "paper_id": "P1", "text": "new EPCR evidence"}) + "\n",
        encoding="utf-8",
    )
    papers.write_text(
        papers.read_text(encoding="utf-8").replace("10.1/p1", "10.1/new-p1"),
        encoding="utf-8",
    )
    build_fts_index(chunks, papers, source)

    with pytest.raises(RetrievalIndexCacheError, match="stale or inconsistent"):
        resolve_read_bundle(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})

    prime_read_index_cache(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})
    new = resolve_read_bundle(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})

    assert new.cache_generation != old.cache_generation
    assert new.cache_generation_path != old.cache_generation_path
    assert old.cache_generation_path is not None and old.cache_generation_path.is_dir()
    assert old.chunks_dir is not None
    assert (old.chunks_dir / "P1.jsonl").read_text(encoding="utf-8") == old_text
    assert new.chunks_dir is not None
    assert "new EPCR evidence" in (new.chunks_dir / "P1.jsonl").read_text(encoding="utf-8")
    assert new.papers_tsv is not None
    assert "10.1/new-p1" in new.papers_tsv.read_text(encoding="utf-8")
    assert "new EPCR evidence" not in old_text


def test_source_mutation_during_copy_fails_without_installing_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks, papers, source = _make_index(tmp_path)
    cache = tmp_path / "local" / "retrieval.sqlite"
    real_copy = cache_module._copy_source

    def mutating_copy(source_path: Path, temporary: Path, identity):
        result = real_copy(source_path, temporary, identity)
        source_path.touch()
        return result

    monkeypatch.setattr(cache_module, "_copy_source", mutating_copy)

    with pytest.raises(RetrievalIndexCacheError, match="changed"):
        prime_read_index_cache(
            source,
            chunks,
            papers,
            environ={READ_CACHE_ENV: str(cache)},
        )
    assert not cache.exists()
    assert not read_cache_receipt_path(cache).exists()
    assert not list(cache.parent.glob(".*.tmp"))


def test_failure_after_generation_rename_removes_unpublished_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks, papers, source = _make_index(tmp_path)
    cache = tmp_path / "local" / "retrieval.sqlite"

    def fail_manifest(*_args, **_kwargs):
        raise RetrievalIndexCacheError("synthetic post-rename failure")

    monkeypatch.setattr(cache_module, "_install_generation_manifest", fail_manifest)

    with pytest.raises(RetrievalIndexCacheError, match="post-rename"):
        prime_read_index_cache(
            source,
            chunks,
            papers,
            environ={READ_CACHE_ENV: str(cache)},
        )

    generations = cache_generations_path(cache)
    assert not read_cache_receipt_path(cache).exists()
    assert not [path for path in generations.iterdir() if path.is_dir()]


def test_failed_reprime_preserves_old_cache_and_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks, papers, source, cache, receipt = _prime(tmp_path)
    old = resolve_read_bundle(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})
    cache_before = old.index_path.read_bytes()
    receipt_before = receipt.read_bytes()
    (chunks / "P1.jsonl").write_text(
        json.dumps({"chunk_id": "P1_C1", "paper_id": "P1", "text": "changed EPCR"}) + "\n",
        encoding="utf-8",
    )
    build_fts_index(chunks, papers, source)

    def fail_copy(*_args, **_kwargs):
        raise RetrievalIndexCacheError("synthetic copy failure")

    monkeypatch.setattr(cache_module, "_copy_source", fail_copy)
    with pytest.raises(RetrievalIndexCacheError, match="synthetic copy failure"):
        prime_read_index_cache(
            source,
            chunks,
            papers,
            environ={READ_CACHE_ENV: str(cache)},
        )
    assert old.index_path.read_bytes() == cache_before
    assert receipt.read_bytes() == receipt_before
    assert not list(cache_generations_path(cache).glob("*.staging-*"))


def test_prune_retains_active_and_skips_all_deletion_for_live_reader(
    tmp_path: Path,
) -> None:
    chunks, papers, source, cache, _ = _prime(tmp_path)
    generation_paths: list[Path] = []
    for index in range(3):
        (chunks / "P1.jsonl").write_text(
            json.dumps(
                {
                    "chunk_id": "P1_C1",
                    "paper_id": "P1",
                    "text": f"EPCR evidence generation {index}",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        build_fts_index(chunks, papers, source)
        index_path = prime_read_index_cache(
            source, chunks, papers, environ={READ_CACHE_ENV: str(cache)}
        )
        generation_paths.append(index_path.parent)

    live = resolve_read_bundle(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})
    skipped = prune_read_bundle_cache(
        environ={READ_CACHE_ENV: str(cache)},
        retain_generations=1,
        min_age_seconds=0,
    )
    assert skipped["reader_lock_detected"] is True
    assert skipped["deleted_generations"] == []
    assert all(path.is_dir() for path in generation_paths)

    cache_module._release_process_read_locks()
    pruned = prune_read_bundle_cache(
        environ={READ_CACHE_ENV: str(cache)},
        retain_generations=1,
        min_age_seconds=0,
    )
    assert pruned["reader_lock_detected"] is False
    assert live.cache_generation_path is not None
    assert live.cache_generation_path.is_dir()
    assert len(pruned["deleted_generations"]) >= 2
    assert sum(path.is_dir() for path in generation_paths) == 1


def test_prune_aborts_before_deleting_when_active_generation_is_unsafe(
    tmp_path: Path,
) -> None:
    chunks, papers, source, cache, _ = _prime(tmp_path)
    (chunks / "P1.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "P1_C1",
                "paper_id": "P1",
                "text": "EPCR evidence in a new generation",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    build_fts_index(chunks, papers, source)
    active_index = prime_read_index_cache(
        source, chunks, papers, environ={READ_CACHE_ENV: str(cache)}
    )
    generations = [path for path in cache_generations_path(cache).iterdir() if path.is_dir()]
    assert len(generations) == 2

    cache_module._release_process_read_locks()
    os.chmod(active_index.parent, 0o700)
    with pytest.raises(RetrievalIndexCacheError, match="active generation"):
        prune_read_bundle_cache(
            environ={READ_CACHE_ENV: str(cache)},
            retain_generations=1,
            min_age_seconds=0,
        )

    assert all(path.is_dir() for path in generations)


def test_insufficient_space_fails_before_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks, papers, source = _make_index(tmp_path)
    cache = tmp_path / "local" / "retrieval.sqlite"

    class TinyFilesystem:
        f_bavail = 1
        f_frsize = 1

    monkeypatch.setattr(cache_module.os, "statvfs", lambda _path: TinyFilesystem())
    with pytest.raises(RetrievalIndexCacheError, match="at least"):
        prime_read_index_cache(
            source,
            chunks,
            papers,
            environ={READ_CACHE_ENV: str(cache)},
        )
    assert not cache.exists()


@pytest.mark.parametrize("deployment", ["managed-server", "gaia-studio"])
def test_managed_server_rejects_temporary_and_network_cache_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, deployment: str
) -> None:
    environment = {
        READ_CACHE_ENV: str(tmp_path / "retrieval.sqlite"),
        "PFEMPIRE_DEPLOYMENT": deployment,
    }
    monkeypatch.setattr(cache_module, "_filesystem_type_for_path", lambda _path: "ext4")
    with pytest.raises(RetrievalIndexCacheError, match="non-temporary"):
        configured_cache_path(environ=environment)

    monkeypatch.setattr(cache_module, "_path_is_temporary", lambda _path: False)
    monkeypatch.setattr(cache_module, "_filesystem_type_for_path", lambda _path: "ceph")
    with pytest.raises(RetrievalIndexCacheError, match="persistent local"):
        configured_cache_path(environ=environment)


def test_relative_and_symlink_cache_paths_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(RetrievalIndexCacheError, match="absolute"):
        configured_cache_path(environ={READ_CACHE_ENV: "relative/cache.sqlite"})

    real = tmp_path / "real.sqlite"
    real.write_bytes(b"not an index")
    alias = tmp_path / "alias.sqlite"
    alias.symlink_to(real)
    with pytest.raises(RetrievalIndexCacheError, match="alias or symlink|symlink"):
        configured_cache_path(environ={READ_CACHE_ENV: str(alias)})


def test_canonical_index_symlink_is_rejected_before_resolution(tmp_path: Path) -> None:
    chunks, papers, source = _make_index(tmp_path)
    alias = tmp_path / "source-alias.sqlite"
    alias.symlink_to(source)
    cache = tmp_path / "local/retrieval.sqlite"

    with pytest.raises(RetrievalIndexCacheError, match="must not be a symlink"):
        prime_read_index_cache(
            alias,
            chunks,
            papers,
            environ={READ_CACHE_ENV: str(cache)},
        )


@pytest.mark.parametrize("target_name", ["index", "receipt"])
def test_owner_only_permissions_are_required_at_runtime(tmp_path: Path, target_name: str) -> None:
    chunks, papers, source, cache, receipt = _prime(tmp_path)
    state = resolve_read_bundle(source, chunks, papers, environ={READ_CACHE_ENV: str(cache)})
    os.chmod(state.index_path if target_name == "index" else receipt, 0o644)

    with pytest.raises(RetrievalIndexCacheError, match="mode"):
        resolve_read_index(source, environ={READ_CACHE_ENV: str(cache)})
