from __future__ import annotations

from pfempire.analysis.answer_format import normalize_answer_format


def test_normalize_hypothesis_headings_from_local_llm_variants() -> None:
    answer = """### Hypothesis Card
#### Title
Example
#### Short_summary
Summary
#### Evidence Backed Statements
- Statement (E001)
#### Testable_prediction
- Prediction (E001)
#### Required Data
Data
#### Analysis Plan
Plan
#### Risk Plan B
Risk
#### Key References
- E001
"""

    normalized = normalize_answer_format(answer, task="hypothesis")

    assert normalized is not None
    assert normalized.startswith("## Hypothesis Card")
    assert "### title" in normalized
    assert "### short_summary" in normalized
    assert "### evidence_backed_statements" in normalized
    assert "### testable_prediction" in normalized
    assert "### required_data" in normalized
    assert "### analysis_plan" in normalized
    assert "### risk_plan_b" in normalized
    assert "### key_references" in normalized


def test_normalize_references_heading_for_hypothesis() -> None:
    normalized = normalize_answer_format("#### References\n- E001", task="hypothesis")

    assert normalized == "### key_references\n- E001"


def test_normalize_short_evidence_ids_to_three_digits() -> None:
    normalized = normalize_answer_format("Supported by E1 and [E05], not E1234.", task="answer")

    assert normalized == "Supported by E001 and [E005], not E1234."


def test_normalize_gap_answer_fills_empty_short_answer() -> None:
    answer = """## Short Answer
## Supported Research Gaps
- **Gap**: Limited understanding of sex-based biological mechanisms mediating malaria immunity.
**Why it matters**: It limits interpretation.
**Supporting evidence labels**: E001
**Missing evidence**: Matched mechanistic data.

## Candidate Testable Questions
- Does sex modify immune marker associations?

## Best Next Data / Analysis
- Matched data.

## Key Sources
- E001.
"""

    normalized = normalize_answer_format(answer, task="gaps")

    assert normalized is not None
    assert "## Short Answer\nThe retrieved evidence points first to this gap:" in normalized
    assert "Limited understanding of sex-based biological mechanisms" in normalized


def test_normalize_answer_fills_empty_cautious_inference_safely() -> None:
    answer = """## Evidence-backed statements
Ciuffreda et al. used RT-qPCR rather than whole-transcriptome RNA-seq (E001).

## Cautious inference

## Limitations / uncertainty
Mixed-stage samples mapped to an intermediate estimate (E002).

## Missing evidence
No multimodal age reconstruction was reported.

## References
- E001
- E002
"""

    normalized = normalize_answer_format(answer, task="answer")

    assert normalized is not None
    assert (
        "## Cautious inference\nNo additional inference is warranted beyond "
        "the evidence-backed statements above."
    ) in normalized
