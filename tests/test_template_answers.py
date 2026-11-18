from __future__ import annotations

import re

import pytest

from originrag.analysis.answer_qc import assess_answer_quality
from originrag.analysis.template_answers import (
    build_data_accessibility_answer,
    build_template_answer,
    clean_evidence_text,
    evidence_contribution,
    question_requests_data_accessibility,
    select_fallback_citation_chunks,
    truncate_at_word,
)


def test_hypothesis_template_passes_qc() -> None:
    pack = {
        "question": "Make a testable hypothesis connecting asymptomatic malaria and PfEMP1.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Ceesay 2025, Results, pages 12-13]",
                "title": "A paper",
                "journal": "PLOS Pathogens",
                "section": "results",
                "text": "Asymptomatic infections had broader PfEMP1 antibody recognition.",
            },
            {
                "evidence_id": "E002",
                "citation_label": "[Tessema 2019, Abstract, pages 3]",
                "title": "Another paper",
                "journal": "Cell Host & Microbe",
                "section": "abstract",
                "text": "Antibodies to PfEMP1 variants were associated with reduced severe malaria risk.",
            },
        ],
    }

    answer = build_template_answer(
        question=str(pack["question"]),
        evidence_pack=pack,
        task="hypothesis",
    )
    qc = assess_answer_quality(answer, pack, task="hypothesis")

    assert "## Hypothesis Card" in answer
    assert "### required_data" in answer
    assert "E001" in answer
    assert qc["passed"] is True, qc


def test_answer_template_passes_qc_while_public_chat_can_hide_internal_headings() -> None:
    pack = {
        "question": "What evidence links PfEMP1 to severe malaria?",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Jespersen 2016, Results]",
                "title": "A paper",
                "text": "PfEMP1 binding was associated with severe malaria.",
            }
        ],
    }

    answer = build_template_answer(str(pack["question"]), pack, task="answer")
    qc = assess_answer_quality(answer, pack, task="answer")

    assert qc["passed"] is True


def test_named_study_fallback_prefers_late_anchor_and_support_over_generic_chunks() -> None:
    pack = {
        "question": (
            "Did Ciuffreda et al. reconstruct continuous parasite-age distributions? "
            "State the assay and cohort."
        ),
        "chunks": [
            {
                "evidence_id": f"E00{index}",
                "paper_id": f"GENERIC_{index}",
                "citation_label": f"[Generic et al., 202{index}]",
                "text": "A broad malaria transcriptome result was reported.",
            }
            for index in range(1, 5)
        ]
        + [
            {
                "evidence_id": "E009",
                "paper_id": "CIUFFREDA2020",
                "citation_label": "[Ciuffreda et al., 2020, Abstract, pages 1]",
                "text": (
                    "The method used three genes differentially expressed across the "
                    "intra-erythrocytic lifecycle in uncomplicated malaria patients "
                    "attending two health clinics in Ghana."
                ),
                "explicit_named_study_anchor": True,
            },
            {
                "evidence_id": "E012",
                "paper_id": "CIUFFREDA2020",
                "citation_label": "[Ciuffreda et al., 2020, Methods, pages 4-5]",
                "text": (
                    "The model used RT-qPCR measurements; mixed-stage samples mapped "
                    "to an intermediate point."
                ),
                "explicit_named_study_support": True,
            },
        ],
    }

    answer = build_template_answer(pack["question"], pack, task="answer")
    qc = assess_answer_quality(answer, pack, task="answer")

    assert "E009" in answer
    assert "E012" in answer
    assert "E001" not in answer
    assert "E002" not in answer
    assert "[Ciuffreda et al., 2020, Abstract, pages 1]" in answer
    assert "[Ciuffreda et al., 2020, Methods, pages 4-5]" in answer
    assert qc["passed"] is True, qc


def test_named_study_fallback_bypasses_broad_cerebral_malaria_template() -> None:
    pack = {
        "question": (
            "In Joste et al. (2020), were A-type ICAM-1-binding PfEMP1 domains "
            "enriched in cerebral malaria?"
        ),
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "GENERIC",
                "citation_label": "[Generic et al., 2019, Results]",
                "text": "A functional PfEMP1 binding experiment was reported.",
            },
            {
                "evidence_id": "E005",
                "paper_id": "JOSTE2020",
                "citation_label": "[Joste et al., 2020, Results, pages 6-7]",
                "text": (
                    "No significant difference in A-type ICAM-1-binding domain "
                    "expression was observed between cerebral and uncomplicated malaria."
                ),
                "explicit_named_study_support": True,
            },
        ],
    }

    answer = build_template_answer(pack["question"], pack, task="answer")

    assert "E005" in answer
    assert "E001" not in answer
    assert "Separate functional binding" not in answer


def test_bounded_resolved_comparison_fallback_uses_only_resolved_target_ids() -> None:
    pack = {
        "question": (
            "Compare the dependency paper with the maximal marginal relevance paper."
        ),
        "bounded_direct_report": {
            "supported": True,
            "scope": "resolved_report_comparison_only",
            "evidence": [
                {"evidence_id": "E004", "paper_id": "BY"},
                {"evidence_id": "E007", "paper_id": "MMR"},
            ],
        },
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "GENERIC",
                "citation_label": "[Generic et al., 2024, Results]",
                "text": "An unrelated method result was reported.",
            },
            {
                "evidence_id": "E004",
                "paper_id": "BY",
                "citation_label": "[Benjamini et al., 2001, Abstract]",
                "text": "FDR control was proved under a dependency condition.",
            },
            {
                "evidence_id": "E007",
                "paper_id": "MMR",
                "citation_label": "[Carbonell et al., 1998, Abstract]",
                "text": "MMR balances query relevance against document redundancy.",
            },
        ],
    }

    answer = build_template_answer(pack["question"], pack, task="answer")

    assert "E004" in answer and "E007" in answer
    assert "E001" not in answer


def test_bounded_causal_comparison_fallback_states_explicit_evidence_limit() -> None:
    pack = {
        "question": (
            "Compare Sakoguchi et al. (2025) and Kassegne et al. (2025). "
            "Does either paper demonstrate that receptor binding itself causes a "
            "reduction in severe-malaria incidence?"
        ),
        "bounded_direct_report": {
            "supported": True,
            "scope": "named_report_comparison_only",
            "evidence": [
                {"evidence_id": "E001", "paper_id": "SAKOGUCHI"},
                {"evidence_id": "E003", "paper_id": "KASSEGNE"},
            ],
        },
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "SAKOGUCHI",
                "citation_label": "[Sakoguchi et al., 2025, Results]",
                "text": "RIFIN bound KIR2DL1 in a molecular assay.",
            },
            {
                "evidence_id": "E002",
                "paper_id": "GENERIC",
                "citation_label": "[Generic et al., 2024, Results]",
                "text": "An unrelated receptor result was reported.",
            },
            {
                "evidence_id": "E003",
                "paper_id": "KASSEGNE",
                "citation_label": "[Kassegne et al., 2025, Results]",
                "text": "Antibodies correlated with reduced severe malaria risk.",
            },
        ],
    }

    answer = build_template_answer(pack["question"], pack, task="answer")

    assert "Neither paper demonstrates" in answer
    assert "causes the requested clinical outcome (E001; E003)" in answer
    assert "does not prove that a causal relationship is absent" in answer
    assert "E002" not in answer


def test_marker_positive_state_fallback_keeps_only_explicit_marker_labels() -> None:
    pack = {
        "question": (
            "What SPP1-positive macrophage states are reported in COVID-19, "
            "and how are they associated with disease severity?"
        ),
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "LIAO2020",
                "citation_short": "Liao et al., 2020",
                "citation_label": "[Liao et al., 2020, Results]",
                "author": "Liao et al.",
                "year": "2020",
                "text": (
                    "The macrophages were grouped by FCN1, SPP1 and FABP4 expression "
                    "patterns including the FCN1hi group 1, FCN1loSPP1+ group 2, "
                    "SPP1+ group 3 and FABP4+ group 4 macrophages. FABP4 was "
                    "preferentially expressed by controls and patients with moderate "
                    "infection, while FCN1 and SPP1 were highly expressed by patients "
                    "with severe/critical infection."
                ),
            }
        ],
    }

    answer = build_template_answer(pack["question"], pack, task="answer")
    qc = assess_answer_quality(answer, pack, task="answer")

    assert "two explicitly SPP1-positive states" in answer
    assert "FCN1loSPP1+ group 2 and SPP1+ group 3" in answer
    assert "SPP1 was more highly expressed in severe/critical than moderate" in answer
    assert "FCN1hi group 1" not in answer
    assert "FABP4+ group 4" not in answer
    assert qc["passed"] is True, qc


def test_data_accessibility_contract_names_every_repository_evidence_item() -> None:
    pack = {
        "question": "Which datasets are accessible for reanalysis?",
        "chunks": [
            {
                "evidence_id": "E010",
                "paper_id": "P1",
                "citation_short": "Milne et al., 2021",
                "citation_label": "[Milne et al., 2021, Data Availability]",
                "title": "Mapping immune variation",
                "doi": "10.7554/elife.62800",
                "text": "Data are under GSE132050 and PRJEB33557.",
            },
            {
                "evidence_id": "E012",
                "paper_id": "P2",
                "citation_short": "Guillochon et al., 2022",
                "citation_label": "[Guillochon et al., 2022, Methods]",
                "title": "Cerebral malaria transcriptome",
                "doi": "10.1093/infdis/jiac086",
                "text": "RNA-seq data are in GEO GSE186820.",
            },
        ],
    }

    answer = build_data_accessibility_answer(pack)
    qc = assess_answer_quality(answer, pack, task="answer")

    assert "GSE132050" in answer
    assert "PRJEB33557" in answer
    assert "GSE186820" in answer
    assert qc["passed"] is True
    assert "## Evidence-backed statements" in answer
    assert "## Missing evidence" in answer


def test_data_accessibility_contract_ignores_incidental_repository_identifiers() -> None:
    pack = {
        "question": (
            "Did Ciuffreda et al. reconstruct parasite-age distributions from "
            "whole-transcriptome RNA-seq? State what they measured, the model input, "
            "and the clinical cohort."
        ),
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_short": "Unrelated et al., 2024",
                "text": "An RNA-seq dataset was deposited under GSE12345.",
            }
        ],
    }

    assert build_data_accessibility_answer(pack) == ""


def test_data_accessibility_intent_recognizes_natural_english_and_german_requests() -> None:
    requests = (
        "Where can I download the RNA-seq data?",
        "How can I access the dataset?",
        "Are the data available?",
        "Kann ich die Daten herunterladen?",
        "Wo sind die Transkriptomdaten hinterlegt?",
    )

    assert all(question_requests_data_accessibility(question) for question in requests)


def test_data_accessibility_intent_ignores_repository_method_question() -> None:
    assert not question_requests_data_accessibility(
        "Which repository-based retrieval method was used?"
    )


def test_explicit_doi_overlap_answer_counts_one_publication_family() -> None:
    pack = {
        "question": (
            "Do DOI 10.1101/2021.11.08.467248 and DOI 10.1093/infdis/jiac086 "
            "report independent studies, or are they preprint and journal versions of the same work?"
        ),
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P_JOURNAL",
                "doi": "10.1093/infdis/jiac086",
                "citation_label": "[Guillochon et al., 2022]",
            },
            {
                "evidence_id": "E002",
                "paper_id": "P_PREPRINT",
                "doi": "10.1101/2021.11.08.467248",
                "citation_label": "[Guillochon et al., 2021]",
            },
        ],
        "automatic_lineage_signals": [
            {
                "paper_id_a": "P_JOURNAL",
                "paper_id_b": "P_PREPRINT",
                "signal_type": "same_preprint_family",
                "status": "suggested",
                "details": {},
            },
            {
                "paper_id_a": "P_JOURNAL",
                "paper_id_b": "P_PREPRINT",
                "signal_type": "same_dataset_accession",
                "status": "suggested",
                "details": {"shared": ["GSE186820"]},
            },
        ],
    }

    answer = build_template_answer(str(pack["question"]), pack, task="answer")
    qc = assess_answer_quality(answer, pack, task="answer")

    assert "preprint and journal version of the same publication family" in answer
    assert "GSE186820" in answer
    assert "at most one publication-family unit" in answer
    assert "automatic metadata-based overlap signal" in answer
    assert "E001" in answer and "E002" in answer
    assert qc["passed"] is True, qc


def test_cerebral_malaria_gene_fallback_answers_without_inventing_core_gene_list() -> None:
    pack = {
        "question": "Which core genes are consistently associated with cerebral malaria?",
        "provisional_synthesis_authorized": True,
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "citation_label": "[Kyriacou et al., 2006, Abstract]",
                "author": "Kyriacou et al.",
                "year": "2006",
                "title": "Differential var transcription in cerebral malaria",
                "text": "Cerebral malaria isolates predominantly transcribe Group A or B/A var genes.",
            },
            {
                "evidence_id": "E002",
                "paper_id": "P2",
                "citation_label": "[Lennartz et al., 2017, Abstract]",
                "author": "Lennartz et al.",
                "year": "2017",
                "title": "Dual receptor-binding PfEMP1 associated with cerebral malaria",
                "text": "EPCR and ICAM-1 dual receptor-binding PfEMP1 was associated with cerebral malaria.",
            },
            {
                "evidence_id": "E003",
                "paper_id": "P3",
                "citation_label": "[Duffy et al., 2019, Abstract]",
                "author": "Duffy et al.",
                "year": "2019",
                "title": "Meta-analysis of var signatures",
                "text": "The meta-analysis found increased transcription of DC8 and DC13 Group A var genes predicted to bind EPCR.",
            },
        ],
    }

    answer = build_template_answer(str(pack["question"]), pack, task="answer")
    assert "no supported universal list" in answer
    assert "Group A or B/A `var`, DC8/DC13" in answer


def test_cerebral_malaria_association_fallback_does_not_answer_unasked_core_gene_question() -> None:
    pack = {
        "question": "What evidence links EPCR-binding PfEMP1 to cerebral malaria?",
        "provisional_synthesis_authorized": True,
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "title": "EPCR-binding DC8 PfEMP1 expression is associated with cerebral malaria",
                "text": "We found higher expression in children with cerebral malaria.",
                "section": "abstract",
                "citation_label": "Example et al., 2020",
                "source_type": "primary_research",
            },
            {
                "evidence_id": "E002",
                "paper_id": "P2",
                "title": "Group A var expression increases in cerebral malaria",
                "text": "Data show elevated Group A var expression in cerebral malaria.",
                "section": "results",
                "citation_label": "Example et al., 2021",
                "source_type": "primary_research",
            },
        ],
    }

    answer = build_template_answer(pack["question"], pack, task="answer")

    assert "Clinical studies associate EPCR-binding PfEMP1" in answer
    assert "There is no supported universal list" not in answer
    assert "study origins remain unverified" in answer


def test_cerebral_malaria_fallback_separates_mechanistic_binding_study() -> None:
    pack = {
        "question": "What evidence links EPCR-binding PfEMP1 to cerebral malaria?",
        "provisional_synthesis_authorized": True,
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "chunk_id": "P1_C1",
                "title": "EPCR-binding PfEMP1 expression increases with severe malaria",
                "text": "Expression was elevated in children with severe malaria.",
                "citation_label": "Clinical et al., 2020",
            },
            {
                "evidence_id": "E002",
                "paper_id": "P2",
                "chunk_id": "P2_C1",
                "title": (
                    "Structural conservation allows EPCR binding by PfEMP1 "
                    "implicated in severe malaria"
                ),
                "text": "Crystal structures and binding assays defined the receptor interaction.",
                "citation_label": "Mechanism et al., 2021",
            },
        ],
    }

    answer = build_template_answer(pack["question"], pack, task="answer")

    assert "Clinical studies associate EPCR-binding PfEMP1" in answer
    assert "do not by themselves establish a clinical association" in answer
    assert "(E001)" in answer
    assert "(E002)" in answer


def test_epcr_fallback_keeps_clinical_binding_and_icam_null_citations_role_pure() -> None:
    pack = {
        "question": "What evidence links EPCR-binding PfEMP1 to cerebral malaria?",
        "provisional_synthesis_authorized": True,
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "CLINICAL",
                "title": (
                    "EPCR-binding PfEMP1 expression increases with malaria disease "
                    "severity and is elevated in cerebral malaria"
                ),
                "text": "Table 4 compares transcript levels in children with severe malaria.",
                "section": "discussion",
                "citation_label": "Clinical et al., 2017",
            },
            {
                "evidence_id": "E002",
                "paper_id": "BINDING",
                "title": (
                    "DC8 var genes associated with severe malaria bind avidly to "
                    "endothelial cells"
                ),
                "text": "Results show that PfEMP1 domains bound brain endothelial cells.",
                "section": "results",
                "citation_label": "Binding et al., 2013",
            },
            {
                "evidence_id": "E003",
                "paper_id": "MIXED_TITLE",
                "title": "Severe malaria is associated with parasite binding to EPCR",
                "text": (
                    "We tested native DC8 PfEMP1 and found that it bound EPCR, "
                    "demonstrated by recombinant EPCR inhibition."
                ),
                "section": "introduction",
                "citation_label": "Mixed et al., 2013",
            },
            {
                "evidence_id": "E004",
                "paper_id": "ICAM_NULL",
                "title": (
                    "PfEMP1 A-Type ICAM-1-Binding Domains Are Not Associated with "
                    "Cerebral Malaria"
                ),
                "text": (
                    "We found no association between A-type ICAM-1-binding domains "
                    "and cerebral malaria in this cohort."
                ),
                "section": "abstract",
                "citation_label": "Null et al., 2020",
                "source_type_assessment": {"source_type": "primary_research"},
            },
            {
                "evidence_id": "E005",
                "paper_id": "ICAM_REVIEW",
                "title": "Review of ICAM-1-binding PfEMP1 in cerebral malaria",
                "text": (
                    "Prior studies found no association between ICAM-1-binding "
                    "domains and cerebral malaria."
                ),
                "section": "abstract",
                "citation_label": "Review et al., 2022",
                "source_type_assessment": {"source_type": "narrative_review"},
            },
            {
                "evidence_id": "E006",
                "paper_id": "EPCR_NULL",
                "title": "EPCR-binding PfEMP1 and cerebral malaria",
                "text": (
                    "Our results show that EPCR-binding variants were not associated "
                    "with cerebral malaria."
                ),
                "section": "abstract",
                "citation_label": "Negative et al., 2023",
                "source_type_assessment": {"source_type": "primary_research"},
            },
        ],
    }

    answer = build_template_answer(pack["question"], pack, task="answer")
    evidence = answer.split("## Cautious inference", 1)[0]
    clinical, remainder = evidence.split("Separate functional", 1)
    binding, icam_null = remainder.split("A distinct null result", 1)

    assert "E001" in clinical
    assert "E002" not in clinical
    assert "E003" not in clinical
    assert "E002" in binding
    assert "E003" in binding
    assert "E001" not in binding
    assert "E004" in icam_null
    assert "E005" not in evidence
    assert "E006" not in evidence
    assert "not EPCR-binding variants" in icam_null


def test_gaps_template_contains_required_sections() -> None:
    pack = {
        "question": "Find gaps.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Warimwe 2009, Discussion]",
                "text": "The mechanism of severe malaria onset remains unclear.",
            }
        ],
    }

    answer = build_template_answer("Find gaps.", pack, task="gaps")
    qc = assess_answer_quality(answer, pack, task="gaps")

    assert "## Supported Research Gaps" in answer
    assert qc["passed"] is True


def test_gaps_template_tracks_resistance_topic() -> None:
    pack = {
        "question": "Find gaps linking antimalarial resistance, diagnostic escape, and malaria surveillance data.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Nsanzabana 2021, Abstract]",
                "title": "Time to scale up molecular surveillance for anti-malarial drug resistance",
                "category": "resistance_dx",
                "text": "Artemisinin resistance and molecular surveillance are important for malaria control.",
            }
        ],
    }

    answer = build_template_answer(str(pack["question"]), pack, task="gaps")
    qc = assess_answer_quality(answer, pack, task="gaps")

    assert "diagnostic escape" in answer
    assert "surveillance" in answer
    assert "PfEMP1 antibody breadth" not in answer
    assert qc["passed"] is True


def test_gap_fallback_keeps_broad_parasite_transcript_question_on_topic() -> None:
    question = (
        "find gaps in the study of associatoins with parasite trancripts/expression/genes "
        "and clinical malrai syndormes. what is unknown?"
    )
    pack = {
        "question": question,
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Warimwe et al., 2009, Discussion]",
                "title": "Plasmodium falciparum var gene expression is modified by host immunity",
                "text": "Syndrome-specific associations require clinical syndrome to be accounted for.",
            },
            {
                "evidence_id": "E002",
                "citation_label": "[Host et al., 2019, Results]",
                "title": "Whole-blood host transcriptional signatures",
                "text": "Host erythropoietic genes differed between clinical groups.",
            },
            {
                "evidence_id": "E003",
                "citation_label": "[Tarr et al., 2018, Results]",
                "title": "Plasmodium falciparum schizont stage transcriptome variation",
                "text": "Parasite stage composition affected transcriptome comparisons.",
            },
        ],
    }

    answer = build_template_answer(question, pack, task="gaps")
    qc = assess_answer_quality(answer, pack, task="gaps")

    assert "parasite transcription and clinical malaria syndromes" in answer
    assert "antibody breadth" not in answer
    assert "E002" not in answer
    assert "stage" in answer.lower()
    assert qc["passed"] is True, qc


def test_gaps_template_has_specific_sex_difference_evidence_map() -> None:
    pack = {
        "question": "Find research gaps in sex differences in malaria.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Briggs 2020, Discussion]",
                "author": "Briggs",
                "year": "2020",
                "section": "discussion",
                "text": "There is still little known about sex-based biological differences that may mediate immunity to malaria.",
            },
            {
                "evidence_id": "E002",
                "citation_label": "[Duncombe 2026, Abstract]",
                "author": "Duncombe",
                "year": "2026",
                "section": "abstract",
                "text": "No studies considered the effect of host sex on Plasmodium liver-stage duration.",
            },
            {
                "evidence_id": "E003",
                "citation_label": "[Atobrah 2025, Abstract]",
                "author": "Atobrah",
                "year": "2025",
                "section": "abstract",
                "text": "Researchers use sex and gender interchangeably in malaria and gender research.",
            },
            {
                "evidence_id": "E004",
                "citation_label": "[Okiring 2022, Results]",
                "author": "Okiring",
                "year": "2022",
                "section": "results",
                "text": "Postadolescent males had a higher probability than females of testing positive.",
            },
        ],
    }

    answer = build_template_answer(str(pack["question"]), pack, task="gaps")
    qc = assess_answer_quality(answer, pack, task="gaps")

    assert "## Evidence Map" in answer
    assert "biological mechanisms remain under-tested" in answer
    assert "sex and gender" in answer
    assert "liver-stage" in answer
    assert "E001" in answer and "E004" in answer
    assert qc["passed"] is True


def test_hypothesis_template_tracks_sex_immunity_topic() -> None:
    pack = {
        "question": "Make a hypothesis connecting sex differences, host immunity, and severe malaria outcomes.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Briggs 2020, Discussion]",
                "title": "Sex-based differences in clearance of chronic Plasmodium falciparum infection",
                "category": "sex_diff",
                "text": "Sex differences may affect immune control of Plasmodium infection.",
            }
        ],
    }

    answer = build_template_answer(str(pack["question"]), pack, task="hypothesis")
    qc = assess_answer_quality(answer, pack, task="hypothesis")

    assert "sex differences" in answer.lower() or "sex-specific" in answer.lower()
    assert "PfEMP1 antibody breadth may mark" not in answer
    assert qc["passed"] is True


def test_hypothesis_template_formats_working_prediction_cleanly() -> None:
    pack = {
        "question": "Make a hypothesis connecting asymptomatic malaria, PfEMP1 antibody breadth, and protection.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Ceesay 2025, Results]",
                "text": "Asymptomatic infections had broader PfEMP1 antibody recognition.",
            }
        ],
    }

    answer = build_template_answer(str(pack["question"]), pack, task="hypothesis")

    assert "Working prediction:" in answer
    assert "consistent with Broader" not in answer


def test_hypothesis_template_titles_pfemp1_host_immunity_question() -> None:
    pack = {
        "question": "Make a testable hypothesis connecting PfEMP1 expression, host immunity, and severe malaria.",
        "chunks": [
            {
                "evidence_id": "E001",
                "citation_label": "[Warimwe 2009, Results]",
                "text": "Antibodies to PfEMP1 variants were associated with protection.",
            }
        ],
    }

    answer = build_template_answer(str(pack["question"]), pack, task="hypothesis")

    assert "Host immunity may shape PfEMP1 expression patterns linked to severe malaria." in answer


def test_clean_evidence_text_repairs_common_pdf_artifacts() -> None:
    clean = clean_evidence_text(
        "9 Malarianaive participants and an ohort study used feminist meth ods to study the role ofantibodies im..."
    )

    assert "Malaria-naive" in clean
    assert "cohort study" in clean
    assert "ccohort" not in clean
    assert "methods" in clean
    assert "of antibodies" in clean
    assert not clean.endswith("im...")


def test_truncate_at_word_does_not_leave_partial_word_fragment() -> None:
    text = "cohort study from Western Kenya " + ("important " * 30)

    truncated = truncate_at_word(text, max_chars=80)

    assert truncated.endswith("...")
    assert "imp..." not in truncated


def test_evidence_contribution_curates_western_kenya_cohort_sentence() -> None:
    contribution = evidence_contribution(
        {
            "text": (
                "cohort study from Western Kenya, individuals with asymptomatic parasitaemia "
                "had an increased 1-month likelihood of symptomatic malaria compared to those "
                "who were uninfected; importantly, these associations were over twice as strong "
                "in females compared to males."
            )
        }
    )

    assert "sex-specific differences" in contribution
    assert "imp..." not in contribution


def test_answer_fallback_honors_german_list_request() -> None:
    answer = build_template_answer(
        "Gib mir alle Korrelationen übersichtlich als Liste auf Deutsch.",
        {
            "chunks": [
                {
                    "evidence_id": "E001",
                    "citation_label": "[Kaestli 2006, Results]",
                    "text": "Group A var genes were associated with severe malaria.",
                }
            ]
        },
        task="answer",
    )

    assert "- E001 (Results) reports that" in answer
    assert answer.count("Kaestli 2006") == 1
    assert "Die abgerufenen Arbeiten" in answer


def test_answer_fallback_emits_one_inline_source_marker_with_locator() -> None:
    answer = build_template_answer(
        "Summarize the malaria finding.",
        {
            "chunks": [
                {
                    "evidence_id": "E001",
                    "paper_id": "P1",
                    "citation_label": "[Macalinao et al., 2023, Discussion, pages 14]",
                    "citation_short": "Macalinao et al., 2023",
                    "text": "Long-lived IgM memory B cells dominated the antibody response.",
                }
            ]
        },
        task="answer",
    )

    body = answer.split("## References", maxsplit=1)[0]
    assert "E001 (Discussion, pages 14) reports that" in body
    assert "Macalinao" not in body


@pytest.mark.parametrize("paper_count", [1, 4, 5, 8, 12, 17])
def test_answer_fallback_retains_every_supplied_paper(paper_count: int) -> None:
    chunks = []
    for index in range(paper_count):
        chunks.append(
            {
                "evidence_id": f"E{index + 1:03d}",
                "paper_id": f"P{index + 1:03d}",
                "citation_label": f"[Author {index + 1}, 2020]",
                "text": f"Caching reduced query latency in benchmark {index + 1}.",
                "quality_flags": [],
            }
        )

    question = "Summarize the evidence on query latency."
    pack = {"question": question, "chunks": chunks}
    answer = build_template_answer(question, pack, task="answer")
    qc = assess_answer_quality(answer, pack, task="answer")

    statements = answer.split("## Cautious inference", maxsplit=1)[0]
    references = answer.split("## References", maxsplit=1)[1]
    expected_ids = [chunk["evidence_id"] for chunk in chunks]
    assert re.findall(r"\bE\d{3}\b", statements) == expected_ids
    assert re.findall(r"\bE\d{3}\b", references) == expected_ids
    assert qc["passed"] is True, qc


def test_fallback_selection_preserves_clean_priority_and_deduplicates_evidence() -> None:
    chunks = [
        {"evidence_id": "E001", "paper_id": "P1", "quality_flags": ["table_like"]},
        {"evidence_id": "E002", "paper_id": "P2"},
        {"evidence_id": "E003", "paper_id": "P1"},
        {"evidence_id": "E003", "paper_id": "P1"},
        {"evidence_id": "E004", "paper_id": "P2"},
        {"evidence_id": "E003", "paper_id": "DUPLICATE_METADATA"},
        {"evidence_id": "E005", "paper_id": "P3", "quality_flags": ["figure_like"]},
        {"evidence_id": "E006", "paper_id": "P4"},
    ]

    selected = select_fallback_citation_chunks(chunks)

    assert [chunk["evidence_id"] for chunk in selected] == [
        "E002", "E003", "E006", "E005"
    ]
    assert all(any(chunk is supplied for supplied in chunks) for chunk in selected)


def test_fallback_selection_keeps_distinct_evidence_without_paper_ids() -> None:
    chunks = [
        {"evidence_id": "E001"},
        {"evidence_id": "E002", "paper_id": None},
        {"evidence_id": "E003", "paper_id": ""},
        {"evidence_id": "E003", "paper_id": ""},
        {"evidence_id": "E004", "paper_id": " "},
        {"evidence_id": "E005", "paper_id": "E001"},
    ]

    selected = select_fallback_citation_chunks(chunks)

    assert [chunk["evidence_id"] for chunk in selected] == [
        "E001", "E002", "E003", "E004", "E005"
    ]


def test_fallback_skips_unciteable_chunks_without_inventing_source_ids() -> None:
    chunks = [
        {"paper_id": "P1", "text": "Untraceable caching claim."},
        {"paper_id": "P1", "evidence_id": None},
        {"paper_id": "P1", "evidence_id": " "},
        {
            "paper_id": "P1",
            "evidence_id": "E001",
            "citation_label": "[Author, 2020]",
            "text": "Caching reduced query latency.",
        },
    ]

    selected = select_fallback_citation_chunks(chunks)
    answer = build_template_answer(
        "Summarize the query latency evidence.", {"chunks": chunks}, task="answer"
    )

    assert selected == [chunks[-1]]
    assert "Untraceable caching claim" not in answer
    assert "None:" not in answer
    assert "Evidence reports" not in answer
    assert re.findall(r"\bE\d{3}\b", answer) == ["E001", "E001"]
    assert select_fallback_citation_chunks(chunks[:-1]) == []
    assert select_fallback_citation_chunks([]) == []


def test_fallback_selection_preserves_direct_comparison_priority(monkeypatch) -> None:
    chunks = [
        {"evidence_id": "E001", "paper_id": "P1"},
        {"evidence_id": "E002", "paper_id": "P2"},
        {"evidence_id": "E003", "paper_id": "P1", "quality_flags": ["figure_like"]},
        {"evidence_id": "E004", "paper_id": "P3"},
    ]
    monkeypatch.setattr(
        "originrag.analysis.template_answers.eligible_direct_comparison_candidate_chunks",
        lambda question, pack: [chunks[2]],
    )

    selected = select_fallback_citation_chunks(
        chunks, question="How strong is the evidence on query latency?"
    )

    assert [chunk["evidence_id"] for chunk in selected] == ["E003", "E002", "E004"]


def test_large_fallback_pack_keeps_named_anchor_and_support_contract() -> None:
    chunks = [
        {
            "evidence_id": f"E{index:03d}",
            "paper_id": f"P{index}",
            "citation_label": "[Context, 2020]",
            "text": "Query latency was measured in a separate benchmark.",
        }
        for index in range(1, 16)
    ]
    support = {
        "evidence_id": "E101",
        "paper_id": "CACHE2025",
        "citation_label": "[Cache, 2025, Methods]",
        "text": "Query latency was measured over repeated benchmark runs.",
        "explicit_named_study_support": True,
    }
    anchor = {
        "evidence_id": "E102",
        "paper_id": "CACHE2025",
        "citation_label": "[Cache, 2025, Results]",
        "text": "Caching reduced median query latency.",
        "explicit_named_study_anchor": True,
    }
    chunks.extend([support, anchor, dict(anchor), dict(support)])

    answer = build_template_answer(
        "What did Cache et al. (2025) report about query latency?",
        {"chunks": chunks},
        task="answer",
    )

    statements = answer.split("## Cautious inference", maxsplit=1)[0]
    references = answer.split("## References", maxsplit=1)[1]
    assert re.findall(r"\bE\d{3}\b", statements) == ["E102", "E101"]
    assert re.findall(r"\bE\d{3}\b", references) == ["E102", "E101"]


def test_evidence_strength_fallback_prioritizes_direct_comparison_results() -> None:
    question = (
        "What changes in var gene expression distinguish asymptomatic dry-season "
        "Plasmodium falciparum infections from clinical malaria, and how strong is "
        "the evidence?"
    )
    chunks = [
        {
            "evidence_id": "E001",
            "paper_id": "P_CEESAY",
            "citation_label": "[Ceesay et al., 2025, Discussion]",
            "section": "discussion",
            "quality_flags": [],
            "text": (
                "We found no significant difference in var UPS type expression "
                "between clinical and asymptomatic infections."
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
        *[
            {
                "evidence_id": f"E{index:03d}",
                "paper_id": f"P_CONTEXT_{index}",
                "citation_label": f"[Context et al., 20{index}, Results]",
                "section": "results",
                "quality_flags": [],
                "text": "Parasites were sampled during the dry season.",
            }
            for index in range(3, 7)
        ],
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
            "paper_id": "P_CONTEXT_8",
            "citation_label": "[Context et al., 2018, Results]",
            "section": "results",
            "quality_flags": [],
            "text": "Parasite age was estimated.",
        },
        {
            "evidence_id": "E009",
            "paper_id": "P_CONTEXT_9",
            "citation_label": "[Context et al., 2019, Results]",
            "section": "results",
            "quality_flags": [],
            "text": "Expression was quantified by RNA sequencing.",
        },
        {
            "evidence_id": "E011",
            "paper_id": "P_PARASITEMIA",
            "citation_label": "[Unrelated et al., 2021, Results]",
            "section": "results",
            "quality_flags": [],
            "text": (
                "Parasitemia was lower in asymptomatic infections than in clinical "
                "malaria."
            ),
        },
        {
            "evidence_id": "E012",
            "paper_id": "P_CONTEXT_12",
            "citation_label": "[Context et al., 2022, Results]",
            "section": "results",
            "quality_flags": [],
            "text": "A separate cohort was recruited.",
        },
        {
            "evidence_id": "E010",
            "paper_id": "P_WARIMWE",
            "citation_label": "[Warimwe et al., 2013, Results, pages 5-6]",
            "section": "results",
            "quality_flags": ["figure_like"],
            "source_type_assessment": {
                "source_type": "reanalysis_secondary_data",
            },
            "text": (
                "Here, we further show that isolates from children with asymptomatic "
                "infection have more focused expression of var genes (higher VEH) than "
                "those from children with clinical malaria. One weakness of this study "
                "is Figure 4. The relationship between VEH and host antibodies is shown "
                "in individual panels."
            ),
        },
    ]
    pack = {"question": question, "chunks": chunks}

    answer = build_template_answer(question, pack, task="answer")
    qc = assess_answer_quality(answer, pack, task="answer")

    assert "E001" in answer
    assert "E002" in answer
    assert "E007" in answer
    assert "E010" in answer
    assert "more focused expression of var genes (higher VEH)" in answer
    assert "In a secondary analysis, E010" in answer
    assert "relationship between VEH and host antibodies" not in answer
    assert "E003" not in answer
    assert "E005" not in answer
    assert "E011" not in answer
    references = answer.split("## References", maxsplit=1)[1]
    assert set(re.findall(r"\bE\d{3}\b", references)) == {
        "E001",
        "E002",
        "E007",
        "E010",
    }
    assert "direct_comparison_evidence_omitted" not in qc["warnings"]
    assert qc["missing_direct_comparison_candidate_groups"] == []
    assert qc["passed"] is True, qc
