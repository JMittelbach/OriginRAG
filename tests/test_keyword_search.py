from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from originrag.retrieval.keyword import (
    _make_snippet,
    _normalize_with_offsets,
    infer_query_category_boosts,
    iter_chunk_records,
    normalize_text,
    query_tokens,
    search_chunks,
    search_chunks_multi,
)
from originrag.retrieval.metadata import load_paper_metadata


def test_linear_snippet_normalization_matches_retrieval_normalization() -> None:
    text = "  CIDRα1–EPCR, β-chain; ﬁnal ﬂow; cérébral!  "
    normalized, offsets = _normalize_with_offsets(text)

    assert normalized == normalize_text(text)
    assert "cerebral" in normalized
    assert len(offsets) == len(normalized)
    assert text[offsets[normalized.index("cidralpha1")]] == "C"
    assert text[offsets[normalized.index("cerebral")]] == "c"


def test_snippet_starts_near_normalized_greek_match() -> None:
    text = "Unrelated preface. " * 30 + "CIDRα1 binds EPCR in severe malaria."
    snippet = _make_snippet("cidralpha1", ["cidralpha1"], text, window=120)

    assert "CIDRα1 binds EPCR" in snippet


def test_search_materializes_preview_only_for_returned_candidates(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    rows = [
        {
            "chunk_id": f"P{index}_CHUNK_0001",
            "paper_id": f"P{index}",
            "text": ("PfEMP1 EPCR severe malaria evidence. " * (10 + index)),
        }
        for index in range(1, 5)
    ]
    (chunks_dir / "chunks.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    results = search_chunks("PfEMP1 EPCR", chunks_dir, limit=2, max_per_paper=1)

    assert len(results) == 2
    assert all(result["snippet"] and result["text_preview"] for result in results)
    assert all(not any(key.startswith("_snippet_") for key in result) for result in results)


def test_exact_phrase_match_scores_above_unrelated_text(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    chunk_path = chunks_dir / "P1.jsonl"
    rows = [
        {
            "chunk_id": "P1_CHUNK_0001",
            "paper_id": "P1",
            "chunk_index": 1,
            "page_start": 1,
            "page_end": 1,
            "text": "PfEMP1 EPCR severe malaria evidence in children. " * 20,
        },
        {
            "chunk_id": "P2_CHUNK_0001",
            "paper_id": "P2",
            "chunk_index": 1,
            "page_start": 1,
            "page_end": 1,
            "text": "Severe malaria can involve host inflammation and clinical anemia. " * 20,
        },
    ]
    with chunk_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    results = search_chunks("PfEMP1 EPCR severe malaria", chunks_dir, limit=2)

    assert len(results) == 2
    assert results[0]["chunk_id"] == "P1_CHUNK_0001"
    assert results[0]["score"] > results[-1]["score"]


def test_keyword_terms_do_not_match_inside_other_words(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    rows = [
        {
            "chunk_id": "EXACT_CHUNK_0001",
            "paper_id": "EXACT",
            "text": "Host age was associated with var gene expression. " * 12,
        },
        {
            "chunk_id": "SUBSTRING_CHUNK_0001",
            "paper_id": "SUBSTRING",
            "text": "Various developmental stages were measured carefully. " * 12,
        },
    ]
    (chunks_dir / "chunks.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    results = search_chunks("var age", chunks_dir, limit=10)

    assert [result["chunk_id"] for result in results] == ["EXACT_CHUNK_0001"]


def test_category_inference_does_not_treat_various_as_var() -> None:
    boosts = infer_query_category_boosts("Various developmental stages were compared")

    assert "var_pfemp1" not in boosts


def test_metadata_loader_tolerates_missing_optional_columns(tmp_path: Path) -> None:
    papers_path = tmp_path / "papers.tsv"
    with papers_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=["paper_id", "title", "year"])
        writer.writeheader()
        writer.writerow({"paper_id": "P1", "title": "A paper", "year": "2024"})

    metadata = load_paper_metadata(papers_path)

    assert metadata["P1"]["title"] == "A paper"
    assert metadata["P1"]["author"] == ""
    assert metadata["P1"]["journal"] == ""


def test_explicit_vaccine_query_can_retrieve_scoped_vaccine_paper(
    tmp_path: Path,
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    (chunks_dir / "V1.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "V1_CHUNK_0001",
                "paper_id": "V1",
                "text": "STEVOR fusion antigen induced mouse IgG and T-cell responses.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    papers_path = tmp_path / "papers.tsv"
    with papers_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=[
                "paper_id",
                "title",
                "domain",
                "topics",
                "paper_type",
                "fulltext_status",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "paper_id": "V1",
                "title": "A STEVOR fusion antigen vaccine construct",
                "domain": "malaria",
                "topics": "var_pfemp1",
                "paper_type": "journal article",
                "fulltext_status": "chunked",
            }
        )

    explicit = search_chunks(
        "STEVOR vaccine mouse immunogenicity",
        chunks_dir,
        papers_tsv=papers_path,
    )
    generic = search_chunks(
        "STEVOR severe malaria",
        chunks_dir,
        papers_tsv=papers_path,
    )

    assert [row["paper_id"] for row in explicit] == ["V1"]
    assert generic == []


def test_metadata_cache_detects_same_size_rewrite_within_one_second(
    tmp_path: Path,
) -> None:
    papers_path = tmp_path / "papers.tsv"
    papers_path.write_text("paper_id\ttitle\nP1\tAAA\n", encoding="utf-8")
    second = 2_000_000_000_000_000_000
    os.utime(papers_path, ns=(second + 100, second + 100))
    assert load_paper_metadata(papers_path)["P1"]["title"] == "AAA"

    papers_path.write_text("paper_id\ttitle\nP1\tBBB\n", encoding="utf-8")
    os.utime(papers_path, ns=(second + 200, second + 200))

    assert load_paper_metadata(papers_path)["P1"]["title"] == "BBB"


def test_chunk_cache_detects_same_size_rewrite_within_one_second(
    tmp_path: Path,
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    path = chunks_dir / "chunks.jsonl"
    first = json.dumps({"chunk_id": "C1", "paper_id": "P1", "text": "AAA"}) + "\n"
    second_text = first.replace("AAA", "BBB")
    path.write_text(first, encoding="utf-8")
    second = 2_000_000_000_000_000_000
    os.utime(path, ns=(second + 100, second + 100))
    assert iter_chunk_records(chunks_dir)[0][2]["text"] == "AAA"

    path.write_text(second_text, encoding="utf-8")
    os.utime(path, ns=(second + 200, second + 200))

    assert iter_chunk_records(chunks_dir)[0][2]["text"] == "BBB"


def test_candidate_sources_preserve_chunk_or_paper_filter_and_section_context(
    tmp_path: Path,
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    p1_rows = [
        {"chunk_id": "P1_C1", "paper_id": "P1", "text": "Results"},
        {
            "chunk_id": "P1_C2",
            "paper_id": "P1",
            "text": "EPCR binding was observed in the experiment. " * 12,
        },
        {
            "chunk_id": "P1_C3",
            "paper_id": "P1",
            "text": "EPCR binding was reproduced in another assay. " * 12,
        },
    ]
    p2_rows = [
        {
            "chunk_id": "P2_C1",
            "paper_id": "P2",
            "section": "results",
            "text": "EPCR binding was measured in a second paper. " * 12,
        }
    ]
    p3_rows = [
        {
            "chunk_id": "P3_C1",
            "paper_id": "P3",
            "section": "results",
            "text": "EPCR binding was measured in an excluded paper. " * 12,
        }
    ]
    for paper_id, rows in (("P1", p1_rows), ("P2", p2_rows), ("P3", p3_rows)):
        (chunks_dir / f"{paper_id}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

    kwargs = {
        "query": "EPCR binding experiment",
        "chunks_dir": chunks_dir,
        "limit": 10,
        "max_per_paper": 10,
        "include_low_quality": True,
        "candidate_chunk_ids": {"P2_C1"},
        "candidate_paper_ids": {"P1"},
    }
    complete_scan = search_chunks(**kwargs)
    narrowed_scan = search_chunks(
        **kwargs,
        candidate_source_paper_ids={"P1", "P2"},
        candidate_source_layout_verified=True,
    )

    assert narrowed_scan == complete_scan
    assert {result["chunk_id"] for result in narrowed_scan} == {
        "P1_C2",
        "P1_C3",
        "P2_C1",
    }
    p1_result = next(result for result in narrowed_scan if result["chunk_id"] == "P1_C2")
    assert p1_result["section"] == "results"
    assert p1_result["section_markers"] == ["previous_section_context"]
    assert p1_result["source_file"] == str((chunks_dir / "P1.jsonl").resolve())
    assert p1_result["line_number"] == 2


def test_candidate_source_narrowing_falls_back_for_aggregate_chunk_file(
    tmp_path: Path,
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    aggregate = chunks_dir / "chunks.jsonl"
    aggregate.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {"chunk_id": "P1_C1", "paper_id": "P1", "text": "target evidence"},
                {"chunk_id": "P2_C1", "paper_id": "P2", "text": "other evidence"},
            )
        ),
        encoding="utf-8",
    )

    records = iter_chunk_records(
        chunks_dir,
        candidate_paper_ids={"P1"},
        canonical_layout_verified=True,
    )

    assert [record[2]["chunk_id"] for record in records] == ["P1_C1", "P2_C1"]
    assert all(record[0] == str(aggregate.resolve()) for record in records)


def test_search_limits_results_per_paper(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    rows = [
        {
            "chunk_id": "P1_CHUNK_0001",
            "paper_id": "P1",
            "chunk_index": 1,
            "page_start": 1,
            "page_end": 1,
            "text": "PfEMP1 EPCR severe malaria CIDRalpha1. " * 30,
        },
        {
            "chunk_id": "P1_CHUNK_0002",
            "paper_id": "P1",
            "chunk_index": 2,
            "page_start": 2,
            "page_end": 2,
            "text": "PfEMP1 EPCR severe malaria var genes. " * 30,
        },
        {
            "chunk_id": "P2_CHUNK_0001",
            "paper_id": "P2",
            "chunk_index": 1,
            "page_start": 1,
            "page_end": 1,
            "text": "PfEMP1 EPCR severe malaria in another cohort. " * 20,
        },
    ]
    with (chunks_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    results = search_chunks("PfEMP1 EPCR severe malaria", chunks_dir, limit=3, max_per_paper=1)

    assert len(results) == 2
    assert [result["paper_id"] for result in results].count("P1") == 1
    assert [result["paper_id"] for result in results].count("P2") == 1


def test_query_category_boost_prefers_sex_difference_papers(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    rows = [
        {
            "chunk_id": "SEX_CHUNK_0001",
            "paper_id": "SEX",
            "chunk_index": 1,
            "page_start": 1,
            "page_end": 1,
            "text": "Sex differences and host immunity shape severe malaria outcomes. " * 8,
        },
        {
            "chunk_id": "VAR_CHUNK_0001",
            "paper_id": "VAR",
            "chunk_index": 1,
            "page_start": 1,
            "page_end": 1,
            "text": "Host immunity and severe malaria outcomes are linked to var gene expression. " * 8,
        },
    ]
    with (chunks_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    papers_path = tmp_path / "papers.tsv"
    with papers_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=["paper_id", "title", "category"],
        )
        writer.writeheader()
        writer.writerow({"paper_id": "SEX", "title": "Sex paper", "category": "sex_diff"})
        writer.writerow({"paper_id": "VAR", "title": "Var paper", "category": "var_pfemp1"})

    results = search_chunks(
        "Find gaps connecting sex differences, host immunity, and severe malaria outcomes.",
        chunks_dir,
        papers_tsv=papers_path,
        limit=2,
        max_per_paper=1,
    )

    assert results[0]["paper_id"] == "SEX"
    assert results[0]["category_boost"] > 0


def test_source_lookup_boost_prefers_matching_sample_size_context(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    rows = [
        {
            "chunk_id": "OKIRING_CHUNK_0001",
            "paper_id": "OKIRING",
            "chunk_index": 1,
            "page_start": 1,
            "page_end": 1,
            "text": (
                "Supporting this hypothesis, another recent cohort study from Western Kenya "
                "found associations that were over twice as strong in females."
            ),
        },
        {
            "chunk_id": "NDUGWA_CHUNK_0001",
            "paper_id": "NDUGWA",
            "chunk_index": 1,
            "page_start": 1,
            "page_end": 1,
            "text": (
                "The study used a large Kenyan cohort from Kilifi and included samples collected "
                "from about 700 children presenting with severe or non-severe malaria."
            ),
        },
    ]
    with (chunks_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    papers_path = tmp_path / "papers.tsv"
    with papers_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=["paper_id", "author", "year", "title", "journal", "doi"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "paper_id": "OKIRING",
                "author": "Okiring",
                "year": "2022",
                "title": "Gender difference in the incidence of malaria",
                "journal": "Malaria Journal",
                "doi": "",
            }
        )
        writer.writerow(
            {
                "paper_id": "NDUGWA",
                "author": "Ndugwa",
                "year": "2026",
                "title": "Expression of specific var gene subtypes",
                "journal": "eLife",
                "doi": "10.7554/eLife.110810.1",
            }
        )

    results = search_chunks(
        "Which paper had the Kenya cohort with over 700 patients?",
        chunks_dir,
        papers_tsv=papers_path,
        limit=2,
        max_per_paper=1,
    )

    assert results[0]["paper_id"] == "NDUGWA"
    assert results[0]["source_boost"] > results[1]["source_boost"]
    assert results[0]["doi"] == "10.7554/eLife.110810.1"


def test_broad_virulence_query_expands_to_pfemp1_mechanisms() -> None:
    tokens = query_tokens("Warum ist Plasmodium falciparum so virulent?")

    assert "pfemp1" in tokens
    assert "cytoadherence" in tokens
    assert "sequestration" in tokens
    assert "epcr" in tokens


def test_multi_query_search_finds_hits_from_distinct_query_variants(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    rows = [
        {
            "chunk_id": "EPCR_CHUNK_0001",
            "paper_id": "EPCR",
            "chunk_index": 1,
            "page_start": 1,
            "page_end": 1,
            "text": "PfEMP1 EPCR endothelial binding severe malaria. " * 10,
        },
        {
            "chunk_id": "VSA_CHUNK_0001",
            "paper_id": "VSA",
            "chunk_index": 1,
            "page_start": 1,
            "page_end": 1,
            "text": "Antigenic variation and persistent infection by variant surface antigens. " * 10,
        },
    ]
    with (chunks_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    results = search_chunks_multi(
        [("main", "EPCR severe malaria"), ("context", "antigenic variation persistent infection")],
        chunks_dir,
        limit=2,
        max_per_paper=1,
    )

    assert {result["paper_id"] for result in results} == {"EPCR", "VSA"}
    assert all(result["retrieval_query_labels"] for result in results)


def test_title_match_boosts_relevant_paper_with_relevant_chunk(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    rows = [
        {
            "chunk_id": "TARGET_CHUNK_0001",
            "paper_id": "TARGET",
            "chunk_index": 1,
            "text": "The Results show endothelial binding in severe malaria.",
            "section": "results",
        },
        {
            "chunk_id": "GENERIC_CHUNK_0001",
            "paper_id": "GENERIC",
            "chunk_index": 1,
            "text": "The Results show endothelial binding in severe malaria.",
            "section": "results",
        },
    ]
    with (chunks_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    papers = tmp_path / "papers.tsv"
    with papers.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=["paper_id", "title"])
        writer.writeheader()
        writer.writerow(
            {
                "paper_id": "TARGET",
                "title": "EPCR-binding PfEMP1 variants in severe malaria",
            }
        )
        writer.writerow({"paper_id": "GENERIC", "title": "A general malaria review"})

    results = search_chunks(
        "EPCR-binding PfEMP1 variants in severe malaria",
        chunks_dir,
        papers_tsv=papers,
        limit=2,
        max_per_paper=1,
    )

    assert results[0]["paper_id"] == "TARGET"
    assert results[0]["metadata_score"] > results[1]["metadata_score"]


def test_title_match_does_not_boost_supplement_chunks(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    (chunks_dir / "chunks.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "P1_CHUNK_0001",
                "paper_id": "P1",
                "chunk_index": 1,
                "text": (
                    "Supplementary experiments discuss EPCR binding in severe malaria and "
                    "provide additional assay context. " * 8
                ),
                "section": "supplement",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\nP1\tEPCR-binding PfEMP1 variants in severe malaria\n",
        encoding="utf-8",
    )

    results = search_chunks(
        "EPCR-binding PfEMP1 variants in severe malaria",
        chunks_dir,
        papers_tsv=papers,
        limit=1,
        include_low_quality=True,
    )

    assert results[0]["metadata_score"] == 0.0
