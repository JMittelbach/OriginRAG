from __future__ import annotations

import csv
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "import_requested_papers.py"
SPEC = importlib.util.spec_from_file_location("import_requested_papers", SCRIPT_PATH)
assert SPEC and SPEC.loader
IMPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMPORTER)


def write_tsv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def test_registry_identifiers_require_a_pdf_in_the_current_bib_dir(tmp_path: Path) -> None:
    bib_dir = tmp_path / "current-bib"
    local_pdf = bib_dir / "papers" / "present.pdf"
    local_pdf.parent.mkdir(parents=True)
    local_pdf.write_bytes(b"%PDF-1.4\n")
    outside_pdf = tmp_path / "outside.pdf"
    outside_pdf.write_bytes(b"%PDF-1.4\n")

    registry_path = tmp_path / "paper_registry.tsv"
    write_tsv(
        registry_path,
        ["doi", "pmid", "pmcid", "local_pdf_relpath"],
        [
            {
                "doi": "10.1000/present",
                "pmid": "1001",
                "pmcid": "PMC1001",
                "local_pdf_relpath": "papers/present.pdf",
            },
            {
                "doi": "10.1000/other-mac",
                "pmid": "1002",
                "pmcid": "",
                "local_pdf_relpath": "papers/missing.pdf",
            },
            {
                "doi": "10.1000/traversal",
                "pmid": "",
                "pmcid": "",
                "local_pdf_relpath": "../outside.pdf",
            },
            {
                "doi": "10.1000/absolute",
                "pmid": "",
                "pmcid": "",
                "local_pdf_relpath": str(local_pdf),
            },
        ],
    )

    identifiers = IMPORTER.load_existing_identifiers(registry_path, bib_dir=bib_dir)

    assert identifiers == {
        "doi:10.1000/present": "papers/present.pdf",
        "pmid:1001": "papers/present.pdf",
        "pmcid:PMC1001": "papers/present.pdf",
    }


def test_inventory_titles_require_a_pdf_in_the_current_bib_dir(tmp_path: Path) -> None:
    bib_dir = tmp_path / "current-bib"
    local_pdf = bib_dir / "papers" / "present.pdf"
    local_pdf.parent.mkdir(parents=True)
    local_pdf.write_bytes(b"%PDF-1.4\n")
    outside_pdf = tmp_path / "outside.pdf"
    outside_pdf.write_bytes(b"%PDF-1.4\n")

    inventory_path = tmp_path / "local_pdf_inventory.tsv"
    fields = [
        "abs_path",
        "relpath_from_bib",
        "pdf_metadata_title",
        "guessed_title",
        "stem",
        "first_page_text_sample",
    ]
    write_tsv(
        inventory_path,
        fields,
        [
            {
                "abs_path": "/Volumes/old-mac/bib/papers/present.pdf",
                "relpath_from_bib": "papers/present.pdf",
                "pdf_metadata_title": "Present local paper",
            },
            {
                "abs_path": "/Volumes/old-mac/bib/papers/missing.pdf",
                "relpath_from_bib": "papers/missing.pdf",
                "pdf_metadata_title": "Only on the other Mac",
            },
            {
                "abs_path": str(outside_pdf),
                "relpath_from_bib": "../outside.pdf",
                "pdf_metadata_title": "Outside bibliography root",
            },
        ],
    )

    titles = IMPORTER.load_existing_titles(inventory_path, bib_dir=bib_dir)

    assert titles == [
        {
            "title": "Present local paper",
            "relpath": "papers/present.pdf",
            "doi": "",
        }
    ]


def test_title_dedupe_does_not_merge_different_exact_dois() -> None:
    duplicate = IMPORTER.best_existing_match(
        "Correction: The same scientific article title",
        [
            {
                "title": "The same scientific article title",
                "relpath": "papers/correction.pdf",
                "doi": "10.1000/correction",
            }
        ],
        requested_doi="10.1000/original",
    )

    assert duplicate is None


def test_stale_metadata_does_not_block_a_missing_paper_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bib_dir = tmp_path / "current-bib"
    bib_dir.mkdir()
    registry_path = tmp_path / "paper_registry.tsv"
    inventory_path = tmp_path / "local_pdf_inventory.tsv"
    write_tsv(
        registry_path,
        ["doi", "local_pdf_relpath"],
        [
            {
                "doi": "10.1000/requested",
                "local_pdf_relpath": "papers/only-on-other-mac.pdf",
            }
        ],
    )
    write_tsv(
        inventory_path,
        ["relpath_from_bib", "pdf_metadata_title"],
        [
            {
                "relpath_from_bib": "papers/only-on-other-mac.pdf",
                "pdf_metadata_title": "Requested source paper",
            }
        ],
    )
    monkeypatch.setattr(IMPORTER, "search_one", lambda **_: None)

    result = IMPORTER.process_request(
        {
            "doi": "10.1000/requested",
            "title": "Requested source paper",
            "pdf_url": "https://example.org/requested.pdf",
        },
        bib_dir=bib_dir,
        inventory_titles=IMPORTER.load_existing_titles(
            inventory_path,
            bib_dir=bib_dir,
        ),
        existing_identifiers=IMPORTER.load_existing_identifiers(
            registry_path,
            bib_dir=bib_dir,
        ),
        dry_run=True,
    )

    assert result["status"] == "dry_run"
    assert result["local_pdf_relpath"].endswith(".pdf")
    assert result["message"] == "https://example.org/requested.pdf"


def test_importer_rejects_metadata_hit_with_different_requested_identifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        IMPORTER,
        "search_one",
        lambda **_: SimpleNamespace(
            doi="10.1000/different",
            pmid="",
            pmcid="",
            title="Wrong paper",
            author="Wrong",
            year="2024",
            journal="Journal",
        ),
    )

    result = IMPORTER.process_request(
        {"doi": "10.1000/requested", "title": "Requested source paper"},
        bib_dir=tmp_path / "bib",
        inventory_titles=[],
        existing_identifiers={},
        dry_run=True,
    )

    assert result["status"] == "no_oa_pdf_found"
    assert result["doi"] == "10.1000/requested"


def test_request_metadata_is_preserved_when_external_lookup_has_no_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(IMPORTER, "search_one", lambda **_: None)

    result = IMPORTER.process_request(
        {
            "doi": "10.1000/classic",
            "author": "Foundational",
            "year": "1948",
            "title": "A foundational method",
            "journal": "Journal of Foundations",
            "pdf_url": "https://example.org/classic.pdf",
        },
        bib_dir=tmp_path / "bib",
        inventory_titles=[],
        existing_identifiers={},
        dry_run=True,
    )

    assert result["status"] == "dry_run"
    assert result["author"] == "Foundational"
    assert result["year"] == "1948"
    assert result["journal"] == "Journal of Foundations"


def test_direct_pdf_survives_transient_europe_pmc_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_lookup(**_kwargs: str) -> None:
        raise OSError("temporary 502")

    monkeypatch.setattr(IMPORTER, "search_one", fail_lookup)

    result = IMPORTER.process_request(
        {
            "doi": "10.1000/direct",
            "author": "Direct",
            "year": "2024",
            "title": "Direct full text",
            "journal": "Direct Journal",
            "pdf_url": "https://example.org/direct.pdf",
        },
        bib_dir=tmp_path / "bib",
        inventory_titles=[],
        existing_identifiers={},
        dry_run=True,
    )

    assert result["status"] == "dry_run"
    assert result["doi"] == "10.1000/direct"
    assert result["message"] == "https://example.org/direct.pdf"
