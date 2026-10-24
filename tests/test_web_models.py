import json
import os
from pathlib import Path
import shutil
import threading
import time

import pytest

import test_web_chat as http
from originrag import model_runtime


PASSWORD = "quiet-lantern-river-orbit-47"


@pytest.fixture(params=[False, True])
def models_server(tmp_path, monkeypatch, request):
    source = Path(__file__).resolve().parents[1]
    shutil.copytree(source / "assets/workspace", tmp_path / "assets/workspace")
    web = http.web_chat
    monkeypatch.setattr(web, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(web, "library_catalog", lambda: ())
    monkeypatch.delenv("ORIGINRAG_MODELS_DIR", raising=False)
    monkeypatch.delenv("ORIGINRAG_MODEL_DOWNLOAD_USERS", raising=False)
    monkeypatch.delenv("ORIGINRAG_MODEL_MANAGE_USERS", raising=False)
    settings = web.WebChatSettings(backend="template")
    auth = http.AuthStore(tmp_path / "auth.sqlite") if request.param else None
    if auth:
        auth.create_user("admin", PASSWORD, role="admin")
        auth.create_user("reader", PASSWORD)
    registry = web.AuthenticatedWebChatRegistry(settings, tmp_path / "sessions") if auth else None
    app = None if auth else web.WebChatApp(settings, tmp_path / "sessions")
    manager = model_runtime.manager_for(tmp_path)
    monkeypatch.setattr(manager, "inventory", lambda **kw: ([
        {"provider": "ollama", "model": "test:tiny", "name": "test:tiny", "bytes": 100,
         "installed": True, "loaded": False, "downloadable": False},
    ], None))
    server = web.create_server("127.0.0.1", 0, web.build_handler(app, auth_store=auth, app_registry=registry))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    accounts = {name: http.login_headers(port, name, PASSWORD)[0]
                for name in ("admin", "reader")} if auth else {}
    try:
        yield port, accounts, manager
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        manager.close()


def get(port, headers=None):
    response = http.http_request(port, "GET", "/api/models", headers=headers)
    return response[0], json.loads(response[2])


def post(port, headers, payload):
    return http.http_request(port, "POST", "/api/models", headers=headers, payload=payload)[0]


def test_models_authentication_csrf_and_admin(models_server):
    port, accounts, manager = models_server
    payload = {"action": "select", "provider": "ollama", "model": "test:tiny"}
    if accounts:
        assert get(port)[0] == 401
        status, data = get(port, accounts["reader"])
        assert status == 200 and data["can_use"] and not data["can_manage"]
        assert not data["local_token"]
        assert post(port, accounts["reader"], {"action": "stop"}) == 403
        admin = accounts["admin"]
        assert post(port, {"Cookie": admin["Cookie"]}, payload) == 403
        assert get(port, admin)[1]["can_manage"]
    else:
        status, data = get(port)
        assert status == 200 and data["can_use"] and data["can_manage"] and data["local_token"]
        assert post(port, {}, payload) == 403
        admin = {"X-OriginRAG-Local-Token": data["local_token"]}
        for extra in ({"Origin": "https://evil.example"}, {"Host": "evil.example"},
                      {"Sec-Fetch-Site": "cross-site"}):
            assert post(port, {**admin, **extra}, payload) == 403
    assert manager.selected() is None
    assert post(port, admin, payload) == 202
    deadline = time.monotonic() + 2
    while manager.job["status"] == "running":
        assert time.monotonic() < deadline
        time.sleep(.01)
    assert manager.selected()["model"] == "test:tiny"
    assert get(port, admin)[1]["selection"]["model"] == "test:tiny"


def test_models_dialog_from_settings(models_server):
    playwright = pytest.importorskip("playwright.sync_api")
    port, accounts, manager = models_server
    base = f"http://127.0.0.1:{port}"
    with playwright.sync_playwright() as p:
        browser = getattr(p, os.environ.get("ORIGINRAG_TEST_BROWSER", "chromium")).launch()
        context = browser.new_context()
        if accounts:
            context.add_cookies([
                {**dict(zip(("name", "value"), cookie.split("=", 1))), "url": base}
                for cookie in accounts["admin"]["Cookie"].split("; ")
            ])
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/library")
        page.locator("#globalSettings").click()
        page.locator("#globalModels").click()
        playwright.expect(page.locator("#modelDialog")).to_be_visible()
        playwright.expect(page.locator("#modelList")).to_contain_text("test:tiny")
        page.on("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Use model", exact=True).click()
        playwright.expect(page.locator("#modelSelection")).to_contain_text("test:tiny", timeout=10000)
        page.locator("#modelProvider").select_option("colibri")
        playwright.expect(page.locator("#modelWarning")).to_contain_text("has not installed")
        playwright.expect(page.locator("#modelList")).to_contain_text("Qwen3-Coder")
        assert page.get_by_role("button", name="Download", exact=True).count() == 0
        page.locator("#modelClose").click()
        playwright.expect(page.locator("#modelDialog")).not_to_be_visible()
        assert not errors
        browser.close()


def test_download_allowlist_is_enforced_for_admins_and_users(models_server, monkeypatch):
    port, accounts, manager = models_server
    monkeypatch.setenv("ORIGINRAG_MODEL_DOWNLOAD_USERS", " reader ")
    calls = []
    monkeypatch.setattr(manager, "change", lambda payload: calls.append(payload))
    download = {"action": "download", "provider": "ollama", "model": "test:tiny", "confirmed": True}
    if not accounts:
        data = get(port)[1]
        assert data["can_download"]
        assert post(port, {"X-OriginRAG-Local-Token": data["local_token"]}, download) == 202
        return
    assert get(port, accounts["admin"])[1]["can_manage"]
    assert not get(port, accounts["admin"])[1]["can_download"]
    assert get(port, accounts["reader"])[1]["can_download"]
    assert not get(port, accounts["reader"])[1]["can_manage"]
    assert post(port, accounts["admin"], download) == 403
    assert post(port, accounts["reader"], download) == 202
    assert post(port, accounts["reader"], {"action": "cancel"}) == 202
    assert post(port, accounts["reader"], {"action": "default"}) == 202
    assert post(port, {"Cookie": accounts["reader"]["Cookie"]}, download) == 403
    assert calls == [download, {"action": "cancel"}, {"action": "default"}]
    monkeypatch.setenv("ORIGINRAG_MODEL_DOWNLOAD_USERS", "")
    assert not get(port, accounts["reader"])[1]["can_download"]
    assert post(port, accounts["reader"], download) == 403


def test_named_user_can_manage_without_admin_role(models_server, monkeypatch):
    port, accounts, manager = models_server
    if not accounts:
        return
    monkeypatch.setenv("ORIGINRAG_MODEL_MANAGE_USERS", "reader")
    calls = []
    monkeypatch.setattr(manager, "change", lambda payload: calls.append(payload))
    assert get(port, accounts["reader"])[1]["can_manage"]
    assert not get(port, accounts["reader"])[1]["can_download"]
    assert post(port, accounts["reader"], {"action": "default"}) == 202
    assert post(port, {"Cookie": accounts["reader"]["Cookie"]}, {"action": "default"}) == 403
    assert calls == [{"action": "default"}]


def test_users_can_use_installed_models_without_download_permission(models_server, monkeypatch):
    port, accounts, manager = models_server
    if not accounts:
        return
    monkeypatch.setenv("ORIGINRAG_MODEL_DOWNLOAD_USERS", "admin")
    calls = []
    monkeypatch.setattr(manager, "change", lambda payload: calls.append(payload))
    reader = accounts["reader"]
    data = get(port, reader)[1]
    assert data["can_use"] and not data["can_manage"] and not data["can_download"]
    for action in ("select", "benchmark", "default"):
        payload = {"action": action, "provider": "ollama", "model": "test:tiny"}
        assert post(port, {"Cookie": reader["Cookie"]}, payload) == 403
        assert post(port, reader, payload) == 202
    for action in ("download", "cancel", "stop"):
        assert post(port, reader, {"action": action}) == 403
    assert [call["action"] for call in calls] == ["select", "benchmark", "default"]


def test_user_selection_uses_real_validation_without_downloading(models_server):
    port, accounts, manager = models_server
    if not accounts:
        return
    reader = accounts["reader"]
    missing_model = next(iter(model_runtime.OLLAMA_DOWNLOADS))
    for action in ("select", "benchmark"):
        assert post(port, reader, {"action": action, "provider": "ollama",
                                   "model": missing_model}) == 400
    assert manager.selected() is None
    assert post(port, reader, {"action": "select", "provider": "ollama",
                               "model": "test:tiny"}) == 202
    deadline = time.monotonic() + 2
    while manager.job["status"] == "running":
        assert time.monotonic() < deadline
        time.sleep(.01)
    assert manager.selected()["model"] == "test:tiny"


def test_download_only_user_sees_enabled_download(models_server, monkeypatch):
    port, accounts, manager = models_server
    if not accounts:
        return
    monkeypatch.setenv("ORIGINRAG_MODEL_DOWNLOAD_USERS", "reader")
    monkeypatch.setattr(manager, "inventory", lambda **kw: ([
        {"provider": "ollama", "model": "test:download", "name": "Downloadable model",
         "bytes": 100, "installed": False, "loaded": False, "downloadable": True},
    ], None))
    playwright = pytest.importorskip("playwright.sync_api")
    base = f"http://127.0.0.1:{port}"
    with playwright.sync_playwright() as p:
        browser = getattr(p, os.environ.get("ORIGINRAG_TEST_BROWSER", "chromium")).launch()
        context = browser.new_context()
        context.add_cookies([
            {**dict(zip(("name", "value"), cookie.split("=", 1))), "url": base}
            for cookie in accounts["reader"]["Cookie"].split("; ")
        ])
        page = context.new_page()
        page.goto(base + "/library")
        page.locator("#globalSettings").click()
        page.locator("#globalModels").click()
        playwright.expect(page.get_by_role("button", name="Download", exact=True)).to_be_enabled()
        playwright.expect(page.locator("#modelAccess")).to_contain_text("You can download")
        browser.close()
