from __future__ import annotations

from originrag.retrieval.paper_selection import (
    collapse_detected_publication_versions,
    requests_direct_clinical_parasite_expression,
    retain_direct_clinical_expression_papers,
)


CLINICAL_EXPRESSION_QUESTION = (
    "Find papers linking Plasmodium falciparum parasite expression to clinical malaria "
    "phenotypes. Focus on associations with var/PfEMP1 genes, RIFINs, STEVORs, and the core "
    "parasite transcriptome, especially for cerebral malaria, severe anaemia, severe vs "
    "uncomplicated malaria, and asymptomatic infection."
)


def paper(paper_id: str, title: str, **values: object) -> dict:
    return {
        "paper_id": paper_id,
        "title": title,
        "rank": values.pop("rank", 1),
        **values,
    }


def chunks(paper_id: str, text: str) -> list[dict]:
    return [
        {
            "paper_id": paper_id,
            "chunk_id": f"{paper_id}_C1",
            "section": "abstract",
            "text": text,
        }
    ]


def test_clinical_expression_filter_activates_only_for_explicit_joint_request() -> None:
    assert requests_direct_clinical_parasite_expression(CLINICAL_EXPRESSION_QUESTION)
    assert requests_direct_clinical_parasite_expression(
        "Find parasite gene-expression differences between clinical outcomes and disease "
        "severity in Plasmodium infections."
    )
    assert not requests_direct_clinical_parasite_expression(
        "Find papers about RIFIN binding to LILRB1 and immune evasion."
    )
    assert not requests_direct_clinical_parasite_expression(
        "Find transcriptome methods for Plasmodium falciparum."
    )


def test_clinical_expression_filter_keeps_direct_and_reanalysis_reports() -> None:
    papers = [
        paper(
            "P_VAR",
            "PfEMP1 expression is associated with cerebral malaria in children",
            rank=1,
        ),
        paper(
            "P_CORE",
            "Integrated parasite transcriptome analysis in severe malaria",
            rank=2,
        ),
        paper(
            "P_RIFIN",
            "RIFIN transcription is higher in severe than uncomplicated malaria",
            rank=3,
        ),
        paper(
            "P_REANALYSIS",
            "Plasmodium transcription in different clinical presentations associates with "
            "circulation time",
            rank=4,
        ),
        paper(
            "P_ASYMPTOMATIC",
            "Parasite transcriptome reanalysis reveals STEVOR upregulation in asymptomatic "
            "malaria",
            rank=5,
        ),
    ]
    contexts = {
        "P_VAR": [],
        "P_CORE": chunks(
            "P_CORE",
            "Abstract Here we performed RNA sequencing in severe and uncomplicated cases. "
            "We found parasite genes differentially expressed between severe and uncomplicated "
            "malaria after adjustment for parasite developmental stage. Introduction",
        ),
        "P_RIFIN": [],
        "P_REANALYSIS": [],
        "P_ASYMPTOMATIC": [],
    }

    retained = retain_direct_clinical_expression_papers(
        CLINICAL_EXPRESSION_QUESTION,
        papers,
        contexts,
    )

    assert [row["paper_id"] for row in retained] == [
        "P_VAR",
        "P_CORE",
        "P_RIFIN",
        "P_REANALYSIS",
        "P_ASYMPTOMATIC",
    ]
    assert retained[1]["clinical_expression_directness"]["basis"] == (
        "abstract_current_result"
    )


def test_clinical_expression_filter_resolves_domain_anaphora_from_specific_title() -> None:
    records = [
        paper(
            "P_DOMAIN",
            "PfEMP1 A-Type ICAM-1-Binding Domains in Cerebral Malaria",
        )
    ]
    contexts = {
        "P_DOMAIN": chunks(
            "P_DOMAIN",
            "Abstract We measured var gene expression in cerebral and uncomplicated "
            "malaria isolates. EPCR-binding domains were overexpressed in cerebral "
            "malaria isolates compared with uncomplicated malaria isolates. Introduction",
        )
    }

    retained = retain_direct_clinical_expression_papers(
        CLINICAL_EXPRESSION_QUESTION,
        records,
        contexts,
    )

    assert [row["paper_id"] for row in retained] == ["P_DOMAIN"]
    assert retained[0]["clinical_expression_directness"]["basis"] == (
        "abstract_current_result"
    )
    assert "EPCR-binding domains were overexpressed" in retained[0][
        "clinical_expression_directness"
    ]["evidence_excerpt"]


def test_clinical_expression_filter_rejects_background_and_mechanistic_matches() -> None:
    papers = [
        paper(
            "P_BINDING",
            "Severe malaria is associated with parasite binding to endothelial protein C "
            "receptor",
            rank=1,
        ),
        paper(
            "P_STRUCTURE",
            "Structural conservation allows EPCR binding by PfEMP1 implicated in severe malaria",
            rank=2,
        ),
        paper(
            "P_ANTIBODY",
            "A subset of Plasmodium falciparum RIFINs is linked to severe malaria risk "
            "reduction",
            rank=3,
        ),
        paper(
            "P_LAB_SELECTION",
            "A subset of var genes encodes ligands for binding to human brain endothelial cells",
            rank=4,
        ),
    ]
    contexts = {
        "P_BINDING": chunks(
            "P_BINDING",
            "Abstract Severe malaria is associated with expression of PfEMP1 domain cassettes "
            "8 and 13, but their receptor was unknown. Here, we identify EPCR as the receptor "
            "and measure recombinant-domain binding. Introduction",
        ),
        "P_STRUCTURE": chunks(
            "P_STRUCTURE",
            "Summary EPCR-binding PfEMP1 expression was previously associated with severe "
            "malaria. We determine crystal structures and identify a conserved binding surface. "
            "Introduction",
        ),
        "P_ANTIBODY": chunks(
            "P_ANTIBODY",
            "Abstract We profiled anti-RIFIN antibody responses in severe and non-severe malaria "
            "and found antibodies to a RIFIN subset associated with lower severe-malaria risk. "
            "We then measured receptor binding. Introduction",
        ),
        "P_LAB_SELECTION": chunks(
            "P_LAB_SELECTION",
            "Abstract Cerebral malaria is associated with var expression in prior studies. Here "
            "we selected laboratory parasites on brain endothelial cells and measured their var "
            "expression and binding phenotype. Introduction",
        ),
    }

    retained = retain_direct_clinical_expression_papers(
        CLINICAL_EXPRESSION_QUESTION,
        papers,
        contexts,
    )

    assert retained == []


def test_clinical_expression_filter_rejects_host_expression_in_parasite_context() -> None:
    records = [
        paper(
            "P_HOST",
            "Parasite transcriptome and host responses in severe malaria",
        )
    ]
    contexts = {
        "P_HOST": chunks(
            "P_HOST",
            "Abstract Here we performed dual RNA sequencing in infected children. "
            "In Plasmodium falciparum malaria, human gene expression was higher in "
            "severe than uncomplicated disease. Introduction",
        )
    }

    retained = retain_direct_clinical_expression_papers(
        CLINICAL_EXPRESSION_QUESTION,
        records,
        contexts,
    )

    assert retained == []


def test_clinical_expression_filter_rejects_technical_batch_relation() -> None:
    records = [
        paper(
            "P_BATCH",
            "Parasite transcriptome variation in clinical malaria samples",
        )
    ]
    contexts = {
        "P_BATCH": chunks(
            "P_BATCH",
            "Abstract Here we sequenced parasite RNA from children with severe malaria. "
            "Parasite gene expression was associated with sequencing batch in severe-malaria "
            "samples; clinical severity was not compared. Introduction",
        )
    }

    retained = retain_direct_clinical_expression_papers(
        CLINICAL_EXPRESSION_QUESTION,
        records,
        contexts,
    )

    assert retained == []


def test_clinical_expression_filter_rejects_mixed_host_and_parasite_load_claim() -> None:
    records = [
        paper(
            "MAL_GEN_0025",
            "Integrated pathogen load and dual transcriptome analysis of systemic "
            "host-pathogen interactions in severe malaria",
        )
    ]
    contexts = {
        "MAL_GEN_0025": chunks(
            "MAL_GEN_0025",
            "Abstract Here we performed dual transcriptome analysis in malaria. Up to 99% of "
            "human differential gene expression in severe malaria was driven by differences "
            "in parasite load, whereas parasite gene expression showed little association "
            "with parasite load. Introduction",
        )
    }

    retained = retain_direct_clinical_expression_papers(
        CLINICAL_EXPRESSION_QUESTION,
        records,
        contexts,
    )

    assert retained == []


def test_clinical_expression_filter_keeps_explicit_severity_association() -> None:
    records = [
        paper(
            "MAL_GEN_0025",
            "Integrated pathogen load and dual transcriptome analysis of systemic "
            "host-pathogen interactions in severe malaria",
        )
    ]
    contexts = {
        "MAL_GEN_0025": chunks(
            "MAL_GEN_0025",
            "Abstract Here we performed dual transcriptome analysis in malaria. We observed "
            "severity-associated variation in the expression of parasite genes that determine "
            "cytoadhesion and parasite growth rate. Introduction",
        )
    }

    retained = retain_direct_clinical_expression_papers(
        CLINICAL_EXPRESSION_QUESTION,
        records,
        contexts,
    )

    assert [row["paper_id"] for row in retained] == ["MAL_GEN_0025"]
    assert "severity-associated variation" in retained[0][
        "clinical_expression_directness"
    ]["evidence_excerpt"]


def test_clinical_expression_filter_does_not_change_unrelated_source_search() -> None:
    records = [paper("P1", "RIFIN binding to LILRB1", rank=7)]

    retained = retain_direct_clinical_expression_papers(
        "Find primary studies of RIFIN receptor binding or immune evasion.",
        records,
        {"P1": []},
    )

    assert retained == records


def test_publication_version_collapse_prefers_journal_and_preserves_version_metadata() -> None:
    title = (
        "Transcriptome analysis of Plasmodium falciparum isolates from Benin reveals specific "
        "gene expression associated with cerebral malaria"
    )
    records = [
        paper(
            "P_PREPRINT",
            title,
            rank=1,
            year="2021",
            doi="10.1101/2021.11.08.467248",
            journal="bioRxiv",
        ),
        paper(
            "P_JOURNAL",
            title,
            rank=2,
            year="2022",
            doi="10.1093/infdis/jiac086",
            journal="The Journal of Infectious Diseases",
        ),
        paper("P_DISTINCT", "A distinct malaria transcriptome report", rank=3),
    ]

    collapsed = collapse_detected_publication_versions(records, {})

    assert [row["paper_id"] for row in collapsed] == ["P_JOURNAL", "P_DISTINCT"]
    representative = collapsed[0]
    assert representative["publication_version_display"] == (
        "representative_of_detected_family"
    )
    assert representative["publication_version_relationship_types"] == [
        "same_preprint_family"
    ]
    assert representative["publication_family_report_count"] == 2
    assert {row["paper_id"] for row in representative["publication_family_reports"]} == {
        "P_PREPRINT",
        "P_JOURNAL",
    }


def test_publication_version_collapse_does_not_merge_title_similarity_alone() -> None:
    records = [
        paper(
            "P1",
            "The pathogenic basis of malaria",
            rank=1,
            doi="10.1038/example-a",
        ),
        paper(
            "P2",
            "The pathogenic basis of malaria",
            rank=2,
            doi="10.1038/example-b",
        ),
    ]

    collapsed = collapse_detected_publication_versions(records, {})

    assert [row["paper_id"] for row in collapsed] == ["P1", "P2"]


def test_publication_version_collapse_never_merges_missing_id_records() -> None:
    records = [
        paper("", "First unresolved report", rank=1),
        paper("", "Second unresolved report", rank=2),
    ]

    collapsed = collapse_detected_publication_versions(records, {})

    assert [row["title"] for row in collapsed] == [
        "First unresolved report",
        "Second unresolved report",
    ]
