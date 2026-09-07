from __future__ import annotations

from originrag.analysis.evidence_table import build_evidence_table_rows, format_evidence_table_markdown


def test_build_evidence_table_rows_adds_contribution() -> None:
    pack = {
        "question": "Find gaps in sex differences in malaria.",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "MAL_SEX_0010",
                "author": "Briggs",
                "year": "2020",
                "title": "Sex-based differences",
                "journal": "eLife",
                "section": "discussion",
                "page_range": "8",
                "chunk_id": "C1",
                "score": 42.0,
                "quality_flags": [],
                "text": "There is still little known about sex-based biological differences that may mediate immunity to malaria.",
            }
        ],
    }

    rows = build_evidence_table_rows(pack)

    assert rows[0]["evidence_id"] == "E001"
    assert rows[0]["paper_id"] == "MAL_SEX_0010"
    assert "limited mechanistic knowledge" in rows[0]["contribution"]


def test_format_evidence_table_markdown_contains_core_columns() -> None:
    markdown = format_evidence_table_markdown(
        {
            "question": "Question",
            "chunks": [
                {
                    "evidence_id": "E001",
                    "author": "Ceesay",
                    "year": "2025",
                    "section": "results",
                    "text": "Asymptomatic infections had broader PfEMP1 antibody recognition.",
                }
            ],
        }
    )

    assert "| Evidence | Paper | Section | Contribution |" in markdown
    assert "| E001 | Ceesay 2025 | results |" in markdown
