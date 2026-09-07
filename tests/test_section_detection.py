from __future__ import annotations

from originrag.retrieval.section_detection import (
    carries_forward_section,
    detect_section_for_chunk,
    stored_section_needs_redetection,
)


def test_abstract_heading_detected() -> None:
    result = detect_section_for_chunk("Abstract\nPfEMP1 is central to severe malaria pathogenesis.")
    assert result["section"] == "abstract"
    assert result["section_confidence"] >= 0.9


def test_methods_heading_detected() -> None:
    result = detect_section_for_chunk("Materials and methods\nSamples were collected from children.")
    assert result["section"] == "methods"


def test_results_heading_detected() -> None:
    result = detect_section_for_chunk("Results\nCIDRalpha1 domains were enriched in severe malaria.")
    assert result["section"] == "results"


def test_numbered_methods_heading_detected() -> None:
    result = detect_section_for_chunk("2.3 Materials and methods\nSamples were sequenced.")
    assert result["section"] == "methods"


def test_combined_results_and_discussion_heading_detected() -> None:
    result = detect_section_for_chunk("Results and discussion\nWe analyzed 136 samples.")
    assert result["section"] == "results"


def test_unlabelled_nature_style_abstract_detected() -> None:
    text = (
        "Article\nhttps://doi.org/10.1038/example\nA scientific study title\n"
        "First Author, Second Author\nInstitute for Genome Sciences, University of Example.\n"
        + "This study examines malaria responses across a longitudinal clinical cohort. " * 15
    )
    result = detect_section_for_chunk(text)
    assert result["section"] == "abstract"
    assert "unlabelled_article_first_page" in result["section_markers"]


def test_discussion_heading_detected() -> None:
    result = detect_section_for_chunk("Discussion\nThese findings suggest an association.")
    assert result["section"] == "discussion"


def test_references_detected() -> None:
    result = detect_section_for_chunk("References\n1. Smith et al. 2020. Journal of Malaria.\n2. Jones et al. 2021.")
    assert result["section"] == "references"


def test_figure_legend_detected() -> None:
    result = detect_section_for_chunk("Figure 2. EPCR binding by infected erythrocytes.")
    assert result["section"] == "figure_legend"


def test_table_detected() -> None:
    result = detect_section_for_chunk("Table 1. Clinical characteristics of the cohort.")
    assert result["section"] == "table"


def test_previous_section_context_preserved() -> None:
    result = detect_section_for_chunk("The next paragraph continues the analysis without a heading.", previous_section="results")
    assert result["section"] == "results"


def test_supplement_mention_does_not_override_results_context() -> None:
    text = "The result was robust (Supplementary Information) across all clinical samples."
    result = detect_section_for_chunk(text, previous_section="results")
    assert result["section"] == "results"


def test_publisher_supplement_notice_does_not_override_first_page_abstract() -> None:
    text = (
        "Article\nhttps://doi.org/10.1038/example\nA scientific study title\n"
        "First Author, Second Author\nInstitute for Genome Sciences, University of Example.\n"
        + "This study examines malaria responses across a clinical cohort. " * 15
        + "\nSupplementary Information accompanies this article."
    )
    result = detect_section_for_chunk(text)
    assert result["section"] == "abstract"


def test_figure_and_table_labels_do_not_carry_forward() -> None:
    assert not carries_forward_section("figure_legend")
    assert not carries_forward_section("table")
    result = detect_section_for_chunk("This paragraph continues without a heading.", previous_section="figure_legend")
    assert result["section"] == "unknown"


def test_only_legacy_suspect_stored_sections_are_redetected() -> None:
    assert stored_section_needs_redetection(
        {"section": "supplement", "section_markers": ["supplement_term"]}
    )
    assert stored_section_needs_redetection(
        {"section": "supplement", "section_markers": ["previous_section_context"]}
    )
    assert not stored_section_needs_redetection(
        {"section": "results", "section_markers": ["previous_section_context"]}
    )
    assert not stored_section_needs_redetection(
        {"section": "supplement", "section_markers": ["Supplement"]}
    )
