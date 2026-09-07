from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


ClaimType = Literal[
    "finding",
    "method",
    "dataset",
    "limitation",
    "speculation",
    "hypothesis",
    "background",
    "contradiction",
    "negative_result",
]
EvidenceStrength = Literal["strong", "moderate", "weak", "unclear"]


class ScientificClaim(BaseModel):
    claim_id: str
    claim_text: str
    claim_type: ClaimType
    evidence_type: str | None = None
    evidence_strength: EvidenceStrength | None = None
    direction: str | None = None
    uncertainty: str | None = None
    organism: str | None = None
    pathogen: str | None = None
    host_system: str | None = None
    disease_context: str | None = None
    phenotype: str | None = None
    parasite_feature: str | None = None
    host_feature: str | None = None
    method: str | None = None
    dataset_or_cohort: str | None = None
    source_paper_id: str
    source_chunk_id: str
    source_section: str | None = None
    source_pages: str | None = None
