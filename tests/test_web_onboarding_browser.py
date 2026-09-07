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
    state = {"user_id": "alice", "total": 0, "searchable": 0, "local_pdf": 0, "error": False}
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
            payload["user"] = {"user_id": state["user_id"], "display_name": "Test user"}
        elif path == "/api/library-stats":
            payload.update({key: state[key] for key in ("total", "searchable", "local_pdf")})
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
