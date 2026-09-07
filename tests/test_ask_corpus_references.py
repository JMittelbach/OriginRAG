from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from ask_corpus import (  # noqa: E402
    label_secondary_citation_roles,
    max_retrieval_queries,
    normalize_answer_references,
    prune_redundant_secondary_citations,
    qualify_evidence_absence_claims,
)


def test_quick_answer_mode_keeps_three_retrieval_perspectives() -> None:
    assert max_retrieval_queries("answer", "quick") == 3


def test_source_search_leaves_room_for_explicit_facets() -> None:
    assert max_retrieval_queries("sources", "pro") == 6


def test_normalize_answer_references_replaces_model_written_labels() -> None:
    answer = """The evidence supports this claim (E002; E001).

## References
- E002: [Wrong Author 2025, Abstract] - invented label.
- E001: [Also Wrong 2024, Results] - invented label.
"""
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Correct One et al., 2020, Results]",
                "title": "First real paper",
                "doi": "10.1000/first",
            },
            {
                "evidence_id": "E002",
                "citation_label": "[Correct Two et al., 2021, Discussion]",
                "title": "Second real paper",
                "doi": "https://doi.org/10.1000/second",
            },
        ],
    }

    normalized = normalize_answer_references(answer, task="answer", evidence_pack=pack)

    assert normalized is not None
    assert "[Wrong Author" not in normalized
    assert "[Also Wrong" not in normalized
    assert "- E002: [Correct Two et al., 2021, Discussion] Second real paper https://doi.org/10.1000/second" in normalized
    assert "- E001: [Correct One et al., 2020, Results] First real paper https://doi.org/10.1000/first" in normalized


def test_normalize_answer_references_leaves_non_answer_tasks_unchanged() -> None:
    answer = "## Hypothesis Card\n\n### title\nExample (E001)"

    assert normalize_answer_references(answer, task="hypothesis", evidence_pack={"chunks": []}) == answer


def test_prune_redundant_secondary_citation_from_mixed_claim() -> None:
    answer = "Expression was elevated in severe cases (E001; E002)."
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "source_type_assessment": {"source_type": "primary_research"},
            },
            {
                "evidence_id": "E002",
                "source_type_assessment": {"source_type": "narrative_review"},
            },
        ]
    }

    normalized = prune_redundant_secondary_citations(
        answer, task="answer", evidence_pack=pack
    )

    assert normalized == "Expression was elevated in severe cases (E001)."


def test_keep_secondary_only_claim_for_explicit_role_qc() -> None:
    answer = "Expression was elevated in severe cases (E002)."
    pack = {
        "chunks": [
            {
                "evidence_id": "E002",
                "source_type_assessment": {"source_type": "narrative_review"},
            }
        ]
    }

    assert (
        prune_redundant_secondary_citations(answer, task="answer", evidence_pack=pack)
        == answer
    )


def test_label_secondary_only_claim_without_rewriting_content() -> None:
    answer = "Primary result (E001). Hviid et al. summarized replication (E002)."
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "source_type_assessment": {"source_type": "primary_research"},
            },
            {
                "evidence_id": "E002",
                "source_type_assessment": {"source_type": "narrative_review"},
            },
        ]
    }

    normalized = label_secondary_citation_roles(
        answer, task="answer", evidence_pack=pack
    )

    assert normalized == (
        "Primary result (E001). Secondary synthesis: "
        "Hviid et al. summarized replication (E002)."
    )


def test_qualify_universal_absence_claim_to_retrieved_pack() -> None:
    answer = (
        "No evidence directly links sex to PfEMP1. "
        "Direct evidence connecting both is absent. "
        "The absence of sex-specific data in the evidence precludes inference."
    )

    normalized = qualify_evidence_absence_claims(answer, task="answer")

    assert normalized == (
        "The retrieved evidence pack contains no direct evidence linking sex to PfEMP1. "
        "The retrieved evidence pack does not contain direct evidence connecting both. "
        "The absence of sex-specific data in the retrieved evidence pack precludes inference."
    )


def test_reference_normalization_drops_reference_not_cited_in_body() -> None:
    answer = "Supported statement (E001).\n\n## References\n- E001.\n- E002."
    pack = {
        "chunks": [
            {"evidence_id": "E001", "citation_label": "[One, 2020]", "title": "One"},
            {"evidence_id": "E002", "citation_label": "[Two, 2021]", "title": "Two"},
        ]
    }

    normalized = normalize_answer_references(answer, task="answer", evidence_pack=pack)

    assert "- E001:" in normalized
    assert "- E002:" not in normalized
