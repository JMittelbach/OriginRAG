import json
import threading

import pytest

from test_web_chat import http_request, login_headers
import web_chat
from originrag.discovery.topic_search import DiscoveryBusy, DiscoveryUnavailable
from originrag.web_auth import AuthStore


def test_discovery_api_requires_login_and_reports_availability(tmp_path, monkeypatch):
    calls = []

    def search(self, owner, **kwargs):
        calls.append((owner, kwargs))
        if kwargs["query"] == "invalid":
            raise ValueError("Invalid query")
        if kwargs["query"] == "busy":
            raise DiscoveryBusy("Try again")
        if kwargs["query"] == "offline":
            raise DiscoveryUnavailable("Service unavailable")
        return {"items": [{"doi": "10.1234/test", "title": "History"}], "downloaded": False}

    monkeypatch.setattr(web_chat.TopicDiscovery, "search", search)
    monkeypatch.setattr(web_chat, "library_catalog", lambda: ({"doi": "10.1234/TEST"},))
    auth = AuthStore(tmp_path / "auth.sqlite")
    user = auth.create_user("alice", "quiet-lantern-river-orbit-47")
    registry = web_chat.AuthenticatedWebChatRegistry(web_chat.WebChatSettings(), tmp_path / "sessions")
    handler = web_chat.build_handler(None, auth_store=auth, app_registry=registry)
    try:
        server = web_chat.create_server("127.0.0.1", 0, handler)
    except PermissionError:
        handler.turn_job_manager.shutdown()
        pytest.skip("Loopback unavailable")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        status, _, _, _ = http_request(port, "GET", "/api/discover-papers?query=history")
        assert status == 401
        assert calls == []
        headers, _ = login_headers(port, "alice", "quiet-lantern-river-orbit-47")
        status, _, body, _ = http_request(port, "GET", "/api/discover-papers?query=history&since=2020&order=newest", headers=headers)
        assert status == 200
        assert json.loads(body)["items"][0]["in_library"] is True
        assert calls[0] == (user.user_id, {"query": "history", "since": "2020", "order": "newest", "kind": "journal-article"})
        for query, expected in (("invalid", 400), ("busy", 429), ("offline", 502)):
            status, _, _, _ = http_request(port, "GET", f"/api/discover-papers?query={query}", headers=headers)
            assert status == expected
    finally:
        server.shutdown()
        server.server_close()
        handler.turn_job_manager.shutdown()
        thread.join(timeout=5)
