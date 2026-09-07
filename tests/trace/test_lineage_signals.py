from pfempire.trace.lineage import (
    build_lineage_edges,
    extract_data_origin_mentions,
    extract_identifiers,
    extract_named_cohorts,
    suggest_lineage_signals,
)


def test_numbered_data_reuse_references_resolve_to_cited_origin():
    document = [
        {
            "chunk_id": "methods",
            "section": "methods",
            "text": "Published var expression profiles were used for this study [20, 21].",
        },
        {
            "chunk_id": "refs",
            "section": "references",
            "text": (
                "20. Warimwe GM et al. (2009) Plasmodium falciparum var expression.\n"
                "21. Warimwe GM et al. (2012) Prognostic indicators."
            ),
        },
    ]

    mentions = extract_data_origin_mentions(document)

    assert set(mentions) == {"warimwe:2009", "warimwe:2012"}
    assert mentions["warimwe:2009"][0]["chunk_id"] == "methods"


def test_shared_cited_data_origin_is_exposed_as_unreviewed_overlap_signal():
    papers = [
        {"paper_id": "new", "title": "New analysis", "author": "Ndugwa", "year": "2026"},
        {"paper_id": "old", "title": "Older analysis", "author": "Warimwe", "year": "2013"},
    ]
    contexts = {
        "new": "The dataset was previously described (Warimwe et al., 2009).",
        "old": (
            "Published expression profiles were used for this study [20].\n"
            "20. Warimwe GM et al. (2009) Earlier profiles."
        ),
    }

    signals = suggest_lineage_signals(papers, contexts)
    signal = next(s for s in signals if s.signal_type == "shared_cited_data_origin")

    assert signal.signal_strength == 0.85
    assert signal.details["shared_origins"] == ["warimwe:2009"]
    assert signal.status == "suggested"

def test_accession_and_trial_regexes():
    ids=extract_identifiers("GSE12345 PRJNA999 NCT12345678 ISRCTN42")
    assert ids["geo"]==["GSE12345"] and ids["nct"]==["NCT12345678"]

def test_shared_active_accession_suggests_dependence_with_evidence_locations():
    papers=[{"paper_id":"a","title":"first","doi":""},{"paper_id":"b","title":"second","doi":""}]
    contexts = {
        "a": [{"chunk_id": "a2", "page_start": 9, "page_end": 9, "text": "METHODS\nWe analyzed data deposited as GSE12345."}],
        "b": [{"chunk_id": "b4", "page_start": 12, "page_end": 13, "text": "DATA AVAILABILITY\nOur data are available under GSE12345."}],
    }
    signals=suggest_lineage_signals(papers, contexts)
    signal=next(s for s in signals if s.signal_type=="same_dataset_accession")
    assert signal.status == "suggested"
    assert signal.details["evidence"]["a"][0]["chunk_id"] == "a2"
    assert signal.details["evidence"]["a"][0]["page_start"] == 9
    assert signal.details["mention_classification"] == {
        "a": ["own_data"],
        "b": ["own_data"],
    }


def test_shared_reference_accession_is_review_only_not_dependence_signal():
    papers=[{"paper_id":"a","title":"first"},{"paper_id":"b","title":"second"}]
    contexts = {
        "a": [{"chunk_id": "a9", "text": "REFERENCES\nPrior et al. GSE12345"}],
        "b": [{"chunk_id": "b8", "text": "INTRODUCTION\nA previous study reported GSE12345."}],
    }
    signals=suggest_lineage_signals(papers, contexts)
    review=next(s for s in signals if s.signal_type=="shared_identifier_mention")
    assert review.details["candidate_signal_type"] == "same_dataset_accession"
    assert review.details["interpretation"] == "shared mention only; not evidence of a shared origin"
    assert not any(s.signal_type=="same_dataset_accession" for s in signals)


def test_ambiguous_shared_identifier_remains_review_only():
    papers=[{"paper_id":"a","title":"first"},{"paper_id":"b","title":"second"}]
    signals=suggest_lineage_signals(papers,{"a":"GSE12345","b":"GSE12345"})
    assert any(s.signal_type=="shared_identifier_mention" for s in signals)
    assert not any(s.signal_type=="same_dataset_accession" for s in signals)

def test_extended_accessions_and_named_cohorts():
    ids=extract_identifiers("E-MTAB-1234 phs000999.v2.p1 ERR42")
    assert ids["ena"]==["E-MTAB-1234"] and ids["dbgap"]==["PHS000999.V2.P1"]
    assert extract_named_cohorts("participants from the PRISM cohort and clinical cohort")==["PRISM"]

def test_shared_named_cohort_requires_active_study_context():
    papers=[{"paper_id":"a","title":"first"},{"paper_id":"b","title":"second"}]
    contexts = {
        "a": "METHODS\nWe recruited the PRISM cohort.",
        "b": "PATIENTS\nParticipants from our PRISM cohort were enrolled.",
    }
    signals=suggest_lineage_signals(papers, contexts)
    signal=next(s for s in signals if s.signal_type=="same_cohort")
    assert signal.status=="suggested" and signal.signal_strength==0.8


def test_automatic_shared_origin_signal_never_becomes_trusted_graph_relation():
    papers=[{"paper_id":"a","title":"first"},{"paper_id":"b","title":"second"}]
    signals=suggest_lineage_signals(
        papers,
        {
            "a": "METHODS\nWe analyzed data deposited as GSE12345.",
            "b": "METHODS\nWe used GSE12345 for this analysis.",
        },
    )
    edge = build_lineage_edges(signals)[0]
    assert edge.origin_relation == "unresolved"
    assert edge.is_verified is False

def test_same_pmcid_marks_publication_version():
    papers=[{"paper_id":"a","title":"first","pmcid":"PMC123"},{"paper_id":"b","title":"second","pmcid":"PMC123"}]
    signals=suggest_lineage_signals(papers)
    assert any(s.signal_type=="same_publication_version" for s in signals)

def test_elife_version_dois_mark_publication_version():
    papers=[
        {"paper_id":"a","title":"same work","doi":"10.7554/eLife.103542.2"},
        {"paper_id":"b","title":"same work","doi":"10.7554/eLife.103542"},
    ]
    signals=suggest_lineage_signals(papers)
    version=next(s for s in signals if s.signal_type=="same_publication_version")
    assert version.details["identifier_type"]=="elife_doi_family"

def test_identical_title_alone_is_not_a_publication_version():
    papers=[
        {"paper_id":"a","title":"The pathogenic basis of malaria","doi":"10.1038/415669a"},
        {"paper_id":"b","title":"The pathogenic basis of malaria","doi":"10.1038/415673a"},
    ]
    signals=suggest_lineage_signals(papers)
    assert any(s.signal_type=="high_title_similarity" for s in signals)
    assert not any(s.signal_type=="same_publication_version" for s in signals)

def test_exact_title_and_preprint_journal_dois_mark_preprint_family():
    papers=[
        {"paper_id":"a","title":"Automated malaria detection","doi":"10.1101/2021.01.01.1"},
        {"paper_id":"b","title":"Automated malaria detection","doi":"10.1017/example"},
    ]
    signals=suggest_lineage_signals(papers)
    assert any(s.signal_type=="same_preprint_family" for s in signals)

def test_long_contained_title_and_preprint_journal_dois_mark_preprint_family():
    papers=[
        {
            "paper_id":"a",
            "title":"Transcriptome analysis of Plasmodium falciparum isolates from Benin",
            "doi":"10.1101/2021.11.08.467248",
        },
        {
            "paper_id":"b",
            "title":"Transcriptome analysis of Plasmodium falciparum isolates from Benin reveals specific gene expression associated with cerebral malaria",
            "doi":"10.1093/infdis/jiac086",
        },
    ]
    signals=suggest_lineage_signals(papers)
    signal=next(s for s in signals if s.signal_type=="same_preprint_family")
    assert signal.signal_strength==0.95

def test_contained_titles_without_preprint_journal_pair_remain_soft():
    papers=[
        {"paper_id":"a","title":"A long shared malaria research article title","doi":"10.1000/a"},
        {"paper_id":"b","title":"A long shared malaria research article title extended","doi":"10.1000/b"},
    ]
    signals=suggest_lineage_signals(papers)
    assert not any(s.signal_type=="same_preprint_family" for s in signals)
