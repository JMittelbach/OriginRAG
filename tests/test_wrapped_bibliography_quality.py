from __future__ import annotations

import pytest

from originrag.retrieval.chunk_quality import classify_chunk_quality


def bibliography(number_style="{number}.", *, signature="volume", authors=True):
    rows = []
    for number, surname in enumerate(("Adams", "Baker", "Chen", "Díaz"), start=11):
        rows.append(number_style.format(number=number))
        rows.append(f"{surname}, A.; Smith, B. A neutral study of document layout." if authors
                    else "The layout system was evaluated on an independent benchmark.")
        if signature == "volume":
            rows.append(f"Journal of Testing. 2020;{number}:120–129.")
        elif signature == "comma":
            rows.append(f"Journal of Testing 2020, {number}, e120129.")
        elif signature == "issue":
            rows.append(f"Journal of Testing. 2020;{number}(2):120–129.")
        elif signature == "crossref":
            rows.append("Proceedings of a layout conference, pp. 120–129. [CrossRef]")
        elif signature == "pubmed":
            rows.append("A neutral publication record. [PubMed]")
        else:
            rows.append("The comparison measured layout stability and readability.")
    return "\n".join(rows)


@pytest.mark.parametrize("number_style", ["{number}.", "{number})", "[{number}]"])
@pytest.mark.parametrize("signature", ["volume", "comma", "issue", "crossref", "pubmed"])
def test_number_only_pdf_reference_lines_need_bibliographic_corroboration(number_style, signature):
    quality = classify_chunk_quality(bibliography(number_style, signature=signature))
    assert quality["is_reference_like"]
    assert "reference_like" in quality["flags"]


def test_author_initials_without_commas_are_recognized():
    text = bibliography().replace(", A.; Smith, B.", " AB, Smith CD, et al.")
    assert classify_chunk_quality(text)["is_reference_like"]


def test_numbered_author_prose_without_publication_signatures_is_retained():
    assert not classify_chunk_quality(bibliography(signature="none"))["is_reference_like"]


def test_year_volume_patterns_without_authorship_are_not_enough():
    assert not classify_chunk_quality(bibliography(authors=False))["is_reference_like"]


def test_numbered_steps_with_inline_citations_are_not_bibliography():
    text = "\n".join(
        f"{number}.\nEvaluate the document under condition {number}. "
        "The procedure follows Smith et al. (2020), with the settings held fixed. "
        "Record the measured outcomes and retain the complete observations."
        for number in range(1, 7)
    )
    assert not classify_chunk_quality(text)["is_reference_like"]


def test_numbered_numeric_results_table_is_not_bibliography():
    text = "\n".join(f"{number}.\n2020;{number}:120\n10 20 30 40" for number in range(1, 7))
    assert not classify_chunk_quality(text)["is_reference_like"]


def test_scientific_prose_with_multiple_citations_is_retained():
    text = (
        "Smith et al. (2020) evaluated the first layout model [11]. "
        "Adams and Baker (2021) reported a larger benchmark [12]. "
        "The current study compared both systems on held-out documents, "
        "with confidence intervals reported for each outcome. "
    ) * 4
    assert not classify_chunk_quality(text)["is_reference_like"]


def test_two_wrapped_citations_do_not_reclassify_a_mixed_chunk():
    text = "\n".join(bibliography().splitlines()[:6]) + (
        "\nThe present study reports new measurements from an independent document set. "
        "Both approaches were evaluated using the same uncertainty analysis."
    ) * 4
    assert not classify_chunk_quality(text)["is_reference_like"]
