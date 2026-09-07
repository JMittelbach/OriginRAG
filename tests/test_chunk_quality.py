from __future__ import annotations

from pfempire.retrieval.chunk_quality import classify_chunk_quality


def test_reference_like_text_is_classified() -> None:
    text = """
    References
    1. Smith J et al. Nature 2013 doi:10.1000/example
    2. Turner L et al. PLOS Pathogens 2013 doi:10.1000/example
    3. Lavstsen T et al. PNAS 2012 doi:10.1000/example
    4. Jespersen JS et al. EMBO Molecular Medicine 2016 doi:10.1000/example
    5. Bernabeu M et al. PNAS 2016 doi:10.1000/example
    6. Lennartz F et al. Cell Host Microbe 2017 doi:10.1000/example
    """

    quality = classify_chunk_quality(text)

    assert quality["is_reference_like"]
    assert "reference_like" in quality["flags"]
    assert quality["quality_penalty"] > 0


def test_table_like_text_is_classified() -> None:
    text = "\n".join(
        [
            "Sample Gene Count Pvalue Ratio",
            "A01 var1 123 0.001 45.2",
            "A02 var2 456 0.050 12.3",
            "A03 var3 789 0.200 99.1",
            "A04 var4 321 0.010 22.5",
            "A05 var5 654 0.030 16.7",
        ]
    )

    quality = classify_chunk_quality(text)

    assert quality["is_table_like"]
    assert "table_like" in quality["flags"]


def test_normal_scientific_paragraph_has_low_penalty() -> None:
    text = (
        "Parasites expressing PfEMP1 variants that bind endothelial protein C receptor "
        "have been associated with severe malaria syndromes in children. These studies "
        "suggest that domain composition and receptor binding phenotype can shape the "
        "clinical presentation of infection, although host immunity and parasite biomass "
        "also contribute to disease outcome. Receptor-binding phenotypes can therefore "
        "provide a mechanistic bridge between parasite antigenic variation and clinical "
        "severity. Longitudinal cohorts and controlled infection studies can help separate "
        "the effects of parasite genotype, transcriptional state, host exposure history, "
        "and inflammatory response. This paragraph is written as continuous scientific "
        "prose with enough context for retrieval evaluation and should not be treated as "
        "a noisy artifact."
    )

    quality = classify_chunk_quality(text)

    assert not quality["is_reference_like"]
    assert not quality["is_table_like"]
    assert quality["quality_penalty"] < 5


def test_citation_dense_introduction_with_quantitative_evidence_is_not_a_reference_list() -> None:
    text = (
        "Most switching studies used long-term cultures (Roberts et al., 1992; "
        "Horrocks et al., 2004; Frank et al., 2007; Mok et al., 2008; Duffy et al., 2009). "
        "Other work examined clinical isolates (Peters et al., 2002; Peters et al., 2007; "
        "Staalsoe et al., 2002; Kaestli et al., 2004; Wunderlich et al., 2005). "
        "In long-term in vitro cultures, switching rates are about 2% or less per generation. "
        "Experimental infections reported rates up to 18% during early infection. "
        "These measurements motivate direct analysis of recent ex vivo isolates. " * 8
    )

    quality = classify_chunk_quality(text)

    assert not quality["is_reference_like"]


def test_statistical_table_continuing_into_discussion_is_flagged() -> None:
    text = """
    Host age
    0.9
    0.80-1.03
    0.17
    Antibodies to P7063
    0.5
    0.32-0.81
    0.005
    cys2
    3.6
    1.50-8.73
    0.004
    Presented are odds ratios, 95% confidence intervals, and p values from
    multiple logistic regression models testing the independent association
    of severe malaria with host age, cys2 expression, and IgG antibody levels.
    """

    quality = classify_chunk_quality(text)

    assert quality["is_table_like"]
    assert "table_like" in quality["flags"]
