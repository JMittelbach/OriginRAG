"""TRACE-RAG: lineage-aware scientific evidence composition."""

from originrag.trace.exact_lineage import (
    ExactPartitionFlowAnalysis,
    exact_partition_flow_analysis,
    verify_exact_partition_witnesses,
)
from originrag.trace.robust_lineage import (
    EffectiveEvidenceBounds,
    LineageDependenceInterval,
    LineageReviewPriority,
    RobustEvidenceFlowBounds,
    complete_lineage_intervals,
    effective_evidence_bounds,
    rank_lineage_reviews,
    robust_evidence_flow,
)
from originrag.trace.schemas import (
    EvidenceBlueprint,
    EvidenceFacet,
    EvidenceFlowResult,
    EvidenceLineageEdge,
    EvidenceSufficiencyCertificate,
    LineageSignal,
    TraceCandidate,
    TraceSelectionResult,
)
from originrag.trace.selector import select_evidence

__all__ = [
    "EffectiveEvidenceBounds",
    "ExactPartitionFlowAnalysis",
    "EvidenceBlueprint",
    "EvidenceFacet",
    "EvidenceFlowResult",
    "EvidenceLineageEdge",
    "EvidenceSufficiencyCertificate",
    "LineageDependenceInterval",
    "LineageReviewPriority",
    "LineageSignal",
    "RobustEvidenceFlowBounds",
    "TraceCandidate",
    "TraceSelectionResult",
    "complete_lineage_intervals",
    "effective_evidence_bounds",
    "exact_partition_flow_analysis",
    "rank_lineage_reviews",
    "robust_evidence_flow",
    "select_evidence",
    "verify_exact_partition_witnesses",
]
