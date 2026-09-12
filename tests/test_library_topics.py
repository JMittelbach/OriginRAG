import pytest

from originrag.library_topics import LIBRARY_TOPICS, library_topic


@pytest.mark.parametrize(("title", "category", "expected"), [
    ("Spatial transcriptomics reveals tumor architecture", "noninfectious_disease_biology", "spatial_transcriptomics"),
    ("Spatially resolved transcriptomics in tissue", "other", "spatial_transcriptomics"),
    ("Deep learning and alignment of spatially resolved single-cell transcriptomes with Tangram", "general_methods", "spatial_transcriptomics"),
    ("Squidpy: a scalable framework for spatial omics analysis", "general_methods", "spatial_transcriptomics"),
    ("SPOTlight: deconvolute spatial transcriptomics spots", "sc_tools", "spatial_transcriptomics"),
    ("PfEMP1 in malaria", "general_malaria", "var_pfemp1"),
    ("RIFINs and STEVORs", "other", "var_pfemp1"),
    ("Multigene families in comparative biology", "other", "var_pfemp1"),
    ("OrthoFinder: orthogroup inference", "other", "genomics_rnaseq"),
    ("Salmon provides fast quantification of transcript expression", "other", "genomics_rnaseq"),
    ("Salmon migration and conservation", "other", "other"),
    ("RNA-seq differential expression analysis", "noninfectious_disease_biology", "genomics_rnaseq"),
    ("Single-cell integration and annotation methods", "noninfectious_disease_biology", "single_cell_methods"),
    ("Single-cell study reveals liver disease", "noninfectious_disease_biology", "host_disease"),
    ("Foundation models for single-cell analysis", "sc_tools", "ai_computational"),
    ("A study of library size in spatial transcriptomics", "general_methods", "spatial_transcriptomics"),
    ("Batch effects in gene expression studies", "other", "research_methods"),
    ("Antimalarial resistance", "resistance_dx", "malaria_omics_resistance"),
    ("Clinical malaria", "general_malaria", "malaria_biology"),
    ("", "new_unknown_category", "other"),
])
def test_library_topic(title, category, expected):
    record = {"title": title, "category": category}
    before = dict(record)
    assert library_topic(record)[0] == expected
    assert record == before


def test_ten_unique_library_topics():
    assert len(LIBRARY_TOPICS) == 10
    assert len({key for key, _, _ in LIBRARY_TOPICS}) == 10
    categories = [category for _, _, cats in LIBRARY_TOPICS for category in cats]
    assert len(categories) == len(set(categories))
