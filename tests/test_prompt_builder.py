from __future__ import annotations

import pytest

import originrag.analysis.llm_backends as llm_backends
from originrag.analysis.llm_backends import generate_answer
from originrag.analysis.llm_backends import clean_ollama_cli_output
from originrag.analysis.llm_backends import LLMResult
from originrag.analysis.prompts import build_answer_repair_prompt, build_citation_answer_prompt
from originrag.analysis.template_answers import build_template_answer


def test_prompt_contains_evidence_labels_and_no_invented_citation_instruction() -> None:
    prompt = build_citation_answer_prompt(
        "What evidence links PfEMP1 to severe malaria?",
        {
            "chunks": [
                {
                    "evidence_id": "E001",
                    "citation_label": "[Jespersen 2016, Results, pages 3-4]",
                    "title": "A paper",
                    "section": "results",
                    "text": "EPCR-binding PfEMP1 was associated with severe malaria.",
                }
            ]
        },
    )

    assert "[Jespersen 2016, Results, pages 3-4]" in prompt
    assert "Allowed evidence IDs: E001" in prompt
    assert "Never cite evidence IDs outside the allowed list" in prompt
    assert "Do not invent citations" in prompt
    assert "Do not add examples in parentheses" in prompt
    assert "Do not make a study design more specific than the evidence" in prompt
    assert "distinguish molecular markers" in prompt
    assert "distinguish expression or receptor-binding association" in prompt
    assert "Do not add downstream outcomes" in prompt
    assert "Use causal verbs" in prompt
    assert '"Highly expressed in severe versus moderate disease" is comparative expression' in prompt
    assert 'Avoid predicate duplication such as "SPP1 expression was highly expressed"' in prompt
    assert 'Treat "may contribute to" as a functional or causal claim' in prompt
    assert "apply a marker-positive label only to members" in prompt
    assert "include every explicit marker-bearing state/group/subtype label" in prompt
    assert "Do not introduce unrelated gaps such as diagnostics" in prompt
    assert "unless the user explicitly asks for a list, table, or overview" in prompt
    assert "## References" in prompt
    assert "In Cautious inference, clearly mark what is inferred rather than directly shown." in prompt
    assert "keep the voice conversational and direct" in prompt
    assert "most directly relevant evidence item was cited" in prompt
    assert "name the exact labels present in the direct evidence" in prompt
    assert "questions about rates, frequencies, effect sizes" in prompt
    assert "write one short natural prose paragraph" in prompt
    assert "author-year label and its evidence ID" in prompt
    assert "retrieved evidence pack does not establish" in prompt
    assert "Receptor binding, competition or blockade experiments" in prompt
    assert "never infer a result from a truncated table fragment" in prompt
    assert "Quality flags: none" in prompt
    assert "Source design: Source type uncertain" in prompt
    assert "cite a directly reporting primary-research paper whenever one is present" in prompt
    assert "keep a normal answer to 120-200 words" in prompt
    assert "Use one main claim per sentence" in prompt
    assert "Do not narrate retrieval" in prompt


def test_answer_prompt_separates_disagreement_and_limits_answer_shape() -> None:
    prompt = build_citation_answer_prompt(
        "Does feature X differ between clinical groups?",
        {
            "chunks": [
                {
                    "evidence_id": "E001",
                    "citation_label": "[Alpha 2024, Results]",
                    "text": "Feature X was higher in severe disease.",
                },
                {
                    "evidence_id": "E002",
                    "citation_label": "[Beta 2025, Results]",
                    "text": "Feature X did not differ between groups.",
                },
            ]
        },
    )

    normalized = " ".join(prompt.split())
    assert "disagree in direction or statistical significance" in normalized
    assert "each study-specific result in a separate sentence" in normalized
    assert "Do not attach both citations to a single claim that implies agreement" in normalized
    assert "Before References, use at most 200 prose words" in normalized
    assert "Evidence-backed statements may use up to five short sentences" in normalized
    assert "Keep every factual result source-local" in normalized
    assert "citation placed immediately after it" in normalized
    assert "Missing evidence may use at most one sentence each" in normalized


def test_comparison_prompt_highlights_direct_positive_and_null_coverage() -> None:
    question = (
        "What expression changes distinguish asymptomatic infections from clinical malaria?"
    )
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Alpha 2024, Results]",
                "text": (
                    "Group B transcripts were higher in clinical infections than in "
                    "asymptomatic infections."
                ),
            },
            {
                "evidence_id": "E002",
                "citation_label": "[Beta 2025, Results]",
                "text": (
                    "Group C transcripts were similar between asymptomatic infection "
                    "and clinical malaria."
                ),
            },
            {
                "evidence_id": "E003",
                "citation_label": "[Gamma 2023, Results]",
                "text": "Expression differed between ex vivo and cultured parasites.",
            },
        ]
    }

    prompt = build_citation_answer_prompt(question, pack)
    repair = build_answer_repair_prompt(
        question,
        pack,
        "answer",
        "Previous answer.",
        {"evidence_ids_available": ["E001", "E002", "E003"]},
    )

    for candidate in (prompt, repair):
        normalized = " ".join(candidate.split())
        assert "Direct comparison coverage" in normalized
        assert "asymptomatic infection; clinical malaria" in normalized
        assert (
            "Evidence items whose prompt-visible text names every requested group: "
            "E001, E002 (contains explicit null or non-difference wording)."
        ) in normalized
        assert "including an explicit null, non-association, or contradictory result" in normalized
        assert "This is not a citation quota" in normalized
        assert "Collapse true publication versions and duplicate findings" in normalized


def test_comparison_coverage_requires_two_question_groups_and_evidence_text() -> None:
    single_group = build_citation_answer_prompt(
        "What is expressed in clinical malaria?",
        {
            "chunks": [
                {
                    "evidence_id": "E001",
                    "citation_label": "[Alpha 2024, Results]",
                    "text": "Clinical malaria was studied.",
                }
            ]
        },
    )
    title_only = build_citation_answer_prompt(
        "Compare severe malaria with uncomplicated malaria.",
        {
            "chunks": [
                {
                    "evidence_id": "E001",
                    "citation_label": "[Alpha 2024, Results]",
                    "title": "Severe versus uncomplicated malaria",
                    "text": "The transcript assay was reproducible.",
                }
            ]
        },
    )

    assert "Direct comparison coverage" not in single_group
    assert "Direct comparison coverage" not in title_only


def test_repair_prompt_lists_missing_direct_comparison_groups() -> None:
    repair = build_answer_repair_prompt(
        "How strong is the evidence?",
        {"chunks": []},
        "answer",
        "Previous answer.",
        {
            "evidence_ids_available": ["E007", "E010"],
            "warnings": [
                "direct_comparison_evidence_omitted",
                "causal_claim_not_supported",
            ],
            "unsupported_causal_claims": [
                "Group C dominance may contribute to persistence."
            ],
            "missing_direct_comparison_candidate_groups": [
                {
                    "evidence_ids": ["E007"],
                    "paper_ids": ["P7"],
                    "result_sentences": [
                        {
                            "evidence_id": "E007",
                            "sentence": (
                                "Var group A and B transcript abundances were higher in "
                                "clinical malaria than in asymptomatic infections."
                            ),
                        }
                    ],
                },
                {
                    "evidence_ids": ["E010", "E011"],
                    "paper_ids": ["P10", "P10_PREPRINT"],
                    "result_sentences": [
                        {
                            "evidence_id": "E010",
                            "sentence": (
                                "Var expression homogeneity was higher in asymptomatic "
                                "infections than in clinical malaria."
                            ),
                        }
                    ],
                },
            ],
        },
    )

    normalized = " ".join(repair.split())
    allowed_index = repair.index("Allowed evidence IDs:")
    mandatory_index = repair.index("MANDATORY DIRECT-COMPARISON CORRECTION:")
    warnings_index = repair.index("QC warnings:")
    assert allowed_index < mandatory_index < warnings_index
    assert "Start from a blank answer" in normalized
    assert "Do not patch the previous answer or merely append citations" in normalized
    assert "Before ``## References``" in normalized
    assert "substantive, source-local result sentence" in normalized
    assert "at least one allowed E-ID from every exact candidate group" in normalized
    assert "Exact candidate group 1: E007 (publication family: P7)" in normalized
    assert (
        "Exact candidate group 2: E010, E011 "
        "(publication family: P10, P10_PREPRINT)"
    ) in normalized
    assert (
        "Source-local result from E007: Var group A and B transcript abundances were "
        "higher in clinical malaria than in asymptomatic infections."
    ) in normalized
    assert (
        "Source-local result from E010: Var expression homogeneity was higher in "
        "asymptomatic infections than in clinical malaria."
    ) in normalized
    assert (
        "preserve its exact comparison groups and reported higher, lower, or null direction"
        in normalized
    )
    assert "Paraphrase it accurately in a sentence citing that source" in normalized
    assert "Do not merge separate results into one apparent consensus" in normalized
    assert "unsupported combined claim" in normalized
    assert "A bare citation, a citation-only sentence, or a References entry" in normalized
    assert "citing one listed version satisfies that group" in normalized
    assert "not a citation quota" in normalized
    assert "Group C dominance may contribute to persistence." in normalized
    assert "These are deletion targets" in normalized
    assert "not wording to quote, hedge, explain, or repeat" in normalized
    assert "E007 (publication family: P7)" in normalized
    assert "E010, E011 (publication family: P10, P10_PREPRINT)" in normalized
    assert "direct_comparison_evidence_omitted" in normalized
    assert "Do not add duplicate versions or unrelated citations merely to reach a count" in normalized


def test_repair_prompt_omits_mandatory_comparison_block_without_missing_groups() -> None:
    repair = build_answer_repair_prompt(
        "How strong is the evidence?",
        {"chunks": []},
        "answer",
        "Previous answer.",
        {
            "evidence_ids_available": ["E001"],
            "warnings": ["causal_claim_not_supported"],
            "unsupported_causal_claims": ["Feature X causes disease."],
        },
    )

    assert "MANDATORY DIRECT-COMPARISON CORRECTION" not in repair


def test_prompt_treats_unresolved_independence_and_topic_gaps_conservatively() -> None:
    prompt = build_citation_answer_prompt(
        "How strong is the evidence for feature X?",
        {"chunks": []},
    )
    repair = build_answer_repair_prompt(
        "How strong is the evidence for feature X?",
        {"chunks": []},
        "answer",
        "Independent replication is absent.",
        {
            "warnings": ["independent_support_not_certified"],
            "evidence_ids_available": [],
        },
    )

    for candidate in (prompt, repair):
        normalized = " ".join(candidate.split())
        assert "describe it only as unknown or not certified" in normalized
        assert "Never call it a lack or absence of independent replication" in normalized
        assert "Do not cite a study as evidence that the topic it directly examined" in normalized
        assert "narrower missing comparison, replication, mechanism" in normalized
        assert "use a source for that comparison only when its visible evidence" in normalized
        assert "ex vivo versus in vitro culture" in normalized
        assert "asymptomatic-versus-clinical comparison" in normalized


def test_repair_prompt_names_unsupported_replication_absence_claim() -> None:
    prompt = build_answer_repair_prompt(
        "How strong is the evidence?",
        {"chunks": []},
        "answer",
        "Independent replication is lacking.",
        {
            "warnings": ["independent_replication_absence_not_established"],
            "unsupported_independence_absence_claims": [
                "Independent replication is lacking"
            ],
            "evidence_ids_available": [],
        },
    )

    normalized = " ".join(prompt.split())
    assert "Unsupported claims that independent replication is absent" in normalized
    assert "Independent replication is lacking" in normalized
    assert "replace every claim that independent replication is absent" in normalized
    assert "study-origin relations remain unresolved" in normalized


def test_epistemic_causal_comparison_prompt_requires_explicit_evidence_limit() -> None:
    question = (
        "Compare Sakoguchi et al. (2025) and Kassegne et al. (2025). "
        "Does either paper demonstrate that receptor binding itself causes a "
        "reduction in severe-malaria incidence?"
    )
    pack = {
        "question": question,
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Sakoguchi et al., 2025, Abstract]",
                "text": "RIFIN bound KIR2DL1 in receptor assays.",
            },
            {
                "evidence_id": "E002",
                "citation_label": "[Kassegne et al., 2025, Abstract]",
                "text": "Antibodies correlated with reduced severe malaria risk.",
            },
        ],
    }

    prompt = build_citation_answer_prompt(question, pack)
    repair = build_answer_repair_prompt(
        question,
        pack,
        "answer",
        "The papers report receptor binding (E001; E002).",
        {
            "warnings": ["causal_evidence_question_not_resolved"],
            "evidence_ids_available": ["E001", "E002"],
        },
    )

    assert "Causal evidence-strength interrogation" in prompt
    normalized_prompt = " ".join(prompt.split())
    assert "does not by itself demonstrate that binding causes" in normalized_prompt
    assert "neither report demonstrates" in prompt
    assert "does not prove that the causal relationship is absent" in prompt
    assert "causal_evidence_question_not_resolved" in repair
    assert "answer the causal-evidence" in repair


def test_affirmative_causal_comparison_does_not_receive_interrogation_exception() -> None:
    prompt = build_citation_answer_prompt(
        (
            "Compare Sakoguchi et al. (2025) and Kassegne et al. (2025). "
            "Explain which paper demonstrates that receptor binding causes a "
            "reduction in severe-malaria incidence."
        ),
        {"chunks": []},
    )

    assert "Causal evidence-strength interrogation" not in prompt


def test_prompt_exposes_trace_claim_ceiling_and_unresolved_facets() -> None:
    prompt = build_citation_answer_prompt(
        "What does the evidence establish?",
        {
            "chunks": [],
            "trace_sufficiency_certificate": {
                "evidence_sufficiency": "weak",
                "allowed_claim_strength": "hypothesis_only",
                "unresolved_facets": ["clinical_replication"],
                "reasons": ["lineage_graph_incomplete"],
            },
            "trace_robust_lineage": {
                "status": "lineage_review_required",
                "effective_evidence": {"lower": 1.2, "upper": 3.0},
                "lineage_count_lower": 1,
                "lineage_count_upper": 3,
                "pessimistic_flow": {
                    "unresolved_facets": ["independent_replication"]
                },
                "missing_pair_count": 2,
                "uncertain_pair_count": 3,
            },
            "trace_certification_core": {
                "certification_paper_count": 2,
                "context_paper_count": 4,
                "paper_ids": ["P1", "P2"],
            },
        },
    )

    assert "TRACE evidence constraints (hard limits)" in prompt
    assert "Maximum allowed claim strength: hypothesis_only" in prompt
    assert "clinical_replication" in prompt
    assert "lineage_graph_incomplete" in prompt
    assert "Robust lineage status: lineage_review_required" in prompt
    assert "Robust effective-evidence interval: [1.2, 3.0]" in prompt
    assert "independent_replication" in prompt
    assert "Certification core: 2 of 4 context reports; paper IDs: P1, P2" in prompt
    assert "Claims of independent or replicated support may rely only" in prompt
    assert "context_only" in prompt
    assert "other than robustly_sufficient as insufficient evidence" in prompt


def test_prompt_uses_current_full_selected_evidence_language() -> None:
    prompt = build_citation_answer_prompt(
        "What does the evidence establish?",
        {
            "chunks": [],
            "trace_sufficiency_certificate": {
                "evidence_sufficiency": "weak",
                "allowed_claim_strength": "hypothesis_only",
            },
            "trace_robust_lineage": {
                "status": "lineage_review_required",
                "effective_evidence": {"lower": 1.0, "upper": 3.0},
                "pessimistic_flow": {},
            },
            "trace_certifying_evidence": {
                "strategy": "full_selected_set_v2",
                "certifying_paper_count": 3,
                "context_paper_count": 3,
                "paper_ids": ["P1", "P2", "P3"],
            },
            "trace_certification_core": {
                "certification_paper_count": 1,
                "context_paper_count": 3,
                "paper_ids": ["P1"],
            },
        },
    )

    assert "Certifying evidence set: 3 of 3 selected reports" in prompt
    assert "P1, P2, P3" in prompt
    assert "Certification core:" not in prompt


def test_prompt_allows_bounded_paper_level_synthesis_without_certifying_origins() -> None:
    prompt = build_citation_answer_prompt(
        "What is reported?",
        {
            "chunks": [],
            "provisional_synthesis_authorized": True,
            "trace_sufficiency_certificate": {
                "evidence_sufficiency": "weak",
                "allowed_claim_strength": "hypothesis_only",
            },
            "trace_robust_lineage": {
                "status": "insufficient_even_optimistically",
                "effective_evidence": {"lower": 1.0, "upper": 4.0},
                "pessimistic_flow": {},
            },
        },
    )

    assert "Provide a cautious paper-level synthesis instead of withholding all text" in prompt
    assert "consistency across independent studies is not certified" in prompt
    assert "Do not present a finding as a core, independently replicated" in prompt
    assert "If a paper itself labels a result as replicated" in prompt
    assert "study-origin independence is not certified" in prompt


def test_prompt_honors_explicit_german_list_request() -> None:
    prompt = build_citation_answer_prompt(
        "Gib mir alle Korrelationen übersichtlich als Liste.",
        {"chunks": []},
    )

    assert "use a Markdown bullet list" in prompt
    assert "one distinct finding per bullet" in prompt
    assert "clinical syndrome/outcome" in prompt
    assert "Write the complete scientific answer in German" in prompt
    assert "hereditary" not in prompt


def test_prompt_cleans_common_pdf_artifacts_before_model_input() -> None:
    prompt = build_citation_answer_prompt(
        "What evidence links sex differences to immunity?",
        {
            "chunks": [
                {
                    "evidence_id": "E001",
                    "citation_label": "[Example 2025, Results]",
                    "title": "A paper",
                    "section": "results",
                    "text": (
                        "[...] Malarianaive participants had IFN-\x03 signals; "
                        "an ohort study from Western Kenya was imp..."
                    ),
                }
            ]
        },
    )

    assert "Malaria-naive" in prompt
    assert "IFN-gamma" in prompt
    assert "cohort study" in prompt
    assert "Malarianaive" not in prompt
    assert "IFN-\x03" not in prompt
    assert "an ohort study" not in prompt
    assert "imp..." not in prompt


def test_gap_prompt_asks_for_supported_research_gaps() -> None:
    prompt = build_citation_answer_prompt(
        "Find gaps connecting var genes and host immunity.",
        {
            "chunks": [
                {
                    "evidence_id": "E001",
                    "citation_label": "[Warimwe 2009, Discussion, pages 5, chunk C1]",
                    "paper_id": "MAL_VAR_0053",
                    "title": "A paper",
                    "journal": "PNAS",
                    "section": "discussion",
                    "text": "The mechanism of disease onset remains unclear.",
                }
            ]
        },
        task="gaps",
    )

    assert "## Supported Research Gaps" in prompt
    assert "what evidence is still missing" in prompt
    assert "Paper ID: MAL_VAR_0053" in prompt


def test_hypothesis_prompt_uses_hypothesis_schema() -> None:
    prompt = build_citation_answer_prompt(
        "Make a hypothesis about PfEMP1 and severe malaria.",
        {"chunks": []},
        task="hypothesis",
    )

    assert "## Hypothesis Card" in prompt
    assert "STRICT OUTPUT FORMAT" in prompt
    assert "Start your answer with exactly: ## Hypothesis Card" in prompt
    assert "### evidence_backed_statements" in prompt
    assert "### testable_prediction" in prompt
    assert "Evidence:" in prompt


def test_unknown_prompt_task_fails_clearly() -> None:
    with pytest.raises(ValueError, match="Unsupported task"):
        build_citation_answer_prompt("Question", {"chunks": []}, task="review")


def test_sources_prompt_asks_for_paper_level_answer() -> None:
    prompt = build_citation_answer_prompt(
        "Which paper had the Kenya cohort?",
        {
            "chunks": [
                {
                    "evidence_id": "E001",
                    "citation_label": "[Okiring 2022, Results]",
                    "paper_id": "MAL_SEX_0007",
                    "title": "A Kenya cohort paper",
                    "journal": "Malaria Journal",
                    "section": "results",
                    "text": "A cohort study from Western Kenya...",
                }
            ]
        },
        task="sources",
    )

    assert "## Best match" in prompt
    assert "Do not mention chunk IDs" in prompt
    assert "Do not expose evidence IDs" in prompt
    assert "title, author/year, and journal" in prompt
    assert "## Caveat" not in prompt


def test_prompt_includes_profile_without_overriding_evidence_rules() -> None:
    prompt = build_citation_answer_prompt(
        "What evidence links sex differences to malaria immunity?",
        {
            "chunks": [
                {
                    "evidence_id": "E001",
                    "citation_label": "[Example 2025, Results]",
                    "text": "Female participants had different cytokine profiles.",
                }
            ]
        },
        profile_instruction="Use concise language and short bullets.",
    )

    assert "User response profile" in prompt
    assert "Use concise language and short bullets." in prompt
    assert "must not override the evidence-only, citation, uncertainty, or safety rules" in prompt
    assert "Answer only using the provided evidence" in prompt


def test_sources_template_returns_paper_not_chunk_details() -> None:
    answer = build_template_answer(
        "Which paper had the Kenya cohort?",
        {
            "chunks": [
                {
                    "evidence_id": "E001",
                    "author": "Okiring",
                    "year": "2022",
                    "title": "Clinical malaria incidence after asymptomatic parasitaemia",
                    "journal": "Malaria Journal",
                    "doi": "10.1186/example",
                    "text": "A cohort study from Western Kenya followed individuals...",
                }
            ]
        },
        task="sources",
    )

    assert "## Best match" in answer
    assert "Okiring 2022" in answer
    assert "Clinical malaria incidence" in answer
    assert "https://doi.org/10.1186/example" in answer
    assert "## Caveat" not in answer
    assert "CHUNK" not in answer
    assert "E001" not in answer


def test_sources_template_prefers_sample_size_match_over_incidental_over_word() -> None:
    answer = build_template_answer(
        "Which paper had the Kenya cohort with over 700 patients?",
        {
            "chunks": [
                {
                    "evidence_id": "E001",
                    "author": "Okiring",
                    "year": "2022",
                    "title": "Gender difference in malaria incidence",
                    "journal": "Malaria Journal",
                    "text": (
                        "Supporting this hypothesis, another recent cohort study from Western Kenya "
                        "found associations that were over twice as strong in females."
                    ),
                },
                {
                    "evidence_id": "E002",
                    "author": "Ndugwa",
                    "year": "2026",
                    "title": "Expression of specific var gene subtypes",
                    "journal": "eLife",
                    "doi": "10.7554/eLife.110810.1",
                    "text": (
                        "The study includes samples collected over a period of almost 20 years from "
                        "about 700 children presenting with severe or non-severe malaria in Kenya."
                    ),
                },
            ]
        },
        task="sources",
    )

    best_match_section = answer.split("## Why this looks like the source", maxsplit=1)[0]

    assert "Ndugwa 2026" in best_match_section
    assert "https://doi.org/10.7554/eLife.110810.1" in best_match_section


def test_repair_prompt_includes_qc_failures_and_previous_answer() -> None:
    prompt = build_answer_repair_prompt(
        question="Make a hypothesis.",
        evidence_pack={
            "chunks": [
                {
                    "evidence_id": "E001",
                    "citation_label": "[Ceesay 2025, Results]",
                    "paper_id": "MAL_GEN_0012",
                    "title": "A paper",
                    "journal": "PLOS Pathogens",
                    "section": "results",
                    "text": "Asymptomatic infections had broader PfEMP1 recognition.",
                }
            ]
        },
        task="hypothesis",
        previous_answer="A loose summary with iSP2.",
        qc_report={
            "warnings": [
                "missing_required_sections",
                "possible_unsupported_terms",
                "causal_claim_not_supported",
            ],
            "missing_sections": ["## Hypothesis Card"],
            "empty_sections": ["### inference"],
            "possible_unsupported_terms": ["iSP2"],
            "unsupported_causal_claims": ["binding leads to pathology"],
            "evidence_ids_available": ["E001"],
        },
    )

    assert "failed automated quality control" in prompt
    assert "## Hypothesis Card" in prompt
    assert "### inference" in prompt
    assert "Empty required sections" in prompt
    assert "instead of leaving ``Cautious inference`` empty" in " ".join(
        prompt.split()
    )
    assert "iSP2" in prompt
    assert "binding leads to pathology" in prompt
    assert "rewrite every listed causal phrase" in prompt
    assert "Allowed evidence IDs" in prompt
    assert "E001" in prompt
    assert "A loose summary" in prompt


def test_repair_prompt_names_unsupported_marker_members_and_relation_upgrade() -> None:
    prompt = build_answer_repair_prompt(
        question="Which SPP1-positive macrophages occur in severe COVID-19?",
        evidence_pack={
            "chunks": [
                {
                    "evidence_id": "E001",
                    "citation_label": "[Liao 2020, Results]",
                    "text": (
                        "Group 2 FCN1loSPP1+ and group 3 SPP1+ were highly expressed "
                        "in severe compared with moderate COVID-19."
                    ),
                }
            ]
        },
        task="answer",
        previous_answer=(
            "Four SPP1-positive subtypes included FCN1hi group 1 and FABP4+ group 4; "
            "they were highly associated with severity (E001)."
        ),
        qc_report={
            "warnings": [
                "marker_membership_not_supported",
                "relation_level_upgraded",
                "marker_positive_labels_omitted",
            ],
            "unsupported_marker_memberships": [
                {
                    "marker": "SPP1",
                    "unsupported_members": ["FCN1hi group 1", "FABP4+ group 4"],
                }
            ],
            "unsupported_relation_upgrades": [
                {
                    "claim": "they were highly associated with severity (E001)",
                    "required_relation": "comparative expression or enrichment",
                }
            ],
            "missing_marker_positive_labels": [
                {
                    "marker": "SPP1",
                    "missing_labels": ["FCN1loSPP1+ group 2"],
                }
            ],
            "evidence_ids_available": ["E001"],
        },
    )

    assert "SPP1-positive scope does not support" in prompt
    assert "FCN1hi group 1, FABP4+ group 4" in prompt
    assert "comparative expression or enrichment" in prompt
    assert "do not label the QC-listed unsupported members marker-positive" in prompt
    assert 'rewrite "highly associated" as comparative expression' in prompt
    assert "Marker-positive labels required by cited evidence but omitted" in prompt
    assert "FCN1loSPP1+ group 2" in prompt
    assert "add every exact missing label" in prompt


def test_repair_prompt_requires_restoring_inverted_comparison_direction() -> None:
    prompt = build_answer_repair_prompt(
        question="Was expression higher or lower?",
        evidence_pack={
            "chunks": [
                {
                    "evidence_id": "E001",
                    "citation_label": "[Test 2024, Results]",
                    "text": "Expression was lower in severe than uncomplicated malaria.",
                }
            ]
        },
        task="answer",
        previous_answer="Expression was higher in severe malaria (E001).",
        qc_report={
            "warnings": ["relation_direction_inverted"],
            "unsupported_relation_directions": [
                {
                    "claim": "Expression was higher in severe malaria (E001).",
                    "required_relation": (
                        "preserve the cited comparison direction (down) for expression"
                    ),
                }
            ],
            "evidence_ids_available": ["E001"],
        },
    )

    assert "relation_direction_inverted" in prompt
    assert "comparison direction (down)" in prompt
    assert "Do not repair a reversed" in prompt


def test_prompt_exposes_automatic_publication_family_without_promoting_it_to_gold() -> None:
    prompt = build_citation_answer_prompt(
        "What was reported?",
        {
            "chunks": [
                {
                    "evidence_id": "E001",
                    "paper_id": "P_PRE",
                    "citation_label": "[Preprint 2024]",
                    "text": "Preprint evidence.",
                },
                {
                    "evidence_id": "E002",
                    "paper_id": "P_JOURNAL",
                    "citation_label": "[Journal 2025]",
                    "text": "Journal evidence.",
                },
            ],
            "automatic_lineage_signals": [
                {
                    "paper_id_a": "P_PRE",
                    "paper_id_b": "P_JOURNAL",
                    "signal_type": "same_preprint_family",
                    "details": {},
                }
            ],
        },
    )

    assert "same_preprint_family: P_PRE (E001) <-> P_JOURNAL (E002)" in prompt
    assert "review-assistive, not adjudicated truth" in prompt
    assert "Do not mention an overlap pair" in prompt
    assert "Each cited report's substantive evidence must" in prompt
    assert "Other overlap signals belong in the interface audit" in prompt


def test_repair_prompt_gives_mechanically_checkable_publication_family_choices() -> None:
    pack = {
        "chunks": [
            {"evidence_id": "E001", "paper_id": "P_PRE", "citation_label": "[Preprint]", "text": "A."},
            {"evidence_id": "E002", "paper_id": "P_JOURNAL", "citation_label": "[Journal]", "text": "B."},
        ],
        "automatic_lineage_signals": [
            {
                "paper_id_a": "P_PRE",
                "paper_id_b": "P_JOURNAL",
                "signal_type": "same_preprint_family",
                "details": {},
            }
        ],
    }
    prompt = build_answer_repair_prompt(
        "What was reported?",
        pack,
        "answer",
        "Previous draft (E001; E002).",
        {
            "warnings": ["publication_family_cited_as_separate_support"],
            "evidence_ids_available": ["E001", "E002"],
        },
    )

    assert "Required publication-version correction" in prompt
    assert "E001 and E002 (P_PRE / P_JOURNAL)" in prompt
    assert "choose the first applicable action" in prompt
    assert "if neither report's substantive evidence directly answers" in prompt
    assert "if only one report directly contributes" in prompt
    assert "publication family and are not independent support" in prompt
    assert "not independent support" in prompt


def test_repair_prompt_preserves_german_list_request() -> None:
    prompt = build_answer_repair_prompt(
        question="Gib mir alle Korrelationen übersichtlich als Liste.",
        evidence_pack={"chunks": []},
        task="answer",
        previous_answer="Wrong prose.",
        qc_report={"warnings": ["possible_question_scope_drift"]},
    )

    assert "use a Markdown bullet list" in prompt
    assert "Write the complete scientific answer in German" in prompt


def test_data_accessibility_prompt_lists_repository_identifiers() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E012",
                "paper_id": "P2",
                "text": "Data availability: GEO repository GSE186820.",
            }
        ]
    }

    prompt = build_citation_answer_prompt(
        "Which datasets are accessible for reanalysis?",
        pack,
        task="answer",
    )

    assert "E012: GSE186820" in prompt
    assert "Do not write 'only study'" in prompt


def test_repair_prompt_names_omitted_repository_identifier() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E012",
                "paper_id": "P2",
                "text": "Data availability: GEO repository GSE186820.",
            }
        ]
    }

    prompt = build_answer_repair_prompt(
        "Which datasets are accessible for reanalysis?",
        pack,
        "answer",
        "No dataset was found.",
        {
            "warnings": ["repository_identifier_omitted"],
            "evidence_ids_available": ["E012"],
            "missing_repository_identifiers": [
                {
                    "identifier": "GSE186820",
                    "evidence_id": "E012",
                    "reason": "identifier_not_named",
                }
            ],
        },
    )

    assert "GSE186820 (E012: identifier_not_named)" in prompt


def test_backend_none_requires_no_credentials() -> None:
    result = generate_answer("Prompt", backend="none")

    assert result.backend == "none"
    assert result.answer is None
    assert result.error is None


def test_openai_is_not_supported_backend() -> None:
    result = generate_answer("Prompt", backend="openai")

    assert result.backend == "openai"
    assert result.answer is None
    assert "Supported backends are: none, ollama, deepseek, qwen, queue" in str(result.error)


def test_qwen_backend_uses_qwen_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    def fake_call(prompt: str, backend: str, base_url: str, model: str) -> LLMResult:
        calls.append({"prompt": prompt, "backend": backend, "base_url": base_url, "model": model})
        return LLMResult(backend=backend, answer="ok")

    monkeypatch.setenv("PFEMPIRE_QWEN_MODEL", "qwen3:4b")
    monkeypatch.setattr(llm_backends, "_call_ollama_generate", fake_call)

    result = generate_answer("Prompt", backend="qwen")

    assert result.backend == "qwen"
    assert result.answer == "ok"
    assert calls == [
        {
            "prompt": "Prompt",
            "backend": "qwen",
            "base_url": "http://localhost:11434",
            "model": "qwen3:4b",
        }
    ]


def test_qwen_backend_accepts_legacy_hyporag_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    def fake_call(prompt: str, backend: str, base_url: str, model: str) -> LLMResult:
        calls.append({"prompt": prompt, "backend": backend, "base_url": base_url, "model": model})
        return LLMResult(backend=backend, answer="ok")

    monkeypatch.delenv("PFEMPIRE_QWEN_MODEL", raising=False)
    monkeypatch.setenv("HYPORAG_QWEN_MODEL", "qwen3:legacy")
    monkeypatch.setattr(llm_backends, "_call_ollama_generate", fake_call)

    result = generate_answer("Prompt", backend="qwen")

    assert result.backend == "qwen"
    assert result.answer == "ok"
    assert calls[0]["model"] == "qwen3:legacy"


def test_ollama_cli_output_cleanup_removes_spinner_controls() -> None:
    output = "\x1b[?25l⠙ \x1b[K\x1b[?25h\n\x1b[?25lSure!\x1b[?25h\n"

    assert clean_ollama_cli_output(output) == "Sure!"


def test_ollama_cli_output_cleanup_removes_qwen_thinking_block() -> None:
    output = "Thinking...\nprivate reasoning\n...done thinking.\n\nFinal answer."

    assert clean_ollama_cli_output(output) == "Final answer."
