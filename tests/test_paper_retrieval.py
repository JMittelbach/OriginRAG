from __future__ import annotations

from pathlib import Path

from pfempire.retrieval.keyword import query_tokens
from pfempire.retrieval.paper_retrieval import score_paper, search_papers


def test_doi_prefix_is_not_reported_as_an_exact_identifier_match() -> None:
    query = "Find DOI 10.1000/example.extension"

    _, rationale = score_paper(
        {"doi": "10.1000/example", "title": "Example paper"},
        query,
        query_tokens(query),
        {},
        "evidence_qa",
    )

    assert "exact DOI match" not in rationale


def test_canonical_doi_equality_is_reported_as_an_exact_identifier_match() -> None:
    query = "Find https://doi.org/10.1000/Example.EXTENSION."

    _, rationale = score_paper(
        {"doi": "10.1000/example.extension", "title": "Example paper"},
        query,
        query_tokens(query),
        {},
        "evidence_qa",
    )

    assert "exact DOI match" in rationale


def test_doi_digits_do_not_become_an_exact_pmid_match() -> None:
    query = "Find DOI 10.1234/5678"

    _, rationale = score_paper(
        {"pmid": "1234", "title": "Unrelated paper"},
        query,
        query_tokens(query),
        {},
        "evidence_qa",
    )

    assert "exact PMID match" not in rationale


def test_pmid_requires_an_explicit_field_label() -> None:
    row = {"pmid": "1234", "title": "Example paper"}

    _, unlabeled = score_paper(
        row,
        "Find identifier 1234",
        query_tokens("Find identifier 1234"),
        {},
        "evidence_qa",
    )
    _, labeled = score_paper(
        row,
        "Find PMID: 1234",
        query_tokens("Find PMID: 1234"),
        {},
        "evidence_qa",
    )

    assert "exact PMID match" not in unlabeled
    assert "exact PMID match" in labeled


def test_ranked_paper_records_preserve_identity_and_local_access_fields(
    tmp_path: Path,
) -> None:
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tdomain\ttopics\tmethods\t"
        "systems\tpaper_type\tfulltext_status\tlocal_pdf_relpath\n"
        "P1\tSPP1 macrophages\t2020\t10.1000/example\t123\tPMC123\thost\t"
        "single_cell\tscRNA-seq\tmacrophage\tjournal article\tchunked\t"
        "papers/example.pdf\n",
        encoding="utf-8",
    )

    result = search_papers("DOI 10.1000/example", papers, limit=1)[0]

    assert result["doi"] == "10.1000/example"
    assert result["pmid"] == "123"
    assert result["pmcid"] == "PMC123"
    assert result["fulltext_status"] == "chunked"
    assert result["local_pdf_relpath"] == "papers/example.pdf"


def test_metadata_search_does_not_claim_absent_local_full_text(tmp_path: Path) -> None:
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tfulltext_status\tlocal_pdf_relpath\n"
        "P1\tSevere malaria example\t2024\tchunked\tpapers/example.pdf\n",
        encoding="utf-8",
    )

    result = search_papers(
        "severe malaria",
        papers,
        limit=1,
        local_pdf_root=tmp_path / "bib",
        chunks_dir=tmp_path / "chunks",
    )[0]

    assert result["local_fulltext_available"] == "false"
    assert "local full text available" not in result["rationale"]


def test_metadata_search_reports_materialised_local_full_text(tmp_path: Path) -> None:
    papers = tmp_path / "papers.tsv"
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    (chunks / "P1.jsonl").write_text("{}\n", encoding="utf-8")
    papers.write_text(
        "paper_id\ttitle\tyear\tfulltext_status\tlocal_pdf_relpath\n"
        "P1\tSevere malaria example\t2024\tchunked\tpapers/example.pdf\n",
        encoding="utf-8",
    )

    result = search_papers(
        "severe malaria",
        papers,
        limit=1,
        local_pdf_root=tmp_path / "bib",
        chunks_dir=chunks,
    )[0]

    assert result["local_fulltext_available"] == "true"
    assert "local full text available" in result["rationale"]


def test_metadata_search_requires_a_term_boundary_match_before_priors() -> None:
    score, rationale = score_paper(
        {
            "title": "Malaria antibody responses",
            "year": "2025",
            "activation_status": "candidate",
        },
        "Type Ia supernovae dark energy",
        query_tokens("Type Ia supernovae dark energy"),
        {},
        "evidence_qa",
    )

    assert score == 0.0
    assert rationale == []
