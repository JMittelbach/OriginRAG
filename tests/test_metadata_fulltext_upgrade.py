import csv
import sqlite3
import sys
from pathlib import Path
import pytest

from test_oa_import import pdf_bytes


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import import_paper_inbox as inbox  # noqa: E402


@pytest.mark.parametrize("files,expected", [
    ({"src/originrag/__init__.py": "code-hash"}, set()),
    ({"bib/papers/example.pdf": "pdf-hash"}, {"pdf-hash"}),
    ([{"path": "bib/papers/example.pdf", "sha256": "legacy-pdf-hash"}], {"legacy-pdf-hash"}),
    ([None, "unexpected", {"path": "bib/papers/example.pdf", "sha256": None}], set()),
])
def test_known_pdf_hashes_accept_both_product_manifest_formats(tmp_path, monkeypatch, files, expected):
    import json
    monkeypatch.setattr(inbox, "REPO_ROOT", tmp_path)
    (tmp_path / "product_manifest.json").write_text(json.dumps({"files": files}))
    cache = tmp_path / "runtime/imports/pdf_hashes.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(["previous-import-hash"]))
    assert inbox.load_known_pdf_hashes() == expected | {"previous-import-hash"}


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


def test_import_distinguishes_preprints_from_journal_articles(tmp_path, monkeypatch):
    source, metadata = setup_library(tmp_path, monkeypatch)
    assert inbox.crossref_paper_type({"type":"journal-article"}) == "journal article"
    assert inbox.crossref_paper_type({"type":"posted-content"}) == "posted content"
    assert inbox.crossref_paper_type({}) == ""
    kind = inbox.crossref_paper_type({"type":"posted-content", "subtype":"preprint"})
    assert kind == "preprint"
    fields, path = ["paper_id", "bib_key", "doi", "title", "paper_type", "fulltext_status"], metadata / "papers.tsv"
    rows, _ = inbox.read_tsv(path)
    inbox.write_tsv_atomic(path, rows, fields)
    fetch = inbox.fetch_crossref
    monkeypatch.setattr(inbox, "fetch_crossref", lambda doi, *, timeout: {**fetch(doi, timeout=timeout), "paper_type": kind})
    assert inbox.import_inbox(source.parent)["imported"] == 1
    assert inbox.read_tsv(path)[0][0]["paper_type"] == "preprint"
    assert "note = {Preprint}" in (tmp_path / "bib/pfempire_library.bib").read_text()


def test_real_import_accepts_product_source_manifest(tmp_path, monkeypatch):
    import json
    source, _ = setup_library(tmp_path, monkeypatch)
    (tmp_path / "product_manifest.json").write_text(json.dumps({
        "schema_version": "originrag_product_source_v1",
        "files": {"scripts/import_paper_inbox.py": "code-hash"},
    }))
    result = inbox.import_inbox(source.parent)
    assert result["imported"] == 1


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


@pytest.mark.parametrize("index_failure", [False, True])
def test_one_click_import_runs_real_sorting_and_indexing_without_user_upload(tmp_path, monkeypatch, index_failure):
    from web_chat import LibrarySyncManager
    from originrag.discovery.oa_import import PaperImports
    source, metadata = setup_library(tmp_path, monkeypatch)
    source.rename(tmp_path / "fixture.pdf")
    if index_failure:
        def fail(*a, **kw):
            raise RuntimeError("Index unavailable")
        monkeypatch.setattr(inbox, "update_fts_index", fail)
    library = LibrarySyncManager(source.parent, importer=inbox.import_inbox, auto_worker=False,
                                 read_cache_refresh=lambda: None, refresh_callback=lambda: None)
    jobs = PaperImports(library, downloader=lambda doi, progress: pdf_bytes(doi))
    try:
        jobs.enqueue("alice", "10.1234/test")
    finally:
        jobs.shutdown()
    job = jobs.snapshot("alice")[0]
    if index_failure:
        assert job["state"] != "imported"
        assert not (tmp_path / "data/chunks/EXISTING_1.jsonl").exists()
        return
    assert job["state"] == "imported"
    with (metadata / "papers.tsv").open() as stream:
        row = next(csv.DictReader(stream, delimiter="\t"))
    assert row["paper_id"] == "EXISTING_1"
    assert row["fulltext_status"] == "chunked"
    assert row["local_pdf_relpath"].startswith("papers/")
    assert (tmp_path / "bib" / row["local_pdf_relpath"]).read_bytes().startswith(b"%PDF-")
    with sqlite3.connect(tmp_path / "data/indexes/pfempire_fts.sqlite") as db:
        found = db.execute("SELECT paper_id FROM chunk_fts WHERE chunk_fts MATCH ?", ("graph",)).fetchall()
    assert found and {r[0] for r in found} == {"EXISTING_1"}
