from originrag.trace.blueprint import suggest_blueprint
from originrag.trace.facets import infer_direct_evidence_roles
from originrag.trace.flow import compute_evidence_flow, role_compatibility
from originrag.trace.robust_lineage import robust_evidence_flow
from originrag.trace.runtime import candidate_from_result


SPP1_COVID_QUESTION = (
    "What SPP1-positive macrophage states are reported, and how are they "
    "associated with COVID-19 severity?"
)
MEMORY_T_CELL_LIFE_COURSE_QUESTION = (
    "i want to know about how the memory t cell repertoire changes from early "
    "childhood to adolescence. Please provide literature."
)
EPCR_QUESTION = "What evidence links EPCR-binding PfEMP1 to severe and cerebral malaria?"


def test_epcr_direct_clinical_and_binding_records_survive_robust_role_projection() -> None:
    blueprint = suggest_blueprint("epcr_fixture", EPCR_QUESTION, "evidence_qa")
    candidates = [
        candidate_from_result(
            {
                "chunk_id": "SHABANI_TABLE",
                "paper_id": "SHABANI",
                "section": "discussion",
                "title": (
                    "EPCR-binding PfEMP1 expression increases with malaria disease "
                    "severity and is elevated in cerebral malaria"
                ),
                "text": "Table 4 reports Spearman correlations in children with severe malaria.",
                "score": 20.0,
            },
            blueprint,
        ),
        candidate_from_result(
            {
                "chunk_id": "TURNER_BINDING",
                "paper_id": "TURNER",
                "section": "introduction",
                "title": "Severe malaria is associated with parasite binding to EPCR",
                "text": (
                    "We tested whether parasites expressing native DC8 PfEMP1 bound "
                    "EPCR and found binding to endothelial cells, demonstrated by "
                    "recombinant EPCR inhibition."
                ),
                "score": 20.0,
            },
            blueprint,
        ),
    ]

    robust = robust_evidence_flow(candidates, blueprint, [], strict_missing_pairs=True)

    assert candidates[0].evidence_type_scores["observational"] == 1.0
    assert candidates[1].evidence_type_scores["biochemical"] == 1.0
    assert candidates[1].section_quality == 0.85
    assert robust.effective_evidence.substantive_paper_count == 2
    assert all(weight > 0.0 for weight in robust.effective_evidence.paper_weights.values())


def test_direct_role_inference_rejects_dict_typed_review_and_background_mentions() -> None:
    review_roles = infer_direct_evidence_roles(
        title="Review of EPCR binding and severe malaria",
        text="Binding assays show that EPCR-binding variants are associated with severe disease.",
        section="abstract",
        source_type={"source_type": "narrative_review", "label": "Narrative review"},
    )
    background_roles = infer_direct_evidence_roles(
        title="Host responses in malaria",
        text=(
            "Prior studies associated EPCR-binding variants with severe malaria. "
            "These domains bind EPCR with high affinity (Smith et al., 2013)."
        ),
        section="discussion",
        source_type={"source_type": "primary_research"},
    )

    assert review_roles == set()
    assert background_roles == set()


def test_secondary_source_title_lexicons_cannot_create_certifiable_evidence() -> None:
    blueprint = suggest_blueprint("epcr_review", EPCR_QUESTION, "evidence_qa")
    review = candidate_from_result(
        {
            "chunk_id": "REVIEW_ABSTRACT",
            "paper_id": "REVIEW",
            "section": "abstract",
            "title": (
                "Systematic review and meta-analysis of PfEMP1 var gene CIDRalpha1 "
                "EPCR binding assays, affinity, severe cerebral malaria patients, "
                "cohorts, and clinical outcomes"
            ),
            "text": (
                "The review summarizes biochemical binding assays and observational "
                "associations in patients and cohorts."
            ),
            "score": 20.0,
            "trace_polarity": "support",
            "source_type_assessment": {
                "source_type": "systematic_review_meta_analysis"
            },
        },
        blueprint,
    )

    robust = robust_evidence_flow([review], blueprint, [], strict_missing_pairs=True)

    assert not any(review.scale_scores.values())
    assert review.evidence_type_scores["observational"] == 0.0
    assert review.evidence_type_scores["biochemical"] == 0.0
    assert review.evidence_type_scores["review"] == 1.0
    assert robust.effective_evidence.substantive_paper_count == 0
    assert robust.effective_evidence.paper_weights == {"REVIEW": 0.0}


def test_binding_experiment_title_modifier_is_not_promoted_to_clinical_association() -> None:
    roles = infer_direct_evidence_roles(
        title="DC8 var genes associated with severe malaria bind avidly to endothelial cells",
        text="Results show that the domains bound brain endothelial cells.",
        section="results",
        source_type={"source_type": "primary_research"},
    )

    assert roles == {"functional_binding"}


def test_clinical_biology_blueprint_accepts_liao_like_direct_evidence() -> None:
    blueprint = suggest_blueprint(
        "spp1_covid",
        SPP1_COVID_QUESTION,
        "evidence_synthesis",
    )
    required = [facet for facet in blueprint.required_facets if facet.is_required]

    assert {facet.facet_id for facet in required} == {
        "spp1_covid_molecular_target",
        "spp1_covid_cellular_context",
        "spp1_covid_disease_covid",
        "spp1_covid_clinical_outcome",
    }
    assert {facet.biological_scale for facet in required} == {
        "molecular",
        "cellular",
        "clinical",
    }
    assert {facet.evidence_type for facet in required} == {
        "transcriptomic",
        "cellular",
        "observational",
    }
    assert "methodological" not in blueprint.preferred_scales

    candidate = candidate_from_result(
        {
            "chunk_id": "LIAO_RESULTS",
            "paper_id": "LIAO_2020",
            "section": "results",
            "title": (
                "Single-cell landscape of bronchoalveolar immune cells in patients with COVID-19"
            ),
            "text": (
                "Single-cell analysis identified immune cells and macrophages. "
                "Macrophages were grouped by FCN1, SPP1 and FABP4 expression "
                "patterns, including FCN1loSPP1+ group 2 and SPP1+ group 3. "
                "SPP1 was highly expressed by patients with severe or critical "
                "COVID-19 infection compared with moderate infection."
            ),
            "score": 20.0,
        },
        blueprint,
    )

    for facet in required:
        assert candidate.facet_scores[facet.facet_id] > 0.0
        assert (
            role_compatibility(
                candidate,
                facet.biological_scale,
                facet.evidence_type,
                facet.polarity_requirement,
            )
            > 0.0
        )
    flow = compute_evidence_flow([candidate], blueprint)
    assert flow.total_flow > 0.0
    assert flow.bottleneck_coverage > 0.0


def test_severe_covid_alone_cannot_satisfy_marker_cell_state_question() -> None:
    blueprint = suggest_blueprint(
        "spp1_covid",
        SPP1_COVID_QUESTION,
        "evidence_synthesis",
    )
    candidate = candidate_from_result(
        {
            "chunk_id": "GENERIC_SEVERE_COVID",
            "paper_id": "GENERIC_2020",
            "section": "results",
            "title": "Clinical outcomes in severe COVID-19",
            "text": (
                "Patients with severe or critical COVID-19 had longer hospital "
                "stays and higher mortality than patients with moderate disease."
            ),
            "score": 20.0,
        },
        blueprint,
    )

    assert candidate.facet_scores["spp1_covid_disease_covid"] > 0.0
    assert candidate.facet_scores["spp1_covid_clinical_outcome"] > 0.0
    assert candidate.facet_scores["spp1_covid_molecular_target"] == 0.0
    assert candidate.facet_scores["spp1_covid_cellular_context"] == 0.0

    flow = compute_evidence_flow([candidate], blueprint)
    assert flow.bottleneck_coverage == 0.0
    assert set(flow.unresolved_facets) >= {
        "spp1_covid_molecular_target",
        "spp1_covid_cellular_context",
    }


def test_runtime_preserves_explicit_facet_origin_contract() -> None:
    blueprint = suggest_blueprint(
        "spp1_states",
        "Which SPP1-positive macrophage states are reported?",
        "evidence_synthesis",
    )
    origin_ids = {
        "spp1_states_molecular_target": "STUDY_ONE",
        "spp1_states_cellular_context": "STUDY_ONE",
    }

    candidate = candidate_from_result(
        {
            "chunk_id": "SPP1_ORIGIN",
            "paper_id": "SPP1_PAPER",
            "section": "results",
            "text": "SPP1-positive macrophage cell states were measured.",
            "score": 10.0,
            "facet_origin_ids": origin_ids,
            "facet_origin_annotations_complete": True,
        },
        blueprint,
    )

    assert candidate.facet_origin_ids == origin_ids
    assert candidate.facet_origin_annotations_complete is True


def test_biological_fallback_is_cellular_not_methodological() -> None:
    blueprint = suggest_blueprint(
        "spp1_states",
        "Which SPP1-positive macrophage states are reported?",
        "evidence_synthesis",
    )
    required = [facet for facet in blueprint.required_facets if facet.is_required]

    assert [facet.facet_id for facet in required] == [
        "spp1_states_molecular_target",
        "spp1_states_cellular_context",
    ]
    assert required[0].biological_scale == "molecular"
    assert required[0].evidence_type == "transcriptomic"
    assert required[0].aliases == ["spp1"]
    assert required[1].biological_scale == "cellular"
    assert required[1].evidence_type == "cellular"
    assert {"macrophage", "macrophages", "cell state"} <= set(required[1].aliases)


def test_methodological_fallback_remains_methodological() -> None:
    blueprint = suggest_blueprint(
        "retrieval_benchmark",
        "Which benchmark pipeline has the best retrieval performance?",
        "evidence_synthesis",
    )
    required = [facet for facet in blueprint.required_facets if facet.is_required]

    assert len(required) == 1
    assert required[0].facet_id == "retrieval_benchmark_direct_answer_evidence"
    assert required[0].biological_scale == "methodological"
    assert required[0].evidence_type == "methodological"


def test_memory_t_cell_repertoire_age_question_has_three_required_facets() -> None:
    blueprint = suggest_blueprint(
        "memory_t_cell_age",
        MEMORY_T_CELL_LIFE_COURSE_QUESTION,
        "evidence_synthesis",
    )
    required = [facet for facet in blueprint.required_facets if facet.is_required]

    assert [facet.facet_id for facet in required] == [
        "memory_t_cell_age_memory_t_cell_identity",
        "memory_t_cell_age_tcr_repertoire",
        "memory_t_cell_age_developmental_age_span",
    ]
    assert {facet.biological_scale for facet in required} == {"cellular", "population"}
    assert "memory_t_cell_age_immune_state" not in {
        facet.facet_id for facet in blueprint.required_facets
    }
    aliases = {alias for facet in required for alias in facet.aliases}
    assert not any("monocyte" in alias for alias in aliases)
    assert not any("b cell" in alias or "bcr" in alias for alias in aliases)


def test_memory_t_cell_age_facets_reject_background_immune_topics() -> None:
    blueprint = suggest_blueprint(
        "memory_t_cell_age",
        MEMORY_T_CELL_LIFE_COURSE_QUESTION,
        "evidence_synthesis",
    )
    relevant = candidate_from_result(
        {
            "chunk_id": "DIRECT_TCR_AGE",
            "paper_id": "DIRECT_TCR_AGE_PAPER",
            "section": "results",
            "title": "Memory T-cell receptor repertoires across pediatric age groups",
            "text": (
                "Longitudinal TCR sequencing showed that the memory CD4 T-cell "
                "repertoire changed from early childhood to adolescence."
            ),
            "score": 20.0,
        },
        blueprint,
    )
    monocyte_background = candidate_from_result(
        {
            "chunk_id": "MONOCYTE_BACKGROUND",
            "paper_id": "MONOCYTE_BACKGROUND_PAPER",
            "section": "discussion",
            "title": "Innate immune responses in children with malaria",
            "text": (
                "Whole-blood transcriptomics found similar innate immune response "
                "pathways and monocyte states in individuals with malaria."
            ),
            "score": 20.0,
        },
        blueprint,
    )
    b_cell_background = candidate_from_result(
        {
            "chunk_id": "B_CELL_BACKGROUND",
            "paper_id": "B_CELL_BACKGROUND_PAPER",
            "section": "results",
            "title": "Memory B-cell receptor repertoires across childhood",
            "text": (
                "The memory B-cell receptor repertoire changed from early "
                "childhood to adolescence."
            ),
            "score": 20.0,
        },
        blueprint,
    )

    required_ids = [
        facet.facet_id for facet in blueprint.required_facets if facet.is_required
    ]
    assert all(relevant.facet_scores[facet_id] > 0.0 for facet_id in required_ids)
    assert all(
        monocyte_background.facet_scores[facet_id] == 0.0
        for facet_id in required_ids
    )
    assert b_cell_background.facet_scores["memory_t_cell_age_memory_t_cell_identity"] == 0.0
    assert b_cell_background.facet_scores["memory_t_cell_age_tcr_repertoire"] == 0.0


def test_generic_malaria_t_cell_question_keeps_generic_immune_state_facet() -> None:
    blueprint = suggest_blueprint(
        "malaria_t_cell",
        "How do T cells respond during malaria infection?",
        "evidence_synthesis",
    )
    required_ids = {
        facet.facet_id for facet in blueprint.required_facets if facet.is_required
    }

    assert "malaria_t_cell_immune_state" in required_ids
    assert not any(
        facet_id.endswith("_memory_t_cell_identity")
        or facet_id.endswith("_tcr_repertoire")
        or facet_id.endswith("_developmental_age_span")
        for facet_id in required_ids
    )
    immune_facet = next(
        facet
        for facet in blueprint.required_facets
        if facet.facet_id == "malaria_t_cell_immune_state"
    )
    assert "t cell" in immune_facet.aliases
    assert not any("monocyte" in alias for alias in immune_facet.aliases)

    monocyte_only = candidate_from_result(
        {
            "chunk_id": "MONOCYTE_ONLY",
            "paper_id": "MORANGA_LIKE",
            "section": "results",
            "title": "Monocyte states in children with malaria",
            "text": "Monocytes showed elevated interferon signaling during malaria.",
            "score": 20.0,
        },
        blueprint,
    )

    assert monocyte_only.facet_scores["malaria_t_cell_immune_state"] == 0.0


def test_host_cell_remodelling_does_not_create_t_cell_trace_facet() -> None:
    blueprint = suggest_blueprint(
        "rifin_or_stevor",
        (
            "Find primary studies on Plasmodium falciparum RIFIN or STEVOR "
            "proteins that address receptor binding, immune evasion, rosetting, "
            "host-cell remodelling, or therapeutic targeting."
        ),
        "evidence_synthesis",
    )

    assert "rifin_or_stevor_immune_state" not in {
        facet.facet_id for facet in blueprint.required_facets
    }
    assert not any(
        alias in {"t cell", "t cells"}
        for facet in blueprint.required_facets
        for alias in facet.aliases
    )
    assert "rifin_or_stevor_interventional_evidence" not in {
        facet.facet_id for facet in blueprint.required_facets
    }


def test_explicit_trial_outcome_request_keeps_interventional_facet() -> None:
    blueprint = suggest_blueprint(
        "therapy_trial",
        (
            "Does this therapeutic intervention improve clinical outcomes in "
            "a randomized trial?"
        ),
        "evidence_synthesis",
    )

    assert "therapy_trial_interventional_evidence" in {
        facet.facet_id
        for facet in blueprint.required_facets
        if facet.is_required
    }


def test_explicit_causal_question_keeps_interventional_facet() -> None:
    blueprint = suggest_blueprint(
        "causal_binding",
        "Does RIFIN receptor binding cause immune suppression?",
        "evidence_synthesis",
    )

    assert "causal_binding_interventional_evidence" in {
        facet.facet_id
        for facet in blueprint.required_facets
        if facet.is_required
    }
