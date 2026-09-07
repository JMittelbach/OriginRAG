from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_SCHEMA_VERSION = "claim_source_trace_bundle_v1"
MANIFEST_NAME = "manifest.json"
DATABASE_ARCHIVE_PATH = "database/pfempire.sqlite"
TRACE_SUFFIXES = {".json", ".md"}


def main() -> int:
    args = parse_args()
    if args.verify:
        result = verify_bundle(resolve_repo_path(args.verify))
        print(
            f"Verified {result['archive']} "
            f"({result['file_count']} payload files; schema "
            f"{result['schema_version']})."
        )
        return 0

    output = ensure_zip_suffix(
        resolve_repo_path(args.out)
        if args.out
        else default_output_path()
    )
    manifest = create_bundle(
        trace_dir=resolve_repo_path(args.trace_dir),
        db_path=resolve_repo_path(args.db),
        output_path=output,
    )
    print(f"Bundle: {display_path(output)}")
    print(
        f"Payload: {len(manifest['files'])} files; "
        f"{manifest['trace_json_count']} trace JSON; "
        f"{manifest['trace_report_count']} Markdown reports."
    )
    print(f"SHA-256: {sha256_file(output)}")
    print(f"Verify: ./originrag export-traces --verify {display_path(output)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create or verify a versioned archive of machine-local Claim Source "
            "Trace reports and a transactionally consistent SQLite snapshot."
        )
    )
    parser.add_argument("--trace-dir", default="outputs/source_traces")
    parser.add_argument("--db", default="data/local_db/pfempire.sqlite")
    parser.add_argument("--out", default="")
    parser.add_argument(
        "--verify",
        metavar="BUNDLE",
        default="",
        help="Verify an existing bundle instead of creating one.",
    )
    args = parser.parse_args()
    if args.verify and args.out:
        parser.error("--verify and --out cannot be combined")
    return args


def create_bundle(
    *,
    trace_dir: Path,
    db_path: Path,
    output_path: Path,
    created_at_utc: str | None = None,
    git_commit: str | None = None,
    git_dirty: bool | None = None,
) -> dict[str, Any]:
    trace_dir = Path(trace_dir).resolve()
    db_path = Path(db_path).resolve()
    output_path = ensure_zip_suffix(Path(output_path).resolve())
    if not trace_dir.is_dir():
        raise FileNotFoundError(f"Trace directory not found: {trace_dir}")
    if not db_path.is_file():
        raise FileNotFoundError(f"Trace database not found: {db_path}")
    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing bundle: {output_path}")

    trace_files = discover_trace_files(trace_dir)
    if not trace_files:
        raise ValueError(f"No .json or .md trace reports found in {trace_dir}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    commit, dirty = current_git_state()
    if git_commit is not None:
        commit = git_commit
    if git_dirty is not None:
        dirty = git_dirty
    created_at = created_at_utc or datetime.now(timezone.utc).isoformat()

    with tempfile.TemporaryDirectory(
        prefix="pfempire_trace_bundle_",
        dir=output_path.parent,
    ) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        snapshot_path = temp_dir / "pfempire.sqlite"
        sqlite_info = snapshot_database(db_path, snapshot_path)
        payloads: list[tuple[str, bytes, str]] = []
        parser_versions: Counter[str] = Counter()
        trace_json_count = 0
        trace_report_count = 0

        for trace_file in trace_files:
            relative = trace_file.relative_to(trace_dir).as_posix()
            archive_path = f"traces/{relative}"
            content = trace_file.read_bytes()
            kind = "trace_json" if trace_file.suffix.lower() == ".json" else "trace_report"
            if kind == "trace_json":
                payload = json.loads(content)
                if not isinstance(payload, dict):
                    raise ValueError(f"Trace JSON must contain an object: {trace_file}")
                parser_versions[str(payload.get("parser_version") or "unknown")] += 1
                trace_json_count += 1
            else:
                trace_report_count += 1
            payloads.append((archive_path, content, kind))

        payloads.append(
            (DATABASE_ARCHIVE_PATH, snapshot_path.read_bytes(), "sqlite_snapshot")
        )
        file_records = [
            {
                "archive_path": archive_path,
                "kind": kind,
                "bytes": len(content),
                "sha256": sha256_bytes(content),
            }
            for archive_path, content, kind in payloads
        ]
        manifest: dict[str, Any] = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "created_at_utc": created_at,
            "git": {"commit": commit, "dirty": dirty},
            "source": {
                "trace_dir": display_path(trace_dir),
                "database": display_path(db_path),
            },
            "trace_json_count": trace_json_count,
            "trace_report_count": trace_report_count,
            "parser_versions": dict(sorted(parser_versions.items())),
            "sqlite": sqlite_info,
            "files": file_records,
        }
        manifest_bytes = (
            json.dumps(
                manifest,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        write_bundle_atomically(
            output_path,
            [(MANIFEST_NAME, manifest_bytes, "manifest"), *payloads],
        )
    verify_bundle(output_path)
    return manifest


def verify_bundle(bundle_path: Path) -> dict[str, Any]:
    bundle_path = Path(bundle_path).resolve()
    if not bundle_path.is_file():
        raise FileNotFoundError(f"Bundle not found: {bundle_path}")
    manifest, database_bytes = read_verified_archive(bundle_path)

    with tempfile.TemporaryDirectory(prefix="pfempire_trace_verify_") as temp_dir:
        snapshot = Path(temp_dir) / "pfempire.sqlite"
        snapshot.write_bytes(database_bytes)
        sqlite_info = inspect_database(snapshot)
    if sqlite_info != manifest.get("sqlite"):
        raise ValueError("SQLite metadata does not match the bundle manifest")
    return {
        "archive": display_path(bundle_path),
        "schema_version": manifest["schema_version"],
        "file_count": len(manifest["files"]),
        "manifest": manifest,
    }


def read_verified_archive(bundle_path: Path) -> tuple[dict[str, Any], bytes]:
    with zipfile.ZipFile(bundle_path) as archive:
        names = validated_archive_names(archive)
        manifest = read_manifest(archive)
        database_bytes, summary = verify_payload_records(
            archive,
            names,
            manifest["files"],
        )
    verify_trace_summary(manifest, summary)
    return manifest, database_bytes


def validated_archive_names(archive: zipfile.ZipFile) -> set[str]:
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ValueError("Bundle contains duplicate archive paths")
    if any(info.is_dir() for info in infos):
        raise ValueError("Bundle contains directory entries")
    for name in names:
        validate_archive_path(name)
    if MANIFEST_NAME not in names:
        raise ValueError("Bundle has no manifest.json")
    return set(names)


def read_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    manifest = json.loads(archive.read(MANIFEST_NAME))
    if not isinstance(manifest, dict):
        raise ValueError("Bundle manifest must contain an object")
    if manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported bundle schema: {manifest.get('schema_version')!r}"
        )
    records = manifest.get("files")
    if not isinstance(records, list):
        raise ValueError("Bundle manifest files must be a list")
    return manifest


def verify_payload_records(
    archive: zipfile.ZipFile,
    names: set[str],
    records: list[Any],
) -> tuple[bytes, dict[str, Any]]:
    expected_names = {MANIFEST_NAME}
    parser_versions: Counter[str] = Counter()
    trace_json_count = 0
    trace_report_count = 0
    database_bytes: bytes | None = None
    for record in records:
        name, content, kind = verify_payload_record(
            archive,
            names,
            record,
            expected_names,
        )
        if kind == "trace_json":
            parser_versions[trace_parser_version(content, name)] += 1
            trace_json_count += 1
        elif kind == "trace_report":
            trace_report_count += 1
        else:
            database_bytes = content
    if names != expected_names:
        unexpected = sorted(names - expected_names)
        raise ValueError(
            "Bundle contains unmanifested payloads: " + ", ".join(unexpected)
        )
    if database_bytes is None:
        raise ValueError("Bundle has no SQLite snapshot")
    return database_bytes, {
        "trace_json_count": trace_json_count,
        "trace_report_count": trace_report_count,
        "parser_versions": dict(sorted(parser_versions.items())),
    }


def verify_payload_record(
    archive: zipfile.ZipFile,
    names: set[str],
    record: Any,
    expected_names: set[str],
) -> tuple[str, bytes, str]:
    if not isinstance(record, dict):
        raise ValueError("Bundle file record must contain an object")
    name = str(record.get("archive_path") or "")
    validate_archive_path(name)
    if name in expected_names:
        raise ValueError(f"Bundle manifest repeats a payload: {name}")
    expected_names.add(name)
    if name not in names:
        raise ValueError(f"Bundle payload is missing: {name}")
    kind = expected_payload_kind(name)
    if record.get("kind") != kind:
        raise ValueError(f"Bundle payload kind mismatch: {name}")
    content = archive.read(name)
    if len(content) != record.get("bytes"):
        raise ValueError(f"Bundle size mismatch: {name}")
    if sha256_bytes(content) != record.get("sha256"):
        raise ValueError(f"Bundle SHA-256 mismatch: {name}")
    return name, content, kind


def expected_payload_kind(name: str) -> str:
    if name == DATABASE_ARCHIVE_PATH:
        return "sqlite_snapshot"
    if name.startswith("traces/") and name.lower().endswith(".json"):
        return "trace_json"
    if name.startswith("traces/") and name.lower().endswith(".md"):
        return "trace_report"
    raise ValueError(f"Unsupported bundle payload path: {name}")


def trace_parser_version(content: bytes, name: str) -> str:
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError(f"Trace JSON must contain an object: {name}")
    return str(payload.get("parser_version") or "unknown")


def verify_trace_summary(
    manifest: dict[str, Any],
    actual: dict[str, Any],
) -> None:
    for field in ("trace_json_count", "trace_report_count", "parser_versions"):
        if manifest.get(field) != actual[field]:
            raise ValueError(f"Bundle manifest {field} does not match its payloads")


def discover_trace_files(trace_dir: Path) -> list[Path]:
    files = []
    for candidate in sorted(trace_dir.rglob("*")):
        if candidate.is_symlink():
            raise ValueError(f"Trace bundle refuses symbolic links: {candidate}")
        if candidate.is_file() and candidate.suffix.lower() in TRACE_SUFFIXES:
            files.append(candidate)
    return files


def snapshot_database(source: Path, destination: Path) -> dict[str, Any]:
    with sqlite3.connect(source) as source_connection:
        source_check = str(source_connection.execute("PRAGMA quick_check").fetchone()[0])
        if source_check != "ok":
            raise ValueError(f"Source SQLite quick_check failed: {source_check}")
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)
    return inspect_database(destination)


def inspect_database(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path) as connection:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]
        row_counts = {
            table: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {quote_identifier(table)}"
                ).fetchone()[0]
            )
            for table in tables
        }
    if quick_check != "ok":
        raise ValueError(f"SQLite snapshot quick_check failed: {quick_check}")
    return {"quick_check": quick_check, "tables": tables, "row_counts": row_counts}


def write_bundle_atomically(
    output_path: Path,
    payloads: list[tuple[str, bytes, str]],
) -> None:
    with tempfile.NamedTemporaryFile(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
        delete=False,
    ) as handle:
        temporary_path = Path(handle.name)
    try:
        with zipfile.ZipFile(
            temporary_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for archive_path, content, _kind in payloads:
                validate_archive_path(archive_path)
                info = zipfile.ZipInfo(
                    filename=archive_path,
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content)
        os.link(temporary_path, output_path)
        temporary_path.unlink()
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def validate_archive_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or ".." in path.parts
        or value != path.as_posix()
    ):
        raise ValueError(f"Unsafe bundle archive path: {value!r}")


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def current_git_state() -> tuple[str, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=True,
                timeout=5,
            ).stdout.strip()
        )
        return commit or "unknown", dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", None


def default_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return REPO_ROOT / "outputs" / "source_trace_exports" / f"claim_source_trace_{stamp}.zip"


def ensure_zip_suffix(path: Path) -> Path:
    return path if path.suffix.lower() == ".zip" else path.with_suffix(".zip")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def resolve_repo_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def display_path(path: Path) -> str:
    resolved = Path(path).resolve()
    return (
        resolved.relative_to(REPO_ROOT).as_posix()
        if resolved.is_relative_to(REPO_ROOT)
        else str(resolved)
    )


if __name__ == "__main__":
    raise SystemExit(main())
