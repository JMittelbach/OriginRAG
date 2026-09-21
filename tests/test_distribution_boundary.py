import csv
from pathlib import Path
import shutil

from originrag.retrieval.metadata import load_paper_metadata
from originrag.retrieval.paper_retrieval import search_papers
from originrag.schema import PaperRecord


ROOT = Path(__file__).resolve().parents[1]


def test_application_checkout_excludes_internal_research_harnesses():
    assert not list((ROOT / "src/originrag/evaluation").glob("*.py"))
    for relative in (
        "src/originrag/trace/lineage_trap_v1.py",
        "scripts/build_malaria_evidence_bench.py",
        "docs/manuscript/main.tex",
        "docs/paper_reproduction.md",
    ):
        assert not (ROOT / relative).exists(), relative


def test_user_guides_and_software_checks_are_retained():
    for relative in (
        "README.md", "LICENSE", "CITATION.cff", "setup.sh", "web", "terminal",
        "docs/getting_started.md", "docs/web_chat.md", "docs/models.md",
        ".github/workflows/public-ci.yml", "tests/trace/test_exact_lineage.py",
        "tests/test_web_chat.py",
    ):
        assert (ROOT / relative).is_file(), relative


def test_shipped_catalogue_contains_bibliography_not_local_processing_state():
    for name in ("papers.tsv", "paper_registry.tsv"):
        with (ROOT / "assets/catalogue" / name).open(newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        assert rows
        for row in rows:
            for field in ("local_pdf_relpath", "extracted_text_relpath", "filename", "notes", "quality_score", "quality_tier"):
                assert not row.get(field), (name, row.get("paper_id"), field)
            if name == "papers.tsv":
                PaperRecord.model_validate(row)
                assert row["fulltext_status"] in {"missing", "duplicate", "archived", "excluded_no_doi"}
            else:
                sources = set(filter(None, row["metadata_source"].split(";")))
                assert sources <= {"crossref", "pubmed", "europepmc", "publisher", "biorxiv", "zenodo"}


def test_fresh_catalogue_is_searchable_without_claiming_local_pdfs(tmp_path):
    from scripts.init_local_data import initialise

    shutil.copytree(ROOT / "assets/catalogue", tmp_path / "assets/catalogue")
    initialise(tmp_path)
    papers = tmp_path / "data/metadata/papers.tsv"
    metadata = load_paper_metadata(papers)
    assert metadata
    reference = next(row for row in metadata.values() if row.get("doi") and row.get("fulltext_status") == "missing")
    results = search_papers(reference["doi"], papers, local_pdf_root=tmp_path / "bib", chunks_dir=tmp_path / "data/chunks")
    assert any(row["paper_id"] == reference["paper_id"] and row["title"] == reference["title"] for row in results)
    assert all(row["local_fulltext_available"] == "false" and not row["local_pdf_relpath"] for row in results)
