from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import fitz

from pfempire.provenance.library import LibraryStatusResolver
from pfempire.provenance.references import (
    extract_pdf_citation_map,
    extract_jats_citation_map,
    normalize_doi,
    normalize_pmcid,
    normalize_pmid,
    parse_numbered_references,
    reference_record,
    trace_claim_sources,
)
from pfempire.provenance.resolver import (
    resolve_reference_against_library,
    resolve_trace_against_library,
)
from pfempire.provenance.store import upsert_source_trace
from pfempire.claims.store import upsert_claims


def test_numbered_reference_parser_keeps_sequence_and_normalizes_doi() -> None:
    text = """
1. Alpha, A. First source title. Journal 12, 1-8 (2020).
2.
Beta, B. Second source title. Journal 13, 9-10 (2021). doi: 10.1000/SECOND.
2024. This line is not a numbered reference.
3. Gamma, C. Third source title. Journal 14, 11-12 (2022).
"""

    references = parse_numbered_references(text)

    assert [reference.reference_index for reference in references] == [1, 2, 3]
    assert references[1].doi == "10.1000/second"
    assert "2024. This line" in references[1].citation_text


def test_reference_parser_handles_references_and_notes_and_wrapped_doi() -> None:
    references = parse_numbered_references(
        """
1. Alpha A. A paper. https://doi.org/10.1001/
example.2024.123 (2024). [PubMed: 123456]
2. Beta B. Another paper. 2023.
"""
    )

    assert references[0].doi == "10.1001/example.2024.123"
    assert references[0].pmid == "123456"


def test_reference_parser_repairs_compact_publisher_doi_spacing() -> None:
    references = parse_numbered_references(
        "1. Alpha A. Journal. https://doi.org/10.1002/ajh.\n23200 (2012).\n"
        "2. Beta B. Another paper. 2023."
    )

    assert references[0].doi == "10.1002/ajh.23200"


def test_reference_parser_removes_publisher_furniture_before_identifier_extraction() -> None:
    carrier_doi = "10.1038/s41467-025-59281-5"
    record = reference_record(
        3,
        "Christensen, S. S. A neurological source. Journal 109, 233–238 (2015). "
        f"Article https://doi.org/{carrier_doi} "
        "Nature Communications | (2025) 16:4620 11",
    )

    assert record.citation_text.endswith("(2015).")
    assert record.doi == ""
    assert record.year == "2015"
    assert carrier_doi not in record.work_id


def test_reference_parser_stops_before_post_reference_article_sections() -> None:
    record = reference_record(
        57,
        "de Steenhuijsen Piters, W. et al. Nasopharyngeal microbiota and disease "
        "severity. Journal 194, 1104–1115 (2016).\nAcknowledgements\nThis study was "
        "supported by a funder. https://doi.org/10.1038/s42003-025-08407-y",
    )

    assert record.citation_text.endswith("(2016).")
    assert record.doi == ""
    assert record.year == "2016"


def test_reference_parser_supports_bracketed_and_bare_number_lists() -> None:
    bracketed = parse_numbered_references(
        "[1] Alpha A. First source. 2020.\n[2] Beta B. Second source. 2021."
    )
    bare = parse_numbered_references(
        "1 Alpha A. First source. 2020.\n2 Beta B. Second source. 2021."
    )

    assert [record.reference_index for record in bracketed] == [1, 2]
    assert [record.reference_index for record in bare] == [1, 2]


def test_reference_identifier_normalizers_fail_closed() -> None:
    assert normalize_doi("10.1000/x(y)") == "10.1000/x(y)"
    assert normalize_doi("not a DOI") == ""
    assert normalize_pmid("garbage 2024") == ""
    assert normalize_pmcid("garbage 2024") == ""


def test_pdf_parser_unions_named_and_plain_markers_and_ignores_parenthetical_numbers(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "hybrid.pdf"
    document = fitz.open()
    body = document.new_page()
    body.insert_text(
        (72, 72),
        "Linked evidence [1] and plain evidence [2]. Case definition (1) is not a citation.",
    )
    marker_rect = body.search_for("[1]")[0]
    body.insert_link({"kind": fitz.LINK_NAMED, "from": marker_rect, "name": "bm_CR1"})
    references = document.new_page()
    references.insert_text(
        (72, 72),
        "References\n1. Alpha A. Linked source. Journal. 2020.\n"
        "2. Beta B. Plain source. Journal. 2021.",
    )
    document.save(pdf_path)
    document.close()

    citation_map = extract_pdf_citation_map(pdf_path, "P_HYBRID")

    methods_by_reference = {
        (mention["reference_index"], mention["citation_link_status"])
        for mention in citation_map["mentions"]
    }
    assert (1, "link_rect") in methods_by_reference
    assert (2, "heuristic_sentence") in methods_by_reference
    assert len([item for item in citation_map["mentions"] if item["reference_index"] == 1]) == 1


def test_unregistered_identifier_is_explicitly_unverified(tmp_path: Path) -> None:
    registry = tmp_path / "paper_registry.tsv"
    registry.write_text("paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tlocal_pdf_relpath\n", encoding="utf-8")
    resolver = LibraryStatusResolver(
        registry_path=registry,
        bib_dir=tmp_path / "bib",
        chunks_dir=tmp_path / "chunks",
    )

    result = resolve_reference_against_library(
        {
            "doi": "10.1000/external",
            "citation_text": "Alpha A. External source. 2024. doi:10.1000/external",
        },
        resolver,
    )

    assert result["bibliographic_resolution_status"] == "identifier_extracted_unverified"
    assert result["library_status"]["state"] == "not_in_library"


def test_externally_verified_identifier_is_not_downgraded_without_local_record(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "paper_registry.tsv"
    registry.write_text("paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tlocal_pdf_relpath\n", encoding="utf-8")
    resolver = LibraryStatusResolver(
        registry_path=registry,
        bib_dir=tmp_path / "bib",
        chunks_dir=tmp_path / "chunks",
    )

    result = resolve_reference_against_library(
        {
            "doi": "10.1000/external",
            "title": "Externally verified source",
            "bibliographic_resolution_status": "exact_id",
            "metadata_source": "crossref_exact_doi",
        },
        resolver,
    )

    assert result["bibliographic_resolution_status"] == "exact_id"
    assert result["work_id"] == "doi:10.1000/external"
    assert result["library_status"]["state"] == "not_in_library"


def test_registry_title_year_match_is_not_mislabeled_as_identifier_match(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "paper_registry.tsv"
    registry.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tlocal_pdf_relpath\n"
        "P1\tExact structured title\t2024\t10.1000/registered\t\t\tmissing.pdf\n",
        encoding="utf-8",
    )
    resolver = LibraryStatusResolver(
        registry_path=registry,
        bib_dir=tmp_path / "bib",
        chunks_dir=tmp_path / "chunks",
    )

    result = resolve_reference_against_library(
        {"title": "Exact structured title", "year": "2024"},
        resolver,
    )

    assert result["bibliographic_resolution_status"] == "exact_title"
    assert result["doi"] == "10.1000/registered"
    assert result["verified_identifiers"] == {"doi": "10.1000/registered"}


def test_printed_identifier_conflict_blocks_local_title_resolution(tmp_path: Path) -> None:
    registry = tmp_path / "paper_registry.tsv"
    registry.write_text(
        "paper_id\tauthor\ttitle\tyear\tdoi\tpmid\tpmcid\tlocal_pdf_relpath\n"
        "P1\tAlpha\tA uniquely printed scientific source title\t2024\t"
        "10.1000/correct\t\t\tmissing.pdf\n",
        encoding="utf-8",
    )
    resolver = LibraryStatusResolver(
        registry_path=registry,
        bib_dir=tmp_path / "bib",
        chunks_dir=tmp_path / "chunks",
    )

    result = resolve_reference_against_library(
        {
            "doi": "10.2000/wrong",
            "author": "Alpha",
            "year": "2024",
            "citation_text": "Alpha. A uniquely printed scientific source title. 2024.",
        },
        resolver,
    )

    assert result["bibliographic_resolution_status"] == "conflict"
    assert result["doi"] == "10.2000/wrong"
    assert result["candidate_registry_paper_id"] == "P1"


def test_title_match_does_not_verify_identifier_absent_from_registry(tmp_path: Path) -> None:
    registry = tmp_path / "paper_registry.tsv"
    registry.write_text(
        "paper_id\tauthor\ttitle\tyear\tdoi\tpmid\tpmcid\tlocal_pdf_relpath\n"
        "P1\tAlpha\tA registry source without identifiers\t2024\t\t\t\tmissing.pdf\n",
        encoding="utf-8",
    )
    resolver = LibraryStatusResolver(
        registry_path=registry,
        bib_dir=tmp_path / "bib",
        chunks_dir=tmp_path / "chunks",
    )

    result = resolve_reference_against_library(
        {
            "doi": "10.9999/unverified",
            "title": "A registry source without identifiers",
            "year": "2024",
        },
        resolver,
    )

    assert result["bibliographic_resolution_status"] == "exact_title"
    assert result["verified_identifiers"] == {}
    assert result["work_id"] == "registry:P1"


def test_fuzzy_candidate_availability_is_not_presented_as_confirmed_source_status(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "paper_registry.tsv"
    registry.write_text(
        "paper_id\tauthor\ttitle\tyear\tdoi\tpmid\tpmcid\tlocal_pdf_relpath\n"
        "P1\tAlpha\tA distinctive scientific evidence title for malaria\t2024\t"
        "10.1000/source\t\t\tpapers/source.pdf\n",
        encoding="utf-8",
    )
    pdf = tmp_path / "bib" / "papers" / "source.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\n")
    resolver = LibraryStatusResolver(
        registry_path=registry,
        bib_dir=tmp_path / "bib",
        chunks_dir=tmp_path / "chunks",
    )

    result = resolve_reference_against_library(
        {
            "author": "Alpha",
            "year": "2024",
            "citation_text": (
                "Alpha. A distinctive scientific evidence tttle for malaria. 2024."
            ),
        },
        resolver,
    )

    assert result["bibliographic_resolution_status"] == "candidate_match"
    assert result["library_status"]["state"] == "unresolved"
    assert result["candidate_library_status"]["state"] == "library"


def test_title_resolution_tolerates_one_missing_word_boundary(tmp_path: Path) -> None:
    registry = tmp_path / "paper_registry.tsv"
    registry.write_text(
        "paper_id\tauthor\ttitle\tyear\tdoi\tpmid\tpmcid\tlocal_pdf_relpath\n"
        "P1\tYamagishi\tInteractive transcriptome analysis of malaria patients and "
        "infectingPlasmodium falciparum\t2014\t10.1000/source\t\t\tmissing.pdf\n",
        encoding="utf-8",
    )
    resolver = LibraryStatusResolver(
        registry_path=registry,
        bib_dir=tmp_path / "bib",
        chunks_dir=tmp_path / "chunks",
    )

    result = resolve_reference_against_library(
        {
            "author": "Yamagishi",
            "year": "2014",
            "citation_text": (
                "Yamagishi J. Interactive transcriptome analysis of malaria patients "
                "and infecting Plasmodium falciparum. Genome Res. 2014."
            ),
        },
        resolver,
    )

    assert result["bibliographic_resolution_status"] == "exact_title"
    assert result["registry_paper_id"] == "P1"
    assert result["verified_identifiers"] == {"doi": "10.1000/source"}


def test_jats_rid_link_is_exact_but_semantic_support_stays_review_required() -> None:
    xml = """
    <article><body><sec><p>
      We reanalyzed an independent cohort <xref ref-type="bibr" rid="R2">22</xref>.
    </p></sec></body><back><ref-list>
      <ref id="R2"><label>22</label><mixed-citation>
        Nallandhighal S et al. Whole-blood transcriptional signatures. 2019.
        <pub-id pub-id-type="doi">10.1093/infdis/jiy468</pub-id>
      </mixed-citation></ref>
    </ref-list></back></article>
    """

    citation_map = extract_jats_citation_map(xml, "P_CARRIER")
    trace = trace_claim_sources(citation_map, "We reanalyzed an independent cohort", min_score=30)

    assert len(trace["mentions"]) == 1
    mention = trace["mentions"][0]
    assert mention["citation_link_status"] == "exact_rid"
    assert mention["reference"]["doi"] == "10.1093/infdis/jiy468"
    assert mention["support_role"] == "reanalysis_candidate"
    assert mention["origin_review_status"] == "review_required"


def test_namespaced_jats_uses_structured_metadata_and_omits_reference_label() -> None:
    xml = """
    <article xmlns="https://jats.nlm.nih.gov/publishing/1.3/">
      <body><sec><p>
        The carrier reused the structured source <xref ref-type="bibr" rid="R7">7</xref>.
        This unrelated sentence must stay outside the citation context.
      </p></sec></body>
      <back><ref-list><ref id="R7"><label>[7]</label><element-citation>
        <person-group person-group-type="author"><name>
          <surname>García</surname><given-names>I</given-names>
        </name></person-group>
        <article-title>A <italic>structured</italic> source</article-title>
        <source>Journal of Structured Evidence</source><year>2022</year>
        <pub-id pub-id-type="doi">https://doi.org/10.1000/STRUCTURED</pub-id>
        <pub-id pub-id-type="pmid">12345678</pub-id>
        <pub-id pub-id-type="pmcid">PMC8765432</pub-id>
      </element-citation></ref></ref-list></back>
    </article>
    """

    citation_map = extract_jats_citation_map(xml, "P_NAMESPACED")

    assert len(citation_map["references"]) == 1
    reference = citation_map["references"][0]
    assert reference["reference_index"] == 7
    assert not reference["citation_text"].startswith("[7]")
    assert reference["doi"] == "10.1000/structured"
    assert reference["pmid"] == "12345678"
    assert reference["pmcid"] == "PMC8765432"
    assert reference["title"] == "A structured source"
    assert reference["author"] == "García"
    assert reference["year"] == "2022"
    assert reference["journal"] == "Journal of Structured Evidence"
    assert reference["work_id"] == "doi:10.1000/structured"

    assert len(citation_map["mentions"]) == 1
    mention = citation_map["mentions"][0]
    assert mention["context_text"] == "The carrier reused the structured source 7."
    assert mention["scope_status"] == "jats_sentence_context"
    assert mention["citation_link_status"] == "exact_rid"
    assert mention["origin_review_status"] == "review_required"


def test_jats_multi_rid_and_repeated_xrefs_keep_distinct_occurrences() -> None:
    xml = """
    <article><body><p>
      One sentence cites <xref ref-type="bibr" rid="R1 R2">1, 2</xref>
      and repeats <xref ref-type="bibr" rid="R1">1</xref>.
    </p></body><back><ref-list>
      <ref id="R1"><label>1</label><mixed-citation>Alpha A. First. 2020.</mixed-citation></ref>
      <ref id="R2"><label>2</label><mixed-citation>Beta B. Second. 2021.</mixed-citation></ref>
    </ref-list></back></article>
    """

    citation_map = extract_jats_citation_map(xml, "P_REPEATED")
    mentions = citation_map["mentions"]

    assert len(mentions) == 3
    assert len({mention["mention_id"] for mention in mentions}) == 3
    assert [mention["reference_index"] for mention in mentions] == [1, 2, 1]
    assert mentions[0]["context_text"] == mentions[1]["context_text"]
    assert mentions[1]["context_text"] == mentions[2]["context_text"]
    assert all(mention["citation_link_status"] == "exact_rid" for mention in mentions)
    assert all(mention["origin_review_status"] == "review_required" for mention in mentions)


def test_claim_matching_abstains_when_context_does_not_match() -> None:
    citation_map = extract_jats_citation_map(
        """
        <article><body><p>EPCR binding was reported <xref ref-type="bibr" rid="R1">1</xref>.</p></body>
        <back><ref-list><ref id="R1"><label>1</label><mixed-citation>Smith A. EPCR. 2020.</mixed-citation></ref></ref-list></back>
        </article>
        """,
        "P1",
    )

    trace = trace_claim_sources(citation_map, "unrelated vaccine efficacy statement", min_score=80)

    assert trace["status"] == "citation_context_unresolved"
    assert trace["mentions"] == []


def test_printed_reference_title_resolves_uniquely_to_local_registry(tmp_path: Path) -> None:
    registry = tmp_path / "paper_registry.tsv"
    fields = [
        "paper_id", "bib_key", "author", "year", "title", "journal", "doi", "pmid", "pmcid",
        "citation_short", "citation_apa", "landing_url", "local_pdf_relpath", "pdf_url",
    ]
    with registry.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerow(
            {
                "paper_id": "P_PRIMARY",
                "bib_key": "Nallandhighal2019WholeBlood",
                "author": "Nallandhighal",
                "year": "2019",
                "title": "Whole-Blood Transcriptional Signatures Differ Between Cerebral Malaria and Severe Malarial Anemia",
                "journal": "Journal of Infectious Diseases",
                "doi": "10.1093/infdis/jiy468",
                "citation_apa": "Nallandhighal et al. (2019). Whole-Blood Transcriptional Signatures.",
                "local_pdf_relpath": "papers/primary.pdf",
            }
        )
    bib_dir = tmp_path / "bib"
    pdf = bib_dir / "papers/primary.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\n")
    resolver = LibraryStatusResolver(
        registry_path=registry,
        bib_dir=bib_dir,
        chunks_dir=tmp_path / "chunks",
    )
    citation_map = extract_jats_citation_map(
        """
        <article><body><p>We used an independent cohort <xref ref-type="bibr" rid="R22">22</xref>.</p></body>
        <back><ref-list><ref id="R22"><label>22</label><mixed-citation>
        Nallandhighal S et al. Whole-Blood Transcriptional Signatures Differ Between Cerebral Malaria and Severe Malarial Anemia. 2019.
        </mixed-citation></ref></ref-list></back></article>
        """,
        "P_CARRIER",
    )
    trace = trace_claim_sources(citation_map, "independent cohort", min_score=20)

    resolved = resolve_trace_against_library(trace, resolver)
    reference = resolved["mentions"][0]["reference"]

    assert reference["bibliographic_resolution_status"] == "exact_title"
    assert reference["registry_paper_id"] == "P_PRIMARY"
    assert reference["doi"] == "10.1093/infdis/jiy468"
    assert reference["library_status"]["state"] == "library"


def test_trace_resolver_rejects_carrier_as_its_own_cited_source(tmp_path: Path) -> None:
    registry = tmp_path / "paper_registry.tsv"
    registry.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tlocal_pdf_relpath\n"
        "P_CARRIER\tCarrier paper\t2025\t10.1000/carrier\t\t\tcarrier.pdf\n",
        encoding="utf-8",
    )
    resolver = LibraryStatusResolver(
        registry_path=registry,
        bib_dir=tmp_path,
        chunks_dir=tmp_path / "chunks",
    )
    trace = {
        "paper_id": "P_CARRIER",
        "mentions": [
            {
                "mention_id": "M1",
                "citing_paper_id": "P_CARRIER",
                "reference": {
                    "doi": "10.1000/carrier",
                    "citation_text": "Alpha A. An unrelated printed source. 2015.",
                },
            }
        ],
    }

    resolved = resolve_trace_against_library(trace, resolver)
    reference = resolved["mentions"][0]["reference"]

    assert reference["bibliographic_resolution_status"] == "conflict"
    assert reference["resolution_issue"] == "carrier_identity_contamination"
    assert reference["rejected_registry_paper_id"] == "P_CARRIER"
    assert reference["verified_identifiers"] == {}
    assert str(reference["work_id"]).startswith("citation:")
    assert resolved["n_exactly_resolved"] == 0
    assert resolved["n_carrier_identity_rejections"] == 1


def test_trace_persistence_creates_citation_graph_without_inventing_claim_edge(tmp_path: Path) -> None:
    trace = {
        "paper_id": "P_CARRIER",
        "parser_version": "claim_source_trace_v1",
        "source_sha256": "a" * 64,
        "mentions": [
            {
                "mention_id": "MENTION_1",
                "citing_paper_id": "P_CARRIER",
                "reference_index": 2,
                "marker_text": "2",
                "context_text": "A cited statement.",
                "source_page": 3,
                "source_rect": [1, 2, 3, 4],
                "citation_link_status": "link_rect",
                "scope_status": "candidate_sentence_scope",
                "parser_version": "claim_source_trace_v1",
                "source_sha256": "a" * 64,
                "support_role": "cited_background_candidate",
                "support_relation": "cites_context",
                "origin_review_status": "review_required",
                "reference": {
                    "work_id": "doi:10.1000/source",
                    "doi": "10.1000/source",
                    "citation_text": "Source A. A paper. 2020.",
                    "bibliographic_resolution_status": "exact_id",
                },
            }
        ],
    }
    db_path = tmp_path / "trace.sqlite"

    counts = upsert_source_trace(trace, db_path, source_chunk_id="CHUNK_1")
    upsert_source_trace(trace, db_path)

    assert counts == {
        "cited_works_upserted": 1,
        "citation_mentions_upserted": 1,
        "claim_source_edges_upserted": 0,
    }
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM cited_works").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM citation_mentions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM claim_sources").fetchone()[0] == 0
        assert connection.execute(
            "SELECT printed_reference FROM citation_mentions WHERE mention_id = 'MENTION_1'"
        ).fetchone()[0] == "Source A. A paper. 2020."
        assert connection.execute(
            "SELECT source_chunk_id FROM citation_mentions WHERE mention_id = 'MENTION_1'"
        ).fetchone()[0] == "CHUNK_1"


def test_trace_persistence_reconciles_enriched_work_identity(tmp_path: Path) -> None:
    db_path = tmp_path / "trace.sqlite"
    upsert_claims(
        db_path,
        [
            {
                "claim_id": "CLAIM_1",
                "claim_text": "A claim carried by the citing paper.",
                "claim_type": "finding",
                "source_paper_id": "P_CARRIER",
                "source_chunk_id": "CHUNK_1",
            }
        ],
    )
    base_mention = {
        "mention_id": "MENTION_1",
        "citing_paper_id": "P_CARRIER",
        "reference_index": 2,
        "marker_text": "2",
        "context_text": "A cited statement.",
        "citation_link_status": "link_rect",
        "scope_status": "candidate_sentence_scope",
        "parser_version": "claim_source_trace_v1",
        "source_sha256": "a" * 64,
        "support_role": "cited_background_candidate",
        "support_relation": "cites_context",
        "origin_review_status": "review_required",
        "claim_match_score": 80,
    }
    unresolved_trace = {
        "paper_id": "P_CARRIER",
        "mentions": [
            {
                **base_mention,
                "reference": {
                    "work_id": "citation:unresolved-hash",
                    "citation_text": "Source A. A paper. 2020.",
                    "bibliographic_resolution_status": "unresolved",
                },
            }
        ],
    }
    upsert_source_trace(unresolved_trace, db_path, claim_id="CLAIM_1")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE claim_sources
            SET source_role = 'primary_source',
                support_relation = 'supports',
                origin_review_status = 'human_confirmed',
                confidence = 1.0,
                reviewer = 'reviewer@example.org'
            WHERE claim_id = 'CLAIM_1' AND mention_id = 'MENTION_1'
            """
        )
        connection.execute(
            """
            INSERT INTO cited_works (
                work_id, doi, citation_text, bibliographic_resolution_status
            ) VALUES ('doi:10.1000/source', '10.1000/source', 'Source A.', 'exact_id')
            """
        )
        connection.executemany(
            """
            INSERT INTO scientific_origins (origin_id, origin_type, label)
            VALUES (?, 'study', ?)
            """,
            [("ORIGIN_1", "Reviewed study"), ("ORIGIN_2", "Candidate cohort")],
        )
        connection.executemany(
            """
            INSERT INTO work_origins (
                work_id, origin_id, relation_type, evidence_locator, review_status
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    "citation:unresolved-hash",
                    "ORIGIN_1",
                    "uses_data",
                    "reviewed old locator",
                    "human_confirmed",
                ),
                (
                    "doi:10.1000/source",
                    "ORIGIN_1",
                    "uses_data",
                    "unreviewed canonical locator",
                    "review_required",
                ),
                (
                    "citation:unresolved-hash",
                    "ORIGIN_2",
                    "reports_cohort",
                    "old-only locator",
                    "review_required",
                ),
            ],
        )
        connection.commit()

    enriched_trace = {
        "paper_id": "P_CARRIER",
        "mentions": [
            {
                **base_mention,
                "claim_match_score": 95,
                "reference": {
                    "work_id": "doi:10.1000/source",
                    "doi": "10.1000/source",
                    "citation_text": "Source A. A paper. 2020.",
                    "bibliographic_resolution_status": "exact_id",
                },
            }
        ],
    }
    upsert_source_trace(enriched_trace, db_path, claim_id="CLAIM_1")

    with sqlite3.connect(db_path) as connection:
        mention_work_id = connection.execute(
            "SELECT cited_work_id FROM citation_mentions WHERE mention_id = 'MENTION_1'"
        ).fetchone()[0]
        edges = connection.execute(
            """
            SELECT cited_work_id, source_role, support_relation,
                   origin_review_status, confidence, reviewer
            FROM claim_sources
            WHERE claim_id = 'CLAIM_1' AND mention_id = 'MENTION_1'
            """
        ).fetchall()
        work_ids = {
            row[0]
            for row in connection.execute("SELECT work_id FROM cited_works").fetchall()
        }
        origin_edges = connection.execute(
            """
            SELECT work_id, origin_id, relation_type, evidence_locator, review_status
            FROM work_origins
            ORDER BY origin_id
            """
        ).fetchall()

    assert mention_work_id == "doi:10.1000/source"
    assert edges == [
        (
            "doi:10.1000/source",
            "primary_source",
            "supports",
            "human_confirmed",
            1.0,
            "reviewer@example.org",
        )
    ]
    assert work_ids == {"citation:unresolved-hash", "doi:10.1000/source"}
    assert origin_edges == [
        (
            "doi:10.1000/source",
            "ORIGIN_1",
            "uses_data",
            "reviewed old locator",
            "human_confirmed",
        ),
        (
            "doi:10.1000/source",
            "ORIGIN_2",
            "reports_cohort",
            "old-only locator",
            "review_required",
        ),
    ]


def test_trace_persistence_does_not_downgrade_enriched_work_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "trace.sqlite"
    base_mention = {
        "mention_id": "MENTION_1",
        "citing_paper_id": "P_CARRIER",
        "context_text": "A cited statement.",
        "citation_link_status": "link_rect",
        "scope_status": "candidate_sentence_scope",
        "parser_version": "claim_source_trace_v1",
        "source_sha256": "a" * 64,
    }
    enriched_reference = {
        "work_id": "doi:10.1000/source",
        "doi": "10.1000/source",
        "pmid": "12345",
        "pmcid": "PMC12345",
        "verified_identifiers": {
            "doi": "10.1000/source",
            "pmid": "12345",
            "pmcid": "PMC12345",
        },
        "title": "Resolved title",
        "author": "Alpha A",
        "year": "2024",
        "journal": "Resolved Journal",
        "citation_text": "Alpha A. Resolved title. 2024.",
        "registry_paper_id": "P_SOURCE",
        "bibliographic_resolution_status": "exact_id",
        "metadata_source": "europe_pmc_exact_identifier",
        "oa_pdf_url": "https://example.org/source.pdf",
    }
    upsert_source_trace(
        {"paper_id": "P_CARRIER", "mentions": [{**base_mention, "reference": enriched_reference}]},
        db_path,
    )
    upsert_source_trace(
        {
            "paper_id": "P_CARRIER",
            "mentions": [
                {
                    **base_mention,
                    "mention_id": "MENTION_2",
                    "reference": {
                        "work_id": "doi:10.1000/source",
                        "doi": "10.9999/conflicting",
                        "verified_identifiers": {},
                        "title": "Conflicting candidate title",
                        "author": "Wrong Author",
                        "citation_text": "Wrong Author. Conflicting candidate title.",
                        "bibliographic_resolution_status": "conflict",
                        "metadata_source": "lower_trust_candidate",
                        "oa_pdf_url": "https://example.org/wrong.pdf",
                    },
                }
            ],
        },
        db_path,
    )

    with sqlite3.connect(db_path) as connection:
        stored = connection.execute(
            """
            SELECT doi, pmid, pmcid, title, author, year, journal, citation_text,
                   registry_paper_id, bibliographic_resolution_status,
                   metadata_source, oa_pdf_url
            FROM cited_works
            WHERE work_id = 'doi:10.1000/source'
            """
        ).fetchone()
        mention_statuses = connection.execute(
            """
            SELECT mention_id, bibliographic_resolution_status
            FROM citation_mentions
            ORDER BY mention_id
            """
        ).fetchall()

    assert stored == (
        "10.1000/source",
        "12345",
        "PMC12345",
        "Resolved title",
        "Alpha A",
        "2024",
        "Resolved Journal",
        "Alpha A. Resolved title. 2024.",
        "P_SOURCE",
        "exact_id",
        "europe_pmc_exact_identifier",
        "https://example.org/source.pdf",
    )
    assert mention_statuses == [("MENTION_1", "exact_id"), ("MENTION_2", "conflict")]


def test_trace_persistence_migrates_origins_from_citation_to_registry_identity(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "trace.sqlite"
    base_mention = {
        "mention_id": "MENTION_1",
        "citing_paper_id": "P_CARRIER",
        "context_text": "A cited statement.",
        "citation_link_status": "link_rect",
        "scope_status": "candidate_sentence_scope",
        "parser_version": "claim_source_trace_v1",
        "source_sha256": "a" * 64,
    }
    upsert_source_trace(
        {
            "paper_id": "P_CARRIER",
            "mentions": [
                {
                    **base_mention,
                    "reference": {
                        "work_id": "citation:unresolved-hash",
                        "citation_text": "Source A. A paper. 2020.",
                        "bibliographic_resolution_status": "unresolved",
                    },
                }
            ],
        },
        db_path,
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO scientific_origins (origin_id, origin_type, label)
            VALUES ('ORIGIN_1', 'study', 'Reviewed study')
            """
        )
        connection.execute(
            """
            INSERT INTO work_origins (
                work_id, origin_id, relation_type, evidence_locator, review_status
            ) VALUES (
                'citation:unresolved-hash', 'ORIGIN_1', 'uses_data',
                'reviewed locator', 'human_confirmed'
            )
            """
        )
        connection.commit()

    upsert_source_trace(
        {
            "paper_id": "P_CARRIER",
            "mentions": [
                {
                    **base_mention,
                    "reference": {
                        "work_id": "registry:P_SOURCE",
                        "registry_paper_id": "P_SOURCE",
                        "verified_identifiers": {},
                        "title": "Source A. A paper",
                        "citation_text": "Source A. A paper. 2020.",
                        "bibliographic_resolution_status": "exact_title",
                    },
                }
            ],
        },
        db_path,
    )

    with sqlite3.connect(db_path) as connection:
        origin_edges = connection.execute(
            """
            SELECT work_id, origin_id, relation_type, evidence_locator, review_status
            FROM work_origins
            """
        ).fetchall()

    assert origin_edges == [
        (
            "registry:P_SOURCE",
            "ORIGIN_1",
            "uses_data",
            "reviewed locator",
            "human_confirmed",
        )
    ]
