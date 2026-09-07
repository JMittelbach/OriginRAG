from pfempire.analysis.evidence_pack import build_evidence_pack


def test_current_evidence_pack_remains_available():
    rows = [
        {
            "chunk_id": "c",
            "paper_id": "p",
            "text": "direct result",
            "score": 10,
            "rank": 1,
            "section": "results",
        }
    ]
    assert build_evidence_pack("q", rows, max_chunks=1)["chunks"][0]["paper_id"] == "p"
