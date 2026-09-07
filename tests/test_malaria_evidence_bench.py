from __future__ import annotations

import json
from pathlib import Path

from pfempire.evaluation.malaria_evidence_bench import (
    benchmark_coverage,
    normalize_item,
    validate_benchmark,
)


def corpus_fixture(tmp_path: Path) -> tuple[Path, Path]:
    papers = tmp_path / "papers.tsv"
    papers.write_text("paper_id\ttitle\nP001\tExample\n", encoding="utf-8")
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    (chunks / "P001.jsonl").write_text(
        json.dumps({"paper_id": "P001", "chunk_id": "P001_C001"}) + "\n",
        encoding="utf-8",
    )
    return papers, chunks


def test_normalize_item_preserves_llm_list_values() -> None:
    item = normalize_item(
        {
            "question": "What is supported?",
            "expected_answer_nuggets": ["First finding", "Second finding"],
            "expected_paper_ids": ["P001", "P001"],
            "abstain_expected": False,
        }
    )

    assert item["expected_answer_nuggets"] == "First finding;Second finding"
    assert item["expected_paper_ids"] == "P001"
    assert item["review_status"] == "candidate"
    assert item["abstain_expected"] == "false"


def test_candidate_can_remain_unreviewed(tmp_path: Path) -> None:
    papers, chunks = corpus_fixture(tmp_path)
    report = validate_benchmark(
        [{"item_id": "MEB0001", "question": "Candidate question"}],
        papers_tsv=papers,
        chunks_dir=chunks,
    )

    assert report["valid"] is True
    assert report["status_counts"] == {"candidate": 1}


def test_unknown_evidence_ids_are_rejected(tmp_path: Path) -> None:
    papers, chunks = corpus_fixture(tmp_path)
    report = validate_benchmark(
        [
            {
                "item_id": "MEB0001",
                "question": "Candidate question",
                "expected_paper_ids": ["P999"],
                "expected_chunk_ids": ["P999_C001"],
            }
        ],
        papers_tsv=papers,
        chunks_dir=chunks,
    )

    issues = {issue["issue"] for issue in report["issues"]}
    assert report["valid"] is False
    assert "unknown_paper_id:P999" in issues
    assert "unknown_chunk_id:P999_C001" in issues


def test_accepted_gold_requires_review_metadata_and_eval_split(tmp_path: Path) -> None:
    papers, chunks = corpus_fixture(tmp_path)
    report = validate_benchmark(
        [
            {
                "item_id": "MEB0001",
                "review_status": "accepted",
                "question": "Gold question",
                "expected_paper_ids": ["P001"],
            }
        ],
        papers_tsv=papers,
        chunks_dir=chunks,
        require_gold=True,
    )

    issues = {issue["issue"] for issue in report["issues"]}
    assert "accepted_item_missing_eval_split" in issues
    assert "accepted_item_missing_reviewer" in issues
    assert "accepted_item_missing_answer_nuggets" in issues


def test_reviewed_gold_item_is_valid(tmp_path: Path) -> None:
    papers, chunks = corpus_fixture(tmp_path)
    report = validate_benchmark(
        [
            {
                "item_id": "MEB0001",
                "split": "test",
                "review_status": "accepted",
                "question": "Gold question",
                "expected_answer_nuggets": ["Supported answer"],
                "expected_paper_ids": ["P001"],
                "expected_chunk_ids": ["P001_C001"],
                "reviewer": "reviewer-01",
            }
        ],
        papers_tsv=papers,
        chunks_dir=chunks,
        require_gold=True,
    )

    assert report["valid"] is True
    assert report["issues"] == []


def test_benchmark_coverage_counts_evidence_and_facets() -> None:
    coverage = benchmark_coverage(
        [
            {
                "item_id": "MEB0001",
                "question": "Question one",
                "task_mode": "dataset_finder",
                "expected_facets": ["severe_malaria", "host_omics"],
                "expected_paper_ids": ["P001"],
                "reviewer": "reviewer-01",
            },
            {
                "item_id": "MEB0002",
                "question": "Question two",
                "abstain_expected": True,
                "expected_facets": ["severe_malaria"],
            },
        ]
    )

    assert coverage["n_items"] == 2
    assert coverage["n_with_reviewer"] == 1
    assert coverage["n_with_paper_gold"] == 1
    assert coverage["n_abstention"] == 1
    assert coverage["facets"] == {"severe_malaria": 2, "host_omics": 1}
