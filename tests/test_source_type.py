from originrag.provenance.source_type import classify_source_document


def test_systematic_review_is_secondary() -> None:
    result = classify_source_document(
        {"title": "Markers of severe malaria: a systematic review and meta-analysis"}
    )

    assert result["source_type"] == "systematic_review_meta_analysis"
    assert result["direct_reporting"] == "no"
    assert result["confidence"] == "high"


def test_review_protocol_is_not_misclassified_as_meta_analysis_results() -> None:
    result = classify_source_document(
        {"title": "A protocol for a systematic review and individual patient data meta-analysis"}
    )

    assert result["source_type"] == "protocol"


def test_explicit_secondary_analysis_is_reanalysis() -> None:
    result = classify_source_document(
        {"title": "An observational secondary analysis of trial data"}
    )

    assert result["source_type"] == "reanalysis_secondary_data"
    assert result["direct_reporting"] == "no"


def test_explicit_secondary_design_in_abstract_overrides_analysis_prose() -> None:
    result = classify_source_document(
        {"title": "Clinical malaria outcomes"},
        (
            "This study is a secondary analysis of existing trial data. "
            "Methods. We analyzed patients and samples. Results."
        ),
    )

    assert result["source_type"] == "reanalysis_secondary_data"
    assert result["confidence"] == "high"


def test_explicit_this_was_secondary_design_overrides_primary_like_prose() -> None:
    result = classify_source_document(
        {"title": "Clinical malaria outcomes"},
        (
            "This was a secondary analysis of existing trial data. "
            "Methods. We analyzed patients and samples. Results."
        ),
    )

    assert result["source_type"] == "reanalysis_secondary_data"


def test_explicit_design_label_and_plural_secondary_analyses_are_detected() -> None:
    design = classify_source_document(
        {"title": "Clinical malaria outcomes"},
        "Study design: secondary analysis. Methods. Results.",
    )
    plural = classify_source_document(
        {"title": "Clinical malaria outcomes"},
        "We performed secondary analyses of trial data. Methods. Results.",
    )

    assert design["source_type"] == "reanalysis_secondary_data"
    assert plural["source_type"] == "reanalysis_secondary_data"


def test_published_profiles_reused_for_new_analysis_are_reanalysis() -> None:
    result = classify_source_document(
        {"title": "Var expression homogeneity"},
        "Published var gene expression profiles from 217 isolates were used for this study.",
    )

    assert result["source_type"] == "reanalysis_secondary_data"


def test_publicly_available_transcriptome_datasets_are_secondary_data() -> None:
    result = classify_source_document(
        {"title": "Comparative malaria transcriptome analysis"},
        (
            "We analyzed publicly available transcriptome datasets containing "
            "asymptomatic and uncomplicated malaria samples. Methods. Results."
        ),
    )

    assert result["source_type"] == "reanalysis_secondary_data"


def test_new_computational_method_using_published_data_is_primary_research() -> None:
    result = classify_source_document(
        {"title": "BLASE: bulk linkage analysis"},
        (
            "Methods. We present a new computational method called BLASE. "
            "Results. We tested the method using published transcriptome datasets."
        ),
    )

    assert result["source_type"] == "primary_research"
    assert result["confidence"] == "high"


def test_multiple_original_data_signals_identify_primary_research() -> None:
    result = classify_source_document(
        {"title": "Parasite binding in children"},
        "We recruited 120 children and collected blood samples. Materials and methods. Results.",
    )

    assert result["source_type"] == "primary_research"
    assert result["confidence"] == "high"
    assert result["direct_reporting"] == "likely"


def test_explicit_review_self_description_overrides_primary_like_vocabulary() -> None:
    result = classify_source_document(
        {"title": "Parasite expression and clinical malaria"},
        (
            "This review summarizes cohort studies of patients with severe "
            "malaria and parasite expression. We analyzed expression results "
            "reported across the literature. Methods. Results."
        ),
    )

    assert result["source_type"] == "narrative_review"
    assert result["confidence"] == "high"
    assert result["direct_reporting"] == "no"


def test_attributed_review_description_does_not_override_primary_design() -> None:
    result = classify_source_document(
        {"title": "Parasite expression in a prospective malaria cohort"},
        (
            "We enrolled 200 children and collected blood samples. We performed "
            "RNA sequencing and flow-cytometry experiments. Smith et al. published "
            "a review [12]. This review summarizes earlier cohort studies of severe "
            "malaria. Methods. Results."
        ),
    )

    assert result["source_type"] == "primary_research"
    assert result["confidence"] == "high"
    assert result["direct_reporting"] == "likely"


def test_original_assay_analysis_plus_results_identifies_primary_research() -> None:
    result = classify_source_document(
        {"title": "Expression is associated with severe disease"},
        "We analyzed parasites from 712 children. We used RT-qPCR. Results. Methods.",
    )

    assert result["source_type"] == "primary_research"
    assert result["confidence"] == "high"


def test_internal_reanalysis_does_not_override_new_clinical_measurements() -> None:
    result = classify_source_document(
        {"title": "Differential PfEMP1 expression in cerebral malaria pathology"},
        (
            "We collected post-mortem tissue from twenty paediatric cases. "
            "We used qRT-PCR to measure var expression and cloned and sequenced "
            "transcript tags. Results. Materials and methods. We re-analysed the "
            "measurements while accounting for HIV status."
        ),
    )

    assert result["source_type"] == "primary_research"
    assert result["confidence"] == "high"


def test_previously_described_expression_profiles_remain_secondary_evidence() -> None:
    result = classify_source_document(
        {"title": "Expression subtypes and severe malaria syndromes"},
        (
            "The expression profiles analyzed in this study have been previously "
            "described. We fitted new regression models to those existing profiles."
        ),
    )

    assert result["source_type"] == "reanalysis_secondary_data"


def test_new_assays_on_previously_described_samples_are_primary_research() -> None:
    result = classify_source_document(
        {"title": "Expression subtypes and severe malaria syndromes"},
        (
            "The patient samples analyzed in this study have been previously "
            "described. We used qRT-PCR to measure var expression and sequencing "
            "to generate new transcript tags. Materials and methods. Results."
        ),
    )

    assert result["source_type"] == "primary_research"
    assert result["confidence"] == "high"


def test_generic_journal_article_and_we_show_abstains() -> None:
    result = classify_source_document(
        {"title": "Malaria biology", "paper_type": "journal article"},
        "In this article we show why this literature matters.",
    )

    assert result["source_type"] == "uncertain"
    assert result["direct_reporting"] == "uncertain"


def test_mechanistic_here_we_identify_abstract_is_primary_research() -> None:
    result = classify_source_document(
        {"title": "Severe malaria is associated with parasite binding to EPCR"},
        "Here, we identify endothelial protein C receptor as the receptor for DC8 PfEMP1. "
        "We show that EPCR binding is mediated through CIDRalpha1.",
    )

    assert result["source_type"] == "primary_research"
    assert result["direct_reporting"] == "possible"


def test_here_we_combined_original_measurements_is_primary_research() -> None:
    result = classify_source_document(
        {"title": "Parasite transcription and brain swelling"},
        "Here, we combined neuroimaging, parasite transcript profiling, and blood profiles.",
    )

    assert result["source_type"] == "primary_research"


def test_here_we_used_rna_seq_is_primary_research() -> None:
    result = classify_source_document(
        {"title": "The parasite transcriptome in severe malaria"},
        "Here, we used RNA sequencing to analyse expression in 44 parasite isolates.",
    )

    assert result["source_type"] == "primary_research"


def test_brief_communication_with_own_patients_and_scrna_seq_is_primary() -> None:
    result = classify_source_document(
        {
            "title": "Single-cell landscape of bronchoalveolar immune cells in patients with COVID-19",
            "paper_type": "journal article",
        },
        (
            "Brief Communication. Here, we performed single-cell RNA sequencing on BALF "
            "cells from three patients with moderate COVID-19 and six patients with "
            "severe or critical infection. All 13 patients with COVID-19 in this study "
            "were enrolled from the hospital. All participants provided written informed "
            "consent for sample collection and subsequent analyses."
        ),
    )

    assert result["source_type"] == "primary_research"
    assert result["confidence"] == "high"


def test_quoted_enrollment_from_another_cohort_is_not_primary_signal() -> None:
    result = classify_source_document(
        {
            "title": "Macrophage states and clinical disease",
            "paper_type": "journal article",
        },
        (
            "Smith and colleagues reported that patients in the original cohort "
            "were enrolled from three hospitals. We discuss the implications of "
            "their findings for future studies."
        ),
    )

    assert result["source_type"] == "uncertain"
    assert result["confidence"] == "low"
