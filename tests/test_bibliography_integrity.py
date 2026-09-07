from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audit_corpus_metadata import audit, weak_author  # noqa: E402
from build_paper_registry import (  # noqa: E402
    append_note,
    append_token,
    apply_path_identity_resolutions,
    finalize_records,
    merge_local_imports,
    merge_existing_registry,
    merge_paper_ids,
    preserve_existing_registry_paths,
    registry_only_identity,
    drop_unidentified_missing_records,
    seed_local_import_paths,
    seed_paper_paths,
)
from discover_open_access_papers import (  # noqa: E402
    collection_for_category,
    is_excluded_publication_title,
    is_relevant_hit,
    score_discovery_row,
)
from enrich_paper_registry_dois import extract_doi as extract_registry_doi  # noqa: E402
from ingest_pmc_bioc import passages_to_pages  # noqa: E402
from import_local_papers import (  # noqa: E402
    canonicalize_doi_against_known,
    classify_category,
    extract_doi as extract_local_doi,
    normalize_doi as normalize_local_doi,
    normalize_title_identity,
    reliable_identity_title,
)
from organize_bib_library import category_directory, discover_pdfs, format_bibtex  # noqa: E402
from sync_registry_to_papers import (  # noqa: E402
    domain_for_category,
    next_paper_id,
    paper_row_from_registry,
    stable_landing_url,
    update_existing_papers,
)
from pfempire.discovery.types import DiscoveryHit  # noqa: E402


def test_unrecognised_local_paper_is_not_assigned_to_single_cell_or_malaria():
    from import_local_papers import collection_for_category as local_collection
    from build_paper_registry import collection_from_category

    assert classify_category("Orbital dynamics of distant planets") == "other"
    assert local_collection("other") == "general_literature"
    assert collection_from_category("other") == "general_literature"
    assert domain_for_category("other") == "general"


def test_publisher_subject_folder_survives_library_organisation():
    from build_paper_registry import collection_from_category

    assert category_directory({"category": "topic_ecology", "title": "Forest dynamics"}) == "papers/topics/ecology"
    assert category_directory({"category": "topic_../../Ecology", "title": ""}) == "papers/topics/ecology"
    assert category_directory({"category": "topic_..", "title": ""}) == "papers/other"
    assert collection_from_category("topic_ecology") == "general_literature"
    assert domain_for_category("topic_ecology") == "general"


def test_organizer_uses_configured_library_and_dry_run_creates_nothing(monkeypatch, tmp_path):
    import organize_bib_library as organizer

    library = tmp_path / "external-library"
    library.mkdir()
    (library / "one.pdf").write_bytes(b"sample PDF bytes")
    monkeypatch.setattr(organizer, "get_bib_dir", lambda: library)
    monkeypatch.setattr(sys, "argv", ["organize", "--categorize", "--dry-run",
                                     "--registry", str(tmp_path / "absent.tsv")])
    assert organizer.main() == 0
    assert sorted(p.name for p in library.iterdir()) == ["one.pdf"]
    assert (library / "one.pdf").read_bytes() == b"sample PDF bytes"


def test_importer_guards_against_reimport_from_configured_library(monkeypatch, tmp_path):
    import import_local_papers as importer
    import pytest

    library = tmp_path / "external-library"
    library.mkdir()
    monkeypatch.setattr(importer, "BIB_DIR", importer.BIB_DIR)
    monkeypatch.setattr(importer, "get_bib_dir", lambda: library)
    monkeypatch.setattr(sys, "argv", ["import-local", "--source-dir", str(library), "--apply"])
    with pytest.raises(SystemExit, match="Refusing --apply"):
        importer.main()


def test_doi_extraction_rejects_truncated_publisher_prefix() -> None:
    assert extract_registry_doi("doi:10.1128/mbio") == ""
    assert extract_registry_doi("doi:10.1128/mbio.01234-21") == "10.1128/mbio.01234-21"


def test_registry_append_helpers_tolerate_empty_tsv_cells() -> None:
    assert append_note(None, "verified") == "verified"
    assert append_note("existing", None) == "existing"
    assert append_token(None, "papers") == "papers"


def test_metadata_audit_accepts_group_author_with_digits() -> None:
    assert not weak_author("COvid-19 Multi-omics Blood ATlas (COMBAT) Consortium")
    assert weak_author("Unknown2024")


def test_sync_repairs_renamed_pdf_by_unique_doi() -> None:
    papers = [
        {
            "paper_id": "P1",
            "doi": "10.1/example1",
            "local_pdf_relpath": "papers/old.pdf",
            "notes": "",
        }
    ]
    registry = [
        {
            "paper_id": "",
            "doi": "10.1/example1",
            "local_pdf_relpath": "papers/new.pdf",
            "metadata_source": "",
        }
    ]

    assert update_existing_papers(papers, registry) == 1
    assert papers[0]["local_pdf_relpath"] == "papers/new.pdf"


def test_sync_classifies_evidence_synthesis_as_bioinformatics() -> None:
    assert domain_for_category("evidence_synthesis") == "bioinformatics"
    assert next_paper_id("evidence_synthesis", {}, set()) == "BIO_ML_0001"


def test_sync_assigns_stable_host_biology_ids_and_domain() -> None:
    assert domain_for_category("host_tissue_atlas") == "host_biology"
    assert next_paper_id("host_tissue_atlas", {}, set()) == "HOST_ATL_0001"
    assert next_paper_id("covid_host_response", {}, set()) == "HOST_COV_0001"


def test_sync_assigns_stable_malaria_host_ids_and_collections() -> None:
    assert domain_for_category("malaria_host_genetics") == "malaria"
    assert domain_for_category("malaria_host_omics") == "malaria"
    assert next_paper_id("malaria_host_genetics", {}, set()) == "MAL_HGEN_0001"
    assert next_paper_id("malaria_host_omics", {}, set()) == "MAL_HOMX_0001"
    assert collection_for_category("malaria_host_genetics") == "malaria"
    assert collection_for_category("malaria_host_omics") == "malaria"
    assert (
        category_directory({"category": "malaria_host_genetics", "title": "Host genetics"})
        == "papers/malaria/host_genetics"
    )
    assert (
        category_directory({"category": "malaria_host_omics", "title": "Host omics"})
        == "papers/malaria/host_omics"
    )


def test_broad_discovery_accepts_targeted_cross_disease_papers() -> None:
    hit = DiscoveryHit(
        source="europepmc",
        query='"single-cell" COVID-19',
        title="A single-cell transcriptomic atlas of COVID-19 host responses",
        journal="Nature Medicine",
        doi="10.1/example",
        is_open_access=True,
        pdf_url="https://example.org/paper.pdf",
    )

    assert is_relevant_hit(
        hit,
        hit.query,
        category="covid_host_response",
        collection="host_biology",
    )
    assert collection_for_category("host_tissue_atlas") == "host_biology"


def test_perturbation_discovery_rejects_mechanical_gears() -> None:
    hit = DiscoveryHit(
        source="openalex",
        query="GEARS",
        title="Dynamic modeling and vibration analysis of planetary gears",
        journal="Mechanical Systems and Signal Processing",
        doi="10.1/gears",
        is_open_access=True,
        pdf_url="https://example.org/gears.pdf",
    )

    assert not is_relevant_hit(
        hit,
        hit.query,
        category="sc_tools",
        collection="bioinformatics_tools",
    )


def test_scrna_discovery_rejects_non_biological_saturn_matches() -> None:
    hit = DiscoveryHit(
        source="openalex",
        query="SATURN",
        title="A Pair of Warm Saturn-mass Planets near the 2:1 Mean Motion Resonance",
        journal="The Astronomical Journal",
        doi="10.1/saturn",
        is_open_access=True,
        pdf_url="https://example.org/saturn.pdf",
        message="openalex query included single-cell foundation model OR SATURN",
    )

    assert not is_relevant_hit(
        hit,
        hit.query,
        category="scrna_annotation",
        collection="scrna_annotation",
    )


def test_malaria_discovery_does_not_treat_diagnostic_message_as_evidence() -> None:
    hit = DiscoveryHit(
        source="openalex",
        query="EPCR OR malaria",
        title="EPCR in wound healing: mechanisms and therapeutic potential",
        journal="Cells",
        doi="10.1/epcr",
        is_open_access=True,
        pdf_url="https://example.org/epcr.pdf",
        message="openalex query=EPCR OR malaria",
    )

    assert not is_relevant_hit(
        hit,
        hit.query,
        category="var_pfemp1",
        collection="malaria",
    )


def test_discovery_excludes_correction_for_publication() -> None:
    assert is_excluded_publication_title(
        "Correction for Smith et al., A molecular target in malaria parasites"
    )
    assert is_excluded_publication_title(
        "Expression of Concern for Smith et al., A malaria antigen study"
    )
    assert is_excluded_publication_title("Review 1: A submitted malaria manuscript")


def test_broad_discovery_quality_rewards_host_and_perturbation_scope() -> None:
    host_score, host_flags = score_discovery_row(
        {
            "title": "Single-cell gene expression atlas of the human liver",
            "journal": "Genome Biology",
            "sources": "europepmc",
            "collection": "host_biology",
            "category": "host_tissue_atlas",
            "query": '"single-cell" liver atlas',
            "matched_categories": "host_tissue_atlas",
            "doi": "10.1/host",
            "pdf_url": "https://example.org/host.pdf",
            "year": "2024",
        }
    )
    method_score, method_flags = score_discovery_row(
        {
            "title": "Perturb-seq maps single-cell CRISPR responses",
            "journal": "Nature Methods",
            "sources": "europepmc",
            "collection": "bioinformatics_tools",
            "category": "sc_tools",
            "query": "Perturb-seq",
            "matched_categories": "sc_tools",
            "doi": "10.1/method",
            "pdf_url": "https://example.org/method.pdf",
            "year": "2024",
        }
    )

    assert host_score >= 55 and "host_biology_signal" in host_flags
    assert method_score >= 55 and "perturbation_method_signal" in method_flags


def test_library_discovery_excludes_recoverable_duplicate_archive(tmp_path: Path) -> None:
    active = tmp_path / "papers" / "active.pdf"
    archived = tmp_path / "duplicate_archive" / "duplicate.pdf"
    active.parent.mkdir(parents=True)
    archived.parent.mkdir(parents=True)
    active.write_bytes(b"%PDF-active")
    archived.write_bytes(b"%PDF-duplicate")

    assert discover_pdfs(
        tmp_path,
        papers_dir=tmp_path / "papers",
        keys_dir=tmp_path / "keys",
    ) == [active]


def test_sync_keeps_stable_url_papers_without_doi_out_of_default_retrieval() -> None:
    registry_row = {
        "title": "A conference paper without a DOI",
        "year": "2024",
        "category": "tools_ml",
        "landing_url": "https://proceedings.example/paper",
        "local_pdf_relpath": "papers/conference.pdf",
    }

    assert stable_landing_url(registry_row)
    paper = paper_row_from_registry(registry_row, "BIO_ML_0001")
    assert paper["fulltext_status"] == "excluded_no_doi"
    assert "no_doi_excluded_from_default_retrieval" in paper["notes"]


def test_audit_flags_duplicate_doi_and_missing_active_registry_row(tmp_path: Path) -> None:
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers/a.pdf").write_bytes(b"pdf")
    registry = [
        {
            "paper_id": "P1",
            "doi": "10.1/x1",
            "author": "Smith",
            "year": "2020",
            "title": "A valid scientific paper title",
            "local_pdf_relpath": "papers/a.pdf",
        },
        {
            "paper_id": "P2",
            "doi": "10.1/x1",
            "author": "Jones",
            "year": "2020",
            "title": "Another valid scientific paper title",
            "local_pdf_relpath": "papers/missing.pdf",
        },
    ]
    papers = [{"paper_id": "P3", "doi": "10.1/y2", "local_pdf_relpath": "papers/a.pdf"}]

    issues = audit(registry, papers, bib_dir=tmp_path)
    issue_types = [row["issue"] for row in issues]
    assert issue_types.count("duplicate_doi") == 2
    assert "missing_pdf" in issue_types
    assert "paper_missing_from_registry" in issue_types


def test_audit_records_archived_unavailable_pdf_without_treating_it_as_active(
    tmp_path: Path,
) -> None:
    registry = [
        {
            "paper_id": "P1",
            "doi": "10.1/archive",
            "author": "Smith",
            "year": "2020",
            "title": "A valid archived scientific paper title",
            "local_pdf_relpath": "papers/unavailable.pdf",
        }
    ]
    papers = [
        {
            "paper_id": "P1",
            "doi": "10.1/archive",
            "fulltext_status": "archived",
            "local_pdf_relpath": "papers/unavailable.pdf",
        }
    ]

    issues = audit(registry, papers, bib_dir=tmp_path)

    assert [(row["severity"], row["issue"]) for row in issues] == [
        ("warning", "archived_pdf_unavailable")
    ]


def test_registry_merge_preserves_metadata_after_pdf_rename() -> None:
    records = {
        "papers/new.pdf": {
            "local_pdf_relpath": "papers/new.pdf",
            "doi": "10.1/example1",
            "author": "Administrator",
            "year": "2020",
            "title": "Broken PDF header title",
            "metadata_source": "inventory",
            "notes": "",
        }
    }
    existing = [
        {
            "local_pdf_relpath": "papers/old.pdf",
            "doi": "https://doi.org/10.1/example1",
            "author": "Smith",
            "year": "2021",
            "title": "Correct title from DOI metadata",
            "journal": "Journal",
            "metadata_source": "crossref_doi_refresh",
            "notes": "verified",
        }
    ]

    merge_existing_registry(records, existing)

    assert records["papers/new.pdf"]["author"] == "Smith"
    assert records["papers/new.pdf"]["title"] == "Correct title from DOI metadata"
    assert "crossref_doi_refresh" in records["papers/new.pdf"]["metadata_source"]


def test_registry_merge_tolerates_null_optional_registry_cells() -> None:
    records = {
        "papers/example.pdf": {
            "local_pdf_relpath": "papers/example.pdf",
            "doi": "10.1/example",
            "metadata_source": "inventory",
            "notes": "",
        }
    }
    existing = [
        {
            "local_pdf_relpath": "papers/example.pdf",
            "doi": "10.1/example",
            "metadata_source": None,
            "notes": None,
        }
    ]

    merge_existing_registry(records, existing)

    assert records["papers/example.pdf"]["metadata_source"] == "inventory;existing_registry"
    assert records["papers/example.pdf"]["notes"] == ""


def test_registry_preserves_papers_missing_from_partial_local_mirror() -> None:
    records = {"papers/local.pdf": {"local_pdf_relpath": "papers/local.pdf"}}
    existing = [
        {
            "local_pdf_relpath": "papers/other_mac.pdf",
            "doi": "10.1/other-mac",
            "title": "Paper available on the complete library mirror",
        }
    ]

    preserve_existing_registry_paths(records, existing)

    assert set(records) == {"papers/local.pdf", "papers/other_mac.pdf"}
    assert records["papers/other_mac.pdf"]["doi"] == "10.1/other-mac"


def test_registry_preserves_identified_bibliography_only_row_without_fake_path() -> None:
    records: dict[str, dict[str, str]] = {}
    existing = [
        {
            "paper_id": "MAL_RES_9999",
            "doi": "10.1001/example",
            "author": "Henrici",
            "year": "2025",
            "title": "A valid bibliography-only scientific paper title",
            "collection": "malaria",
            "category": "resistance_dx",
            "local_pdf_relpath": "",
            "metadata_source": "manual_metadata_only",
        }
    ]

    preserve_existing_registry_paths(records, existing)
    drop_unidentified_missing_records(records, inventory_paths=set())
    finalize_records(records)

    key = "__registry_only__:paper_id:MAL_RES_9999"
    assert registry_only_identity(existing[0]) == "paper_id:MAL_RES_9999"
    assert records[key]["local_pdf_relpath"] == ""
    assert records[key]["doi"] == "10.1001/example"


def test_bibtex_export_omits_file_for_bibliography_only_record() -> None:
    output = format_bibtex(
        [
            {
                "bib_key": "Henrici2025ArtemisininPartial",
                "title": "Artemisinin Partial Resistance in Ugandan Children",
                "author": "Henrici",
                "year": "2025",
                "journal": "JAMA",
                "doi": "10.1001/jama.2024.22343",
                "pmid": "39540797",
                "pmcid": "PMC11565293",
                "category": "resistance_dx",
                "local_pdf_relpath": "",
            }
        ]
    )

    assert "doi = {10.1001/jama.2024.22343}" in output
    assert "pmid = {39540797}" in output
    assert "pmcid = {PMC11565293}" in output
    assert "\n  file = " not in output


def test_registry_drops_unidentified_inventory_ghost_from_missing_path() -> None:
    records = {
        "papers/local.pdf": {
            "local_pdf_relpath": "papers/local.pdf",
            "metadata_source": "inventory",
        },
        "papers/phantom_duplicate.pdf": {
            "local_pdf_relpath": "papers/phantom_duplicate.pdf",
            "title": "Duplicate title",
            "metadata_source": "inventory",
        },
        "papers/identified_other_mac.pdf": {
            "local_pdf_relpath": "papers/identified_other_mac.pdf",
            "doi": "10.1/other-mac",
            "metadata_source": "inventory",
        },
        "papers/curated_other_mac.pdf": {
            "local_pdf_relpath": "papers/curated_other_mac.pdf",
            "title": "Curated local record",
            "metadata_source": "local_paper_import",
        },
    }

    drop_unidentified_missing_records(
        records,
        inventory_paths={"papers/local.pdf"},
    )

    assert set(records) == {
        "papers/local.pdf",
        "papers/identified_other_mac.pdf",
        "papers/curated_other_mac.pdf",
    }


def test_registry_seeds_curated_import_missing_from_partial_inventory() -> None:
    records = {"papers/local.pdf": {"local_pdf_relpath": "papers/local.pdf"}}

    seed_local_import_paths(
        records,
        [{"local_pdf_relpath": "papers/new_curated_import.pdf"}],
    )

    assert set(records) == {
        "papers/local.pdf",
        "papers/new_curated_import.pdf",
    }
    assert (
        records["papers/new_curated_import.pdf"]["local_pdf_relpath"]
        == "papers/new_curated_import.pdf"
    )


def test_failed_import_report_does_not_seed_missing_pdf_path() -> None:
    records: dict[str, dict[str, str]] = {}

    seed_local_import_paths(
        records,
        [
            {
                "status": "download_failed",
                "local_pdf_relpath": "papers/missing.pdf",
            }
        ],
    )

    assert records == {}


def test_downloaded_exact_identifier_repairs_known_registry_path() -> None:
    records = {
        "papers/new.pdf": {
            "local_pdf_relpath": "papers/new.pdf",
            "title": "Weak inventory title",
            "doi": "",
            "metadata_source": "inventory",
            "notes": "",
        }
    }

    apply_path_identity_resolutions(
        records,
        [
            {
                "status": "downloaded",
                "local_pdf_relpath": "papers/new.pdf",
                "title": "Verified paper title from exact DOI lookup",
                "doi": "10.1000/example",
            }
        ],
    )

    assert records["papers/new.pdf"]["doi"] == "10.1000/example"
    assert records["papers/new.pdf"]["title"] == "Verified paper title from exact DOI lookup"


def test_failed_download_metadata_applies_after_pdf_arrives_from_fallback() -> None:
    records = {
        "papers/new.pdf": {
            "local_pdf_relpath": "papers/new.pdf",
            "title": "Weak inventory title",
            "doi": "",
            "metadata_source": "inventory",
            "notes": "",
        }
    }

    apply_path_identity_resolutions(
        records,
        [
            {
                "status": "download_failed",
                "local_pdf_relpath": "papers/new.pdf",
                "title": "Verified title after fallback download",
                "doi": "10.1000/fallback",
            }
        ],
        existing_local_paths={"papers/new.pdf"},
    )

    assert records["papers/new.pdf"]["doi"] == "10.1000/fallback"


def test_bibtex_export_keeps_stable_url_record_without_doi() -> None:
    output = format_bibtex(
        [
            {
                "bib_key": "Liu2023IdentifiabilityLabel",
                "title": "Identifiability of Label Noise Transition Matrix",
                "author": "Liu",
                "year": "2023",
                "journal": "Proceedings of Machine Learning Research",
                "landing_url": "https://proceedings.mlr.press/v202/liu23g.html",
                "local_pdf_relpath": (
                    "papers/foundation_models/Liu2023IdentifiabilityLabelNoise.pdf"
                ),
                "category": "foundation_models",
            }
        ]
    )

    assert output.startswith("@inproceedings{Liu2023IdentifiabilityLabel,")
    assert "booktitle = {Proceedings of Machine Learning Research}" in output
    assert "url = {https://proceedings.mlr.press/v202/liu23g.html}" in output
    assert "\n  doi = " not in output


def test_registry_preserves_curated_row_over_local_pdf_guesses() -> None:
    records = {
        "papers/local.pdf": {
            "local_pdf_relpath": "papers/local.pdf",
            "author": "Given name",
            "title": "Weak PDF metadata title",
        }
    }
    existing = [
        {
            "local_pdf_relpath": "papers/local.pdf",
            "author": "Family",
            "title": "Curated canonical title",
        }
    ]

    preserve_existing_registry_paths(records, existing)

    assert records["papers/local.pdf"] == existing[0]


def test_verified_local_import_overrides_filename_guesses() -> None:
    records = {
        "papers/local.pdf": {
            "local_pdf_relpath": "papers/local.pdf",
            "author": "",
            "year": "2024",
            "title": "Smith2024WeakFilenameGuess",
            "journal": "",
            "doi": "",
            "metadata_source": "inventory",
            "notes": "",
        }
    }
    imports = [
        {
            "local_pdf_relpath": "papers/local.pdf",
            "author": "Smith",
            "year": "2023",
            "title": "Curated title from the verified full text",
            "journal": "Journal",
            "doi": "10.1/example",
            "landing_url": "https://doi.org/10.1/example",
            "collection": "bioinformatics_tools",
            "category": "tools_ml",
            "notes": "metadata_verified_2026-08-03 via local full text",
        }
    ]

    merge_local_imports(records, imports)

    assert records["papers/local.pdf"]["author"] == "Smith"
    assert records["papers/local.pdf"]["year"] == "2023"
    assert records["papers/local.pdf"]["title"] == ("Curated title from the verified full text")
    assert records["papers/local.pdf"]["journal"] == "Journal"
    assert records["papers/local.pdf"]["doi"] == "10.1/example"


def test_registry_accepts_stable_paper_id_after_curated_row_restore() -> None:
    records = {
        "papers/local.pdf": {
            "local_pdf_relpath": "papers/local.pdf",
            "paper_id": "",
        }
    }

    merge_paper_ids(
        records,
        [
            {
                "local_pdf_relpath": "papers/local.pdf",
                "paper_id": "MAL_OMX_0001",
            }
        ],
    )

    assert records["papers/local.pdf"]["paper_id"] == "MAL_OMX_0001"


def test_partial_mirror_seeds_active_papers_but_ignores_duplicate_rows() -> None:
    records: dict[str, dict[str, str]] = {}
    rows = [
        {
            "paper_id": "P_ACTIVE",
            "local_pdf_relpath": "papers/active.pdf",
            "fulltext_status": "chunked",
        },
        {
            "paper_id": "P_DUPLICATE",
            "local_pdf_relpath": "papers/duplicate.pdf",
            "fulltext_status": "duplicate",
        },
    ]

    seed_paper_paths(records, rows)

    assert "papers/active.pdf" in records
    assert "papers/duplicate.pdf" not in records


def test_duplicate_paper_row_cannot_overwrite_canonical_paper_id() -> None:
    records = {"papers/study.pdf": {"paper_id": ""}}
    rows = [
        {
            "paper_id": "P_CANONICAL",
            "local_pdf_relpath": "papers/study.pdf",
            "fulltext_status": "chunked",
        },
        {
            "paper_id": "P_DUPLICATE",
            "local_pdf_relpath": "papers/study.pdf",
            "fulltext_status": "duplicate",
        },
    ]

    merge_paper_ids(records, rows)

    assert records["papers/study.pdf"]["paper_id"] == "P_CANONICAL"


def test_registry_does_not_reparse_existing_curated_author() -> None:
    records = {
        "papers/local.pdf": {
            "local_pdf_relpath": "papers/local.pdf",
            "author": "Given Family",
            "title": "A sufficiently descriptive title",
            "year": "2024",
            "collection": "malaria",
            "category": "malaria_omics",
        }
    }

    finalize_records(records, preserve_author_paths={"papers/local.pdf"})

    assert records["papers/local.pdf"]["author"] == "Given Family"


def test_verified_local_import_uses_curated_family_name() -> None:
    records = {
        "papers/new.pdf": {
            "local_pdf_relpath": "papers/new.pdf",
            "author": "Given Family",
            "metadata_source": "inventory",
            "notes": "",
        }
    }
    local_imports = [
        {
            "local_pdf_relpath": "papers/new.pdf",
            "author": "Family",
            "notes": "metadata_verified_from_pdf",
        }
    ]

    merge_local_imports(records, local_imports)

    assert records["papers/new.pdf"]["author"] == "Family"


def test_explicit_legacy_path_resolution_can_correct_registry_only_row() -> None:
    records = {
        "papers/legacy.pdf": {
            "local_pdf_relpath": "papers/legacy.pdf",
            "author": "Unknown",
            "year": "",
            "title": "legacy.dvi",
            "doi": "",
            "metadata_source": "existing_registry",
            "notes": "",
        }
    }
    imports = [
        {
            "local_pdf_relpath": "papers/legacy.pdf",
            "author": "Hooker",
            "year": "2007",
            "title": "Resolved title",
            "doi": "10.1/resolved",
            "notes": "legacy_identity_resolved_20260804 from local full text",
        }
    ]

    apply_path_identity_resolutions(records, imports)

    assert records["papers/legacy.pdf"]["author"] == "Hooker"
    assert records["papers/legacy.pdf"]["year"] == "2007"
    assert records["papers/legacy.pdf"]["doi"] == "10.1/resolved"


def test_bioc_passages_are_grouped_with_section_context() -> None:
    payload = [
        {
            "documents": [
                {
                    "passages": [
                        {"infons": {"section_type": "TITLE"}, "text": "Paper title"},
                        {
                            "infons": {"section_type": "RESULTS"},
                            "text": "Direct experimental evidence.",
                        },
                    ]
                }
            ]
        }
    ]

    pages = passages_to_pages(payload, max_chars=1000)

    assert pages == [{"page": 1, "text": "Paper title\n\nResults\nDirect experimental evidence."}]


def test_bioc_abstract_gets_detectable_section_heading() -> None:
    payload = [
        {
            "documents": [
                {
                    "passages": [
                        {
                            "infons": {"section_type": "ABSTRACT"},
                            "text": "A sufficiently informative abstract.",
                        },
                    ]
                }
            ]
        }
    ]

    pages = passages_to_pages(payload, max_chars=1000)

    assert pages[0]["text"] == "Abstract\nA sufficiently informative abstract."


def test_local_import_classification_separates_requested_library_topics() -> None:
    assert (
        classify_category("scBERT: a pretrained language model for cell annotation")
        == "foundation_models"
    )
    assert (
        classify_category("Benchmarking methods for single-cell data integration")
        == "sc_harmonization"
    )
    assert (
        classify_category("Automated cell type annotation for single-cell RNA-seq")
        == "scrna_annotation"
    )
    assert classify_category("PfEMP1 var gene switching in severe malaria") == "var_pfemp1"
    assert classify_category("Antibody immunity after Plasmodium infection") == "host_immunity"
    assert (
        classify_category("Controlled human malaria infection", "Bachmann_2019.pdf")
        == "general_malaria"
    )


def test_local_import_doi_extraction_rejects_truncated_prefix() -> None:
    assert extract_local_doi("doi: 10.1128/cmr") == ""
    assert extract_local_doi("doi: 10.1128/CMR.00104-19") == "10.1128/cmr.00104-19"


def test_local_import_normalizes_pdf_link_artifacts_and_known_elife_alias() -> None:
    assert (
        normalize_local_doi("10.1073/pnas.1913525117/-/DCSupplemental") == "10.1073/pnas.1913525117"
    )
    assert (
        canonicalize_doi_against_known(
            "10.7554/eLife.33105.001",
            {"10.7554/elife.33105"},
        )
        == "10.7554/elife.33105"
    )


def test_local_import_title_identity_ignores_case_punctuation_and_diacritics() -> None:
    assert normalize_title_identity("CeLLTra: aligning cell names") == normalize_title_identity(
        "celltra — Aligning Cell Names"
    )
    assert reliable_identity_title("CeLLTra: aligning cell names with gene expression")
    assert not reliable_identity_title("Science Journals — AAAS")
    assert not reliable_identity_title("University of Dundee")


def test_category_directory_uses_stable_nested_taxonomy() -> None:
    assert (
        category_directory({"category": "scrna_annotation", "title": "Annotation"})
        == "papers/single_cell/annotation"
    )
    assert (
        category_directory({"category": "var_pfemp1", "title": "PfEMP1"})
        == "papers/malaria/var_genes"
    )
    assert (
        category_directory({"category": "host_immunity", "title": "Immune response"})
        == "papers/immunology"
    )
    assert (
        category_directory({"category": "host_tissue_atlas", "title": "Liver atlas"})
        == "papers/host_biology/tissue_atlases"
    )
    assert (
        category_directory({"category": "general_methods", "title": "Perturbation method"})
        == "papers/methods/general"
    )
