from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


EntityType = Literal[
    "gene",
    "protein",
    "domain",
    "receptor",
    "phenotype",
    "disease",
    "cell_type",
    "immune_state",
    "pathway",
    "pathogen_species",
    "host_species",
    "drug",
    "vaccine",
    "assay",
    "cohort",
    "dataset",
    "method",
    "tool",
    "country",
    "tissue",
]
RelationType = Literal[
    "associated_with",
    "binds_to",
    "expressed_in",
    "upregulated_in",
    "downregulated_in",
    "protects_against",
    "increases_risk_of",
    "measured_by",
    "tested_in",
    "derived_from",
    "contradicts",
    "supports",
    "requires_data",
]


class Entity(BaseModel):
    entity_id: str
    name: str
    normalized_name: str
    entity_type: EntityType
    aliases: list[str] = Field(default_factory=list)
    source: str | None = None


class EvidenceRelation(BaseModel):
    relation_id: str
    subject_entity_id: str
    relation_type: RelationType
    object_entity_id: str
    claim_id: str | None = None
    paper_id: str | None = None
    confidence: float | None = None
