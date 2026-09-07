from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from extract_lineage_signals import (  # noqa: E402
    lineage_chunks_for_paper,
    lineage_text_for_paper,
)


def test_lineage_signal_text_uses_full_local_chunks(tmp_path: Path) -> None:
    rows = [
        {"text": "abstract without cohort"},
        {"text": "Methods name the NeuroCM study and accession GSE186820."},
    ]
    (tmp_path / "P1.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    text = lineage_text_for_paper("P1", tmp_path, fallback="retrieval snippet")

    assert "NeuroCM study" in text
    assert "GSE186820" in text
    assert "retrieval snippet" not in text


def test_lineage_signal_text_falls_back_when_full_text_is_missing(tmp_path: Path) -> None:
    assert lineage_text_for_paper("P1", tmp_path, fallback="candidate text") == (
        "candidate text"
    )


def test_lineage_chunks_are_not_truncated_after_200k_and_keep_pages(tmp_path: Path) -> None:
    rows = [
        {"chunk_id": "P1_CHUNK_0001", "page_start": 1, "text": "a" * 100_000},
        {"chunk_id": "P1_CHUNK_0002", "page_start": 30, "text": "b" * 100_000},
        {
            "chunk_id": "P1_CHUNK_0003",
            "page_start": 61,
            "text": "METHODS\nWe deposited the data as GSE99999.",
        },
    ]
    (tmp_path / "P1.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    chunks = lineage_chunks_for_paper("P1", tmp_path, fallback="")

    assert len(chunks) == 3
    assert chunks[-1]["chunk_id"] == "P1_CHUNK_0003"
    assert chunks[-1]["page_start"] == 61
    assert "GSE99999" in chunks[-1]["text"]
