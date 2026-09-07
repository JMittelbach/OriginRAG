from __future__ import annotations

from originrag.analysis.evidence_pack_grading import (
    chunks_matching_explicit_report,
    epistemic_causal_comparison_question,
    grade_evidence_pack,
    question_relevance_terms,
    resolved_report_comparison_chunks,
)


def test_grades_diverse_evidence_pack_as_good() -> None:
    pack = {
        "question": "What evidence links PfEMP1 to EPCR binding in severe malaria?",
        "chunks": [
            evidence_chunk("P1", "results", "PfEMP1 binds EPCR in severe malaria.", doi="10.1/a"),
            evidence_chunk(
                "P2", "discussion", "CIDRalpha1 domains are linked to EPCR binding.", doi="10.1/b"
            ),
            evidence_chunk(
                "P3", "abstract", "DC8 and DC13 var genes associate with severity.", doi="10.1/c"
            ),
            evidence_chunk(
                "P4",
                "methods",
                "Clinical phenotype and parasite expression were measured.",
                doi="10.1/d",
            ),
        ],
    }

    grade = grade_evidence_pack(pack, expected_terms=["PfEMP1", "EPCR", "CIDRalpha1"])

    assert grade["grade"] == "good"
    assert grade["score"] >= 80
    assert grade["metrics"]["n_unique_papers"] == 4
    assert grade["metrics"]["expected_term_coverage"] == 1.0


def test_diverse_off_topic_results_cannot_grade_as_good() -> None:
    pack = {
        "question": "What evidence links PfEMP1 CIDRalpha1 binding to EPCR in severe malaria?",
        "chunks": [
            evidence_chunk(
                f"Z{index}",
                "results",
                f"Zebrafish fin regeneration assay {index} quantified developmental morphology.",
                doi=f"10.1234/zebrafish.{index}",
            )
            for index in range(1, 5)
        ],
    }

    grade = grade_evidence_pack(pack)

    assert grade["grade"] in {"bad", "weak"}
    assert grade["score"] < 60
    assert "very_low_question_term_coverage" in grade["reasons"]
    assert "very_low_question_relevant_chunk_fraction" in grade["reasons"]
    assert grade["metrics"]["n_unique_papers"] == 4
    assert grade["metrics"]["doi_fraction"] == 1.0


def test_task_wording_is_not_counted_as_scientific_question_coverage() -> None:
    pack = {
        "question": (
            "i want to know about how the memory t cell repertoire changes from "
            "early childhood to adolescence. Please provide literature."
        ),
        "chunks": [
            evidence_chunk(
                f"P{index}",
                "results",
                "Memory T-cell repertoire changes across early childhood and adolescence.",
                doi=f"10.1234/life-course.{index}",
            )
            for index in range(1, 5)
        ],
    }

    grade = grade_evidence_pack(pack)

    assert grade["metrics"]["question_terms"] == [
        "memory",
        "cell",
        "repertoire",
        "changes",
        "early",
        "childhood",
        "adolescence",
    ]
    assert grade["grade"] == "good"


def test_named_report_attribution_and_output_directives_are_not_scientific_terms() -> None:
    question = (
        "According to Sakoguchi et al. (Nature, 2025), which RIFIN-bound "
        "receptor is inhibitory and which is activating, and what happened to "
        "NK-cell activation in each case? Separate the study's own experiments "
        "from its background discussion of LILRB1 and LAIR1."
    )

    assert question_relevance_terms(question) == [
        "rifin",
        "bound",
        "receptor",
        "inhibitory",
        "activating",
        "cell",
        "activation",
        "lilrb1",
        "lair1",
    ]


def test_named_report_background_cannot_be_replaced_by_other_direct_papers() -> None:
    question = (
        "In Joste et al. (2020), were A-type ICAM-1-binding PfEMP1 domains "
        "enriched in cerebral malaria? Report the groups, assays, direction of "
        "the ICAM-1 result, and the contrasting EPCR result without turning the "
        "null finding into evidence that ICAM-1 adhesion never occurs."
    )
    pack = {
        "question": question,
        "chunks": [
            {
                **evidence_chunk(
                    "JOSTE",
                    "abstract",
                    (
                        "Cerebral malaria involves infected erythrocyte adhesion to "
                        "brain endothelium through surface antigens and receptors."
                    ),
                    doi="10.1128/mbio.02103-20",
                ),
                "author": "Joste",
                "year": "2020",
                "title": (
                    "PfEMP1 A-Type ICAM-1-Binding Domains Are Not Associated with "
                    "Cerebral Malaria in Beninese Children"
                ),
                "source_type_assessment": {"source_type": "primary_research"},
            },
            {
                **evidence_chunk(
                    "OTHER",
                    "results",
                    (
                        "We found that A-type ICAM-1-binding PfEMP1 domains were not "
                        "enriched in cerebral malaria, whereas EPCR expression was "
                        "higher; the null result does not exclude ICAM-1 adhesion."
                    ),
                    doi="10.1000/other",
                ),
                "author": "Other",
                "year": "2021",
                "source_type_assessment": {"source_type": "primary_research"},
            },
        ],
    }

    grade = grade_evidence_pack(pack)

    assert grade["grade"] in {"bad", "weak"}
    assert "named_report_direct_passage_absent" in grade["reasons"]
    assert grade["metrics"]["explicit_report_match_count"] == 1
    assert grade["bounded_direct_report"]["supported"] is False


def test_named_report_direct_passage_opens_bounded_report_path() -> None:
    question = (
        "In Joste et al. (2020), were A-type ICAM-1-binding PfEMP1 domains "
        "enriched in cerebral malaria? Report the groups, assays, direction of "
        "the ICAM-1 result, and the contrasting EPCR result without turning the "
        "null finding into evidence that ICAM-1 adhesion never occurs."
    )
    pack = {
        "question": question,
        "chunks": [
            {
                **evidence_chunk(
                    "JOSTE",
                    "abstract",
                    (
                        "In this study, we compared cerebral-malaria and uncomplicated-"
                        "malaria groups using ICAM-1 adhesion assays and PfEMP1 domain "
                        "RT-qPCR. We found that A-type ICAM-1-binding domains were not "
                        "enriched, a null result, whereas EPCR-binding expression was "
                        "higher in cerebral malaria."
                    ),
                    doi="10.1128/mbio.02103-20",
                ),
                "author": "Joste",
                "year": "2020",
                "title": (
                    "PfEMP1 A-Type ICAM-1-Binding Domains Are Not Associated with "
                    "Cerebral Malaria in Beninese Children"
                ),
                "source_type_assessment": {"source_type": "primary_research"},
            }
        ],
    }

    grade = grade_evidence_pack(pack)

    assert grade["grade"] == "usable"
    assert "named_report_direct_passage_absent" not in grade["reasons"]
    assert grade["bounded_direct_report"]["supported"] is True
    assert grade["bounded_direct_report"]["independence_certified"] is False


def test_named_report_metrics_cannot_be_completed_by_other_papers() -> None:
    question = (
        "In Smith et al. (2024), was receptor activation higher or lower, and "
        "which assay established the direction?"
    )
    pack = {
        "question": question,
        "chunks": [
            {
                **evidence_chunk(
                    "TARGET",
                    "abstract",
                    "We measured receptor activation in the target cohort.",
                    doi="10.1000/target",
                ),
                "author": "Smith",
                "year": "2024",
                "title": "Receptor activation in the target cohort",
                "source_type_assessment": {"source_type": "primary_research"},
            },
            {
                **evidence_chunk(
                    "OTHER",
                    "results",
                    (
                        "We found higher receptor activation with the requested assay, "
                        "rather than lower activation."
                    ),
                    doi="10.1000/other",
                ),
                "author": "Other",
                "year": "2025",
                "source_type_assessment": {"source_type": "primary_research"},
            },
        ],
    }

    grade = grade_evidence_pack(pack)

    assert grade["grade"] in {"bad", "weak"}
    assert grade["metrics"]["question_term_coverage"] < 0.75
    assert grade["metrics"]["explicit_report_match_count"] == 1


def test_singular_named_report_wording_cannot_borrow_another_papers_result() -> None:
    chunks = [
        {
            **evidence_chunk(
                "TARGET",
                "abstract",
                (
                    "This abstract provides general receptor background but "
                    "does not state the requested experiment or outcome."
                ),
                doi="10.1000/target",
            ),
            "author": "Smith",
            "year": "2024",
            "explicit_named_study_anchor": True,
            "named_study_surname": "Smith",
            "named_study_year": "2024",
            "source_type_assessment": {"source_type": "primary_research"},
        },
        {
            **evidence_chunk(
                "OTHER",
                "results",
                (
                    "We found higher receptor activation using the requested "
                    "assay and directly report its direction in this detailed "
                    "primary analysis passage."
                ),
                doi="10.1000/other",
            ),
            "author": "Other",
            "year": "2025",
            "source_type_assessment": {"source_type": "primary_research"},
        },
    ]

    for attribution in ("Smith et al. (2024)", "Smith et al."):
        question = (
            f"What did {attribution} report about receptor activation and "
            "assay direction?"
        )
        grade = grade_evidence_pack({"question": question, "chunks": chunks})

        assert grade["grade"] in {"bad", "weak"}
        assert grade["bounded_direct_report"]["supported"] is False
        assert grade["bounded_direct_report"]["evidence"] == []
        assert grade["metrics"]["explicit_report_match_count"] == 1
        assert "named_report_direct_passage_absent" in grade["reasons"]


def test_singular_named_report_wording_accepts_exact_uncertain_direct_anchor() -> None:
    direct = {
        **evidence_chunk(
            "TARGET",
            "abstract",
            (
                "Here we measured receptor activation with a functional assay "
                "and found higher activation in the exposed group. Our results "
                "report the direction, comparison, cohort, and uncertainty for "
                "this directly observed experimental outcome."
            ),
            doi="10.1000/target",
        ),
        "author": "Smith",
        "year": "2024",
        "explicit_named_study_anchor": True,
        "named_study_surname": "Smith",
        "named_study_year": "2024",
        "source_type_assessment": {"source_type": "uncertain"},
    }

    bounded = []
    for attribution in ("Smith et al. (2024)", "Smith et al."):
        question = (
            f"What did {attribution} report about receptor activation and "
            "assay direction?"
        )
        bounded.append(
            grade_evidence_pack(
                {"question": question, "chunks": [direct]}
            )["bounded_direct_report"]
        )
    question = (
        "What did Smith et al. report about receptor activation and "
        "assay direction?"
    )
    generic = grade_evidence_pack(
        {
            "question": question,
            "chunks": [
                {
                    **direct,
                    "explicit_named_study_anchor": False,
                    "named_study_surname": "",
                    "named_study_year": "",
                }
            ],
        }
    )["bounded_direct_report"]

    assert all(item["supported"] is True for item in bounded)
    assert all(item["evidence"][0]["paper_id"] == "TARGET" for item in bounded)
    assert generic["supported"] is False


def test_exact_named_report_can_boundedly_correct_a_detail_heavy_false_premise() -> None:
    question = (
        "Did Ciuffreda et al. reconstruct continuous or multimodal parasite-age "
        "distributions from whole-transcriptome RNA-seq? State what they actually "
        "measured, the model input, clinical cohort, and mixed-stage limitation."
    )
    direct = {
        **evidence_chunk(
            "CIUFFREDA",
            "abstract",
            (
                "Here we found that a three-gene model estimated parasite age in "
                "clinical cohort samples. Mixed-stage samples produced an "
                "intermediate estimate rather than a continuous distribution."
            ),
            doi="10.1038/s41598-020-67817-6",
        ),
        "author": "Ciuffreda",
        "year": "2020",
        "explicit_named_study_anchor": True,
        "named_study_surname": "Ciuffreda",
        "named_study_year": "2020",
        "source_type_assessment": {"source_type": "uncertain"},
    }

    bounded = grade_evidence_pack(
        {"question": question, "chunks": [direct]}
    )["bounded_direct_report"]

    assert bounded["supported"] is True
    assert bounded["scope"] == "individual_paper_report_only"
    assert bounded["evidence"][0]["paper_id"] == "CIUFFREDA"


def test_named_report_same_author_year_prefers_resolved_identity_anchor() -> None:
    reference = {"author": "smith", "year": "2024"}
    chunks = [
        {
            "paper_id": "WRONG",
            "author": "Smith",
            "year": "2024",
            "text": "A different result.",
        },
        {
            "paper_id": "RIGHT",
            "author": "Smith",
            "year": "2024",
            "explicit_named_study_anchor": True,
            "text": "The resolved requested result.",
        },
    ]

    assert [
        chunk["paper_id"]
        for chunk in chunks_matching_explicit_report(chunks, reference)
    ] == ["RIGHT"]


def test_two_named_direct_reports_open_only_bounded_comparison_path() -> None:
    question = (
        "Compare Alpha et al. (2024) with Beta et al. (2025): which genome "
        "populations and loci were analysed, and which association was absent?"
    )
    chunks = [
        {
            **evidence_chunk(
                "ALPHA",
                "abstract",
                (
                    "Here we analysed parasite genomes from two populations and "
                    "identified three associated loci. The abstract describes the "
                    "sampling frame, the genome-wide method, and the population "
                    "comparison used for this direct result in sufficient detail."
                ),
                doi="10.1000/alpha",
            ),
            "author": "Alpha",
            "year": "2024",
            "explicit_named_study_anchor": True,
            "named_study_surname": "Alpha",
            "named_study_year": "2024",
            "source_type_assessment": {"source_type": "uncertain"},
        },
        {
            **evidence_chunk(
                "BETA",
                "abstract",
                (
                    "We performed genome analysis in a third population and found "
                    "that one locus was absent while another association remained. "
                    "Our results specify the cohort, genotyping assay, comparison, "
                    "and uncertainty for this directly reported negative finding."
                ),
                doi="10.1000/beta",
            ),
            "author": "Beta",
            "year": "2025",
            "explicit_named_study_anchor": True,
            "named_study_surname": "Beta",
            "named_study_year": "2025",
            "source_type_assessment": {"source_type": "uncertain"},
        },
    ]

    grade = grade_evidence_pack({"question": question, "chunks": chunks})

    assert grade["bounded_direct_report"]["supported"] is True
    assert grade["bounded_direct_report"]["scope"] == (
        "named_report_comparison_only"
    )
    assert grade["bounded_direct_report"]["named_report_match_count"] == 2
    assert grade["bounded_direct_report"]["independence_certified"] is False

    missing = grade_evidence_pack(
        {"question": question, "chunks": chunks[:1]}
    )
    assert missing["bounded_direct_report"]["supported"] is False


def test_descriptive_paper_comparison_resolves_two_unique_direct_reports() -> None:
    question = (
        "Compare the Benjamini-Yekutieli dependency paper with the original "
        "maximal marginal relevance paper. What problem does each solve, under "
        "which assumptions, and why is MMR not a multiple-testing correction?"
    )
    chunks = [
        {
            **evidence_chunk(
                "BY",
                "unknown",
                (
                    "A prior procedure controlled FDR for independent statistics. "
                    "We prove that it also controls the false discovery rate under "
                    "positive regression dependency in multiple testing. The theorem "
                    "states the dependency condition and distinguishes it from the "
                    "earlier independent-test setting."
                ),
                doi="10.1214/aos/1013699998",
            ),
            "evidence_id": "E001",
            "author": "Benjamini",
            "year": "2001",
            "title": (
                "The control of the false discovery rate in multiple testing "
                "under dependency"
            ),
            "source_type_assessment": {"source_type": "uncertain"},
        },
        {
            **evidence_chunk(
                "MMR",
                "abstract",
                (
                    "Given a query and a retrieved document set, MMR incrementally "
                    "balances relevance against diversity during reranking and "
                    "produces a maximal diversity ranking at lambda zero. The method "
                    "selects documents from the unselected set using query similarity "
                    "and redundancy with already selected documents."
                ),
                doi="10.1145/290941.291025",
            ),
            "evidence_id": "E002",
            "author": "Carbonell",
            "year": "1998",
            "title": (
                "The use of MMR, diversity-based reranking for reordering "
                "documents and producing summaries"
            ),
            "source_type_assessment": {"source_type": "uncertain"},
        },
        {
            **evidence_chunk(
                "DISTRACTOR_A",
                "results",
                "We found a batch correction result under unrelated assumptions.",
                doi="10.1000/a",
            ),
            "evidence_id": "E003",
            "title": "Single-cell batch correction",
            "source_type_assessment": {"source_type": "primary_research"},
        },
        {
            **evidence_chunk(
                "DISTRACTOR_B",
                "results",
                "We measured overlap among systematic reviews and reports.",
                doi="10.1000/b",
            ),
            "evidence_id": "E004",
            "title": "Evidence overlap assessment",
            "source_type_assessment": {"source_type": "primary_research"},
        },
    ]

    assert [
        chunk["evidence_id"]
        for chunk in resolved_report_comparison_chunks(question, chunks)
    ] == ["E001", "E002"]
    grade = grade_evidence_pack({"question": question, "chunks": chunks})

    assert grade["grade"] in {"usable", "good"}
    assert "fragmented_question_term_coverage" not in grade["reasons"]
    assert grade["bounded_direct_report"]["supported"] is True
    assert grade["bounded_direct_report"]["scope"] == (
        "resolved_report_comparison_only"
    )
    assert grade["bounded_direct_report"]["independence_certified"] is False
    assert grade["metrics"]["comparison_target_count"] == 2


def test_descriptive_comparison_resolution_fails_closed_on_missing_or_ambiguous_target() -> None:
    question = (
        "Compare the Benjamini-Yekutieli dependency paper with the original "
        "maximal marginal relevance paper."
    )
    by = {
        **evidence_chunk(
            "BY",
            "unknown",
            (
                "We prove FDR control under positive dependency in multiple testing. "
                "The theorem specifies the relevant dependency condition for true null "
                "hypotheses and compares it with the independent-statistics procedure."
            ),
            doi="10.1214/aos/1013699998",
        ),
        "evidence_id": "E001",
        "author": "Benjamini",
        "title": "False discovery rate under dependency",
        "source_type_assessment": {"source_type": "uncertain"},
    }
    mmr = {
        **evidence_chunk(
            "MMR_A",
            "abstract",
            (
                "MMR reranks retrieved documents using relevance and diversity. The "
                "method incrementally selects from unselected documents by combining "
                "query similarity with redundancy against already selected documents."
            ),
            doi="10.1145/one",
        ),
        "evidence_id": "E002",
        "title": "The use of MMR for document reranking",
        "source_type_assessment": {"source_type": "uncertain"},
    }
    ambiguous_mmr = {
        **mmr,
        "paper_id": "MMR_B",
        "evidence_id": "E003",
        "doi": "10.1145/two",
    }

    assert resolved_report_comparison_chunks(question, [by]) == []
    assert resolved_report_comparison_chunks(
        question, [by, mmr, ambiguous_mmr]
    ) == []


def test_descriptive_comparison_resolution_requires_distinct_clean_doi_reports() -> None:
    question = "Compare the Benjamini dependency MMR paper with the MMR paper."
    same = {
        **evidence_chunk(
            "ONE",
            "abstract",
            (
                "We prove a dependency result and define MMR relevance reranking. "
                "The report supplies a sufficiently long direct description of both "
                "methods, their assumptions, and the document selection procedure."
            ),
            doi="10.1000/one",
        ),
        "evidence_id": "E001",
        "author": "Benjamini",
        "title": "Dependency and MMR relevance",
        "source_type_assessment": {"source_type": "uncertain"},
    }
    assert resolved_report_comparison_chunks(question, [same]) == []

    no_doi = {**same, "paper_id": "NO_DOI", "doi": ""}
    reference_like = {
        **same,
        "paper_id": "REFERENCES",
        "doi": "10.1000/references",
        "quality_flags": ["reference_like"],
    }
    assert resolved_report_comparison_chunks(question, [no_doi, reference_like]) == []


def test_generic_concept_comparison_does_not_open_report_resolution() -> None:
    question = "Compare FDR control with document reranking under dependency."
    chunks = [
        {
            **evidence_chunk(
                "P1",
                "results",
                "We found FDR control under dependency.",
                doi="10.1000/one",
            ),
            "evidence_id": "E001",
            "title": "FDR dependency",
            "source_type_assessment": {"source_type": "primary_research"},
        },
        {
            **evidence_chunk(
                "P2",
                "results",
                "We found relevance-diversity document reranking.",
                doi="10.1000/two",
            ),
            "evidence_id": "E002",
            "title": "Document reranking",
            "source_type_assessment": {"source_type": "primary_research"},
        },
    ]

    assert resolved_report_comparison_chunks(question, chunks) == []
    bounded = grade_evidence_pack({"question": question, "chunks": chunks})[
        "bounded_direct_report"
    ]
    assert bounded["scope"] == "individual_paper_report_only"
    assert bounded["scope"] != "resolved_report_comparison_only"


def test_epistemic_causal_comparison_uses_named_support_but_positive_prompt_stays_blocked() -> None:
    question = (
        "Compare Sakoguchi et al. (2025) and Kassegne et al. (2025). "
        "Does either paper demonstrate that receptor binding itself causes a "
        "reduction in severe-malaria incidence?"
    )
    chunks = [
        named_comparison_chunk(
            "SAKOGUCHI",
            "E001",
            "Sakoguchi",
            "2025",
            (
                "Here we identified RIFIN binding to KIR2DL1 and measured NK-cell "
                "signalling. We performed receptor-binding and structural assays and "
                "observed suppressed activation in KIR2DL1-expressing cells, while "
                "KIR2DS1 engagement produced activation in the reported experiments."
            ),
            anchor=True,
        ),
        named_comparison_chunk(
            "KASSEGNE",
            "E002",
            "Kassegne",
            "2025",
            "Article header and background on severe malaria risk.",
            anchor=True,
            section="unknown",
        ),
        named_comparison_chunk(
            "KASSEGNE",
            "E003",
            "Kassegne",
            "2025",
            (
                "We demonstrated LILRB1 receptor binding in structural assays and "
                "reported that antibodies correlated with reduced severe malaria risk. "
                "The analysis combined clinical association measurements with "
                "structural modelling, molecular dynamics, and receptor mutagenesis."
            ),
            support=True,
        ),
    ] + [
        {
            **evidence_chunk(
                f"DISTRACTOR_{index}",
                "results",
                (
                    "We reported an unrelated direct malaria result with enough "
                    "narrative context for evidence-pack quality accounting."
                ),
                doi=f"10.1000/distractor.{index}",
            ),
            "evidence_id": f"E{index + 3:03d}",
            "source_type_assessment": {"source_type": "primary_research"},
        }
        for index in range(1, 7)
    ]

    grade = grade_evidence_pack({"question": question, "chunks": chunks})

    assert epistemic_causal_comparison_question(question) is True
    assert grade["grade"] in {"usable", "good"}
    assert grade["bounded_direct_report"]["supported"] is True
    assert grade["bounded_direct_report"]["blocked_question_scope"] is False
    assert [
        item["evidence_id"] for item in grade["bounded_direct_report"]["evidence"]
    ] == ["E001", "E003"]

    positive = question.replace(
        "Does either paper demonstrate that",
        "Which paper demonstrates that",
    )
    positive_bounded = grade_evidence_pack(
        {"question": positive, "chunks": chunks}
    )["bounded_direct_report"]
    assert epistemic_causal_comparison_question(positive) is False
    assert positive_bounded["supported"] is False
    assert positive_bounded["blocked_question_scope"] is True


def test_multi_report_scope_wins_when_question_starts_with_in_attribution() -> None:
    question = (
        "In Alpha et al. (2024), compare the reported genome populations and "
        "loci with Beta et al. (2025), including which association was absent."
    )
    chunks = [
        {
            **evidence_chunk(
                "ALPHA",
                "abstract",
                (
                    "Here we analysed parasite genomes from two populations and "
                    "identified three associated loci. The abstract describes the "
                    "sampling frame, genome-wide method, and direct population result."
                    " Additional analyses quantified uncertainty across the sampled "
                    "parasite population."
                ),
                doi="10.1000/alpha",
            ),
            "author": "Alpha",
            "year": "2024",
            "explicit_named_study_anchor": True,
            "named_study_surname": "Alpha",
            "named_study_year": "2024",
            "source_type_assessment": {"source_type": "primary_research"},
        },
        {
            **evidence_chunk(
                "BETA",
                "abstract",
                (
                    "We performed genome analysis in a third population and found "
                    "that one locus was absent while another association remained. "
                    "Our results describe the cohort, assay, and negative finding, "
                    "including uncertainty across the sampled parasite population."
                ),
                doi="10.1000/beta",
            ),
            "author": "Beta",
            "year": "2025",
            "explicit_named_study_anchor": True,
            "named_study_surname": "Beta",
            "named_study_year": "2025",
            "source_type_assessment": {"source_type": "primary_research"},
        },
    ]

    grade = grade_evidence_pack({"question": question, "chunks": chunks})

    assert grade["bounded_direct_report"]["supported"] is True
    assert grade["bounded_direct_report"]["scope"] == (
        "named_report_comparison_only"
    )
    assert grade["bounded_direct_report"]["named_report_match_count"] == 2
    assert grade["metrics"]["named_report_anchor_count"] == 2
    assert "named_report_direct_passage_absent" not in grade["reasons"]


def test_fragmented_memory_t_cell_pack_cannot_open_synthesis() -> None:
    question = (
        "i want to know about how the memory t cell repertoire changes from "
        "early childhood to adolescence. Please provide literature."
    )
    fragments = [
        (
            "IL-27 regulates malaria-specific memory CD4+ T cells",
            "The early antibody response included long-lived memory B cells.",
        ),
        (
            "B-cell receptor repertoire sequencing",
            "Heavy and light chains were assembled in individual human B cells.",
        ),
        (
            "Antibody lineage diversification in young children",
            "Immune repertoire sequencing was performed after malaria infection.",
        ),
        (
            "Single-cell monocyte responses in uncomplicated malaria",
            "Innate immune pathways have been studied after childhood exposure.",
        ),
        (
            "Clinical immunity during repeated malaria",
            "Longitudinal changes in parasitaemia were measured over 18 months.",
        ),
        (
            "Recall responses after reinfection",
            "Participants were enrolled during adolescence.",
        ),
        (
            "Macrophages and malaria transmission",
            "Monocytes altered mosquito midgut microbiota.",
        ),
        (
            "Inflammatory monocytes during infection",
            "Myelopoiesis was compared during sepsis and COVID-19.",
        ),
    ]
    pack = {
        "question": question,
        "chunks": [
            {
                **evidence_chunk(
                    f"P{index}",
                    "discussion",
                    text,
                    doi=f"10.1234/fragment.{index}",
                ),
                "title": title,
                "source_type_assessment": {"source_type": "primary_research"},
            }
            for index, (title, text) in enumerate(fragments, start=1)
        ],
    }

    grade = grade_evidence_pack(pack)

    assert grade["metrics"]["question_term_coverage"] == 1.0
    assert grade["metrics"]["max_passage_question_term_coverage"] < 0.5
    assert grade["metrics"]["joint_question_coverage_supported"] is False
    assert "fragmented_question_term_coverage" in grade["reasons"]
    assert grade["grade"] in {"bad", "weak"}
    assert grade["score"] < 60
    assert grade["bounded_direct_report"]["supported"] is False


def test_joint_anchor_passage_preserves_legitimate_multi_paper_synthesis() -> None:
    pack = {
        "question": (
            "How does the memory T cell repertoire change from early childhood "
            "to adolescence?"
        ),
        "chunks": [
            evidence_chunk(
                "P1",
                "results",
                "Memory T-cell repertoire trajectories were measured across childhood.",
                doi="10.1234/anchor",
            ),
            evidence_chunk(
                "P2",
                "results",
                "Age-associated change was measured longitudinally.",
                doi="10.1234/change",
            ),
            evidence_chunk(
                "P3",
                "abstract",
                "The early pediatric time point preceded adolescence.",
                doi="10.1234/age",
            ),
            evidence_chunk(
                "P4",
                "methods",
                "Participants were sampled in four age strata.",
                doi="10.1234/methods",
            ),
        ],
    }

    grade = grade_evidence_pack(pack)

    assert grade["metrics"]["question_term_coverage"] == 1.0
    assert grade["metrics"]["joint_question_coverage_supported"] is True
    assert "fragmented_question_term_coverage" not in grade["reasons"]
    assert grade["grade"] == "good"


def test_german_question_is_graded_against_canonical_scientific_concepts() -> None:
    pack = {
        "question": (
            "Was spricht für und gegen einen kausalen Zusammenhang zwischen "
            "EPCR-bindendem PfEMP1 und zerebraler Malaria?"
        ),
        "chunks": [
            evidence_chunk(
                f"P{index}",
                "results",
                "EPCR-binding PfEMP1 was associated with cerebral malaria.",
                doi=f"10.1234/epcr.{index}",
            )
            for index in range(1, 5)
        ],
    }

    grade = grade_evidence_pack(pack)

    assert grade["grade"] == "good"
    assert grade["metrics"]["question_terms"] == [
        "epcr",
        "binding",
        "pfemp1",
        "cerebral",
    ]
    assert grade["metrics"]["question_term_coverage"] == 1.0


def test_german_question_still_rejects_unrelated_evidence() -> None:
    pack = {
        "question": "Welche SPP1-positiven Makrophagensignaturen werden bei schwerem COVID-19 berichtet?",
        "chunks": [
            evidence_chunk(
                f"Z{index}",
                "results",
                "Zebrafish fin regeneration quantified developmental morphology.",
                doi=f"10.1234/zebrafish.{index}",
            )
            for index in range(1, 5)
        ],
    }

    grade = grade_evidence_pack(pack)

    assert grade["grade"] in {"bad", "weak"}
    assert "very_low_question_term_coverage" in grade["reasons"]
    assert "very_low_question_relevant_chunk_fraction" in grade["reasons"]


def test_expected_term_matching_respects_word_boundaries() -> None:
    pack = {
        "chunks": [
            evidence_chunk(f"P{index}", "results", "Developmental stages were compared.")
            for index in range(1, 5)
        ]
    }

    grade = grade_evidence_pack(pack, expected_terms=["age"])

    assert grade["metrics"]["expected_term_hits"] == []
    assert grade["metrics"]["expected_term_coverage"] == 0.0


def test_empty_evidence_pack_is_bad() -> None:
    grade = grade_evidence_pack({"chunks": []})

    assert grade["grade"] == "bad"
    assert grade["score"] == 0
    assert "no_evidence_chunks" in grade["reasons"]


def test_noisy_single_paper_pack_is_penalized() -> None:
    pack = {
        "chunks": [
            evidence_chunk("P1", "references", "References. Lancet. Nature.", quality_flags=["reference_like"]),
            evidence_chunk("P1", "table", "Table 1", quality_flags=["table_like"]),
            evidence_chunk("P1", "figure_legend", "Figure legend", quality_flags=["figure_like"]),
            evidence_chunk("P1", "supplement", "Supplementary appendix", quality_flags=["supplement_like"]),
        ]
    }

    grade = grade_evidence_pack(pack, expected_terms=["PfEMP1", "EPCR"])

    assert grade["grade"] in {"bad", "weak"}
    assert "low_paper_diversity" in grade["reasons"]
    assert "too_many_problem_sections" in grade["reasons"]
    assert "too_many_quality_flags" in grade["reasons"]


def test_weak_direct_primary_abstract_supports_only_bounded_paper_report() -> None:
    pack = {
        "question": (
            "Welche SPP1-positiven Makrophagensignaturen werden bei schwerem "
            "COVID-19 berichtet?"
        ),
        "chunks": [
            {
                **evidence_chunk(
                    "LIAO",
                    "abstract",
                    (
                        "SPP1+ macrophages were enriched in patients with severe disease. "
                        "The single-cell analysis compared bronchoalveolar immune-cell "
                        "populations across clinical severity groups and described the "
                        "inflammatory macrophage population in substantial narrative prose. "
                    )
                    * 2,
                    doi="10.1038/s41591-020-0901-9",
                    quality_flags=["figure_like"],
                ),
                "title": "Single-cell landscape of immune cells in patients with COVID-19",
                "source_type_assessment": {"source_type": "primary_research"},
            }
        ],
    }

    grade = grade_evidence_pack(pack)

    assert grade["grade"] == "weak"
    assert grade["bounded_direct_report"]["supported"] is True
    assert grade["bounded_direct_report"]["independence_certified"] is False
    assert grade["bounded_direct_report"]["evidence"][0]["paper_id"] == "LIAO"


def test_direct_abstract_with_figure_pointers_and_brief_attribution_stays_eligible() -> None:
    pack = {
        "question": (
            "What SPP1-positive macrophage states are reported in COVID-19, and how "
            "are they associated with disease severity?"
        ),
        "chunks": [
            {
                **evidence_chunk(
                    "LIAO",
                    "abstract",
                    (
                        "According to recent classification criteria4, we re-clustered "
                        "20 macrophage subclusters (Extended Data Fig. 2a). Macrophages "
                        "included FCN1loSPP1+ group 2 and SPP1+ group 3. FCN1 and SPP1 "
                        "were highly expressed in severe or critical COVID-19 compared "
                        "with moderate infection (Fig. 1d)."
                    ),
                    doi="10.1038/s41591-020-0901-9",
                    quality_flags=["figure_like"],
                ),
                "title": "Single-cell landscape of immune cells in patients with COVID-19",
                "source_type_assessment": {"source_type": "primary_research"},
            }
        ],
    }

    bounded = grade_evidence_pack(pack)["bounded_direct_report"]

    assert bounded["supported"] is True
    assert bounded["evidence"][0]["paper_id"] == "LIAO"


def test_unrelated_or_structurally_noisy_single_source_cannot_support_bounded_report() -> None:
    base = {
        "question": "Which SPP1 macrophages are reported in severe COVID-19?",
        "chunks": [
            {
                **evidence_chunk(
                    "P1",
                    "results",
                    "Zebrafish regeneration quantified fin morphology.",
                    doi="10.1000/unrelated",
                ),
                "source_type_assessment": {"source_type": "primary_research"},
            }
        ],
    }
    noisy = {
        **base,
        "chunks": [
            {
                **base["chunks"][0],
                "text": "SPP1 macrophages severe COVID-19 " * 20,
                "quality_flags": ["reference_like"],
            }
        ],
    }

    assert grade_evidence_pack(base)["bounded_direct_report"]["supported"] is False
    assert grade_evidence_pack(noisy)["bounded_direct_report"]["supported"] is False


def test_direct_single_source_cannot_open_bounded_gate_for_causal_question() -> None:
    pack = {
        "question": "Verursachen SPP1-Makrophagen kausal schweres COVID-19?",
        "chunks": [
            {
                **evidence_chunk(
                    "LIAO",
                    "results",
                    "SPP1 macrophages were associated with severe COVID-19. " * 10,
                    doi="10.1038/direct",
                ),
                "source_type_assessment": {"source_type": "primary_research"},
            }
        ],
    }

    bounded = grade_evidence_pack(pack)["bounded_direct_report"]

    assert bounded["supported"] is False
    assert bounded["blocked_question_scope"] is True


def test_discussion_background_or_cited_prior_claim_cannot_open_bounded_report() -> None:
    question = "Which SPP1 macrophages are reported in severe COVID-19?"
    common = {
        "title": "SPP1 macrophages in severe COVID-19",
        "doi": "10.1000/direct",
        "source_type_assessment": {"source_type": "primary_research"},
        "quality_flags": [],
    }
    unsafe_texts = [
        (
            "discussion",
            "Previous studies reported SPP1 macrophages in severe COVID-19.",
        ),
        (
            "discussion",
            "SPP1 macrophages were associated with severe COVID-19 (Smith et al., 2020).",
        ),
        (
            "background",
            "SPP1 macrophages were associated with severe COVID-19.",
        ),
    ]

    for section, text in unsafe_texts:
        grade = grade_evidence_pack(
            {"question": question, "chunks": [{**common, "section": section, "text": text}]}
        )
        assert grade["bounded_direct_report"]["supported"] is False


def test_attributed_own_result_in_discussion_can_open_bounded_report() -> None:
    grade = grade_evidence_pack(
        {
            "question": "Which SPP1 macrophages are reported in severe COVID-19?",
            "chunks": [
                {
                    "paper_id": "P1",
                    "title": "SPP1 macrophages in severe COVID-19",
                    "section": "discussion",
                    "text": (
                        "We found that SPP1 macrophages were enriched in severe COVID-19 "
                        "and observed the same direct comparison in our analysis."
                    ),
                    "doi": "10.1000/direct",
                    "quality_flags": [],
                    "source_type_assessment": {"source_type": "primary_research"},
                }
            ],
        }
    )

    assert grade["bounded_direct_report"]["supported"] is True


def test_drive_determine_mediate_and_cross_study_scope_remain_blocked() -> None:
    questions = [
        "Do SPP1 macrophages drive severe COVID-19?",
        "Do SPP1 macrophages determine severe COVID-19?",
        "Do SPP1 macrophages mediate severe COVID-19?",
        "Do all studies agree about SPP1 macrophages in severe COVID-19?",
        "Is the SPP1 result consistent across studies?",
    ]
    chunk = {
        "paper_id": "P1",
        "title": "SPP1 macrophages in severe COVID-19",
        "section": "results",
        "text": "SPP1 macrophages were enriched in severe COVID-19.",
        "doi": "10.1000/direct",
        "quality_flags": [],
        "source_type_assessment": {"source_type": "primary_research"},
    }

    for question in questions:
        bounded = grade_evidence_pack(
            {"question": question, "chunks": [chunk]}
        )["bounded_direct_report"]
        assert bounded["supported"] is False
        assert bounded["blocked_question_scope"] is True


def evidence_chunk(
    paper_id: str,
    section: str,
    text: str,
    *,
    doi: str = "",
    quality_flags: list[str] | None = None,
) -> dict:
    return {
        "paper_id": paper_id,
        "title": f"{paper_id} title",
        "journal": "Test Journal",
        "section": section,
        "text": text,
        "doi": doi,
        "quality_flags": quality_flags or [],
    }


def named_comparison_chunk(
    paper_id: str,
    evidence_id: str,
    author: str,
    year: str,
    text: str,
    *,
    anchor: bool = False,
    support: bool = False,
    section: str = "abstract",
) -> dict:
    return {
        **evidence_chunk(
            paper_id,
            section,
            text,
            doi=f"10.1000/{paper_id.lower()}",
        ),
        "evidence_id": evidence_id,
        "author": author,
        "year": year,
        "title": f"{author} receptor binding and severe malaria",
        "explicit_named_study_anchor": anchor,
        "explicit_named_study_support": support,
        "named_study_surname": author,
        "named_study_year": year,
        "source_type_assessment": {"source_type": "uncertain"},
    }
