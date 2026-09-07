from __future__ import annotations

from pfempire.retrieval.text_cleaning import clean_pdf_text_for_display, evidence_window


def test_clean_pdf_text_removes_hyphenated_line_breaks_and_ligatures() -> None:
    text = "se-\nvere malaria and con\ufb01dence inter-\nvals"

    clean = clean_pdf_text_for_display(text)

    assert "severe malaria" in clean
    assert "confidence intervals" in clean


def test_evidence_window_keeps_query_terms() -> None:
    text = "Opening. " + ("background " * 200) + "CIDRalpha1 PfEMP1 EPCR severe malaria signal."

    window = evidence_window("PfEMP1 EPCR severe malaria", text, max_chars=120)

    assert "PfEMP1 EPCR severe malaria" in window
