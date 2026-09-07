import pytest
from pydantic import ValidationError

from pfempire.trace.schemas import (
    EvidenceBlueprint,
    EvidenceFacet,
    EvidenceLineageEdge,
    LineageSignal,
    TraceCandidate,
)


def test_schema_bounds_signal_strength():
    signal = LineageSignal(
        signal_id="s",
        paper_id_a="a",
        paper_id_b="b",
        signal_type="same_cohort",
        signal_strength=2,
        source="test",
        status="suggested",
    )
    assert signal.signal_strength == 1


def test_schema_rejects_unknown_scale():
    with pytest.raises(ValidationError):
        EvidenceFacet(
            facet_id="f",
            question_id="q",
            label="x",
            description="x",
            facet_type="x",
            biological_scale="galactic",
        )


def test_trace_candidate_rejects_nonfinite_scores() -> None:
    with pytest.raises(ValidationError, match="retrieval_score must be finite"):
        TraceCandidate(
            chunk_id="C1",
            paper_id="P1",
            text="evidence",
            token_cost=10,
            retrieval_score=float("nan"),
        )


def test_trace_candidate_normalizes_tsv_quality_flags() -> None:
    candidate = TraceCandidate(
        chunk_id="C1",
        paper_id="P1",
        text="evidence",
        token_cost=10,
        retrieval_score=1.0,
        quality_flags="ocr_noise;table_like",
    )

    assert candidate.quality_flags == ["ocr_noise", "table_like"]


def test_trace_candidate_rejects_conflicting_normalized_origin_keys() -> None:
    with pytest.raises(ValidationError, match="conflicting normalized facet keys"):
        TraceCandidate(
            chunk_id="C1",
            paper_id="P1",
            text="evidence",
            token_cost=10,
            retrieval_score=1.0,
            facet_origin_ids={"f1": "ORIGIN_A", " f1 ": "ORIGIN_B"},
        )


def test_blueprint_rejects_duplicate_facet_ids() -> None:
    facet = EvidenceFacet(
        facet_id="duplicate",
        question_id="Q1",
        label="Evidence",
        description="Evidence",
        facet_type="mechanism",
        biological_scale="molecular",
    )

    with pytest.raises(ValidationError, match="unique facet_id"):
        EvidenceBlueprint(
            question_id="Q1",
            question="Question",
            task_mode="evidence_qa",
            required_facets=[facet, facet.model_copy()],
        )


def test_blueprint_rejects_facet_from_another_question() -> None:
    facet = EvidenceFacet(
        facet_id="foreign",
        question_id="Q2",
        label="Evidence",
        description="Evidence",
        facet_type="mechanism",
        biological_scale="molecular",
    )

    with pytest.raises(ValidationError, match="must match blueprint question_id"):
        EvidenceBlueprint(
            question_id="Q1",
            question="Question",
            task_mode="evidence_qa",
            required_facets=[facet],
        )


def test_schema_rejects_nonfinite_scientific_weights() -> None:
    with pytest.raises(ValidationError, match="importance_weight must be finite"):
        EvidenceFacet(
            facet_id="f",
            question_id="q",
            label="Evidence",
            description="Evidence",
            facet_type="mechanism",
            biological_scale="molecular",
            importance_weight=float("inf"),
        )
    with pytest.raises(ValidationError, match="signal_strength must be finite"):
        LineageSignal(
            signal_id="s",
            paper_id_a="a",
            paper_id_b="b",
            signal_type="same_cohort",
            signal_strength=float("nan"),
            source="test",
        )


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan")])
def test_lineage_edge_rejects_invalid_dependence_score(value: float) -> None:
    with pytest.raises(ValidationError, match="within \\[0, 1\\]"):
        EvidenceLineageEdge(
            paper_id_a="A",
            paper_id_b="B",
            dependence_score=value,
        )


def test_trace_candidate_rejects_empty_identity() -> None:
    with pytest.raises(ValidationError, match="must be nonempty"):
        TraceCandidate(
            chunk_id="",
            paper_id="P1",
            text="evidence",
            token_cost=10,
            retrieval_score=1.0,
        )
