from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ask_corpus import label_secondary_citation_roles  # noqa: E402
from originrag.analysis.answer_qc import unlabeled_secondary_citations  # noqa: E402


PACK = {
    "chunks": [
        {"evidence_id": "E001", "source_type_assessment": {"source_type": "primary_research"}},
        {"evidence_id": "E002", "source_type_assessment": {"source_type": "reanalysis_secondary_data"}},
        {"evidence_id": "E003", "source_type_assessment": {"source_type": "narrative_review"}},
        {"evidence_id": "E004", "source_type_assessment": {"source_type": "reanalysis_secondary_data"}},
        {"evidence_id": "E005", "source_type_assessment": {"source_type": "systematic_review_meta_analysis"}},
        {"evidence_id": "E006", "source_type_assessment": {"source_type": "protocol"}},
        {"evidence_id": "E007", "source_type_assessment": {"source_type": "editorial_commentary"}},
        {"evidence_id": "E008", "source_type_assessment": {"source_type": "unknown"}},
        {"evidence_id": "E009"},
    ]
}


@pytest.mark.parametrize("citations", ["E002", "E002; E004", "E004; E002; E004"])
def test_reanalysis_only_claim_has_precise_role(citations):
    claim = f"The recalculated estimate differed between groups ({citations})."
    result = label_secondary_citation_roles(claim, "answer", PACK)
    assert result == "Reanalysis: " + claim
    assert unlabeled_secondary_citations(result, PACK) == []


@pytest.mark.parametrize("other", ["E003", "E005", "E006", "E007"])
def test_mixed_secondary_types_use_nonmisleading_generic_role(other):
    claim = f"The estimates differed between the reported groups (E002; {other})."
    result = label_secondary_citation_roles(claim, "answer", PACK)
    assert result == "Secondary source evidence: " + claim
    assert unlabeled_secondary_citations(result, PACK) == []


@pytest.mark.parametrize("citations", ["E003", "E005", "E006", "E007", "E003; E005"])
def test_nonreanalysis_secondary_normalization_is_unchanged(citations):
    claim = f"The account described differences between groups ({citations})."
    assert label_secondary_citation_roles(claim, "answer", PACK) == (
        "Secondary synthesis: " + claim
    )


@pytest.mark.parametrize("citations", ["E001", "E008", "E009", "E999", "E001; E002", "E002; E999"])
def test_primary_or_unknown_source_group_is_not_relabelled(citations):
    claim = f"The estimates differed between groups ({citations})."
    assert label_secondary_citation_roles(claim, "answer", PACK) == claim


@pytest.mark.parametrize("role", ["Reanalysis", "Secondary synthesis", "Review", "Secondary source evidence"])
def test_explicit_model_role_is_not_rewritten(role):
    claim = f"{role}: The estimate changed between groups (E002)."
    assert label_secondary_citation_roles(claim, "answer", PACK) == claim


def test_paragraph_roles_references_and_claim_text_are_preserved():
    first = "The recalculated value was 2.5 units (E002)."
    second = "The account reported agreement (E003)."
    references = "\n\n## References\nE002: Example A.\nE003: Example B.\n"
    answer = first + "\n\n  " + second + references
    expected = "Reanalysis: " + first + "\n\n  Secondary synthesis: " + second + references
    normalized = label_secondary_citation_roles(answer, "answer", PACK)
    assert normalized == expected
    assert normalized.replace("Reanalysis: ", "").replace("Secondary synthesis: ", "") == answer
    assert label_secondary_citation_roles(normalized, "answer", PACK) == normalized


@pytest.mark.parametrize("task", ["sources", "hypotheses"])
def test_other_task_outputs_are_unchanged(task):
    answer = "The estimate was recalculated (E002)."
    assert label_secondary_citation_roles(answer, task, PACK) == answer


def test_missing_answer_or_source_metadata_is_unchanged():
    assert label_secondary_citation_roles(None, "answer", PACK) is None
    answer = "The estimate was recalculated (E002)."
    assert label_secondary_citation_roles(answer, "answer", None) == answer
