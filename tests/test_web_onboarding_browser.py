import json
import sys
from pathlib import Path

import pytest


playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import web_chat  # noqa: E402


@pytest.fixture
def browser_page(tmp_path, request):
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
        if path.startswith('/api/library/pdf?'):
            import fitz
            with fitz.open() as document:
                document.new_page().insert_text((72, 72), 'Synthetic shelf PDF')
                route.fulfill(status=200, content_type='application/pdf', headers={'Content-Disposition': 'inline; filename="test.pdf"'}, body=document.tobytes())
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
        elif path.startswith("/api/library?"):
            payload = {"items": state.get("library", []), "total": len(state.get("library", [])), "has_more": False}
        elif path == "/api/shelf":
            shelf = state.setdefault("shelves", {}).setdefault(state["user_id"], {})
            if request.method == "POST":
                change = request.post_data_json
                assert set(change) == {"action", "paper_id"}
                assert request.headers.get("x-csrf-token") == "test-csrf"
                if change["action"] == "add":
                    paper = next(p for p in state["library"] if p["paper_id"] == change["paper_id"])
                    shelf[paper["paper_id"]] = {**paper, "available": True, "summary_available": paper.get("searchable", False), "pdf_url": f'/api/library/pdf?paper_id={paper["paper_id"]}' if paper.get("local_pdf") else ""}
                else:
                    shelf.pop(change["paper_id"], None)
            payload = {"items": list(shelf.values()), "limit": 200}
        elif path == "/api/chat":
            state.setdefault("chat_requests", []).append(request.post_data_json)
            payload = {"job_id": "summary-test", "state": "queued", "session_dir": request.post_data_json.get("session_dir", "")}
            code = 202
        elif path == "/api/jobs":
            payload["jobs"] = state.get("jobs", [])
        elif path == "/api/job/cancel":
            assert request.headers.get("x-csrf-token") == "test-csrf"
            assert set(request.post_data_json) == {"job_id"}
            state.setdefault("cancel_requests", []).append(request.post_data_json)
            if state.get("cancel_error"):
                payload = {"error": "Could not reach the server. Please retry."}
                code = 503
            else:
                job = next(j for j in state["jobs"] if j["job_id"] == request.post_data_json["job_id"])
                job["state"] = "cancelling" if job["state"] == "running" else "cancelled"
                job["can_cancel"] = False
                payload = job
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
        elif path in {"/api/library-upload", "/api/library-sync"}:
            assert request.method == "POST"
            assert state["local_imports"]
            assert request.headers.get("x-originrag-local-token") == "local-test-token"
            payload = {"state": "queued", "status": "queued"}
            code = 202
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
        browser = p.chromium.launch(channel='chromium' if getattr(request, 'param', None) == 'pdf' else None)
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
    page.wait_for_function("document.querySelector('.discovery-item button').textContent === 'Waiting…'")
    page.locator("#discoveryClose").click()
    page.reload()
    page.wait_for_load_state("networkidle")
    page.locator("#discoveryButton").click()
    page.wait_for_function("document.getElementById('paperImportJobs').textContent.includes('Waiting to import')")
    job = dict(state["imports"][0])
    state["imports"] = []
    page.wait_for_function("document.getElementById('paperImportJobs').textContent.includes('no longer available')")
    state["imports"] = [{**job, "state": "manual_upload", "message": "No matching accessible PDF found"}]
    page.wait_for_function("document.getElementById('paperImportJobs').textContent.includes('Not added to the RAG library')")
    assert page.locator("#paperImportJobs button").count() == 0
    assert not page.locator("#paperUploadBackdrop").evaluate("el => el.classList.contains('open')")


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


@pytest.mark.parametrize("job_state", ["failed", "manual_upload", "error"])
def test_failed_import_stays_an_import_without_upload_detour(browser_page, job_state):
    page, state, requests, tmp = browser_page
    page.locator("#guideSkip").click()
    state["imports"] = [{"job_id": "job1", "doi": "10.1234/test", "state": job_state,
                         "reason": "browser_required", "download_url": "https://example.org/pdf",
                         "message": "Import failed: source blocked downloading. Not added to the RAG library."}]
    page.locator("#discoveryButton").click()
    page.locator("#discoveryQuery").fill("graph algorithms")
    page.locator("#discoverySearch").click()
    row = page.locator(".discovery-item")
    button = row.get_by_role("button", name="Retry import", exact=True)
    button.wait_for()
    assert row.get_by_role("link", name="Open PDF in browser", exact=True).count() == 0
    assert page.locator("#discoveryDialog").get_by_role("button", name="Upload PDF", exact=True).count() == 0
    assert "Not added to the RAG library" in row.inner_text()
    button.click()
    page.wait_for_function("document.querySelector('.discovery-item button').textContent === 'Waiting…'")
    assert "/api/paper-import" in requests
    assert not page.locator("#paperUploadBackdrop").evaluate("el => el.classList.contains('open')")
    job = dict(state["imports"][0])
    for stage, label in [("downloading", "Downloading…"), ("indexing", "Indexing…"), ("imported", "In library")]:
        state["imports"] = [{**job, "state": stage, "message": label}]
        page.wait_for_function("label => document.querySelector('.discovery-item button').textContent === label", arg=label)
        assert row.locator("button").is_disabled()
    assert len([p for p in requests if p == "/api/paper-import"]) == 1
    assert "/api/library-upload" not in requests


def test_local_import_without_account_or_cookie(browser_page):
    page, state, requests, tmp = browser_page
    page.locator("#guideSkip").click()
    state["authenticated"], state["local_imports"] = False, True
    page.context.clear_cookies()
    page.reload()
    page.locator("#guideSkip").click()
    page.locator("#paperUploadButton").wait_for(state="visible")
    assert page.locator("#libraryButton").is_visible()
    page.locator("#discoveryButton").click()
    page.locator("#discoveryQuery").fill("graph algorithms")
    page.locator("#discoverySearch").click()
    page.locator(".discovery-item").get_by_role("button", name="Add to library").click()
    page.wait_for_function("document.querySelector('#paperImportJobs').textContent.includes('Waiting to import')")
    assert "/api/paper-import" in requests
    assert not any("/login" in path for path in requests)
    page.locator("#discoveryClose").click()
    page.locator("#paperUploadButton").click()
    page.locator("#paperFileInput").set_input_files({"name": "example.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.7 synthetic test"})
    page.wait_for_function("document.querySelector('#paperUploadList').textContent.includes('Queued')")
    assert "/api/library-upload" in requests
    assert "/api/library-sync" in requests


def test_chat_submission_is_visible_before_server_acknowledgement(browser_page):
    page, state, requests, tmp = browser_page
    page.locator("#guideSkip").click()
    page.evaluate("""() => {
      const originalFetch = window.fetch;
      window.fetch = (url, options) => url === '/api/chat'
        ? new Promise(resolve => { window.finishTestSubmission = resolve; })
        : originalFetch(url, options);
    }""")
    page.locator("#message").fill("Welche Methoden findet ihr im Korpus?")
    page.locator("#message").press("Enter")
    page.wait_for_function("typeof window.finishTestSubmission === 'function'")
    assert "waiting for the server" in page.locator(".working-stage-label").inner_text()
    assert "Submitting" in page.locator("#queueSummary").inner_text()
    assert page.locator("#send").is_disabled()
    page.evaluate("""() => window.finishTestSubmission(new Response(
      JSON.stringify({error: 'Test rejection'}), {status: 400, headers: {'Content-Type': 'application/json'}}))""")
    page.wait_for_function("!document.querySelector('[data-job-id^=submission]')")
    assert "Test rejection" in page.locator("#chat").inner_text()


def test_inventory_citations_render_as_links_not_internal_ids(browser_page):
    from originrag.analysis.corpus_inventory import inventory_markdown

    page, state, requests, tmp = browser_page
    page.locator("#guideSkip").click()
    source = {"paper_id": "SYNTHETIC", "title": "A numerical methods example",
              "author": "Example", "year": "2020", "citation_short": "Example, 2020",
              "evidence_ids": ["E001"], "url": "https://example.org/paper",
              "source_role": "search_result"}
    answer = inventory_markdown({"items": [{"name": "finite elements", "paper_id": "SYNTHETIC"}]},
                                language="de", sources=[source])
    result = {"answer": answer, "sources": [source], "task": "sources",
              "answer_state": "results", "assurance": {}}
    page.evaluate("data => addMessage('assistant', data.answer, resultMeta(data), data)", result)
    body = page.locator(".assistant .markdown").last
    assert "finite elements" in body.inner_text()
    assert "[[cite:" not in body.inner_text()
    assert body.locator('a[href="https://example.org/paper"]').count() == 1


def test_library_summary_uses_chat_queue_and_preserves_draft(browser_page):
    page, state, requests, tmp = browser_page
    page.evaluate("Object.defineProperty(navigator, 'language', {get: () => 'de-DE'})")
    page.locator("#guideSkip").click()
    state["library"] = [
        {"paper_id": "P1", "title": "Numerical methods", "doi": "10.1234/numerical", "searchable": True},
        {"paper_id": "P2", "title": "Metadata only", "searchable": False},
    ]
    page.locator("#message").fill("Unsent draft")
    page.locator("#libraryButton").click()
    buttons = page.locator(".library-paper").get_by_role("button", name="Summarize", exact=True)
    buttons.first.wait_for()
    assert buttons.count() == 2 and buttons.nth(1).is_disabled()
    buttons.first.click()
    assert page.locator("#message").input_value() == "Unsent draft"
    assert not state.get("chat_requests")
    page.evaluate("document.getElementById('message').value = ''")
    buttons.first.click()
    page.wait_for_function("!document.querySelector('#libraryBrowserBackdrop').classList.contains('open')")
    page.wait_for_function("!document.querySelector('[data-job-id^=submission]')")
    assert len(state["chat_requests"]) == 1
    assert state["chat_requests"][0]["message"] == "Summarize this paper: 10.1234/numerical"
    assert "/api/chat" in requests


def test_reading_card_has_collapsed_quotes_and_no_evidence_certificate(browser_page):
    page, state, requests, tmp = browser_page
    page.locator("#guideSkip").click()
    data = {"task": "paper_summary", "answer": "## Numerical methods\n\n### At a glance\nTwo methods were compared.",
            "answer_state": "answered", "elapsed_seconds": 3,
            "reading_card": {"coverage": {"basis": "abstract_only"}, "cache_hit": True,
                             "passages": [{"section": "Abstract", "page_start": 1, "quote": "<script>untrusted</script> literal passage"}]},
            "sources": [{"paper_id": "P1", "title": "Numerical methods", "author": "Example", "year": "2025",
                         "source_role": "summary_source", "cited": True, "url": "https://example.org/paper"}]}
    page.evaluate("data => addMessage('assistant', data.answer, resultMeta(data), data)", data)
    assert "abstract only" in page.locator(".result-meta").inner_text()
    assert "Independent studies" not in page.locator(".result-meta").inner_text()
    quote = page.locator(".result-meta blockquote")
    assert not quote.is_visible()
    page.get_by_text("Passages behind this summary", exact=True).click()
    assert quote.is_visible() and "<script>" in quote.inner_text()
    assert quote.locator("script").count() == 0
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.locator(".result-meta").evaluate("el => el.scrollWidth <= el.clientWidth + 2")
    page.screenshot(path=str(tmp / "reading-card-mobile.png"))


def test_personal_shelf_save_reload_summary_and_remove(browser_page):
    page, state, requests, tmp = browser_page
    page.add_init_script("Object.defineProperty(navigator, 'language', {get: () => 'de-DE'})")
    page.locator("#guideSkip").click()
    state["library"] = [{"paper_id": f"P{i}", "title": title, "author": author, "year": "2025",
                         "doi": f"10.1234/p{i}", "doi_url": f"https://doi.org/10.1234/p{i}", "searchable": True}
                        for i, (title, author) in enumerate([
                            ("A practical guide to scientific evidence", "Morgan"),
                            ("Mapping the diversity of life", "Rivera"),
                            ("Statistical learning with incomplete observations", "Kim"),
                            ("The hidden structure of complex networks", "Ali"),
                            ("Methods for spatial data integration", "Taylor"),
                            ("Reproducible computational research", "Chen"),
                        ])]
    page.locator("#libraryButton").click()
    page.locator(".library-paper .shelf-save").first.wait_for()
    for save in page.locator(".library-paper .shelf-save").all():
        save.click()
    page.keyboard.press("Escape")
    page.locator("#shelfButton").click()
    page.wait_for_function("document.querySelectorAll('.shelf-book').length === 6")
    page.locator(".shelf-book").first.click()
    assert page.locator("#shelfDetail h3").inner_text() == state["library"][0]["title"]
    assert page.get_by_role("link", name="Publisher page", exact=True).get_attribute("href") == "https://doi.org/10.1234/p0"
    page.screenshot(path=str(tmp / "bookshelf-desktop.png"))
    for theme in ("light", "dark"):
        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
        for width in (1280, 390, 320):
            page.set_viewport_size({"width": width, "height": 844})
            assert page.locator("#shelfDialog").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
        page.screenshot(path=str(tmp / f"bookshelf-mobile-{theme}.png"))
    page.set_viewport_size({"width": 1280, "height": 900})
    page.keyboard.press("Escape")
    assert page.locator("#shelfButton").evaluate("el => document.activeElement === el")
    page.reload()
    page.locator("#shelfButton").click()
    page.wait_for_function("document.querySelectorAll('.shelf-book').length === 6")
    page.locator(".shelf-book").first.click()
    page.evaluate("document.querySelector('#message').value = 'Keep my draft'")
    page.locator("#shelfDetail").get_by_role("button", name="Summarize", exact=True).click()
    assert page.locator("#message").input_value() == "Keep my draft"
    assert not state.get("chat_requests")
    page.evaluate("document.querySelector('#message').value = ''")
    page.locator("#shelfDetail").get_by_role("button", name="Summarize", exact=True).click()
    page.wait_for_function("!document.querySelector('[data-job-id^=submission]')")
    assert page.evaluate("navigator.language") == "de-DE"
    assert state["chat_requests"][0]["message"] == "Summarize this paper: 10.1234/p0"
    page.locator("#shelfButton").click()
    page.locator(".shelf-book").first.click()
    page.get_by_role("button", name="Remove from shelf", exact=True).click()
    page.wait_for_function("document.querySelectorAll('.shelf-book').length === 5")
    assert len(state["library"]) == 6
    page.keyboard.press("Escape")
    state["user_id"] = "bob"
    page.reload()
    page.locator("#guideSkip").click()
    page.locator("#shelfButton").click()
    page.wait_for_function("document.querySelector('#shelfNotice').textContent.startsWith('0 papers')")
    assert page.locator(".shelf-book").count() == 0


def test_shelf_empty_browse_and_save_from_chat(browser_page):
    page, state, requests, tmp = browser_page
    page.locator("#guideSkip").click()
    paper = {"paper_id": "P1", "title": "<img src=x onerror=alert(1)>", "author": "Example", "year": "2025", "searchable": False}
    state["library"] = [paper]
    page.locator("#shelfButton").click()
    page.get_by_role("button", name="Browse Library", exact=True).click()
    page.wait_for_function("document.activeElement === document.querySelector('#librarySearch')")
    page.keyboard.press("Escape")
    page.evaluate("paper => document.querySelector('#chat').append(buildSourcesPanel([{...paper, cited: true}]))", paper)
    page.locator("#chat .sources-box > summary").click()
    page.locator("#chat .shelf-save").click()
    page.locator("#shelfButton").click()
    page.locator(".shelf-book").wait_for()
    page.locator(".shelf-book").click()
    assert page.locator("#shelfDetail h3").inner_text() == paper["title"]
    assert page.locator("#shelfDialog img").count() == 0
    assert page.get_by_role('link', name='Open PDF', exact=True).count() == 0
    assert 'No PDF is stored' in page.locator('#shelfDetail').inner_text()
    assert page.locator("#shelfDetail").get_by_role("button", name="Summarize", exact=True).is_disabled()
    page.emulate_media(reduced_motion="reduce")
    assert page.locator(".shelf-book").evaluate("el => parseFloat(getComputedStyle(el).transitionDuration)") <= 0.001


@pytest.mark.parametrize("selector", ["#composerStop", ".job-stop"])
def test_stop_at_progress_and_composer_keeps_draft_and_other_chat(browser_page, selector):
    page, state, requests, tmp = browser_page
    page.locator("#guideSkip").click()
    state["jobs"] = [
        {"job_id": "a" * 32, "session_dir": "first", "state": "running", "can_cancel": True, "message": "Example", "progress": 40},
        {"job_id": "b" * 32, "session_dir": "second", "state": "queued", "can_cancel": True, "message": "Another example"},
    ]
    page.evaluate("jobs => { currentSessionDir='first'; for (const job of jobs) { knownJobStates.set(job.job_id,job.state); rememberTurnJob(job); } addJobMessage(jobs[0]); }", state["jobs"])
    page.locator("#message").fill("Keep this unsent question")
    state["cancel_error"] = True
    page.locator(selector).click()
    page.wait_for_function("document.querySelector('#status').textContent.includes('Please retry')")
    assert page.locator(selector).is_enabled()
    state["cancel_error"] = False
    page.locator(selector).click()
    page.wait_for_function("document.querySelector('.working-stage-label').textContent.startsWith('Stopping')")
    assert page.locator("#composerStop").is_disabled()
    assert page.locator(".job-stop").is_disabled()
    assert state["jobs"][1]["state"] == "queued"
    assert page.locator("#message").input_value() == "Keep this unsent question"
    assert not state.get("chat_requests")
    page.screenshot(path=str(tmp / "stopping-request.png"))
    page.set_viewport_size({'width': 390, 'height': 844})
    page.evaluate('setSidebarOpen(false)')
    page.wait_for_function("document.querySelector('aside').getBoundingClientRect().right <= 1")
    assert page.locator('.working .bubble').evaluate('el => el.scrollWidth <= el.clientWidth + 1')
    assert page.locator('#composerStop').is_visible()
    page.wait_for_function("document.querySelector('.job-stop').getBoundingClientRect().right <= innerWidth")
    page.screenshot(path=str(tmp / 'stopping-request-mobile.png'))
    state["jobs"][0]["state"] = "cancelled"
    page.evaluate("pollJobs()")
    page.wait_for_function("document.querySelector('.working-stage-label').textContent === 'Stopped by you'")
    assert page.locator("#composerStop").is_hidden()
    assert page.locator(".job-stop").is_hidden()
    page.evaluate("currentSessionDir='second'; updateComposerStop()")
    assert page.locator("#composerStop").is_enabled()
    page.locator("#composerStop").click()
    page.wait_for_function("document.querySelector('#composerStop').hidden")
    assert state["jobs"][1]["state"] == "cancelled"


def test_stop_before_server_acknowledges_submission(browser_page):
    page, state, requests, tmp = browser_page
    page.locator('#guideSkip').click()
    pending = []
    page.route('**/api/chat', lambda route: pending.append(route))
    page.locator('#message').fill('Synthetic test question')
    page.locator('#send').click()
    page.locator('[data-job-id^="submission-"] .job-stop').click()
    assert page.locator('#composerStop').is_disabled()
    assert 'waiting for server confirmation' in page.locator('.working-stage-label').inner_text()
    assert len(pending) == 1
    job = {'job_id': 'c' * 32, 'session_dir': page.evaluate('currentSessionDir'), 'state': 'queued', 'can_cancel': True}
    state['jobs'] = [job]
    pending[0].fulfill(status=202, content_type='application/json', body=json.dumps(job))
    page.wait_for_function("!document.querySelector('[data-job-id^=submission]')")
    assert state['cancel_requests'] == [{'job_id': 'c' * 32}]
    assert state['jobs'][0]['state'] == 'cancelled'


@pytest.mark.parametrize('browser_page', ['pdf'], indirect=True)
def test_shelf_opens_local_pdf_not_publisher_page(browser_page):
    page, state, requests, tmp = browser_page
    page.locator('#guideSkip').click()
    state['shelves'] = {'alice': {'P1': {
        'paper_id': 'P1', 'title': 'Previously saved paper', 'author': 'Example',
        'available': True, 'pdf_url': '/api/library/pdf?paper_id=P1',
        'doi_url': 'https://doi.org/10.1234/example', 'summary_available': True,
    }}}
    page.locator('#shelfButton').click()
    page.locator('.shelf-book').click()
    pdf = page.get_by_role('link', name='Open PDF', exact=True)
    assert pdf.get_attribute('href') == '/api/library/pdf?paper_id=P1'
    assert page.locator('#shelfDetail a').first.inner_text() == 'Open PDF'
    assert page.get_by_role('link', name='Publisher page', exact=True).get_attribute('href') == 'https://doi.org/10.1234/example'
    with page.expect_popup() as opened:
        pdf.click()
    reader = opened.value
    reader.wait_for_url('**/api/library/pdf?paper_id=P1', wait_until='domcontentloaded', timeout=10000)
    assert '/api/library/pdf?paper_id=P1' in requests
    assert page.locator('#shelfDialog').evaluate('el => el.open')
    reader.close()
