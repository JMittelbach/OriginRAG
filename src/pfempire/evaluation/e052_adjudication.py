"""Strict blinded-adjudication schemas for the E052 external-RAG benchmark."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


E052_EVIDENCE_ADJUDICATION_SCHEMA_VERSION = (
    "pfempire_e052_evidence_adjudication_v3"
)
E052_ANSWER_ADJUDICATION_SCHEMA_VERSION = "pfempire_e052_answer_adjudication_v4"
E052_EVIDENCE_RAW_REVIEW_SCHEMA_VERSION = "pfempire_e052_evidence_review_v2"
E052_ANSWER_RAW_REVIEW_SCHEMA_VERSION = "pfempire_e052_answer_review_v2"

ReviewBasis = Literal["author_adjudicated", "independent_adjudicated"]
ReviewerRole = Literal["author", "independent_external"]
PrimaryRole = Literal[
    "direct_primary",
    "secondary_carrier",
    "reanalysis",
    "protocol",
    "context_only",
    "not_relevant",
    "unresolved",
]
ClaimStrength = Literal[
    "none",
    "single_report",
    "single_origin",
    "multiple_independent_origins",
    "unresolved",
]
EvidenceDisagreementField = Literal[
    "direct_primary_role",
    "facet_origin_support",
    "maximum_claim_strength",
    "substantive_relevance",
]
AnswerDisagreementField = Literal[
    "abstention_appropriate",
    "citation_link_judgments",
    "claim_judgments",
    "correct_noncontradicted_facet_count",
    "covered_correct_facet_ids",
    "false_independence_q",
    "full_answer",
    "overstatement_q",
    "required_facet_count",
    "reviewer_system_guess",
    "useful_qualified_partial",
]

_EVIDENCE_CONTENT_FIELDS = (
    "direct_primary_role",
    "facet_origin_support",
    "maximum_claim_strength",
    "substantive_relevance",
)
_ANSWER_CONTENT_FIELDS = (
    "abstention_appropriate",
    "citation_link_judgments",
    "claim_judgments",
    "correct_noncontradicted_facet_count",
    "covered_correct_facet_ids",
    "false_independence_q",
    "full_answer",
    "overstatement_q",
    "required_facet_count",
    "reviewer_system_guess",
    "useful_qualified_partial",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class E052FacetOriginSupport(_StrictModel):
    """Facet-specific links to conservative scientific-origin components."""

    facet_id: str
    origin_component_ids: list[str] = Field(default_factory=list)

    @field_validator("facet_id")
    @classmethod
    def nonempty_facet_id(cls, value: str) -> str:
        if not value:
            raise ValueError("facet identifiers must be nonempty")
        return value

    @field_validator("origin_component_ids")
    @classmethod
    def canonical_component_ids(cls, value: list[str]) -> list[str]:
        if any(not item for item in value) or value != sorted(set(value)):
            raise ValueError(
                "origin component IDs must be nonempty, unique, and sorted"
            )
        return value


class E052EvidenceAdjudication(_StrictModel):
    """One locked judgment for a blinded question--paper item."""

    schema_version: Literal[E052_EVIDENCE_ADJUDICATION_SCHEMA_VERSION] = (
        E052_EVIDENCE_ADJUDICATION_SCHEMA_VERSION
    )
    blind_item_id: str
    question_id: str
    review_status: Literal["locked"]
    review_basis: ReviewBasis
    reviewer_ids: list[str] = Field(min_length=1)
    reviewer_independence_attested: bool
    adjudicator_id: str | None = None
    source_review_record_ids: list[str] = Field(min_length=2)
    disagreement_fields: list[EvidenceDisagreementField] = Field(default_factory=list)
    adjudication_rationale: str
    locked_at_utc: str
    substantive_relevance: bool
    facet_origin_support: list[E052FacetOriginSupport] = Field(default_factory=list)
    direct_primary_role: PrimaryRole
    maximum_claim_strength: ClaimStrength
    review_notes: str | None = None

    @field_validator("blind_item_id", "question_id")
    @classmethod
    def nonempty_identity(cls, value: str) -> str:
        if not value:
            raise ValueError("adjudication identifiers must be nonempty")
        return value

    @field_validator("reviewer_ids")
    @classmethod
    def unique_nonempty_values(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("reviewer identifiers must be nonempty")
        if len(value) != len(set(value)):
            raise ValueError("reviewer identifiers must be unique")
        return value

    @field_validator("source_review_record_ids")
    @classmethod
    def canonical_source_review_ids(cls, value: list[str]) -> list[str]:
        if any(not item for item in value) or value != sorted(set(value)):
            raise ValueError(
                "source review record IDs must be nonempty, unique, and sorted"
            )
        return value

    @field_validator("disagreement_fields")
    @classmethod
    def canonical_disagreement_fields(
        cls, value: list[EvidenceDisagreementField]
    ) -> list[EvidenceDisagreementField]:
        if value != sorted(set(value)):
            raise ValueError("disagreement fields must be unique and sorted")
        return value

    @field_validator("facet_origin_support")
    @classmethod
    def canonical_facet_support(
        cls, value: list[E052FacetOriginSupport]
    ) -> list[E052FacetOriginSupport]:
        facets = [row.facet_id for row in value]
        if facets != sorted(set(facets)):
            raise ValueError("facet-origin support must be unique and sorted by facet_id")
        return value

    @property
    def answer_facets(self) -> list[str]:
        return [row.facet_id for row in self.facet_origin_support]

    @property
    def origin_component_ids(self) -> list[str]:
        return sorted(
            {
                component_id
                for row in self.facet_origin_support
                for component_id in row.origin_component_ids
            }
        )

    def component_ids_for_facets(self, facet_ids: set[str]) -> set[str]:
        return {
            component_id
            for row in self.facet_origin_support
            if row.facet_id in facet_ids
            for component_id in row.origin_component_ids
        }

    @field_validator("review_notes", "adjudicator_id")
    @classmethod
    def nonempty_optional_notes(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("review_notes cannot be blank when supplied")
        return value

    @field_validator("adjudication_rationale")
    @classmethod
    def nonempty_rationale(cls, value: str) -> str:
        if not value:
            raise ValueError("adjudication_rationale must be nonempty")
        return value

    @field_validator("locked_at_utc")
    @classmethod
    def valid_lock_time(cls, value: str) -> str:
        return _require_utc_timestamp(value)

    @model_validator(mode="after")
    def coherent_evidence_judgment(self) -> "E052EvidenceAdjudication":
        _validate_review_basis(
            review_basis=self.review_basis,
            reviewer_ids=self.reviewer_ids,
            reviewer_independence_attested=self.reviewer_independence_attested,
            adjudicator_id=self.adjudicator_id,
        )
        if self.disagreement_fields and self.adjudicator_id is None:
            raise ValueError("disagreement resolution requires an adjudicator_id")
        _validate_evidence_content(self)
        return self


class E052ClaimJudgment(_StrictModel):
    """Blinded decision about one canonical answer segment."""

    blind_claim_id: str
    substantive: bool
    facet_ids: list[str]
    expressed_claim_strength: ClaimStrength

    @field_validator("blind_claim_id")
    @classmethod
    def nonempty_claim_id(cls, value: str) -> str:
        if not value:
            raise ValueError("blind claim identifier must be nonempty")
        return value

    @field_validator("facet_ids")
    @classmethod
    def canonical_claim_facets(cls, value: list[str]) -> list[str]:
        if any(not item for item in value) or value != sorted(set(value)):
            raise ValueError("claim facet IDs must be nonempty, unique, and sorted")
        return value

    @model_validator(mode="after")
    def coherent_claim_judgment(self) -> "E052ClaimJudgment":
        if self.substantive:
            if self.expressed_claim_strength == "none":
                raise ValueError("a substantive claim requires a non-none strength")
        elif self.facet_ids or self.expressed_claim_strength != "none":
            raise ValueError(
                "a non-substantive segment cannot cover facets or express support"
            )
        return self


class E052CitationLinkJudgment(_StrictModel):
    """Entailment decision for one explicit claim-to-evidence edge."""

    blind_citation_id: str
    blind_claim_id: str
    entails_claim: bool

    @field_validator("blind_citation_id", "blind_claim_id")
    @classmethod
    def nonempty_link_id(cls, value: str) -> str:
        if not value:
            raise ValueError("blind citation and claim identifiers must be nonempty")
        return value


class _RawReviewProvenance(_StrictModel):
    review_record_id: str
    reviewer_id: str
    reviewer_role: ReviewerRole
    system_blind_attested: Literal[True]
    submitted_at_utc: str
    review_notes: str

    @field_validator("review_record_id", "reviewer_id", "review_notes")
    @classmethod
    def nonempty_review_provenance(cls, value: str) -> str:
        if not value:
            raise ValueError("raw review identifiers and notes must be nonempty")
        return value

    @field_validator("submitted_at_utc")
    @classmethod
    def valid_submission_time(cls, value: str) -> str:
        return _require_utc_timestamp(value)


class E052EvidenceRawReview(_RawReviewProvenance):
    """One immutable system-blind paper judgment before discussion."""

    schema_version: Literal[E052_EVIDENCE_RAW_REVIEW_SCHEMA_VERSION] = (
        E052_EVIDENCE_RAW_REVIEW_SCHEMA_VERSION
    )
    blind_item_id: str
    question_id: str
    substantive_relevance: bool
    facet_origin_support: list[E052FacetOriginSupport] = Field(default_factory=list)
    direct_primary_role: PrimaryRole
    maximum_claim_strength: ClaimStrength

    @field_validator("blind_item_id", "question_id")
    @classmethod
    def nonempty_item_identity(cls, value: str) -> str:
        if not value:
            raise ValueError("raw evidence-review identifiers must be nonempty")
        return value

    @field_validator("facet_origin_support")
    @classmethod
    def canonical_evidence_support(
        cls, value: list[E052FacetOriginSupport]
    ) -> list[E052FacetOriginSupport]:
        facets = [row.facet_id for row in value]
        if facets != sorted(set(facets)):
            raise ValueError("raw facet-origin support must be unique and sorted")
        return value

    @property
    def answer_facets(self) -> list[str]:
        return [row.facet_id for row in self.facet_origin_support]

    @property
    def origin_component_ids(self) -> list[str]:
        return sorted(
            {
                component_id
                for row in self.facet_origin_support
                for component_id in row.origin_component_ids
            }
        )

    @model_validator(mode="after")
    def coherent_raw_evidence(self) -> "E052EvidenceRawReview":
        _validate_evidence_content(self)
        return self


class E052AnswerRawReview(_RawReviewProvenance):
    """One immutable system-blind answer judgment before discussion."""

    schema_version: Literal[E052_ANSWER_RAW_REVIEW_SCHEMA_VERSION] = (
        E052_ANSWER_RAW_REVIEW_SCHEMA_VERSION
    )
    blind_answer_id: str
    question_id: str
    overstatement_q: bool
    false_independence_q: bool
    required_facet_count: int = Field(ge=0)
    correct_noncontradicted_facet_count: int = Field(ge=0)
    covered_correct_facet_ids: list[str]
    claim_judgments: list[E052ClaimJudgment] = Field(default_factory=list)
    citation_link_judgments: list[E052CitationLinkJudgment] = Field(
        default_factory=list
    )
    full_answer: bool
    useful_qualified_partial: bool
    abstention_appropriate: bool | None = None
    reviewer_system_guess: str | None = None

    @field_validator("blind_answer_id", "question_id")
    @classmethod
    def nonempty_answer_identity(cls, value: str) -> str:
        if not value:
            raise ValueError("raw answer-review identifiers must be nonempty")
        return value

    @field_validator("reviewer_system_guess")
    @classmethod
    def nonempty_optional_guess(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("reviewer_system_guess cannot be blank")
        return value

    @model_validator(mode="after")
    def coherent_raw_answer(self) -> "E052AnswerRawReview":
        _validate_answer_content(self)
        return self


class E052AnswerAdjudication(_StrictModel):
    """One locked, system-blinded judgment for a normalized system answer."""

    schema_version: Literal[E052_ANSWER_ADJUDICATION_SCHEMA_VERSION] = (
        E052_ANSWER_ADJUDICATION_SCHEMA_VERSION
    )
    blind_answer_id: str
    question_id: str
    review_status: Literal["locked"]
    review_basis: ReviewBasis
    reviewer_ids: list[str] = Field(min_length=1)
    reviewer_independence_attested: bool
    adjudicator_id: str | None = None
    source_review_record_ids: list[str] = Field(min_length=2)
    disagreement_fields: list[AnswerDisagreementField] = Field(default_factory=list)
    adjudication_rationale: str
    locked_at_utc: str
    overstatement_q: bool
    false_independence_q: bool
    required_facet_count: int = Field(ge=0)
    correct_noncontradicted_facet_count: int = Field(ge=0)
    covered_correct_facet_ids: list[str]
    claim_judgments: list[E052ClaimJudgment] = Field(default_factory=list)
    citation_link_judgments: list[E052CitationLinkJudgment] = Field(
        default_factory=list
    )
    full_answer: bool
    useful_qualified_partial: bool
    abstention_appropriate: bool | None = None
    reviewer_system_guess: str | None = None
    review_notes: str | None = None

    @field_validator("blind_answer_id", "question_id")
    @classmethod
    def nonempty_identity(cls, value: str) -> str:
        if not value:
            raise ValueError("adjudication identifiers must be nonempty")
        return value

    @field_validator("reviewer_ids")
    @classmethod
    def unique_nonempty_reviewers(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("reviewer identifiers must be nonempty")
        if len(value) != len(set(value)):
            raise ValueError("reviewer identifiers must be unique")
        return value

    @field_validator("source_review_record_ids")
    @classmethod
    def canonical_source_review_ids(cls, value: list[str]) -> list[str]:
        if any(not item for item in value) or value != sorted(set(value)):
            raise ValueError(
                "source review record IDs must be nonempty, unique, and sorted"
            )
        return value

    @field_validator("disagreement_fields")
    @classmethod
    def canonical_disagreement_fields(
        cls, value: list[AnswerDisagreementField]
    ) -> list[AnswerDisagreementField]:
        if value != sorted(set(value)):
            raise ValueError("disagreement fields must be unique and sorted")
        return value

    @field_validator("reviewer_system_guess", "review_notes", "adjudicator_id")
    @classmethod
    def nonempty_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("optional adjudication text cannot be blank")
        return value

    @field_validator("adjudication_rationale")
    @classmethod
    def nonempty_rationale(cls, value: str) -> str:
        if not value:
            raise ValueError("adjudication_rationale must be nonempty")
        return value

    @field_validator("locked_at_utc")
    @classmethod
    def valid_lock_time(cls, value: str) -> str:
        return _require_utc_timestamp(value)

    @model_validator(mode="after")
    def coherent_answer_judgment(self) -> "E052AnswerAdjudication":
        _validate_review_basis(
            review_basis=self.review_basis,
            reviewer_ids=self.reviewer_ids,
            reviewer_independence_attested=self.reviewer_independence_attested,
            adjudicator_id=self.adjudicator_id,
        )
        if self.disagreement_fields and self.adjudicator_id is None:
            raise ValueError("disagreement resolution requires an adjudicator_id")
        _validate_answer_content(self)
        return self


def _validate_review_basis(
    *,
    review_basis: ReviewBasis,
    reviewer_ids: list[str],
    reviewer_independence_attested: bool,
    adjudicator_id: str | None,
) -> None:
    if adjudicator_id is not None and adjudicator_id in reviewer_ids:
        raise ValueError("adjudicator must be distinct from listed reviewers")
    if review_basis == "independent_adjudicated":
        if len(reviewer_ids) < 2 or not reviewer_independence_attested:
            raise ValueError(
                "independent adjudication requires at least two reviewers and an "
                "explicit independence attestation"
            )
    elif reviewer_independence_attested:
        raise ValueError("author-adjudicated review cannot attest independence")


def _validate_evidence_content(value: Any) -> None:
    if value.substantive_relevance:
        if not value.facet_origin_support:
            raise ValueError("substantive evidence requires at least one answer facet")
        if (
            not value.origin_component_ids
            and value.maximum_claim_strength != "unresolved"
        ):
            raise ValueError(
                "substantive evidence requires at least one origin component"
            )
        if (
            value.maximum_claim_strength == "multiple_independent_origins"
            and len(value.origin_component_ids) < 2
        ):
            raise ValueError(
                "multiple-independent-origins strength requires at least two components"
            )
        if value.direct_primary_role == "not_relevant":
            raise ValueError("substantive evidence cannot have role not_relevant")
        if value.maximum_claim_strength == "none":
            raise ValueError("substantive evidence cannot have claim strength none")
    else:
        if value.facet_origin_support:
            raise ValueError("non-substantive evidence cannot cover answer facets")
        if value.direct_primary_role not in {"context_only", "not_relevant"}:
            raise ValueError(
                "non-substantive evidence must be context_only or not_relevant"
            )
        if value.maximum_claim_strength != "none":
            raise ValueError("non-substantive evidence must have claim strength none")


def _validate_answer_content(value: Any) -> None:
    if value.correct_noncontradicted_facet_count > value.required_facet_count:
        raise ValueError("correct facet count exceeds required facet count")
    if (
        any(not item for item in value.covered_correct_facet_ids)
        or value.covered_correct_facet_ids
        != sorted(set(value.covered_correct_facet_ids))
    ):
        raise ValueError("covered correct facet IDs must be nonempty, unique, and sorted")
    if value.correct_noncontradicted_facet_count != len(
        value.covered_correct_facet_ids
    ):
        raise ValueError(
            "correct facet count must equal the covered-correct facet ID count"
        )
    claim_ids = [row.blind_claim_id for row in value.claim_judgments]
    citation_ids = [
        row.blind_citation_id for row in value.citation_link_judgments
    ]
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("claim judgments must have unique blind claim IDs")
    if len(citation_ids) != len(set(citation_ids)):
        raise ValueError("citation judgments must have unique blind citation IDs")
    substantive_claim_ids = {
        row.blind_claim_id for row in value.claim_judgments if row.substantive
    }
    linked_claim_ids = {
        row.blind_claim_id for row in value.citation_link_judgments
    }
    if linked_claim_ids - set(claim_ids):
        raise ValueError("citation judgments reference unknown answer claims")
    if linked_claim_ids - substantive_claim_ids:
        raise ValueError("every citation edge must resolve to a substantive claim")
    if value.full_answer and value.useful_qualified_partial:
        raise ValueError("an answer cannot be both full and qualified-partial")


def _require_utc_timestamp(value: str) -> str:
    if not value or not value.endswith("Z"):
        raise ValueError("timestamp must be RFC 3339 UTC ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("timestamp must be RFC 3339 UTC") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must be UTC")
    return value


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _canonical_field_value(row: _StrictModel, field: str) -> str:
    return json.dumps(
        row.model_dump(mode="json")[field],
        sort_keys=True,
        separators=(",", ":"),
    )


def _derive_disagreement_fields(
    reviews: list[_StrictModel], fields: tuple[str, ...]
) -> list[str]:
    return sorted(
        field
        for field in fields
        if len({_canonical_field_value(row, field) for row in reviews}) > 1
    )


def _validate_unanimous_content(
    *,
    kind: str,
    item_id: str,
    judgment: _StrictModel,
    reviews: list[_StrictModel],
    fields: tuple[str, ...],
    disagreement_fields: list[str],
) -> None:
    """Reject any final label that silently changes unanimous raw judgment."""

    disputed = set(disagreement_fields)
    for field in fields:
        if field in disputed:
            continue
        if _canonical_field_value(judgment, field) != _canonical_field_value(
            reviews[0], field
        ):
            raise ValueError(
                f"{kind} {item_id} adjudication changed unanimous raw field {field}"
            )


def _read_jsonl(path: Path, model: type[_StrictModel]) -> list[_StrictModel]:
    records: list[_StrictModel] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid adjudication JSON on line {line_number}: {path}"
                ) from exc
            records.append(model.model_validate(payload))
    if not records:
        raise ValueError(f"adjudication JSONL is empty: {path}")
    return records


def read_e052_evidence_adjudications(
    path: Path,
) -> list[E052EvidenceAdjudication]:
    return [
        row
        for row in _read_jsonl(Path(path), E052EvidenceAdjudication)
        if isinstance(row, E052EvidenceAdjudication)
    ]


def read_e052_answer_adjudications(path: Path) -> list[E052AnswerAdjudication]:
    return [
        row
        for row in _read_jsonl(Path(path), E052AnswerAdjudication)
        if isinstance(row, E052AnswerAdjudication)
    ]


def read_e052_evidence_raw_reviews(path: Path) -> list[E052EvidenceRawReview]:
    return [
        row
        for row in _read_jsonl(Path(path), E052EvidenceRawReview)
        if isinstance(row, E052EvidenceRawReview)
    ]


def read_e052_answer_raw_reviews(path: Path) -> list[E052AnswerRawReview]:
    return [
        row
        for row in _read_jsonl(Path(path), E052AnswerRawReview)
        if isinstance(row, E052AnswerRawReview)
    ]


def validate_raw_review_trail(
    *,
    evidence_reviews: list[E052EvidenceRawReview],
    answer_reviews: list[E052AnswerRawReview],
    evidence_adjudications: list[E052EvidenceAdjudication],
    answer_adjudications: list[E052AnswerAdjudication],
) -> dict[str, Any]:
    """Bind raw double reviews to locked adjudications, fail closed.

    Raw records remain immutable. This validator reproduces item coverage,
    reviewer identity, disagreement fields, consensus preservation and temporal
    ordering; it intentionally does not fabricate agreement coefficients from
    adjudicated labels.
    """

    evidence_items = {row.blind_item_id: row for row in evidence_adjudications}
    answer_items = {row.blind_answer_id: row for row in answer_adjudications}
    if len(evidence_items) != len(evidence_adjudications) or len(answer_items) != len(
        answer_adjudications
    ):
        raise ValueError("locked adjudications contain duplicate item IDs")
    grouped_evidence: dict[str, list[E052EvidenceRawReview]] = defaultdict(list)
    grouped_answers: dict[str, list[E052AnswerRawReview]] = defaultdict(list)
    seen_record_ids: set[str] = set()
    seen_item_reviewer: set[tuple[str, str, str]] = set()
    for kind, reviews, grouped in (
        ("evidence", evidence_reviews, grouped_evidence),
        ("answer", answer_reviews, grouped_answers),
    ):
        for row in reviews:
            item_id = row.blind_item_id if kind == "evidence" else row.blind_answer_id
            if row.review_record_id in seen_record_ids:
                raise ValueError("raw review_record_id values must be globally unique")
            seen_record_ids.add(row.review_record_id)
            key = (kind, item_id, row.reviewer_id)
            if key in seen_item_reviewer:
                raise ValueError("one reviewer submitted multiple reviews for one item")
            seen_item_reviewer.add(key)
            grouped[item_id].append(row)
    if set(grouped_evidence) != set(evidence_items) or set(grouped_answers) != set(
        answer_items
    ):
        raise ValueError("raw review item coverage differs from locked adjudications")

    role_counts: Counter[str] = Counter()
    unanimous: Counter[str] = Counter()
    disagreement_counts: Counter[str] = Counter()
    referenced_review_ids: set[str] = set()
    reviewed_count = 0
    for kind, grouped, adjudications in (
        ("evidence", grouped_evidence, evidence_items),
        ("answer", grouped_answers, answer_items),
    ):
        for item_id, reviews in grouped.items():
            if len(reviews) < 2:
                raise ValueError("every adjudicated item requires at least two raw reviews")
            judgment = adjudications[item_id]
            reviewers = sorted(row.reviewer_id for row in reviews)
            if sorted(judgment.reviewer_ids) != reviewers:
                raise ValueError("locked adjudication reviewer IDs differ from raw reviews")
            expected_record_ids = sorted(row.review_record_id for row in reviews)
            if judgment.source_review_record_ids != expected_record_ids:
                raise ValueError(
                    f"{kind} {item_id} adjudication does not reference every raw "
                    "review exactly once"
                )
            roles = {row.reviewer_role for row in reviews}
            if judgment.review_basis == "independent_adjudicated":
                if roles != {"independent_external"}:
                    raise ValueError("independent adjudication lacks independent raw reviews")
            elif roles == {"independent_external"}:
                raise ValueError("author adjudication contradicts independent raw reviews")
            if any(row.question_id != judgment.question_id for row in reviews):
                raise ValueError("raw review question differs from locked adjudication")
            fields = (
                _EVIDENCE_CONTENT_FIELDS
                if kind == "evidence"
                else _ANSWER_CONTENT_FIELDS
            )
            expected_disagreements = _derive_disagreement_fields(reviews, fields)
            if judgment.disagreement_fields != expected_disagreements:
                raise ValueError(
                    f"{kind} {item_id} disagreement_fields do not reproduce the "
                    "raw reviews"
                )
            _validate_unanimous_content(
                kind=kind,
                item_id=item_id,
                judgment=judgment,
                reviews=reviews,
                fields=fields,
                disagreement_fields=expected_disagreements,
            )
            latest_review = max(_parse_utc(row.submitted_at_utc) for row in reviews)
            if _parse_utc(judgment.locked_at_utc) < latest_review:
                raise ValueError(
                    f"{kind} {item_id} was locked before its raw reviews"
                )
            referenced_review_ids.update(judgment.source_review_record_ids)
            disagreement_counts.update(
                f"{kind}.{field}" for field in expected_disagreements
            )
            role_counts.update(row.reviewer_role for row in reviews)
            reviewed_count += 1
            unanimous[kind] += int(not expected_disagreements)
    if referenced_review_ids != seen_record_ids:
        raise ValueError("one or more raw review records are not represented in gold")
    return {
        "status": "validated_raw_double_review_trail",
        "evidence_item_count": len(evidence_items),
        "answer_item_count": len(answer_items),
        "raw_evidence_review_count": len(evidence_reviews),
        "raw_answer_review_count": len(answer_reviews),
        "reviewer_role_counts": dict(sorted(role_counts.items())),
        "unanimous_evidence_item_count": unanimous["evidence"],
        "unanimous_answer_item_count": unanimous["answer"],
        "disagreement_field_counts": dict(sorted(disagreement_counts.items())),
        "reviewed_item_count": reviewed_count,
    }
