from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ask_corpus import label_secondary_citation_roles  # noqa: E402


PACK = {
    "chunks": [
        {"evidence_id": "E001", "source_type_assessment": {"source_type": "primary_research"}},
        {"evidence_id": "E002", "source_type_assessment": {"source_type": "narrative_review"}},
    ]
}


@pytest.mark.parametrize(
    "claim",
    [
        "The *A. example* instrument measured intensity (E002).",
        "A. Example measured intensity (E002).",
        "A. B. Example measured intensity (E002).",
        "Dr. Example measured intensity (E002).",
        "Example et al. measured intensity (E002).",
        "The procedure used e.g. a calibrated instrument (E002).",
        "The result, i.e. the measured intensity, was recorded (E002).",
        "The value in Fig. 2 was 2.5 units (E002).",
        "The mean was 2.5 units, with an interval of 2.1–2.9 (E002).",
        "Measurements came from the U.S. laboratory (E002).",
    ],
)
def test_secondary_role_is_added_before_claim_not_inside_abbreviation(claim: str) -> None:
    assert label_secondary_citation_roles(claim, "answer", PACK) == f"Secondary synthesis: {claim}"


@pytest.mark.parametrize("separator", [" ", "  ", "\n", "\n\n"])
def test_true_sentence_boundaries_keep_independent_citation_roles(separator: str) -> None:
    primary = "The instrument measured intensity (E001)."
    secondary = "The *A. example* instrument measured 2.5 units (E002)."
    answer = primary + separator + secondary

    assert label_secondary_citation_roles(answer, "answer", PACK) == (
        primary + separator + "Secondary synthesis: " + secondary
    )


@pytest.mark.parametrize("punctuation", [".", "?", "!"])
def test_true_sentence_boundary_after_secondary_claim_preserves_primary(punctuation: str) -> None:
    secondary = f"The instrument measured 2.5 units (E002){punctuation}"
    primary = "A different instrument measured intensity (E001)."

    assert label_secondary_citation_roles(secondary + " " + primary, "answer", PACK) == (
        "Secondary synthesis: " + secondary + " " + primary
    )


def test_existing_source_role_is_not_repeated_after_initial() -> None:
    answer = "The review discussed the *A. example* instrument (E002)."

    assert label_secondary_citation_roles(answer, "answer", PACK) == answer


def test_mixed_primary_secondary_claim_and_references_are_unchanged() -> None:
    answer = (
        "The *A. example* instrument measured 2.5 units (E001; E002).\n\n"
        "## References\nE002: A. Example. A collected account."
    )

    assert label_secondary_citation_roles(answer, "answer", PACK) == answer


def test_secondary_annotation_is_idempotent_and_preserves_original_text() -> None:
    answer = "The *A. example* instrument measured 2.5 units (E002).  Another value was measured (E002)."
    normalized = label_secondary_citation_roles(answer, "answer", PACK)

    assert normalized.replace("Secondary synthesis: ", "") == answer
    assert label_secondary_citation_roles(normalized, "answer", PACK) == normalized
