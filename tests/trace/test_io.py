from __future__ import annotations

from pathlib import Path

import pytest

from pfempire.trace.io import read_jsonl, read_tsv, write_json, write_tsv


def test_read_tsv_rejects_duplicate_headers_and_width_mismatch(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.tsv"
    duplicate.write_text("paper_id\tpaper_id\nA\tB\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate TSV headers"):
        read_tsv(duplicate)

    ragged = tmp_path / "ragged.tsv"
    ragged.write_text("paper_id\tscore\nA\n", encoding="utf-8")
    with pytest.raises(ValueError, match=":2"):
        read_tsv(ragged)


def test_write_tsv_preserves_fields_introduced_after_first_row(tmp_path: Path) -> None:
    path = tmp_path / "rows.tsv"
    write_tsv(path, [{"a": 1}, {"a": 2, "b": 3}])

    assert read_tsv(path) == [{"a": "1", "b": ""}, {"a": "2", "b": "3"}]
    assert b"\r\n" not in path.read_bytes()


def test_json_writers_reject_nonfinite_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        write_json(tmp_path / "bad.json", {"score": float("nan")})


def test_jsonl_error_reports_file_line(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text('{"ok": 1}\n{broken}\n', encoding="utf-8")

    with pytest.raises(ValueError, match=r"rows\.jsonl:2"):
        read_jsonl(path)
