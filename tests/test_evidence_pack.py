from __future__ import annotations

from originrag.analysis.evidence_pack import (
    apply_certification_core_roles,
    build_evidence_pack,
    format_evidence_pack_markdown,
    freeze_prompt_visible_result,
    select_evidence_results,
)


def test_evidence_pack_creates_stable_citation_labels() -> None:
    pack = build_evidence_pack(
        "What evidence links PfEMP1 to severe malaria?",
        [
            {
                "paper_id": "MAL_VAR_0001",
                "author": "Jespersen",
                "year": "2016",
                "title": "A var paper",
                "journal": "Cell Host Microbe",
                "page_start": 3,
                "page_end": 4,
                "chunk_id": "MAL_VAR_0001_CHUNK_0002",
                "section": "results",
                "text": "CIDRalpha1 and EPCR binding evidence.",
            }
        ],
    )

    assert pack["chunks"][0]["citation_label"] == "[Jespersen 2016, Results, pages 3-4]"
    assert pack["n_unique_papers"] == 1


def test_evidence_pack_truncates_long_text() -> None:
    pack = build_evidence_pack(
        "PfEMP1 evidence",
        [{"paper_id": "P1", "chunk_id": "C1", "text": "PfEMP1 evidence. " + "A" * 5000}],
        max_chars_per_chunk=100,
    )

    assert len(pack["chunks"][0]["text"]) <= 120
    assert "PfEMP1 evidence" in pack["chunks"][0]["text"]


def test_frozen_prompt_visible_text_is_identical_to_certified_input() -> None:
    result = {
        "paper_id": "P1",
        "chunk_id": "C1",
        "text": "irrelevant " * 300 + "PfEMP1 binds EPCR in severe malaria.",
    }

    frozen = freeze_prompt_visible_result("PfEMP1 EPCR", result, 140)
    pack = build_evidence_pack(
        "PfEMP1 EPCR",
        [frozen],
        max_chars_per_chunk=140,
    )

    assert pack["chunks"][0]["text"] == frozen["text"]
    assert len(frozen["text"]) <= 140


def test_evidence_pack_preserves_resolved_named_study_identity() -> None:
    pack = build_evidence_pack(
        "Compare Smith et al. (2024) with Jones et al. (2025).",
        [
            {
                "paper_id": "P1",
                "chunk_id": "C1",
                "text": "Here we report the requested direct comparison.",
                "explicit_named_study_anchor": True,
                "named_study_surname": "Smith",
                "named_study_year": "2024",
                "named_study_source_text_scope": "front_summary_excerpt",
            }
        ],
    )

    chunk = pack["chunks"][0]
    assert chunk["explicit_named_study_anchor"] is True
    assert chunk["explicit_named_study_support"] is False
    assert chunk["named_study_surname"] == "Smith"
    assert chunk["named_study_year"] == "2024"
    assert chunk["named_study_source_text_scope"] == "front_summary_excerpt"


def test_named_study_front_summary_is_not_rewindowed_away_from_opening_facts() -> None:
    opening = "Here we constructed PfPan from 13 genomes using Minigraph-Cactus."
    closing = "We then applied PfPan to 878 genomes and identified structural variants."
    pack = build_evidence_pack(
        "Compare the Billows PfPan preprint with another report and keep genome counts.",
        [
            {
                "paper_id": "BILLOWS",
                "chunk_id": "BILLOWS_C1",
                "text": opening + " Background sentence. " * 30 + closing,
                "explicit_named_study_anchor": True,
                "named_study_surname": "Billows",
                "named_study_year": "2026",
                "named_study_source_text_scope": "front_summary_excerpt",
            }
        ],
        max_chars_per_chunk=180,
    )

    assert pack["chunks"][0]["text"].startswith(opening)
    assert closing in pack["chunks"][0]["text"]


def test_named_study_support_keeps_model_input_and_late_validation_result() -> None:
    opening = "A GLMM used three RT-qPCR Ct values as model input."
    closing = "Mixed-stage samples produced an intermediate parasite-age estimate."
    pack = build_evidence_pack(
        "What was the model input and mixed-stage limitation?",
        [
            {
                "paper_id": "CIUFFREDA",
                "chunk_id": "CIUFFREDA_C5",
                "text": opening + " Model details. " * 130 + closing,
                "explicit_named_study_support": True,
            }
        ],
        max_chars_per_chunk=950,
    )

    assert pack["chunks"][0]["text"].startswith(opening)
    assert closing in pack["chunks"][0]["text"]


def test_data_accessibility_window_keeps_repository_identifier() -> None:
    result = {
        "paper_id": "P1",
        "chunk_id": "C1",
        "text": (
            "Clinical transcript methods and var expression measurements. " * 80
            + "Data availability: RNA-seq data were deposited in GEO under GSE186820."
        ),
        "evidence_window_intent": "data_accessibility",
    }

    frozen = freeze_prompt_visible_result(
        "Which var-expression data are accessible for reanalysis?",
        result,
        220,
    )

    assert "GSE186820" in frozen["text"]


def test_vague_request_words_do_not_displace_gene_from_visible_window() -> None:
    result = {
        "paper_id": "P1",
        "chunk_id": "C1",
        "text": (
            "General information about signatures and annotations. " * 80
            + "SPP1, GPNMB, and TREM2 define the reported macrophage signature."
        ),
        "required_entity_anchors": [
            ["spp1", "osteopontin", "secreted phosphoprotein 1"]
        ],
        "required_entity_anchors_verified": True,
    }

    frozen = freeze_prompt_visible_result(
        "Do you find any information about SPP1 gene signatures in whatever?",
        result,
        220,
    )

    assert "SPP1" in frozen["text"]


def test_visible_window_keeps_later_entity_severity_statement() -> None:
    result = {
        "paper_id": "LIAO",
        "chunk_id": "LIAO_C2",
        "text": (
            "Background cell annotations. " * 18
            + "Patients with severe or critical COVID-19 had altered macrophage proportions. "
            + "Macrophage heterogeneity and classification criteria were evaluated. " * 5
            + "The groups included FCN1-high, FCN1-low SPP1-positive, SPP1-positive, "
            "and FABP4-positive macrophages. Their composition and SPP1 expression differed. "
            "SPP1 was highly expressed in patients with severe or critical infection. "
            + "Downstream pathway analysis followed. " * 20
        ),
        "required_entity_anchors": [
            ["spp1", "osteopontin"],
            ["covid 19", "coronavirus"],
            ["severity", "severe", "critical"],
        ],
        "required_entity_anchors_verified": True,
    }

    frozen = freeze_prompt_visible_result(
        "How are SPP1 macrophages associated with COVID-19 severity?",
        result,
        950,
    )

    assert "SPP1 was highly expressed in patients with severe or critical infection" in frozen["text"]


def test_context_only_anchor_filter_preserves_full_question_for_windowing() -> None:
    result = {
        "paper_id": "P1",
        "chunk_id": "C1",
        "text": (
            "General malaria and Plasmodium background. " * 35
            + "Here, we found lower var gene expression in asymptomatic dry-season "
            "infections than in clinical malaria cases."
        ),
        "required_entity_anchors": [["malaria", "plasmodium"]],
        "required_entity_anchors_verified": True,
        "anchor_only_evidence_window": False,
    }

    frozen = freeze_prompt_visible_result(
        (
            "What changes in var gene expression distinguish asymptomatic dry-season "
            "Plasmodium falciparum infections from clinical malaria?"
        ),
        result,
        320,
    )

    assert "lower var gene expression" in frozen["text"]


def test_evidence_pack_markdown_contains_section_metadata() -> None:
    pack = build_evidence_pack(
        "Question",
        [{"paper_id": "P1", "chunk_id": "C1", "section": "abstract", "text": "Evidence."}],
    )
    markdown = format_evidence_pack_markdown(pack)

    assert "# Evidence Pack" in markdown
    assert "Section: abstract" in markdown


def test_evidence_pack_markdown_exposes_robust_lineage_bounds() -> None:
    pack = {
        "question": "Question",
        "n_chunks": 0,
        "n_unique_papers": 0,
        "chunks": [],
        "trace_robust_lineage": {
            "status": "lineage_review_required",
            "effective_evidence": {"lower": 1.0, "upper": 2.5},
            "lineage_count_lower": 1,
            "lineage_count_upper": 3,
            "pessimistic_flow": {"unresolved_facets": ["replication"]},
            "missing_pair_count": 2,
            "uncertain_pair_count": 3,
            "exact_for_declared_lineage_uncertainty": True,
        },
        "trace_certification_core": {
            "certification_paper_count": 2,
            "context_paper_count": 4,
            "paper_ids": ["P1", "P2"],
        },
    }

    markdown = format_evidence_pack_markdown(pack)

    assert "## Robust Lineage Certificate" in markdown
    assert "Status: lineage_review_required" in markdown
    assert "Effective evidence interval: [1.0, 2.5]" in markdown
    assert "Pessimistic unresolved facets: replication" in markdown
    assert "Certification core / synthesis context reports: 2 / 4" in markdown
    assert "Certification-core paper IDs: P1, P2" in markdown


def test_certification_core_roles_are_visible_in_evidence_pack() -> None:
    pack = {
        "trace_certification_core": {"paper_ids": ["P1"]},
        "chunks": [
            {"evidence_id": "E001", "paper_id": "P1"},
            {"evidence_id": "E002", "paper_id": "P2"},
        ],
    }

    apply_certification_core_roles(pack)

    assert [chunk["certification_role"] for chunk in pack["chunks"]] == [
        "certifying",
        "context_only",
    ]


def test_current_certifying_evidence_metadata_precedes_compatibility_alias() -> None:
    pack = {
        "trace_certifying_evidence": {
            "strategy": "full_selected_set_v2",
            "paper_ids": ["P1", "P2"],
        },
        "trace_certification_core": {"paper_ids": ["P1"]},
        "chunks": [
            {"evidence_id": "E001", "paper_id": "P1"},
            {"evidence_id": "E002", "paper_id": "P2"},
        ],
    }

    apply_certification_core_roles(pack)

    assert [chunk["certification_role"] for chunk in pack["chunks"]] == [
        "certifying",
        "certifying",
    ]


def test_same_report_prompt_context_does_not_inherit_certifying_role() -> None:
    pack = build_evidence_pack(
        "What did the named report measure?",
        [
            {
                "paper_id": "P1",
                "chunk_id": "P1_ANCHOR",
                "text": "Here we report the primary result.",
            },
            {
                "paper_id": "P1",
                "chunk_id": "P1_SUPPORT",
                "text": "Additional method detail from the same report.",
                "prompt_context_only": True,
                "evidence_selection_reason": "named_study_same_report_context",
            },
        ],
    )
    pack["trace_certifying_evidence"] = {
        "strategy": "full_selected_set_v2",
        "paper_ids": ["P1"],
    }

    apply_certification_core_roles(pack)

    assert [chunk["certification_role"] for chunk in pack["chunks"]] == [
        "certifying",
        "context_only",
    ]
    assert pack["chunks"][1]["selection_reason"] == (
        "named_study_same_report_context"
    )


def test_evidence_pack_markdown_names_the_current_full_selected_strategy() -> None:
    pack = {
        "question": "Question",
        "n_chunks": 0,
        "n_unique_papers": 0,
        "chunks": [],
        "trace_robust_lineage": {
            "status": "insufficient_even_optimistically",
            "effective_evidence": {"lower": 1.0, "upper": 1.5},
            "lineage_count_lower": 1,
            "lineage_count_upper": 2,
            "pessimistic_flow": {"unresolved_facets": []},
        },
        "trace_certifying_evidence": {
            "strategy": "full_selected_set_v2",
            "certifying_paper_count": 2,
            "context_paper_count": 2,
            "paper_ids": ["P1", "P2"],
        },
    }

    markdown = format_evidence_pack_markdown(pack)

    assert "Certifying evidence / selected context reports: 2 / 2" in markdown
    assert "Certifying-evidence paper IDs: P1, P2" in markdown
    assert "Certification core" not in markdown


def test_evidence_pack_cleans_common_pdf_artifacts() -> None:
    pack = build_evidence_pack(
        "sex differences malaria",
        [
            {
                "paper_id": "P1",
                "chunk_id": "C1",
                "section": "results",
                "text": "[...] Malarianaive participants had IFN-\x03 signals in an ohort study.",
            }
        ],
    )
    markdown = format_evidence_pack_markdown(pack)

    assert "Malaria-naive" in pack["chunks"][0]["text"]
    assert "IFN-gamma" in pack["chunks"][0]["text"]
    assert "a cohort study" in pack["chunks"][0]["text"]
    assert "Malarianaive" not in markdown
    assert "IFN-\x03" not in markdown
    assert "an ohort study" not in markdown


def test_evidence_pack_uses_query_centered_window() -> None:
    text = "Background sentence. " + ("irrelevant text " * 300) + "PfEMP1 binds EPCR in severe malaria."
    pack = build_evidence_pack(
        "PfEMP1 EPCR severe malaria",
        [{"paper_id": "P1", "chunk_id": "C1", "text": text}],
        max_chars_per_chunk=180,
    )

    assert "PfEMP1 binds EPCR" in pack["chunks"][0]["text"]
    assert "irrelevant text irrelevant text irrelevant text irrelevant text irrelevant text" not in pack["chunks"][0]["text"]


def test_evidence_selection_prefers_clean_evidence_sections() -> None:
    results = [
        {
            "rank": 1,
            "score": 30.0,
            "section": "table",
            "quality_flags": ["table_like"],
            "chunk_id": "T1",
        },
        {
            "rank": 2,
            "score": 29.0,
            "section": "results",
            "quality_flags": [],
            "chunk_id": "R1",
        },
    ]

    selected = select_evidence_results(results, max_chunks=1)

    assert selected[0]["chunk_id"] == "R1"


def test_evidence_selection_preserves_rrf_over_raw_keyword_score() -> None:
    results = [
        {
            "rank": 1,
            "score": 40.0,
            "rrf_score": 0.05,
            "section": "results",
            "quality_flags": [],
            "chunk_id": "SPECIALIZED",
        },
        {
            "rank": 2,
            "score": 120.0,
            "rrf_score": 0.02,
            "section": "results",
            "quality_flags": [],
            "chunk_id": "GENERIC",
        },
    ]

    selected = select_evidence_results(results, max_chunks=1)

    assert selected[0]["chunk_id"] == "SPECIALIZED"


def test_evidence_selection_preserves_paper_level_rrf() -> None:
    results = [
        {
            "rank": 1,
            "score": 40.0,
            "rrf_score": 0.02,
            "paper_rrf_score": 0.05,
            "section": "results",
            "quality_flags": [],
            "chunk_id": "MULTI_QUERY_PAPER",
        },
        {
            "rank": 2,
            "score": 120.0,
            "rrf_score": 0.05,
            "paper_rrf_score": 0.02,
            "section": "results",
            "quality_flags": [],
            "chunk_id": "ONE_QUERY_PAPER",
        },
    ]

    selected = select_evidence_results(results, max_chunks=1)

    assert selected[0]["chunk_id"] == "MULTI_QUERY_PAPER"


def test_evidence_selection_reserves_most_slots_for_distinct_papers() -> None:
    results = []
    for index in range(10):
        results.append({
            "rank": index + 1,
            "score": 100.0 - index,
            "paper_relevance_score": 0.1 - index * 0.001,
            "paper_id": "DOMINANT" if index < 5 else f"P{index}",
            "section": "results",
            "quality_flags": [],
            "chunk_id": f"C{index}",
        })

    selected = select_evidence_results(results, max_chunks=6)

    assert len({row["paper_id"] for row in selected}) >= 5


def test_evidence_selection_reserves_a_specific_title_anchor() -> None:
    results = []
    for index in range(8):
        results.append({
            "rank": index + 1,
            "score": 100.0 - index,
            "paper_relevance_score": 0.08 - index * 0.001,
            "paper_id": f"P{index}",
            "section": "results",
            "quality_flags": [],
            "chunk_id": f"C{index}",
            "title": "General EPCR binding in cerebral malaria",
        })
    results.append({
        "rank": 9,
        "score": 70.0,
        "paper_relevance_score": 0.04,
        "paper_id": "DIRECT",
        "section": "results",
        "quality_flags": [],
        "chunk_id": "DIRECT_C1",
        "title": "Dual receptor binding by a PfEMP1 variant",
    })

    selected = select_evidence_results(
        results,
        max_chunks=6,
        question="Why are dual EPCR and ICAM1 receptor-binding PfEMP1 variants relevant?",
    )

    direct = next(row for row in selected if row["paper_id"] == "DIRECT")
    assert direct["evidence_selection_reason"].startswith("specific_title_anchor:")


def test_results_title_anchor_survives_soft_artifact_flags() -> None:
    results = [
        {
            "rank": 1,
            "score": 100.0,
            "paper_relevance_score": 0.08,
            "paper_id": "GENERAL",
            "section": "results",
            "quality_flags": [],
            "chunk_id": "GENERAL_C1",
            "title": "General cerebral malaria mechanisms",
        },
        {
            "rank": 9,
            "score": 70.0,
            "paper_relevance_score": 0.04,
            "paper_id": "DIRECT",
            "section": "results",
            "quality_flags": ["figure_like", "supplement_like"],
            "chunk_id": "DIRECT_C1",
            "title": "Dual receptor binding by PfEMP1",
        },
    ]

    selected = select_evidence_results(
        results,
        max_chunks=1,
        question="Why does dual receptor binding by PfEMP1 matter?",
    )

    assert selected[0]["paper_id"] == "DIRECT"


def test_evidence_pack_records_selection_reason() -> None:
    results = [
        {
            "rank": index + 1,
            "score": 100.0 - index,
            "paper_relevance_score": 0.08 - index * 0.001,
            "paper_id": f"P{index}",
            "section": "results",
            "quality_flags": [],
            "chunk_id": f"C{index}",
            "title": "Specific receptor mechanism" if index == 7 else "General malaria paper",
            "text": "Evidence text.",
        }
        for index in range(8)
    ]

    pack = build_evidence_pack("What is the specific receptor mechanism?", results, max_chunks=6)

    assert all(chunk["selection_reason"] for chunk in pack["chunks"])
    assert "Selection reason:" in format_evidence_pack_markdown(pack)
