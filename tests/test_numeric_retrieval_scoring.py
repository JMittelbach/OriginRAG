from __future__ import annotations

import json

import pytest

from originrag.retrieval import fts_index, keyword


def score(query, text):
    return keyword._score_text(
        keyword.normalize_text(query), keyword._scoring_query_tokens(query), text
    )


@pytest.mark.parametrize(
    ("query_value", "matching", "different"),
    [
        ("0.1", "0.1", "1.0"),
        ("0.01", "0.01", "0.1"),
        ("-0.1", "-0.1", "0.1"),
        ("−0.1", "-0.1", "+0.1"),
        ("+0.1", "0.1", "-0.1"),
        (".01", "0.01", "0.1"),
        ("1e-3", "0.001", "0.01"),
        ("1E+3", "1000.0", "0.001"),
        ("-1.0e-3", "−0.001", "0.001"),
        ("0.0100", "1e-2", "0.1"),
    ],
)
def test_numeric_magnitude_and_sign_are_not_digit_bags(query_value, matching, different):
    query = f"How should calibration be interpreted at uncertainty {query_value}?"
    correct = f"Calibration measurement uncertainty was {matching}."
    misleading = f"Calibration measurement uncertainty was {different}."
    repeated_ticks = misleading + (f" Tick labels showed {different}." * 12)
    assert score(query, correct) > score(query, misleading)
    assert score(query, correct) > score(query, repeated_ticks)


def test_decimal_tokens_do_not_generate_independent_digit_matches():
    query = "Calibration uncertainty 0.01 and error 0.1"
    terms = keyword._scoring_query_tokens(query)
    assert "0.01" in terms and "0.1" in terms
    assert not {"0", "01", "1"}.intersection(terms)
    assert score(query, "Calibration uncertainty was 0.01 and error was 0.1.") > score(
        query, "Calibration uncertainty error. " + "0 01 1 " * 30
    )


def test_exact_phrase_bonus_cannot_erase_negative_sign():
    assert score("uncertainty -0.1", "uncertainty -0.1") > score(
        "uncertainty -0.1", "uncertainty 0.1"
    )


@pytest.mark.parametrize(
    "query",
    [
        "3 donors with 3000 cells and 18000 measurements",
        "Q3 model A12 X-42 software 2026",
        "DOI 10.1000/example.version.2",
        "version v1.2 uses package2.3 or release1.0.2",
        "build 1.2.3 file /0.1/example",
        "1 measurement and 0 controls",
        "measurement1e3 identifier A1E-3",
    ],
)
def test_identifiers_versions_and_integer_counts_keep_existing_tokenization(query):
    assert keyword._scoring_query_tokens(query) == keyword.query_tokens(query)


def test_integer_count_is_retained_when_decimal_is_also_present():
    terms = keyword._scoring_query_tokens("1 measurement at uncertainty 0.1")
    assert "1" in terms
    assert "0.1" in terms
    assert "0" not in terms


@pytest.mark.parametrize("backend", ["keyword", "multi", "bm25"])
def test_search_ranks_actual_numerical_value_above_repeated_plot_ticks(tmp_path, backend):
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    common = (
        "Calibration measurement uncertainty was assessed. "
        "The measurement procedure included repeated observations and recorded the results. "
    ) * 5
    rows = [
        ("CORRECT", common + "Measured uncertainty was 0.1."),
        ("WRONG", common + "Measured uncertainty was 1.0. " * 12),
        ("TICKS", common + "Tick labels: " + "0 1 01 " * 30),
    ]
    for paper_id, text in rows:
        (chunks / (paper_id + ".jsonl")).write_text(
            json.dumps({
                "paper_id": paper_id,
                "chunk_id": paper_id + "_C1",
                "section": "results",
                "text": text,
            }) + "\n",
            encoding="utf-8",
        )
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tfulltext_status\n"
        + "".join(f"{paper_id}\tCalibration report\tchunked\n" for paper_id, _ in rows),
        encoding="utf-8",
    )
    query = "How should calibration be interpreted at uncertainty 0.1?"
    options = {"chunks_dir": chunks, "papers_tsv": papers, "limit": 10}
    if backend == "multi":
        results = keyword.search_chunks_multi([("query", query)], **options)
    elif backend == "bm25":
        index = tmp_path / "index.sqlite"
        fts_index.build_fts_index(chunks, papers, index)
        results = fts_index.search_chunks_bm25(query, index_path=index, **options)
    else:
        results = keyword.search_chunks(query, **options)
    assert results[0]["paper_id"] == "CORRECT"
    assert {row["paper_id"] for row in results} == {"CORRECT", "WRONG", "TICKS"}


def test_title_score_uses_intact_numerical_values():
    query = "How should calibration be interpreted at uncertainty 0.1?"
    terms = keyword._scoring_query_tokens(query)
    normalized_query = keyword.normalize_text(query)
    matching = keyword._metadata_match_score(
        normalized_query, terms, {"title": "Calibration uncertainty 0.1"}, "results", 1
    )
    different = keyword._metadata_match_score(
        normalized_query, terms, {"title": "Calibration uncertainty 1.0"}, "results", 1
    )
    assert matching > different


def test_numeric_canonicalization_preserves_precision_and_bounds_representation():
    assert keyword._numeric_literal_key("0.100000000000000000000000000001") != (
        keyword._numeric_literal_key("0.100000000000000000000000000002")
    )
    assert keyword._numeric_literal_key("1e1000000") == (0, (1,), 1000000)
    assert keyword._numeric_literal_key("-0.0") == keyword._numeric_literal_key("0.0")
