import json
import sys
from pathlib import Path

import pytest


playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import web_chat  # noqa: E402


@pytest.fixture
def browser_page(tmp_path):
    state = {"user_id": "alice", "authenticated": True, "local_imports": False, "total": 0, "searchable": 0, "local_pdf": 0, "error": False, "imports": []}
    requests = []
    base = "http://127.0.0.1:39843"

    def route_request(route):
        request = route.request
        assert request.url.startswith(base), request.url
        path = request.url.removeprefix(base)
        requests.append(path)
        if path == "/":
            route.fulfill(status=200, content_type="text/html", body=web_chat.INDEX_HTML)
            return
        if path.startswith("/assets/"):
            asset = ROOT / path.lstrip("/").split("?")[0]
            if asset.is_file():
                route.fulfill(status=200, content_type="image/png", body=asset.read_bytes())
            else:
                route.fulfill(status=404)
            return
        payload = {"jobs": [], "sessions": [], "queue": {}, "state": "idle", "groups": []}
        code = 200
        if path == "/api/status":
            payload["library_access"] = {"enabled": state["authenticated"] or state["local_imports"],
                                         "csrf_token": "local-test-token" if state["local_imports"] else ""}
            if state["authenticated"]:
                payload["user"] = {"user_id": state["user_id"], "display_name": "Test user"}
        elif path == "/api/library-stats":
            payload.update({key: state[key] for key in ("total", "searchable", "local_pdf")})
        elif path == "/api/paper-import":
            assert request.method == "POST"
            assert request.post_data_json == {"doi": "10.1234/test"}
            if state["local_imports"]:
                assert request.headers.get("x-originrag-local-token") == "local-test-token"
            else:
                assert request.headers.get("x-csrf-token") == "test-csrf"
            job = {"job_id": "job1", "doi": "10.1234/test", "state": "queued", "message": "Waiting to import"}
            state["imports"] = [job]
            payload = job
            code = 202
        elif path == "/api/paper-imports":
            payload = {"jobs": state["imports"]}
        elif path.startswith("/api/discover-journals"):
            payload = {"items": [
                {"title": "Nature", "publisher": "Springer Nature", "issns": ["0028-0836", "1476-4687"]},
                {"title": "Science", "publisher": "AAAS", "issns": ["0036-8075"]},
            ]}
        elif path.startswith("/api/discover-papers"):
            if state["error"]:
                payload = {"error": "The literature service is unavailable."}
                code = 502
            else:
                payload = {"items": [{"title": "<img src=x onerror=alert(1)>", "author": "Example", "year": "2025", "doi": "10.1234/test", "url": "https://doi.org/10.1234/test", "in_library": True}]}
        route.fulfill(status=code, content_type="application/json", body=json.dumps(payload))

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_cookies([{"name": "pfempire_csrf", "value": "test-csrf", "url": base}])
        context.route("**/*", route_request)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base)
        page.wait_for_selector("#welcomeGuide[open]")
        yield page, state, requests, tmp_path
        assert errors == []
        browser.close()


def test_first_visit_replay_and_account_specific_completion(browser_page):
    page, state, requests, tmp = browser_page
    assert "Your library is empty" in page.locator("#guideText").inner_text()
    assert page.locator("#guideNext").evaluate("el => document.activeElement === el")
    page.screenshot(path=str(tmp / "first-visit.png"))
    page.locator("#guideNext").click()
    assert page.locator("#guideCount").inner_text() == "2 of 5"
    page.locator("#guideBack").click()
    assert page.locator("#guideCount").inner_text() == "1 of 5"
    page.keyboard.press("Escape")
    page.reload()
    page.wait_for_selector("#discoveryButton")
    page.wait_for_load_state("networkidle")
    assert not page.locator("#welcomeGuide").evaluate("el => el.open")
    page.locator("#aboutButton").click()
    page.locator("#guideReplay").click()
    assert page.locator("#welcomeGuide").evaluate("el => el.open")
    page.locator("#guideSkip").click()
    state["user_id"] = "bob"
    page.reload()
    page.wait_for_selector("#welcomeGuide[open]")
    page.locator("#guideSkip").click()
    assert not any(path.startswith("/api/discover-papers") for path in requests)


def test_journal_multi_selection_keyboard_and_removal(browser_page):
    from urllib.parse import parse_qs, urlparse
    page, state, requests, tmp = browser_page
    page.locator("#guideSkip").click()
    page.locator("#discoveryButton").click()
    page.locator("#journalQuery").fill("Nature")
    page.locator("#journalOptions input").first.wait_for()
    page.locator("#journalQuery").press("Enter")
    page.keyboard.press("Space")
    page.locator("#journalOptions input").nth(1).check()
    assert page.locator("#journalSelected button").count() == 2
    assert not page.locator("#discoveryQuery").evaluate("el => el.required")
    page.locator("#discoverySearch").click()
    page.locator(".discovery-item").wait_for()
    request = next(path for path in reversed(requests) if path.startswith("/api/discover-papers"))
    assert set(parse_qs(urlparse(request).query)["issn"]) == {"0028-0836", "1476-4687", "0036-8075"}
    page.get_by_role("button", name="Remove Nature", exact=True).click()
    page.get_by_role("button", name="Remove Science", exact=True).click()
    assert page.locator("#discoveryQuery").evaluate("el => el.required")
    assert page.locator("#journalOptions input:checked").count() == 0
    page.locator("#discoveryQuery").fill("graph theory")
    before = len([path for path in requests if path.startswith("/api/discover-papers")])
    page.locator("#discoverySearch").click()
    assert "Tick a matching journal" in page.locator("#journalStatus").inner_text()
    assert len([path for path in requests if path.startswith("/api/discover-papers")]) == before
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("#journalOptions input").first.check()
    assert page.locator("#discoveryDialog").evaluate("el => el.scrollWidth <= el.clientWidth")
    page.screenshot(path=str(tmp / "journal-picker-mobile.png"))


def test_search_empty_state_updates_mobile_and_themes(browser_page):
    page, state, requests, tmp = browser_page
    page.locator("#guideSkip").click()
    assert page.locator("header .header-left #discoveryButton").count() == 1
    assert page.locator(".library-start").count() == 0
    find_box = page.locator("#discoveryButton").bounding_box()
    appearance_box = page.locator("#appearanceButton").bounding_box()
    assert find_box["x"] < appearance_box["x"]
    assert abs(find_box["y"] + find_box["height"] / 2 - appearance_box["y"] - appearance_box["height"] / 2) < 3
    page.evaluate("window.dispatchEvent(new CustomEvent('originrag:library-stats', {detail: {total: 40, searchable: 0, local_pdf: 0}}))")
    assert page.locator(".library-start").count() == 0
    page.locator("#aboutButton").click()
    page.locator("#guideReplay").click()
    assert "No full-text papers" in page.locator("#guideText").inner_text()
    page.locator("#guideSkip").click()
    page.locator("#discoveryButton").click()
    page.select_option("#discoveryExample", "historical linguistics language change")
    page.locator("#discoverySearch").click()
    page.wait_for_selector(".discovery-item")
    assert page.locator(".discovery-item a").inner_text() == "<img src=x onerror=alert(1)>"
    assert page.locator(".discovery-item img").count() == 0
    assert "Already in the catalogue" in page.locator(".discovery-item").inner_text()
    assert any("query=historical+linguistics+language+change" in path for path in requests)
    for theme in ("dark", "light", "black"):
        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
        for width in (1280, 390, 320):
            page.set_viewport_size({"width": width, "height": 840})
            assert page.locator("#discoveryDialog").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
            page.screenshot(path=str(tmp / f"discovery-{theme}-{width}.png"))
    state["error"] = True
    page.locator("#discoverySearch").click()
    page.wait_for_function("document.getElementById('discoveryStatus').textContent.includes('unavailable')")
    assert page.locator("#discoverySearch").is_enabled()
    page.locator("#discoveryUpload").click()
    assert page.locator("#paperUploadBackdrop").evaluate("el => el.classList.contains('open')")
    page.locator("#paperUploadClose").click()
    page.evaluate("window.dispatchEvent(new CustomEvent('originrag:library-stats', {detail: {total: 40, searchable: 1, local_pdf: 1}}))")
    assert page.locator(".library-start").count() == 0


def test_direct_import_status_survives_dialog_and_page_reload(browser_page):
    page, state, requests, tmp = browser_page
    page.locator("#guideSkip").click()
    page.locator("#discoveryButton").click()
    page.locator("#discoveryQuery").fill("graph algorithms")
    page.locator("#discoverySearch").click()
    page.locator(".discovery-item").get_by_role("button", name="Add to library").click()
    page.wait_for_function("document.querySelector('.discovery-item button').textContent === 'Importing…'")
    page.locator("#discoveryClose").click()
    page.reload()
    page.wait_for_load_state("networkidle")
    page.locator("#discoveryButton").click()
    page.wait_for_function("document.getElementById('paperImportJobs').textContent.includes('Waiting to import')")
    job = dict(state["imports"][0])
    state["imports"] = []
    page.wait_for_function("document.getElementById('paperImportJobs').textContent.includes('no longer available')")
    state["imports"] = [{**job, "state": "manual_upload", "message": "No matching accessible PDF found"}]
    page.locator("#paperImportJobs").get_by_role("button", name="Upload PDF").wait_for()
    page.locator("#paperImportJobs").get_by_role("button", name="Upload PDF").click()
    assert page.locator("#paperDoi").input_value() == "10.1234/test"
    assert page.locator("#paperUploadBackdrop").evaluate("el => el.classList.contains('open')")


def test_anonymous_session_explains_disabled_import(browser_page):
    page, state, requests, tmp = browser_page
    page.locator("#guideSkip").click()
    state["authenticated"] = False
    page.reload()
    page.wait_for_selector("#welcomeGuide[open]")
    page.locator("#guideSkip").click()
    page.locator("#discoveryButton").click()
    page.locator("#discoveryQuery").fill("graph algorithms")
    page.locator("#discoverySearch").click()
    button = page.locator(".discovery-item").get_by_role("button", name="Import unavailable")
    button.wait_for()
    assert button.is_disabled()
    assert "./web" in button.get_attribute("title")
    assert "/api/paper-import" not in requests


def test_local_import_without_account_or_cookie(browser_page):
    page, state, requests, tmp = browser_page
    page.locator("#guideSkip").click()
    state["authenticated"], state["local_imports"] = False, True
    page.context.clear_cookies()
    page.reload()
    page.locator("#guideSkip").click()
    page.locator("#paperUploadButton").wait_for(state="visible")
    page.locator("#discoveryButton").click()
    page.locator("#discoveryQuery").fill("graph algorithms")
    page.locator("#discoverySearch").click()
    page.locator(".discovery-item").get_by_role("button", name="Add to library").click()
    page.wait_for_function("document.querySelector('#paperImportJobs').textContent.includes('Waiting to import')")
    assert "/api/paper-import" in requests
    assert not any("/login" in path for path in requests)
