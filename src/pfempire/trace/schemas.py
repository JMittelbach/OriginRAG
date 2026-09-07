from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pfempire.trace.method_versions import EVIDENCE_ACCOUNTING_VERSION
from pfempire.utils.normalization import normalize_string_list


BiologicalScale = Literal[
    "molecular", "subcellular", "cellular", "tissue", "organ", "clinical",
    "population", "methodological",
]
EvidenceType = Literal[
    "structural", "biochemical", "functional", "cellular", "observational",
    "longitudinal", "interventional", "genetic", "transcriptomic", "single_cell",
    "multi_omics", "methodological", "dataset", "review", "unknown",
]
Polarity = Literal["support", "contradiction", "null_result", "limitation", "unknown", "any"]


class TraceModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class EvidenceFacet(TraceModel):
    facet_id: str
    question_id: str
    label: str
    description: str
    facet_type: str
    biological_scale: BiologicalScale
    evidence_type: EvidenceType | None = None
    polarity_requirement: Polarity | None = None
    importance_weight: float = 1.0
    minimum_items: int = 1
    is_required: bool = True
    aliases: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("importance_weight")
    @classmethod
    def nonnegative_weight(cls, value: float) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("importance_weight must be finite")
        return max(0.0, parsed)

    @field_validator("minimum_items")
    @classmethod
    def positive_minimum_items(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("minimum_items must be greater than zero")
        return int(value)


class EvidenceBlueprint(TraceModel):
    question_id: str
    question: str
    task_mode: str
    required_facets: list[EvidenceFacet] = Field(default_factory=list)
    preferred_scales: list[str] = Field(default_factory=list)
    preferred_evidence_types: list[str] = Field(default_factory=list)
    required_polarities: list[str] = Field(default_factory=list)
    context_variables: list[str] = Field(default_factory=list)
    minimum_independent_lineages: int = 2
    allowed_claim_strength_if_complete: str = "association"
    notes: str | None = None
    review_status: Literal["suggested", "human_reviewed", "frozen"] = "suggested"
    reviewer: str | None = None

    @field_validator("minimum_independent_lineages")
    @classmethod
    def nonnegative_minimum_lineages(cls, value: int) -> int:
        if value < 0:
            raise ValueError("minimum_independent_lineages must be nonnegative")
        return int(value)

    @model_validator(mode="after")
    def unique_facet_ids(self) -> "EvidenceBlueprint":
        facet_ids = [facet.facet_id for facet in self.required_facets]
        if len(facet_ids) != len(set(facet_ids)):
            raise ValueError("required_facets must have unique facet_id values")
        mismatched = sorted(
            facet.facet_id
            for facet in self.required_facets
            if facet.question_id != self.question_id
        )
        if mismatched:
            raise ValueError(
                "facet question_id must match blueprint question_id: "
                + ", ".join(mismatched)
            )
        return self


class LineageSignal(TraceModel):
    signal_id: str
    paper_id_a: str
    paper_id_b: str
    signal_type: str
    signal_strength: float
    source: str
    details: dict[str, Any] = Field(default_factory=dict)
    status: Literal["suggested", "verified", "rejected"] = "suggested"
    reviewer: str | None = None

    @field_validator("signal_strength")
    @classmethod
    def bounded_strength(cls, value: float) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("signal_strength must be finite")
        return min(1.0, max(0.0, parsed))

    @model_validator(mode="after")
    def valid_pair(self) -> "LineageSignal":
        if not self.paper_id_a.strip() or not self.paper_id_b.strip():
            raise ValueError("lineage signal paper IDs must be nonempty")
        if self.paper_id_a == self.paper_id_b:
            raise ValueError("lineage signal must connect two distinct papers")
        return self


class EvidenceLineageEdge(TraceModel):
    paper_id_a: str
    paper_id_b: str
    dependence_score: float
    relationship_types: list[str] = Field(default_factory=list)
    signals: list[LineageSignal] = Field(default_factory=list)
    is_hard_duplicate: bool = False
    is_verified: bool = False
    origin_relation: Literal["unresolved", "same_origin", "distinct_origin"] = (
        "unresolved"
    )
    distinct_dependence_lower: float | None = None
    distinct_dependence_upper: float | None = None
    origin_review_status: Literal[
        "suggested", "single_reviewed", "adjudicated", "frozen"
    ] = "suggested"
    origin_reviewers: list[str] = Field(default_factory=list)
    origin_adjudicator: str | None = None
    origin_review_timestamp: str | None = None
    origin_evidence_locator: str | None = None
    origin_rationale: str | None = None
    explanation: str = ""

    @field_validator("dependence_score")
    @classmethod
    def bounded_dependence(cls, value: float) -> float:
        parsed = float(value)
        if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
            raise ValueError("dependence_score must be finite and within [0, 1]")
        return parsed

    @field_validator("distinct_dependence_lower", "distinct_dependence_upper")
    @classmethod
    def bounded_optional_dependence(cls, value: float | None) -> float | None:
        if value is None:
            return None
        parsed = float(value)
        if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
            raise ValueError("distinct dependence endpoints must be finite and within [0, 1]")
        return parsed

    @field_validator("origin_reviewers", mode="before")
    @classmethod
    def normalized_origin_reviewers(cls, value: Any) -> list[str]:
        return normalize_string_list(value)

    @model_validator(mode="after")
    def valid_pair(self) -> "EvidenceLineageEdge":
        if not self.paper_id_a.strip() or not self.paper_id_b.strip():
            raise ValueError("lineage edge paper IDs must be nonempty")
        if self.paper_id_a == self.paper_id_b:
            raise ValueError("lineage edge must connect two distinct papers")
        endpoints = (
            self.distinct_dependence_lower,
            self.distinct_dependence_upper,
        )
        if (endpoints[0] is None) != (endpoints[1] is None):
            raise ValueError("distinct dependence endpoints must be supplied together")
        if (
            endpoints[0] is not None
            and endpoints[1] is not None
            and endpoints[0] > endpoints[1]
        ):
            raise ValueError("distinct dependence lower endpoint cannot exceed upper endpoint")
        return self


class TraceCandidate(TraceModel):
    chunk_id: str
    paper_id: str
    text: str
    token_cost: int
    retrieval_score: float
    section: str | None = None
    section_quality: float = 0.5
    quality_flags: list[str] = Field(default_factory=list)
    facet_scores: dict[str, float] = Field(default_factory=dict)
    scale_scores: dict[str, float] = Field(default_factory=dict)
    evidence_type_scores: dict[str, float] = Field(default_factory=dict)
    polarity_scores: dict[str, float] = Field(default_factory=dict)
    publication_family_id: str | None = None
    study_lineage_id: str | None = None
    dataset_lineage_id: str | None = None
    facet_origin_ids: dict[str, str] = Field(default_factory=dict)
    facet_origin_annotations_complete: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("token_cost")
    @classmethod
    def positive_token_cost(cls, value: int) -> int:
        return max(1, int(value))

    @field_validator("chunk_id", "paper_id", "text")
    @classmethod
    def nonempty_candidate_identity(cls, value: str) -> str:
        parsed = str(value).strip()
        if not parsed:
            raise ValueError("candidate identifiers and text must be nonempty")
        return parsed

    @field_validator("retrieval_score")
    @classmethod
    def finite_retrieval_score(cls, value: float) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("retrieval_score must be finite")
        return parsed

    @field_validator("section_quality")
    @classmethod
    def bounded_section_quality(cls, value: float) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("section_quality must be finite")
        return min(1.0, max(0.0, parsed))

    @field_validator(
        "facet_scores",
        "scale_scores",
        "evidence_type_scores",
        "polarity_scores",
    )
    @classmethod
    def bounded_score_mapping(cls, value: dict[str, float]) -> dict[str, float]:
        bounded = {}
        for key, score in value.items():
            parsed = float(score)
            if not math.isfinite(parsed):
                raise ValueError(f"score for {key!r} must be finite")
            bounded[str(key)] = min(1.0, max(0.0, parsed))
        return bounded

    @field_validator("facet_origin_ids", mode="before")
    @classmethod
    def normalized_facet_origin_ids(cls, value: Any) -> dict[str, str]:
        if value is None or value == "":
            return {}
        if not isinstance(value, dict):
            raise ValueError("facet_origin_ids must be a mapping")
        normalized: dict[str, str] = {}
        for raw_facet_id, raw_origin_id in value.items():
            facet_id = str(raw_facet_id).strip()
            origin_id = str(raw_origin_id).strip()
            if not facet_id or not origin_id:
                raise ValueError(
                    "facet_origin_ids must contain nonempty facet and origin IDs"
                )
            previous = normalized.get(facet_id)
            if previous is not None and previous != origin_id:
                raise ValueError(
                    "facet_origin_ids contains conflicting normalized facet keys: "
                    f"{facet_id}"
                )
            normalized[facet_id] = origin_id
        return normalized

    @field_validator("quality_flags", mode="before")
    @classmethod
    def normalized_quality_flags(cls, value: object) -> list[str]:
        return normalize_string_list(value)


class TraceSelectionResult(TraceModel):
    question_id: str
    selector_name: str
    selected_candidates: list[TraceCandidate] = Field(default_factory=list)
    utility_total: float = 0.0
    facet_coverage: float = 0.0
    scale_triangulation: float = 0.0
    polarity_coverage: float = 0.0
    relevance_quality: float = 0.0
    effective_independent_evidence: float = 0.0
    raw_paper_count: int = 0
    lineage_count: int = 0
    token_cost: int = 0
    unresolved_facets: list[str] = Field(default_factory=list)
    constraint_violations: list[str] = Field(default_factory=list)
    run_config: dict[str, Any] = Field(default_factory=dict)


class EvidenceFlowResult(TraceModel):
    """Auditable max-min evidence flow from study lineages to required facets."""

    total_flow: float = 0.0
    total_demand: float = 0.0
    normalized_coverage: float = 0.0
    bottleneck_coverage: float = 0.0
    facet_flow: dict[str, float] = Field(default_factory=dict)
    facet_demand: dict[str, float] = Field(default_factory=dict)
    facet_coverage: dict[str, float] = Field(default_factory=dict)
    lineage_outflow: dict[str, float] = Field(default_factory=dict)
    active_lineages: list[str] = Field(default_factory=list)
    unresolved_facets: list[str] = Field(default_factory=list)
    lineage_capacity: float = 1.0


class EvidenceSufficiencyCertificate(TraceModel):
    question_id: str
    evidence_accounting_version: str = EVIDENCE_ACCOUNTING_VERSION
    facet_coverage: float
    required_facets_covered: list[str] = Field(default_factory=list)
    unresolved_facets: list[str] = Field(default_factory=list)
    biological_scales_covered: list[str] = Field(default_factory=list)
    evidence_types_covered: list[str] = Field(default_factory=list)
    raw_paper_count: int
    substantive_paper_count: int
    publication_family_count: int
    evidence_lineage_count: int
    effective_independent_evidence: float
    supporting_lineages: int
    contradictory_lineages: int
    null_result_lineages: int
    limitation_lineages: int
    duplicate_lineage_rate: float
    independent_evidence_ratio: float
    evidence_inflation_factor: float
    minimum_required_papers: int = 0
    selection_stopping_reason: str | None = None
    lineage_graph_status: str = "not_assessed"
    evidence_sufficiency: Literal["good", "usable", "weak", "insufficient"]
    allowed_claim_strength: str
    reasons: list[str] = Field(default_factory=list)
