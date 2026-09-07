"""Strict, outcome-blind result contract for the E052 external-RAG benchmark."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EXTERNAL_RAG_RESULT_SCHEMA_VERSION = "pfempire_external_rag_result_v2"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

ResultStatus = Literal["answer", "abstain", "error", "timeout"]
BenchmarkTrack = Literal["native_end_to_end", "controlled_evidence_handoff"]
SupportDecision = Literal["sufficient", "insufficient", "not_assessed"]
TokenCountMethod = Literal["reported", "estimated", "unavailable"]
ComponentName = Literal[
    "answer_model",
    "embedding_model",
    "planner_model",
    "evaluator_model",
    "reranker_model",
    "verifier_model",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class AnswerClaim(_StrictModel):
    """One canonical answer segment available for blinded claim review.

    Claims use zero-based, half-open character offsets into the exact normalized
    answer.  The common segmenter covers every non-whitespace answer character,
    including headings and reference lines.  Reviewers, rather than systems,
    decide which segments are substantive scientific claims.
    """

    claim_id: str
    text_span_start: int = Field(ge=0)
    text_span_end: int = Field(ge=1)
    text: str

    @field_validator("claim_id", "text")
    @classmethod
    def nonempty_claim_field(cls, value: str) -> str:
        if not value:
            raise ValueError("claim identifiers and text must be nonempty")
        return value

    @model_validator(mode="after")
    def coherent_span(self) -> "AnswerClaim":
        if self.text_span_end <= self.text_span_start:
            raise ValueError("claim span end must be greater than its start")
        if self.text != self.text.strip():
            raise ValueError("claim text must not contain boundary whitespace")
        return self


class Citation(_StrictModel):
    """One explicit claim-to-evidence edge emitted with an answer."""

    citation_id: str
    claim_id: str
    evidence_id: str

    @field_validator("citation_id", "claim_id", "evidence_id")
    @classmethod
    def nonempty_identifier(cls, value: str) -> str:
        if not value:
            raise ValueError("citation, claim, and evidence identifiers must be nonempty")
        return value


class RetrievedEvidence(_StrictModel):
    """One ranked passage made available to an evaluated system."""

    evidence_id: str
    rank: int = Field(ge=1)
    text: str
    paper_id: str | None = None
    chunk_id: str | None = None
    doi: str | None = None
    source_locator: str | None = None
    retrieval_score: float | None = None

    @field_validator("evidence_id", "text")
    @classmethod
    def nonempty_required_text(cls, value: str) -> str:
        if not value:
            raise ValueError("evidence_id and text must be nonempty")
        return value

    @field_validator("paper_id", "chunk_id", "doi", "source_locator")
    @classmethod
    def nonempty_optional_identifier(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("optional evidence identifiers cannot be blank")
        return value

    @field_validator("retrieval_score")
    @classmethod
    def finite_optional_score(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("retrieval_score must be finite")
        return value


class LLMUsage(_StrictModel):
    """Language-model calls and token accounting for one system result."""

    calls: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    token_count_method: TokenCountMethod = "unavailable"

    @model_validator(mode="after")
    def coherent_token_accounting(self) -> "LLMUsage":
        values = (self.input_tokens, self.output_tokens, self.total_tokens)
        if self.token_count_method == "unavailable":
            if any(value is not None for value in values):
                raise ValueError(
                    "token counts must be null when token_count_method is unavailable"
                )
            return self
        if any(value is None for value in values):
            raise ValueError(
                "input, output, and total tokens are required for reported or estimated counts"
            )
        if self.total_tokens != self.input_tokens + self.output_tokens:  # type: ignore[operator]
            raise ValueError("total_tokens must equal input_tokens plus output_tokens")
        return self


class ResourceUsage(_StrictModel):
    """Nonnegative runtime and optional process-resource telemetry."""

    wall_time_seconds: float = Field(ge=0.0)
    cpu_time_seconds: float | None = Field(default=None, ge=0.0)
    peak_rss_mb: float | None = Field(default=None, ge=0.0)
    peak_vram_mb: float | None = Field(default=None, ge=0.0)

    @field_validator(
        "wall_time_seconds",
        "cpu_time_seconds",
        "peak_rss_mb",
        "peak_vram_mb",
    )
    @classmethod
    def finite_telemetry(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("resource telemetry must be finite")
        return value


class ExternalRAGResult(_StrictModel):
    """Normalized output of one system for one frozen E052 question."""

    schema_version: Literal[EXTERNAL_RAG_RESULT_SCHEMA_VERSION] = (
        EXTERNAL_RAG_RESULT_SCHEMA_VERSION
    )
    question_id: str
    system_id: str
    system_version: str
    track: BenchmarkTrack
    prompt_policy: str
    status: ResultStatus
    answer: str = ""
    reason: str | None = None
    support_decision: SupportDecision
    claims: list[AnswerClaim] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    retrieved: list[RetrievedEvidence] = Field(default_factory=list)
    final_evidence: list[str] = Field(default_factory=list)
    model_hash: str = Field(pattern=_SHA256_PATTERN)
    component_hashes: dict[ComponentName, str]
    prompt_hash: str = Field(pattern=_SHA256_PATTERN)
    config_hash: str = Field(pattern=_SHA256_PATTERN)
    corpus_hash: str = Field(pattern=_SHA256_PATTERN)
    llm_usage: LLMUsage
    resource_usage: ResourceUsage
    error_type: str | None = None
    error_message: str | None = None
    adapter_trace_path: str | None = None
    adapter_trace_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    retries: int = Field(ge=0)
    network_access: Literal[False] = False

    @field_validator(
        "question_id",
        "system_id",
        "system_version",
        "prompt_policy",
    )
    @classmethod
    def nonempty_identity(cls, value: str) -> str:
        if not value:
            raise ValueError("result identity and policy fields must be nonempty")
        return value

    @field_validator("reason", "error_type", "error_message", "adapter_trace_path")
    @classmethod
    def nonempty_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("optional reason and error fields cannot be blank")
        return value

    @field_validator("final_evidence")
    @classmethod
    def nonempty_final_evidence_ids(cls, value: list[str]) -> list[str]:
        if any(not evidence_id for evidence_id in value):
            raise ValueError("final_evidence identifiers must be nonempty")
        return value

    @field_validator("component_hashes")
    @classmethod
    def valid_component_hashes(
        cls, value: dict[ComponentName, str]
    ) -> dict[ComponentName, str]:
        if "answer_model" not in value:
            raise ValueError("component_hashes must contain answer_model")
        for digest in value.values():
            if not isinstance(digest, str) or not re.fullmatch(_SHA256_PATTERN, digest):
                raise ValueError("component hashes must be lowercase SHA-256 digests")
        return value

    @model_validator(mode="after")
    def coherent_result(self) -> "ExternalRAGResult":
        evidence_ids = [row.evidence_id for row in self.retrieved]
        ranks = [row.rank for row in self.retrieved]
        citation_ids = [row.citation_id for row in self.citations]
        claim_ids = [row.claim_id for row in self.claims]

        _require_unique(evidence_ids, label="retrieved evidence_id")
        _require_unique(ranks, label="retrieved rank")
        _require_unique(citation_ids, label="citation_id")
        _require_unique(claim_ids, label="claim_id")
        _require_unique(self.final_evidence, label="final_evidence")

        if self.status == "answer" and self.answer:
            previous_end = -1
            covered = bytearray(len(self.answer))
            for claim in self.claims:
                if claim.text_span_start < previous_end:
                    raise ValueError("claim spans must be ordered and nonoverlapping")
                if claim.text_span_end > len(self.answer):
                    raise ValueError("claim span falls outside the normalized answer")
                if self.answer[claim.text_span_start : claim.text_span_end] != claim.text:
                    raise ValueError("claim text must equal its exact normalized-answer span")
                covered[claim.text_span_start : claim.text_span_end] = b"\x01" * (
                    claim.text_span_end - claim.text_span_start
                )
                previous_end = claim.text_span_end
            if any(
                not covered[index]
                for index, character in enumerate(self.answer)
                if not character.isspace()
            ):
                raise ValueError("claims must cover every non-whitespace answer character")

        retrieved_id_set = set(evidence_ids)
        final_id_set = set(self.final_evidence)
        unknown_final = final_id_set - retrieved_id_set
        if unknown_final:
            raise ValueError(
                "final_evidence must reference retrieved evidence IDs: "
                + ", ".join(sorted(unknown_final))
            )
        unknown_citations = {
            citation.evidence_id for citation in self.citations
        } - final_id_set
        if unknown_citations:
            raise ValueError(
                "citations must reference final_evidence IDs: "
                + ", ".join(sorted(unknown_citations))
            )
        unknown_claims = {citation.claim_id for citation in self.citations} - set(
            claim_ids
        )
        if unknown_claims:
            raise ValueError(
                "citations must reference answer claim IDs: "
                + ", ".join(sorted(unknown_claims))
            )
        citation_edges = [
            (citation.claim_id, citation.evidence_id) for citation in self.citations
        ]
        _require_unique(citation_edges, label="claim-to-evidence citation edge")

        trace_fields = (self.adapter_trace_path, self.adapter_trace_hash)
        if (trace_fields[0] is None) != (trace_fields[1] is None):
            raise ValueError(
                "adapter_trace_path and adapter_trace_hash must be supplied together"
            )
        if self.adapter_trace_path is not None:
            trace_path = Path(self.adapter_trace_path)
            if trace_path.is_absolute() or ".." in trace_path.parts:
                raise ValueError("adapter_trace_path must be a portable relative path")
        if self.component_hashes["answer_model"] != self.model_hash:
            raise ValueError(
                "model_hash must equal component_hashes['answer_model']"
            )

        has_answer = bool(self.answer)
        has_reason = self.reason is not None
        has_error = self.error_type is not None or self.error_message is not None
        if self.status == "answer":
            if not has_answer:
                raise ValueError("answer status requires a nonempty answer")
            if not self.claims:
                raise ValueError("answer status requires canonical answer claims")
            if has_error:
                raise ValueError("answer status cannot contain error fields")
        elif self.status == "abstain":
            if has_answer or not has_reason:
                raise ValueError("abstain status requires an empty answer and a reason")
            if self.support_decision == "sufficient":
                raise ValueError("abstain status cannot declare sufficient support")
            if has_error:
                raise ValueError("abstain status cannot contain error fields")
            if self.citations:
                raise ValueError("abstain status cannot contain answer citations")
            if self.claims:
                raise ValueError("abstain status cannot contain answer claims")
        else:
            if has_answer or not has_reason:
                raise ValueError(
                    "error and timeout statuses require an empty answer and a reason"
                )
            if self.error_type is None or self.error_message is None:
                raise ValueError(
                    "error and timeout statuses require error_type and error_message"
                )
            if self.support_decision != "not_assessed":
                raise ValueError(
                    "error and timeout statuses require support_decision=not_assessed"
                )
            if self.citations:
                raise ValueError("error and timeout statuses cannot contain citations")
            if self.claims:
                raise ValueError("error and timeout statuses cannot contain claims")
        return self


def _require_unique(values: list[Any], *, label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} values must be unique")


def segment_answer_claims(answer: str) -> list[dict[str, Any]]:
    """Return deterministic, exhaustive sentence/line spans for one answer.

    This is deliberately a mechanical representation step, not semantic claim
    extraction.  It prevents systems from choosing a favorable completeness
    denominator.  Blinded reviewers label which resulting segments are
    substantive.
    """

    if not isinstance(answer, str) or not answer:
        return []
    spans: list[tuple[int, int]] = []
    for line_match in re.finditer(r"[^\r\n]+", answer):
        raw_start, raw_end = line_match.span()
        while raw_start < raw_end and answer[raw_start].isspace():
            raw_start += 1
        while raw_end > raw_start and answer[raw_end - 1].isspace():
            raw_end -= 1
        if raw_start == raw_end:
            continue
        segment_start = raw_start
        for index in range(raw_start, raw_end):
            if answer[index] not in ".!?":
                continue
            if index + 1 < raw_end and not answer[index + 1].isspace():
                continue
            segment_end = index + 1
            while segment_start < segment_end and answer[segment_start].isspace():
                segment_start += 1
            if segment_start < segment_end:
                spans.append((segment_start, segment_end))
            segment_start = segment_end
            while segment_start < raw_end and answer[segment_start].isspace():
                segment_start += 1
        if segment_start < raw_end:
            spans.append((segment_start, raw_end))
    return [
        {
            "claim_id": f"CLM{index:04d}",
            "text_span_start": start,
            "text_span_end": end,
            "text": answer[start:end],
        }
        for index, (start, end) in enumerate(spans, start=1)
    ]


def claim_id_for_exact_span(
    claims: Sequence[Mapping[str, Any]], *, start: int, end: int
) -> str:
    """Resolve a nonempty answer span to exactly one canonical claim."""

    matches = [
        str(claim["claim_id"])
        for claim in claims
        if int(claim["text_span_start"]) <= start
        and end <= int(claim["text_span_end"])
    ]
    if len(matches) != 1:
        raise ValueError("citation span must resolve to exactly one canonical claim")
    return matches[0]


def validate_external_rag_result(
    payload: ExternalRAGResult | Mapping[str, Any],
) -> ExternalRAGResult:
    """Validate one normalized result without accepting undeclared fields."""

    if isinstance(payload, ExternalRAGResult):
        return payload
    return ExternalRAGResult.model_validate(payload)


def external_rag_result_to_dict(
    payload: ExternalRAGResult | Mapping[str, Any],
) -> dict[str, Any]:
    """Return a JSON-compatible, fully validated result mapping."""

    return validate_external_rag_result(payload).model_dump(mode="json")


def read_external_rag_jsonl(path: Path) -> list[ExternalRAGResult]:
    """Read and validate a result JSONL file with line-local diagnostics."""

    rows: list[ExternalRAGResult] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid external-RAG JSONL in {path}:{line_number}: {exc.msg}"
                ) from exc
            try:
                rows.append(validate_external_rag_result(payload))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid external-RAG result in {path}:{line_number}: {exc}"
                ) from exc
    return rows


def write_external_rag_jsonl(
    path: Path,
    rows: Iterable[ExternalRAGResult | Mapping[str, Any]],
) -> None:
    """Validate all rows, then atomically replace ``path`` with canonical JSONL."""

    validated = [external_rag_result_to_dict(row) for row in rows]
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for row in validated:
                handle.write(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        sort_keys=True,
                        allow_nan=False,
                    )
                )
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
