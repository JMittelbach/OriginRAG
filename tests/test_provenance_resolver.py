from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pfempire.provenance.resolver as resolver_module
from pfempire.provenance.library import LibraryStatusResolver
from pfempire.provenance.resolver import (
    enrich_reference_online,
    resolve_reference_against_library,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_crossref_exact_doi_requires_returned_identifier_equality(monkeypatch) -> None:
    payload = {
        "message": {
            "DOI": "10.1000/EXACT",
            "title": ["Exact source"],
            "container-title": ["Journal"],
            "author": [{"family": "Alpha"}],
            "issued": {"date-parts": [[2024]]},
        }
    }
    monkeypatch.setattr(
        resolver_module.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(payload),
    )

    result = resolver_module._crossref_exact_doi(
        "10.1000/exact",
        timeout_seconds=3,
        mailto="researcher@example.org",
    )

    assert result == {
        "doi": "10.1000/exact",
        "title": "Exact source",
        "author": "Alpha",
        "year": "2024",
        "journal": "Journal",
        "landing_url": "https://doi.org/10.1000/exact",
    }


def test_exact_doi_enrichment_falls_back_from_europe_pmc_to_crossref(monkeypatch) -> None:
    monkeypatch.setattr(resolver_module, "search_one", lambda **kwargs: None)
    monkeypatch.setattr(
        resolver_module,
        "_crossref_exact_doi",
        lambda doi, **kwargs: {
            "doi": doi,
            "title": "Cross-domain source",
            "author": "Beta",
            "year": "2023",
            "journal": "Domain Journal",
        },
    )

    result = enrich_reference_online(
        {
            "doi": "10.1000/cross-domain",
            "bibliographic_resolution_status": "identifier_extracted_unverified",
        }
    )

    assert result["bibliographic_resolution_status"] == "exact_id"
    assert result["metadata_source"] == "crossref_exact_doi"
    assert result["work_id"] == "doi:10.1000/cross-domain"


def test_online_enrichment_fails_closed_when_metadata_services_are_unreachable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        resolver_module,
        "search_one",
        lambda **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    monkeypatch.setattr(
        resolver_module.urllib.request,
        "urlopen",
        lambda request, timeout: (_ for _ in ()).throw(OSError("offline")),
    )

    identifier_result = enrich_reference_online(
        {
            "doi": "10.1000/unverified",
            "bibliographic_resolution_status": "identifier_extracted_unverified",
        }
    )
    title_result = enrich_reference_online(
        {
            "citation_text": "Alpha A. A sufficiently descriptive source title. Journal. 2024.",
            "bibliographic_resolution_status": "unresolved",
        }
    )

    assert identifier_result["bibliographic_resolution_status"] == (
        "identifier_extracted_unverified"
    )
    assert title_result["bibliographic_resolution_status"] == "unresolved"


def test_exact_enrichment_requires_every_supplied_identifier_to_match(monkeypatch) -> None:
    monkeypatch.setattr(
        resolver_module,
        "search_one",
        lambda **kwargs: SimpleNamespace(
            doi="10.1000/source",
            pmid="999",
            pmcid="",
            title="Conflicting source",
            author="Alpha",
            year="2024",
            journal="Journal",
        ),
    )
    crossref_called = []
    monkeypatch.setattr(
        resolver_module,
        "_crossref_exact_doi",
        lambda *args, **kwargs: crossref_called.append(True),
    )

    result = enrich_reference_online(
        {
            "doi": "10.1000/source",
            "pmid": "123",
            "bibliographic_resolution_status": "identifier_extracted_unverified",
        }
    )

    assert result["bibliographic_resolution_status"] == "identifier_extracted_unverified"
    assert result["pmid"] == "123"
    assert crossref_called == []


def test_bibliographic_candidate_is_not_promoted_by_its_suggested_doi(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "paper_registry.tsv"
    registry.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tlocal_pdf_relpath\n"
        "P1\tCandidate source\t2024\t10.1000/candidate\t\t\tmissing.pdf\n",
        encoding="utf-8",
    )
    resolver = LibraryStatusResolver(
        registry_path=registry,
        bib_dir=tmp_path / "bib",
        chunks_dir=tmp_path / "chunks",
    )

    result = resolve_reference_against_library(
        {
            "doi": "10.1000/candidate",
            "title": "Candidate source",
            "year": "2024",
            "citation_text": "Alpha A. The printed reference being reviewed. 2024.",
            "bibliographic_resolution_status": "candidate_match",
            "metadata_source": "crossref_bibliographic_candidate",
        },
        resolver,
    )

    assert result["bibliographic_resolution_status"] == "candidate_match"
    assert result["verified_identifiers"] == {}
    assert result["library_status"]["state"] == "unresolved"
    assert result["candidate_library_status"]["state"] == "registry_only"
    assert result["candidate_registry_paper_id"] == "P1"
    assert str(result["work_id"]).startswith("citation:")


def test_candidate_oa_lookup_failure_does_not_abort_enrichment(monkeypatch) -> None:
    monkeypatch.setattr(
        resolver_module,
        "_crossref_bibliographic_candidate",
        lambda *args, **kwargs: {
            "doi": "10.1000/candidate",
            "title": "Candidate source",
            "author": "Alpha",
            "year": "2024",
        },
    )
    monkeypatch.setattr(
        resolver_module,
        "search_one",
        lambda **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )

    result = enrich_reference_online(
        {
            "citation_text": "Alpha A. A sufficiently descriptive source title. Journal. 2024.",
            "bibliographic_resolution_status": "unresolved",
        }
    )

    assert result["bibliographic_resolution_status"] == "candidate_match"
    assert result["doi"] == "10.1000/candidate"
    assert "oa_pdf_url" not in result
