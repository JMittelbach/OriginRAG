"""System-blind question-gold and raw-review schemas for E052.

These records define corpus-conditional answerability and the canonical facets
against which every system answer is judged. They are evaluator-only inputs and
must never be exposed to an inference adapter.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


E052_QUESTION_GOLD_REVIEW_SCHEMA_VERSION = "pfempire_e052_question_gold_review_v2"
E052_QUESTION_GOLD_SCHEMA_VERSION = "pfempire_e052_question_gold_v2"

Answerability = Literal["answerable_full", "answerable_partial", "unanswerable"]
ClaimStrength = Literal[
    "none",
    "single_report",
    "single_origin",
    "multiple_independent_origins",
    "unresolved",
]
ReviewBasis = Literal["author_adjudicated", "independent_adjudicated"]
ReviewerRole = Literal["author", "independent_external"]
OriginRelationType = Literal[
    "cohort",
    "dataset",
    "publication_family",
    "study",
    "trial",
]
DisagreementField = Literal[
    "answerability",
    "canonical_required_facets",
    "maximum_allowable_claim_strength",
]

_CLAIM_STRENGTH_ORDER = {
    "none": 0,
    "single_report": 1,
    "single_origin": 2,
    "multiple_independent_origins": 3,
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class E052OriginResource(_StrictModel):
    """One typed scientific resource that can connect otherwise separate reports."""

    relation_type: OriginRelationType
    resource_id: str

    @field_validator("resource_id")
    @classmethod
    def nonempty_resource_id(cls, value: str) -> str:
        if not value:
            raise ValueError("origin resource IDs must be nonempty")
        return value


class E052OriginComponent(_StrictModel):
    """One conservative independence unit after collapsing shared resources."""

    component_id: str
    resources: list[E052OriginResource] = Field(min_length=1)

    @field_validator("component_id")
    @classmethod
    def nonempty_component_id(cls, value: str) -> str:
        if not value:
            raise ValueError("origin component IDs must be nonempty")
        return value

    @field_validator("resources")
    @classmethod
    def canonical_resources(
        cls, value: list[E052OriginResource]
    ) -> list[E052OriginResource]:
        keys = [(row.relation_type, row.resource_id) for row in value]
        if keys != sorted(set(keys)):
            raise ValueError("typed origin resources must be unique and sorted")
        return value


class E052CanonicalFacet(_StrictModel):
    """One required, system-independent answer facet for a question."""

    facet_id: str
    description: str
    expected_resolution: str
    supporting_origin_component_ids: list[str] = Field(min_length=1)
    maximum_allowable_claim_strength: ClaimStrength

    @field_validator("facet_id", "description", "expected_resolution")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        if not value:
            raise ValueError("facet identifiers and text must be nonempty")
        return value

    @field_validator("supporting_origin_component_ids")
    @classmethod
    def canonical_origins(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("supporting origin component IDs must be nonempty")
        if value != sorted(set(value)):
            raise ValueError("supporting origin component IDs must be unique and sorted")
        return value

    @model_validator(mode="after")
    def substantive_facet(self) -> "E052CanonicalFacet":
        if self.maximum_allowable_claim_strength == "none":
            raise ValueError("a required facet cannot have claim strength none")
        if (
            self.maximum_allowable_claim_strength == "multiple_independent_origins"
            and len(self.supporting_origin_component_ids) < 2
        ):
            raise ValueError(
                "multiple-independent-origins strength requires at least two components"
            )
        return self


class _QuestionGoldContent(_StrictModel):
    question_id: str
    question_sha256: str
    answerability: Answerability
    origin_components: list[E052OriginComponent] = Field(default_factory=list)
    canonical_required_facets: list[E052CanonicalFacet] = Field(default_factory=list)
    maximum_allowable_claim_strength: ClaimStrength
    abstention_expected: bool

    @field_validator("question_id")
    @classmethod
    def nonempty_question_id(cls, value: str) -> str:
        if not value:
            raise ValueError("question_id must be nonempty")
        return value

    @field_validator("question_sha256")
    @classmethod
    def valid_question_hash(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("question_sha256 must be a lowercase SHA-256 digest")
        return value

    @field_validator("canonical_required_facets")
    @classmethod
    def canonical_facets(
        cls, value: list[E052CanonicalFacet]
    ) -> list[E052CanonicalFacet]:
        facet_ids = [facet.facet_id for facet in value]
        if facet_ids != sorted(set(facet_ids)):
            raise ValueError("canonical required facets must be unique and sorted by facet_id")
        return value

    @field_validator("origin_components")
    @classmethod
    def canonical_origin_components(
        cls, value: list[E052OriginComponent]
    ) -> list[E052OriginComponent]:
        component_ids = [component.component_id for component in value]
        if component_ids != sorted(set(component_ids)):
            raise ValueError("origin components must be unique and sorted by component_id")
        resource_owners: dict[tuple[str, str], str] = {}
        for component in value:
            for resource in component.resources:
                resource_key = (resource.relation_type, resource.resource_id)
                previous = resource_owners.setdefault(
                    resource_key, component.component_id
                )
                if previous != component.component_id:
                    raise ValueError(
                        "origin components sharing any typed scientific resource must "
                        "be collapsed into one conservative independence component"
                    )
        return value

    @model_validator(mode="after")
    def coherent_answerability(self) -> "_QuestionGoldContent":
        if self.answerability == "unanswerable":
            if self.origin_components:
                raise ValueError("an unanswerable question cannot have origin components")
            if self.canonical_required_facets:
                raise ValueError("an unanswerable question cannot have required facets")
            if self.maximum_allowable_claim_strength != "none":
                raise ValueError("an unanswerable question must have claim strength none")
            if not self.abstention_expected:
                raise ValueError("an unanswerable question must expect abstention")
            return self
        if not self.canonical_required_facets:
            raise ValueError("an answerable question requires at least one canonical facet")
        if not self.origin_components:
            raise ValueError("an answerable question requires typed origin components")
        if self.abstention_expected:
            raise ValueError("an answerable question cannot expect abstention")
        component_ids = {component.component_id for component in self.origin_components}
        referenced_components = {
            component_id
            for facet in self.canonical_required_facets
            for component_id in facet.supporting_origin_component_ids
        }
        if referenced_components != component_ids:
            raise ValueError(
                "canonical facets must reference every and only defined origin component"
            )
        expected_strength = maximum_facet_strength(self.canonical_required_facets)
        if self.maximum_allowable_claim_strength != expected_strength:
            raise ValueError(
                "question claim strength must equal the maximum across canonical facets"
            )
        return self


class E052QuestionGoldReview(_QuestionGoldContent):
    """One raw, system-blind reviewer proposal retained before adjudication."""

    schema_version: Literal[E052_QUESTION_GOLD_REVIEW_SCHEMA_VERSION] = (
        E052_QUESTION_GOLD_REVIEW_SCHEMA_VERSION
    )
    review_record_id: str
    reviewer_id: str
    reviewer_role: ReviewerRole
    system_outputs_seen: Literal[False]
    system_blind_attested: Literal[True]
    submitted_at_utc: str
    review_notes: str

    @field_validator("review_record_id", "reviewer_id", "review_notes")
    @classmethod
    def nonempty_review_text(cls, value: str) -> str:
        if not value:
            raise ValueError("review identifiers and notes must be nonempty")
        return value

    @field_validator("submitted_at_utc")
    @classmethod
    def valid_review_time(cls, value: str) -> str:
        return require_utc_timestamp(value)


class E052QuestionGold(_QuestionGoldContent):
    """One locked adjudicated question-level gold record."""

    schema_version: Literal[E052_QUESTION_GOLD_SCHEMA_VERSION] = (
        E052_QUESTION_GOLD_SCHEMA_VERSION
    )
    review_status: Literal["locked"]
    review_basis: ReviewBasis
    reviewer_ids: list[str] = Field(min_length=2)
    reviewer_independence_attested: bool
    adjudicator_id: str | None = None
    source_review_record_ids: list[str] = Field(min_length=2)
    disagreement_fields: list[DisagreementField] = Field(default_factory=list)
    adjudication_rationale: str
    system_outputs_seen: Literal[False]
    system_blind_attested: Literal[True]
    locked_at_utc: str

    @field_validator("reviewer_ids", "source_review_record_ids")
    @classmethod
    def canonical_identifiers(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("review provenance identifiers must be nonempty")
        if value != sorted(set(value)):
            raise ValueError("review provenance identifiers must be unique and sorted")
        return value

    @field_validator("disagreement_fields")
    @classmethod
    def canonical_disagreements(
        cls, value: list[DisagreementField]
    ) -> list[DisagreementField]:
        if value != sorted(set(value)):
            raise ValueError("disagreement fields must be unique and sorted")
        return value

    @field_validator("adjudication_rationale")
    @classmethod
    def nonempty_rationale(cls, value: str) -> str:
        if not value:
            raise ValueError("adjudication_rationale must be nonempty")
        return value

    @field_validator("adjudicator_id")
    @classmethod
    def nonempty_adjudicator(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("adjudicator_id cannot be blank when supplied")
        return value

    @field_validator("locked_at_utc")
    @classmethod
    def valid_lock_time(cls, value: str) -> str:
        return require_utc_timestamp(value)

    @model_validator(mode="after")
    def coherent_review_provenance(self) -> "E052QuestionGold":
        if self.adjudicator_id is not None and self.adjudicator_id in self.reviewer_ids:
            raise ValueError("adjudicator must differ from the raw reviewers")
        if self.disagreement_fields and self.adjudicator_id is None:
            raise ValueError("disagreement resolution requires an adjudicator_id")
        if self.review_basis == "independent_adjudicated":
            if not self.reviewer_independence_attested:
                raise ValueError("independent adjudication requires an attestation")
        elif self.reviewer_independence_attested:
            raise ValueError("author-adjudicated gold cannot attest independence")
        return self


def maximum_facet_strength(facets: list[E052CanonicalFacet]) -> ClaimStrength:
    values = [facet.maximum_allowable_claim_strength for facet in facets]
    if "unresolved" in values:
        return "unresolved"
    return max(values, key=lambda value: _CLAIM_STRENGTH_ORDER[value])


def question_sha256(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def require_utc_timestamp(value: str) -> str:
    if not value or not value.endswith("Z"):
        raise ValueError("timestamp must be RFC 3339 UTC ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("timestamp must be RFC 3339 UTC") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must be UTC")
    return value


ModelT = TypeVar("ModelT", bound=_StrictModel)


def read_strict_jsonl(path: Path, model: type[ModelT]) -> list[ModelT]:
    records: list[ModelT] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise ValueError(f"blank JSONL row at {path}:{line_number}")
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_number}") from exc
            records.append(model.model_validate(payload))
    if not records:
        raise ValueError(f"question-gold JSONL is empty: {path}")
    return records


def read_e052_question_gold_reviews(path: Path) -> list[E052QuestionGoldReview]:
    return read_strict_jsonl(Path(path), E052QuestionGoldReview)


def read_e052_question_gold(path: Path) -> list[E052QuestionGold]:
    return read_strict_jsonl(Path(path), E052QuestionGold)
