from __future__ import annotations

import csv
import json

import pytest

from originrag.retrieval import fts_index, keyword


@pytest.fixture(params=["keyword", "multi", "bm25"])
def search_backend(request, tmp_path):
    def run(rows, query, **filters):
        chunks = tmp_path / "chunks"
        chunks.mkdir(exist_ok=True)
        papers = tmp_path / "papers.tsv"
        fields = ["paper_id", "title", "category", "doi", "fulltext_status"]
        with papers.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fields})
                chunk = {
                    "paper_id": row["paper_id"],
                    "chunk_id": row["paper_id"] + "_CHUNK_0001",
                    "section": row.get("section", "results"),
                    "text": row["text"],
                }
                (chunks / (row["paper_id"] + ".jsonl")).write_text(
                    json.dumps(chunk) + "\n", encoding="utf-8"
                )
        options = {"chunks_dir": chunks, "papers_tsv": papers, "limit": 50, **filters}
        if request.param == "multi":
            result = keyword.search_chunks_multi([("query", query)], **options)
        elif request.param == "bm25":
            index = tmp_path / "retrieval.sqlite"
            fts_index.build_fts_index(chunks, papers, index)
            result = fts_index.search_chunks_bm25(query, index_path=index, **options)
        else:
            result = keyword.search_chunks(query, **options)
        return {row["paper_id"] for row in result}

    return run


def active_paper(paper_id="P1", **overrides):
    return {
        "paper_id": paper_id,
        "title": "Texture measurements in Rothko paintings",
        "category": "art_history",
        "doi": "10.1000/texture",
        "fulltext_status": "chunked",
        "text": (
            "Texture measurements distinguished coarse and smooth surfaces in the paintings. "
            "The observations were reproducible across repeated photographic measurements. "
            "Lighting conditions were documented and uncertainty was reported for each surface. "
        ) * 2,
        **overrides,
    }


def test_active_import_does_not_inherit_acquisition_topic_exclusion(search_backend):
    row = active_paper()
    assert keyword._is_out_of_scope_paper(row, query="texture measurements")
    assert search_backend([row], "texture measurements") == {"P1"}


def test_public_health_paper_remains_available_for_prevention_query(search_backend):
    row = active_paper(
        title="Insecticide-treated bed nets and malaria prevention in children",
        category="public_health",
        text=(
            "Insecticide-treated bed nets were evaluated for malaria prevention in children. "
            "The study compared clinical outcomes between groups and reported uncertainty. "
            "The report distinguished the observed association from a causal effectiveness claim. "
        ) * 2,
    )
    query = "effectiveness insecticide-treated bed nets malaria prevention children"
    assert keyword._is_out_of_scope_paper(row, query=query)
    assert search_backend([row], query) == {"P1"}


def test_retrieval_does_not_call_acquisition_topic_policy(search_backend, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Acquisition scope must not silently filter the imported corpus")

    monkeypatch.setattr(keyword, "_is_out_of_scope_paper", forbidden)
    assert search_backend([active_paper()], "texture measurements") == {"P1"}


@pytest.mark.parametrize("status", ["metadata_only", "missing", "excluded", "inactive"])
def test_inactive_full_text_is_still_excluded(search_backend, status):
    row = active_paper(fulltext_status=status)
    assert search_backend([row], "texture measurements") == set()


def test_explicit_inactive_override_remains_opt_in(search_backend):
    row = active_paper(fulltext_status="metadata_only")
    assert search_backend([row], "texture measurements", include_inactive=True) == {"P1"}


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"category": "art_history"}, {"P1"}),
        ({"category": "statistics"}, {"P2"}),
        ({"category": "unrelated"}, set()),
        ({"paper_id": "P1"}, {"P1"}),
        ({"require_doi": True}, {"P1"}),
        ({"min_score": 1e9}, set()),
        ({"include_sections": ["methods"]}, {"P2"}),
        ({"exclude_sections": ["results"]}, {"P2"}),
    ],
)
def test_explicit_user_filters_are_unchanged(search_backend, filters, expected):
    rows = [
        active_paper(),
        active_paper("P2", category="statistics", doi="", section="methods"),
    ]
    assert search_backend(rows, "texture measurements", **filters) == expected


def test_reference_and_low_quality_filters_still_apply(search_backend):
    rows = [
        active_paper(),
        active_paper("REFERENCE", section="references"),
        active_paper("EMPTY", text=""),
        active_paper(
            "LOW_QUALITY",
            text=(
                "texture measurements supplementary figure figure figure figure\n"
                "100 200 300\n400 500 600\n700 800 900\n1000 1100 1200\n"
            ),
        ),
    ]
    assert search_backend(rows, "texture measurements") == {"P1"}
