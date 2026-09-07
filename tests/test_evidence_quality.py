from __future__ import annotations

from pfempire.analysis.evidence_quality import score_evidence_pack


def test_evidence_pack_with_multiple_clean_sources_is_good_or_usable() -> None:
    pack = {
        "evidence_items": [
            item("P1", "results", 30),
            item("P2", "discussion", 28),
            item("P3", "abstract", 26),
            item("P4", "results", 24),
            item("P5", "discussion", 22),
        ]
    }

    grade = score_evidence_pack(pack)

    assert grade["grade"] == "good"
    assert grade["recommended_action"] == "answer"


def test_evidence_pack_with_references_is_weak_or_bad() -> None:
    pack = {
        "evidence_items": [
            item("P1", "references", 30, quality_flags=["reference_like"]),
            item("P1", "references", 28, quality_flags=["reference_like"]),
            item("P2", "table", 20, quality_flags=["table_like"]),
        ]
    }

    grade = score_evidence_pack(pack)

    assert grade["grade"] in {"weak", "bad"}
    assert "contains_references" in grade["reasons"]


def test_evidence_pack_reports_expected_key_papers() -> None:
    pack = {
        "evidence_items": [
            {
                **item("P1", "results", 30),
                "author": "Turner",
                "year": "2013",
                "title": "PfEMP1 and severe malaria",
            },
            {
                **item("P2", "discussion", 28),
                "author": "Jespersen",
                "year": "2016",
                "title": "CIDRalpha1 domains",
            },
            item("P3", "abstract", 26),
        ]
    }

    grade = score_evidence_pack(pack, expected_sources=["Turner 2013", "Missing 2020"])

    assert grade["metrics"]["expected_key_papers_found"] == ["Turner 2013"]
    assert grade["metrics"]["expected_key_papers_missing"] == ["Missing 2020"]
    assert grade["expected_sources_found"] == ["Turner 2013"]
    assert grade["missing_expected_sources"] == ["Missing 2020"]


def item(paper_id: str, section: str, score: float, quality_flags: list[str] | None = None) -> dict:
    return {
        "paper_id": paper_id,
        "section": section,
        "score": score,
        "quality_flags": quality_flags or [],
        "title": f"{paper_id} title",
    }
