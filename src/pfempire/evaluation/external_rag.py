"""Deterministic building blocks for an adapted DF-RAG reproduction.

This module deliberately contains no model or embedding inference.  It implements
the geometric maximal marginal relevance (gMMR) selection rule and the published
lambda sweep needed by a separately versioned benchmark runner.  The prompt
templates below are concise local adaptations, not verbatim copies of a complete
reference implementation.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence


DFRAG_REFERENCE_DOI = "10.18653/v1/2026.findings-eacl.150"
DFRAG_OFFICIAL_EMBEDDING_MODEL = "sentence-transformers/multi-qa-mpnet-base-cos-v1"
DFRAG_LAMBDA_GRID = tuple(index / 10.0 for index in range(1, 11))
DFRAG_DEFAULT_K = 5
DFRAG_PLANNER_PROMPT_VERSION = "pfempire-dfrag-planner-adaptation-v1"
DFRAG_EVALUATOR_PROMPT_VERSION = "pfempire-dfrag-evaluator-adaptation-v1"
DFRAG_GENERATOR_PROMPT_VERSION = "pfempire-dfrag-generator-citation-adaptation-v1"
DFRAG_ADAPTED_REPRODUCTION_BOUNDARY = (
    "The adapter reproduces the published deterministic gMMR rule, lambda grid, "
    "and upper-median tie decision. Its concise planner and evaluator prompts are "
    "versioned local adaptations. It provides neither model inference nor reference "
    "weights, so it is not by itself a complete or exact DF-RAG reproduction."
)
DFRAG_REFERENCE_METADATA: Mapping[str, object] = MappingProxyType(
    {
        "doi": DFRAG_REFERENCE_DOI,
        "embedding_model": DFRAG_OFFICIAL_EMBEDDING_MODEL,
        "lambda_grid": DFRAG_LAMBDA_GRID,
        "default_k": DFRAG_DEFAULT_K,
        "reproduction_boundary": DFRAG_ADAPTED_REPRODUCTION_BOUNDARY,
    }
)


@dataclass(frozen=True)
class EmbeddedChunk:
    """A uniquely identified text chunk and its precomputed dense vector."""

    chunk_id: str
    text: str
    vector: tuple[float, ...]


@dataclass(frozen=True)
class PromptPassage:
    """A uniquely identified passage supplied to the DF-RAG evaluator."""

    passage_id: str
    text: str


@dataclass(frozen=True)
class GeneratedCitation:
    """One citation emitted by the adapted DF-RAG generator."""

    evidence_id: str
    answer_span: str


@dataclass(frozen=True)
class GeneratedAnswer:
    """Strictly parsed answer and its answer-to-evidence links."""

    answer: str
    citations: tuple[GeneratedCitation, ...]


def gmmr_select(
    query_vector: Sequence[float],
    candidates: Sequence[EmbeddedChunk],
    *,
    lambda_value: float,
    k: int = DFRAG_DEFAULT_K,
) -> tuple[EmbeddedChunk, ...]:
    r"""Select ``k`` chunks using the published geometric MMR rule.

    The first chunk maximizes cosine similarity to the query.  For every later
    chunk ``c`` and already selected set ``S``, the score is

    ``lambda * cos(q, c) + (1 - lambda) * sqrt(2 - 2*cos(c, centroid(S)))``.

    Equal scores are resolved by ascending ``chunk_id``. Invalid vectors and
    duplicate identifiers fail closed. Distinct chunks may legitimately have
    identical embeddings; they remain eligible and are ordered by identifier.
    """

    query, rows = _validate_vector_problem(query_vector, candidates)
    lambda_number = _validate_lambda(lambda_value)
    if isinstance(k, bool) or not isinstance(k, int) or k <= 0:
        raise ValueError("k must be a positive integer")
    if k > len(rows):
        raise ValueError("k cannot exceed the number of candidate chunks")

    relevance = {row.chunk_id: _cosine_similarity(query, row.vector) for row in rows}
    remaining = {row.chunk_id: row for row in rows}
    first = min(rows, key=lambda row: (-relevance[row.chunk_id], row.chunk_id))
    selected = [first]
    del remaining[first.chunk_id]

    while len(selected) < k:
        centroid = tuple(
            math.fsum(row.vector[dimension] for row in selected) / len(selected)
            for dimension in range(len(query))
        )
        _validate_nonzero_vector(centroid, label="selected-set centroid")

        scored: list[tuple[float, str, EmbeddedChunk]] = []
        for chunk_id, row in remaining.items():
            centroid_cosine = _cosine_similarity(row.vector, centroid)
            diversity = math.sqrt(max(0.0, 2.0 - 2.0 * centroid_cosine))
            score = lambda_number * relevance[chunk_id] + (1.0 - lambda_number) * diversity
            if not math.isfinite(score):
                raise ValueError(f"non-finite gMMR score for chunk {chunk_id!r}")
            scored.append((-score, chunk_id, row))
        chosen = min(scored)[2]
        selected.append(chosen)
        del remaining[chosen.chunk_id]

    return tuple(selected)


def gmmr_lambda_sweep(
    query_vector: Sequence[float],
    candidates: Sequence[EmbeddedChunk],
    *,
    k: int = DFRAG_DEFAULT_K,
    lambdas: Sequence[float] = DFRAG_LAMBDA_GRID,
) -> tuple[tuple[float, tuple[EmbeddedChunk, ...]], ...]:
    """Run deterministic gMMR independently for each unique lambda value."""

    normalized_lambdas = _validate_lambda_sequence(lambdas)
    return tuple(
        (
            lambda_value,
            gmmr_select(
                query_vector,
                candidates,
                lambda_value=lambda_value,
                k=k,
            ),
        )
        for lambda_value in normalized_lambdas
    )


def choose_upper_median_lambda(
    scores: Mapping[float, int] | Iterable[tuple[float, int]],
    *,
    expected_lambdas: Sequence[float] = DFRAG_LAMBDA_GRID,
) -> float:
    """Choose the upper-median lambda among all maximum-scoring candidates.

    By default all ten published lambda settings must be present exactly once.
    Accepting an iterable of pairs, rather than only a mapping, lets the function
    detect duplicated evaluator results and fail closed.
    """

    items = tuple(scores.items()) if isinstance(scores, Mapping) else tuple(scores)
    expected = _validate_lambda_sequence(expected_lambdas)
    if not items:
        raise ValueError("at least one evaluator score is required")

    score_by_lambda: dict[float, int] = {}
    for raw_lambda, score in items:
        lambda_value = _validate_lambda(raw_lambda)
        if lambda_value in score_by_lambda:
            raise ValueError(f"duplicate evaluator score for lambda {lambda_value:g}")
        if isinstance(score, bool) or not isinstance(score, int) or score < 0:
            raise ValueError("evaluator scores must be non-negative integers")
        score_by_lambda[lambda_value] = score

    if set(score_by_lambda) != set(expected):
        missing = sorted(set(expected) - set(score_by_lambda))
        extra = sorted(set(score_by_lambda) - set(expected))
        raise ValueError(
            f"evaluator lambda set differs from the frozen sweep; missing={missing}, extra={extra}"
        )

    best_score = max(score_by_lambda.values())
    tied = sorted(
        lambda_value for lambda_value, score in score_by_lambda.items() if score == best_score
    )
    return tied[len(tied) // 2]


_ITEM_SCORE_RE = re.compile(r"^Item ([1-9][0-9]*): ([0-5])(?:\s*[-:]\s*.+)?$")
_TOTAL_SCORE_RE = re.compile(r"^Total Score: ([0-9]+)$")
_PLAN_ITEM_RE = re.compile(r"^([1-9][0-9]*)[.)] (\S(?:.*\S)?)$")


def parse_plan(response: str, *, maximum_items: int = 12) -> tuple[str, ...]:
    """Parse one consecutive, commentary-free numbered plan.

    The parser accepts ``1. text`` or ``1) text`` because both appear in the
    published prompt family, but it rejects headings, blank lines, duplicated
    numbers and trailing commentary.  Returning the item text separately makes
    the evaluator item count independently checkable.
    """

    if not isinstance(response, str) or not response.strip():
        raise ValueError("planner response is empty")
    if isinstance(maximum_items, bool) or not isinstance(maximum_items, int) or maximum_items <= 0:
        raise ValueError("maximum_items must be a positive integer")
    lines = response.rstrip().splitlines()
    if any(not line for line in lines):
        raise ValueError("planner response must not contain blank lines")
    if len(lines) > maximum_items:
        raise ValueError("planner response exceeds the frozen item limit")
    items: list[str] = []
    for expected_index, line in enumerate(lines, start=1):
        match = _PLAN_ITEM_RE.fullmatch(line)
        if match is None or int(match.group(1)) != expected_index:
            raise ValueError("planner response must contain consecutive numbered items only")
        items.append(match.group(2))
    return tuple(items)


def parse_total_score(response: str, *, expected_item_count: int | None = None) -> int:
    """Parse and validate per-plan support scores plus their final total.

    Every nonempty line before the total must begin with ``Item i: s``, item
    numbers must be consecutive from one, each score must be within 0--5, and
    the final total must equal their sum. Decimal, signed, duplicated, or
    embedded totals are rejected rather than guessed.
    """

    if not isinstance(response, str) or not response.strip():
        raise ValueError("evaluator response is empty")
    lines = response.rstrip().splitlines()
    occurrences = [line for line in lines if "Total Score" in line]
    if len(occurrences) != 1:
        raise ValueError("evaluator response must contain exactly one Total Score line")
    match = _TOTAL_SCORE_RE.fullmatch(lines[-1])
    if match is None:
        raise ValueError("the final line must be exactly 'Total Score: N'")
    item_scores: list[int] = []
    for expected_index, line in enumerate(lines[:-1], start=1):
        item_match = _ITEM_SCORE_RE.fullmatch(line)
        if item_match is None:
            raise ValueError("every evaluator item line must be 'Item i: s' with s in 0..5")
        if int(item_match.group(1)) != expected_index:
            raise ValueError("evaluator item numbers must be consecutive from one")
        item_scores.append(int(item_match.group(2)))
    if not item_scores:
        raise ValueError("evaluator response must contain at least one item score")
    if expected_item_count is not None:
        if (
            isinstance(expected_item_count, bool)
            or not isinstance(expected_item_count, int)
            or expected_item_count <= 0
        ):
            raise ValueError("expected_item_count must be a positive integer")
        if len(item_scores) != expected_item_count:
            raise ValueError("evaluator item count differs from the frozen plan")
    total = int(match.group(1))
    if total != sum(item_scores):
        raise ValueError("Total Score must equal the sum of evaluator item scores")
    return total


def build_planner_prompt(question: str) -> str:
    """Build the frozen concise planner adaptation for one question."""

    clean_question = _validate_prompt_text(question, label="question")
    return (
        "You are a question-decomposition planner. Break the question into the "
        "smallest ordered list of subquestions needed to answer it. Do not answer "
        "the question and do not add optional background. Return only a PLAN with "
        "one numbered subquestion per line.\n\n"
        f"QUESTION_JSON: {json.dumps(clean_question, ensure_ascii=False)}\n\n"
        "PLAN:"
    )


def build_evaluator_prompt(
    *,
    question: str,
    plan: str,
    passages: Sequence[PromptPassage],
) -> str:
    """Build the frozen concise support evaluator adaptation."""

    clean_question = _validate_prompt_text(question, label="question")
    clean_plan = _validate_prompt_text(plan, label="plan")
    if not passages:
        raise ValueError("at least one evaluator passage is required")
    passage_rows = []
    seen_ids: set[str] = set()
    for passage in passages:
        if not isinstance(passage, PromptPassage):
            raise ValueError("passages must be PromptPassage values")
        passage_id = _validate_identifier(passage.passage_id, label="passage_id")
        if passage_id in seen_ids:
            raise ValueError(f"duplicate evaluator passage id: {passage_id}")
        seen_ids.add(passage_id)
        passage_rows.append(
            {
                "passage_id": passage_id,
                "text": _validate_prompt_text(passage.text, label="passage text"),
            }
        )

    return (
        "You are a strict evidence-support judge. Using explicit information in "
        "the passages only, score how completely the passages support each PLAN "
        "item from 0 (no support) to 5 (complete support). Sum the item scores. "
        "Give one short line per item and finish with exactly one line in the form "
        "'Total Score: N'. Do not infer missing facts.\n\n"
        f"QUESTION_JSON: {json.dumps(clean_question, ensure_ascii=False)}\n"
        f"PLAN_JSON: {json.dumps(clean_plan, ensure_ascii=False)}\n"
        "PASSAGES_JSON: "
        f"{json.dumps(passage_rows, ensure_ascii=False, sort_keys=True)}"
    )


def build_generator_prompt(
    *,
    question: str,
    passages: Sequence[PromptPassage],
) -> str:
    """Build the frozen citation-aware generator adaptation.

    The DF-RAG paper's generator returns answer text only.  E052 additionally
    needs auditable answer-to-passage links for citation evaluation, so this
    prompt is explicitly a local output-contract adaptation rather than a
    verbatim reproduction of the paper's LongBench prompt.
    """

    clean_question = _validate_prompt_text(question, label="question")
    if not passages:
        raise ValueError("at least one generator passage is required")
    passage_rows: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for passage in passages:
        if not isinstance(passage, PromptPassage):
            raise ValueError("passages must be PromptPassage values")
        passage_id = _validate_identifier(passage.passage_id, label="passage_id")
        if passage_id in seen_ids:
            raise ValueError(f"duplicate generator passage id: {passage_id}")
        seen_ids.add(passage_id)
        passage_rows.append(
            {
                "evidence_id": passage_id,
                "text": _validate_prompt_text(passage.text, label="passage text"),
            }
        )
    return (
        "Answer the question using only the supplied passages. Return exactly one "
        "JSON object with keys 'answer' and 'citations'. 'answer' must be a concise "
        "nonempty string. 'citations' must be a nonempty array of objects with "
        "exactly 'evidence_id' and 'answer_span'. Every evidence_id must come from "
        "the passages. Every answer_span must be an exact nonempty substring of the "
        "answer that the cited passage supports. Do not use Markdown or add keys.\n\n"
        f"QUESTION_JSON: {json.dumps(clean_question, ensure_ascii=False)}\n"
        "PASSAGES_JSON: "
        f"{json.dumps(passage_rows, ensure_ascii=False, sort_keys=True)}"
    )


def parse_generator_response(
    response: str,
    *,
    allowed_evidence_ids: Sequence[str],
) -> GeneratedAnswer:
    """Parse an exact citation-aware generator JSON object without repair."""

    if not isinstance(response, str) or not response.strip():
        raise ValueError("generator response is empty")
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as exc:
        raise ValueError("generator response is not exactly one JSON value") from exc
    if not isinstance(payload, dict) or set(payload) != {"answer", "citations"}:
        raise ValueError("generator response must have exactly answer and citations")
    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip() or answer != answer.strip():
        raise ValueError("generator answer must be a nonempty trimmed string")
    allowed = tuple(
        _validate_identifier(value, label="allowed evidence id") for value in allowed_evidence_ids
    )
    if len(set(allowed)) != len(allowed):
        raise ValueError("allowed evidence identifiers must be unique")
    citations_payload = payload.get("citations")
    if not isinstance(citations_payload, list) or not citations_payload:
        raise ValueError("generator citations must be a nonempty array")
    citations: list[GeneratedCitation] = []
    seen_links: set[tuple[str, str]] = set()
    for index, row in enumerate(citations_payload):
        if not isinstance(row, dict) or set(row) != {"evidence_id", "answer_span"}:
            raise ValueError(
                f"generator citation {index} must have exactly evidence_id and answer_span"
            )
        evidence_id = _validate_identifier(
            row.get("evidence_id"), label="generator citation evidence_id"
        )
        if evidence_id not in allowed:
            raise ValueError("generator citation names evidence outside the selected set")
        answer_span = row.get("answer_span")
        if (
            not isinstance(answer_span, str)
            or not answer_span.strip()
            or answer_span != answer_span.strip()
            or answer_span not in answer
        ):
            raise ValueError("generator citation answer_span is not an exact answer substring")
        link = (evidence_id, answer_span)
        if link in seen_links:
            raise ValueError("generator response contains a duplicate citation link")
        seen_links.add(link)
        citations.append(GeneratedCitation(evidence_id=evidence_id, answer_span=answer_span))
    return GeneratedAnswer(answer=answer, citations=tuple(citations))


def _validate_vector_problem(
    query_vector: Sequence[float],
    candidates: Sequence[EmbeddedChunk],
) -> tuple[tuple[float, ...], tuple[EmbeddedChunk, ...]]:
    query = _coerce_vector(query_vector, label="query vector")
    if not candidates:
        raise ValueError("at least one candidate chunk is required")

    rows: list[EmbeddedChunk] = []
    seen_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, EmbeddedChunk):
            raise ValueError("candidates must be EmbeddedChunk values")
        chunk_id = _validate_identifier(candidate.chunk_id, label="chunk_id")
        if chunk_id in seen_ids:
            raise ValueError(f"duplicate candidate chunk id: {chunk_id}")
        vector = _coerce_vector(candidate.vector, label=f"vector for {chunk_id}")
        if len(vector) != len(query):
            raise ValueError(
                f"vector dimension mismatch for {chunk_id}: "
                f"expected {len(query)}, got {len(vector)}"
            )
        seen_ids.add(chunk_id)
        rows.append(EmbeddedChunk(chunk_id=chunk_id, text=candidate.text, vector=vector))
    return query, tuple(rows)


def _coerce_vector(vector: Sequence[float], *, label: str) -> tuple[float, ...]:
    if isinstance(vector, (str, bytes)):
        raise ValueError(f"{label} must be a numeric sequence")
    try:
        raw_values = tuple(vector)
    except TypeError as exc:
        raise ValueError(f"{label} must be a numeric sequence") from exc
    if not raw_values:
        raise ValueError(f"{label} must not be empty")

    values: list[float] = []
    for value in raw_values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{label} contains a non-numeric value")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{label} contains a non-finite value")
        values.append(number)
    result = tuple(values)
    _validate_nonzero_vector(result, label=label)
    return result


def _validate_nonzero_vector(vector: Sequence[float], *, label: str) -> None:
    squared_norm = math.fsum(value * value for value in vector)
    if not math.isfinite(squared_norm) or squared_norm <= 0.0:
        raise ValueError(f"{label} must have a finite non-zero norm")


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("cosine vectors must have equal dimensions")
    left_norm_squared = math.fsum(value * value for value in left)
    right_norm_squared = math.fsum(value * value for value in right)
    if left_norm_squared <= 0.0 or right_norm_squared <= 0.0:
        raise ValueError("cosine vectors must have non-zero norms")
    denominator = math.sqrt(left_norm_squared) * math.sqrt(right_norm_squared)
    cosine = math.fsum(a * b for a, b in zip(left, right, strict=True)) / denominator
    if not math.isfinite(cosine):
        raise ValueError("cosine similarity is non-finite")
    return min(1.0, max(-1.0, cosine))


def _validate_lambda(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("lambda must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise ValueError("lambda must be finite and within [0, 1]")
    return number


def _validate_lambda_sequence(values: Sequence[float]) -> tuple[float, ...]:
    if not values:
        raise ValueError("lambda sequence must not be empty")
    normalized = tuple(_validate_lambda(value) for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError("lambda sequence contains duplicates")
    return normalized


def _validate_identifier(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{label} must not have surrounding whitespace")
    return value


def _validate_prompt_text(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()
