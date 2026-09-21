from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import threading

import pytest

from originrag.personal_shelf import PersonalShelf
from originrag.reading_history import ReadingHistory, reading_query


CATALOGUE = [{"paper_id": "P1", "title": "Numerical methods", "author": "Example", "year": "2026", "journal": "Methods",
              "doi": "10.1234/example", "doi_url": "https://doi.org/10.1234/example", "local_pdf": True,
              "local_pdf_relpath": "/private/papers/never-expose.pdf", "searchable": True},
             {"paper_id": "P2", "title": "Spatial modelling", "author": "Other", "year": "2025", "journal": "Science"}]


def change(history, paper_id="P1", action="mark_read", **options):
    return history.change({"action": action, "paper_id": paper_id, **options}, CATALOGUE)


def test_old_shelf_migrates_without_losing_books_and_status_is_explicit(tmp_path, monkeypatch):
    path = tmp_path / ".personal_shelf.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE books (paper_id TEXT PRIMARY KEY, metadata TEXT NOT NULL, saved_at INTEGER NOT NULL)")
        connection.execute("INSERT INTO books VALUES (?, ?, ?)", ("P1", json.dumps({"paper_id": "P1", "title": "Old title"}), 123))
    os.chmod(path, 0o644)
    history = ReadingHistory(tmp_path)
    assert history.status("P1") == {"paper_id": "P1", "is_read": False, "read_at": None, "on_shelf": True, "read_count": 0}
    assert history.snapshot(CATALOGUE)["items"] == []
    monkeypatch.setattr("originrag.reading_history.time.time_ns", lambda: 1_800_000_001_234_567_890)
    result = change(history)
    assert result == {"paper_id": "P1", "is_read": True, "read_at": 1_800_000_001_234, "on_shelf": True, "read_count": 1}
    first = history.snapshot(CATALOGUE)["items"][0]
    assert first["title"] == "Numerical methods"
    assert first["pdf_url"] == "/api/library/pdf?paper_id=P1"
    assert first["is_read"] and first["on_shelf"] and first["available"]
    assert "local_pdf_relpath" not in first and "private" not in json.dumps(first)
    assert path.stat().st_mode & 0o777 == 0o600
    shelf = PersonalShelf(tmp_path).snapshot(CATALOGUE)["items"][0]
    assert shelf["saved_at"] == 123 and shelf["is_read"] and shelf["read_at"] == result["read_at"]
    assert ReadingHistory(tmp_path / "bob").snapshot(CATALOGUE)["items"] == []


def test_marking_is_idempotent_and_shelf_removal_is_explicit(tmp_path, monkeypatch):
    history, shelf = ReadingHistory(tmp_path), PersonalShelf(tmp_path)
    shelf.change({"action": "add", "paper_id": "P1"}, CATALOGUE)
    monkeypatch.setattr("originrag.reading_history.time.time_ns", lambda: 1_800_000_000_000_000_000)
    first = change(history)
    monkeypatch.setattr("originrag.reading_history.time.time_ns", lambda: 1_800_000_009_000_000_000)
    assert change(history) == first
    removed = change(history, remove_from_shelf=True)
    assert removed == {**first, "on_shelf": False}
    assert change(history, remove_from_shelf=True) == removed
    assert shelf.snapshot(CATALOGUE)["items"] == []
    assert change(history, action="mark_unread") == {**removed, "is_read": False, "read_at": None, "read_count": 0}
    assert shelf.snapshot(CATALOGUE)["items"] == []
    shelf.change({"action": "add", "paper_id": "P1"}, CATALOGUE)
    assert change(history, action="mark_unread")["on_shelf"]
    assert not shelf.snapshot(CATALOGUE)["items"][0]["is_read"]


def test_read_history_survives_missing_pdf_catalogue_and_can_be_unread(tmp_path):
    history, shelf = ReadingHistory(tmp_path), PersonalShelf(tmp_path)
    shelf.change({"action": "add", "paper_id": "P1"}, CATALOGUE)
    first = change(history, remove_from_shelf=True)
    missing = history.snapshot([])["items"][0]
    assert missing["title"] == CATALOGUE[0]["title"]
    assert not missing["available"] and missing["pdf_url"] == ""
    no_pdf = history.snapshot([{**CATALOGUE[0], "local_pdf": False}])["items"][0]
    assert no_pdf["available"] and not no_pdf["pdf_url"]
    assert history.change({"action": "mark_read", "paper_id": "P1"}, []) == first
    unread = history.change({"action": "mark_unread", "paper_id": "P1"}, [])
    assert not unread["is_read"] and not unread["on_shelf"] and unread["read_at"] is None
    assert history.change({"action": "mark_unread", "paper_id": "P1"}, []) == unread
    assert history.snapshot([])["items"] == []
    shelf.change({"action": "add", "paper_id": "P2"}, CATALOGUE)
    assert history.change({"action": "mark_read", "paper_id": "P2"}, [])["is_read"]


def test_search_pagination_current_metadata_and_no_unsafe_links(tmp_path, monkeypatch):
    history = ReadingHistory(tmp_path)
    monkeypatch.setattr("originrag.reading_history.time.time_ns", lambda: 1_800_000_000_000_000_000)
    for paper in CATALOGUE:
        change(history, paper["paper_id"])
    assert [item["paper_id"] for item in history.snapshot(CATALOGUE)["items"]] == ["P1", "P2"]
    page = history.snapshot(CATALOGUE, offset=1, limit=1)
    assert page["total"] == page["read_count"] == 2 and page["items"][0]["paper_id"] == "P2"
    filtered = history.snapshot(CATALOGUE, query="NUMERICAL example", limit=1)
    assert filtered["total"] == 1 and filtered["read_count"] == 2 and filtered["items"][0]["paper_id"] == "P1"
    assert history.snapshot(CATALOGUE, query="NUMERICAL", offset=1)["items"] == []
    updated = [{**CATALOGUE[0], "title": "Updated title", "doi_url": "javascript:alert(1)"}]
    assert history.snapshot(updated, query="updated")["items"][0]["doi_url"] == ""
    assert history.snapshot(updated, query="spatial")["items"][0]["paper_id"] == "P2"
    assert history.snapshot([{**CATALOGUE[0], "doi_url": "https://[bad"}], query="numerical")["items"][0]["doi_url"] == ""


def test_atomic_rollback_if_shelf_removal_fails(tmp_path):
    history, shelf = ReadingHistory(tmp_path), PersonalShelf(tmp_path)
    shelf.change({"action": "add", "paper_id": "P1"}, CATALOGUE)
    with sqlite3.connect(history.path) as connection:
        connection.execute("CREATE TRIGGER deny_shelf_removal BEFORE DELETE ON books BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    with pytest.raises(sqlite3.IntegrityError, match="blocked"):
        change(history, remove_from_shelf=True)
    assert history.status("P1") == {"paper_id": "P1", "is_read": False, "read_at": None, "on_shelf": True, "read_count": 0}
    with sqlite3.connect(history.path) as connection:
        connection.execute("DROP TRIGGER deny_shelf_removal")
    assert not change(history, remove_from_shelf=True)["on_shelf"]


def test_concurrent_limit_and_same_paper_idempotency(tmp_path, monkeypatch):
    monkeypatch.setattr("originrag.reading_history.MAX_READ_PAPERS", 3)
    catalogue = [{"paper_id": str(i), "title": str(i)} for i in range(12)]
    ReadingHistory(tmp_path).snapshot(catalogue)
    def mark(number):
        try:
            return ReadingHistory(tmp_path).change({"action": "mark_read", "paper_id": str(number)}, catalogue)
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        successes = [result for result in pool.map(mark, range(12)) if result]
    assert len(successes) == 3
    history = ReadingHistory(tmp_path)
    assert history.snapshot(catalogue)["read_count"] == 3
    existing = successes[0]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(mark, [existing["paper_id"]] * 12))
    assert all(result["read_at"] == existing["read_at"] and result["read_count"] == 3 for result in results)
    history.change({"action": "mark_unread", "paper_id": existing["paper_id"]}, catalogue)
    assert history.snapshot(catalogue)["read_count"] == 2


@pytest.mark.parametrize("payload", [None, [], {}, {"action": [], "paper_id": "P1"},
    {"action": "mark_read", "paper_id": []}, {"action": "mark_read", "paper_id": ""},
    {"action": "mark_read", "paper_id": "P1", "owner": "bob"},
    {"action": "mark_read", "paper_id": "P1", "metadata": {"title": "forged"}},
    {"action": "mark_read", "paper_id": "unknown"}, {"action": "mark_read", "paper_id": "P1", "remove_from_shelf": "false"},
    {"action": "mark_unread", "paper_id": "P1", "remove_from_shelf": True},
    {"action": "mark_read", "paper_id": "P1", "remove_from_shelf": 1}])
def test_rejects_untrusted_mutations(tmp_path, payload):
    with pytest.raises(ValueError):
        ReadingHistory(tmp_path).change(payload, CATALOGUE)


@pytest.mark.parametrize("query", ["owner=bob", "paper_id=", "paper_id=P1&owner=bob", "paper_id=P1&paper_id=P2",
    "limit=0", "limit=101", "offset=-1", "offset=10001", "offset=", "offset=0&offset=1", "query=&query=",
    "offset=1.0", "offset=1e3", "paper_id=P1&limit=24", "query=" + "x" * 301])
def test_rejects_query_injection_duplicates_and_invalid_bounds(query):
    with pytest.raises(ValueError):
        reading_query(query)


def test_query_defaults_and_valid_options():
    assert reading_query("") == {"offset": 0, "limit": 24, "query": ""}
    assert reading_query("paper_id=P1") == {"paper_id": "P1"}
    assert reading_query("offset=10&limit=100&query=spatial+data") == {"offset": 10, "limit": 100, "query": "spatial data"}


@pytest.mark.parametrize("authenticated", [False, True])
def test_http_owner_boundaries_csrf_status_and_list(tmp_path, monkeypatch, authenticated):
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
        assert helpers.http_request(port, "POST", "/api/shelf", payload={"action": "add", "paper_id": "P1"}, headers=headers)[0] == 200
        payload = {"action": "mark_read", "paper_id": "P1", "remove_from_shelf": True}
        status, _, raw, _ = helpers.http_request(port, "POST", "/api/reading", payload=payload, headers=headers)
        result = json.loads(raw)
        assert status == 200 and result["is_read"] and not result["on_shelf"] and result["read_count"] == 1
        assert json.loads(helpers.http_request(port, "GET", "/api/reading?paper_id=P1", headers=headers)[2]) == result
        listing = json.loads(helpers.http_request(port, "GET", "/api/reading?query=numerical&limit=1", headers=headers)[2])
        assert listing["total"] == 1 and listing["read_count"] == 1 and listing["items"][0]["paper_id"] == "P1"
        assert not json.loads(helpers.http_request(port, "GET", "/api/shelf", headers=headers)[2])["items"]
        bad = {"Cookie": headers["Cookie"]} if auth else {"Origin": "https://evil.example"}
        assert helpers.http_request(port, "POST", "/api/reading", payload=payload, headers=bad)[0] == 403
        for query in ("owner=bob", "paper_id=P1&paper_id=P2", "limit=101", "query=&query="):
            assert helpers.http_request(port, "GET", "/api/reading?"+query, headers=headers)[0] == 400
        assert helpers.http_request(port, "POST", "/api/reading?owner=bob", payload=payload, headers=headers)[0] == 400
        assert helpers.http_request(port, "POST", "/api/reading", payload={**payload, "owner": "bob"}, headers=headers)[0] == 400
        if auth:
            bob = helpers.login_headers(port, "bob", "violet-planet-canvas-forest-83")[0]
            assert not json.loads(helpers.http_request(port, "GET", "/api/reading?paper_id=P1", headers=bob)[2])["is_read"]
            assert json.loads(helpers.http_request(port, "GET", "/api/reading", headers=bob)[2])["read_count"] == 0
            assert helpers.http_request(port, "GET", "/api/reading")[0] == 401
            assert helpers.http_request(port, "POST", "/api/reading", payload=payload)[0] == 401
        else:
            assert helpers.http_request(port, "GET", "/api/reading", headers={"Host": "evil.example"})[0] == 403
        monkeypatch.setattr(web, "library_catalog", lambda: ())
        assert not json.loads(helpers.http_request(port, "GET", "/api/reading", headers=headers)[2])["items"][0]["available"]
        unread = {"action": "mark_unread", "paper_id": "P1"}
        assert not json.loads(helpers.http_request(port, "POST", "/api/reading", payload=unread, headers=headers)[2])["is_read"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
