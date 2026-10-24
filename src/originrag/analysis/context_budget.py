"""Pure, model-independent accounting for an answer's available context.

The estimates are deliberately conservative heuristics, not tokenizer counts.
Callers must budget the effective runtime context and check the fully rendered
initial and repair prompts; this module never changes a model or trims evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re


def estimate_prompt_tokens(text: str) -> int:
    """Estimate prompt cost, including Unicode and long unbroken metadata.

    The regex estimate matches ``trace.runtime.estimate_tokens`` without making
    this small utility depend on the optional TRACE runtime dependencies. The
    byte-based estimate prevents long identifiers and Unicode text from being
    treated as single-token words.
    """
    if not isinstance(text, str):
        raise TypeError("prompt text must be a string")
    byte_estimate = (len(text.encode("utf-8")) + 2) // 3
    trace_estimate = max(1, int(len(re.findall(r"\w+|[^\w\s]", text)) * 1.15))
    return max(byte_estimate, trace_estimate)


@dataclass(frozen=True)
class AnswerContextBudget:
    evidence_token_budget: int
    prompt_evidence_capacity: int
    context_window_tokens: int
    output_reserve_tokens: int
    base_prompt_estimated_tokens: int
    repair_reserve_tokens: int
    safety_margin_tokens: int
    requested_evidence_tokens: int | None

    def summary(self) -> dict[str, int | None]:
        """Return independent, JSON-serializable accounting metadata."""
        return asdict(self)

    def fits_prompt(self, prompt: str) -> bool:
        """Check a fully rendered prompt without double-counting its reserves.

        Base instructions and any repair text are already in ``prompt``; only
        the model's output and safety margin remain to be reserved here.
        """
        return (
            estimate_prompt_tokens(prompt)
            + self.output_reserve_tokens
            + self.safety_margin_tokens
            <= self.context_window_tokens
        )

    def require_prompt_fit(self, prompt: str) -> None:
        """Reject an oversized prompt without modifying evidence or context."""
        estimated_tokens = estimate_prompt_tokens(prompt)
        required_tokens = (
            estimated_tokens + self.output_reserve_tokens + self.safety_margin_tokens
        )
        if required_tokens > self.context_window_tokens:
            raise ValueError(
                "Answer prompt exceeds the context budget: "
                f"{estimated_tokens} estimated prompt tokens + "
                f"{self.output_reserve_tokens} output tokens + "
                f"{self.safety_margin_tokens} safety tokens > "
                f"{self.context_window_tokens} context tokens. "
                "No evidence was removed."
            )


def context_capacity(
    *,
    context_window_tokens: int,
    output_tokens: int,
    base_prompt: str,
    requested_evidence_tokens: int | None = None,
    repair_reserve_tokens: int = 0,
    automatic_evidence_ceiling_tokens: int = 8000,
    safety_margin_tokens: int = 1024,
) -> AnswerContextBudget:
    """Allocate evidence space after reserving instructions and generation.

    ``base_prompt`` must include the question, history, profile, and other known
    non-evidence instructions. The safety margin reserves dynamic metadata and
    estimator uncertainty. A repair reserve can additionally hold room for a
    previous answer and repair instructions. Explicit evidence requests are
    clamped to available context, but not to the automatic-mode ceiling.
    """
    _validate_integer("context_window_tokens", context_window_tokens, minimum=1)
    _validate_integer("output_tokens", output_tokens, minimum=1)
    _validate_integer("repair_reserve_tokens", repair_reserve_tokens, minimum=0)
    _validate_integer("safety_margin_tokens", safety_margin_tokens, minimum=0)
    _validate_integer(
        "automatic_evidence_ceiling_tokens", automatic_evidence_ceiling_tokens, minimum=1
    )
    if requested_evidence_tokens is not None:
        _validate_integer("requested_evidence_tokens", requested_evidence_tokens, minimum=1)

    base_prompt_tokens = estimate_prompt_tokens(base_prompt)
    available_tokens = (
        context_window_tokens
        - output_tokens
        - base_prompt_tokens
        - repair_reserve_tokens
        - safety_margin_tokens
    )
    if available_tokens <= 0:
        raise ValueError(
            "Insufficient answer context for evidence after reserving "
            f"{base_prompt_tokens} base-prompt tokens, {output_tokens} output tokens, "
            f"{repair_reserve_tokens} repair tokens, and {safety_margin_tokens} safety "
            f"tokens within {context_window_tokens} context tokens."
        )
    desired_tokens = (
        automatic_evidence_ceiling_tokens
        if requested_evidence_tokens is None
        else requested_evidence_tokens
    )
    return AnswerContextBudget(
        evidence_token_budget=min(desired_tokens, available_tokens),
        prompt_evidence_capacity=available_tokens,
        context_window_tokens=context_window_tokens,
        output_reserve_tokens=output_tokens,
        base_prompt_estimated_tokens=base_prompt_tokens,
        repair_reserve_tokens=repair_reserve_tokens,
        safety_margin_tokens=safety_margin_tokens,
        requested_evidence_tokens=requested_evidence_tokens,
    )


def _validate_integer(name: str, value: int, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "positive" if minimum else "nonnegative"
        raise ValueError(f"{name} must be a {qualifier} integer")
