from __future__ import annotations

import pytest

from pfempire.retrieval.query_plan import (
    ADOLESCENCE_ALIASES,
    EARLY_CHILDHOOD_ALIASES,
    MEMORY_T_CELL_ALIASES,
    PARASITE_OMICS_ANCHOR,
    RIFIN_STEVOR_ALTERNATIVE_ALIASES,
    T_CELL_IDENTITY_ALIASES,
    T_CELL_REPERTOIRE_ALIASES,
    build_retrieval_queries,
    classify_paper_search_mode,
    explicit_named_study_references,
    required_context_anchor_groups,
    required_entity_anchor_groups,
    source_lookup_query,
    unwrap_explicit_query_instruction,
)


def test_spp1_question_gets_required_symbol_and_alias_query() -> None:
    question = "Do you find any information about spp1 gene signatures in whatever?"

    queries = build_retrieval_queries(question, task="answer", max_queries=4)

    assert required_entity_anchor_groups(question) == [
        (
            "spp1",
            "osteopontin",
            "ostéopontine",
            "osteopontine",
            "osteopontina",
            "secreted phosphoprotein 1",
        )
    ]
    assert queries[1].label == "required_entity_1"
    assert queries[1].intent == "required_entity"
    assert queries[1].query == "spp1 osteopontin"


def test_generic_entity_anchor_is_conservative_but_keeps_explicit_symbols() -> None:
    assert required_entity_anchor_groups("What is the type1 interferon gene signature?") == []
    assert required_entity_anchor_groups("What is the CD8 T-cell gene signature?") == [
        ("cd8",)
    ]


def test_state_suffixes_do_not_become_impossible_compound_anchors() -> None:
    question = (
        "What SPP1-positive macrophage signatures are reported, and how are they "
        "associated with COVID-19 severity?"
    )

    assert required_entity_anchor_groups(question) == [
        (
            "spp1",
            "osteopontin",
            "ostéopontine",
            "osteopontine",
            "osteopontina",
            "secreted phosphoprotein 1",
        )
    ]
    assert required_entity_anchor_groups(
        "Which SPP1-positive macrophages occur in SARS-CoV-2 infection?"
    ) == [
        (
            "spp1",
            "osteopontin",
            "ostéopontine",
            "osteopontine",
            "osteopontina",
            "secreted phosphoprotein 1",
        )
    ]
    assert required_entity_anchor_groups("Which CD8high gene state is reported?") == [
        ("cd8",)
    ]


def test_uppercase_gene_without_digit_can_be_required() -> None:
    assert required_entity_anchor_groups("What APOE expression signature is reported?") == [
        ("apoe",)
    ]


def test_identifier_labels_never_become_biological_entity_anchors() -> None:
    question = (
        "Do DOI 10.1101/example and DOI 10.1093/example report the same study "
        "about gene expression in cerebral malaria?"
    )

    assert required_entity_anchor_groups(question) == []


def test_badly_phrased_cell_state_questions_still_require_the_entity() -> None:
    aliases = (
        "spp1",
        "osteopontin",
        "ostéopontine",
        "osteopontine",
        "osteopontina",
        "secreted phosphoprotein 1",
    )

    assert required_entity_anchor_groups(
        "list pappers w SPP1+ makrophags in diseae, whatevr lol"
    ) == [aliases]
    assert required_entity_anchor_groups(
        "What does osteopontin-positive macrophage mean in COVID?"
    ) == [aliases]
    assert required_entity_anchor_groups(
        "find ZZZ999-positive macrophages in covid and tell me the mechanism"
    ) == [("zzz999",)]
    assert required_entity_anchor_groups(
        "list pappers w ZZZ999+ makrophags in malaria, whatevr lol"
    ) == [("zzz999",)]
    assert required_entity_anchor_groups(
        "CD8high stuff in infection what is going on"
    ) == [("cd8",)]
    assert required_entity_anchor_groups(
        "Que sait-on des macrophages ostéopontine-positifs dans le COVID sévère ?"
    ) == [aliases]
    assert required_entity_anchor_groups(
        "sppp111 makrofaggg x coviddd ??? papers plz"
    ) == [("sppp111",)]


def test_named_disease_context_is_required_without_generic_disease_word() -> None:
    assert required_context_anchor_groups(
        "What does osteopontin-positive macrophage mean in COVID?"
    ) == [("covid 19", "covid", "sars cov 2")]
    assert required_context_anchor_groups(
        "compare SPP1+ macrophages in COVID and fibrosis"
    ) == [
        ("covid 19", "covid", "sars cov 2", "fibrosis", "fibrotic", "ipf")
    ]
    assert required_context_anchor_groups(
        "Which paper proves SPP1 causes malaria?"
    ) == [("malaria", "plasmodium")]
    assert required_context_anchor_groups(
        "How are SPP1 macrophages associated with COVID-19 disease severity?"
    ) == [
        ("covid 19", "covid", "sars cov 2"),
        ("severity", "severe", "critical"),
    ]


def test_explicit_severity_is_a_separate_required_context() -> None:
    assert required_context_anchor_groups(
        "Which SPP1 macrophages are reported in critical COVID-19?"
    ) == [
        ("covid 19", "covid", "sars cov 2"),
        ("severity", "severe", "critical"),
    ]
    assert required_context_anchor_groups(
        "Which var genes are reported in severe malaria?"
    ) == [
        ("malaria", "plasmodium"),
        ("severity", "severe", "critical"),
    ]
    assert required_context_anchor_groups(
        "Critically review SPP1 macrophage studies in COVID-19."
    ) == [("covid 19", "covid", "sars cov 2")]


def test_cerebral_malaria_requires_both_disease_and_brain_outcome() -> None:
    assert required_context_anchor_groups(
        "What evidence links PfEMP1 expression to cerebral malaria?"
    ) == [
        ("malaria", "plasmodium"),
        ("cerebral malaria", "cerebral", "brain malaria", "brain", "retinopathy"),
    ]


def test_cerebral_or_severe_malaria_is_one_outcome_alternative_group() -> None:
    expected = [
        ("malaria", "plasmodium"),
        (
            "cerebral malaria",
            "cerebral",
            "brain malaria",
            "brain",
            "retinopathy",
            "severity",
            "severe",
            "critical",
        ),
    ]

    assert required_context_anchor_groups(
        "Find PfEMP1 associations with cerebral or severe malaria."
    ) == expected
    assert required_context_anchor_groups(
        "Find PfEMP1 associations with severe malaria or cerebral malaria."
    ) == expected


def test_disjunctive_var_or_pfemp1_uses_one_alternative_entity_group() -> None:
    question = "Which papers report var gene or PfEMP1 expression in severe malaria?"

    assert required_entity_anchor_groups(question) == [
        ("var gene", "var genes", "var", "pfemp1")
    ]
    queries = build_retrieval_queries(question, task="sources", max_queries=3)
    assert queries[1].label == "required_entity_1"
    assert queries[1].query == "var pfemp1"


def test_multi_family_clinical_expression_search_uses_one_union_anchor() -> None:
    question = (
        "xFind papers linking Plasmodium falciparum expression to clinical malaria "
        "phenotypes. Focus on var/PfEMP1, RIFINs, STEVORs, and the core parasite "
        "transcriptome. For each paper, report whether parasite developmental stage "
        "was adjusted for."
    )

    assert required_entity_anchor_groups(question) == [
        (
            "var gene",
            "var genes",
            "var",
            "pfemp1",
            "rifin",
            "rifins",
            "stevor",
            "stevors",
            PARASITE_OMICS_ANCHOR,
        )
    ]

    queries = build_retrieval_queries(question, task="sources", max_queries=6)
    assert [query.label for query in queries] == [
        "main",
        "required_entity_1",
        "var_pfemp1_clinical_expression",
        "rifin_stevor_clinical_expression",
        "core_transcriptome_clinical_expression",
        "clinical_expression_stage_adjustment",
    ]
    assert queries[1].query == "var pfemp1 rifin rifins stevor stevors transcriptome"


def test_multi_family_clinical_search_treats_phenotype_list_as_alternatives() -> None:
    question = (
        "Find papers linking Plasmodium falciparum expression to clinical malaria "
        "phenotypes. Focus on var/PfEMP1, RIFINs, STEVORs, and the core parasite "
        "transcriptome for cerebral malaria, severe anaemia, respiratory distress, "
        "severe vs uncomplicated malaria, and asymptomatic infection."
    )

    groups = required_context_anchor_groups(question)

    assert groups[0] == ("malaria", "plasmodium")
    assert len(groups) == 2
    assert "cerebral malaria" in groups[1]
    assert "severe anaemia" in groups[1]
    assert "respiratory distress" in groups[1]
    assert "uncomplicated malaria" in groups[1]
    assert "asymptomatic infection" in groups[1]


def test_single_expression_family_keeps_all_requested_phenotype_alternatives() -> None:
    question = (
        "Find papers linking PfEMP1 expression to cerebral malaria, severe anaemia, "
        "or asymptomatic infection."
    )

    assert required_entity_anchor_groups(question) == [
        ("var gene", "var genes", "var", "pfemp1")
    ]
    assert required_context_anchor_groups(question) == [
        ("malaria", "plasmodium"),
        (
            "cerebral malaria",
            "cerebral",
            "brain malaria",
            "brain",
            "retinopathy",
            "severe anemia",
            "severe anaemia",
            "anemia",
            "anaemia",
            "asymptomatic infection",
            "asymptomatic malaria",
            "asymptomatic",
        ),
    ]


def test_two_expression_families_are_one_passage_level_union() -> None:
    question = (
        "Find papers linking RIFIN and STEVOR expression to cerebral or "
        "asymptomatic malaria."
    )

    assert required_entity_anchor_groups(question) == [
        ("rifin", "rifins", "stevor", "stevors")
    ]
    groups = required_context_anchor_groups(question)
    assert groups[0] == ("malaria", "plasmodium")
    assert len(groups) == 2
    assert "cerebral malaria" in groups[1]
    assert "asymptomatic malaria" in groups[1]


def test_one_expression_family_and_one_phenotype_are_both_anchored() -> None:
    question = "Find papers on STEVOR expression in asymptomatic malaria."

    assert required_entity_anchor_groups(question) == [("stevor", "stevors")]
    assert required_context_anchor_groups(question) == [
        ("malaria", "plasmodium"),
        ("asymptomatic infection", "asymptomatic malaria", "asymptomatic"),
    ]


def test_expression_variation_does_not_activate_the_var_gene_family() -> None:
    question = "Find papers about parasite expression variation in severe malaria."

    assert required_entity_anchor_groups(question) == []


def test_multi_family_logic_does_not_change_mechanistic_comparison_to_or() -> None:
    question = "How do PfEMP1 and RIFIN differ in infected erythrocytes?"

    assert required_entity_anchor_groups(question) == []


def test_rifin_or_stevor_source_request_uses_one_alternative_group() -> None:
    question = (
        "Find primary studies on Plasmodium falciparum RIFIN or STEVOR proteins "
        "that address receptor binding, immune evasion, rosetting, host-cell "
        "remodelling, or therapeutic targeting. Explain why each paper is "
        "directly relevant."
    )

    assert required_entity_anchor_groups(question) == [
        RIFIN_STEVOR_ALTERNATIVE_ALIASES
    ]
    queries = build_retrieval_queries(question, task="sources", max_queries=8)
    labels = [query.label for query in queries]
    assert labels[:2] == ["main", "required_entity_1"]
    assert queries[1].query == "rifin rifins stevor stevors"
    assert labels[2] == "entity_function_intent"
    assert queries[2].query == (
        "rifin rifins stevor stevors receptor binding immune evasion "
        "rosetting infected erythrocyte membrane therapeutic target"
    )
    assert "required_entity_2" not in labels
    assert "host_single_cell_immunity" not in labels


def test_rifin_stevor_source_union_accepts_common_list_connectors() -> None:
    for entity_phrase in (
        "RIFINs and STEVORs",
        "RIFIN/STEVOR",
        "RIFIN and/or STEVOR",
    ):
        question = f"Find primary studies on {entity_phrase}."
        assert required_entity_anchor_groups(question) == [
            RIFIN_STEVOR_ALTERNATIVE_ALIASES
        ]


@pytest.mark.parametrize(
    ("family", "aliases"),
    [
        ("RIFIN", ("rifin", "rifins")),
        ("RIFINs", ("rifin", "rifins")),
        ("STEVOR", ("stevor", "stevors")),
        ("STEVORs", ("stevor", "stevors")),
    ],
)
def test_single_family_source_anchor_accepts_singular_and_plural(
    family: str,
    aliases: tuple[str, ...],
) -> None:
    question = f"Find the primary paper on {family} protein receptor binding."

    assert aliases in required_entity_anchor_groups(question)


def test_host_cell_does_not_match_t_cell_but_real_t_cell_still_does() -> None:
    host_cell_question = (
        "Which RIFIN proteins contribute to host-cell remodelling in malaria?"
    )
    real_t_cell_question = "How do T cells respond during falciparum malaria?"

    assert T_CELL_IDENTITY_ALIASES not in required_entity_anchor_groups(
        host_cell_question
    )
    assert "host_single_cell_immunity" not in {
        query.label
        for query in build_retrieval_queries(
            host_cell_question,
            task="sources",
            max_queries=8,
        )
    }
    assert required_entity_anchor_groups(real_t_cell_question) == [
        T_CELL_IDENTITY_ALIASES
    ]
    assert "host_single_cell_immunity" in {
        query.label
        for query in build_retrieval_queries(
            real_t_cell_question,
            task="answer",
            max_queries=8,
        )
    }


def test_host_cell_memory_repertoire_is_not_a_developmental_tcr_request() -> None:
    question = (
        "Find papers on how the host-cell memory repertoire changes from "
        "childhood through adolescence."
    )

    assert required_entity_anchor_groups(question) == []
    labels = {
        query.label
        for query in build_retrieval_queries(
            question,
            task="sources",
            max_queries=8,
        )
    }
    assert "developmental_t_cell_repertoire" not in labels
    assert "host_single_cell_immunity" not in labels


def test_general_t_cell_question_does_not_inject_malaria_single_cell_query() -> None:
    question = (
        "i want to know about how the memory t cell repertoire changes from "
        "early childhood to adolescence. Please provide literature."
    )

    queries = build_retrieval_queries(question, task="answer", max_queries=4)

    assert [query.label for query in queries] == [
        "main",
        "required_entity_1",
        "required_entity_2",
        "developmental_t_cell_repertoire",
    ]
    assert all("malaria" not in query.query.lower() for query in queries)
    assert "memory T cell TCR repertoire" in queries[-1].query
    assert required_entity_anchor_groups(question) == [
        T_CELL_REPERTOIRE_ALIASES,
        MEMORY_T_CELL_ALIASES,
    ]
    assert required_context_anchor_groups(question) == [
        EARLY_CHILDHOOD_ALIASES,
        ADOLESCENCE_ALIASES,
    ]


def test_malaria_t_cell_question_keeps_domain_specific_single_cell_expansion() -> None:
    question = "How do T cell states change during Plasmodium falciparum malaria?"
    queries = build_retrieval_queries(
        question,
        task="answer",
        max_queries=4,
    )

    assert "host_single_cell_immunity" in {query.label for query in queries}
    assert required_entity_anchor_groups(question) == [T_CELL_IDENTITY_ALIASES]
    expansion = next(
        query for query in queries if query.label == "host_single_cell_immunity"
    )
    assert "T cell state CD4 CD8" in expansion.query
    assert "monocyte" not in expansion.query.lower()


def test_non_memory_tcr_age_question_does_not_activate_memory_specific_anchors() -> None:
    question = "How does the naive TCR repertoire change from childhood to adolescence?"

    assert required_entity_anchor_groups(question) == []
    assert required_context_anchor_groups(question) == []
    assert "developmental_t_cell_repertoire" not in {
        query.label
        for query in build_retrieval_queries(question, task="answer", max_queries=4)
    }


def test_stage_confounding_source_wrapper_does_not_create_false_entities() -> None:
    question = (
        "Use this as the OriginRAG query: > Find primary research papers that "
        "address how Plasmodium falciparum developmental stage / intraerythrocytic "
        "life-cycle composition can confound differential gene-expression (DEG) "
        "and host-parasite interaction analyses in clinical malaria transcriptomics. "
        "Prioritize bulk RNA-seq or microarray studies and comparisons such as "
        "severe versus asymptomatic or uncomplicated malaria."
    )

    assert unwrap_explicit_query_instruction(question).startswith(
        "Find primary research papers"
    )
    assert required_entity_anchor_groups(question) == []
    assert required_context_anchor_groups(question) == [("malaria", "plasmodium")]
    labels = [
        query.label
        for query in build_retrieval_queries(question, task="sources", max_queries=6)
    ]
    assert labels[:4] == [
        "main",
        "clinical_stage_composition_confounding",
        "stage_estimation_deconvolution_methods",
        "stage_adjusted_clinical_transcriptomics",
    ]
    assert "disease_signature_context" not in labels
    assert "method_tools" not in labels


def test_symbol_lookup_language_requires_uppercase_symbol() -> None:
    assert required_entity_anchor_groups("papers about APOE in disease idk") == [
        ("apoe",)
    ]


def test_misspelled_disease_gene_search_gets_biological_context_query() -> None:
    queries = build_retrieval_queries(
        "list papers where they discuss theses spp1 gene signature in desease",
        task="sources",
        max_queries=3,
    )

    assert [query.label for query in queries] == [
        "main",
        "required_entity_1",
        "disease_signature_context",
    ]
    assert "fibrosis" in queries[2].query
    assert "clinical phenotype" in queries[2].query
    context_groups = required_context_anchor_groups(
        "list papers where they discuss theses spp1 gene signature in desease"
    )
    assert len(context_groups) == 1
    assert "fibrosis" in context_groups[0]
    assert "covid 19" in context_groups[0]


def test_pfrh5_development_question_fans_out_to_trials_and_barriers() -> None:
    queries = build_retrieval_queries(
        "How advanced is PfRH5-based blood-stage vaccine development, and what major barriers remain?",
        task="answer",
    )

    labels = {query.label for query in queries}
    assert "vaccine_clinical_development" in labels
    assert "vaccine_development_barriers" in labels


def test_stevor_mechanism_contrast_preserves_both_direct_experimental_axes() -> None:
    queries = build_retrieval_queries(
        "Which experiments directly support STEVOR-glycophorin-C-mediated invasion "
        "and PfEMP1-independent rosetting, and how does that evidence differ from "
        "the PfCDP/H3K9me3 conditional-knockout study of RIFIN regulation and "
        "rosetting?",
        task="answer",
    )

    assert [query.label for query in queries] == [
        "main",
        "stevor_glycophorin_rosetting",
        "rifin_chromatin_rosetting",
    ]
    assert "glycophorin C" in queries[1].query
    assert "merozoite invasion" in queries[1].query
    assert "PfEMP1-independent" in queries[1].query
    assert "conditional knockout" in queries[2].query
    assert "pfemp1_epcr_severe" not in {query.label for query in queries}


def test_stevor_vaccine_question_separates_preclinical_and_clinical_queries() -> None:
    question = (
        "Does the current corpus contain human interventional evidence that a "
        "STEVOR vaccine prevents severe malaria? Distinguish human-serum "
        "reactivity, mouse immunogenicity, in-silico predictions, and actual "
        "clinical efficacy."
    )
    queries = build_retrieval_queries(
        question,
        task="answer",
    )

    assert [query.label for query in queries] == [
        "main",
        "required_entity_1",
        "stevor_vaccine_preclinical",
        "stevor_vaccine_clinical_efficacy",
    ]
    assert "human serum" in queries[2].query
    assert "mouse immunogenicity" in queries[2].query
    assert "clinical trial" in queries[3].query
    assert "vaccine_development_barriers" not in {query.label for query in queries}
    assert required_entity_anchor_groups(question) == [("stevor", "stevors")]


def test_var_switching_rate_question_fans_out_to_quantitative_and_clinical_evidence() -> None:
    queries = build_retrieval_queries(
        "Tell me something about the switching rate of var genes.",
        task="answer",
    )

    labels = {query.label for query in queries}
    assert "var_switching_rates" in labels
    assert "clinical_var_switching" in labels


def test_virulence_question_gets_mechanism_fanout_query() -> None:
    queries = build_retrieval_queries("Warum ist Plasmodium falciparum so virulent?", task="answer")

    labels = [query.label for query in queries]
    joined = " ".join(query.query for query in queries).lower()

    assert "main" in labels
    assert "virulence_mechanisms" in labels
    assert "pfemp1" in joined
    assert "sequestration" in joined
    assert "epcr" in joined


def test_followup_context_gets_current_and_source_title_queries() -> None:
    queries = build_retrieval_queries(
        "\n".join(
            [
                "Previous user question: Warum ist Plasmodium falciparum so virulent?",
                "Previous cited paper titles: Severe malaria is associated with EPCR binding",
                "Current user question: Und was heißt das für EPCR?",
            ]
        ),
        task="answer",
    )

    by_label = {query.label: query.query for query in queries}

    assert by_label["current"] == "Und was heißt das für EPCR?"
    assert "Severe malaria is associated with EPCR binding" in by_label["followup_sources"]


def test_epcr_pfemp1_question_gets_specific_domain_expansion() -> None:
    queries = build_retrieval_queries(
        "What evidence links PfEMP1 CIDR domains to EPCR binding in severe malaria?",
        task="answer",
        max_queries=8,
    )

    by_label = {query.label: query.query for query in queries}
    joined = " ".join(by_label.values()).lower()

    assert "pfemp1_epcr_severe" in by_label
    assert "cidralpha1" in joined
    assert "dc8" in joined
    assert "dc13" in joined


def test_cerebral_malaria_question_gets_endothelial_mechanism_query() -> None:
    queries = build_retrieval_queries(
        "What evidence links PfEMP1 to cerebral malaria?",
        task="answer",
        max_queries=3,
    )

    by_label = {query.label: query.query for query in queries}

    assert "cerebral_malaria_endothelium" in by_label
    assert "brain endothelial" in by_label["cerebral_malaria_endothelium"]


def test_specific_dual_receptor_queries_precede_generic_why_context() -> None:
    queries = build_retrieval_queries(
        "Why might PfEMP1 variants with dual EPCR and ICAM1 binding matter in cerebral malaria?",
        task="answer",
        max_queries=3,
    )

    labels = [query.label for query in queries]

    assert labels == ["main", "dual_receptor_brain_endothelium", "pfemp1_epcr_severe"]
    assert "host_parasite_context" not in labels
    assert "DBLbeta" in queries[1].query
    assert "brain endothelial" in queries[1].query


def test_mosquito_passage_gets_epigenetic_reset_query_before_generic_var_context() -> None:
    queries = build_retrieval_queries(
        "What does the var-expression shift after mosquito passage imply about epigenetic virulence programs?",
        task="answer",
        max_queries=3,
    )

    assert queries[1].label == "mosquito_passage_var_reset"
    assert "epigenetic reset" in queries[1].query
    assert "controlled human malaria infection" in queries[1].query


def test_host_immunity_gets_specific_var_selection_query() -> None:
    queries = build_retrieval_queries(
        "How does naturally acquired host immunity modify var gene expression?",
        task="answer",
        max_queries=3,
    )

    assert queries[1].label == "host_immunity_var_selection"
    assert "semi-immune" in queries[1].query


def test_gap_question_gets_paired_context_expansion() -> None:
    queries = build_retrieval_queries(
        "Find gaps linking var gene expression and host immune states in severe malaria.",
        task="gaps",
        max_queries=8,
    )

    by_label = {query.label: query.query for query in queries}

    assert "var_expression_phenotype" in by_label
    assert "paired_gap_context" in by_label
    assert "host immune state" in by_label["paired_gap_context"]


def test_reanalysis_question_gets_repository_availability_query() -> None:
    queries = build_retrieval_queries(
        "Which studies provide accessible var expression data and metadata for reanalysis?",
        task="answer",
        max_queries=4,
    )

    by_label = {query.label: query.query for query in queries}

    assert "data_accessibility" in by_label
    assert "Gene Expression Omnibus" in by_label["data_accessibility"]
    assert "data availability" in by_label["data_accessibility"]


def test_data_accessibility_survives_specific_domain_query_budget() -> None:
    queries = build_retrieval_queries(
        "Which cerebral-malaria var expression studies provide accessible RNA-seq data "
        "for reanalysis?",
        task="answer",
        max_queries=4,
    )

    labels = [query.label for query in queries]

    assert labels[0] == "main"
    assert labels[1] == "data_accessibility"


def test_misspelled_broad_transcript_clinical_gap_question_gets_robust_expansion() -> None:
    queries = build_retrieval_queries(
        "find gaps in the study of associatoins with parasite trancripts/expression/genes "
        "and clinical malrai syndormes. what is unknown?",
        task="gaps",
        max_queries=4,
    )

    by_label = {query.label: query.query for query in queries}

    assert "parasite_transcript_clinical_phenotypes" in by_label
    assert "syndrome_specific_expression_limits" in by_label
    assert "transcript_measurement_comparability" in by_label
    assert len(queries) == 4


def test_molecular_target_question_gets_receptor_target_expansion() -> None:
    queries = build_retrieval_queries(
        "What molecular targets or receptor interactions appear promising for malaria intervention?",
        task="answer",
        max_queries=8,
    )

    by_label = {query.label: query.query for query in queries}
    joined = " ".join(by_label.values()).lower()

    assert "molecular_targets" in by_label
    assert "pfemp1" in joined
    assert "epcr" in joined
    assert "cd36" in joined
    assert "var2csa" in joined
    assert "pfrh5" in joined


def test_artemisinin_expansion_does_not_inject_diagnostic_escape_genes() -> None:
    question = (
        "Find primary studies comparing the genomic backgrounds and evolutionary "
        "routes to Plasmodium falciparum artemisinin resistance in Southeast Asia "
        "and East Africa."
    )

    queries = build_retrieval_queries(question, task="sources", max_queries=6)
    by_label = {query.label: query.query for query in queries}
    joined = " ".join(by_label.values()).lower()

    assert "artemisinin_resistance" in by_label
    assert "kelch13" in by_label["artemisinin_resistance"].lower()
    assert "pfhrp2" not in joined
    assert "pfhrp3" not in joined
    assert "diagnostic_escape" not in by_label


def test_bare_human_k13_symbol_does_not_inject_malaria_resistance() -> None:
    queries = build_retrieval_queries(
        "Find primary studies on human K13 keratin expression.",
        task="sources",
        max_queries=6,
    )

    assert "artemisinin_resistance" not in {query.label for query in queries}
    assert "artemisinin" not in " ".join(query.query for query in queries).lower()


def test_bare_human_dhfr_symbol_does_not_inject_malaria_antifolates() -> None:
    queries = build_retrieval_queries(
        "Find primary studies on human DHFR expression in cancer.",
        task="sources",
        max_queries=6,
    )

    assert "antifolate_resistance" not in {query.label for query in queries}
    assert "plasmodium" not in " ".join(query.query for query in queries).lower()


def test_pfhrp_diagnostic_escape_has_a_separate_expansion() -> None:
    queries = build_retrieval_queries(
        "Which pfhrp2 and pfhrp3 deletions cause malaria diagnostic escape?",
        task="sources",
        max_queries=6,
    )
    by_label = {query.label: query.query for query in queries}

    assert "diagnostic_escape" in by_label
    assert "pfhrp2" in by_label["diagnostic_escape"].lower()
    assert "artemisinin_resistance" not in by_label


def test_chloroquine_resistance_uses_pfcrt_not_artemisinin_specific_expansion() -> None:
    queries = build_retrieval_queries(
        "Find papers on chloroquine resistance in Plasmodium falciparum.",
        task="sources",
        max_queries=6,
    )
    by_label = {query.label: query.query for query in queries}

    assert "chloroquine_resistance" in by_label
    assert "pfcrt" in by_label["chloroquine_resistance"].lower()
    assert "kelch13" not in by_label["chloroquine_resistance"].lower()
    assert "diagnostic_escape" not in by_label


def test_molecular_resistance_and_diagnostic_escape_get_separate_views() -> None:
    queries = build_retrieval_queries(
        "Find primary papers on molecular resistance and diagnostic escape in "
        "Plasmodium falciparum.",
        task="sources",
        max_queries=6,
    )
    by_label = {query.label: query.query for query in queries}

    assert "antimalarial_drug_resistance" in by_label
    assert "diagnostic_escape" in by_label
    assert "pfcrt" in by_label["antimalarial_drug_resistance"].lower()
    assert "pfhrp2" in by_label["diagnostic_escape"].lower()


def test_named_report_comparison_reserves_query_budget_for_each_report() -> None:
    question = (
        "Compare Lawton et al., 'Diamonds in the rif,' with the Billows et al. "
        "PfPan preprint. For each study, report the genome input, main method, "
        "biological target, key numerical result, and publication status."
    )

    references = explicit_named_study_references(question)
    queries = build_retrieval_queries(question, task="answer", max_queries=3)

    assert [(item.surname, item.year) for item in references] == [
        ("Lawton", None),
        ("Billows", None),
    ]
    assert [query.label for query in queries] == [
        "main",
        "named_study_1",
        "named_study_2",
    ]
    assert "lawton" in queries[1].query.lower()
    assert "diamonds" in queries[1].query.lower()
    assert "billows" in queries[2].query.lower()
    assert "pfpan" in queries[2].query.lower()
    assert "preprint" in queries[2].query.lower()
    assert all(query.intent == "named_source" for query in queries[1:])


def test_author_year_comparison_displaces_generic_disease_fanout() -> None:
    question = (
        "Compare Band et al. (2022) with Hamilton et al. (2026) on host "
        "HbS-parasite-genotype association and disease phenotypes."
    )

    queries = build_retrieval_queries(question, task="answer", max_queries=3)

    assert [query.label for query in queries] == [
        "main",
        "named_study_1",
        "named_study_2",
    ]
    assert "band" in queries[1].query.lower()
    assert "2022" in queries[1].query
    assert "hamilton" in queries[2].query.lower()
    assert "2026" in queries[2].query
    assert "disease_signature_context" not in {query.label for query in queries}


def test_clinical_cohort_does_not_imply_dataset_discovery() -> None:
    cohort_queries = build_retrieval_queries(
        "Compare expression in a clinical cohort and report the disease phenotype.",
        task="answer",
        max_queries=8,
    )
    dataset_queries = build_retrieval_queries(
        "Find the downloadable dataset, repository, and accession for this cohort.",
        task="answer",
        max_queries=8,
    )

    assert "dataset_discovery" not in {query.label for query in cohort_queries}
    assert "dataset_discovery" in {query.label for query in dataset_queries}


def test_paper_search_routing_separates_lookup_from_discovery() -> None:
    assert classify_paper_search_mode("Find DOI 10.7554/eLife.110810.1") == "targeted_lookup"
    assert (
        classify_paper_search_mode("Which paper did Ndugwa et al. 2026 publish?")
        == "targeted_lookup"
    )
    assert (
        classify_paper_search_mode(
            "Find primary studies linking var gene expression to malaria syndromes."
        )
        == "topical_discovery"
    )
    assert (
        classify_paper_search_mode(
            "Which paper first demonstrated RIFIN binding to LILRB1?"
        )
        == "topical_discovery"
    )
    for topical_question in (
        "Find primary studies on HLA_DRB1 expression in malaria.",
        "Find papers about PF3D7_0425800 expression in cerebral malaria.",
        "Find Kenya 2025 malaria studies.",
        "List Africa 2024 malaria papers.",
        "Find studies from Ghana 2023 on RIFIN expression.",
    ):
        assert classify_paper_search_mode(topical_question) == "topical_discovery"
    assert (
        classify_paper_search_mode(
            'Find studies about the concept "dark energy equation of state".'
        )
        == "topical_discovery"
    )
    assert (
        classify_paper_search_mode(
            'Find the paper titled "A sufficiently long identifiable paper title".'
        )
        == "targeted_lookup"
    )


@pytest.mark.parametrize(
    "question",
    [
        "Find primary papers about RIFIN and report the DOI for each paper.",
        "List recent malaria studies with PMID and DOI.",
        "Which papers report PfEMP1 expression in cerebral malaria?",
        "List malaria studies published in 2025.",
    ],
)
def test_paper_search_metadata_output_fields_do_not_imply_targeted_lookup(
    question: str,
) -> None:
    assert classify_paper_search_mode(question) == "topical_discovery"


def test_source_lookup_removes_paper_as_a_word_not_a_substring() -> None:
    query = source_lookup_query("Find papers about malaria transcriptomics")

    assert query == "find about malaria transcriptomics"
    assert " s " not in f" {query} "
