from email.message import Message
import json
import threading

import pytest

from originrag.web_local_access import local_browser_request
from test_web_chat import http_request
import web_chat


def headers(**values):
    result = Message()
    for key, value in {"Host": "127.0.0.1:8765", **values}.items():
        result[key.replace("_", "-")] = value
    return result


@pytest.mark.parametrize("extra", [{}, {"Origin": "http://127.0.0.1:8765"}, {"Sec_Fetch_Site": "same-origin"}])
def test_local_browser_allowed(extra):
    assert local_browser_request(("127.0.0.1", 8765), ("127.0.0.1", 2222), headers(**extra))


@pytest.mark.parametrize("extra", [
    {"Host": "attacker.example:8765"}, {"Host": "127.0.0.1:1234"}, {"Host": "localhost.attacker.example:8765"},
    {"Host": "user@127.0.0.1:8765"}, {"Host": "127.0.0.1:8765/anything"},
    {"Origin": "https://attacker.example"}, {"Origin": "null"},
    {"Origin": "http://127.0.0.1:9999"}, {"Sec_Fetch_Site": "cross-site"}, {"Sec_Fetch_Site": "same-site"},
])
def test_foreign_websites_and_rebinding_rejected(extra):
    assert not local_browser_request(("127.0.0.1", 8765), ("127.0.0.1", 2222), headers(**extra))


def test_network_binding_peer_and_duplicate_host_rejected():
    assert not local_browser_request(("0.0.0.0", 8765), ("127.0.0.1", 2222), headers())
    assert not local_browser_request(("127.0.0.1", 8765), ("192.168.1.2", 2222), headers())
    duplicate = headers()
    duplicate["Host"] = "attacker.example"
    assert not local_browser_request(("127.0.0.1", 8765), ("127.0.0.1", 2222), duplicate)
    assert local_browser_request(("::1", 8765), ("::1", 2222), headers(Host="[::1]:8765"))


def test_local_import_token_and_complete_indexing_without_login(tmp_path, monkeypatch):
    from test_metadata_fulltext_upgrade import setup_library
    from test_oa_import import pdf_bytes
    from originrag.discovery.oa_import import PaperImports
    source, _ = setup_library(tmp_path, monkeypatch)
    source.unlink()
    library = web_chat.LibrarySyncManager(source.parent, auto_worker=False, refresh_callback=lambda: None, read_cache_refresh=lambda: None)
    downloads = []

    def download(doi, progress):
        downloads.append(doi)
        return pdf_bytes(doi)

    monkeypatch.setattr(web_chat, "PaperImports", lambda manager: PaperImports(manager, downloader=download))
    app = web_chat.WebChatApp(web_chat.WebChatSettings(backend="template"), tmp_path / "sessions")
    handler = web_chat.build_handler(app, library_manager=library)
    try:
        server = web_chat.create_server("127.0.0.1", 0, handler)
    except PermissionError:
        handler.turn_job_manager.shutdown()
        handler.paper_import_manager.shutdown()
        pytest.skip("Loopback unavailable")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        status, response_headers, raw, _ = http_request(port, "GET", "/api/status")
        data = json.loads(raw)
        assert status == 200 and "user" not in data
        assert data["library_access"]["enabled"] is True
        token = data["library_access"]["csrf_token"]
        assert len(token) >= 32
        for bad in ({"Host": f"attacker.example:{port}"}, {"Origin": "https://attacker.example"}, {"Sec-Fetch-Site": "cross-site"}):
            _, _, raw, _ = http_request(port, "GET", "/api/status", headers=bad)
            assert not json.loads(raw)["library_access"]["enabled"]
            assert not json.loads(raw)["library_access"]["csrf_token"]
        for endpoint in ("/api/paper-import", "/api/library-upload", "/api/library-sync"):
            for bad in ({}, {"X-OriginRAG-Local-Token": "wrong"},
                        {"X-OriginRAG-Local-Token": token, "Origin": "https://attacker.example"},
                        {"X-OriginRAG-Local-Token": token, "Host": f"attacker.example:{port}"}):
                assert http_request(port, "POST", endpoint, payload={"doi": "10.1234/test"}, headers=bad)[0] == 403
        assert not downloads
        status, _, raw, _ = http_request(port, "POST", "/api/paper-import", payload={"doi": "10.1234/test"},
                                       headers={"X-OriginRAG-Local-Token": token, "Origin": f"http://127.0.0.1:{port}"})
        assert status == 202, raw
        handler.paper_import_manager.shutdown()
        _, _, raw, _ = http_request(port, "GET", "/api/paper-imports")
        assert json.loads(raw)["jobs"][0]["state"] == "imported", raw
        assert (tmp_path / "data/chunks/EXISTING_1.jsonl").exists()
        upload_headers = {"X-OriginRAG-Local-Token": token, "Content-Type": "application/pdf",
                          "X-Paper-Filename": "local-copy.pdf", "X-Paper-DOI": "10.1234/test", "X-Paper-Defer-Sync": "1"}
        status, _, raw, _ = http_request(port, "POST", "/api/library-upload", body=pdf_bytes(), headers=upload_headers)
        assert status == 202, raw
        assert list(source.parent.glob("*.pdf"))
        assert http_request(port, "POST", "/api/library-sync", headers={"X-OriginRAG-Local-Token": token})[0] == 202
        report = library.run_pending_now()
        assert report["duplicates"] == 1
    finally:
        server.shutdown()
        server.server_close()
        handler.turn_job_manager.shutdown()
        handler.paper_import_manager.shutdown()
        thread.join(timeout=5)
