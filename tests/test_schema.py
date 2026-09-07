from __future__ import annotations

import csv
from pathlib import Path

import pytest
from pydantic import ValidationError

from originrag.schema import PaperRecord


def test_canonical_paper_metadata_conforms_to_schema() -> None:
    metadata_path = Path(__file__).parents[1] / "data" / "metadata" / "papers.tsv"
    with metadata_path.open(encoding="utf-8", newline="") as handle:
        records = [PaperRecord(**row) for row in csv.DictReader(handle, delimiter="\t")]

    assert records


def test_paper_record_minimal_defaults() -> None:
    record = PaperRecord(paper_id="P1", title="A malaria paper")

    assert record.domain == "malaria"
    assert record.fulltext_status == "missing"
    assert record.topic_list() == []


def test_paper_record_semicolon_fields() -> None:
    record = PaperRecord(
        paper_id="P2",
        title="Immune states",
        topics="severe_malaria;t_cells",
        methods=["scRNA_seq", "flow_cytometry"],
        systems="human_host",
    )

    assert record.topic_list() == ["severe_malaria", "t_cells"]
    assert record.method_list() == ["scRNA_seq", "flow_cytometry"]
    assert record.system_list() == ["human_host"]


def test_paper_record_rejects_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        PaperRecord(paper_id="", title="")


def test_paper_record_rejects_unknown_fulltext_status() -> None:
    with pytest.raises(ValidationError):
        PaperRecord(paper_id="P3", title="Bad status", fulltext_status="downloaded")


def test_paper_record_normalizes_blank_tabular_values() -> None:
    record = PaperRecord(
        paper_id=" P4 ",
        title=" Tabular import ",
        year="",
        domain=" ",
        topics=None,
        fulltext_status="",
        unknown_legacy_column="ignored",
    )

    assert record.paper_id == "P4"
    assert record.title == "Tabular import"
    assert record.year is None
    assert record.domain == "malaria"
    assert record.topics == ""
    assert record.fulltext_status == "missing"
    assert "unknown_legacy_column" not in record.model_dump()
