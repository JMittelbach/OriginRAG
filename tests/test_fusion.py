from __future__ import annotations

from originrag.retrieval.fusion import reciprocal_rank_fusion


def test_reciprocal_rank_fusion_combines_ranked_lists() -> None:
    fused = reciprocal_rank_fusion(
        [
            [
                {"chunk_id": "C1", "score": 10, "query_id": "Q1"},
                {"chunk_id": "C2", "score": 8, "query_id": "Q1"},
            ],
            [
                {"chunk_id": "C2", "score": 9, "query_id": "Q2"},
                {"chunk_id": "C3", "score": 7, "query_id": "Q2"},
            ],
        ],
        k=60,
    )

    by_id = {item["chunk_id"]: item for item in fused}

    assert fused[0]["chunk_id"] == "C2"
    assert by_id["C2"]["contributing_queries"] == ["Q1", "Q2"]
    assert by_id["C2"]["query_ranks"] == {"Q1": 2, "Q2": 1}
    assert by_id["C2"]["rrf_score"] > by_id["C1"]["rrf_score"]


def test_fusion_retains_metadata_from_strongest_duplicate_hit() -> None:
    result = reciprocal_rank_fusion(
        [
            [{"chunk_id": "C1", "query_id": "Q1", "score": 1, "snippet": "weak"}],
            [{"chunk_id": "C1", "query_id": "Q2", "score": 100, "snippet": "strong"}],
        ]
    )

    assert result[0]["score"] == 100
    assert result[0]["snippet"] == "strong"
    assert result[0]["contributing_queries"] == ["Q1", "Q2"]
