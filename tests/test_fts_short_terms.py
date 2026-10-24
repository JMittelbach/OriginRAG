from __future__ import annotations

import json
from pathlib import Path

import pytest

from originrag.retrieval.fts_index import (
    build_fts_index,
    build_fts_query,
    search_chunks_bm25,
    search_index_candidates,
)
from originrag.retrieval.keyword import search_chunks


@pytest.mark.parametrize("term", ["AI", "ai", "ML", "ml", "UX", "ux", "DB", "VR", "IO"])
def test_fts_retains_two_letter_subject_terms(term: str) -> None:
    assert build_fts_query(term) == f'"{term.lower()}"'


@pytest.mark.parametrize(
    "query, expected",
    [
        ("What is AI?", '"ai"'),
        ("How do we evaluate UX?", '"evaluate" OR "ux"'),
        ("Was ist ML im Vergleich zu AI?", '"was" OR "ist" OR "ml" OR "vergleich" OR "ai"'),
        ("AI OR ML", '"ai" OR "ml"'),
        ("AI vs ML", '"ai" OR "ml"'),
        ("database indexing", '"database" OR "indexing"'),
        ("c x y 12 99", ""),
        ("a an in of to by or on as", ""),
        ("we do it if he is at my be me us no so up", ""),
        ("ab am da de du er es im ja ob um wo zu", ""),
    ],
)
def test_fts_short_term_filter_keeps_subjects_not_function_words(
    query: str, expected: str
) -> None:
    assert build_fts_query(query) == expected


@pytest.mark.parametrize("query", ["AI", "ML", "UX"])
def test_short_subjects_are_retrieved_like_keyword_search(
    tmp_path: Path, query: str
) -> None:
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tfulltext_status\n"
        "AI\tAI software evaluation\tchunked\n"
        "ML\tML model evaluation\tchunked\n"
        "UX\tUX interface evaluation\tchunked\n"
        "OTHER\tRainfall measurements\tchunked\n",
        encoding="utf-8",
    )
    for term in ("AI", "ML", "UX", "OTHER"):
        text = (
            f"The {term} benchmark compared different experimental conditions. "
            "Measurements were repeated independently with matched input data. "
            "The reported estimates describe the observed performance and its uncertainty. "
        ) * 6
        (chunks / f"{term}.jsonl").write_text(
            json.dumps(
                {"chunk_id": f"{term}_1", "paper_id": term, "section": "results", "text": text}
            )
            + "\n",
            encoding="utf-8",
        )
    index = tmp_path / "retrieval.sqlite"
    build_fts_index(chunks, papers, index)

    candidates = search_index_candidates(query, index)
    assert candidates.chunk_ids == frozenset({f"{query}_1"})
    assert candidates.paper_ids == frozenset({query})
    direct = search_chunks(query, chunks, papers)
    indexed = search_chunks_bm25(query, chunks, papers, index_path=index)
    assert [(row["chunk_id"], row["score"]) for row in indexed] == [
        (row["chunk_id"], row["score"]) for row in direct
    ]
    assert [row["paper_id"] for row in indexed] == [query]
