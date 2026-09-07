from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ask_corpus import rank_candidates_by_paper_rrf  # noqa: E402


def test_paper_rrf_combines_query_support_across_different_chunks() -> None:
    candidates = [
        {"paper_id": "P1", "chunk_id": "P1_C1", "query_ranks": {"Q1": 2}, "rrf_score": 0.016, "score": 20},
        {"paper_id": "P1", "chunk_id": "P1_C2", "query_ranks": {"Q2": 3}, "rrf_score": 0.015, "score": 18},
        {"paper_id": "P2", "chunk_id": "P2_C1", "query_ranks": {"Q1": 1}, "rrf_score": 0.017, "score": 25},
    ]

    ranked = rank_candidates_by_paper_rrf(candidates)

    assert ranked[0]["paper_id"] == "P1"
    assert ranked[0]["paper_query_hits"] == 2
    assert ranked[0]["paper_relevance_score"] > ranked[-1]["paper_relevance_score"]
    assert ranked[-1]["paper_id"] == "P2"


def test_direct_relevance_can_rescue_specific_primary_paper() -> None:
    candidates = [
        {"paper_id": "BROAD", "chunk_id": "B1", "query_ranks": {"Q1": 1, "Q2": 1}, "rrf_score": 0.03, "score": 45, "metadata_score": 2},
        {"paper_id": "DIRECT", "chunk_id": "D1", "query_ranks": {"Q1": 5, "Q2": 8}, "rrf_score": 0.025, "score": 120, "metadata_score": 20},
    ]

    ranked = rank_candidates_by_paper_rrf(candidates)

    assert ranked[0]["paper_id"] == "DIRECT"
