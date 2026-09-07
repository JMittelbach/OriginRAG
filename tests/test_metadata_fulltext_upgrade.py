import csv
import sys
from pathlib import Path
import pytest

from test_oa_import import pdf_bytes


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import import_paper_inbox as inbox  # noqa: E402


def setup_library(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(inbox, "fetch_crossref", lambda *a, **k: {"title": "A study of graph algorithms", "author": "Example", "year": "2024", "journal": "Test Journal"})
    metadata = tmp_path / "data/metadata"
    metadata.mkdir(parents=True)
    fields = ["paper_id", "bib_key", "doi", "title", "author", "year", "journal", "local_pdf_relpath", "fulltext_status", "notes"]
    row = {"paper_id": "EXISTING_1", "bib_key": "Example2024", "doi": "10.1234/test", "title": "A study of graph algorithms", "notes": "curated note"}
    for filename in ("papers.tsv", "paper_registry.tsv"):
        inbox.write_tsv_atomic(metadata / filename, [row], fields)
    source = tmp_path / "bib/inbox/paper.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(pdf_bytes())
    return source, metadata


def test_metadata_only_record_is_upgraded_without_duplicate_ids(tmp_path, monkeypatch):
    source, metadata = setup_library(tmp_path, monkeypatch)
    result = inbox.import_inbox(source.parent)
    assert result["imported"] == 1, result
    for filename in ("papers.tsv", "paper_registry.tsv"):
        with (metadata / filename).open() as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        assert len(rows) == 1
        assert rows[0]["paper_id"] == "EXISTING_1"
        assert rows[0]["bib_key"] == "Example2024"
        assert "curated note" in rows[0]["notes"]
        assert (tmp_path / "bib" / rows[0]["local_pdf_relpath"]).is_file()
    assert (tmp_path / "data/chunks/EXISTING_1.jsonl").is_file()
    assert (tmp_path / "data/indexes/pfempire_fts.sqlite").is_file()
    source.write_bytes(pdf_bytes())
    repeated = inbox.import_inbox(source.parent)
    assert repeated["imported"] == 0 and repeated["duplicates"] == 1


def test_existing_assets_and_conflicting_ids_are_not_upgraded(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "REPO_ROOT", tmp_path)
    rows = [{"doi": "10.1234/test", "paper_id": "P1"}]
    assert inbox.metadata_only_records(rows)
    assert not inbox.metadata_only_records(rows + [{"doi": "10.1234/test", "paper_id": "P2"}])
    asset = tmp_path / "data/chunks/P1.jsonl"
    asset.parent.mkdir(parents=True)
    asset.write_text("{}")
    assert not inbox.metadata_only_records(rows)


def test_upgrade_rolls_back_metadata_and_restores_pdf_on_index_failure(tmp_path, monkeypatch):
    source, metadata = setup_library(tmp_path, monkeypatch)
    original = {path.name: path.read_bytes() for path in metadata.iterdir()}
    def fail(*args, **kwargs):
        raise RuntimeError("test index failure")
    monkeypatch.setattr(inbox, "update_fts_index", fail)
    with pytest.raises(RuntimeError, match="test index failure"):
        inbox.import_inbox(source.parent)
    assert source.is_file()
    assert not (tmp_path / "data/chunks/EXISTING_1.jsonl").exists()
    assert {path.name: path.read_bytes() for path in metadata.iterdir()} == original
