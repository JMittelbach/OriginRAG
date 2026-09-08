import time
from urllib.parse import urlsplit

import pytest

from originrag.discovery import oa_import as oa
from test_oa_import import pdf_bytes


DOI = "10.1234/test"
PMC_ROOT = "https://pmc-oa-opendata.s3.amazonaws.com/"


@pytest.fixture(autouse=True)
def no_optional_credentials(monkeypatch):
    monkeypatch.delenv("ORIGINRAG_UNPAYWALL_EMAIL", raising=False)
    monkeypatch.delenv("ORIGINRAG_OPENALEX_API_KEY", raising=False)


@pytest.mark.parametrize("known_oa,licence,expected", [
    (True, [], True),
    (False, [{"URL": "https://creativecommons.org/licenses/by/4.0/"}], True),
    (False, [{"URL": "http://creativecommons.org/publicdomain/zero/1.0/"}], True),
    (False, [], False),
    (False, [{"URL": "https://creativecommons.org.example/licenses/by/4.0/"}], False),
    (False, [{"URL": "https://creativecommons.org/licenses/by/4.0/", "start": {"timestamp": 9999999999999}}], False),
    (False, [{"URL": "https://creativecommons.org/licenses/by/4.0/", "start": {"timestamp": "NaN"}}], False),
])
def test_crossref_finds_pdf_without_openalex_pdf_url(monkeypatch, known_oa, licence, expected):
    def metadata(url, deadline):
        if "openalex.org" in url:
            return {"doi": DOI, "open_access": {"is_oa": known_oa}}
        if "crossref.org" in url:
            return {"message": {"DOI": DOI, "license": licence, "link": [
                {"content-type": "application/pdf", "content-version": "am", "URL": "https://publisher.example/accepted.pdf"},
                {"content-type": "text/xml", "URL": "https://publisher.example/article.xml"},
                {"content-type": "application/pdf", "content-version": "vor", "URL": "https://publisher.example/final.pdf"},
            ]}}
        return {}
    monkeypatch.setattr(oa, "metadata", metadata)
    assert list(oa.oa_candidates(DOI, time.monotonic() + 5)) == (
        ["https://publisher.example/final.pdf", "https://publisher.example/accepted.pdf"] if expected else [])
    assert list(oa.oa_candidates("10.1234/different", time.monotonic() + 5)) == []


def cloud_record(**changes):
    return {"pmcid": "PMC123", "version": 2, "doi": DOI, "is_pmc_openaccess": True,
            "is_retracted": False, "pdf_url": "s3://pmc-oa-opendata/PMC123.2/PMC123.2.pdf?md5=abc", **changes}


def cloud_listing(*, truncated="false", keys=("metadata/PMC123.1.json", "metadata/PMC123.2.json")):
    return (f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>{truncated}</IsTruncated>'
            + "".join(f"<Contents><Key>{key}</Key></Contents>" for key in keys) + "</ListBucketResult>").encode()


def test_pmc_cloud_uses_latest_exact_article_version(monkeypatch):
    calls = []
    def fetch(url, **kwargs):
        assert url.startswith(PMC_ROOT)
        assert "prefix=metadata%2FPMC123." in url and "max-keys=100" in url
        assert kwargs["max_bytes"] == 500_000
        return cloud_listing(keys=("metadata/PMC123.2.json", "metadata/PMC123.1.json", "metadata/PMC1234.99.json"))
    def metadata(url, deadline):
        calls.append(url)
        return cloud_record()
    monkeypatch.setattr(oa, "fetch_public", fetch)
    monkeypatch.setattr(oa, "metadata", metadata)
    assert list(oa.pmc_cloud_candidates("PMC123", DOI, time.monotonic() + 5)) == [PMC_ROOT + "PMC123.2/PMC123.2.pdf?md5=abc"]
    assert calls == [PMC_ROOT + "metadata/PMC123.2.json"]


@pytest.mark.parametrize("changes", [
    {"pmcid": "PMC999"}, {"version": 1}, {"doi": "10.1234/wrong"},
    {"is_pmc_openaccess": False}, {"is_pmc_openaccess": "true"},
    {"is_retracted": True}, {"is_retracted": None},
    {"pdf_url": "s3://other-bucket/PMC123.2/PMC123.2.pdf"},
    {"pdf_url": "s3://pmc-oa-opendata/PMC999.2/PMC999.2.pdf"},
    {"pdf_url": "s3://pmc-oa-opendata/PMC123.1/PMC123.1.pdf"},
    {"pdf_url": "https://127.0.0.1/private.pdf"}, {"pdf_url": ""},
])
def test_pmc_never_substitutes_wrong_or_older_article(monkeypatch, changes):
    monkeypatch.setattr(oa, "fetch_public", lambda *a, **k: cloud_listing())
    monkeypatch.setattr(oa, "metadata", lambda *a: cloud_record(**changes))
    assert list(oa.pmc_cloud_candidates("PMC123", DOI, time.monotonic() + 5)) == []


@pytest.mark.parametrize("listing", [cloud_listing(truncated="true"), cloud_listing(keys=()), b'<!DOCTYPE x [<!ENTITY a "x">]><x/>'])
def test_pmc_rejects_incomplete_or_entity_listing(monkeypatch, listing):
    monkeypatch.setattr(oa, "fetch_public", lambda *a, **k: listing)
    monkeypatch.setattr(oa, "metadata", lambda *a: pytest.fail("Must not fetch article metadata"))
    assert list(oa.pmc_cloud_candidates("PMC123", DOI, time.monotonic() + 5)) == []


def test_openalex_landing_page_resolves_pmc_before_blocked_publisher(monkeypatch):
    calls = []
    def metadata(url, deadline):
        return {"doi": DOI, "locations": [
            {"is_oa": True, "pdf_url": "https://publisher.example/article.pdf"},
            {"is_oa": True, "landing_page_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/"},
            {"is_oa": True, "landing_page_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/"},
        ]} if "openalex.org" in url else {}
    def cloud(pmcid, doi, deadline):
        calls.append((pmcid, doi))
        yield PMC_ROOT + "PMC123.2/PMC123.2.pdf"
    monkeypatch.setattr(oa, "metadata", metadata)
    monkeypatch.setattr(oa, "pmc_cloud_candidates", cloud)
    assert list(oa.oa_candidates(DOI, time.monotonic() + 5)) == [PMC_ROOT + "PMC123.2/PMC123.2.pdf", "https://publisher.example/article.pdf"]
    assert calls == [("PMC123", DOI)]


def test_failed_pmc_service_does_not_hide_crossref_alternative(monkeypatch):
    def metadata(url, deadline):
        if "openalex.org" in url:
            return {"doi": DOI, "open_access": {"is_oa": True}, "locations": [
                {"is_oa": True, "landing_page_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/"}]}
        if "crossref.org" in url:
            return {"message": {"DOI": DOI, "link": [{"content-type": "application/pdf", "URL": "https://publisher.example/article.pdf"}]}}
        return {}
    def cloud(*args):
        raise oa.PublicDownloadError(404, PMC_ROOT)
    monkeypatch.setattr(oa, "metadata", metadata)
    monkeypatch.setattr(oa, "pmc_cloud_candidates", cloud)
    assert list(oa.oa_candidates(DOI, time.monotonic() + 5)) == ["https://publisher.example/article.pdf"]


@pytest.mark.parametrize("status,reason", [(401, "browser_required"), (403, "browser_required"), (503, "source_unavailable"), (404, "source_unavailable")])
def test_download_failure_has_actionable_reason(monkeypatch, status, reason):
    url = "https://publisher.example/article.pdf"
    monkeypatch.setattr(oa, "oa_candidates", lambda *a: iter([url]))
    def fetch(*args, **kwargs):
        raise oa.PublicDownloadError(status, url)
    monkeypatch.setattr(oa, "fetch_public", fetch)
    with pytest.raises(oa.ManualUploadRequired) as error:
        oa.download_open_pdf(DOI, lambda s: None)
    assert error.value.reason == reason
    assert error.value.download_url == (url if reason == "browser_required" else "")


def test_failed_link_does_not_prevent_alternative_on_same_host(monkeypatch):
    urls = ["https://publisher.example/a.pdf", "https://publisher.example/b.pdf"]
    data = pdf_bytes()
    monkeypatch.setattr(oa, "oa_candidates", lambda *a: iter(urls))
    def fetch(url, **kwargs):
        if url == urls[0]:
            raise oa.PublicDownloadError(403, url)
        return data
    monkeypatch.setattr(oa, "fetch_public", fetch)
    assert oa.download_open_pdf(DOI, lambda s: None) == data


def test_download_attempt_budget_and_deduplication(monkeypatch):
    calls = []
    monkeypatch.setattr(oa, "oa_candidates", lambda *a: iter(["https://example.org/0.pdf"] * 5 + [f"https://example.org/{i}.pdf" for i in range(9)]))
    def fetch(url, **kwargs):
        calls.append(url)
        raise oa.PublicDownloadError(403, url)
    monkeypatch.setattr(oa, "fetch_public", fetch)
    with pytest.raises(oa.ManualUploadRequired):
        oa.download_open_pdf(DOI, lambda s: None)
    assert len(calls) == len(set(calls)) == 4


def test_manual_job_preserves_browser_link_without_importing():
    def download(*a):
        raise oa.ManualUploadRequired("Use browser", reason="browser_required", download_url="https://publisher.example/x.pdf")
    jobs = oa.PaperImports(None, downloader=download)
    try:
        jobs.enqueue("alice", DOI)
    finally:
        jobs.shutdown()
    job = jobs.snapshot("alice")[0]
    assert job["state"] == "failed" and job["reason"] == "browser_required"
    assert urlsplit(job["download_url"]).hostname == "publisher.example"
    assert jobs.snapshot("bob") == []


@pytest.mark.parametrize("article_doi,expected", [(DOI, True), ("10.1234/wrong", False), ("", False)])
def test_landing_page_identity_and_redirect_relative_pdf(monkeypatch, article_doi, expected):
    html = f'<meta name="citation_doi" content="{article_doi}"><meta name="citation_pdf_url" content="files/article.pdf?x=1&amp;y=2">'.encode()
    def fetch(url, **kwargs):
        assert kwargs["with_url"] is True
        assert kwargs["max_bytes"] == 2_000_000
        return html, "https://publisher.example/articles/123/"
    monkeypatch.setattr(oa, "fetch_public", fetch)
    assert list(oa.landing_pdf_candidates("https://doi.org/" + DOI, DOI, time.monotonic()+5)) == (
        ["https://publisher.example/articles/123/files/article.pdf?x=1&y=2"] if expected else [])


def test_landing_links_are_downloaded_automatically_and_pdf_is_verified(monkeypatch):
    data = pdf_bytes()
    calls = []
    def metadata(url, deadline):
        if "openalex.org" in url:
            return {"doi": DOI, "locations": [
                {"is_oa": False, "landing_page_url": "https://closed.example/article"},
                {"is_oa": True, "landing_page_url": "https://publisher.example/article"}]}
        return {}
    def fetch(url, **kwargs):
        calls.append(url)
        if kwargs.get("with_url"):
            return (f'<meta name="citation_doi" content="{DOI}"><meta name="citation_pdf_url" content="https://publisher.example/article.pdf">'.encode(), url)
        return data
    monkeypatch.setattr(oa, "metadata", metadata)
    monkeypatch.setattr(oa, "fetch_public", fetch)
    assert oa.download_open_pdf(DOI, lambda state: None) == data
    assert calls == ["https://publisher.example/article", "https://publisher.example/article.pdf"]


def test_landing_page_budget_and_duplicate_links(monkeypatch):
    calls = []
    monkeypatch.setattr(oa, "metadata", lambda url, deadline: {
        "doi": DOI, "locations": [{"is_oa": True, "landing_page_url": f"https://example.org/{i}"} for i in [0, 0, 1, 2, 3, 4]]
    } if "openalex.org" in url else {})
    def landing(url, doi, deadline):
        calls.append(url)
        return iter(())
    monkeypatch.setattr(oa, "landing_pdf_candidates", landing)
    assert list(oa.oa_candidates(DOI, time.monotonic()+5)) == []
    assert calls == [f"https://example.org/{i}" for i in range(3)]
