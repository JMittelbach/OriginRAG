from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiscoveryHit:
    """Normalized literature discovery record from an external source."""

    source: str
    query: str
    author: str = ""
    year: str = ""
    title: str = ""
    journal: str = ""
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""
    first_publication_date: str = ""
    is_open_access: bool = False
    pdf_url: str = ""
    landing_url: str = ""
    message: str = ""

