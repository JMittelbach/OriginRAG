from __future__ import annotations

import csv
import json
from pathlib import Path

from originrag.retrieval.adaptive import adaptive_retrieve, write_adaptive_outputs


def test_adaptive_retrieval_runs_on_tiny_synthetic_corpus(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    rows = [
        {
            "chunk_id": "P1_CHUNK_0001",
            "paper_id": "P1",
            "chunk_index": 1,
            "page_start": 1,
            "page_end": 1,
            "section": "results",
            "text": (
                "PfEMP1 CIDRalpha1 domains bind EPCR in severe malaria and cerebral malaria. "
                "The results describe endothelial binding, cytoadherence, parasite sequestration, "
                "and clinical severity in children. "
            )
            * 10,
        },
        {
            "chunk_id": "P2_CHUNK_0001",
            "paper_id": "P2",
            "chunk_index": 1,
            "page_start": 2,
            "page_end": 2,
            "section": "discussion",
            "text": (
                "Cytoadherence and sequestration may contribute to cerebral malaria pathogenesis. "
                "The discussion connects infected erythrocyte adhesion, endothelial activation, "
                "and PfEMP1-mediated host parasite interactions. "
            )
            * 10,
        },
        {
            "chunk_id": "P3_CHUNK_0001",
            "paper_id": "P3",
            "chunk_index": 1,
            "page_start": 3,
            "page_end": 3,
            "section": "abstract",
            "text": (
                "DC8 and DC13 PfEMP1 variants are linked to severe malaria in children. "
                "The abstract reports domain cassette enrichment, CIDRalpha1 features, and "
                "clinical associations with severe disease. "
            )
            * 10,
        },
    ]
    with (chunks_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    papers = tmp_path / "papers.tsv"
    with papers.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=["paper_id", "author", "year", "title", "journal", "category", "topics"],
        )
        writer.writeheader()
        writer.writerow({"paper_id": "P1", "author": "Turner", "year": "2013", "title": "PfEMP1 EPCR binding", "journal": "Nature", "category": "var_pfemp1", "topics": "severe_malaria"})
        writer.writerow({"paper_id": "P2", "author": "Avril", "year": "2012", "title": "Cytoadherence and cerebral malaria", "journal": "JID", "category": "var_pfemp1", "topics": "cerebral_malaria"})
        writer.writerow({"paper_id": "P3", "author": "Jespersen", "year": "2016", "title": "DC8 DC13 PfEMP1", "journal": "EMBO", "category": "var_pfemp1", "topics": "severe_malaria"})

    result = adaptive_retrieve(
        "What evidence links PfEMP1 to cerebral malaria?",
        task_mode="evidence_qa",
        chunks_dir=chunks_dir,
        papers_tsv=papers,
        max_chunks=6,
        max_per_paper=2,
        expected_sources=["Turner 2013"],
    )

    assert result["query_plan"]["queries"]
    assert len(result["query_plan"]["queries"]) > 1
    assert result["per_subquery_results"]
    assert all("query_id" in hit for hit in result["per_subquery_results"])
    assert all("subquery_rank" in hit for hit in result["per_subquery_results"])
    assert result["evidence_pack"]["n_chunks"] >= 2
    assert result["evidence_quality"]["grade"] in {"usable", "weak", "good"}
    assert "expected_key_papers_found" in result["evidence_quality"]["metrics"]
    assert result["diagnostics"]["n_raw_candidates"] >= 2
    assert all("rrf_score" in hit for hit in result["retrieval_candidates"])
    assert all("contributing_queries" in hit for hit in result["retrieval_candidates"])
    assert all("query_ranks" in hit for hit in result["retrieval_candidates"])

    out_dir = tmp_path / "adaptive_output"
    write_adaptive_outputs(result, out_dir)
    assert (out_dir / "per_subquery_results.tsv").exists()
    assert (out_dir / "retrieval_candidates.tsv").exists()

    hybrid_result = adaptive_retrieve(
        "What evidence links PfEMP1 to cerebral malaria?",
        task_mode="evidence_qa",
        chunks_dir=chunks_dir,
        papers_tsv=papers,
        retriever="hybrid",
        max_chunks=3,
        max_per_paper=1,
    )
    assert hybrid_result["retriever_requested"] == "hybrid"
    assert hybrid_result["retriever"] == "keyword"
    assert hybrid_result["retriever_fallback_reason"] == "semantic_backend_unavailable"
    assert hybrid_result["per_subquery_results"]
