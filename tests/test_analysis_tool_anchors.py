from pathlib import Path
import sys

import pytest

import originrag.retrieval.query_plan as query_plan
from originrag.analysis.evidence_pack import (
    build_evidence_pack,
    freeze_prompt_visible_result,
)
from originrag.retrieval.query_plan import required_entity_anchor_groups


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ask_corpus  # noqa: E402


@pytest.mark.parametrize("tools", [
    "Tool2 or ToolX",
    "ToolX or Tool2",
    "Tool2 or Tool3",
    "TOOL2 or TOOLX",
    "tool2 or ToolX",
    "MixTool7 or edgeTool",
    "Tool2 or Tool3 or ToolX",
])
@pytest.mark.parametrize("verb", ["analyzed with", "analysed using"])
def test_explicit_analysis_tool_alternatives_are_not_biological_anchors(tools, verb):
    question = (
        f"For gene expression {verb} {tools}, does a small adjusted p-value "
        "establish a practically meaningful effect?"
    )
    assert required_entity_anchor_groups(question) == []


@pytest.mark.parametrize("question", [
    "Does ABC1 gene expression analyzed with Tool2 or ToolX establish an effect?",
    "For expression analyzed with Tool2 or ToolX, is ABC1 a meaningful gene?",
])
def test_an_independent_true_entity_remains_required(question):
    assert required_entity_anchor_groups(question) == [("abc1",)]


@pytest.mark.parametrize("question", [
    "Is Tool2 gene expression meaningful when analyzed with Tool2 or ToolX?",
    "For expression analyzed with Tool2 or ToolX, is Tool2 a useful gene marker?",
    "Which gene marker, Tool2 or ToolX, is associated with expression?",
    "Does gene expression analyzed with Tool2 establish a meaningful effect?",
    "Is gene expression analyzed with Tool2 or ToolX proteins?",
    "Is gene expression analyzed with Tool2-positive or ToolX?",
])
def test_entity_mentions_outside_clear_tool_alternatives_are_preserved(question):
    assert ("tool2",) in required_entity_anchor_groups(question)


def test_known_entity_alias_is_not_overridden_by_a_tool_role(monkeypatch):
    monkeypatch.setattr(query_plan, "ENTITY_ALIASES", {"tool2": ("tool2", "known marker")})
    question = "Does gene expression analyzed with Tool2 or ToolX establish an effect?"
    assert ("tool2", "known marker") in required_entity_anchor_groups(question)


@pytest.mark.parametrize("role", [
    "as protein markers",
    "as known biological markers",
    "as a candidate protein",
    "monoclonal antibodies",
    "as monoclonal antibodies",
    "polyclonal antibodies",
    "expression",
])
def test_explicit_local_biological_role_keeps_both_alternative_symbols(role):
    question = f"Is gene expression analyzed with ABC1 or XYZ2 {role} reliable?"
    assert required_entity_anchor_groups(question) == [("abc1",), ("xyz2",)]


def test_role_guard_does_not_cross_punctuation_to_a_separate_clause():
    question = "For expression analyzed with Tool2 or ToolX, biological markers require interpretation."
    assert required_entity_anchor_groups(question) == []


def test_either_or_analysis_tools_do_not_exclude_the_second_methods_paper():
    question = "Does gene expression analysed with either Tool2 or ToolX imply certainty?"
    groups = required_entity_anchor_groups(question)
    rows = [
        {"paper_id": "METHOD_A", "text": "Tool2 estimates expression uncertainty."},
        {"paper_id": "METHOD_B", "text": "ToolX estimates expression uncertainty."},
    ]
    retained = ask_corpus.retain_entity_anchored_evidence(
        rows, groups, question=question, max_chars_per_chunk=300,
    )
    assert groups == []
    assert [row["paper_id"] for row in retained] == ["METHOD_A", "METHOD_B"]


def test_required_anchor_window_preserves_the_substantive_question():
    question = "Does ABC1 measurement error establish practical significance with certainty?"
    row = {
        "paper_id": "P1", "chunk_id": "C1", "title": "Measurement methods",
        "text": (
            "ABC1 calibration was checked against the standard. " * 30
            + "ABC1 measurement error does not establish practical significance with "
            "certainty. The effect magnitude and the intended purpose must be considered."
        ),
    }
    retained = ask_corpus.retain_entity_anchored_evidence(
        [row], [("abc1",)], question=question, max_chars_per_chunk=280,
    )
    assert len(retained) == 1
    visible = freeze_prompt_visible_result(question, retained[0], 280)
    assert "does not establish practical significance" in visible["text"]
    assert "ABC1" in visible["text"]
    assert retained[0]["required_entity_anchors_verified"] is True
    assert retained[0]["anchor_only_evidence_window"] is True
    pack = build_evidence_pack(question, retained, max_chars_per_chunk=280)
    assert pack["chunks"][0]["text"] == visible["text"]
    assert len(row["text"]) > 1000
    assert "required_entity_anchors" not in row


def test_question_window_falls_back_if_it_would_lose_the_real_anchor():
    question = "Does ABC1 measurement error establish practical significance with certainty?"
    row = {
        "paper_id": "P1", "chunk_id": "C1", "title": "Measurement methods",
        "text": (
            "ABC1 calibration used a reference standard. "
            + "The instrument was regularly cleaned. " * 35
            + "Measurement error does not establish practical significance with certainty. " * 3
        ),
    }
    ordinary = freeze_prompt_visible_result(question, row, 220)
    assert "ABC1" not in ordinary["text"]
    retained = ask_corpus.retain_entity_anchored_evidence(
        [row], [("abc1",)], question=question, max_chars_per_chunk=220,
    )
    assert len(retained) == 1
    assert "ABC1" in freeze_prompt_visible_result(question, retained[0], 220)["text"]


@pytest.mark.parametrize("text", [
    "Measurement error does not establish practical significance with certainty.",
    "ABC10 measurement error does not establish practical significance with certainty.",
    "ABC1 measurement error does not establish practical significance with certainty.",
])
def test_every_required_entity_is_still_verified_in_the_visible_evidence(text):
    retained = ask_corpus.retain_entity_anchored_evidence(
        [{"paper_id": "P1", "text": text}], [("abc1",), ("xyz2",)],
        question="Do ABC1 and XYZ2 measurement errors imply practical significance?",
        max_chars_per_chunk=220,
    )
    assert retained == []


def test_identity_anchor_does_not_gain_entity_evidence_status():
    retained = ask_corpus.retain_entity_anchored_evidence(
        [{"paper_id": "P1", "text": "A bibliographic identity only.",
          "explicit_identifier_anchor": True}],
        [("abc1",)], question="Does ABC1 establish a practical effect?",
        max_chars_per_chunk=220,
    )
    assert retained[0]["required_entity_anchors_verified"] is False
    assert retained[0]["entity_source_directness"] == "exact_identifier_identity_only"
