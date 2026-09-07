from __future__ import annotations

from originrag.schemas.claims import ScientificClaim
from originrag.schemas.entities import Entity, EvidenceRelation
from originrag.schemas.evidence import EvidenceItem, EvidencePack
from originrag.schemas.gaps import ResearchGap
from originrag.schemas.hypotheses import HypothesisCard
from originrag.schemas.summaries import PaperStructuredSummary

__all__ = [
    "Entity",
    "EvidenceItem",
    "EvidencePack",
    "EvidenceRelation",
    "HypothesisCard",
    "PaperStructuredSummary",
    "ResearchGap",
    "ScientificClaim",
]
