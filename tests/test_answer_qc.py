from __future__ import annotations

import pytest

from originrag.analysis.answer_qc import (
    annotate_publication_family_relationships,
    assess_answer_quality,
    normalize_inline_evidence_labels,
    unsupported_technical_terms,
)


def test_hypothesis_answer_qc_passes_structured_cited_card() -> None:
    pack = {
        "question": "Make a hypothesis.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Ceesay 2025, Results]",
                "text": "Asymptomatic infections had broader PfEMP1 antibody recognition.",
            }
        ],
    }
    answer = """## Hypothesis Card

### hypothesis_id
TEMP_HYP_001

### title
PfEMP1 antibody breadth marks asymptomatic control.

### short_summary
Asymptomatic infections may reflect broader PfEMP1 antibody recognition (E001).

### evidence_backed_statements
- Asymptomatic infections had broader PfEMP1 antibody recognition (E001).

### inference
- Broader antibody recognition is consistent with asymptomatic control.

### speculation
- The causal mechanism remains uncertain.

### testable_prediction
- Broader PfEMP1 antibody recognition predicts lower risk.

### required_data
- Antibody breadth and clinical phenotype.

### analysis_plan
- Model phenotype by antibody breadth.

### risk_plan_b
- Stratify by age and exposure.

### novelty
- Must be checked against broader literature.

### impact
- Could prioritize immune correlates.

### key_references
- E001: asymptomatic antibody recognition.
"""

    qc = assess_answer_quality(answer, pack, task="hypothesis")

    assert qc["passed"] is True
    assert qc["warnings"] == []
    assert qc["evidence_ids_cited"] == ["E001"]


def test_hypothesis_answer_qc_flags_unstructured_uncited_answer() -> None:
    pack = {
        "question": "Make a hypothesis.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Ceesay 2025, Results]",
                "text": "Asymptomatic infections had broader PfEMP1 antibody recognition.",
            }
        ],
    }
    answer = "This is a general summary without the requested hypothesis card."

    qc = assess_answer_quality(answer, pack, task="hypothesis")

    assert qc["passed"] is False
    assert "missing_required_sections" in qc["warnings"]
    assert "no_evidence_citations" in qc["warnings"]


def test_answer_qc_flags_unknown_citations_and_possible_unsupported_terms() -> None:
    pack = {
        "question": "Make a hypothesis.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Ceesay 2025, Results]",
                "text": "Asymptomatic infections had broader PfEMP1 antibody recognition.",
            }
        ],
    }
    answer = """iSP2 changed in severe disease (E999).

## References
- E999.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["passed"] is False
    assert "unknown_evidence_citations" in qc["warnings"]
    assert "possible_unsupported_terms" in qc["warnings"]
    assert qc["unknown_citations"] == ["E999"]
    assert "iSP2" in qc["possible_unsupported_terms"]


def test_answer_qc_rejects_unlabeled_secondary_source_for_empirical_claim() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": "A review summarizes EPCR-binding variants.",
                "source_type_assessment": {
                    "source_type": "systematic_review_meta_analysis"
                },
            }
        ]
    }
    answer = "EPCR-binding variants were increased in cases (E001).\n\n## References\n- E001."

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "secondary_source_role_unlabeled" in qc["warnings"]
    assert qc["unlabeled_secondary_citations"] == ["E001"]


def test_answer_qc_allows_explicit_secondary_synthesis_claim() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": "A review summarizes EPCR-binding variants.",
                "source_type_assessment": {
                    "source_type": "systematic_review_meta_analysis"
                },
            }
        ]
    }
    answer = "A meta-analysis synthesizes increased EPCR-binding variants (E001).\n\n## References\n- E001."

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "secondary_source_role_unlabeled" not in qc["warnings"]


def test_secondary_role_label_survives_et_al_abbreviation() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": "A review summarizes EPCR-binding variants.",
                "source_type_assessment": {"source_type": "narrative_review"},
            }
        ]
    }
    answer = (
        "Secondary synthesis: Hviid et al. (E001) summarizes increased expression.\n\n"
        "## References\n- E001."
    )

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "secondary_source_role_unlabeled" not in qc["warnings"]


def test_supported_entity_with_blocking_modifier_is_not_new_term() -> None:
    pack = {
        "question": "Does EPCR binding matter?",
        "chunks": [{"evidence_id": "E001", "paper_id": "P1", "text": "EPCR binding"}],
    }
    answer = "The pack does not establish an EPCR-blocking intervention (E001)."

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "EPCR-blocking" not in qc["possible_unsupported_terms"]


def test_sources_qc_allows_paper_level_answer_without_evidence_labels() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Ndugwa 2026, Results]",
                "title": "Expression of specific var gene subtypes is differentially associated with severe malaria syndromes",
                "journal": "eLife",
                "text": "A large Kenyan cohort included severe and non-severe malaria cases.",
            }
        ],
    }
    answer = """## Best match
- Ndugwa 2026. Expression of specific var gene subtypes is differentially associated with severe malaria syndromes. eLife. https://doi.org/10.7554/eLife.110810.1

## Why this looks like the source
- The retrieved text matched the source lookup and names a large Kenyan cohort.

## Other possible matches
- No strong alternative source was retrieved.
"""

    qc = assess_answer_quality(answer, pack, task="sources")

    assert qc["passed"] is True
    assert "no_evidence_citations" not in qc["warnings"]


def test_answer_qc_flags_malformed_evidence_citations() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E005",
                "citation_label": "[Wu 2025, Abstract]",
                "text": "Estrogen has immunomodulatory roles in malaria.",
            }
        ],
    }
    answer = """Estrogen is discussed in the source (E05).

## References
- E05.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["passed"] is False
    assert "malformed_evidence_citations" in qc["warnings"]
    assert qc["malformed_citations"] == ["E05"]


def test_answer_qc_flags_empty_required_sections() -> None:
    pack = {
        "question": "Find research gaps.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Example 2025, Discussion]",
                "text": "The evidence identifies a gap in sex-disaggregated malaria data.",
            }
        ],
    }
    answer = """## Short Answer

## Supported Research Gaps
- gap: Sex-disaggregated data remain limited.
  why it matters: It limits interpretation.
  supporting evidence labels: E001
  what evidence is still missing: Matched data.

## Candidate Testable Questions
- Does sex modify immune marker associations?

## Best Next Data / Analysis
- Matched sex-disaggregated datasets.

## Key Sources
- E001: describes the data gap.
"""

    qc = assess_answer_quality(answer, pack, task="gaps")

    assert qc["passed"] is False
    assert "empty_required_sections" in qc["warnings"]
    assert qc["empty_sections"] == ["## Short Answer"]


def test_answer_qc_rejects_clipped_prose_before_next_heading() -> None:
    pack = {
        "question": "What does the report show about var gene expression?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": (
                    "The report associates var gene expression with clinical "
                    "phenotype, but it does not establish causality."
                ),
            }
        ],
    }
    answer = """## Evidence-backed statements
The report associates var gene expression with clinical phenotype (E001).

## Cautious inference
The association is compatible with a phenotype-linked expression pattern.

## Limitations / uncertainty
The report provides association-level evidence and does not establish causality.

## Missing evidence
The evidence does not establish the causal relationship between specific var gene expression

## References
- E001: Example report.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["passed"] is False
    assert "incomplete_prose_section" in qc["warnings"]
    assert qc["incomplete_prose_sections"] == ["## Missing evidence"]


def test_answer_qc_accepts_completed_prose_before_next_heading() -> None:
    pack = {
        "question": "What does the report show about var gene expression?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": (
                    "The report associates var gene expression with clinical "
                    "phenotype, but it does not establish causality."
                ),
            }
        ],
    }
    answer = """## Evidence-backed statements
The report associates var gene expression with clinical phenotype (E001).

## Cautious inference
The association is compatible with a phenotype-linked expression pattern.

## Limitations / uncertainty
The report provides association-level evidence and does not establish causality.

## Missing evidence
The evidence does not establish the causal relationship between specific var gene expression and the clinical phenotype.

## References
- E001: Example report.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "incomplete_prose_section" not in qc["warnings"]
    assert qc["incomplete_prose_sections"] == []


def test_answer_qc_does_not_treat_unpunctuated_list_items_as_clipped_prose() -> None:
    pack = {"chunks": []}
    answer = """## Evidence-backed statements
- No direct result

## Cautious inference
- No additional inference

## Limitations / uncertainty
- Limited cohort information

## Missing evidence
- Matched longitudinal data
- Functional validation

## References
- No references
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "incomplete_prose_section" not in qc["warnings"]
    assert qc["incomplete_prose_sections"] == []


def test_answer_qc_does_not_flag_plain_lowercase_hyphen_words_as_technical() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Briggs 2020, Abstract]",
                "text": "Sex differences may alter immunity in malaria.",
            }
        ],
    }
    answer = """Sex differences may involve immune-related variation (E001).

## References
- E001.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "possible_unsupported_terms" not in qc["warnings"]


def test_answer_qc_does_not_flag_capitalized_plain_hyphen_words_as_technical() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Briggs 2020, Abstract]",
                "text": "Sex differences may alter immunity in malaria.",
            }
        ],
    }
    answer = """Sex-specific variation is plausible from the retrieved evidence (E001).

## References
- E001.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "possible_unsupported_terms" not in qc["warnings"]


def test_answer_qc_does_not_flag_doi_fragments_from_references() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Wu 2025, Discussion]",
                "title": "The Immunomodulatory Role of Estrogen in Malaria",
                "doi": "10.1002/iid3.70148",
                "text": "Estrogen has immunomodulatory roles in malaria.",
            }
        ],
    }
    answer = """Estrogen has immunomodulatory roles in malaria (E001).

## References
- E001: [Wu 2025, Discussion] The Immunomodulatory Role of Estrogen in Malaria https://doi.org/10.1002/iid3.70148
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "possible_unsupported_terms" not in qc["warnings"]


def test_answer_qc_allows_supported_entity_with_generic_hyphen_modifier() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Example 2025, Results]",
                "text": "PfEMP1 variants are linked to severe malaria pathogenesis.",
            }
        ],
    }
    answer = """PfEMP1-mediated pathogenesis is supported by the retrieved evidence (E001).

## References
- E001: [Example 2025, Results].
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "possible_unsupported_terms" not in qc["warnings"]


@pytest.mark.parametrize("source_wording", [
    "single-cell RNA sequencing",
    "single cell RNA-seq",
    "scRNA-seq",
    "scRNAseq",
    "single‑cell RNA sequencing",
])
def test_german_single_cell_rna_translation_requires_source_wording(source_wording: str) -> None:
    pack = {
        "question": "What is AnnData?",
        "chunks": [{
            "evidence_id": "E001",
            "text": f"AnnData stores annotated {source_wording} data.",
        }],
    }
    answer = """## Evidence-backed statements
AnnData speichert annotierte Einzelzell-RNA-Sequenzierungsdaten (E001).

## Cautious inference
Keine weitere Schlussfolgerung ist erforderlich.

## Limitations / uncertainty
Der vorliegende Ausschnitt ist begrenzt.

## Missing evidence
Weitere Messungen fehlen.

## References
- E001.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["passed"] is True
    assert qc["possible_unsupported_terms"] == []


@pytest.mark.parametrize("source_wording", [
    "single-cell DNA sequencing",
    "single-cell ATAC-seq",
    "RNA sequencing",
    "RNA data from bulk measurements",
    "single-cell sequencing",
    "annotated matrices",
])
def test_german_rna_translation_is_not_licensed_by_other_source_modalities(source_wording: str) -> None:
    pack = {
        "question": "What is scRNA-seq?",
        "chunks": [{"text": f"AnnData stores {source_wording} data."}],
    }

    terms = unsupported_technical_terms(
        "AnnData speichert Einzelzell-RNA-Sequenzierungsdaten.", pack,
    )

    assert "Einzelzell-RNA-Sequenzierungsdaten" in terms


@pytest.mark.parametrize("unknown", [
    "InventedGene999",
    "PhantomTool42",
    "Einzelzell-RNA-FantasieSeq99",
    "Einzelzell-DNA-Sequenzierungsdaten",
])
def test_source_supported_translation_does_not_exempt_other_technical_terms(unknown: str) -> None:
    pack = {"chunks": [{"text": "AnnData stores annotated scRNA-seq data."}]}

    terms = unsupported_technical_terms(
        f"AnnData speichert Einzelzell-RNA-Sequenzierungsdaten mit {unknown}.", pack,
    )

    assert "Einzelzell-RNA-Sequenzierungsdaten" not in terms
    assert unknown in terms


def test_source_title_can_support_an_exact_technical_translation() -> None:
    pack = {"chunks": [{"title": "Single-cell RNA sequencing", "text": "Methods overview."}]}

    assert unsupported_technical_terms("Einzelzell-RNA-Sequenzierung", pack) == []


def test_translation_support_is_not_assembled_from_different_source_fields() -> None:
    pack = {"chunks": [
        {"title": "Single-cell", "text": "RNA sequencing"},
        {"title": "single-cell", "text": "DNA sequencing"},
    ]}

    assert unsupported_technical_terms("Einzelzell-RNA-Sequenzierungsdaten", pack) == [
        "Einzelzell-RNA-Sequenzierungsdaten",
    ]


def test_answer_qc_flags_over_specific_trial_design_not_in_evidence() -> None:
    pack = {
        "question": "Find research gaps in sex differences and malaria.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Wu 2025, Discussion]",
                "text": (
                    "Further research is needed, including clinical trials to assess "
                    "the efficacy and safety of SERMs as potential therapeutic agents."
                ),
            }
        ],
    }
    answer = """## Short Answer
The evidence points to missing interventional evidence (E001).

## Supported Research Gaps
- gap: Randomized controlled trials of SERMs are needed.
  why it matters: It would test translation.
  supporting evidence labels: E001
  what evidence is still missing: Randomized controlled trials.

## Candidate Testable Questions
- Are SERMs effective in malaria?

## Best Next Data / Analysis
- Randomized controlled trials.

## Key Sources
- E001: SERMs are discussed.
"""

    qc = assess_answer_quality(answer, pack, task="gaps")

    assert qc["passed"] is False
    assert "possible_unsupported_phrases" in qc["warnings"]
    assert "randomized controlled trial" in qc["possible_unsupported_phrases"]


def test_answer_qc_allows_trial_design_when_present_in_evidence() -> None:
    pack = {
        "question": "Find research gaps.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Example 2025, Discussion]",
                "text": "A randomized controlled trial is needed to test this intervention.",
            }
        ],
    }
    answer = """## Short Answer
The source directly calls for a randomized controlled trial (E001).

## Supported Research Gaps
- gap: The intervention has not been tested in a randomized controlled trial.
  why it matters: It limits causal inference.
  supporting evidence labels: E001
  what evidence is still missing: A randomized controlled trial.

## Candidate Testable Questions
- Does the intervention improve outcomes?

## Best Next Data / Analysis
- A randomized controlled trial.

## Key Sources
- E001: names the needed trial design.
"""

    qc = assess_answer_quality(answer, pack, task="gaps")

    assert "possible_unsupported_phrases" not in qc["warnings"]


def test_answer_qc_allows_supported_plural_and_compound_terms() -> None:
    pack = {
        "question": "Find gaps in sex differences and immunity.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Example 2025, Discussion]",
                "text": "SERMs, estrogen, and IFN-gamma are discussed as preliminary mechanisms.",
            }
        ],
    }
    answer = """## Short Answer
The retrieved evidence points to preliminary sex-immunity mechanisms (E001).

## Supported Research Gaps
- gap: SERM and estrogen-IFN-gamma mechanisms remain uncertain.
  why it matters: Mechanisms are preliminary.
  supporting evidence labels: E001
  what evidence is still missing: Matched mechanistic data.

## Candidate Testable Questions
- Do estrogen-IFN-gamma patterns differ by sex?

## Best Next Data / Analysis
- Matched mechanistic data.

## Key Sources
- E001: discusses preliminary mechanisms.
"""

    qc = assess_answer_quality(answer, pack, task="gaps")

    assert "possible_unsupported_terms" not in qc["warnings"]


def test_answer_qc_flags_meta_analysis_when_not_supported() -> None:
    pack = {
        "question": "Find gaps in sex differences and malaria.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Example 2025, Discussion]",
                "text": "The evidence calls for standardized sex-disaggregated datasets.",
            }
        ],
    }
    answer = """## Short Answer
The retrieved evidence points to a data harmonization gap (E001).

## Supported Research Gaps
- gap: Sex-disaggregated data are limited.
  why it matters: It limits interpretation.
  supporting evidence labels: E001
  what evidence is still missing: Harmonized data.

## Candidate Testable Questions
- Does sex modify malaria outcomes?

## Best Next Data / Analysis
- Meta-analyses distinguishing sex and gender.

## Key Sources
- E001: identifies the data gap.
"""

    qc = assess_answer_quality(answer, pack, task="gaps")

    assert qc["passed"] is False
    assert "possible_unsupported_phrases" in qc["warnings"]
    assert "meta-analysis" in qc["possible_unsupported_phrases"]


def test_answer_qc_flags_cross_source_serm_chmi_conflation() -> None:
    pack = {
        "question": "Find gaps in sex differences and malaria.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Wu 2025, Discussion]",
                "text": "SERMs are preliminary and malaria-specific clinical trials are absent.",
            },
            {
                "evidence_id": "E002",
                "citation_label": "[Duncombe 2026, Abstract]",
                "text": "CHMI trials in malaria-naive populations can isolate sex effects.",
            },
        ],
    }
    answer = """## Short Answer
The retrieved evidence points to gaps in sex-immunity mechanisms (E001).

## Supported Research Gaps
- gap: SERM trials in malaria-naive populations are missing.
  why it matters: It would isolate sex effects.
  supporting evidence labels: E001, E002
  what evidence is still missing: SERM trial data in malaria-naive populations.

## Candidate Testable Questions
- Are SERMs useful in malaria-naive groups?

## Best Next Data / Analysis
- Clinical trials of SERMs in malaria-naive populations.

## Key Sources
- E001: discusses SERMs.
- E002: discusses CHMI malaria-naive populations.
"""

    qc = assess_answer_quality(answer, pack, task="gaps")

    assert qc["passed"] is False
    assert "possible_cross_source_conflation" in qc["warnings"]
    assert "SERM + malaria-naive" in qc["possible_cross_source_conflations"]


def test_answer_qc_allows_serm_chmi_pair_when_same_evidence_supports_it() -> None:
    pack = {
        "question": "Find gaps.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Example 2026, Results]",
                "text": "A SERM study in CHMI malaria-naive populations is explicitly described.",
            }
        ],
    }
    answer = """## Short Answer
The retrieved evidence identifies a SERM gap in CHMI malaria-naive populations (E001).

## Supported Research Gaps
- gap: SERM CHMI evidence remains incomplete.
  why it matters: It limits interpretation.
  supporting evidence labels: E001
  what evidence is still missing: Complete trial data.

## Candidate Testable Questions
- Does the SERM signal replicate?

## Best Next Data / Analysis
- Complete CHMI data.

## Key Sources
- E001: directly links SERM and CHMI.
"""

    qc = assess_answer_quality(answer, pack, task="gaps")

    assert "possible_cross_source_conflation" not in qc["warnings"]


def test_answer_qc_flags_direct_serm_efficacy_overclaim() -> None:
    pack = {
        "question": "Find gaps.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Wu 2025, Discussion]",
                "text": "SERMs are preliminary and clinical trials are absent.",
            }
        ],
    }
    answer = """## Short Answer
The retrieved evidence points to preliminary SERM evidence (E001).

## Supported Research Gaps
- gap: SERM data are preliminary.
  why it matters: SERMs could address sex-specific immune vulnerabilities.
  supporting evidence labels: E001
  what evidence is still missing: Efficacy and safety data.

## Candidate Testable Questions
- Can SERMs improve outcomes?

## Best Next Data / Analysis
- Clinical trials.

## Key Sources
- E001: says SERM evidence is preliminary.
"""

    qc = assess_answer_quality(answer, pack, task="gaps")

    assert qc["passed"] is False
    assert "possible_intervention_overclaim" in qc["warnings"]


def test_answer_qc_rejects_universal_causality_from_association_only() -> None:
    pack = {
        "question": "Does EPCR binding cause cerebral malaria?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": "EPCR binding was associated with cerebral malaria.",
            }
        ],
    }
    answer = """## Evidence-backed statements
EPCR binding causes every cerebral malaria case (E001).
## Cautious inference
EPCR binding proves direct causality (E001).
## Limitations / uncertainty
Other pathways may exist (E001).
## Missing evidence
More cases would refine estimates (E001).
## References
- E001: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["passed"] is False
    assert "causal_claim_not_supported" in qc["warnings"]
    assert len(qc["unsupported_causal_claims"]) == 2


def test_answer_qc_allows_causal_claim_when_cited_evidence_is_explicitly_causal() -> None:
    pack = {
        "question": "Does receptor blockade cause protection?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": "A randomized intervention prevented pathology and demonstrated a causal effect.",
            }
        ],
    }
    answer = """## Evidence-backed statements
The intervention caused protection in the reported experiment (E001).
## Cautious inference
Generalization remains uncertain (E001).
## Limitations / uncertainty
Only one experiment is available (E001).
## Missing evidence
Independent replication is needed (E001).
## References
- E001: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "causal_claim_not_supported" not in qc["warnings"]


def test_answer_qc_allows_explicit_cause_or_consequence_uncertainty() -> None:
    pack = {
        "question": "Are SPP1 macrophages associated with severity?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": "SPP1 expression was higher in severe than moderate infection.",
            }
        ],
    }
    answer = """## Evidence-backed statements
SPP1 expression was higher in severe infection (E001).
## Cautious inference
This is an association, not evidence of causality (E001).
## Limitations / uncertainty
The evidence does not specify whether SPP1 macrophages are a direct cause of severity or a consequence of severe disease (E001).
## Missing evidence
Longitudinal or interventional evidence is missing (E001).
## References
- E001: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "causal_claim_not_supported" not in qc["warnings"]


def test_epistemic_causal_comparison_requires_and_accepts_explicit_limit() -> None:
    pack = {
        "question": (
            "Compare Sakoguchi et al. (2025) and Kassegne et al. (2025). "
            "Does either paper demonstrate that receptor binding itself causes a "
            "reduction in severe-malaria incidence?"
        ),
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "SAKOGUCHI",
                "text": (
                    "RIFIN bound KIR2DL1 and suppressed activation in a molecular "
                    "signalling assay."
                ),
            },
            {
                "evidence_id": "E002",
                "paper_id": "KASSEGNE",
                "text": (
                    "Antibodies correlated with reduced severe malaria risk, and "
                    "RIFIN bound LILRB1 in structural assays."
                ),
            },
        ],
    }
    answer = """## Evidence-backed statements
Sakoguchi reports receptor binding and molecular signalling effects (E001). Kassegne reports a clinical risk association and a separate receptor-binding assay (E002). Neither paper demonstrates that receptor binding itself causes a reduction in severe-malaria incidence (E001; E002).
## Cautious inference
The molecular and clinical observations can be compared, but they do not form a causal clinical experiment (E001; E002).
## Limitations / uncertainty
This evidence limit does not show that a causal relationship is absent (E001; E002).
## Missing evidence
A binding intervention with a measured severe-malaria incidence endpoint is missing (E001; E002).
## References
- E001: Sakoguchi et al., 2025.
- E002: Kassegne et al., 2025.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["passed"] is True, qc
    assert "causal_claim_not_supported" not in qc["warnings"]
    assert "causal_evidence_question_not_resolved" not in qc["warnings"]


def test_epistemic_causal_comparison_rejects_evasive_or_positive_upgrade() -> None:
    pack = {
        "question": (
            "Compare Sakoguchi et al. (2025) and Kassegne et al. (2025). "
            "Does either paper demonstrate that receptor binding itself causes a "
            "reduction in severe-malaria incidence?"
        ),
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "SAKOGUCHI",
                "text": "RIFIN bound KIR2DL1 in a molecular assay.",
            },
            {
                "evidence_id": "E002",
                "paper_id": "KASSEGNE",
                "text": "Antibodies correlated with reduced severe malaria risk.",
            },
        ],
    }
    wrapper = """## Evidence-backed statements
{claim}
## Cautious inference
The reports use different evidence types (E001; E002).
## Limitations / uncertainty
The designs differ (E001; E002).
## Missing evidence
Further evidence is needed (E001; E002).
## References
- E001: Sakoguchi et al., 2025.
- E002: Kassegne et al., 2025.
"""

    evasive = assess_answer_quality(
        wrapper.format(
            claim="One paper reports binding and one reports a risk association (E001; E002)."
        ),
        pack,
        task="answer",
    )
    positive = assess_answer_quality(
        wrapper.format(
            claim=(
                "Kassegne demonstrates that receptor binding reduces severe-malaria "
                "incidence (E002)."
            )
        ),
        pack,
        task="answer",
    )

    assert "causal_evidence_question_not_resolved" in evasive["warnings"]
    assert evasive["epistemic_causal_comparison_issues"] == [
        "missing_explicit_causal_evidence_limit"
    ]
    assert "causal_evidence_question_not_resolved" in positive["warnings"]
    assert any(
        issue.startswith("positive_clinical_causality_not_supported")
        for issue in positive["epistemic_causal_comparison_issues"]
    )


def test_answer_qc_rejects_marker_scope_relation_upgrade_and_hedged_function() -> None:
    pack = {
        "question": "Which SPP1-positive macrophage signatures occur in severe COVID-19?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "LIAO",
                "text": (
                    "Four macrophage groups were identified: FCN1hi group 1, "
                    "FCN1loSPP1+ group 2, SPP1+ group 3, and FABP4+ group 4. "
                    "Groups 2 and 3 were highly expressed in severe compared with "
                    "moderate COVID-19."
                ),
            }
        ],
    }
    answer = """## Evidence-backed statements
The study identified four SPP1-positive subtypes: FCN1hi group 1, FCN1loSPP1+ group 2, SPP1+ group 3, and FABP4+ group 4 (E001).
Groups 2 and 3 were highly associated with severe rather than moderate COVID-19 (E001).
## Cautious inference
These macrophages may contribute to severe disease (E001).
## Limitations / uncertainty
Only one paper is available (E001).
## Missing evidence
Functional experiments are missing (E001).
## References
- E001: Liao.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "marker_membership_not_supported" in qc["warnings"]
    assert qc["unsupported_marker_memberships"][0]["marker"] == "SPP1"
    assert qc["unsupported_marker_memberships"][0]["unsupported_members"] == [
        "FCN1hi group 1",
        "FABP4+ group 4",
    ]
    assert "relation_level_upgraded" in qc["warnings"]
    assert qc["unsupported_relation_upgrades"][0]["required_relation"] == (
        "comparative expression or enrichment"
    )
    assert "causal_claim_not_supported" in qc["warnings"]
    assert any("may contribute" in claim for claim in qc["unsupported_causal_claims"])


def test_answer_qc_rejects_explicit_comparative_direction_inversion() -> None:
    pack = {
        "question": "Was DC8 var expression higher or lower in severe malaria?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": (
                    "DC8 var expression was significantly lower in children with severe "
                    "malaria than in children with uncomplicated malaria."
                ),
            }
        ],
    }
    answer = """## Evidence-backed statements
DC8 var expression was significantly higher in children with severe malaria than in children with uncomplicated malaria (E001).

## Cautious inference
This is a reported comparison, not a causal result (E001).

## Limitations / uncertainty
The evidence pack contains one study (E001).

## Missing evidence
Independent replication is not available in the retrieved evidence (E001).

## References
- E001: Test.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" in qc["warnings"]
    assert qc["unsupported_relation_directions"][0]["reported_direction"] == "down"
    assert not qc["passed"]


def test_answer_qc_allows_equivalent_comparison_with_reversed_group_order() -> None:
    pack = {
        "question": "How did DC8 var expression differ?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": (
                    "DC8 var expression was higher in children with severe malaria than "
                    "in children with uncomplicated malaria."
                ),
            }
        ],
    }
    answer = """## Evidence-backed statements
DC8 var expression was lower in children with uncomplicated malaria than in children with severe malaria (E001).

## Cautious inference
This is a reported comparison, not a causal result (E001).

## Limitations / uncertainty
The evidence pack contains one study (E001).

## Missing evidence
Independent replication is not available in the retrieved evidence (E001).

## References
- E001: Test.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" not in qc["warnings"]


def test_answer_qc_rejects_null_result_upgraded_to_higher() -> None:
    pack = {
        "question": "Did RIFIN expression differ by severity?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": (
                    "RIFIN expression did not significantly differ between children with "
                    "severe malaria and children with uncomplicated malaria."
                ),
            }
        ],
    }
    answer = """## Evidence-backed statements
RIFIN expression was higher in children with severe malaria than in children with uncomplicated malaria (E001).

## Cautious inference
This is a reported comparison, not a causal result (E001).

## Limitations / uncertainty
The evidence pack contains one study (E001).

## Missing evidence
Independent replication is not available in the retrieved evidence (E001).

## References
- E001: Test.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" in qc["warnings"]
    assert qc["unsupported_relation_directions"][0]["reported_direction"] == "null"


def test_answer_qc_joins_pdf_line_wraps_inside_comparisons() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": (
                    "DC8 var expression was significantly lower in children with severe\n"
                    "malaria than in children with uncompli-\n"
                    "cated malaria."
                ),
            }
        ],
    }
    answer = (
        "DC8 var expression was higher in children with severe malaria than in "
        "children with uncomplicated malaria (E001)."
    )

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" in qc["warnings"]
    assert qc["unsupported_relation_directions"][0]["reported_direction"] == "down"


def test_answer_qc_recognizes_no_statistically_significant_difference() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": (
                    "There was no statistically significant difference in RIFIN "
                    "expression between children with severe malaria and children "
                    "with uncomplicated malaria."
                ),
            }
        ],
    }
    answer = (
        "RIFIN expression was higher in children with severe malaria than in children "
        "with uncomplicated malaria (E001)."
    )

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" in qc["warnings"]
    assert qc["unsupported_relation_directions"][0]["reported_direction"] == "null"


def test_answer_qc_ignores_unrelated_cocitation_when_aligned_source_disagrees() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": (
                    "DC8 var expression was lower in children with severe malaria than "
                    "in children with uncomplicated malaria."
                ),
            },
            {
                "evidence_id": "E002",
                "paper_id": "P2",
                "text": "The second study described the cohort recruitment criteria.",
            },
        ],
    }
    answer = (
        "DC8 var expression was higher in children with severe malaria than in children "
        "with uncomplicated malaria (E001, E002)."
    )

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" in qc["warnings"]
    assert qc["unsupported_relation_directions"][0]["evidence_ids"] == ["E001"]


def test_answer_qc_flags_conflicting_cocitation_without_masking_support() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": (
                    "DC8 var expression was lower in children with severe malaria than "
                    "in children with uncomplicated malaria."
                ),
            },
            {
                "evidence_id": "E002",
                "paper_id": "P2",
                "text": (
                    "DC8 var expression was higher in children with severe malaria than "
                    "in children with uncomplicated malaria."
                ),
            },
        ],
    }
    answer = (
        "DC8 var expression was higher in children with severe malaria than in children "
        "with uncomplicated malaria (E001, E002)."
    )

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" in qc["warnings"]
    assert qc["unsupported_relation_directions"][0]["evidence_ids"] == ["E001"]


def test_answer_qc_flags_group_c_null_source_attached_to_directional_claim() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E002",
                "paper_id": "KAESTLI",
                "text": (
                    "A large proportion of var group C transcripts was found in "
                    "asymptomatic children."
                ),
            },
            {
                "evidence_id": "E007",
                "paper_id": "ROTTMANN",
                "text": (
                    "The var group C and var1-like transcript abundances were similar "
                    "between the three sample groups."
                ),
            },
        ],
    }
    answer = (
        "Additionally, var group B transcripts are significantly more abundant in "
        "clinical malaria, while var group C transcripts are more prevalent in "
        "asymptomatic infections (Kaestli et al., 2006; E002; Rottmann et al., "
        "2006; E007)."
    )

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" in qc["warnings"]
    assert len(qc["unsupported_relation_directions"]) == 1
    conflict = qc["unsupported_relation_directions"][0]
    assert conflict["evidence_ids"] == ["E007"]
    assert conflict["answer_direction"] == "up"
    assert conflict["reported_direction"] == "null"
    assert conflict["required_relation"] == (
        "do not attach E007 to this directional claim; the excerpt reports null for "
        "var group C transcripts"
    )


def test_answer_qc_flags_group_focused_dominance_null_cocitation() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E002",
                "paper_id": "KAESTLI",
                "text": (
                    "A large proportion of var group C transcripts was found in "
                    "asymptomatic children."
                ),
            },
            {
                "evidence_id": "E007",
                "paper_id": "ROTTMANN",
                "text": (
                    "The var group C and var1-like transcript abundances were similar "
                    "between the three sample groups."
                ),
            },
        ],
    }
    answer = (
        "Clinical malaria cases show higher proportions of var group B transcripts, "
        "while asymptomatic infections are marked by a dominance of var group C "
        "transcripts (Kaestli et al., 2006; E002; Rottmann et al., 2006; E007)."
    )

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" in qc["warnings"]
    assert len(qc["unsupported_relation_directions"]) == 1
    conflict = qc["unsupported_relation_directions"][0]
    assert conflict["evidence_ids"] == ["E007"]
    assert conflict["answer_direction"] == "up"
    assert conflict["reported_direction"] == "null"


def test_answer_qc_flags_live_group_focused_dominance_null_cocitation() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E002",
                "paper_id": "KAESTLI",
                "text": (
                    "A large proportion of var group C transcripts was found in "
                    "asymptomatic children."
                ),
            },
            {
                "evidence_id": "E007",
                "paper_id": "ROTTMANN",
                "text": (
                    "The var group C and var1-like transcript abundances were similar "
                    "between the three sample groups."
                ),
            },
        ],
    }
    answer = (
        "Specifically, asymptomatic infections show a more focused expression pattern "
        "with higher var expression homogeneity (VEH) and a dominance of var group C "
        "transcripts, whereas clinical malaria is associated with broader var gene "
        "activation, particularly of group A and B genes (Kaestli et al., 2006; E002; "
        "Rottmann et al., 2006; E007)."
    )

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" in qc["warnings"]
    assert len(qc["unsupported_relation_directions"]) == 1
    conflict = qc["unsupported_relation_directions"][0]
    assert conflict["evidence_ids"] == ["E007"]
    assert conflict["answer_direction"] == "up"
    assert conflict["reported_direction"] == "null"
    assert conflict["required_relation"] == (
        "do not attach E007 to this directional claim; the excerpt reports null for "
        "var group C transcripts"
    )


def test_answer_qc_parses_group_focused_possession_relations() -> None:
    claims = [
        "Asymptomatic infections have enrichment of var group C transcripts (E007).",
        "Asymptomatic infections had a prevalence of var group C transcripts (E007).",
        "Asymptomatic infections presented a dominance of var group C transcripts "
        "(E007).",
    ]
    pack = {
        "chunks": [
            {
                "evidence_id": "E007",
                "paper_id": "ROTTMANN",
                "text": (
                    "The var group C transcript abundances were similar between the "
                    "three sample groups."
                ),
            }
        ],
    }

    for claim in claims:
        qc = assess_answer_quality(claim, pack, task="answer")

        assert "relation_direction_inverted" in qc["warnings"]
        assert qc["unsupported_relation_directions"][0]["evidence_ids"] == ["E007"]


def test_answer_qc_does_not_parse_negated_group_focused_relation() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E007",
                "paper_id": "ROTTMANN",
                "text": (
                    "The var group C transcript abundances were similar between the "
                    "three sample groups."
                ),
            }
        ],
    }
    answer = (
        "Asymptomatic infections did not show a dominance of var group C transcripts "
        "(E007)."
    )

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" not in qc["warnings"]
    assert qc["unsupported_relation_directions"] == []


def test_answer_qc_allows_study_separated_group_c_dominance_and_null() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E002",
                "paper_id": "KAESTLI",
                "text": (
                    "Asymptomatic infections were marked by a dominance of var group C "
                    "transcripts."
                ),
            },
            {
                "evidence_id": "E007",
                "paper_id": "ROTTMANN",
                "text": (
                    "The var group C transcript abundances were similar between the "
                    "three sample groups."
                ),
            },
        ],
    }
    answer = (
        "Asymptomatic infections were marked by a dominance of var group C transcripts "
        "in Kaestli et al. (E002), whereas var group C transcript abundances were "
        "similar between the three sample groups in Rottmann et al. (E007)."
    )

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" not in qc["warnings"]
    assert qc["unsupported_relation_directions"] == []


def test_answer_qc_allows_study_separated_group_c_show_and_null() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E002",
                "paper_id": "KAESTLI",
                "text": (
                    "Asymptomatic infections showed a dominance of var group C "
                    "transcripts."
                ),
            },
            {
                "evidence_id": "E007",
                "paper_id": "ROTTMANN",
                "text": (
                    "The var group C transcript abundances were similar between the "
                    "three sample groups."
                ),
            },
        ],
    }
    answer = (
        "Asymptomatic infections showed a dominance of var group C transcripts in "
        "Kaestli et al. (E002), whereas var group C transcript abundances were similar "
        "between the three sample groups in Rottmann et al. (E007)."
    )

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" not in qc["warnings"]
    assert qc["unsupported_relation_directions"] == []


def test_answer_qc_allows_explicitly_contrasted_group_c_results() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E002",
                "paper_id": "KAESTLI",
                "text": (
                    "Var group C transcripts were more prevalent in asymptomatic "
                    "infections."
                ),
            },
            {
                "evidence_id": "E007",
                "paper_id": "ROTTMANN",
                "text": (
                    "The var group C transcript abundances were similar between the "
                    "three sample groups."
                ),
            },
        ],
    }
    answer = (
        "Var group C transcripts were more prevalent in asymptomatic infections "
        "(E002), whereas var group C transcript abundances were similar between the "
        "three sample groups (E007)."
    )

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" not in qc["warnings"]
    assert qc["unsupported_relation_directions"] == []


def test_answer_qc_keeps_citations_local_to_sentences_on_one_markdown_line() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": (
                    "Feature X was higher in severe malaria than in uncomplicated "
                    "malaria."
                ),
            },
            {
                "evidence_id": "E002",
                "paper_id": "P2",
                "text": (
                    "Feature X did not differ between severe malaria and uncomplicated "
                    "malaria."
                ),
            },
        ],
    }
    answer = (
        "Feature X was higher in severe malaria than in uncomplicated malaria (E001). "
        "Feature X did not differ between severe malaria and uncomplicated malaria "
        "(E002)."
    )

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" not in qc["warnings"]
    assert qc["unsupported_relation_directions"] == []


def test_answer_qc_does_not_confuse_named_var_group_labels() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E007",
                "paper_id": "ROTTMANN",
                "text": (
                    "Var group B transcripts were more abundant in clinical malaria. "
                    "The var group C transcript abundances were similar between the "
                    "three sample groups."
                ),
            }
        ],
    }
    answer = "Var group B transcripts were more abundant in clinical malaria (E007)."

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" not in qc["warnings"]
    assert qc["unsupported_relation_directions"] == []


def test_answer_qc_does_not_conflate_prevalence_and_concentration_endpoints() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": "Antibody prevalence was similar across all clinical groups.",
            }
        ],
    }
    answer = "Antibody concentration was higher in severe malaria (E001)."

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" not in qc["warnings"]
    assert qc["unsupported_relation_directions"] == []


def test_answer_qc_does_not_treat_negated_answer_frame_as_positive_relation() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": (
                    "DC8 var expression was lower in children with severe malaria than "
                    "in children with uncomplicated malaria."
                ),
            }
        ],
    }
    answer = (
        "The study did not show that DC8 var expression was higher in children with "
        "severe malaria than in children with uncomplicated malaria (E001)."
    )

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" not in qc["warnings"]
    assert qc["unsupported_relation_directions"] == []


def test_answer_qc_does_not_extract_positive_relation_from_negated_evidence() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": (
                    "The study did not show that DC8 var expression was higher in "
                    "children with severe malaria than in children with uncomplicated "
                    "malaria."
                ),
            }
        ],
    }
    answer = (
        "DC8 var expression was lower in children with severe malaria than in children "
        "with uncomplicated malaria (E001)."
    )

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "relation_direction_inverted" not in qc["warnings"]
    assert qc["unsupported_relation_directions"] == []


def test_answer_qc_rejects_marker_scope_with_context_qualifier_v3() -> None:
    pack = {
        "question": "Which SPP1-positive macrophage states occur in severe COVID-19?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "LIAO",
                "text": (
                    "Four macrophage groups were identified: FCN1hi group 1, "
                    "FCN1loSPP1+ group 2, SPP1+ group 3, and FABP4+ group 4. "
                    "Groups 2 and 3 were highly expressed in severe or critical COVID-19."
                ),
            }
        ],
    }
    answer = """## Evidence-backed statements
The study identified four SPP1-positive macrophage states in severe/critical COVID-19: FCN1hi group 1, FCN1loSPP1+ group 2, SPP1+ group 3, and FABP4+ group 4 (E001).
SPP1 expression was highly expressed in severe or critical COVID-19 (E001).
## Cautious inference
This is comparative expression (E001).
## Limitations / uncertainty
Only one paper is available (E001).
## Missing evidence
Functional evidence is missing (E001).
## References
- E001: Liao.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "marker_membership_not_supported" in qc["warnings"]
    assert qc["unsupported_marker_memberships"][0]["unsupported_members"] == [
        "FCN1hi group 1",
        "FABP4+ group 4",
    ]
    assert "relation_level_upgraded" in qc["warnings"]
    assert "SPP1 expression was highly expressed" in (
        qc["unsupported_relation_upgrades"][0]["claim"]
    )


def test_answer_qc_flags_omitted_compound_marker_label_v5() -> None:
    pack = {
        "question": "Which SPP1-positive macrophage states occur in severe COVID-19?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "LIAO",
                "text": (
                    "Four macrophage groups were identified: FCN1hi group 1, "
                    "FCN1loSPP1+ group 2, SPP1+ group 3, and FABP4+ group 4. "
                    "Groups 2 and 3 were highly expressed in severe or critical COVID-19."
                ),
            }
        ],
    }
    answer = """## Evidence-backed statements
The explicitly reported SPP1-positive macrophage state was SPP1+ group 3 (E001).
## Cautious inference
This is a descriptive single-paper result (E001).
## Limitations / uncertainty
Independent replication is not established (E001).
## Missing evidence
Functional evidence is missing (E001).
## References
- E001: Liao.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "marker_positive_labels_omitted" in qc["warnings"]
    assert qc["missing_marker_positive_labels"] == [
        {
            "marker": "SPP1",
            "missing_labels": ["FCN1loSPP1+ group 2"],
            "required_labels": [
                "FCN1loSPP1+ group 2",
                "SPP1+ group 3",
            ],
            "label_sources": {"FCN1loSPP1+ group 2": ["E001"]},
        }
    ]


def test_answer_qc_accepts_complete_explicit_marker_label_inventory() -> None:
    pack = {
        "question": "Which SPP1-positive macrophage states occur in severe COVID-19?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "LIAO",
                "text": (
                    "FCN1loSPP1+ group 2 and SPP1+ group 3 were highly expressed "
                    "in severe or critical COVID-19."
                ),
            }
        ],
    }
    answer = """## Evidence-backed statements
The explicitly reported states were FCN1loSPP1+ group 2 and SPP1+ group 3 (E001).
## Cautious inference
This is a descriptive single-paper result (E001).
## Limitations / uncertainty
Independent replication is not established (E001).
## Missing evidence
Functional evidence is missing (E001).
## References
- E001: Liao.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "marker_positive_labels_omitted" not in qc["warnings"]
    assert qc["missing_marker_positive_labels"] == []


def test_marker_scope_keeps_adjacent_uncommaed_group_labels_separate() -> None:
    pack = {
        "question": "Which SPP1-positive macrophage states occur in severe COVID-19?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "LIAO",
                "text": (
                    "Macrophages were grouped as FCN1hi group 1, FCN1loSPP1+ group 2, "
                    "SPP1+ group 3 and FABP4+ group 4 macrophages. FCN1 and SPP1 were "
                    "highly expressed in severe or critical infection."
                ),
            }
        ],
    }
    answer = """## Evidence-backed statements
Four SPP1-positive macrophage states in severe COVID-19: FCN1hi group 1, FCN1loSPP1+ group 2, SPP1+ group 3, and FABP4+ group 4 macrophages (E001).
## Cautious inference
This is descriptive (E001).
## Limitations / uncertainty
One report is available (E001).
## Missing evidence
Functional evidence is missing (E001).
## References
- E001: Liao.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["unsupported_marker_memberships"][0]["unsupported_members"] == [
        "FCN1hi group 1",
        "FABP4+ group 4 macrophages",
    ]


def test_marker_scope_rejects_contrast_group_in_including_list() -> None:
    pack = {
        "question": "What SPP1-positive macrophage states occur in severe COVID-19?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "LIAO",
                "text": (
                    "Macrophages included FCN1loSPP1+ group 2, SPP1+ group 3 and "
                    "FABP4+ group 4. SPP1 was highly expressed in severe infection."
                ),
            }
        ],
    }
    answer = """## Evidence-backed statements
The paper reported SPP1-positive macrophage states in severe COVID-19, including FCN1loSPP1+ group 2, SPP1+ group 3, and FABP4+ group 4 (E001).
## Cautious inference
This is descriptive (E001).
## Limitations / uncertainty
One report is available (E001).
## Missing evidence
Functional evidence is missing (E001).
## References
- E001: Liao.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "marker_membership_not_supported" in qc["warnings"]
    assert qc["unsupported_marker_memberships"][0]["unsupported_members"] == [
        "FABP4+ group 4"
    ]


def test_marker_scope_rejects_direct_expression_assignment_to_contrast_group() -> None:
    pack = {
        "question": "What SPP1-positive macrophage states occur in severe COVID-19?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "LIAO",
                "text": (
                    "Macrophages included FCN1loSPP1+ group 2, SPP1+ group 3 and "
                    "FABP4+ group 4. SPP1 was highly expressed in severe infection."
                ),
            }
        ],
    }
    answer = """## Evidence-backed statements
The paper reported SPP1 expression in macrophage groups 3 and 4 (SPP1+ group 3 and FABP4+ group 4), with FCN1loSPP1+ group 2 also expressing SPP1 (E001).
## Cautious inference
This is descriptive (E001).
## Limitations / uncertainty
One report is available (E001).
## Missing evidence
Functional evidence is missing (E001).
## References
- E001: Liao.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "marker_membership_not_supported" in qc["warnings"]
    assert any(
        finding["unsupported_members"] == ["group 4"]
        for finding in qc["unsupported_marker_memberships"]
    )


def test_answer_qc_does_not_apply_later_unrelated_negation_to_causal_claim() -> None:
    pack = {
        "question": "Are SPP1 macrophages associated with severity?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": "SPP1 expression was higher in severe than moderate infection.",
            }
        ],
    }
    answer = """## Evidence-backed statements
SPP1 drives severity, but the study does not determine whether age confounds the association (E001).
## Cautious inference
The reported result is associative (E001).
## Limitations / uncertainty
The observational comparison cannot establish causality (E001).
## Missing evidence
Longitudinal or interventional evidence is missing (E001).
## References
- E001: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "causal_claim_not_supported" in qc["warnings"]
    assert qc["unsupported_causal_claims"] == [
        "spp1 drives severity, but the study does not determine whether age confounds the association (e001)"
    ]


def test_answer_qc_does_not_carry_negation_across_contrast_clause() -> None:
    pack = {
        "question": "Are SPP1 macrophages associated with severity?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": "SPP1 expression was higher in severe than moderate infection.",
            }
        ],
    }
    answer = """## Evidence-backed statements
The study does not determine whether age confounds the association, but SPP1 drives severity (E001).
## Cautious inference
The reported result is associative (E001).
## Limitations / uncertainty
The observational comparison cannot establish causality (E001).
## Missing evidence
Longitudinal or interventional evidence is missing (E001).
## References
- E001: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "causal_claim_not_supported" in qc["warnings"]


def test_answer_qc_keeps_english_uncertainty_across_complement_comma() -> None:
    pack = {
        "question": "Are SPP1 macrophages associated with severity?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": "SPP1 expression was higher in severe than moderate infection.",
            }
        ],
    }
    answer = """## Evidence-backed statements
It is uncertain, whether SPP1 causes severity or reflects severe disease (E001).
## Cautious inference
The reported result is associative (E001).
## Limitations / uncertainty
The direction of the association remains uncertain (E001).
## Missing evidence
Longitudinal or interventional evidence is missing (E001).
## References
- E001: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "causal_claim_not_supported" not in qc["warnings"]
    assert qc["unsupported_causal_claims"] == []


def test_answer_qc_keeps_german_uncertainty_across_complement_comma() -> None:
    pack = {
        "question": "Sind SPP1-Makrophagen mit Schwere assoziiert?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "text": "SPP1 war bei schwerer Erkrankung höher exprimiert.",
            }
        ],
    }
    answer = """## Evidence-backed statements
Es ist unklar, ob SPP1 die Schwere verursacht oder eine Folge schwerer Krankheit ist (E001).
## Cautious inference
Das berichtete Ergebnis ist eine Assoziation (E001).
## Limitations / uncertainty
Die Richtung der Assoziation bleibt unklar (E001).
## Missing evidence
Longitudinale oder interventionelle Evidenz fehlt (E001).
## References
- E001: Quelle.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "causal_claim_not_supported" not in qc["warnings"]
    assert qc["unsupported_causal_claims"] == []


def test_hypothesis_qc_flags_novelty_overclaim() -> None:
    pack = {
        "question": "Make a hypothesis.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Ceesay 2025, Results]",
                "text": "Asymptomatic infections had broader PfEMP1 antibody recognition.",
            }
        ],
    }
    answer = """## Hypothesis Card

### hypothesis_id
TEMP_HYP_001

### title
PfEMP1 antibody breadth marks asymptomatic control.

### short_summary
Asymptomatic infections may reflect broader PfEMP1 antibody recognition (E001).

### evidence_backed_statements
- Asymptomatic infections had broader PfEMP1 antibody recognition (E001).

### inference
- Broader antibody recognition may contribute to control (E001).

### speculation
- Causality remains uncertain (E001).

### testable_prediction
- Broader recognition predicts phenotype (E001).

### required_data
- Antibody and phenotype data.

### analysis_plan
- Model phenotype by antibody breadth.

### risk_plan_b
- Stratify by exposure.

### novelty
This is a novel connection supported by the evidence.

### impact
- Could prioritize immune correlates.

### key_references
- E001.
"""

    qc = assess_answer_quality(answer, pack, task="hypothesis")

    assert qc["passed"] is False
    assert "novelty_overclaim" in qc["warnings"]


def test_answer_qc_flags_unrequested_inheritance_reinterpretation() -> None:
    pack = {
        "question": "Welche var-Gene korrelieren mit klinischen Syndromen?",
        "chunks": [{"evidence_id": "E001", "text": "Group A var genes correlate with severe malaria."}],
    }
    answer = """## Evidence-backed statements
- Group A var genes correlate with severe malaria (E001).

## Cautious inference
No hereditary syndrome can be inferred.

## Limitations / uncertainty
Evidence is associative (E001).

## Missing evidence
Direct causality is not established (E001).

## References
- E001: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["passed"] is False
    assert "possible_question_scope_drift" in qc["warnings"]


def test_answer_qc_flags_mechanism_absence_claim_when_pack_contains_direct_assay() -> None:
    pack = {
        "question": "What evidence links EPCR-binding PfEMP1 to severe malaria?",
        "chunks": [{
            "evidence_id": "E001",
            "text": "Surface plasmon resonance showed binding, and a blocking antibody inhibited binding to endothelial cells.",
        }],
    }
    answer = """## Evidence-backed statements
PfEMP1 bound EPCR in an assay (Turner et al., 2013; E001).

## Cautious inference
The clinical consequence remains uncertain (Turner et al., 2013; E001).

## Limitations / uncertainty
The evidence does not establish every downstream step (Turner et al., 2013; E001).

## Missing evidence
There is no direct experimental evidence showing the molecular mechanism.

## References
- E001: [Turner et al., 2013] - receptor-binding assay.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["passed"] is False
    assert "mechanism_evidence_contradicted" in qc["warnings"]
    assert "surface plasmon resonance" in qc["possible_mechanism_absence_overclaims"]


def test_answer_qc_reports_breadth_without_treating_paper_count_as_sufficiency() -> None:
    pack = {
        "question": "Synthesize the evidence.",
        "chunks": [
            {"evidence_id": f"E{index:03d}", "paper_id": f"P{index:03d}", "text": "Direct evidence."}
            for index in range(1, 13)
        ],
    }
    answer = """## Evidence-backed statements
Evidence was reported (Smith et al., 2020; E001).
## Cautious inference
Inference (Smith et al., 2020; E001).
## Limitations / uncertainty
Limited (Smith et al., 2020; E001).
## Missing evidence
More evidence is needed (Smith et al., 2020; E001).
## References
- E001: Smith 2020.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["passed"] is True
    assert "insufficient_citation_breadth" not in qc["warnings"]
    assert qc["citation_breadth_target"] == 8
    assert qc["citation_breadth_advisory"] is True
    assert "not evidence sufficiency" in qc["citation_breadth_semantics"]
    assert qc["cited_paper_count"] == 1


def test_answer_qc_rejects_raw_report_count_after_same_study_admission() -> None:
    pack = {
        "question": "Do these reports describe the same study, and how many independent origins remain?",
        "chunks": [
            {
                "evidence_id": f"E{index:03d}",
                "paper_id": f"P{index:03d}",
                "text": "Evidence.",
            }
            for index in range(1, 6)
        ],
    }
    answer = """## Evidence-backed statements
The journal article is likely the peer-reviewed version of the preprint. Five independent studies remain (E001).
## Cautious inference
They are related (E001).
## Limitations / uncertainty
Other relations are unknown (E001).
## Missing evidence
Origin adjudication is missing (E001).
## References
- E001: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["passed"] is False
    assert "origin_count_ignores_same_study_relation" in qc["warnings"]
    assert qc["origin_count_same_study_contradiction"]["claimed_independent_origin_count"] == 5
    assert qc["origin_count_same_study_contradiction"]["raw_report_count"] == 5


def test_answer_qc_rejects_independence_claim_when_lineage_is_unresolved() -> None:
    pack = {
        "question": "Is the EPCR result real or are papers copying each other?",
        "chunks": [
            {"evidence_id": "E001", "paper_id": "P001", "text": "EPCR binding was measured."}
        ],
        "trace_robust_lineage": {"status": "insufficient_even_optimistically"},
    }
    answer = """## Evidence-backed statements
EPCR binding was measured (Smith et al., 2020; E001).
## Cautious inference
The result is consistent across multiple independent studies (Smith et al., 2020; E001).
## Limitations / uncertainty
Other relations remain uncertain (Smith et al., 2020; E001).
## Missing evidence
Origin adjudication is missing (Smith et al., 2020; E001).
## References
- E001: Smith 2020.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["passed"] is False
    assert "independent_support_not_certified" in qc["warnings"]
    assert qc["unsupported_independence_claims"] == [
        "across multiple independent studies"
    ]


def test_robust_sufficiency_does_not_certify_independence_with_unknown_pairs() -> None:
    pack = {
        "question": "Are these studies independent?",
        "chunks": [
            {"evidence_id": "E001", "paper_id": "P001", "text": "Evidence."}
        ],
        "trace_robust_lineage": {
            "status": "robustly_sufficient",
            "missing_pair_count": 1,
            "uncertain_pair_count": 1,
        },
    }
    answer = """## Evidence-backed statements
Evidence was reported (E001).
## Cautious inference
The result is supported across multiple independent studies (E001).
## Limitations / uncertainty
Other limitations remain (E001).
## Missing evidence
More evidence is needed (E001).
## References
- E001: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "independent_support_not_certified" in qc["warnings"]
    assert "lineage_uncertainty_not_disclosed" in qc["warnings"]


def test_prior_disclaimer_does_not_hide_later_positive_independence_claim() -> None:
    pack = {
        "question": "Are these studies independent?",
        "chunks": [{"evidence_id": "E001", "paper_id": "P001", "text": "Evidence."}],
        "trace_robust_lineage": {
            "status": "lineage_review_required",
            "missing_pair_count": 1,
            "uncertain_pair_count": 1,
        },
    }
    answer = """## Evidence-backed statements
Evidence was reported (E001).
## Cautious inference
Independence is unresolved. Nevertheless, support exists across multiple independent studies (E001).
## Limitations / uncertainty
Study origins are unresolved (E001).
## Missing evidence
Origin adjudication is needed (E001).
## References
- E001: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "independent_support_not_certified" in qc["warnings"]


def test_answer_qc_allows_explicitly_unresolved_independence() -> None:
    pack = {
        "question": "Are these studies independent?",
        "chunks": [
            {"evidence_id": "E001", "paper_id": "P001", "text": "Evidence."}
        ],
        "trace_robust_lineage": {"status": "insufficient_even_optimistically"},
    }
    answer = """## Evidence-backed statements
Evidence was reported (Smith et al., 2020; E001).
## Cautious inference
Support across independent studies is not certified (Smith et al., 2020; E001).
## Limitations / uncertainty
Independence is unresolved (Smith et al., 2020; E001).
## Missing evidence
Origin adjudication is missing (Smith et al., 2020; E001).
## References
- E001: Smith 2020.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "independent_support_not_certified" not in qc["warnings"]


def test_answer_qc_rejects_replication_absence_when_lineage_is_unresolved() -> None:
    pack = {
        "question": "How strong is the evidence?",
        "chunks": [
            {"evidence_id": "E001", "paper_id": "P001", "text": "Evidence."}
        ],
        "trace_robust_lineage": {
            "status": "lineage_review_required",
            "missing_pair_count": 1,
            "uncertain_pair_count": 1,
        },
    }
    answers = (
        "No independent replication exists (E001).",
        "Independent replication is lacking (E001).",
        "Evidence for independent replication is absent (E001).",
        "The lack of independent replication limits confidence (E001).",
        "These findings have not been independently replicated (E001).",
        "The result lacks independent confirmation (E001).",
        (
            "Confidence is limited by the lack of independent replication across "
            "studies (E001)."
        ),
        "Ein Mangel an unabhängiger Replikation begrenzt die Evidenzstärke (E001).",
        "Das Fehlen einer unabhängigen Replikation begrenzt die Evidenz (E001).",
    )

    for claim in answers:
        answer = f"""## Evidence-backed statements
Evidence was reported (E001).
## Cautious inference
The result remains provisional (E001).
## Limitations / uncertainty
{claim}
## Missing evidence
Study-origin adjudication is needed (E001).
## References
- E001: source.
"""

        qc = assess_answer_quality(answer, pack, task="answer")

        assert "independent_replication_absence_not_established" in qc["warnings"]
        assert qc["unsupported_independence_absence_claims"]


def test_prior_lineage_caveat_does_not_excuse_replication_absence_claim() -> None:
    pack = {
        "question": "Are the reports independent?",
        "chunks": [{"evidence_id": "E001", "paper_id": "P001", "text": "Evidence."}],
        "trace_robust_lineage": {
            "status": "lineage_review_required",
            "missing_pair_count": 1,
            "uncertain_pair_count": 1,
        },
    }
    answer = """## Evidence-backed statements
Evidence was reported (E001).
## Cautious inference
Study-origin independence is unresolved. Therefore, no independent replication exists (E001).
## Limitations / uncertainty
Origin relations are not certified (E001).
## Missing evidence
Study-origin adjudication is needed (E001).
## References
- E001: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "independent_replication_absence_not_established" in qc["warnings"]


def test_answer_qc_allows_epistemically_bounded_replication_statement() -> None:
    pack = {
        "question": "How strong is the evidence?",
        "chunks": [
            {"evidence_id": "E001", "paper_id": "P001", "text": "Evidence."}
        ],
        "trace_robust_lineage": {
            "status": "lineage_review_required",
            "missing_pair_count": 1,
            "uncertain_pair_count": 1,
        },
    }
    answer = """## Evidence-backed statements
Evidence was reported (E001).
## Cautious inference
The result remains provisional (E001).
## Limitations / uncertainty
No independent replication has been established because study-origin relations are unresolved (E001).
## Missing evidence
Study-origin adjudication is needed (E001).
## References
- E001: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "independent_replication_absence_not_established" not in qc["warnings"]
    assert qc["unsupported_independence_absence_claims"] == []

    epistemic_answer = answer.replace(
        "No independent replication has been established because study-origin "
        "relations are unresolved",
        "It is not established whether these findings have been independently replicated",
    )
    epistemic_qc = assess_answer_quality(epistemic_answer, pack, task="answer")

    assert (
        "independent_replication_absence_not_established"
        not in epistemic_qc["warnings"]
    )
    assert epistemic_qc["unsupported_independence_absence_claims"] == []


def test_answer_qc_allows_exact_cited_source_quote_about_replication_absence() -> None:
    pack = {
        "question": "What did the review state?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P001",
                "text": 'The review concluded: "No independent replication was available."',
            }
        ],
        "trace_robust_lineage": {
            "status": "lineage_review_required",
            "missing_pair_count": 1,
            "uncertain_pair_count": 1,
        },
    }
    answer = """## Evidence-backed statements
The review states, “No independent replication was available” (E001).
## Cautious inference
This is the review's statement, not a lineage certificate (E001).
## Limitations / uncertainty
Study-origin relations remain unresolved (E001).
## Missing evidence
Independent origin adjudication is needed (E001).
## References
- E001: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "independent_replication_absence_not_established" not in qc["warnings"]
    assert qc["unsupported_independence_absence_claims"] == []


def test_exact_quote_does_not_exempt_unquoted_replication_absence_claim() -> None:
    pack = {
        "question": "What did the review state?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P001",
                "text": 'The review concluded: "No independent replication was available."',
            }
        ],
        "trace_robust_lineage": {
            "status": "lineage_review_required",
            "missing_pair_count": 1,
            "uncertain_pair_count": 1,
        },
    }
    answer = """## Evidence-backed statements
No independent replication exists, consistent with the review statement, “No independent replication was available” (E001).
## Cautious inference
The result remains provisional (E001).
## Limitations / uncertainty
Study-origin relations remain unresolved (E001).
## Missing evidence
Independent origin adjudication is needed (E001).
## References
- E001: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "independent_replication_absence_not_established" in qc["warnings"]
    assert qc["unsupported_independence_absence_claims"] == [
        (
            "No independent replication exists, consistent with the review statement, "
            "“No independent replication was available” (E001)."
        )
    ]


def test_answer_qc_requires_lineage_caveat_for_copying_question() -> None:
    pack = {
        "question": "Is EPCR real or are the papers copying each other?",
        "chunks": [
            {"evidence_id": "E001", "paper_id": "P001", "text": "EPCR binding was measured."}
        ],
        "trace_robust_lineage": {"status": "insufficient_even_optimistically"},
    }
    answer = """## Evidence-backed statements
EPCR binding was measured (Smith et al., 2020; E001).
## Cautious inference
The result is biologically plausible (Smith et al., 2020; E001).
## Limitations / uncertainty
Causality is unresolved (Smith et al., 2020; E001).
## Missing evidence
More assays are needed (Smith et al., 2020; E001).
## References
- E001: Smith 2020.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "lineage_uncertainty_not_disclosed" in qc["warnings"]
    assert qc["lineage_uncertainty_not_disclosed"] is True


def test_lineage_caveat_is_inserted_into_limitations() -> None:
    from originrag.analysis.answer_qc import (
        ensure_required_lineage_caveat,
        missing_required_lineage_caveat,
    )

    pack = {
        "question": "Are the papers copying each other?",
        "trace_robust_lineage": {"status": "insufficient_even_optimistically"},
    }
    answer = """## Evidence-backed statements
Evidence exists.
## Limitations / uncertainty
Causality remains unresolved.
"""

    normalized = ensure_required_lineage_caveat(answer, pack)

    assert normalized is not None
    assert "Study-origin independence" in normalized
    assert missing_required_lineage_caveat(normalized, pack) is False


def test_answer_qc_rejects_invented_retrieved_paper_count() -> None:
    pack = {
        "question": "Do five papers mean five independent studies?",
        "n_unique_papers": 12,
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P001",
                "text": "EPCR binding was measured.",
            }
        ],
        "trace_robust_lineage": {"status": "insufficient_even_optimistically"},
    }
    answer = """## Evidence-backed statements
The system retrieved five papers about EPCR (Smith et al., 2020; E001).
## Cautious inference
The exact independent count is unresolved (Smith et al., 2020; E001).
## Limitations / uncertainty
Study-origin independence is not certified (Smith et al., 2020; E001).
## Missing evidence
Origin adjudication is missing (Smith et al., 2020; E001).
## References
- E001: Smith 2020.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["passed"] is False
    assert "retrieved_report_count_mismatch" in qc["warnings"]
    assert qc["retrieved_report_count_mismatch"]["claimed_report_count"] == 5
    assert qc["retrieved_report_count_mismatch"]["actual_unique_paper_count"] == 12


def test_answer_qc_rejects_claim_that_present_doi_is_absent() -> None:
    pack = {
        "question": "Is DOI 10.1093/infdis/jiac086 represented?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P001",
                "doi": "10.1093/infdis/jiac086",
                "text": "Journal evidence.",
            }
        ],
    }
    answer = """## Evidence-backed statements
DOI 10.1093/infdis/jiac086 is not referenced in any evidence item (E001).
## Cautious inference
It may be absent (E001).
## Limitations / uncertainty
The evidence is limited (E001).
## Missing evidence
No evidence item contains the DOI (E001).
## References
- E001: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["passed"] is False
    assert "evidence_identifier_contradicted" in qc["warnings"]
    assert qc["contradicted_evidence_identifiers"] == ["10.1093/infdis/jiac086"]


def test_answer_qc_rejects_claim_that_present_doi_is_not_mentioned() -> None:
    pack = {
        "question": "Compare DOI 10.1093/infdis/jiac086.",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P001",
                "doi": "10.1093/infdis/jiac086",
                "text": "Journal evidence.",
            }
        ],
    }
    answer = "DOI 10.1093/infdis/jiac086 is not mentioned in any evidence item (E001)."

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "evidence_identifier_contradicted" in qc["warnings"]


def test_answer_qc_rejects_chunks_presented_as_five_reports() -> None:
    pack = {
        "question": "How many independent reports remain?",
        "chunks": [
            {"evidence_id": "E001", "paper_id": "P1", "text": "Version one."},
            {"evidence_id": "E002", "paper_id": "P1", "text": "Version one continued."},
            {"evidence_id": "E003", "paper_id": "P2", "text": "Version two."},
            {"evidence_id": "E004", "paper_id": "P3", "text": "Independent report."},
            {"evidence_id": "E005", "paper_id": "P4", "text": "Independent report."},
            {"evidence_id": "E006", "paper_id": "P5", "text": "Independent report."},
        ],
    }
    answer = """## Evidence-backed statements
All five retrieved reports (E001, E002, E003) derive from one publication.
## Cautious inference
The versions are related (E001).
## Limitations / uncertainty
Other papers remain (E004).
## Missing evidence
Origin review is missing (E005).
## References
- E001: source.
- E002: source.
- E003: source.
- E004: source.
- E005: source.
"""

    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["passed"] is False
    assert "evidence_chunks_misreported_as_reports" in qc["warnings"]
    assert qc["report_chunk_unit_conflation"]["represented_paper_count"] == 2


def test_gap_qc_rejects_broken_citations_and_incomplete_key_sources() -> None:
    pack = {
        "question": "Find gaps in parasite expression and clinical syndromes.",
        "chunks": [
            {
                "evidence_id": "E005",
                "paper_id": "P5",
                "citation_label": "[Milne et al., 2021, Discussion]",
                "text": "No preferential expansion was observed in this challenge model.",
            }
        ],
    }
    answer = """## Short Answer
The retrieved evidence is limited (E005).
## Supported Research Gaps
- Supporting evidence: E005 (one open parenthesis.
## Candidate Testable Questions
- Is the pattern replicated?
## Best Next Data / Analysis
- A matched cohort.
## Key Sources
- E005: Direct
"""

    qc = assess_answer_quality(answer, pack, task="gaps")

    assert qc["passed"] is False
    assert "unbalanced_delimiters" in qc["warnings"]
    assert "incomplete_key_sources" in qc["warnings"]


def test_gap_qc_accepts_complete_citation_label() -> None:
    pack = {
        "question": "Find gaps.",
        "chunks": [
            {
                "evidence_id": "E005",
                "paper_id": "P5",
                "citation_label": "[Milne et al., 2021, Discussion]",
                "text": "A limitation was reported.",
            }
        ],
    }
    answer = """## Short Answer
The retrieved evidence reports a limitation (E005).
## Supported Research Gaps
- The reported limitation remains (E005).
## Candidate Testable Questions
- Is the result replicated?
## Best Next Data / Analysis
- A matched cohort.
## Key Sources
- E005: [Milne et al., 2021, Discussion] - reports the limitation.
"""

    qc = assess_answer_quality(answer, pack, task="gaps")

    assert "unbalanced_delimiters" not in qc["warnings"]
    assert "incomplete_key_sources" not in qc["warnings"]


def test_answer_qc_rejects_two_publication_versions_as_unlabeled_support() -> None:
    pack = {
        "question": "What differs between the groups?",
        "chunks": [
            {"evidence_id": "E001", "paper_id": "P_PRE", "text": "Preprint result."},
            {"evidence_id": "E002", "paper_id": "P_JOURNAL", "text": "Journal result."},
        ],
        "automatic_lineage_signals": [
            {
                "paper_id_a": "P_PRE",
                "paper_id_b": "P_JOURNAL",
                "signal_type": "same_preprint_family",
            }
        ],
    }
    answer = "The result was reported twice (E001; E002)."

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "publication_family_cited_as_separate_support" in qc["warnings"]
    assert qc["unlabeled_publication_family_citations"] == ["P_JOURNAL|P_PRE"]


def test_answer_qc_accepts_explicitly_labeled_preprint_journal_relationship() -> None:
    pack = {
        "question": "What differs between the groups?",
        "chunks": [
            {"evidence_id": "E001", "paper_id": "P_PRE", "text": "Preprint result."},
            {"evidence_id": "E002", "paper_id": "P_JOURNAL", "text": "Journal result."},
        ],
        "automatic_lineage_signals": [
            {
                "paper_id_a": "P_PRE",
                "paper_id_b": "P_JOURNAL",
                "signal_type": "same_preprint_family",
            }
        ],
    }
    answer = "The related preprint and journal version report the result (E001; E002)."

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "publication_family_cited_as_separate_support" not in qc["warnings"]
    assert qc["unlabeled_publication_family_citations"] == []


def test_deterministic_annotation_exposes_cited_publication_family() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P_PRE",
                "citation_label": "[Ceesay 2024, Abstract]",
                "text": "Preprint result.",
            },
            {
                "evidence_id": "E002",
                "paper_id": "P_JOURNAL",
                "citation_label": "[Ceesay 2025, Results]",
                "text": "Journal result.",
            },
        ],
        "automatic_lineage_signals": [
            {
                "paper_id_a": "P_PRE",
                "paper_id_b": "P_JOURNAL",
                "signal_type": "same_preprint_family",
            }
        ],
    }
    answer = "A result (E001; E002).\n\n## References\n- E001\n- E002"

    annotated = annotate_publication_family_relationships(answer, pack)

    assert annotated is not None
    assert "## Publication-version note" in annotated
    assert "Ceesay 2024 (E001) and Ceesay 2025 (E002)" in annotated
    assert "one publication family, not independent support" in annotated
    assert annotated.index("## Publication-version note") < annotated.index("## References")
    qc = assess_answer_quality(annotated, pack, task="answer")
    assert "publication_family_cited_as_separate_support" not in qc["warnings"]


def _evidence_strength_comparison_pack() -> dict:
    question = (
        "What changes in var gene expression distinguish asymptomatic dry-season "
        "Plasmodium falciparum infections from clinical malaria, and how strong is "
        "the evidence?"
    )
    return {
        "question": question,
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P_CEESAY_JOURNAL",
                "citation_label": "[Ceesay et al., 2025, Discussion]",
                "section": "discussion",
                "quality_flags": [],
                "text": (
                    "We found no significant difference in var UPS type expression "
                    "between clinical and asymp tomatic infections."
                ),
            },
            {
                "evidence_id": "E002",
                "paper_id": "P_KAESTLI",
                "citation_label": "[Kaestli et al., 2006, Abstract]",
                "section": "abstract",
                "quality_flags": [],
                "text": (
                    "A higher proportion of var group B transcripts occurred in children "
                    "with clinical malaria, whereas var group C transcripts occurred in "
                    "children with asymptomatic infection."
                ),
            },
            {
                "evidence_id": "E006",
                "paper_id": "P_CONTEXT_1",
                "section": "results",
                "quality_flags": [],
                "text": "Parasites were sampled during the dry season.",
            },
            {
                "evidence_id": "E007",
                "paper_id": "P_ROTTMANN",
                "citation_label": "[Rottmann et al., 2006, Abstract]",
                "section": "abstract",
                "quality_flags": [],
                "text": (
                    "Transcript abundances of var group A and B genes were higher for "
                    "children with clinical malaria than for children with asymptomatic "
                    "infections."
                ),
            },
            {
                "evidence_id": "E008",
                "paper_id": "P_CONTEXT_2",
                "section": "results",
                "quality_flags": [],
                "text": "Parasite age was estimated.",
            },
            {
                "evidence_id": "E009",
                "paper_id": "P_CEESAY_PREPRINT",
                "section": "methods",
                "quality_flags": ["methods_like"],
                "text": (
                    "Asymptomatic infections had fewer, more dominant var variants "
                    "compared with clinical infections."
                ),
            },
            {
                "evidence_id": "E010",
                "paper_id": "P_FLORINI",
                "citation_label": "[Florini et al., 2025, Results]",
                "section": "results",
                "quality_flags": ["figure_like"],
                "text": (
                    "Isolates from children with asymptomatic infection have more focused "
                    "expression of var genes than those from children with clinical malaria. "
                    "Figure 2 shows individual points."
                ),
            },
            {
                "evidence_id": "E011",
                "paper_id": "P_PARASITEMIA",
                "section": "results",
                "quality_flags": [],
                "text": (
                    "Parasitemia was lower in asymptomatic infections than in clinical "
                    "malaria."
                ),
            },
        ],
        "automatic_lineage_signals": [
            {
                "paper_id_a": "P_CEESAY_JOURNAL",
                "paper_id_b": "P_CEESAY_PREPRINT",
                "signal_type": "same_preprint_family",
            }
        ],
    }


def test_evidence_strength_qc_flags_missing_direct_comparison_results() -> None:
    pack = _evidence_strength_comparison_pack()
    answer = "Findings were mixed (E001; E002; E006; E008; E009)."

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "direct_comparison_evidence_omitted" in qc["warnings"]
    assert [
        group["evidence_ids"]
        for group in qc["missing_direct_comparison_candidate_groups"]
    ] == [["E007"], ["E010"]]
    assert [
        group["result_sentences"]
        for group in qc["missing_direct_comparison_candidate_groups"]
    ] == [
        [
            {
                "evidence_id": "E007",
                "sentence": (
                    "Transcript abundances of var group A and B genes were higher for "
                    "children with clinical malaria than for children with asymptomatic "
                    "infections."
                ),
            }
        ],
        [
            {
                "evidence_id": "E010",
                "sentence": (
                    "Isolates from children with asymptomatic infection have more focused "
                    "expression of var genes than those from children with clinical malaria."
                ),
            }
        ],
    ]


def test_evidence_strength_qc_collapses_publication_versions() -> None:
    pack = _evidence_strength_comparison_pack()
    pack["chunks"] = [pack["chunks"][0], {**pack["chunks"][5], "section": "results", "quality_flags": []}]
    answer = "The direct comparison found no expression difference (E001)."

    qc = assess_answer_quality(answer, pack, task="answer")

    assert "direct_comparison_evidence_omitted" not in qc["warnings"]
    assert qc["missing_direct_comparison_candidate_groups"] == []


def test_direct_comparison_qc_is_not_a_quota_for_ordinary_questions() -> None:
    pack = _evidence_strength_comparison_pack()
    pack["question"] = (
        "What var gene expression changes distinguish asymptomatic infections from "
        "clinical malaria?"
    )

    qc = assess_answer_quality("One result was reported (E001).", pack, task="answer")

    assert "direct_comparison_evidence_omitted" not in qc["warnings"]
    assert qc["missing_direct_comparison_candidate_groups"] == []


def test_reference_list_citations_do_not_satisfy_direct_comparison_qc() -> None:
    pack = _evidence_strength_comparison_pack()
    answer = "A direct result was reported (E001).\n\n## References\n- E002\n- E007\n- E010"

    qc = assess_answer_quality(answer, pack, task="answer")

    assert [
        group["evidence_ids"]
        for group in qc["missing_direct_comparison_candidate_groups"]
    ] == [["E002"], ["E007"], ["E010"]]


def test_named_report_comparison_does_not_activate_group_coverage_qc() -> None:
    pack = _evidence_strength_comparison_pack()
    pack["question"] = (
        "Compare Smith et al. (2024) and Jones et al. (2025) for asymptomatic "
        "infection versus clinical malaria: how strong is the evidence?"
    )

    qc = assess_answer_quality("One report was summarized (E001).", pack, task="answer")

    assert "direct_comparison_evidence_omitted" not in qc["warnings"]


def test_inline_label_is_normalized_from_the_cited_paper() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E005",
                "paper_id": "P_PRE",
                "citation_label": "[Ceesay 2024, Abstract]",
            }
        ]
    }

    answer = normalize_inline_evidence_labels(
        "The preprint reports narrower breadth (Ceesay et al., 2025; E005).", pack
    )

    assert answer == "The preprint reports narrower breadth (Ceesay 2024; E005)."


def test_bare_inline_id_with_locator_is_not_expanded_to_a_duplicate_label() -> None:
    pack = {
        "chunks": [
            {
                "evidence_id": "E005",
                "paper_id": "P_PRE",
                "citation_label": "[Ceesay 2024, Discussion, pages 3-4]",
            }
        ]
    }

    answer = normalize_inline_evidence_labels(
        "The preprint reports narrower breadth (E005, Discussion, pages 3-4).", pack
    )

    assert answer == "The preprint reports narrower breadth (E005, Discussion, pages 3-4)."


def test_answer_qc_rejects_dominant_gene_subclass_substitution() -> None:
    pack = {
        "chunks": [
            {"evidence_id": "E001", "paper_id": "P1", "text": "One or a few dominant var genes."}
        ]
    }

    qc = assess_answer_quality(
        "Asymptomatic infections had one or a few dominant var subclasses (E001).",
        pack,
        task="answer",
    )

    assert "var_gene_subclass_conflation" in qc["warnings"]


def test_data_accessibility_qc_requires_every_repository_identifier_and_citation() -> None:
    pack = {
        "question": "Which datasets are accessible for reanalysis?",
        "chunks": [
            {
                "evidence_id": "E010",
                "paper_id": "P1",
                "text": "Data are deposited under PRJNA679547.",
            },
            {
                "evidence_id": "E012",
                "paper_id": "P2",
                "text": "RNA-seq data are in GEO repository GSE186820.",
            },
        ],
    }

    qc = assess_answer_quality(
        "The only accessible dataset is PRJNA679547 (E010). No other evidence exists.",
        pack,
        task="answer",
    )

    assert "repository_identifier_omitted" in qc["warnings"]
    assert qc["missing_repository_identifiers"] == [
        {
            "identifier": "GSE186820",
            "evidence_id": "E012",
            "reason": "identifier_not_named",
        }
    ]


def test_data_accessibility_qc_accepts_named_and_cited_identifiers() -> None:
    pack = {
        "question": "Which datasets are accessible for reanalysis?",
        "chunks": [
            {
                "evidence_id": "E010",
                "paper_id": "P1",
                "text": "Data are deposited under PRJNA679547.",
            },
            {
                "evidence_id": "E012",
                "paper_id": "P2",
                "text": "RNA-seq data are in GEO repository GSE186820.",
            },
        ],
    }

    qc = assess_answer_quality(
        "PRJNA679547 is reported by E010. GSE186820 is reported by E012.",
        pack,
        task="answer",
    )

    assert "repository_identifier_omitted" not in qc["warnings"]
    assert qc["missing_repository_identifiers"] == []


def test_data_accessibility_qc_needs_one_relevant_identifier_per_evidence_item() -> None:
    pack = {
        "question": "Which datasets are accessible for reanalysis?",
        "chunks": [
            {
                "evidence_id": "E010",
                "paper_id": "P1",
                "text": "RNA-seq is PRJEB33557 and metabolomics is MTBLS1188.",
            }
        ],
    }

    qc = assess_answer_quality(
        "The relevant RNA-seq dataset is PRJEB33557 (E010).",
        pack,
        task="answer",
    )

    assert "repository_identifier_omitted" not in qc["warnings"]
