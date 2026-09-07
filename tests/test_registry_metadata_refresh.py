from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from refresh_registry_metadata_from_doi import apply_crossref_metadata, clean_crossref_text  # noqa: E402
from refresh_registry_metadata_from_doi import is_weak_author, is_weak_title, remove_note_prefix  # noqa: E402


def test_pdf_header_artifacts_and_placeholder_authors_need_refresh() -> None:
    assert is_weak_author("Administrator")
    assert is_weak_author("PhD")
    assert is_weak_title("Abstract In this study, we performed cell-free expression")
    assert is_weak_title("Noble et al. eLife 2013;2:e01074. DOI: 10.7554/eLife.01074")
    assert not is_weak_title("Variable switching rates of malaria virulence genes are associated with chromosomal position")


def test_crossref_refresh_rebuilds_placeholder_citations() -> None:
    row = {
        "author": "",
        "year": "2023",
        "title": "A sufficiently long existing title",
        "journal": "",
        "doi": "10.1000/example",
        "landing_url": "",
        "citation_short": "Unknown et al., 2023",
        "citation_apa": "Unknown et al. (2023). A title.",
        "metadata_source": "inventory",
        "notes": "",
    }
    item = {
        "author": [{"family": "Smith"}],
        "container-title": ["Example Journal"],
        "published": {"date-parts": [[2023]]},
    }

    apply_crossref_metadata(row, item)

    assert row["author"] == "Smith"
    assert row["journal"] == "Example Journal"
    assert row["citation_short"] == "Smith et al., 2023"
    assert row["citation_apa"].startswith("Smith et al. (2023).")


def test_crossref_overwrite_replaces_truncated_title() -> None:
    row = {
        "author": "Old",
        "year": "2007",
        "title": "Six Genes Are Preferentially Transcribed by the Circulating and",
        "journal": "",
        "doi": "10.1000/example",
        "landing_url": "",
        "citation_short": "Old et al., 2007",
        "citation_apa": "Old citation",
        "metadata_source": "inventory",
        "notes": "",
    }
    item = {
        "author": [{"family": "Francis"}],
        "title": ["Six genes preferentially transcribed by circulating parasites"],
        "container-title": ["Infection and Immunity"],
        "published": {"date-parts": [[2007]]},
    }

    apply_crossref_metadata(row, item, overwrite=True)

    assert row["author"] == "Francis"
    assert row["title"] == "Six genes preferentially transcribed by circulating parasites"
    assert row["journal"] == "Infection and Immunity"


def test_crossref_text_removes_inline_markup() -> None:
    assert clean_crossref_text("Differential <i>var</i> gene\n expression") == "Differential var gene expression"


def test_successful_refresh_can_clear_stale_failure_note() -> None:
    notes = "verified;crossref_doi_refresh_failed:HTTP_Error_404:_Not_Found;kept"

    assert remove_note_prefix(notes, "crossref_doi_refresh_failed:") == "verified;kept"
