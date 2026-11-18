import csv
from pathlib import Path

from originrag.retrieval.metadata import load_paper_metadata
from originrag.retrieval.paper_retrieval import search_papers


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


def test_fresh_catalogue_is_empty_and_only_searches_local_additions(tmp_path):
    from scripts.init_local_data import initialise

    initialise(tmp_path)
    papers = tmp_path / "data/metadata/papers.tsv"
    assert not load_paper_metadata(papers)
    for path in (papers, papers.with_name("paper_registry.tsv")):
        with path.open(newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            assert {"paper_id", "title", "doi"} <= set(reader.fieldnames)
            assert not list(reader)
    reference = {"paper_id": "TEST_001", "title": "Synthetic checkout fixture", "fulltext_status": "missing"}
    def search():
        return search_papers(reference["title"], papers, local_pdf_root=tmp_path / "bib", chunks_dir=tmp_path / "data/chunks")
    assert search() == []
    fields = papers.read_text().splitlines()[0].split("\t")
    with papers.open("a", newline="") as stream:
        csv.DictWriter(stream, fieldnames=fields, delimiter="\t").writerow(reference)
    results = search()
    assert [row["paper_id"] for row in results] == [reference["paper_id"]]
    assert all(row["local_fulltext_available"] == "false" and not row["local_pdf_relpath"] for row in results)
