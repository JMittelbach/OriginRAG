from pathlib import Path
import re
import sys

import pytest

from originrag.analysis.answer_qc import assess_answer_quality
from originrag.analysis.llm_backends import LLMResult
from originrag.analysis.prompts import build_answer_repair_prompt, build_citation_answer_prompt


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ask_corpus import normalize_llm_result  # noqa: E402


QUESTION = "What did the sensor experiment measure?"
PACK = {
    "question": QUESTION,
    "chunks": [{
        "evidence_id": "E001", "paper_id": "SENSOR_001",
        "citation_label": "[Example 2025, Results]", "title": "Sensor measurements",
        "doi": "10.1000/example", "text": "Sensor2 measured light intensity.",
        "source_type_assessment": {"source_type": "primary_research"},
    }],
}


def prompts(task: str = "answer") -> tuple[str, str]:
    return (
        build_citation_answer_prompt(QUESTION, PACK, task=task),
        build_answer_repair_prompt(
            QUESTION, PACK, task, "An incomplete draft.",
            {"evidence_ids_available": ["E001"], "warnings": ["missing_required_sections"]},
        ),
    )


def test_initial_and_repair_model_contract_only_requests_four_prose_sections() -> None:
    for prompt in prompts():
        assert re.findall(r"^## .+$", prompt, re.MULTILINE) == [
            "## Evidence-backed statements", "## Cautious inference",
            "## Limitations / uncertainty", "## Missing evidence",
        ]
        normalized = " ".join(prompt.split())
        assert "Software generates ``## References`` from inline evidence IDs" in normalized
        assert "Stop after Missing evidence" in normalized
        assert "Do not write reference entries or source-use explanations" in normalized
        assert "Cite evidence-backed claims inline with author-year labels and evidence IDs" in normalized
        assert "Use at most three short prose paragraphs" not in normalized


def test_initial_and_repair_do_not_force_fabricated_inference_or_gaps() -> None:
    for prompt in prompts():
        normalized = " ".join(prompt.split())
        assert "If no further inference is justified, say so briefly instead of inventing one" in normalized
        assert "If no additional question-relevant gap is identified, state that rather than fabricating one" in normalized
        assert "Do not contradict a supported answer merely to fill these sections" in normalized
        assert "Keep all four prose headings nonempty" in normalized
        assert "Put at least one substantive sentence under every required heading" not in normalized
        assert "Keep every factual result source-local" in normalized
        assert "citation, source-grounding, uncertainty, or study-origin accounting rules" in normalized


def test_initial_and_repair_check_question_premises_and_preserve_measure_meaning() -> None:
    question = "Does the agreement score prove every device passes?"
    source = "The report defines an agreement score and an application-specific screening rule."
    pack = {"chunks": [{"evidence_id": "E001", "paper_id": "SENSOR_001", "text": source}]}
    candidates = (
        build_citation_answer_prompt(question, pack),
        build_answer_repair_prompt(
            question, pack, "answer", "Every device passes.",
            {"evidence_ids_available": ["E001"]},
        ),
    )

    for prompt in candidates:
        normalized = " ".join(prompt.split())
        assert "Treat claims and interpretations in the user's question as premises to check" in normalized
        assert "not as evidence themselves" in normalized
        assert "Preserve the meaning of statistical measures and uncertainty terms" in normalized
        assert "unless the cited evidence explicitly justifies that interpretation" in normalized
        assert "Keep study-specific selection rules scoped to that study" in normalized
        assert "do not present them as universal criteria or definitions" in normalized
        assert question in prompt
        assert source in prompt
        assert "Keep every factual result source-local" in normalized


@pytest.mark.parametrize("task", ["gaps", "hypothesis", "sources"])
def test_other_task_generation_contracts_remain_model_owned(task: str) -> None:
    initial, repair = prompts(task)

    assert "Use at most three short prose paragraphs before References" in initial
    assert "Put at least one substantive sentence under every required heading" in repair
    for prompt in (initial, repair):
        assert "Software generates ``## References``" not in prompt
        assert "Keep all four prose headings nonempty" not in prompt
        assert "Stop after Missing evidence" not in prompt


def test_software_references_keep_existing_complete_answer_qc_contract() -> None:
    draft = """## Evidence-backed statements
Sensor2 measured light intensity (Example 2025; E001).

## Cautious inference
No additional inference is warranted.

## Limitations / uncertainty
The supplied excerpt does not describe measurement uncertainty.

## Missing evidence
Calibration details are not supplied in this excerpt."""
    result = normalize_llm_result(LLMResult(backend="qwen", answer=draft), "answer", PACK)
    qc = assess_answer_quality(result.answer, PACK, task="answer")

    assert "## References\n- E001: [Example 2025, Results]" in result.answer
    assert "Sensor2 measured light intensity" in result.answer
    assert "E001" in result.answer.partition("## References")[0]
    assert qc["missing_sections"] == []
    assert qc["empty_sections"] == []
    assert qc["passed"]


def test_software_references_do_not_hide_an_unknown_inline_citation() -> None:
    draft = """## Evidence-backed statements
Sensor2 measured light intensity (E999).

## Cautious inference
No additional inference is warranted.

## Limitations / uncertainty
Measurement uncertainty is not supplied.

## Missing evidence
Calibration details are not supplied."""
    result = normalize_llm_result(LLMResult(backend="qwen", answer=draft), "answer", PACK)
    qc = assess_answer_quality(result.answer, PACK, task="answer")

    assert qc["unknown_citations"] == ["E999"]
    assert "unknown_evidence_citations" in qc["warnings"]
    assert not qc["passed"]
