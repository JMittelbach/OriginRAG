import io
import json
import socket
import threading
import time

import fitz
import pytest

from originrag.discovery import oa_import as oa


def pdf_bytes(doi="10.1234/test"):
    with fitz.open() as document:
        page = document.new_page()
        page.insert_textbox(fitz.Rect(30, 30, 570, 800), f"A study of graph algorithms\nDOI: {doi}\n" + "Graph colouring and computational complexity. " * 80, fontsize=10)
        return document.tobytes()


@pytest.mark.parametrize("url", ["http://example.org/x", "file:///etc/passwd", "https://u:p@example.org/x", "https://example.org:8443/x", "https://example.org/x\nHeader:value"])
def test_download_requires_plain_https(url):
    with pytest.raises(ValueError):
        oa.public_target(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "fc00::1", "0.0.0.0"])
def test_private_or_mixed_dns_answers_are_blocked(monkeypatch, address):
    monkeypatch.setattr(oa.socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)), (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(ValueError):
        oa.public_target("https://papers.example/pdf")


def test_public_dns_is_pinned_not_looked_up_again(monkeypatch):
    monkeypatch.setattr(oa.socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    assert oa.public_target("https://papers.example/a.pdf?download=1") == ("papers.example", "8.8.8.8", "/a.pdf?download=1")
    calls = []
    raw = object()
    monkeypatch.setattr(oa.socket, "create_connection", lambda address, timeout: calls.append(address) or raw)
    class TLS:
        def wrap_socket(self, sock, *, server_hostname):
            assert sock is raw
            assert server_hostname == "papers.example"
            return raw
    connection = oa.PinnedHTTPSConnection("papers.example", "8.8.8.8", 1)
    connection._context = TLS()
    connection.connect()
    assert calls == [("8.8.8.8", 443)]


def test_redirects_are_revalidated_and_size_limits_apply(monkeypatch):
    requests = []
    monkeypatch.setattr(oa.socket, "getaddrinfo", lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1" if host == "private.example" else "8.8.8.8", 443))])
    class Response(io.BytesIO):
        status = 302
        def getheader(self, key):
            return "https://private.example/secret" if key == "Location" else None
    class Connection:
        sock = None
        def __init__(self, *args):
            pass
        def request(self, *a, **k):
            requests.append(a)
        def getresponse(self):
            return Response(b"x" * 20)
        def close(self):
            pass
    monkeypatch.setattr(oa, "PinnedHTTPSConnection", Connection)
    with pytest.raises(ValueError, match="Non-public"):
        oa.fetch_public("https://papers.example/a", max_bytes=10, deadline=time.monotonic()+5)
    assert len(requests) == 1
    Response.status = 200
    with pytest.raises(ValueError, match="size limit"):
        oa.fetch_public("https://papers.example/a", max_bytes=10, deadline=time.monotonic()+5)


def test_only_matching_oa_metadata_produces_candidates(monkeypatch):
    monkeypatch.delenv("ORIGINRAG_UNPAYWALL_EMAIL", raising=False)
    monkeypatch.delenv("ORIGINRAG_OPENALEX_API_KEY", raising=False)
    monkeypatch.setattr(oa, "metadata", lambda url, deadline: {"doi": "https://doi.org/10.1234/test", "locations": [{"is_oa": False, "pdf_url": "https://closed.example/x"}, {"is_oa": True, "pdf_url": "https://open.example/x"}]})
    assert list(oa.oa_candidates("10.1234/test", time.monotonic()+5)) == ["https://open.example/x"]
    assert list(oa.oa_candidates("10.1234/wrong", time.monotonic()+5)) == []


def test_pdf_signature_and_requested_identity_are_checked(monkeypatch):
    monkeypatch.setattr(oa, "oa_candidates", lambda *args: iter(["https://papers.example/x"]))
    for data in (b"<html>Paywall</html>", pdf_bytes("10.1234/wrong"), pdf_bytes("10.1234/test123")):
        monkeypatch.setattr(oa, "fetch_public", lambda *a, data=data, **k: data)
        with pytest.raises(oa.ManualUploadRequired):
            oa.download_open_pdf("10.1234/test", lambda state: None)
    expected = pdf_bytes()
    monkeypatch.setattr(oa, "fetch_public", lambda *a, **k: expected)
    assert oa.download_open_pdf("10.1234/test", lambda state: None) == expected


def test_job_survives_caller_and_is_owner_scoped(tmp_path):
    from web_chat import LibrarySyncManager
    started = threading.Event()
    release = threading.Event()
    def download(doi, progress):
        started.set()
        assert release.wait(3)
        return pdf_bytes(doi)
    def import_inbox(inbox, **kwargs):
        sidecar = next(inbox.glob("*.upload.json"))
        name = json.loads(sidecar.read_text())["original_filename"]
        for source in inbox.glob("*.pdf"):
            source.rename(tmp_path / source.name)
        return {"imported": 1, "results": [{"source_file": name, "status": "accepted"}]}
    library = LibrarySyncManager(tmp_path / "inbox", importer=import_inbox, auto_worker=False, refresh_callback=lambda: None, read_cache_refresh=lambda: None)
    jobs = oa.PaperImports(library, downloader=download)
    try:
        job = jobs.enqueue("alice", "10.1234/test")
        assert started.wait(3)
        assert jobs.enqueue("alice", "10.1234/test")["job_id"] == job["job_id"]
        assert jobs.snapshot("bob") == []
        with pytest.raises(oa.ImportBusy):
            jobs.enqueue("bob", "10.1234/test")
        release.set()
    finally:
        release.set()
        jobs.shutdown()
    assert jobs.snapshot("alice")[0]["state"] == "imported"
    assert "owner" not in jobs.snapshot("alice")[0]


def test_no_access_requires_upload_instead_of_success():
    def denied(*args):
        raise oa.ManualUploadRequired("Upload needed")
    jobs = oa.PaperImports(None, downloader=denied)
    jobs.enqueue("alice", "10.1234/test")
    jobs.executor.shutdown(wait=True)
    assert jobs.snapshot("alice")[0]["state"] == "failed"


def test_queue_is_bounded_and_rejects_arbitrary_urls():
    gate = threading.Event()
    def wait(*args):
        gate.wait(3)
        raise oa.ManualUploadRequired("Upload needed")
    jobs = oa.PaperImports(None, downloader=wait)
    try:
        with pytest.raises(ValueError):
            jobs.enqueue("alice", "https://localhost/secret")
        for i in range(8):
            jobs.enqueue("alice", f"10.1234/test{i}")
        with pytest.raises(oa.ImportBusy):
            jobs.enqueue("alice", "10.1234/test9")
    finally:
        gate.set()
        jobs.shutdown()
