from pathlib import Path

from pfempire.curation.corpus_hygiene import (
    PaperChunkStats,
    assess_paper,
    collect_chunk_stats,
    find_exact_duplicates,
)


def paper(paper_id: str, **values: str) -> dict[str, str]:
    return {
        "paper_id": paper_id,
        "title": values.pop("title", f"A sufficiently specific scientific title for {paper_id}"),
        "domain": values.pop("domain", "malaria"),
        "topics": values.pop("topics", "var_pfemp1"),
        "fulltext_status": values.pop("fulltext_status", "chunked"),
        **values,
    }


def test_exact_doi_duplicate_keeps_chunked_canonical() -> None:
    papers = [
        paper("P1", doi="10.1/test", fulltext_status="duplicate"),
        paper("P2", doi="https://doi.org/10.1/test"),
    ]
    duplicate_map = find_exact_duplicates(
        papers,
        {"P1": PaperChunkStats(total_chunks=0), "P2": PaperChunkStats(total_chunks=8)},
    )
    assert duplicate_map["P1"][0] == "P2"
    assert "exact_doi" in duplicate_map["P1"][1]
    assert "P2" not in duplicate_map


def test_methods_collection_is_retained() -> None:
    result = assess_paper(paper("S1", domain="single_cell_bioinformatics"), PaperChunkStats(total_chunks=5))
    assert result.recommended_action == "keep_methods"


def test_scope_issue_requires_review_not_quarantine() -> None:
    result = assess_paper(
        paper("M1", title="Community acceptability of insecticide-treated bed nets", topics="public_health"),
        PaperChunkStats(total_chunks=5),
    )
    assert result.recommended_action == "review_scope"


def test_all_empty_chunks_are_quarantined_as_corrupt() -> None:
    result = assess_paper(
        paper("M1"),
        PaperChunkStats(total_chunks=4, empty_chunks=4, low_quality_chunks=4),
    )
    assert result.recommended_action == "quarantine_corrupt"


def test_supplement_only_source_requires_reextraction() -> None:
    result = assess_paper(
        paper("M1"),
        PaperChunkStats(total_chunks=10, problem_chunks=9),
    )
    assert result.recommended_action == "review_extraction"


def test_boilerplate_title_requires_metadata_review() -> None:
    result = assess_paper(
        paper("M1", title="This copy is for your personal, non-commercial use only."),
        PaperChunkStats(total_chunks=10),
    )
    assert result.recommended_action == "review_metadata"


def test_boilerplate_titles_do_not_create_duplicate_publications() -> None:
    papers = [
        paper("P1", title="This copy is for your personal, non-commercial use only."),
        paper("P2", title="This copy is for your personal, non-commercial use only."),
    ]
    assert find_exact_duplicates(papers, {}) == {}


def test_collect_chunk_stats(tmp_path: Path) -> None:
    chunk_file = tmp_path / "P1.jsonl"
    long_text = "Results " + "evidence " * 120
    chunk_file.write_text(
        '{"paper_id":"P1","section":"results","text":' + repr(long_text).replace("'", '"') + "}\n",
        encoding="utf-8",
    )
    stats = collect_chunk_stats(tmp_path)["P1"]
    assert stats.total_chunks == 1
    assert stats.useful_chunks == 1
    assert stats.low_quality_chunks == 0
