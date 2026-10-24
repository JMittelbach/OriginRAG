from __future__ import annotations

import inspect
import json
import random
import re
import string
import unicodedata
from itertools import product
from pathlib import Path

import pytest

from originrag.retrieval import fts_index, keyword, paper_retrieval


def original_normalize_text(value):
    value = (value or "").lower()
    for source, target in {
        "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta",
        "–": "-", "—": "-", "\u00ad": "", "\ufb01": "fi", "\ufb02": "fl",
    }.items():
        value = value.replace(source, target)
    value = "".join(character for character in unicodedata.normalize("NFKD", value)
                    if not unicodedata.combining(character))
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


@pytest.mark.parametrize("value", [
    None, "", " \t\n", "ASCII: MATRIX 0.05 _CODE-17", "fi ﬂow ﬁnal", "Αβγδ αβγδ",
    "Café cafe\u0301", "İ I ı i", "Straße STRASSE", "測定 matrix", "soft\u00adhyphen",
    "A–B—C", "① Å K Ⅳ", "😀matrix😀", "a\x00b\x7fc", "µm μm", "\ud800a",
])
def test_normalization_fast_path_matches_original_examples(value):
    assert keyword.normalize_text(value) == original_normalize_text(value)


def test_normalization_fast_path_matches_seeded_mixed_unicode_inputs():
    rng = random.Random(42)
    alphabets = [string.printable, string.printable + "Αβγδµμ–—ﬁﬂéä①\u0301\u00adİ😀"]
    for alphabet in alphabets:
        for _ in range(400):
            value = "".join(rng.choice(alphabet) for _ in range(rng.randrange(300)))
            assert keyword.normalize_text(value) == original_normalize_text(value)
    for _ in range(400):
        value = "".join(chr(rng.randrange(0x110000)) for _ in range(rng.randrange(80)))
        assert keyword.normalize_text(value) == original_normalize_text(value)


def test_ascii_normalization_does_not_enter_unicode_decomposition(monkeypatch):
    def forbidden(*args):
        pytest.fail("ASCII normalization must not perform Unicode decomposition")

    with monkeypatch.context() as patch:
        patch.setattr(keyword.unicodedata, "normalize", forbidden)
        patch.setattr(keyword.unicodedata, "combining", forbidden)
        result = keyword.normalize_text(" ASCII_42:  matrix\n")
    assert result == "ascii 42 matrix"


def test_presence_checks_match_unchanged_occurrence_counts():
    texts = ["", "matrix matrix matrix", "prematrix matrixlike", "matrix2 matrix_2",
             "alpha beta gamma", "matrix method\nother", "cafe final flow", "matrix-method"]
    terms = ["", "matrix", "matrix method", "α", "alpha beta", "CAFÉ", "ﬁnal", ".+", "\t"]
    for text, term in product(texts, terms):
        assert keyword.contains_normalized_term(text, term) is (
            keyword.count_normalized_occurrences(text, term) > 0)
    assert keyword.count_normalized_occurrences("matrix matrix matrix", "matrix") == 3


def test_presence_checks_use_first_match_without_counting(monkeypatch):
    class SearchOnly:
        def search(self, text):
            assert text == "matrix matrix"
            return object()

        def findall(self, text):
            pytest.fail("Existence check must not enumerate every match")

    monkeypatch.setattr(keyword, "_normalized_term_pattern", lambda term: SearchOnly())
    assert keyword.contains_normalized_term("matrix matrix", "matrix") is True


@pytest.mark.parametrize("query", [
    "numerical calibration", "DOI 10.1000/Example.", "PMID: 123", "PMC456",
    "citation key: Example2024", "paper id: Example2024", "MATRIX β calibration",
    "Find DOI 10.1000/example.extension PMID#123 PMC456", "", "unrelated stars",
])
@pytest.mark.parametrize("mode", ["evidence_qa", "dataset_finder", "method_comparison"])
def test_prepared_paper_search_keeps_public_scores_rationales_and_order(
    monkeypatch, tmp_path, query, mode,
):
    rows = [
        {"paper_id": "P2", "title": "Numerical calibration", "year": "2024",
         "doi": "10.1000/example", "pmid": "123", "pmcid": "PMC456", "bib_key": "Example2024",
         "topics": "numerical", "methods": "matrix β method", "fulltext_status": "chunked",
         "activation_status": "active_full"},
        {"paper_id": "P1", "title": "Numerical calibration", "year": "2024",
         "doi": "10.1000/example.extension", "methods": "matrix β method",
         "notes": "dataset cohort pipeline"},
        {"paper_id": "P3", "title": "Stars", "year": "unknown", "pmid": "1234"},
        {"paper_id": "EMPTY"},
    ]
    monkeypatch.setattr(paper_retrieval, "read_metadata_rows", lambda path: [dict(row) for row in rows])
    tokens = keyword.query_tokens(query)
    boosts = keyword.infer_query_category_boosts(query)
    expected = []
    for row in rows:
        score, rationale = paper_retrieval.score_paper(row, query, tokens, boosts, mode)
        if score > 0:
            expected.append((row["paper_id"], round(score, 3), "; ".join(rationale)))
    years = {row["paper_id"]: paper_retrieval.safe_year(row.get("year")) for row in rows}
    expected.sort(key=lambda item: (-item[1], -years[item[0]], item[0]))

    calls = []
    prepare = paper_retrieval._prepare_query_features

    def counted_prepare(value):
        calls.append(value)
        return prepare(value)

    monkeypatch.setattr(paper_retrieval, "_prepare_query_features", counted_prepare)
    result = paper_retrieval.search_papers(query, tmp_path / "unused.tsv", task_mode=mode, limit=3)

    assert calls == [query]
    assert [(row["paper_id"], row["score"], row["rationale"]) for row in result] == expected[:3]
    assert [row["rank"] for row in result] == list(range(1, len(result) + 1))


def test_public_paper_scorer_signature_and_query_features_are_preserved():
    assert list(inspect.signature(paper_retrieval.score_paper).parameters) == [
        "row", "query", "tokens", "category_boosts", "task_mode", "local_fulltext_available"]
    query = "DOI https://doi.org/10.1000/Example. PMID: 123 pmc456 citation key: Example2024"
    features = paper_retrieval._prepare_query_features(query)
    assert features.dois == frozenset({"10.1000/example"})
    assert features.pmids == frozenset({"123"})
    assert features.pmcids == frozenset({"PMC456"})
    assert features.bib_keys == frozenset({"example2024"})
    with pytest.raises(AttributeError):
        features.dois.add("different")


def test_missing_metadata_still_returns_no_matches(tmp_path):
    assert paper_retrieval.search_papers("numerical calibration", tmp_path / "missing.tsv") == []


@pytest.mark.parametrize("chunk_ids,paper_ids,expected", [
    ({"C1", "C2"}, {"P1", "EMPTY"}, [("mixed.jsonl", 1), ("mixed.jsonl", 2), ("mixed.jsonl", 3)]),
    ({"C1"}, {"P1"}, [("mixed.jsonl", 1), ("mixed.jsonl", 2)]),
    (set(), {"EMPTY"}, []),
    (set(), set(), []),
])
def test_candidate_coverage_keeps_duplicates_zero_counts_and_source_order(
    tmp_path: Path, chunk_ids, paper_ids, expected,
):
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text("paper_id\ttitle\nP1\tCalibration\nP2\tMeasurements\nEMPTY\tMetadata only\n")
    rows = [{"paper_id": "P1", "chunk_id": "C1", "text": "calibration"},
            {"paper_id": "P1", "chunk_id": "C1", "text": "calibration duplicate location"},
            {"paper_id": "P2", "chunk_id": "C2", "text": "measurements"}]
    (chunks / "mixed.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    index = tmp_path / "index.sqlite"
    fts_index.build_fts_index(chunks, papers, index)

    records = fts_index.load_index_candidate_records(
        index, chunks, chunk_ids=chunk_ids, paper_ids=paper_ids, record_store_available=True)

    assert [(Path(path).name, line) for path, line, _ in records] == expected
    assert [row for _, _, row in records] == [rows[line - 1] for _, line in expected]
    with pytest.raises(fts_index.RetrievalIndexError, match="cover every FTS chunk candidate"):
        fts_index.load_index_candidate_records(
            index, chunks, chunk_ids={"MISSING"}, paper_ids=paper_ids, record_store_available=True)

