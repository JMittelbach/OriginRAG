from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from originrag.retrieval import keyword


def _wait_for_filesystem_change_tick(path: Path, previous_ctime_ns: int) -> None:
    """Keep fixture rewrites distinguishable on filesystems with coarse ctime."""
    probe = path.with_suffix(".clock-probe")
    deadline = time.monotonic() + 2.0
    try:
        while True:
            probe.touch()
            if probe.stat().st_ctime_ns != previous_ctime_ns:
                return
            if time.monotonic() >= deadline:
                pytest.fail("Filesystem change clock did not advance within two seconds")
            time.sleep(0.002)
    finally:
        probe.unlink(missing_ok=True)


def _write_chunk(path: Path, marker: str) -> None:
    path.write_text(
        json.dumps(
            {
                "chunk_id": f"{path.stem}_001",
                "paper_id": path.stem,
                "section": "results",
                "text": (
                    f"The synthetic database benchmark used the {marker} configuration. "
                    "Measurements compared query latency and memory allocation across "
                    "independent experimental runs with fixed hardware settings."
                ),
            }
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("atomic_replace", [False, True])
def test_chunk_cache_refreshes_same_size_rewrite_with_preserved_mtime(
    tmp_path: Path, canonical: bool, atomic_replace: bool
) -> None:
    path = tmp_path / "P1.jsonl"
    _write_chunk(path, "cobalt")
    kwargs = (
        {"candidate_paper_ids": {"P1"}, "canonical_layout_verified": True}
        if canonical
        else {}
    )
    before = path.stat()
    initial = keyword.iter_chunk_records(tmp_path, **kwargs)
    assert "cobalt" in initial[0][2]["text"]

    if not atomic_replace:
        _wait_for_filesystem_change_tick(path, before.st_ctime_ns)
    replacement = path.with_suffix(".replacement") if atomic_replace else path
    replacement.write_text(
        path.read_text(encoding="utf-8").replace("cobalt", "violet"),
        encoding="utf-8",
    )
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    if atomic_replace:
        replacement.replace(path)
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert (after.st_ino, after.st_ctime_ns) != (before.st_ino, before.st_ctime_ns)

    refreshed = keyword.iter_chunk_records(tmp_path, **kwargs)
    assert "violet" in refreshed[0][2]["text"]
    assert "cobalt" not in refreshed[0][2]["text"]
    assert "cobalt" in initial[0][2]["text"]


@pytest.mark.parametrize("canonical", [False, True])
def test_unchanged_chunk_cache_reuses_parsed_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, canonical: bool
) -> None:
    _write_chunk(tmp_path / "P1.jsonl", "cobalt")
    _write_chunk(tmp_path / "P2.jsonl", "violet")
    kwargs = (
        {"candidate_paper_ids": {"P1", "P2"}, "canonical_layout_verified": True}
        if canonical
        else {}
    )
    original_loads = json.loads
    parsed = []

    def counted_loads(value: str):
        parsed.append(value)
        return original_loads(value)

    monkeypatch.setattr(keyword.json, "loads", counted_loads)
    first = keyword.iter_chunk_records(tmp_path, **kwargs)
    second = keyword.iter_chunk_records(tmp_path, **kwargs)
    assert first == second
    assert len(parsed) == 2

    changed = tmp_path / "P1.jsonl"
    before = changed.stat()
    _wait_for_filesystem_change_tick(changed, before.st_ctime_ns)
    _write_chunk(changed, "silver")
    os.utime(changed, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert changed.stat().st_ctime_ns != before.st_ctime_ns
    refreshed = keyword.iter_chunk_records(tmp_path, **kwargs)
    assert len(parsed) == 3
    assert "silver" in refreshed[0][2]["text"]


def test_keyword_search_does_not_return_stale_passages(tmp_path: Path) -> None:
    path = tmp_path / "P1.jsonl"
    _write_chunk(path, "cobalt")
    assert keyword.search_chunks("cobalt", tmp_path)[0]["paper_id"] == "P1"
    before = path.stat()
    _wait_for_filesystem_change_tick(path, before.st_ctime_ns)
    _write_chunk(path, "violet")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_ctime_ns != before.st_ctime_ns

    assert keyword.search_chunks("cobalt", tmp_path) == []
    assert keyword.search_chunks("violet", tmp_path)[0]["paper_id"] == "P1"


def test_chunk_cache_tracks_added_and_removed_files(tmp_path: Path) -> None:
    _write_chunk(tmp_path / "P1.jsonl", "cobalt")
    assert len(keyword.iter_chunk_records(tmp_path)) == 1
    _write_chunk(tmp_path / "P2.jsonl", "violet")
    assert len(keyword.iter_chunk_records(tmp_path)) == 2
    (tmp_path / "P1.jsonl").unlink()
    assert [row[2]["paper_id"] for row in keyword.iter_chunk_records(tmp_path)] == ["P2"]
