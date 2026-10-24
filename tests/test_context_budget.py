from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
import json

import pytest

from originrag.analysis.context_budget import context_capacity, estimate_prompt_tokens
from originrag.trace.runtime import estimate_tokens


@pytest.mark.parametrize(
    "text",
    [
        "",
        "A short factual answer.",
        "A" * 300,
        ". " * 300,
        "résumé — 東京 — 🙂" * 100,
        "LongMetadataIdentifier" * 100,
    ],
)
def test_prompt_estimate_covers_utf8_bytes_and_trace_estimate(text):
    expected = max((len(text.encode("utf-8")) + 2) // 3, estimate_tokens(text))
    assert estimate_prompt_tokens(text) == expected
    assert estimate_prompt_tokens(text) == estimate_prompt_tokens(text)


def test_unicode_and_unbroken_metadata_are_not_single_token_words():
    assert estimate_prompt_tokens("界" * 100) == 100
    assert estimate_prompt_tokens("🙂" * 100) >= 134
    assert estimate_prompt_tokens("Identifier" * 100) >= 334


@pytest.mark.parametrize("invalid", [None, 42, [], {}])
def test_estimator_rejects_non_text(invalid):
    with pytest.raises(TypeError, match="must be a string"):
        estimate_prompt_tokens(invalid)


def test_automatic_budget_reserves_context_without_changing_model_window():
    budget = context_capacity(
        context_window_tokens=16384,
        output_tokens=2200,
        base_prompt="X" * 6000,
        repair_reserve_tokens=1200,
    )
    assert budget.prompt_evidence_capacity == 9960
    assert budget.evidence_token_budget == 8000
    assert budget.context_window_tokens == 16384
    assert budget.requested_evidence_tokens is None
    assert budget.base_prompt_estimated_tokens == 2000
    assert budget.summary() == asdict(budget)
    assert json.loads(json.dumps(budget.summary())) == budget.summary()
    with pytest.raises(FrozenInstanceError):
        budget.context_window_tokens = 32768


@pytest.mark.parametrize(
    "requested, expected", [(None, 8000), (2000, 2000), (9000, 9000), (20000, 9960)]
)
def test_explicit_budget_is_only_clamped_to_remaining_capacity(requested, expected):
    budget = context_capacity(
        context_window_tokens=16384,
        output_tokens=2200,
        base_prompt="X" * 6000,
        repair_reserve_tokens=1200,
        requested_evidence_tokens=requested,
    )
    assert budget.evidence_token_budget == expected
    assert budget.requested_evidence_tokens == requested


def test_auto_budget_respects_custom_ceiling_and_small_positive_capacity():
    budget = context_capacity(
        context_window_tokens=4096,
        output_tokens=1000,
        base_prompt="X" * 3000,
        repair_reserve_tokens=1000,
        automatic_evidence_ceiling_tokens=500,
    )
    assert budget.prompt_evidence_capacity == 72
    assert budget.evidence_token_budget == 72
    assert context_capacity(
        context_window_tokens=16384,
        output_tokens=2200,
        base_prompt="Instructions",
        automatic_evidence_ceiling_tokens=500,
    ).evidence_token_budget == 500


def test_history_profile_and_metadata_consume_base_prompt_capacity():
    basic = context_capacity(
        context_window_tokens=16384, output_tokens=2200, base_prompt="Question"
    )
    expanded_base = (
        "Question\nHistory: " + "PriorTurn" * 1000
        + "\nProfile: " + "DetailedPreference" * 1000
        + "\nTitle: " + "Identifier" * 100
    )
    expanded = context_capacity(
        context_window_tokens=16384, output_tokens=2200, base_prompt=expanded_base
    )
    assert expanded.prompt_evidence_capacity < basic.prompt_evidence_capacity
    assert expanded.evidence_token_budget < basic.evidence_token_budget
    assert expanded.context_window_tokens == basic.context_window_tokens


def test_additional_output_and_repair_reserves_reduce_capacity_exactly():
    basic = context_capacity(
        context_window_tokens=16384, output_tokens=2200, base_prompt="Question"
    )
    reserved = context_capacity(
        context_window_tokens=16384,
        output_tokens=3200,
        base_prompt="Question",
        repair_reserve_tokens=2000,
    )
    assert reserved.prompt_evidence_capacity == basic.prompt_evidence_capacity - 3000


@pytest.mark.parametrize("context_window", [1, 4000, 4024])
def test_insufficient_context_fails_closed(context_window):
    with pytest.raises(ValueError, match="Insufficient answer context"):
        context_capacity(
            context_window_tokens=context_window,
            output_tokens=1000,
            base_prompt="X" * 3000,
            repair_reserve_tokens=1000,
        )


@pytest.mark.parametrize(
    "field",
    [
        "context_window_tokens",
        "output_tokens",
        "automatic_evidence_ceiling_tokens",
        "requested_evidence_tokens",
    ],
)
@pytest.mark.parametrize("invalid", [0, -1, True, False, 1.5, "1000"])
def test_positive_budget_fields_require_positive_integers(field, invalid):
    options = {
        "context_window_tokens": 16384,
        "output_tokens": 2200,
        "base_prompt": "Question",
        field: invalid,
    }
    with pytest.raises(ValueError, match=field + " must be a positive integer"):
        context_capacity(**options)


@pytest.mark.parametrize("field", ["repair_reserve_tokens", "safety_margin_tokens"])
@pytest.mark.parametrize("invalid", [-1, True, False, 1.5, "1000", None])
def test_optional_reserves_require_nonnegative_integers(field, invalid):
    options = {
        "context_window_tokens": 16384,
        "output_tokens": 2200,
        "base_prompt": "Question",
        field: invalid,
    }
    with pytest.raises(ValueError, match=field + " must be a nonnegative integer"):
        context_capacity(**options)


def test_zero_reserves_are_allowed():
    budget = context_capacity(
        context_window_tokens=3,
        output_tokens=1,
        base_prompt="",
        repair_reserve_tokens=0,
        safety_margin_tokens=0,
    )
    assert budget.evidence_token_budget == 1


def test_fully_rendered_prompt_fit_does_not_double_count_base_or_repair():
    budget = context_capacity(
        context_window_tokens=10000,
        output_tokens=2000,
        base_prompt="X" * 3000,
        repair_reserve_tokens=2000,
        safety_margin_tokens=1000,
    )
    assert budget.prompt_evidence_capacity == 4000
    full_prompt = "X" * 21000
    assert estimate_prompt_tokens(full_prompt) == 7000
    assert budget.fits_prompt(full_prompt)
    assert budget.require_prompt_fit(full_prompt) is None
    assert not budget.fits_prompt(full_prompt + "X")
    with pytest.raises(ValueError, match="No evidence was removed"):
        budget.require_prompt_fit(full_prompt + "X")
    assert budget.context_window_tokens == 10000
    assert full_prompt == "X" * 21000


def test_summary_mutation_does_not_change_accounting():
    options = {
        "context_window_tokens": 16384,
        "output_tokens": 2200,
        "base_prompt": "Question",
    }
    budget = context_capacity(**options)
    summary = budget.summary()
    summary["context_window_tokens"] = 1
    assert budget.context_window_tokens == 16384
    assert budget == context_capacity(**options)
