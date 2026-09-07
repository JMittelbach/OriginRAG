from __future__ import annotations

import csv
import json
from pathlib import Path

from originrag.provenance.library import (
    LibraryStatusResolver,
    normalize_doi,
    normalize_pmcid,
    normalize_pmid,
    resolve_library_status,
)


REGISTRY_FIELDS = [
    "paper_id",
    "title",
    "year",
    "doi",
    "pmid",
    "pmcid",
    "local_pdf_relpath",
    "pdf_url",
    "fulltext_status",
]


def write_registry(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTRY_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def make_resolver(
    tmp_path: Path, rows: list[dict[str, str]]
) -> tuple[LibraryStatusResolver, Path, Path, Path]:
    registry_path = tmp_path / "data" / "metadata" / "paper_registry.tsv"
    bib_dir = tmp_path / "bib"
    chunks_dir = tmp_path / "data" / "chunks"
    bib_dir.mkdir(parents=True)
    chunks_dir.mkdir(parents=True)
    write_registry(registry_path, rows)
    return (
        LibraryStatusResolver(
            registry_path=registry_path,
            bib_dir=bib_dir,
            chunks_dir=chunks_dir,
        ),
        registry_path,
        bib_dir,
        chunks_dir,
    )


def test_identifier_normalization_handles_labels_and_canonical_urls() -> None:
    assert normalize_doi(" DOI: 10.1038/S41591-025-04126-3. ") == (
        "10.1038/s41591-025-04126-3"
    )
    assert normalize_doi("https://doi.org/10.1000/example%2Fpart") == "10.1000/example/part"
    assert normalize_doi("not a DOI") == ""
    assert normalize_doi("10.prefix/not-a-doi") == ""
    assert normalize_pmid("PMID: 12345678.") == "12345678"
    assert normalize_pmid("https://pubmed.ncbi.nlm.nih.gov/9876543/") == "9876543"
    assert normalize_pmcid("pmcid: 123456") == "PMC123456"
    assert normalize_pmcid("https://pmc.ncbi.nlm.nih.gov/articles/PMC7654321/") == (
        "PMC7654321"
    )


def test_corpus_state_requires_a_real_chunk_file_and_is_json_serializable(
    tmp_path: Path,
) -> None:
    resolver, _, bib_dir, chunks_dir = make_resolver(
        tmp_path,
        [
            {
                "paper_id": "P001",
                "title": "A source paper",
                "year": "2024",
                "doi": "10.1000/source",
                "local_pdf_relpath": "papers/source.pdf",
                "fulltext_status": "missing",
            }
        ],
    )
    pdf_path = bib_dir / "papers" / "source.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.4\n")
    (chunks_dir / "P001.jsonl").write_text('{"paper_id":"P001"}\n', encoding="utf-8")

    status = resolver.resolve({"doi": "https://doi.org/10.1000/SOURCE"})

    assert status["state"] == "corpus"
    assert status["display"] == "● In searchable corpus"
    assert status["registry"]["matched_by"] == ["doi"]
    assert status["local_pdf"]["exists"] is True
    assert status["local_chunk"]["exists"] is True
    assert status["can_queue"] is False
    json.dumps(status)


def test_library_state_checks_current_bib_dir_not_fulltext_metadata(tmp_path: Path) -> None:
    resolver, _, bib_dir, _ = make_resolver(
        tmp_path,
        [
            {
                "paper_id": "P002",
                "title": "Local source",
                "year": "2023",
                "pmid": "2222",
                "local_pdf_relpath": "papers/local.pdf",
                "fulltext_status": "chunked",
            },
            {
                "paper_id": "P003",
                "title": "Remote-only source",
                "year": "2022",
                "pmid": "3333",
                "local_pdf_relpath": "papers/other-machine.pdf",
                "fulltext_status": "chunked",
            },
        ],
    )
    pdf_path = bib_dir / "papers" / "local.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.4\n")

    local = resolver.resolve({"pmid": "PMID: 2222"})
    remote_only = resolver.resolve({"pmid": "3333"})

    assert local["state"] == "library"
    assert local["display"] == "✓ PDF in local library"
    assert remote_only["state"] == "registry_only"
    assert remote_only["display"] == "◐ Registered; PDF missing"
    assert remote_only["local_pdf"]["exists"] is False
    assert remote_only["can_queue"] is True


def test_curated_registry_pdf_url_reports_oa_available(tmp_path: Path) -> None:
    resolver, _, _, _ = make_resolver(
        tmp_path,
        [
            {
                "paper_id": "P004",
                "title": "Open source",
                "year": "2021",
                "pmcid": "PMC4444",
                "local_pdf_relpath": "papers/open.pdf",
                "pdf_url": "https://europepmc.org/articles/PMC4444?pdf=render",
            }
        ],
    )

    status = resolver.resolve({"pmcid": "4444"})

    assert status["state"] == "oa_available"
    assert status["display"] == "↓ Open-access copy available"
    assert status["oa"] == {
        "available": True,
        "url": "https://europepmc.org/articles/PMC4444?pdf=render",
    }
    assert status["can_queue"] is True


def test_missing_exact_identifier_is_not_in_library_and_can_be_queued(tmp_path: Path) -> None:
    resolver, registry_path, bib_dir, chunks_dir = make_resolver(tmp_path, [])

    status = resolve_library_status(
        {"doi": "10.1000/missing", "title": "Missing paper", "year": "2020"},
        registry_path=registry_path,
        bib_dir=bib_dir,
        chunks_dir=chunks_dir,
    )

    assert status["state"] == "not_in_library"
    assert status["display"] == "○ No registered local copy"
    assert status["registry"]["matched"] is False
    assert status["can_queue"] is True


def test_online_oa_pdf_url_is_reported_as_available(tmp_path: Path) -> None:
    resolver, _, _, _ = make_resolver(tmp_path, [])

    status = resolver.resolve(
        {
            "doi": "10.1000/open",
            "oa_pdf_url": "https://example.org/article.pdf",
        }
    )

    assert status["state"] == "oa_available"
    assert status["oa"] == {
        "available": True,
        "url": "https://example.org/article.pdf",
    }


def test_title_year_matching_is_unique_and_not_fuzzy(tmp_path: Path) -> None:
    resolver, _, _, _ = make_resolver(
        tmp_path,
        [
            {
                "paper_id": "P005",
                "title": "A Precise: Scientific Title",
                "year": "2019",
                "local_pdf_relpath": "papers/precise.pdf",
            }
        ],
    )

    exact = resolver.resolve(title="  a precise: SCIENTIFIC title  ", year=2019)
    punctuation_changed = resolver.resolve(title="A Precise Scientific Title", year=2019)

    assert exact["state"] == "registry_only"
    assert exact["registry"]["matched_by"] == ["title_year"]
    assert punctuation_changed["state"] == "not_in_library"
    assert punctuation_changed["registry"]["matched"] is False


def test_ambiguous_title_year_and_conflicting_identifiers_are_unresolved(
    tmp_path: Path,
) -> None:
    resolver, _, _, _ = make_resolver(
        tmp_path,
        [
            {
                "paper_id": "P006",
                "title": "Shared title",
                "year": "2018",
                "doi": "10.1000/one",
                "local_pdf_relpath": "papers/one.pdf",
            },
            {
                "paper_id": "P007",
                "title": "Shared title",
                "year": "2018",
                "doi": "10.1000/two",
                "local_pdf_relpath": "papers/two.pdf",
            },
        ],
    )

    ambiguous = resolver.resolve(title="shared title", year="2018")
    conflicting = resolver.resolve(paper_id="P006", doi="10.1000/two")

    assert ambiguous["state"] == "unresolved"
    assert ambiguous["display"] == "? Source unresolved"
    assert ambiguous["registry"]["candidate_paper_ids"] == ["P006", "P007"]
    assert ambiguous["can_queue"] is False
    assert conflicting["state"] == "unresolved"
    assert "different registry records" in conflicting["reason"]


def test_invalid_or_unsafe_local_paths_never_count_as_local_files(tmp_path: Path) -> None:
    resolver, _, _, _ = make_resolver(
        tmp_path,
        [
            {
                "paper_id": "../P008",
                "title": "Unsafe paths",
                "year": "2017",
                "doi": "10.1000/unsafe",
                "local_pdf_relpath": "../outside.pdf",
            }
        ],
    )
    (tmp_path / "outside.pdf").write_bytes(b"%PDF-1.4\n")

    status = resolver.resolve(doi="10.1000/unsafe")

    assert status["state"] == "registry_only"
    assert status["local_pdf"]["exists"] is False
    assert status["local_pdf"]["path_is_safe"] is False
    assert status["local_chunk"]["path_is_safe"] is False
