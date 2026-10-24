from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from originrag.analysis.context_budget import context_capacity
from originrag.analysis.evidence_pack import PROMPT_VISIBLE_TEXT_FROZEN
from originrag.model_runtime import COLIBRI_MODELS, CURRENT_RUNTIME


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ask_corpus  # noqa: E402


QUESTION = "What evidence describes query latency in database benchmarks?"


@pytest.fixture(autouse=True)
def isolated_answer_runtime(monkeypatch):
    token = CURRENT_RUNTIME.set(None)
    monkeypatch.setenv("PFEMPIRE_OLLAMA_NUM_CTX", "65536")
    monkeypatch.setenv("PFEMPIRE_OLLAMA_NUM_PREDICT", "1024")
    try:
        yield
    finally:
        CURRENT_RUNTIME.reset(token)


def planner_args(**overrides):
    return SimpleNamespace(**{
        "trace_token_budget": None,
        "repair_attempts": 0,
        "conversation_context": None,
        "profile_instruction": "",
        **overrides,
    })


def candidate(index, **overrides):
    return {
        "chunk_id": f"C{index:03d}",
        "paper_id": f"P{index:03d}",
        "title": f"Database benchmark {index:03d}",
        "author": f"Analyst {index:03d}",
        "year": "2025",
        "doi": f"10.1000/benchmark.{index:03d}",
        "section": "results",
        "score": 100.0 - index,
        "text": (
            f"Database benchmark {index:03d} measured query latency. "
            "Caching reduced median latency in repeated query measurements."
        ),
        "quality_flags": [],
        **overrides,
    }


def select(candidates, budget, **options):
    return ask_corpus.budget_answer_candidates(
        candidates,
        question=QUESTION,
        max_chars_per_chunk=1800,
        budget=budget,
        exploration_multiplier=1,
        **options,
    )


def test_ample_context_keeps_more_than_twelve_ranked_distinct_papers():
    rows = [candidate(index) for index in range(1, 18)]
    original = deepcopy(rows)
    budget = ask_corpus.plan_answer_context(planner_args(), QUESTION)

    selected, metadata = select(rows, budget)

    assert [row["paper_id"] for row in selected] == [row["paper_id"] for row in rows]
    assert metadata["shortlist_paper_count"] == 17
    assert metadata["complete_candidate_pool_included"] is True
    assert metadata["omitted_by_reason"] == {}
    assert metadata["evidence_estimated_tokens"] <= budget.evidence_token_budget
    assert metadata["serialized_evidence_estimated_tokens"] <= budget.prompt_evidence_capacity
    assert rows == original
    assert all(row[PROMPT_VISIBLE_TEXT_FROZEN] for row in selected)


def test_explicit_ceiling_retains_ranked_prefix_and_reports_omissions():
    rows = [candidate(index) for index in range(1, 8)]
    per_row = ask_corpus.estimate_tokens(rows[0]["text"])
    assert {ask_corpus.estimate_tokens(row["text"]) for row in rows} == {per_row}
    budget = ask_corpus.plan_answer_context(
        planner_args(trace_token_budget=per_row * 3), QUESTION
    )

    selected, metadata = select(rows, budget)

    assert budget.requested_evidence_tokens == per_row * 3
    assert budget.evidence_token_budget == per_row * 3
    assert [row["chunk_id"] for row in selected] == ["C001", "C002", "C003"]
    assert metadata["evidence_estimated_tokens"] == per_row * 3
    assert metadata["omitted_by_reason"] == {"evidence_budget": 4}
    assert metadata["complete_candidate_pool_included"] is False


def test_default_exploration_keeps_later_candidate_beyond_final_pack_capacity():
    rows = [candidate(index) for index in range(1, 11)]
    cost = ask_corpus.estimate_tokens(rows[0]["text"])
    budget = ask_corpus.plan_answer_context(
        planner_args(trace_token_budget=cost * 3), QUESTION
    )

    selected, metadata = ask_corpus.budget_answer_candidates(
        rows, question=QUESTION, max_chars_per_chunk=1800, budget=budget
    )

    assert [row["chunk_id"] for row in selected] == [f"C{index:03d}" for index in range(1, 10)]
    assert selected[-1]["chunk_id"] == "C009"
    assert metadata["exploration_multiplier"] == 3
    assert metadata["evidence_estimated_tokens"] == budget.evidence_token_budget * 3
    assert metadata["serialized_evidence_estimated_tokens"] <= budget.prompt_evidence_capacity * 3
    assert metadata["exploration_evidence_token_budget"] == budget.evidence_token_budget * 3
    assert metadata["exploration_prompt_token_budget"] == budget.prompt_evidence_capacity * 3
    assert sum(row["_answer_prompt_token_cost"] for row in selected) == metadata["serialized_evidence_estimated_tokens"]


@pytest.mark.parametrize("rich_field, rich_value", [
    ("title", "LongUnbrokenMetadataIdentifier" * 150),
    ("title", "資料測定🙂" * 200),
    ("text", "界" * 1600),
])
def test_serialized_metadata_and_unicode_cost_can_exclude_a_short_text_candidate(
    rich_field, rich_value,
):
    plain = candidate(2)
    rich = candidate(1, **{rich_field: rich_value, PROMPT_VISIBLE_TEXT_FROZEN: True})
    ample = ask_corpus.plan_answer_context(planner_args(), QUESTION)
    _, plain_metadata = select([plain], ample)
    _, rich_metadata = select([rich], ample)
    assert rich_metadata["serialized_evidence_estimated_tokens"] > plain_metadata["serialized_evidence_estimated_tokens"]
    budget = replace(
        ample, prompt_evidence_capacity=plain_metadata["serialized_evidence_estimated_tokens"]
    )

    selected, metadata = select([rich, plain], budget)

    assert [row["chunk_id"] for row in selected] == ["C002"]
    assert metadata["omitted_by_reason"] == {"prompt_budget": 1}
    assert rich[rich_field] == rich_value


def test_missing_identity_duplicates_and_empty_text_are_audited():
    first = candidate(1)
    location_identity = candidate(4, chunk_id="", source_file="benchmarks.jsonl", line_number=4)
    rows = [
        first,
        dict(first),
        candidate(2, chunk_id="", source_file="", line_number=None),
        candidate(3, text=" "),
        location_identity,
        candidate(5, paper_id=""),
    ]
    budget = ask_corpus.plan_answer_context(planner_args(), QUESTION)

    selected, metadata = select(rows, budget)

    assert [row["chunk_id"] for row in selected] == ["C001"]
    assert metadata["omitted_by_reason"] == {
        "duplicate_or_missing_identity": 4, "empty_text": 1
    }
    assert metadata["shortlist_paper_count"] == 1
    assert select([], budget)[0] == []


def test_primary_papers_are_budgeted_before_named_support_passages():
    rows = [
        candidate(4, paper_id="P001", explicit_named_study_support=True),
        candidate(1, explicit_named_study_anchor=True),
        candidate(2),
        candidate(3),
    ]
    cost = ask_corpus.estimate_tokens(rows[1]["text"])
    budget = ask_corpus.plan_answer_context(
        planner_args(trace_token_budget=cost * 3), QUESTION
    )

    selected, metadata = select(rows, budget)

    assert [row["chunk_id"] for row in selected] == ["C001", "C002", "C003"]
    assert metadata["omitted_by_reason"] == {"evidence_budget": 1}


def test_budget_does_not_silently_clip_already_frozen_evidence():
    text = "Caching reduced database query latency in repeated benchmark runs. " * 40
    row = candidate(1, text=text, **{PROMPT_VISIBLE_TEXT_FROZEN: True})
    ample = ask_corpus.plan_answer_context(planner_args(), QUESTION)
    selected, _ = ask_corpus.budget_answer_candidates(
        [row], question=QUESTION, max_chars_per_chunk=32,
        budget=ample, exploration_multiplier=1,
    )
    assert selected[0]["text"] == text
    too_small = replace(ample, evidence_token_budget=ask_corpus.estimate_tokens(text) - 1)

    excluded, metadata = select([row], too_small)

    assert excluded == []
    assert metadata["omitted_by_reason"] == {"evidence_budget": 1}
    assert row["text"] == text


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "10"])
def test_invalid_candidate_safety_limits_fail_closed(invalid):
    budget = ask_corpus.plan_answer_context(planner_args(), QUESTION)
    with pytest.raises(ValueError, match="max_candidates must be a positive integer"):
        select([candidate(1)], budget, max_candidates=invalid)


def test_candidate_safety_limit_is_explicit_and_audited():
    rows = [candidate(index) for index in range(1, 5)]
    budget = ask_corpus.plan_answer_context(planner_args(), QUESTION)
    selected, metadata = select(rows, budget, max_candidates=2)
    assert [row["chunk_id"] for row in selected] == ["C001", "C002"]
    assert metadata["omitted_by_reason"] == {"candidate_safety_limit": 2}
    assert metadata["candidate_safety_limit"] == 2


@pytest.mark.parametrize("invalid", [0, -1, 11, True, 1.5, "3"])
def test_invalid_exploration_multiplier_fails_closed(invalid):
    budget = ask_corpus.plan_answer_context(planner_args(), QUESTION)
    with pytest.raises(ValueError, match="exploration_multiplier must be an integer between 1 and 10"):
        ask_corpus.budget_answer_candidates(
            [candidate(1)], question=QUESTION, max_chars_per_chunk=1800,
            budget=budget, exploration_multiplier=invalid,
        )


def test_plan_uses_selected_colibri_context_instead_of_ollama_environment(monkeypatch):
    monkeypatch.setenv("PFEMPIRE_OLLAMA_NUM_CTX", "65536")
    model = "qwen3-coder-30b"
    CURRENT_RUNTIME.set({"provider": "colibri", "model": model})

    budget = ask_corpus.plan_answer_context(planner_args(), QUESTION)

    assert budget.context_window_tokens == COLIBRI_MODELS[model]["context"] == 16384
    assert budget.evidence_token_budget <= budget.prompt_evidence_capacity


def test_plan_uses_ollama_override_and_reserves_repair_and_profile(monkeypatch):
    monkeypatch.setenv("PFEMPIRE_OLLAMA_NUM_CTX", "16384")
    CURRENT_RUNTIME.set({"provider": "ollama", "model": "test-model"})
    plain = ask_corpus.plan_answer_context(planner_args(), QUESTION)
    repaired = ask_corpus.plan_answer_context(planner_args(repair_attempts=2), QUESTION)
    profiled = ask_corpus.plan_answer_context(
        planner_args(profile_instruction="Explain database measurements carefully. " * 100),
        QUESTION,
    )

    assert plain.context_window_tokens == 16384
    assert repaired.repair_reserve_tokens == repaired.output_reserve_tokens == 1024
    assert repaired.prompt_evidence_capacity == plain.prompt_evidence_capacity - 1024
    assert profiled.prompt_evidence_capacity < plain.prompt_evidence_capacity


@pytest.mark.parametrize("requested_context, expected_context", [(8192, 16384), (32768, 32768)])
def test_plan_resolves_selected_runner_floor_without_changing_runtime(
    monkeypatch, requested_context, expected_context,
):
    endpoint = "http://127.0.0.1:11436"
    selected = {"provider": "ollama", "model": "selected-benchmark-model", "base_url": endpoint}
    CURRENT_RUNTIME.set(selected)
    monkeypatch.setenv("PFEMPIRE_OLLAMA_NUM_CTX", str(requested_context))
    monkeypatch.setenv("ORIGINRAG_OLLAMA_RUNNER_CONTEXTS", json.dumps({
        endpoint: {"selected-benchmark-model": 16384, "caller-model": 65536},
    }))

    budget = ask_corpus.plan_answer_context(
        planner_args(backend="qwen", answer_model="caller-model"), QUESTION
    )

    assert budget.context_window_tokens == expected_context
    assert CURRENT_RUNTIME.get() is selected
    assert CURRENT_RUNTIME.get() == {
        "provider": "ollama", "model": "selected-benchmark-model", "base_url": endpoint,
    }
    assert ask_corpus.answer_num_ctx() == requested_context


def test_whitespace_id_normalization_keeps_runtime_cost_mapping_complete(tmp_path):
    rows = [
        candidate(1, chunk_id=" C001 \t", paper_id=" P001 "),
        candidate(2, chunk_id="C001", paper_id="P001"),
        candidate(3, chunk_id=" C003 ", paper_id="\tP003 "),
    ]
    original = deepcopy(rows)
    budget = ask_corpus.plan_answer_context(planner_args(), QUESTION)
    selected, metadata = select(rows, budget)
    costs = {row["chunk_id"]: row["_answer_prompt_token_cost"] for row in selected}

    result = ask_corpus.run_trace_selection(
        QUESTION, "evidence_qa", selected, tmp_path,
        selector="topk", config_path=REPO_ROOT / "config/trace_rag.yaml",
        token_budget=budget.evidence_token_budget,
        prompt_token_budget=budget.prompt_evidence_capacity,
        prompt_token_costs=costs,
    )

    assert [(row["chunk_id"], row["paper_id"]) for row in selected] == [
        ("C001", "P001"), ("C003", "P003"),
    ]
    assert metadata["omitted_by_reason"] == {"duplicate_or_missing_identity": 1}
    assert [row["chunk_id"] for row in result["selected_results"]] == ["C001", "C003"]
    assert result["selection"].run_config["prompt_token_cost"] == sum(costs.values())
    assert rows == original


def test_nominal_16k_metadata_heavy_selection_fits_and_keeps_full_certificate(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("PFEMPIRE_OLLAMA_NUM_CTX", "16384")
    monkeypatch.setenv("PFEMPIRE_OLLAMA_NUM_PREDICT", "2200")
    budget = ask_corpus.plan_answer_context(planner_args(repair_attempts=2), QUESTION)
    rows = [
        candidate(
            index,
            title=f"Benchmark {index}: " + "LongMetadataIdentifier" * 120,
            text=("The benchmark measured query latency across repeated measurements. " * 20)[:950],
        )
        for index in range(1, 26)
    ]
    explored, metadata = ask_corpus.budget_answer_candidates(
        rows, question=QUESTION, max_chars_per_chunk=950, budget=budget,
    )
    result = ask_corpus.run_trace_selection(
        QUESTION, "evidence_qa", explored, tmp_path,
        selector="topk", config_path=REPO_ROOT / "config/trace_rag.yaml",
        token_budget=budget.evidence_token_budget,
        prompt_token_budget=budget.prompt_evidence_capacity,
        prompt_token_costs={row["chunk_id"]: row["_answer_prompt_token_cost"] for row in explored},
    )
    selected = result["selected_results"]
    pack = ask_corpus.build_evidence_pack(
        QUESTION, selected, max_chunks=len(selected), max_chars_per_chunk=950,
    )
    pack.update({
        "trace_sufficiency_certificate": result["certificate"].model_dump(mode="json"),
        "trace_robust_lineage": result["robust_lineage"].model_dump(mode="json"),
        "trace_certifying_evidence": result["selection"].run_config["certifying_evidence"],
    })
    ask_corpus.apply_certification_core_roles(pack)
    prompt = ask_corpus.build_citation_answer_prompt(QUESTION, pack)

    assert len(explored) > 12
    assert 0 < len(selected) < len(explored)
    assert metadata["serialized_evidence_estimated_tokens"] > budget.prompt_evidence_capacity
    assert result["selection"].run_config["prompt_token_cost"] <= budget.prompt_evidence_capacity
    assert budget.fits_prompt(prompt)
    assert result["certifying_evidence"] == result["selection"].selected_candidates
    assert set(pack["trace_certifying_evidence"]["paper_ids"]) == {
        row["paper_id"] for row in selected
    }
    assert all(chunk["certification_role"] == "certifying" for chunk in pack["chunks"])
    assert result["robust_lineage"].status != "robustly_sufficient"
    assert all(chunk["text"] in prompt for chunk in pack["chunks"])


def test_insufficient_runtime_context_fails_before_selection(monkeypatch):
    monkeypatch.setenv("PFEMPIRE_OLLAMA_NUM_CTX", "2048")
    with pytest.raises(ValueError, match="Insufficient answer context"):
        ask_corpus.plan_answer_context(planner_args(repair_attempts=1), QUESTION)


def test_rendered_prompt_overflow_is_rejected_without_removing_evidence():
    rows = [candidate(1), candidate(2)]
    pack = ask_corpus.build_evidence_pack(QUESTION, rows, max_chunks=len(rows))
    original = deepcopy(pack)
    budget = context_capacity(
        context_window_tokens=6000, output_tokens=1024, base_prompt="Instructions"
    )
    prompt = ask_corpus.build_citation_answer_prompt(
        QUESTION, pack, task="answer", profile_instruction="LongPreference" * 3000
    )

    with pytest.raises(ValueError, match="No evidence was removed"):
        budget.require_prompt_fit(prompt)

    assert pack == original
    assert all(chunk["text"] in prompt for chunk in pack["chunks"])


@pytest.mark.parametrize("selector", ["topk", "trace_flow"])
def test_frozen_pool_run_retains_broad_context_and_certifies_the_full_selected_set(
    tmp_path, monkeypatch, selector,
):
    rows = [candidate(index) for index in range(1, 18)]
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tpaper_type\tfulltext_status\n" + "".join(
            f"{row['paper_id']}\t{row['title']}\t2025\t{row['doi']}\tarticle\tchunked\n"
            for row in rows
        ), encoding="utf-8",
    )
    for row in rows:
        (chunks_dir / f"{row['paper_id']}.jsonl").write_text(
            json.dumps(row) + "\n", encoding="utf-8"
        )
    pool = tmp_path / "candidates.jsonl"
    pool.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "ask_corpus", QUESTION,
        "--chunks-dir", str(chunks_dir), "--papers", str(papers),
        "--candidate-pool", str(pool), "--backend", "none",
        "--evidence-selector", selector, "--limit", "4",
        "--repair-attempts", "0", "--no-understand-question", "--no-template-fallback",
    ])
    args = ask_corpus.parse_args()
    assert args.trace_token_budget is None
    output = tmp_path / "run"

    ask_corpus.run_question(args, output)

    pack = json.loads((output / "evidence_pack.json").read_text())
    config = json.loads((output / "run_config.json").read_text())
    selection = json.loads((output / "trace_selection_result.json").read_text())
    prompt = (output / "answer_prompt.md").read_text()
    selected_ids = {chunk["paper_id"] for chunk in pack["chunks"]}
    assert len(selected_ids) > 12
    assert selected_ids <= {row["paper_id"] for row in rows}
    assert pack["answer_candidate_shortlist_count"] == 17
    assert pack["trace_certificate_text_scope"] == "exact_prompt_visible_chunks"
    certifying = pack["trace_certifying_evidence"]
    assert certifying["strategy"] == "full_selected_set_v2"
    assert set(certifying["paper_ids"]) == selected_ids
    assert certifying["certifying_paper_count"] == len(selected_ids)
    assert certifying["maximum_certification_papers"] is None
    assert all(chunk["certification_role"] == "certifying" for chunk in pack["chunks"])
    accounting = pack["answer_context_budget"]
    assert accounting["rendered_prompt_fits"] is True
    assert (
        accounting["rendered_prompt_estimated_tokens"]
        + accounting["output_reserve_tokens"]
        + accounting["safety_margin_tokens"]
        <= accounting["context_window_tokens"]
    )
    assert sum(ask_corpus.estimate_tokens(chunk["text"]) for chunk in pack["chunks"]) <= accounting["evidence_token_budget"]
    assert selection["run_config"]["prompt_token_cost"] <= selection["run_config"]["prompt_token_budget"]
    assert selection["run_config"]["prompt_token_budget"] == accounting["prompt_evidence_capacity"]
    assert all(chunk["text"] in prompt for chunk in pack["chunks"])
    assert config["answer_call_performed"] is False
