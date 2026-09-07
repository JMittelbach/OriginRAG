from __future__ import annotations

import csv
from pathlib import Path

from originrag.retrieval.metadata import load_paper_metadata


def test_metadata_correction_refreshes_placeholder_citations(tmp_path: Path) -> None:
    papers_path = tmp_path / "papers.tsv"
    with papers_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=["paper_id", "title", "year", "doi"],
        )
        writer.writeheader()
        writer.writerow({"paper_id": "P1", "title": "Incomplete title", "year": "2013", "doi": "10.1/example"})

    registry_path = tmp_path / "paper_registry.tsv"
    with registry_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=["paper_id", "citation_short", "citation_apa"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "paper_id": "P1",
                "citation_short": "Unknown et al., 2013",
                "citation_apa": "Unknown et al. (2013). Incomplete title.",
            }
        )

    corrections_path = tmp_path / "metadata_corrections.tsv"
    with corrections_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=["paper_id", "author", "title", "journal"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "paper_id": "P1",
                "author": "Bertin",
                "title": "Complete title",
                "journal": "Example Journal",
            }
        )

    record = load_paper_metadata(papers_path)["P1"]

    assert record["citation_short"] == "Bertin et al., 2013"
    assert record["citation_apa"] == "Bertin et al. (2013). Complete title. Example Journal. https://doi.org/10.1/example"
