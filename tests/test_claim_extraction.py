from __future__ import annotations

from originrag.claims.extraction import extract_claims_from_chunk, extract_claims_from_chunks, split_sentences


def test_extracts_structured_claim_from_pfemp1_epcr_sentence() -> None:
    chunk = {
        "paper_id": "P1",
        "chunk_id": "P1_CHUNK_0001",
        "section": "results",
        "page_start": 3,
        "page_end": 4,
        "text": (
            "We found that PfEMP1 CIDRalpha1 domains were associated with EPCR binding "
            "in children with severe malaria. This sentence has no relevant biomedical claim."
        ),
    }
    paper_meta = {
        "author": "Smith",
        "year": "2024",
        "citation_short": "Smith 2024",
        "title": "EPCR binding paper",
        "doi": "10.1/test",
    }

    claims = extract_claims_from_chunk(chunk, paper_meta=paper_meta)

    assert len(claims) == 1
    claim = claims[0]
    assert claim["claim_type"] == "finding"
    assert claim["evidence_strength"] in {"moderate", "strong"}
    assert claim["parasite_feature"] == "PfEMP1"
    assert claim["host_feature"] == "EPCR"
    assert claim["disease_context"] == "severe malaria"
    assert claim["source_pages"] == "3-4"
    assert claim["doi"] == "10.1/test"
    assert claim["entity_ids"]
    assert claim["duplicate_count"] == 1


def test_skips_reference_like_chunks() -> None:
    chunk = {
        "paper_id": "P1",
        "chunk_id": "P1_CHUNK_REF",
        "section": "references",
        "text": "References. Smith et al. PfEMP1 EPCR severe malaria. Nature. 2024.",
    }

    assert extract_claims_from_chunk(chunk) == []


def test_sentence_splitter_protects_scientific_abbreviations() -> None:
    sentences = split_sentences(
        "Fig. 1 shows P. falciparum binding to EPCR. Smith et al. demonstrated this in severe malaria."
    )

    assert sentences == [
        "Fig. 1 shows P. falciparum binding to EPCR.",
        "Smith et al. demonstrated this in severe malaria.",
    ]


def test_deduplicates_same_claim_sentence_within_paper() -> None:
    chunks = [
        {
            "paper_id": "P1",
            "chunk_id": "P1_CHUNK_0001",
            "section": "results",
            "text": "PfEMP1 CIDRalpha1 domains were associated with EPCR binding in severe malaria.",
        },
        {
            "paper_id": "P1",
            "chunk_id": "P1_CHUNK_0002",
            "section": "discussion",
            "text": "PfEMP1 CIDRalpha1 domains were associated with EPCR binding in severe malaria.",
        },
    ]

    claims = extract_claims_from_chunks(chunks)

    assert len(claims) == 1
    assert claims[0]["duplicate_count"] == 2
    assert "duplicate_sentence" in claims[0]["claim_quality_flags"]
