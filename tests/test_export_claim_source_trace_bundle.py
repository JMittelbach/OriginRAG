from __future__ import annotations

import importlib.util
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "export_claim_source_trace_bundle.py"
)
SPEC = importlib.util.spec_from_file_location("export_claim_source_trace_bundle", SCRIPT_PATH)
assert SPEC and SPEC.loader
bundle_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle_module)


def make_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE citation_mentions "
            "(mention_id TEXT PRIMARY KEY, context_text TEXT)"
        )
        connection.execute(
            "INSERT INTO citation_mentions VALUES (?, ?)",
            ("M1", "claim context"),
        )


def make_trace_dir(path: Path) -> None:
    path.mkdir()
    (path / "trace.json").write_text(
        '{"parser_version":"claim_source_trace_v1.2","mentions":[]}\n',
        encoding="utf-8",
    )
    (path / "trace.md").write_text("# Trace\n", encoding="utf-8")
    (path / "ignored.txt").write_text("not part of the bundle\n", encoding="utf-8")


def test_bundle_round_trip_and_consistent_database_snapshot(tmp_path: Path) -> None:
    trace_dir = tmp_path / "traces"
    database = tmp_path / "source.sqlite"
    bundle = tmp_path / "bundle.zip"
    make_trace_dir(trace_dir)
    make_database(database)

    manifest = bundle_module.create_bundle(
        trace_dir=trace_dir,
        db_path=database,
        output_path=bundle,
        created_at_utc="2026-07-26T00:00:00+00:00",
        git_commit="test-commit",
        git_dirty=False,
    )

    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO citation_mentions VALUES (?, ?)",
            ("M2", "added after export"),
        )

    result = bundle_module.verify_bundle(bundle)
    assert result["schema_version"] == "claim_source_trace_bundle_v1"
    assert manifest["trace_json_count"] == 1
    assert manifest["trace_report_count"] == 1
    assert manifest["parser_versions"] == {"claim_source_trace_v1.2": 1}
    assert manifest["sqlite"]["row_counts"]["citation_mentions"] == 1
    assert {row["archive_path"] for row in manifest["files"]} == {
        "traces/trace.json",
        "traces/trace.md",
        "database/pfempire.sqlite",
    }


def test_bundle_verification_rejects_modified_payload(tmp_path: Path) -> None:
    trace_dir = tmp_path / "traces"
    database = tmp_path / "source.sqlite"
    original = tmp_path / "original.zip"
    tampered = tmp_path / "tampered.zip"
    make_trace_dir(trace_dir)
    make_database(database)
    bundle_module.create_bundle(
        trace_dir=trace_dir,
        db_path=database,
        output_path=original,
        created_at_utc="2026-07-26T00:00:00+00:00",
        git_commit="test-commit",
        git_dirty=False,
    )

    with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "traces/trace.json":
                content += b" "
            target.writestr(info, content)

    with pytest.raises(ValueError, match="size mismatch|SHA-256 mismatch"):
        bundle_module.verify_bundle(tampered)


def test_bundle_verification_rejects_inconsistent_manifest_summary(
    tmp_path: Path,
) -> None:
    trace_dir = tmp_path / "traces"
    database = tmp_path / "source.sqlite"
    original = tmp_path / "original.zip"
    tampered = tmp_path / "tampered.zip"
    make_trace_dir(trace_dir)
    make_database(database)
    bundle_module.create_bundle(
        trace_dir=trace_dir,
        db_path=database,
        output_path=original,
        created_at_utc="2026-07-26T00:00:00+00:00",
        git_commit="test-commit",
        git_dirty=False,
    )

    with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "manifest.json":
                manifest = json.loads(content)
                manifest["trace_json_count"] = 99
                content = (json.dumps(manifest) + "\n").encode()
            target.writestr(info, content)

    with pytest.raises(ValueError, match="trace_json_count"):
        bundle_module.verify_bundle(tampered)


def test_bundle_refuses_overwrite_and_symbolic_links(tmp_path: Path) -> None:
    trace_dir = tmp_path / "traces"
    database = tmp_path / "source.sqlite"
    bundle = tmp_path / "bundle.zip"
    make_trace_dir(trace_dir)
    make_database(database)
    bundle.write_bytes(b"existing")

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        bundle_module.create_bundle(
            trace_dir=trace_dir,
            db_path=database,
            output_path=bundle,
        )

    bundle.unlink()
    (trace_dir / "linked.json").symlink_to(trace_dir / "trace.json")
    with pytest.raises(ValueError, match="symbolic links"):
        bundle_module.create_bundle(
            trace_dir=trace_dir,
            db_path=database,
            output_path=bundle,
        )
