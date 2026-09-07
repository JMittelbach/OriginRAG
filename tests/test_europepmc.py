from __future__ import annotations

import io
import tarfile
from pathlib import Path

from pfempire.discovery import europepmc


def _tarball(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_download_pmc_oa_pdf_selects_primary_pdf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    xml = b"""<OA><records><record id="PMC123"><link format="tgz"
        href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/x.tar.gz" />
        </record></records></OA>"""
    package = _tarball(
        {
            "article/supplement.pdf": b"%PDF-supplement" + b"x" * 100,
            "article/main.pdf": b"%PDF-primary",
        }
    )

    def fake_get_bytes(url: str, *, timeout_seconds: int) -> bytes:
        assert timeout_seconds == 12
        return xml if "oa.fcgi" in url else package

    monkeypatch.setattr(europepmc, "get_bytes", fake_get_bytes)
    target = tmp_path / "paper.pdf"

    package_url = europepmc.download_pmc_oa_pdf(
        "123",
        target,
        timeout_seconds=12,
    )

    assert package_url == "https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/x.tar.gz"
    assert target.read_bytes() == b"%PDF-primary"


def test_download_pmc_oa_pdf_falls_back_to_canonical_article_pdf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    xml = b"<OA><records><record id='PMC456' /></records></OA>"
    article = b"""<html><head><meta name="citation_pdf_url"
        content="https://pmc.ncbi.nlm.nih.gov/articles/PMC456/pdf/paper.pdf" />
        </head></html>"""
    requested: list[str] = []

    def fake_get_bytes(url: str, *, timeout_seconds: int) -> bytes:
        requested.append(url)
        assert timeout_seconds == 12
        if "oa.fcgi" in url:
            return xml
        if url.endswith("/PMC456/"):
            return article
        if url.endswith("/PMC456/pdf/paper.pdf"):
            return b"%PDF-canonical"
        raise AssertionError(url)

    monkeypatch.setattr(europepmc, "get_bytes", fake_get_bytes)
    target = tmp_path / "paper.pdf"

    pdf_url = europepmc.download_pmc_oa_pdf("PMC456", target, timeout_seconds=12)

    assert pdf_url == "https://pmc.ncbi.nlm.nih.gov/articles/PMC456/pdf/paper.pdf"
    assert target.read_bytes() == b"%PDF-canonical"
    assert len(requested) == 3


def test_pmc_citation_pdf_url_rejects_non_pmc_host() -> None:
    article_url = "https://pmc.ncbi.nlm.nih.gov/articles/PMC456/"
    document = '<meta name="citation_pdf_url" content="https://example.org/paper.pdf">'

    assert europepmc._pmc_citation_pdf_url(document, article_url=article_url) == ""
