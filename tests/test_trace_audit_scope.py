from __future__ import annotations

from copy import deepcopy

import pytest

from originrag.analysis import prompts
from originrag.analysis.answer_qc import lineage_independence_certified
from originrag.analysis.context_budget import context_capacity, estimate_prompt_tokens


QUESTION = "Do strong sensor benchmarks establish generalization to unseen conditions?"
PACK = {
    "chunks": [{
        "evidence_id": "E001", "paper_id": "P1", "citation_label": "Example 2026",
        "title": "Sensor benchmark", "section": "results",
        "text": "After tuning, SENSOR_A was compared with baselines across ten conditions. "
                "The excerpt does not specify whether those conditions were absent from training.",
    }],
    "trace_sufficiency_certificate": {
        "evidence_sufficiency": "weak", "allowed_claim_strength": "hypothesis_only",
        "unresolved_facets": ["held_out_conditions"],
        "reasons": ["lineage_graph_not_provided", "blueprint_not_human_reviewed"],
    },
    "trace_robust_lineage": {
        "status": "insufficient_even_optimistically",
        "effective_evidence": {"lower": 0, "upper": 0},
        "lineage_count_lower": 0, "lineage_count_upper": 0,
        "missing_pair_count": 1, "uncertain_pair_count": 1,
        "pessimistic_flow": {"unresolved_facets": ["held_out_conditions"]},
    },
    "trace_certifying_evidence": {
        "strategy": "full_selected_set_v2", "paper_ids": ["P1"],
        "certifying_paper_count": 1, "context_paper_count": 1,
    },
    "provisional_synthesis_authorized": True,
}


@pytest.mark.parametrize("repair", [False, True])
def test_shared_trace_policy_omits_machine_diagnostics_and_preserves_sources(repair):
    original = deepcopy(PACK)
    if repair:
        prompt = prompts.build_answer_repair_prompt(
            QUESTION, PACK, "answer", "An earlier answer.",
            {"evidence_ids_available": ["E001"]},
        )
    else:
        prompt = prompts.build_citation_answer_prompt(QUESTION, PACK)
    for diagnostic in (
        "lineage_graph_not_provided", "blueprint_not_human_reviewed", "held_out_conditions",
        "Certificate grade:", "Certificate reasons:", "Unresolved required facets:",
        "Robust lineage status:", "Robust effective-evidence interval:",
        "Robust lineage-count interval:", "Pessimistic unresolved facets:",
        "Missing / uncertain lineage pairs:", "insufficient_even_optimistically",
        "graph availability", "blueprint review",
    ):
        assert diagnostic not in prompt
    assert "Maximum allowed claim strength: hypothesis_only" in prompt
    assert "Study-origin independence among the retrieved reports is not certified" in prompt
    assert PACK == original
    assert PACK["chunks"][0]["text"] in prompt
    assert prompt.endswith(prompts._format_evidence_text(PACK) + "\n")


@pytest.mark.parametrize("provisional", [False, True])
def test_audit_policy_preserves_claim_ceiling_source_restrictions_and_provisional_branch(provisional):
    pack = {**deepcopy(PACK), "provisional_synthesis_authorized": provisional}
    block = prompts._trace_constraint_block(pack)

    for required in (
        "Maximum allowed claim strength: hypothesis_only",
        "Certifying evidence set: paper IDs: P1",
        "Claims of independent or replicated support may rely only on the robust certificate",
        "Evidence entries labelled `context_only` may support attributed descriptive statements, but not a claim of independent or replicated support",
        "Never make a claim stronger than the maximum allowed claim strength",
    ):
        assert required in block
    if provisional:
        assert "Provide a cautious paper-level synthesis instead of withholding all text" in block
        assert "Explicitly state that consistency across independent studies is not certified" in block
        assert "Do not present a finding as a core, independently replicated, or independently supported result" in block
        assert "If a paper itself labels a result as replicated, you may report that wording only with explicit paper attribution" in block
        assert "Do not treat this evidence set as sufficient" not in block
    else:
        assert "Do not treat this evidence set as sufficient unless the user explicitly forced an answer" in block
        assert "Provide a cautious paper-level synthesis" not in block


@pytest.mark.parametrize("certificate", [None, "unknown", []])
def test_no_audit_instructions_are_added_without_a_certificate(certificate):
    assert prompts._trace_constraint_block({"trace_sufficiency_certificate": certificate}) == ""


def test_trace_scope_with_no_robust_certificate_does_not_invent_a_certificate():
    pack = {"trace_sufficiency_certificate": deepcopy(PACK["trace_sufficiency_certificate"])}
    block = prompts._trace_constraint_block(pack)

    assert "Maximum allowed claim strength: hypothesis_only" in block
    assert "Robust lineage status:" not in block
    assert "Certifying evidence set:" not in block
    assert "Study-origin independence" not in block


@pytest.mark.parametrize("status", [
    None, "unknown", "lineage_review_required", "insufficient_even_optimistically",
    "robustly_sufficient", "sufficient", "certified",
])
@pytest.mark.parametrize("counts", [
    {}, {"missing_pair_count": 0, "uncertain_pair_count": 0},
    {"missing_pair_count": 1, "uncertain_pair_count": 0},
    {"missing_pair_count": 0, "uncertain_pair_count": 1},
    {"missing_pair_count": None, "uncertain_pair_count": 0},
    {"missing_pair_count": "invalid", "uncertain_pair_count": 0},
])
def test_sufficiency_permission_and_independence_are_separate_fail_closed_policies(status, counts):
    pack = deepcopy(PACK)
    robust = {"status": status, **counts}
    pack["trace_robust_lineage"] = robust
    pack["provisional_synthesis_authorized"] = False
    block = prompts._trace_constraint_block(pack)

    assert ("Do not treat this evidence set as sufficient" in block) == (status != "robustly_sufficient")
    assert ("Study-origin independence among the retrieved reports is not certified" in block) == (
        not lineage_independence_certified(robust)
    )
    assert "Maximum allowed claim strength: hypothesis_only" in block
    assert "Never make a claim stronger than the maximum allowed claim strength" in block
    assert "independence is certified" not in block
    assert "permits synthesis" not in block


@pytest.mark.parametrize("repair", [False, True])
def test_ignored_diagnostics_cannot_change_model_prompt_or_raw_audit_data(repair):
    changed = deepcopy(PACK)
    changed["trace_sufficiency_certificate"].update({
        "evidence_sufficiency": "good", "reasons": ["new_machine_reason_SENTINEL"],
        "unresolved_facets": ["new_generated_facet_SENTINEL"],
        "lineage_graph_status": "new_graph_status_SENTINEL",
    })
    changed["trace_robust_lineage"].update({
        "effective_evidence": {"lower": 789.123, "upper": 987.321},
        "lineage_count_lower": 789123, "lineage_count_upper": 987321,
        "missing_pair_count": 456789, "uncertain_pair_count": 987654,
        "pessimistic_flow": {"unresolved_facets": ["new_pessimistic_facet_SENTINEL"]},
    })
    changed["trace_certifying_evidence"].update({
        "certifying_paper_count": 987654, "context_paper_count": 456789,
    })
    original = deepcopy(changed)

    def render(pack):
        if repair:
            return prompts.build_answer_repair_prompt(
                QUESTION, pack, "answer", "Earlier answer.", {"evidence_ids_available": ["E001"]},
            )
        return prompts.build_citation_answer_prompt(QUESTION, pack)

    assert render(changed) == render(PACK)
    assert changed == original


def test_current_certifying_ids_take_precedence_and_legacy_core_restrictions_remain():
    pack = deepcopy(PACK)
    pack["trace_certification_core"] = {
        "paper_ids": ["P_LEGACY"], "certification_paper_count": 1, "context_paper_count": 9,
    }
    block = prompts._trace_constraint_block(pack)

    assert "Certifying evidence set: paper IDs: P1" in block
    assert "P_LEGACY" not in block
    pack.pop("trace_certifying_evidence")
    block = prompts._trace_constraint_block(pack)
    assert "Certification core: paper IDs: P_LEGACY" in block
    assert "Claims of independent or replicated support may rely only on the certification core" in block
    assert "Other context may inform the synthesis but cannot increase certified origin support" in block
    assert "1 of 9" not in block


def test_explicit_comparison_keeps_source_text_as_final_prompt_content():
    question = "Distinguish a sensor mention from a study of the sensor itself."
    prompt = prompts.build_citation_answer_prompt(question, PACK)

    assert prompt.endswith(prompts._format_evidence_text(PACK) + "\n")


def test_fully_rendered_audit_prompt_still_uses_existing_fail_closed_context_checks():
    original = deepcopy(PACK)
    base = prompts.build_citation_answer_prompt(QUESTION, {"chunks": []})
    budget = context_capacity(
        context_window_tokens=16384, output_tokens=2200, base_prompt=base,
        repair_reserve_tokens=2200,
    )
    prompt = prompts.build_citation_answer_prompt(QUESTION, PACK)

    assert budget.context_window_tokens == 16384
    assert estimate_prompt_tokens(prompt) > estimate_prompt_tokens(base)
    budget.require_prompt_fit(prompt)
    with pytest.raises(ValueError, match="No evidence was removed"):
        budget.require_prompt_fit(prompt + " Additional quoted evidence." * 2000)
    assert PACK == original
