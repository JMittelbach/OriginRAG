from __future__ import annotations

import atexit
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pfempire.retrieval.fts_index import (
    CANDIDATE_RECORD_STORE_VERSION,
    INDEX_SCHEMA_VERSION,
    CorpusFileRecord,
    RetrievalIndexError,
    corpus_content_digest,
    current_corpus_fast_state,
    ensure_current_index,
    manifest_fast_state,
    strong_corpus_file_manifest,
)


READ_CACHE_ENV = "PFEMPIRE_RETRIEVAL_INDEX_READ_CACHE"
RECEIPT_SCHEMA = "pfempire_retrieval_bundle_cache_v3"
GENERATION_MANIFEST_SCHEMA = "pfempire_retrieval_bundle_generation_v1"
RECEIPT_SUFFIX = ".receipt.json"
GENERATION_DIRECTORY_SUFFIX = ".generations"
GENERATION_MANIFEST_FILENAME = "generation.json"
LOCK_SUFFIX = ".lock"
DEFAULT_RETAIN_GENERATIONS = 2
DEFAULT_PRUNE_MIN_AGE_SECONDS = 24 * 60 * 60
MIN_FREE_BYTES = 2 * 1024**3
_NETWORK_FILESYSTEMS = {
    "9p",
    "afs",
    "ceph",
    "cifs",
    "davfs",
    "gcsfuse",
    "glusterfs",
    "gpfs",
    "lustre",
    "nfs",
    "nfs4",
    "smbfs",
    "sshfs",
}
_EPHEMERAL_FILESYSTEMS = {"ramfs", "tmpfs"}
_REQUIRED_META_KEYS = {
    "schema_version",
    "candidate_record_store_version",
    "chunk_count",
    "candidate_record_count",
    "paper_count",
    "corpus_content_digest",
}
_RECEIPT_KEYS = {
    "schema_version",
    "cache_path",
    "source_index_path",
    "source_index_identity",
    "source_index_sha256",
    "source_chunks_dir",
    "source_papers_tsv",
    "generation_name",
    "generation_identity",
    "generation_manifest_identity",
    "generation_manifest_sha256",
    "index_sha256",
    "generation_index_identity",
    "generation_chunks_identity",
    "generation_metadata_identity",
    "generation_papers_relative_path",
    "generation_papers_identity",
    "index_schema_version",
    "candidate_record_store_version",
    "chunk_count",
    "candidate_record_count",
    "paper_count",
    "corpus_content_digest",
    "sqlite_quick_check",
    "candidate_parity_validated",
    "receipt_payload_sha256",
}
_GENERATION_MANIFEST_KEYS = {
    "schema_version",
    "generation_name",
    "corpus_content_digest",
    "files",
}
_GENERATION_FILE_KEYS = {
    "kind",
    "relative_path",
    "identity",
    "sha256",
    "byte_count",
    "row_count",
}
_GENERATION_NAME_PATTERN = re.compile(r"^[0-9a-f]{16}-[0-9a-f]{32}$")
_PROCESS_READ_LOCKS: dict[str, int] = {}
_PROCESS_READ_LOCKS_PID = os.getpid()
_PROCESS_READ_LOCKS_MUTEX = threading.Lock()


class RetrievalIndexCacheError(RetrievalIndexError):
    """Raised when an explicitly configured read cache cannot be trusted."""


@dataclass(frozen=True)
class ReadIndexResolution:
    """One captured immutable retrieval bundle.

    Configured BM25 callers must use all three returned paths. Mixing the
    cached index with canonical chunks or metadata would defeat the snapshot
    boundary this object represents. Unconfigured callers receive their input
    paths and retain the established validation path.
    """

    index_path: Path
    chunks_dir: Path | None
    papers_tsv: Path | None
    corpus_content_digest: str | None
    candidate_record_store_validated: bool
    cache_generation: str | None
    cache_generation_path: Path | None
    cache_receipt_path: Path | None
    cache_receipt_sha256: str | None


def configured_cache_path(
    *,
    cache_path: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    """Resolve and safety-check the optional retrieval-index read-cache path."""

    environment = os.environ if environ is None else environ
    raw = cache_path
    if raw is None:
        raw = str(environment.get(READ_CACHE_ENV) or "").strip()
    if raw is None or not str(raw).strip():
        return None
    path = Path(str(raw).strip()).expanduser()
    if not path.is_absolute():
        raise RetrievalIndexCacheError(f"{READ_CACHE_ENV} must be an absolute expanded path")
    _reject_alias_or_symlink(path)
    deployment = str(environment.get("PFEMPIRE_DEPLOYMENT") or "").strip()
    if deployment in {"managed-server", "gaia-studio"}:
        _validate_persistent_local_location(path)
    return path


def resolve_read_index(
    source_index: Path | str,
    *,
    cache_path: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the index from a trusted bundle.

    Retrieval code should use :func:`resolve_read_bundle` and consume all three
    paths. This compatibility helper remains for index-only health checks.
    """

    return resolve_read_index_state(
        source_index,
        cache_path=cache_path,
        environ=environ,
    ).index_path


def resolve_read_index_state(
    source_index: Path | str,
    *,
    cache_path: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ReadIndexResolution:
    """Resolve an index-only view of a configured immutable bundle."""

    return _resolve_read_bundle(
        source_index,
        source_chunks_dir=None,
        source_papers_tsv=None,
        cache_path=cache_path,
        environ=environ,
    )


def resolve_read_bundle(
    source_index: Path | str,
    source_chunks_dir: Path | str,
    source_papers_tsv: Path | str,
    *,
    cache_path: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ReadIndexResolution:
    """Capture one index/chunks/metadata generation for a complete request."""

    return _resolve_read_bundle(
        source_index,
        source_chunks_dir=source_chunks_dir,
        source_papers_tsv=source_papers_tsv,
        cache_path=cache_path,
        environ=environ,
    )


def verify_read_bundle_cache(
    source_index: Path | str,
    source_chunks_dir: Path | str,
    source_papers_tsv: Path | str,
    *,
    cache_path: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ReadIndexResolution:
    """Strongly verify canonical inputs and every file in the active bundle.

    Normal requests use bounded identity and permission checks. This explicit
    maintenance check additionally hashes all copied files, reruns SQLite
    ``quick_check`` and candidate parity, and validates the canonical index
    against its corpus.
    """

    target = configured_cache_path(cache_path=cache_path, environ=environ)
    if target is None:
        raise RetrievalIndexCacheError(
            f"Cannot strongly check a read cache without {READ_CACHE_ENV}"
        )
    bundle = resolve_read_bundle(
        source_index,
        source_chunks_dir,
        source_papers_tsv,
        cache_path=target,
        environ=environ,
    )
    if (
        bundle.cache_generation_path is None
        or bundle.cache_receipt_path is None
        or bundle.chunks_dir is None
        or bundle.papers_tsv is None
    ):
        raise RetrievalIndexCacheError("Configured retrieval bundle is incomplete")
    receipt_bytes = _read_bounded_bytes(
        bundle.cache_receipt_path,
        label="retrieval-bundle cache receipt",
        max_bytes=64 * 1024,
    )
    if hashlib.sha256(receipt_bytes).hexdigest() != bundle.cache_receipt_sha256:
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle cache receipt changed during strong check"
        )
    receipt = _load_receipt_bytes(receipt_bytes)
    manifest_path = bundle.cache_generation_path / GENERATION_MANIFEST_FILENAME
    manifest_bytes = _read_bounded_bytes(
        manifest_path,
        label="retrieval-bundle generation manifest",
        max_bytes=4 * 1024 * 1024,
    )
    if hashlib.sha256(manifest_bytes).hexdigest() != str(
        receipt.get("generation_manifest_sha256") or ""
    ):
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle generation manifest digest changed"
        )
    manifest = _load_generation_manifest(manifest_bytes)
    if (
        manifest.get("generation_name") != bundle.cache_generation
        or manifest.get("corpus_content_digest") != bundle.corpus_content_digest
    ):
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle generation manifest is inconsistent"
        )
    _validate_generation_files(
        bundle.cache_generation_path,
        manifest,
        verify_content=True,
    )
    if _sha256_file(bundle.index_path) != receipt.get("index_sha256"):
        raise RetrievalIndexCacheError("Configured retrieval-bundle index content digest changed")
    _validate_copied_index(bundle.index_path, str(bundle.corpus_content_digest))
    snapshot_manifest = strong_corpus_file_manifest(
        bundle.chunks_dir,
        bundle.papers_tsv,
    )
    if corpus_content_digest(snapshot_manifest) != bundle.corpus_content_digest:
        raise RetrievalIndexCacheError("Configured retrieval-bundle corpus content digest changed")
    canonical_source = _canonical_source(Path(source_index))
    canonical_source_identity = _path_identity(canonical_source)
    if (
        _sha256_file(canonical_source) != receipt.get("source_index_sha256")
        or _path_identity(canonical_source) != canonical_source_identity
    ):
        raise RetrievalIndexCacheError(
            "Canonical retrieval index changed or disagrees with the active generation"
        )
    canonical_manifest = strong_corpus_file_manifest(
        Path(source_chunks_dir),
        Path(source_papers_tsv),
    )
    canonical_digest = corpus_content_digest(canonical_manifest)
    indexed_digest = ensure_current_index(
        Path(source_index),
        Path(source_chunks_dir),
        Path(source_papers_tsv),
    )
    if canonical_digest != bundle.corpus_content_digest or indexed_digest != canonical_digest:
        raise RetrievalIndexCacheError(
            "Configured retrieval bundle no longer matches the canonical corpus"
        )
    final_receipt = _read_bounded_bytes(
        bundle.cache_receipt_path,
        label="retrieval-bundle cache receipt",
        max_bytes=64 * 1024,
    )
    if hashlib.sha256(final_receipt).hexdigest() != bundle.cache_receipt_sha256:
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle cache receipt changed during strong check"
        )
    return bundle


def _resolve_read_bundle(
    source_index: Path | str,
    *,
    source_chunks_dir: Path | str | None,
    source_papers_tsv: Path | str | None,
    cache_path: Path | str | None,
    environ: Mapping[str, str] | None,
) -> ReadIndexResolution:
    """Resolve one complete bundle, failing closed when a cache is configured."""

    source_argument = Path(source_index)
    target = configured_cache_path(cache_path=cache_path, environ=environ)
    if target is None:
        return ReadIndexResolution(
            index_path=source_argument,
            chunks_dir=(None if source_chunks_dir is None else Path(source_chunks_dir)),
            papers_tsv=(None if source_papers_tsv is None else Path(source_papers_tsv)),
            corpus_content_digest=None,
            candidate_record_store_validated=False,
            cache_generation=None,
            cache_generation_path=None,
            cache_receipt_path=None,
            cache_receipt_sha256=None,
        )

    source = _canonical_source(source_argument)
    _acquire_process_read_lock(target)
    receipt_path = read_cache_receipt_path(target)
    _validate_runtime_file(receipt_path, "retrieval-bundle cache receipt")
    _reject_same_file(source, target, "Cache path aliases the canonical source index")
    _reject_same_file(source, receipt_path, "Receipt path aliases the canonical source index")
    if target.exists():
        _reject_existing_unsafe_target(target, "retrieval-cache anchor")

    receipt_identity = _path_identity(receipt_path)
    receipt_bytes = _read_bounded_bytes(
        receipt_path,
        label="retrieval-bundle cache receipt",
        max_bytes=64 * 1024,
    )
    receipt = _load_receipt_bytes(receipt_bytes)
    source_identity = _path_identity(source)
    if (
        receipt.get("schema_version") != RECEIPT_SCHEMA
        or receipt.get("cache_path") != str(target)
        or receipt.get("source_index_path") != str(source)
        or _identity_from_json(receipt.get("source_index_identity")) != source_identity
        or not _payload_digest_valid(receipt, "receipt_payload_sha256")
    ):
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle cache receipt is stale or inconsistent; "
            "prime the cache before starting the service"
        )

    expected_chunks = _resolve_expected_corpus_path(source_chunks_dir, directory=True)
    expected_papers = _resolve_expected_corpus_path(source_papers_tsv, directory=False)
    if (
        expected_chunks is not None and receipt.get("source_chunks_dir") != str(expected_chunks)
    ) or (expected_papers is not None and receipt.get("source_papers_tsv") != str(expected_papers)):
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle cache was primed for different corpus paths"
        )

    generation_name = str(receipt.get("generation_name") or "")
    if not _GENERATION_NAME_PATTERN.fullmatch(generation_name):
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle cache receipt has an invalid generation"
        )
    generations = cache_generations_path(target)
    generation = generations / generation_name
    manifest_path = generation / GENERATION_MANIFEST_FILENAME
    index_path = generation / "index.sqlite"
    chunks_path = generation / "chunks"
    metadata_path = generation / "metadata"
    papers_relative_text = str(receipt.get("generation_papers_relative_path") or "")
    papers_relative = Path(papers_relative_text)
    if (
        papers_relative.is_absolute()
        or ".." in papers_relative.parts
        or papers_relative.parent != Path("metadata")
        or papers_relative.as_posix() != papers_relative_text
    ):
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle receipt has an unsafe papers path"
        )
    papers_path = generation / papers_relative
    _validate_generation_directory(generations, mode=0o700, label="generation store")
    _validate_generation_directory(generation, mode=0o500, label="generation")
    if _path_identity(generation) != _identity_from_json(receipt.get("generation_identity")):
        raise RetrievalIndexCacheError("Configured retrieval-bundle generation identity changed")
    _validate_runtime_file(
        manifest_path,
        "retrieval-bundle generation manifest",
        expected_mode=0o400,
    )
    if _path_identity(manifest_path) != _identity_from_json(
        receipt.get("generation_manifest_identity")
    ):
        raise RetrievalIndexCacheError("Configured retrieval-bundle generation manifest changed")
    _validate_runtime_file(
        index_path,
        "retrieval-bundle index",
        expected_mode=0o400,
    )
    if _path_identity(index_path) != _identity_from_json(receipt.get("generation_index_identity")):
        raise RetrievalIndexCacheError("Configured retrieval-bundle index identity changed")
    _validate_generation_directory(chunks_path, mode=0o500, label="chunks directory")
    if _path_identity(chunks_path) != _identity_from_json(
        receipt.get("generation_chunks_identity")
    ):
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle chunks directory identity changed"
        )
    _validate_generation_directory(metadata_path, mode=0o500, label="metadata directory")
    if _path_identity(metadata_path) != _identity_from_json(
        receipt.get("generation_metadata_identity")
    ):
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle metadata directory identity changed"
        )
    _validate_runtime_file(
        papers_path,
        "retrieval-bundle paper metadata",
        expected_mode=0o400,
    )
    if _path_identity(papers_path) != _identity_from_json(
        receipt.get("generation_papers_identity")
    ):
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle paper metadata identity changed"
        )
    metadata = _read_index_metadata(index_path)
    if not _receipt_matches_metadata(receipt, metadata):
        raise RetrievalIndexCacheError("Configured retrieval-bundle index metadata is inconsistent")
    if (
        _path_identity(source) != source_identity
        or _path_identity(receipt_path) != receipt_identity
        or _path_identity(generation) != _identity_from_json(receipt.get("generation_identity"))
        or _path_identity(manifest_path)
        != _identity_from_json(receipt.get("generation_manifest_identity"))
        or _path_identity(index_path)
        != _identity_from_json(receipt.get("generation_index_identity"))
        or _path_identity(chunks_path)
        != _identity_from_json(receipt.get("generation_chunks_identity"))
        or _path_identity(metadata_path)
        != _identity_from_json(receipt.get("generation_metadata_identity"))
        or _path_identity(papers_path)
        != _identity_from_json(receipt.get("generation_papers_identity"))
    ):
        raise RetrievalIndexCacheError(
            "Source, generation, or receipt changed while the cache was resolved"
        )
    return ReadIndexResolution(
        index_path=index_path,
        chunks_dir=chunks_path,
        papers_tsv=papers_path,
        corpus_content_digest=str(receipt["corpus_content_digest"]),
        candidate_record_store_validated=True,
        cache_generation=generation_name,
        cache_generation_path=generation,
        cache_receipt_path=receipt_path,
        cache_receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest(),
    )


def prime_read_index_cache(
    source_index: Path | str,
    chunks_dir: Path | str,
    papers_tsv: Path | str,
    *,
    cache_path: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Atomically publish a strongly validated immutable retrieval bundle."""

    target = configured_cache_path(cache_path=cache_path, environ=environ)
    if target is None:
        raise RetrievalIndexCacheError(f"Cannot prime a read cache without {READ_CACHE_ENV}")
    source = _canonical_source(Path(source_index))
    chunks = Path(chunks_dir).expanduser().resolve(strict=True)
    papers = Path(papers_tsv).expanduser().resolve(strict=True)
    if not chunks.is_dir() or chunks.is_symlink():
        raise RetrievalIndexCacheError(
            f"Canonical chunks input is not a regular directory: {chunks}"
        )
    if not papers.is_file() or papers.is_symlink():
        raise RetrievalIndexCacheError(f"Canonical paper metadata is not a regular file: {papers}")
    receipt_path = read_cache_receipt_path(target)
    _prepare_cache_parent(target)
    _reject_existing_unsafe_target(target, "retrieval-index cache")
    _reject_existing_unsafe_target(receipt_path, "retrieval-index cache receipt")
    _reject_same_file(source, target, "Cache path aliases the canonical source index")
    _reject_same_file(source, receipt_path, "Receipt path aliases the canonical source index")
    generations = cache_generations_path(target)
    _prepare_generation_store(generations)

    source_identity = _path_identity(source)
    try:
        content_digest = ensure_current_index(source, chunks, papers)
    except (OSError, sqlite3.DatabaseError, RetrievalIndexError) as exc:
        raise RetrievalIndexCacheError(
            f"Canonical retrieval index did not match the configured corpus: {exc}"
        ) from exc
    if _path_identity(source) != source_identity:
        raise RetrievalIndexCacheError("Canonical retrieval index changed while it was validated")

    source_manifest = strong_corpus_file_manifest(chunks, papers)
    if corpus_content_digest(source_manifest) != content_digest:
        raise RetrievalIndexCacheError("Canonical corpus changed after retrieval-index validation")
    if _path_identity(source) != source_identity:
        raise RetrievalIndexCacheError(
            "Canonical retrieval index changed while its corpus was validated"
        )

    try:
        existing = resolve_read_bundle(
            source,
            chunks,
            papers,
            cache_path=target,
            environ=environ,
        )
    except RetrievalIndexCacheError:
        existing = None
    if existing is not None and existing.corpus_content_digest == content_digest:
        return existing.index_path

    total_bytes = source.stat().st_size + sum(max(0, row.byte_count) for row in source_manifest)
    _require_capacity(target.parent, total_bytes)
    generation_name = f"{content_digest[:16]}-{uuid.uuid4().hex}"
    final_generation = generations / generation_name
    staging = Path(tempfile.mkdtemp(dir=generations, prefix=f".{generation_name}.staging-"))
    os.chmod(staging, 0o700)
    staging_index = staging / "index.sqlite"
    staging_chunks = staging / "chunks"
    staging_metadata = staging / "metadata"
    staging_chunks.mkdir(mode=0o700)
    staging_metadata.mkdir(mode=0o700)
    copied_rows: list[tuple[CorpusFileRecord, Path, str]] = []
    published = False
    try:
        source_sha256 = _copy_source(source, staging_index, source_identity)
        for row in source_manifest:
            source_path = (
                chunks / row.filename if row.kind == "chunk" else papers.parent / row.filename
            )
            destination = (
                staging_chunks / row.filename
                if row.kind == "chunk"
                else staging_metadata / row.filename
            )
            if row.byte_count < 0:
                if source_path.exists():
                    raise RetrievalIndexCacheError(
                        f"Canonical corpus input appeared during cache prime: {source_path}"
                    )
                continue
            digest = _copy_manifest_file(source_path, destination, row)
            copied_rows.append((row, destination, digest))

        observed_after = strong_corpus_file_manifest(chunks, papers)
        if observed_after != source_manifest:
            raise RetrievalIndexCacheError(
                "Canonical corpus changed while its retrieval bundle was copied"
            )
        if _path_identity(source) != source_identity:
            raise RetrievalIndexCacheError(
                "Canonical retrieval index changed while its cache was validated"
            )
        copied_manifest = strong_corpus_file_manifest(
            staging_chunks,
            staging_metadata / papers.name,
        )
        copied_digest = corpus_content_digest(copied_manifest)
        metadata = _validate_copied_index(staging_index, content_digest)
        source_content_rows = tuple(
            (row.kind, row.filename, row.byte_count, row.sha256, row.row_count)
            for row in source_manifest
        )
        copied_content_rows = tuple(
            (row.kind, row.filename, row.byte_count, row.sha256, row.row_count)
            for row in copied_manifest
        )
        if copied_digest != content_digest or copied_content_rows != source_content_rows:
            raise RetrievalIndexCacheError(
                "Copied retrieval bundle disagrees with the canonical corpus digest"
            )

        for _, destination, _ in copied_rows:
            os.chmod(destination, 0o400)
        os.chmod(staging_index, 0o400)
        os.chmod(staging_chunks, 0o500)
        os.chmod(staging_metadata, 0o500)
        os.replace(staging, final_generation)
        _fsync_directory(generations)

        file_entries = [
            _generation_file_entry(
                "index",
                final_generation / "index.sqlite",
                final_generation,
                source_sha256,
                row_count=0,
            )
        ]
        for row, destination, digest in copied_rows:
            installed = final_generation / destination.relative_to(staging)
            file_entries.append(
                _generation_file_entry(
                    "papers"
                    if row.kind == "metadata" and row.filename == papers.name
                    else row.kind,
                    installed,
                    final_generation,
                    digest,
                    row_count=row.row_count,
                )
            )
        generation_manifest = {
            "schema_version": GENERATION_MANIFEST_SCHEMA,
            "generation_name": generation_name,
            "corpus_content_digest": content_digest,
            "files": sorted(file_entries, key=lambda row: row["relative_path"]),
        }
        generation_manifest_path = final_generation / GENERATION_MANIFEST_FILENAME
        _install_generation_manifest(generation_manifest_path, generation_manifest)
        os.chmod(final_generation, 0o500)
        _fsync_directory(generations)

        if _path_identity(source) != source_identity or current_corpus_fast_state(
            chunks, papers
        ) != manifest_fast_state(source_manifest):
            raise RetrievalIndexCacheError(
                "Canonical inputs changed before the retrieval bundle was published"
            )
        manifest_bytes = generation_manifest_path.read_bytes()
        payload = _receipt_payload_v3(
            target=target,
            source=source,
            source_identity=source_identity,
            source_sha256=source_sha256,
            chunks=chunks,
            papers=papers,
            generation=final_generation,
            manifest_path=generation_manifest_path,
            manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            metadata=metadata,
            index_sha256=source_sha256,
        )
        _install_receipt(
            receipt_path,
            payload,
            source=source,
            source_identity=source_identity,
        )
        published = True
    finally:
        if staging.exists():
            _remove_staging_tree(staging)
        if (
            final_generation.exists()
            and not published
            and not _receipt_points_to_generation(receipt_path, generation_name)
        ):
            _remove_private_generation_tree(final_generation, generations)
    return final_generation / "index.sqlite"


def read_cache_receipt_path(cache_path: Path | str) -> Path:
    path = Path(cache_path)
    return path.with_name(f"{path.name}{RECEIPT_SUFFIX}")


def cache_generations_path(cache_path: Path | str) -> Path:
    """Return the private immutable-generation store for a cache anchor."""

    path = Path(cache_path)
    return path.with_name(f".{path.name}{GENERATION_DIRECTORY_SUFFIX}")


def cache_lock_path(cache_path: Path | str) -> Path:
    path = Path(cache_path)
    return path.with_name(f"{path.name}{LOCK_SUFFIX}")


def prune_read_bundle_cache(
    *,
    cache_path: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    retain_generations: int = DEFAULT_RETAIN_GENERATIONS,
    min_age_seconds: int = DEFAULT_PRUNE_MIN_AGE_SECONDS,
    dry_run: bool = False,
) -> dict[str, object]:
    """Remove old inactive generations only when no reader process is active."""

    target = configured_cache_path(cache_path=cache_path, environ=environ)
    if target is None:
        raise RetrievalIndexCacheError(f"Cannot prune a read cache without {READ_CACHE_ENV}")
    if retain_generations < 1 or min_age_seconds < 0:
        raise RetrievalIndexCacheError(
            "Pruning requires retain_generations >= 1 and min_age_seconds >= 0"
        )
    descriptor = _try_exclusive_cache_lock(target)
    if descriptor is None:
        return {
            "active_generation": None,
            "deleted_generations": [],
            "dry_run": bool(dry_run),
            "reader_lock_detected": True,
            "retained_generations": [],
        }
    try:
        receipt_path = read_cache_receipt_path(target)
        _validate_runtime_file(receipt_path, "retrieval-bundle cache receipt")
        receipt_identity = _path_identity(receipt_path)
        receipt = _load_receipt_bytes(
            _read_bounded_bytes(
                receipt_path,
                label="retrieval-bundle cache receipt",
                max_bytes=64 * 1024,
            )
        )
        active = str(receipt.get("generation_name") or "")
        if (
            receipt.get("schema_version") != RECEIPT_SCHEMA
            or receipt.get("cache_path") != str(target)
            or not _payload_digest_valid(receipt, "receipt_payload_sha256")
            or not _GENERATION_NAME_PATTERN.fullmatch(active)
        ):
            raise RetrievalIndexCacheError("Cannot prune with an invalid active-generation receipt")
        generations = cache_generations_path(target)
        _validate_generation_directory(generations, mode=0o700, label="generation store")
        active_path = generations / active
        _validate_generation_directory(active_path, mode=0o500, label="active generation")
        if _path_identity(active_path) != _identity_from_json(receipt.get("generation_identity")):
            raise RetrievalIndexCacheError(
                "Cannot prune because the active-generation identity changed"
            )
        observed: list[Path] = []
        for path in generations.iterdir():
            try:
                path_stat = path.lstat()
            except OSError as exc:
                raise RetrievalIndexCacheError(
                    f"Cannot inspect retrieval-bundle generation during pruning: {path}"
                ) from exc
            if (
                stat.S_ISDIR(path_stat.st_mode)
                and path_stat.st_uid == os.getuid()
                and _GENERATION_NAME_PATTERN.fullmatch(path.name)
            ):
                observed.append(path)
        observed.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
        retained = {active, *(path.name for path in observed[:retain_generations])}
        cutoff_ns = time.time_ns() - int(min_age_seconds) * 1_000_000_000
        candidates = [
            path
            for path in observed
            if path.name not in retained and path.stat().st_mtime_ns <= cutoff_ns
        ]
        deleted = [path.name for path in candidates]
        if not dry_run:
            if _path_identity(receipt_path) != receipt_identity or _path_identity(
                active_path
            ) != _identity_from_json(receipt.get("generation_identity")):
                raise RetrievalIndexCacheError(
                    "Active generation changed before pruning could begin"
                )
            for path in candidates:
                _remove_private_generation_tree(path, generations)
        return {
            "active_generation": active,
            "deleted_generations": deleted,
            "dry_run": bool(dry_run),
            "reader_lock_detected": False,
            "retained_generations": sorted(retained),
        }
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _canonical_source(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise RetrievalIndexCacheError(
            f"Canonical retrieval index must not be a symlink: {expanded}"
        )
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise RetrievalIndexCacheError(f"Canonical retrieval index is unavailable: {path}") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise RetrievalIndexCacheError(
            f"Canonical retrieval index is not a regular file: {resolved}"
        )
    return resolved


def _receipt_points_to_generation(path: Path, generation_name: str) -> bool:
    """Conservatively detect whether a just-installed pointer names a generation."""

    try:
        payload = _load_receipt_bytes(
            _read_bounded_bytes(
                path,
                label="retrieval-bundle cache receipt",
                max_bytes=64 * 1024,
            )
        )
        return bool(
            payload.get("schema_version") == RECEIPT_SCHEMA
            and payload.get("generation_name") == generation_name
            and _payload_digest_valid(payload, "receipt_payload_sha256")
        )
    except RetrievalIndexCacheError:
        return False


def _reject_alias_or_symlink(path: Path) -> None:
    try:
        if path.resolve(strict=False) != path:
            raise RetrievalIndexCacheError(
                f"Configured retrieval-index cache path contains an alias or symlink: {path}"
            )
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            if current.is_symlink():
                raise RetrievalIndexCacheError(
                    f"Configured retrieval-index cache path contains a symlink: {current}"
                )
            if not current.exists():
                break
    except (OSError, RuntimeError) as exc:
        if isinstance(exc, RetrievalIndexCacheError):
            raise
        raise RetrievalIndexCacheError(
            f"Could not verify retrieval-index cache path safety: {path}"
        ) from exc


def _validate_persistent_local_location(path: Path) -> None:
    if _path_is_temporary(path):
        raise RetrievalIndexCacheError(
            "Retrieval-index cache must use persistent, non-temporary storage"
        )
    filesystem = _filesystem_type_for_path(path)
    if not filesystem:
        raise RetrievalIndexCacheError("Could not attest the retrieval-index cache filesystem")
    normalized = filesystem.casefold()
    if (
        normalized in _NETWORK_FILESYSTEMS
        or normalized in _EPHEMERAL_FILESYSTEMS
        or normalized.startswith("fuse.")
    ):
        raise RetrievalIndexCacheError(
            f"Retrieval-index cache must use persistent local storage, not {filesystem}"
        )


def _path_is_temporary(path: Path) -> bool:
    candidates = {Path("/tmp"), Path("/var/tmp"), Path("/private/tmp")}
    try:
        candidates.add(Path(tempfile.gettempdir()).resolve(strict=False))
        resolved = path.resolve(strict=False)
        return any(resolved == root or resolved.is_relative_to(root) for root in candidates)
    except (OSError, RuntimeError):
        return True


def _filesystem_type_for_path(path: Path) -> str | None:
    """Return the Linux mount type for the closest existing path ancestor."""

    mountinfo = Path("/proc/self/mountinfo")
    if not mountinfo.is_file():
        return None
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    try:
        candidate = candidate.resolve(strict=True)
        best: tuple[int, str] | None = None
        for line in mountinfo.read_text(encoding="utf-8").splitlines():
            left, separator, right = line.partition(" - ")
            if not separator:
                continue
            fields = left.split()
            trailing = right.split()
            if len(fields) < 5 or not trailing:
                continue
            mount_path = Path(_decode_mountinfo_path(fields[4]))
            if candidate != mount_path and not candidate.is_relative_to(mount_path):
                continue
            score = len(mount_path.parts)
            if best is None or score > best[0]:
                best = (score, trailing[0])
        return None if best is None else best[1]
    except (OSError, RuntimeError, UnicodeError):
        return None


def _decode_mountinfo_path(value: str) -> str:
    return re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def _prepare_cache_parent(target: Path) -> None:
    _reject_alias_or_symlink(target)
    try:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise RetrievalIndexCacheError(
            f"Could not create retrieval-index cache directory: {target.parent}"
        ) from exc
    _reject_alias_or_symlink(target)
    parent_stat = target.parent.stat()
    if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != os.getuid():
        raise RetrievalIndexCacheError(
            "Retrieval-index cache directory must be an owner-controlled directory"
        )
    if stat.S_IMODE(parent_stat.st_mode) & 0o022:
        raise RetrievalIndexCacheError(
            "Retrieval-index cache directory must not be group- or world-writable"
        )


def _open_cache_lock(target: Path, operation: int) -> int:
    """Open the owner-only cache lock and apply one flock operation."""

    path = cache_lock_path(target)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_uid != os.getuid():
            raise RetrievalIndexCacheError(
                f"Retrieval-bundle lock is not an owner-controlled file: {path}"
            )
        fcntl.flock(descriptor, operation)
        return descriptor
    except RetrievalIndexCacheError:
        if "descriptor" in locals():
            os.close(descriptor)
        raise
    except OSError as exc:
        if "descriptor" in locals():
            os.close(descriptor)
        raise RetrievalIndexCacheError(
            f"Could not acquire retrieval-bundle cache lock: {exc}"
        ) from exc


def _try_exclusive_cache_lock(target: Path) -> int | None:
    try:
        return _open_cache_lock(target, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except RetrievalIndexCacheError as exc:
        if isinstance(exc.__cause__, BlockingIOError):
            return None
        raise


def _acquire_process_read_lock(target: Path) -> None:
    """Hold a conservative shared lock until this process exits.

    Generation pruning takes a nonblocking exclusive lock. Retaining this
    shared descriptor therefore protects every bundle path returned to the
    process, including compatibility callers that retain only an index path.
    """

    global _PROCESS_READ_LOCKS_PID
    pid = os.getpid()
    key = str(target)
    with _PROCESS_READ_LOCKS_MUTEX:
        if _PROCESS_READ_LOCKS_PID != pid:
            for descriptor in _PROCESS_READ_LOCKS.values():
                os.close(descriptor)
            _PROCESS_READ_LOCKS.clear()
            _PROCESS_READ_LOCKS_PID = pid
        if key not in _PROCESS_READ_LOCKS:
            _PROCESS_READ_LOCKS[key] = _open_cache_lock(target, fcntl.LOCK_SH)


def _release_process_read_locks() -> None:
    """Release process-held reader locks at shutdown (and in focused tests)."""

    with _PROCESS_READ_LOCKS_MUTEX:
        for descriptor in _PROCESS_READ_LOCKS.values():
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        _PROCESS_READ_LOCKS.clear()


def _prepare_generation_store(path: Path) -> None:
    """Create or validate the private directory holding immutable generations."""

    _reject_alias_or_symlink(path)
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
    except OSError as exc:
        raise RetrievalIndexCacheError(
            f"Could not create retrieval-bundle generation store: {path}"
        ) from exc
    _validate_generation_directory(path, mode=0o700, label="generation store")


def _remove_staging_tree(path: Path) -> None:
    """Remove only a private unpublished staging tree after a failed prime."""

    try:
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_dir() and not child.is_symlink():
                os.chmod(child, 0o700)
            elif child.is_file() and not child.is_symlink():
                os.chmod(child, 0o600)
        os.chmod(path, 0o700)
        shutil.rmtree(path)
    except OSError as exc:
        raise RetrievalIndexCacheError(
            f"Could not remove unpublished retrieval-bundle staging tree: {exc}"
        ) from exc


def _remove_private_generation_tree(path: Path, generations: Path) -> None:
    """Delete exactly one validated direct child of the generation store."""

    try:
        if path.parent.resolve(strict=True) != generations.resolve(
            strict=True
        ) or not _GENERATION_NAME_PATTERN.fullmatch(path.name):
            raise RetrievalIndexCacheError(f"Refusing to remove an unsafe generation path: {path}")
        path_stat = path.lstat()
        if not stat.S_ISDIR(path_stat.st_mode) or path_stat.st_uid != os.getuid():
            raise RetrievalIndexCacheError(f"Refusing to remove an unsafe generation path: {path}")
    except OSError as exc:
        raise RetrievalIndexCacheError(
            f"Could not validate generation cleanup path: {path}"
        ) from exc
    _remove_staging_tree(path)


def _validate_generation_directory(path: Path, *, mode: int, label: str) -> None:
    _reject_alias_or_symlink(path)
    try:
        file_stat = path.stat()
    except OSError as exc:
        raise RetrievalIndexCacheError(
            f"Configured retrieval-bundle {label} is missing or unreadable: {path}"
        ) from exc
    if (
        not stat.S_ISDIR(file_stat.st_mode)
        or file_stat.st_uid != os.getuid()
        or stat.S_IMODE(file_stat.st_mode) != mode
    ):
        raise RetrievalIndexCacheError(
            f"Configured retrieval-bundle {label} must be an owner-only "
            f"directory with mode {mode:04o}: {path}"
        )


def _require_capacity(parent: Path, source_size: int) -> None:
    try:
        filesystem = os.statvfs(parent)
        available = filesystem.f_bavail * filesystem.f_frsize
    except OSError as exc:
        raise RetrievalIndexCacheError(
            "Could not determine retrieval-index cache capacity"
        ) from exc
    required = max(MIN_FREE_BYTES, source_size * 2)
    if available < required:
        raise RetrievalIndexCacheError(
            "Retrieval-index cache requires at least "
            f"{required} free bytes; only {available} are available"
        )


def _reject_existing_unsafe_target(path: Path, label: str) -> None:
    if path.is_symlink():
        raise RetrievalIndexCacheError(f"Configured {label} must not be a symlink: {path}")
    if path.exists():
        file_stat = path.stat()
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_uid != os.getuid():
            raise RetrievalIndexCacheError(
                f"Configured {label} must be an owner-controlled regular file: {path}"
            )


def _validate_runtime_file(
    path: Path,
    label: str,
    *,
    expected_mode: int = 0o600,
) -> None:
    _reject_existing_unsafe_target(path, label)
    if not path.is_file():
        raise RetrievalIndexCacheError(
            f"Configured {label} is missing; prime the cache before starting the service"
        )
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != expected_mode:
        raise RetrievalIndexCacheError(f"Configured {label} must have mode {expected_mode:04o}")


def _reject_same_file(left: Path, right: Path, message: str) -> None:
    if left == right:
        raise RetrievalIndexCacheError(message)
    try:
        if right.exists() and os.path.samefile(left, right):
            raise RetrievalIndexCacheError(message)
    except OSError as exc:
        raise RetrievalIndexCacheError(f"Could not verify cache path aliases: {exc}") from exc


def _new_same_directory_temporary(target: Path, purpose: str) -> Path:
    try:
        descriptor, raw_path = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.{purpose}.",
            suffix=".tmp",
        )
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        return Path(raw_path)
    except OSError as exc:
        raise RetrievalIndexCacheError(
            f"Could not create same-directory cache temporary: {exc}"
        ) from exc


def _copy_source(
    source: Path,
    temporary: Path,
    expected_source_identity: tuple[int, int, int, int, int],
) -> str:
    digest = hashlib.sha256()
    try:
        source_descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(source_descriptor, "rb") as source_handle, temporary.open("wb") as target:
            if _stat_identity(os.fstat(source_handle.fileno())) != expected_source_identity:
                raise RetrievalIndexCacheError(
                    "Canonical retrieval index changed before its cache copy began"
                )
            while True:
                block = source_handle.read(1024 * 1024)
                if not block:
                    break
                target.write(block)
                digest.update(block)
            if _stat_identity(os.fstat(source_handle.fileno())) != expected_source_identity:
                raise RetrievalIndexCacheError(
                    "Canonical retrieval index changed while it was copied"
                )
            target.flush()
            os.fsync(target.fileno())
            os.fchmod(target.fileno(), 0o600)
    except RetrievalIndexCacheError:
        raise
    except OSError as exc:
        raise RetrievalIndexCacheError(f"Could not copy canonical retrieval index: {exc}") from exc
    return digest.hexdigest()


def _copy_manifest_file(
    source: Path,
    destination: Path,
    expected: CorpusFileRecord,
) -> str:
    """Copy one strongly manifested corpus file without following symlinks."""

    digest = hashlib.sha256()
    row_count = 0
    try:
        before = source.stat()
        expected_state = (
            expected.byte_count,
            expected.mtime_ns,
            expected.ctime_ns,
        )
        if (
            source.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != expected_state
        ):
            raise RetrievalIndexCacheError(f"Canonical corpus input changed before copy: {source}")
        source_identity = _stat_identity(before)
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source_handle, destination.open("wb") as target_handle:
            if _stat_identity(os.fstat(source_handle.fileno())) != source_identity:
                raise RetrievalIndexCacheError(
                    f"Canonical corpus input changed before copy: {source}"
                )
            while True:
                line = source_handle.readline()
                if not line:
                    break
                target_handle.write(line)
                digest.update(line)
                if expected.kind == "chunk" and line.strip():
                    row_count += 1
            if _stat_identity(os.fstat(source_handle.fileno())) != source_identity:
                raise RetrievalIndexCacheError(
                    f"Canonical corpus input changed while copied: {source}"
                )
            target_handle.flush()
            os.fsync(target_handle.fileno())
            os.fchmod(target_handle.fileno(), 0o600)
    except RetrievalIndexCacheError:
        raise
    except OSError as exc:
        raise RetrievalIndexCacheError(
            f"Could not copy canonical corpus input {source}: {exc}"
        ) from exc
    observed = digest.hexdigest()
    if (
        observed != expected.sha256
        or destination.stat().st_size != expected.byte_count
        or row_count != expected.row_count
    ):
        raise RetrievalIndexCacheError(
            f"Copied corpus input disagrees with its strong manifest: {source}"
        )
    return observed


def _generation_file_entry(
    kind: str,
    path: Path,
    generation: Path,
    sha256: str,
    *,
    row_count: int,
) -> dict[str, object]:
    try:
        relative = path.relative_to(generation)
    except ValueError as exc:
        raise RetrievalIndexCacheError("Generation file escaped its root") from exc
    if not _is_sha256(sha256):
        raise RetrievalIndexCacheError("Generation file has an invalid SHA256")
    file_stat = path.stat()
    return {
        "kind": kind,
        "relative_path": relative.as_posix(),
        "identity": list(_stat_identity(file_stat)),
        "sha256": sha256,
        "byte_count": file_stat.st_size,
        "row_count": int(row_count),
    }


def _validate_copied_index(path: Path, expected_content_digest: str) -> dict[str, str]:
    try:
        connection = _read_only_connection(path)
        try:
            quick_rows = connection.execute("PRAGMA quick_check").fetchall()
            if quick_rows != [("ok",)]:
                raise RetrievalIndexCacheError(
                    "Copied retrieval-index cache failed SQLite quick_check"
                )
            metadata = dict(connection.execute("SELECT key, value FROM index_meta"))
            _validate_index_metadata(metadata, expected_content_digest)
            actual_records = int(
                connection.execute("SELECT count(*) FROM chunk_records").fetchone()[0]
            )
            mismatch = connection.execute(
                "WITH fts_counts AS ("
                "SELECT paper_id, count(*) AS n FROM chunk_fts GROUP BY paper_id"
                "), record_counts AS ("
                "SELECT paper_id, count(*) AS n FROM chunk_records GROUP BY paper_id"
                ") SELECT 1 FROM ("
                "SELECT paper_id, n FROM fts_counts EXCEPT "
                "SELECT paper_id, n FROM record_counts UNION ALL "
                "SELECT paper_id, n FROM record_counts EXCEPT "
                "SELECT paper_id, n FROM fts_counts) LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
    except RetrievalIndexCacheError:
        raise
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
        raise RetrievalIndexCacheError(
            f"Copied retrieval-index cache failed validation: {exc}"
        ) from exc
    expected_count = int(metadata["chunk_count"])
    if actual_records != expected_count or mismatch is not None:
        raise RetrievalIndexCacheError(
            "Copied retrieval-index cache failed candidate-record parity"
        )
    return metadata


def _validate_index_metadata(metadata: dict[str, str], content_digest: str) -> None:
    if not _REQUIRED_META_KEYS.issubset(metadata):
        raise RetrievalIndexCacheError("Retrieval-index cache metadata is incomplete")
    try:
        chunk_count = int(metadata["chunk_count"])
        record_count = int(metadata["candidate_record_count"])
        paper_count = int(metadata["paper_count"])
    except (TypeError, ValueError) as exc:
        raise RetrievalIndexCacheError("Retrieval-index cache counts are invalid") from exc
    if (
        metadata["schema_version"] != INDEX_SCHEMA_VERSION
        or metadata["candidate_record_store_version"] != CANDIDATE_RECORD_STORE_VERSION
        or chunk_count < 0
        or record_count != chunk_count
        or paper_count < 0
        or metadata["corpus_content_digest"] != content_digest
    ):
        raise RetrievalIndexCacheError(
            "Retrieval-index cache schema, store, counts, or corpus digest are invalid"
        )


def _read_index_metadata(path: Path) -> dict[str, str]:
    try:
        connection = _read_only_connection(path)
        try:
            metadata = dict(
                connection.execute(
                    "SELECT key, value FROM index_meta WHERE key IN (?, ?, ?, ?, ?, ?)",
                    tuple(sorted(_REQUIRED_META_KEYS)),
                ).fetchall()
            )
        finally:
            connection.close()
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
        raise RetrievalIndexCacheError(
            f"Configured retrieval-index cache metadata is unreadable: {exc}"
        ) from exc
    _validate_index_metadata(metadata, str(metadata.get("corpus_content_digest") or ""))
    return metadata


def _read_only_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)


def _install_receipt(
    receipt_path: Path,
    payload: dict[str, object],
    *,
    source: Path,
    source_identity: tuple[int, int, int, int, int],
) -> None:
    temporary = _new_same_directory_temporary(receipt_path, "receipt")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o600)
        if _path_identity(source) != source_identity:
            raise RetrievalIndexCacheError(
                "Source index changed before the bundle receipt was installed"
            )
        _reject_existing_unsafe_target(receipt_path, "retrieval-index cache receipt")
        os.replace(temporary, receipt_path)
        _fsync_directory(receipt_path.parent)
        if _path_identity(source) != source_identity:
            raise RetrievalIndexCacheError(
                "Source index changed while the bundle receipt was installed"
            )
    finally:
        temporary.unlink(missing_ok=True)


def _install_generation_manifest(path: Path, payload: dict[str, object]) -> None:
    temporary = _new_same_directory_temporary(path, "generation-manifest")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o400)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read_bounded_bytes(path: Path, *, label: str, max_bytes: int) -> bytes:
    try:
        size = path.stat().st_size
        if size > max_bytes:
            raise RetrievalIndexCacheError(f"Configured {label} is oversized")
        return path.read_bytes()
    except RetrievalIndexCacheError:
        raise
    except OSError as exc:
        raise RetrievalIndexCacheError(f"Configured {label} is unreadable: {exc}") from exc


def _load_receipt_bytes(raw: bytes) -> dict[str, object]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RetrievalIndexCacheError(
            f"Configured retrieval-bundle cache receipt is malformed: {exc}"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != _RECEIPT_KEYS:
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle cache receipt has an invalid schema"
        )
    return payload


def _load_generation_manifest(raw: bytes) -> dict[str, object]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RetrievalIndexCacheError(
            f"Configured retrieval-bundle generation manifest is malformed: {exc}"
        ) from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != _GENERATION_MANIFEST_KEYS
        or payload.get("schema_version") != GENERATION_MANIFEST_SCHEMA
        or not isinstance(payload.get("files"), list)
        or not all(
            isinstance(row, dict) and set(row) == _GENERATION_FILE_KEYS for row in payload["files"]
        )
    ):
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle generation manifest has an invalid schema"
        )
    return payload


def _receipt_payload_v3(
    *,
    target: Path,
    source: Path,
    source_identity: tuple[int, int, int, int, int],
    source_sha256: str,
    chunks: Path,
    papers: Path,
    generation: Path,
    manifest_path: Path,
    manifest_sha256: str,
    metadata: dict[str, str],
    index_sha256: str,
) -> dict[str, object]:
    if not all(_is_sha256(value) for value in (source_sha256, manifest_sha256, index_sha256)):
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle cache receipt has invalid SHA256 values"
        )
    payload: dict[str, object] = {
        "schema_version": RECEIPT_SCHEMA,
        "cache_path": str(target),
        "source_index_path": str(source),
        "source_index_identity": list(source_identity),
        "source_index_sha256": source_sha256,
        "source_chunks_dir": str(chunks),
        "source_papers_tsv": str(papers),
        "generation_name": generation.name,
        "generation_identity": list(_path_identity(generation)),
        "generation_manifest_identity": list(_path_identity(manifest_path)),
        "generation_manifest_sha256": manifest_sha256,
        "index_sha256": index_sha256,
        "generation_index_identity": list(_path_identity(generation / "index.sqlite")),
        "generation_chunks_identity": list(_path_identity(generation / "chunks")),
        "generation_metadata_identity": list(_path_identity(generation / "metadata")),
        "generation_papers_relative_path": f"metadata/{papers.name}",
        "generation_papers_identity": list(_path_identity(generation / "metadata" / papers.name)),
        "index_schema_version": metadata["schema_version"],
        "candidate_record_store_version": metadata["candidate_record_store_version"],
        "chunk_count": int(metadata["chunk_count"]),
        "candidate_record_count": int(metadata["candidate_record_count"]),
        "paper_count": int(metadata["paper_count"]),
        "corpus_content_digest": metadata["corpus_content_digest"],
        "sqlite_quick_check": "ok",
        "candidate_parity_validated": True,
    }
    payload["receipt_payload_sha256"] = _payload_digest(payload)
    return payload


def _payload_digest(payload: dict[str, object]) -> str:
    unsigned = dict(payload)
    unsigned.pop("receipt_payload_sha256", None)
    return hashlib.sha256(
        json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _payload_digest_valid(payload: dict[str, object], key: str) -> bool:
    value = str(payload.get(key) or "")
    return _is_sha256(value) and value == _payload_digest(payload)


def _identity_from_json(value: object) -> tuple[int, int, int, int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 5
        or not all(isinstance(item, int) for item in value)
    ):
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle cache has an invalid file identity"
        )
    return tuple(value)  # type: ignore[return-value]


def _resolve_expected_corpus_path(
    value: Path | str | None,
    *,
    directory: bool,
) -> Path | None:
    if value is None:
        return None
    try:
        resolved = Path(value).expanduser().resolve(strict=True)
    except OSError as exc:
        raise RetrievalIndexCacheError(
            f"Configured canonical corpus path is unavailable: {value}"
        ) from exc
    if directory and not resolved.is_dir():
        raise RetrievalIndexCacheError(
            f"Configured canonical chunks path is not a directory: {resolved}"
        )
    if not directory and not resolved.is_file():
        raise RetrievalIndexCacheError(
            f"Configured canonical paper metadata path is not a file: {resolved}"
        )
    return resolved


def _receipt_matches_metadata(receipt: dict[str, object], metadata: dict[str, str]) -> bool:
    try:
        return (
            receipt.get("index_schema_version") == metadata["schema_version"]
            and receipt.get("candidate_record_store_version")
            == metadata["candidate_record_store_version"]
            and int(receipt.get("chunk_count", -1)) == int(metadata["chunk_count"])
            and int(receipt.get("candidate_record_count", -1))
            == int(metadata["candidate_record_count"])
            and int(receipt.get("paper_count", -1)) == int(metadata["paper_count"])
            and receipt.get("corpus_content_digest") == metadata["corpus_content_digest"]
            and receipt.get("sqlite_quick_check") == "ok"
            and receipt.get("candidate_parity_validated") is True
        )
    except (KeyError, TypeError, ValueError):
        return False


def _validate_generation_files(
    generation: Path,
    manifest: dict[str, object],
    *,
    verify_content: bool = False,
) -> dict[str, Path]:
    rows = manifest["files"]
    assert isinstance(rows, list)
    seen: set[str] = set()
    index_paths: list[Path] = []
    paper_paths: list[Path] = []
    for row in rows:
        assert isinstance(row, dict)
        relative_text = str(row.get("relative_path") or "")
        relative = Path(relative_text)
        if (
            not relative_text
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != relative_text
            or relative_text in seen
        ):
            raise RetrievalIndexCacheError(
                "Configured retrieval-bundle generation has an unsafe file path"
            )
        seen.add(relative_text)
        kind = str(row.get("kind") or "")
        if kind not in {"index", "chunk", "metadata", "papers"}:
            raise RetrievalIndexCacheError(
                "Configured retrieval-bundle generation has an invalid file kind"
            )
        if (
            (kind == "index" and relative_text != "index.sqlite")
            or (kind == "chunk" and relative.parent != Path("chunks"))
            or (kind in {"metadata", "papers"} and relative.parent != Path("metadata"))
        ):
            raise RetrievalIndexCacheError(
                "Configured retrieval-bundle generation has an invalid layout"
            )
        path = generation / relative
        _validate_runtime_file(path, "retrieval-bundle generation file", expected_mode=0o400)
        if (
            _path_identity(path) != _identity_from_json(row.get("identity"))
            or path.stat().st_size != int(row.get("byte_count", -1))
            or not _is_sha256(str(row.get("sha256") or ""))
            or int(row.get("row_count", -1)) < 0
        ):
            raise RetrievalIndexCacheError("Configured retrieval-bundle generation file changed")
        if verify_content and _sha256_file(path) != str(row.get("sha256") or ""):
            raise RetrievalIndexCacheError(
                "Configured retrieval-bundle generation file content changed"
            )
        if kind == "index":
            index_paths.append(path)
        elif kind == "papers":
            paper_paths.append(path)

    expected_files = seen | {GENERATION_MANIFEST_FILENAME}
    observed_files = {
        path.relative_to(generation).as_posix() for path in generation.rglob("*") if path.is_file()
    }
    observed_dirs = {
        path.relative_to(generation).as_posix() for path in generation.rglob("*") if path.is_dir()
    }
    if observed_files != expected_files or observed_dirs != {"chunks", "metadata"}:
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle generation is partial or has extra files"
        )
    _validate_generation_directory(generation / "chunks", mode=0o500, label="chunks directory")
    _validate_generation_directory(generation / "metadata", mode=0o500, label="metadata directory")
    if len(index_paths) != 1 or len(paper_paths) != 1:
        raise RetrievalIndexCacheError(
            "Configured retrieval-bundle generation lacks one index or papers.tsv"
        )
    return {
        "index": index_paths[0],
        "chunks": generation / "chunks",
        "papers": paper_paths[0],
    }


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _path_identity(path: Path) -> tuple[int, int, int, int, int]:
    return _stat_identity(path.stat())


def _stat_identity(file_stat: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise RetrievalIndexCacheError(
            f"Could not durably install retrieval-index cache files: {exc}"
        ) from exc


atexit.register(_release_process_read_locks)
