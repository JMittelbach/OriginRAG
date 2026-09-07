from __future__ import annotations

import html
import io
import json
import re
import tarfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


EUROPEPMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
NCBI_PMC_OA_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
NCBI_PMC_ARTICLE_URL = "https://pmc.ncbi.nlm.nih.gov/articles"
USER_AGENT = "OriginRAG/0.1 local literature discovery; contact: local-user"


@dataclass(frozen=True)
class EuropePMCHit:
    query: str
    raw: dict[str, Any]

    @property
    def title(self) -> str:
        return clean_text(self.raw.get("title"))

    @property
    def year(self) -> str:
        return str(self.raw.get("pubYear") or self.raw.get("pubDate") or "").strip()[:4]

    @property
    def author(self) -> str:
        author_string = clean_text(self.raw.get("authorString"))
        if not author_string:
            return ""
        return author_string.split(",", 1)[0].strip()

    @property
    def journal(self) -> str:
        return clean_text(self.raw.get("journalTitle"))

    @property
    def doi(self) -> str:
        return str(self.raw.get("doi") or "").strip()

    @property
    def pmid(self) -> str:
        source = str(self.raw.get("source") or "").strip().upper()
        ext_id = str(self.raw.get("id") or "").strip()
        return ext_id if source == "MED" else str(self.raw.get("pmid") or "").strip()

    @property
    def pmcid(self) -> str:
        value = str(self.raw.get("pmcid") or "").strip()
        if value:
            return value
        source = str(self.raw.get("source") or "").strip().upper()
        ext_id = str(self.raw.get("id") or "").strip()
        return ext_id if source == "PMC" else ""

    @property
    def is_open_access(self) -> bool:
        return str(self.raw.get("isOpenAccess") or "").strip().upper() == "Y"

    @property
    def first_publication_date(self) -> str:
        return str(self.raw.get("firstPublicationDate") or self.raw.get("pubDate") or "").strip()


def search(
    query: str,
    *,
    page_size: int = 25,
    sort: str = "",
    timeout_seconds: int = 45,
) -> list[EuropePMCHit]:
    """Search Europe PMC and return raw result hits.

    The caller is responsible for keeping queries broad and for downloading only open-access PDFs.
    """
    params = {
        "query": query,
        "format": "json",
        "resultType": "core",
        "pageSize": str(max(1, min(page_size, 1000))),
    }
    if sort:
        params["sort"] = sort
    url = f"{EUROPEPMC_SEARCH_URL}?{urllib.parse.urlencode(params)}"
    payload = get_json(url, timeout_seconds=timeout_seconds)
    results = payload.get("resultList", {}).get("result", [])
    return [EuropePMCHit(query=query, raw=item) for item in results if isinstance(item, dict)]


def search_one(
    *,
    doi: str = "",
    pmid: str = "",
    pmcid: str = "",
    title: str = "",
    timeout_seconds: int = 45,
) -> EuropePMCHit | None:
    query = build_identifier_query(doi=doi, pmid=pmid, pmcid=pmcid, title=title)
    if not query:
        return None
    hits = search(query, page_size=5, timeout_seconds=timeout_seconds)
    return hits[0] if hits else None


def build_identifier_query(*, doi: str = "", pmid: str = "", pmcid: str = "", title: str = "") -> str:
    doi = doi.strip()
    pmid = pmid.strip()
    pmcid = pmcid.strip()
    title = " ".join(title.split())
    if doi:
        return f'DOI:"{doi}"'
    if pmcid:
        normalized_pmcid = pmcid if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
        return f'EXT_ID:"{normalized_pmcid}" SRC:PMC'
    if pmid:
        return f'EXT_ID:"{pmid}" SRC:MED'
    if title:
        escaped_title = title.replace('"', "")
        return f'TITLE:"{escaped_title}"'
    return ""


def best_pdf_url(hit: EuropePMCHit) -> str:
    """Return the best open-access PDF URL for a hit, or an empty string."""
    candidates = pdf_url_candidates(hit)
    return candidates[0] if candidates else ""


def pdf_url_candidates(hit: EuropePMCHit) -> list[str]:
    """Return open-access PDF candidates, ordered from specific links to fallbacks."""
    if not hit.is_open_access and not hit.pmcid:
        return []

    candidates: list[str] = []
    for item in _full_text_urls(hit.raw):
        url = str(item.get("url") or "").strip()
        document_style = str(item.get("documentStyle") or "").strip().lower()
        availability = str(item.get("availabilityCode") or "").strip().upper()
        if not url:
            continue
        if document_style == "pdf" or url.lower().split("?", 1)[0].endswith(".pdf"):
            if not availability or availability in {"OA", "F", "FREE", "OPEN_ACCESS"}:
                candidates.append(url)

    if hit.pmcid:
        candidates.append(f"https://europepmc.org/articles/{hit.pmcid}?pdf=render")
        candidates.append(f"https://pmc.ncbi.nlm.nih.gov/articles/{hit.pmcid}/pdf/")
    return list(dict.fromkeys(candidates))


def download_pdf(url: str, output_path: Path, *, timeout_seconds: int = 120) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        content_type = str(response.headers.get("Content-Type") or "").lower()
        data = response.read()
    if not data.startswith(b"%PDF") and "pdf" not in content_type:
        raise RuntimeError(f"download did not look like a PDF: content-type={content_type or 'unknown'}")
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(output_path)


def download_pmc_oa_pdf(
    pmcid: str,
    output_path: Path,
    *,
    timeout_seconds: int = 120,
) -> str:
    """Download the primary PDF from an official NCBI PMC OA package.

    The package is read in memory and never extracted as a directory. This keeps
    archive paths and supplementary files from escaping into the bibliography.
    """

    normalized = pmcid.strip().upper()
    if not normalized.startswith("PMC"):
        normalized = f"PMC{normalized}"
    if not re.fullmatch(r"PMC\d+", normalized):
        raise ValueError(f"invalid PMCID: {pmcid}")

    package_url = _pmc_oa_package_url(normalized, timeout_seconds=timeout_seconds)
    if package_url:
        try:
            data = _primary_pdf_from_oa_package(package_url, timeout_seconds=timeout_seconds)
            _write_pdf(output_path, data)
            return package_url
        except Exception:



            pass

    article_url = f"{NCBI_PMC_ARTICLE_URL}/{normalized}/"
    article_html = get_bytes(article_url, timeout_seconds=timeout_seconds).decode(
        "utf-8", errors="replace"
    )
    pdf_url = _pmc_citation_pdf_url(article_html, article_url=article_url)
    if not pdf_url:
        raise RuntimeError(f"no official PMC PDF available for {normalized}")
    data = get_bytes(pdf_url, timeout_seconds=timeout_seconds)
    if not data.startswith(b"%PDF"):
        raise RuntimeError(f"official PMC PDF for {normalized} is invalid")
    _write_pdf(output_path, data)
    return pdf_url


def _pmc_oa_package_url(pmcid: str, *, timeout_seconds: int) -> str:
    metadata_url = f"{NCBI_PMC_OA_URL}?{urllib.parse.urlencode({'id': pmcid})}"
    try:
        metadata = get_bytes(metadata_url, timeout_seconds=timeout_seconds)
        root = ET.fromstring(metadata)
    except Exception:
        return ""
    links = [
        str(node.attrib.get("href") or "").strip()
        for node in root.findall(".//link")
        if str(node.attrib.get("format") or "").strip().lower() == "tgz"
    ]
    package_url = next((url for url in links if url), "")
    if package_url.startswith("ftp://ftp.ncbi.nlm.nih.gov/"):
        package_url = "https://ftp.ncbi.nlm.nih.gov/" + package_url.removeprefix(
            "ftp://ftp.ncbi.nlm.nih.gov/"
        )
    return package_url


def _primary_pdf_from_oa_package(package_url: str, *, timeout_seconds: int) -> bytes:
    package = get_bytes(package_url, timeout_seconds=timeout_seconds)
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as archive:
        pdf_members = [
            member
            for member in archive.getmembers()
            if member.isfile() and member.name.lower().endswith(".pdf")
        ]
        if not pdf_members:
            raise RuntimeError("NCBI OA package contains no PDF")
        primary = max(pdf_members, key=_primary_pdf_rank)
        handle = archive.extractfile(primary)
        data = handle.read() if handle is not None else b""
    if not data.startswith(b"%PDF"):
        raise RuntimeError("NCBI OA package PDF is invalid")
    return data


class _CitationPdfParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.pdf_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta" or self.pdf_url:
            return
        values = {key.lower(): (value or "") for key, value in attrs}
        if values.get("name", "").lower() == "citation_pdf_url":
            self.pdf_url = html.unescape(values.get("content", "").strip())


def _pmc_citation_pdf_url(document: str, *, article_url: str) -> str:
    parser = _CitationPdfParser()
    parser.feed(document)
    if not parser.pdf_url:
        return ""
    resolved = urllib.parse.urljoin(article_url, parser.pdf_url)
    parsed = urllib.parse.urlparse(resolved)
    article_path = urllib.parse.urlparse(article_url).path.rstrip("/") + "/pdf/"
    if (
        parsed.scheme != "https"
        or parsed.hostname != "pmc.ncbi.nlm.nih.gov"
        or not parsed.path.startswith(article_path)
        or not parsed.path.lower().endswith(".pdf")
    ):
        return ""
    return resolved


def _write_pdf(output_path: Path, data: bytes) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(output_path)


def get_json(url: str, *, timeout_seconds: int) -> dict[str, Any]:
    return json.loads(get_bytes(url, timeout_seconds=timeout_seconds).decode("utf-8"))


def get_bytes(url: str, *, timeout_seconds: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def _primary_pdf_rank(member: tarfile.TarInfo) -> tuple[int, int]:
    name = member.name.lower()
    supplementary = any(token in name for token in ("supp", "appendix", "additional"))
    return (0 if supplementary else 1, member.size)


def _full_text_urls(raw: dict[str, Any]) -> list[dict[str, Any]]:
    items = raw.get("fullTextUrlList", {}).get("fullTextUrl", [])
    return [item for item in items if isinstance(item, dict)]


def clean_text(value: Any) -> str:
    return " ".join(html.unescape(str(value or "")).split())
