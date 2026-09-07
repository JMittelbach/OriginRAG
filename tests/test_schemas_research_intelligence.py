from __future__ import annotations

import pytest
from pydantic import ValidationError

from pfempire.schemas import (
    Entity,
    EvidenceItem,
    EvidencePack,
    EvidenceRelation,
    HypothesisCard,
    PaperStructuredSummary,
    ResearchGap,
    ScientificClaim,
)


def test_evidence_pack_schema() -> None:
    item = EvidenceItem(
        evidence_id="E001",
        paper_id="MAL_VAR_0001",
        chunk_id="MAL_VAR_0001_CHUNK_0001",
        source_label="Smith 2020",
        text="PfEMP1 binds EPCR in this evidence snippet.",
    )
    pack = EvidencePack(
        question="What links PfEMP1 and EPCR?",
        task_mode="evidence_qa",
        evidence_items=[item],
        n_chunks=1,
        n_unique_papers=1,
    )

    assert pack.evidence_items[0].evidence_id == "E001"


def test_claim_schema_rejects_unknown_claim_type() -> None:
    with pytest.raises(ValidationError):
        ScientificClaim(
            claim_id="C001",
            claim_text="Unsupported claim type should fail.",
            claim_type="opinion",
            source_paper_id="P001",
            source_chunk_id="P001_CHUNK_0001",
        )


def test_entity_relation_summary_gap_hypothesis_schemas() -> None:
    entity = Entity(
        entity_id="ENT_EPCR",
        name="EPCR",
        normalized_name="epcr",
        entity_type="receptor",
    )
    relation = EvidenceRelation(
        relation_id="REL001",
        subject_entity_id="ENT_CIDR",
        relation_type="binds_to",
        object_entity_id=entity.entity_id,
    )
    summary = PaperStructuredSummary(paper_id="P001", title="Example paper")
    gap = ResearchGap(
        gap_id="G001",
        title="Missing paired data",
        description="No paired parasite-host data.",
        missing_link="paired parasite var-expression and host scRNA-seq",
    )
    hypothesis = HypothesisCard(
        hypothesis_id="H001",
        title="Paired profiling hypothesis",
        short_summary="A testable paired-data hypothesis.",
    )

    assert relation.relation_type == "binds_to"
    assert summary.source_chunks == []
    assert "paired" in gap.missing_link
    assert hypothesis.evidence_backed_statements == []
