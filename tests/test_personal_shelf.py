from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from originrag.personal_shelf import PersonalShelf


CATALOGUE = [{"paper_id": "P1", "title": "Numerical methods", "author": "Example",
              "doi_url": "https://doi.org/10.1234/example", "searchable": True,
              "local_pdf_relpath": "private/path.pdf"}]


def test_persistence_isolation_and_removal(tmp_path):
    alice = PersonalShelf(tmp_path / "alice")
    alice.change({"action": "add", "paper_id": "P1"}, CATALOGUE)
    first = PersonalShelf(tmp_path / "alice").snapshot(CATALOGUE)
    assert first["items"][0]["summary_available"]
    assert "local_pdf_relpath" not in first["items"][0]
    assert alice.path.stat().st_mode & 0o777 == 0o600
    assert PersonalShelf(tmp_path / "bob").snapshot(CATALOGUE)["items"] == []
    assert alice.change({"action": "add", "paper_id": "P1"}, CATALOGUE) == first
    missing = alice.snapshot([])["items"][0]
    assert missing["title"] == "Numerical methods"
    assert not missing["available"] and not missing["summary_available"]
    assert alice.change({"action": "remove", "paper_id": "P1"}, [])["items"] == []
    assert alice.change({"action": "remove", "paper_id": "P1"}, [])["items"] == []
    assert CATALOGUE[0]["local_pdf_relpath"] == "private/path.pdf"


@pytest.mark.parametrize("payload", [None, [], {}, {"action": [], "paper_id": "P1"},
    {"action": "add", "paper_id": []}, {"action": "add", "paper_id": "../alice"},
    {"action": "add", "paper_id": "P1", "user_id": "bob"},
    {"action": "add", "paper_id": "P1", "metadata": {"title": "forged"}},
    {"action": "remove", "paper_id": ""}, {"action": "delete", "paper_id": "P1"}])
def test_rejects_invalid_and_untrusted_payloads(tmp_path, payload):
    with pytest.raises(ValueError):
        PersonalShelf(tmp_path).change(payload, CATALOGUE)


def test_concurrent_capacity_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr("originrag.personal_shelf.MAX_BOOKS", 3)
    catalogue = [{"paper_id": str(i), "title": str(i)} for i in range(12)]
    shelf = PersonalShelf(tmp_path)
    shelf.snapshot(catalogue)
    def add(i):
        try:
            PersonalShelf(tmp_path).change({"action": "add", "paper_id": str(i)}, catalogue)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(add, range(12))) == 3
    assert len(shelf.snapshot(catalogue)["items"]) == 3


@pytest.mark.parametrize("authenticated", [False, True])
def test_http_owner_boundaries_and_csrf(tmp_path, monkeypatch, authenticated):
    import test_web_chat as helpers
    web = helpers.web_chat
    monkeypatch.setattr(web, "library_catalog", lambda: CATALOGUE)
    auth = helpers.AuthStore(tmp_path / "auth.sqlite") if authenticated else None
    if auth:
        auth.create_user("alice", "quiet-lantern-river-orbit-47")
        auth.create_user("bob", "violet-planet-canvas-forest-83")
    settings = web.WebChatSettings(backend="template")
    registry = web.AuthenticatedWebChatRegistry(settings, tmp_path / "sessions") if auth else None
    app = None if auth else web.WebChatApp(settings, tmp_path / "sessions")
    server = web.create_server("127.0.0.1", 0, web.build_handler(app, auth_store=auth, app_registry=registry, secure_cookies=False))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        headers = helpers.login_headers(port, "alice", "quiet-lantern-river-orbit-47")[0] if auth else {}
        payload = {"action": "add", "paper_id": "P1"}
        assert helpers.http_request(port, "POST", "/api/shelf", payload=payload, headers=headers)[0] == 200
        status, _, raw, _ = helpers.http_request(port, "GET", "/api/shelf", headers=headers)
        assert status == 200 and json.loads(raw)["items"][0]["paper_id"] == "P1"
        bad = {"Cookie": headers["Cookie"]} if auth else {"Origin": "https://evil.example"}
        assert helpers.http_request(port, "POST", "/api/shelf", payload=payload, headers=bad)[0] == 403
        if auth:
            bob_headers = helpers.login_headers(port, "bob", "violet-planet-canvas-forest-83")[0]
            assert json.loads(helpers.http_request(port, "GET", "/api/shelf", headers=bob_headers)[2])["items"] == []
            assert helpers.http_request(port, "GET", "/api/shelf")[0] == 401
        else:
            assert helpers.http_request(port, "GET", "/api/shelf", headers={"Host": "evil.example"})[0] == 403
        assert helpers.http_request(port, "POST", "/api/shelf", payload={**payload, "owner": "bob"}, headers=headers)[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
