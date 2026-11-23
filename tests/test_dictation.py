import hashlib
import io
import json
import sys
import threading
from pathlib import Path

import pytest

from originrag import dictation

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def test_verified_installer_is_atomic_and_reuses_verified_file(tmp_path, monkeypatch):
    path = tmp_path / "model.bin"
    monkeypatch.setattr(dictation, "MODEL", path)
    monkeypatch.setattr(dictation, "MODEL_BYTES", 5)
    monkeypatch.setattr(dictation, "SHA256", hashlib.sha256(b"model").hexdigest())
    monkeypatch.setattr(dictation, "urlopen", lambda *a, **kw: io.BytesIO(b"wrong"))
    with pytest.raises(ValueError, match="verification"):
        dictation.prepare()
    assert not path.exists()
    monkeypatch.setattr(dictation, "urlopen", lambda *a, **kw: io.BytesIO(b"model"))
    dictation.prepare()
    monkeypatch.setattr(dictation, "urlopen", lambda *a, **kw: pytest.fail("unexpected download"))
    dictation.prepare()
    assert path.read_bytes() == b"model"


@pytest.mark.parametrize(("audio", "language"), [(b"", "de"), (b"0"*8101, "de"), (b"0"*8000, "path")])
def test_invalid_recordings_do_not_start_worker(audio, language):
    with pytest.raises(ValueError):
        dictation.transcribe(audio, language)


def test_loopback_recording_needs_token_and_does_not_create_turn(tmp_path, monkeypatch):
    import web_chat as web
    from http.client import HTTPConnection
    app = web.WebChatApp(web.WebChatSettings(), str(tmp_path / "sessions"))
    handler = web.build_handler(app)
    monkeypatch.setattr(dictation, "ready", lambda: True)
    calls = []
    def decode(raw, lang):
        calls.append((len(raw), lang))
        return "Welche Studien?"
    monkeypatch.setattr(dictation, "transcribe", decode)
    try:
        server = web.create_server("127.0.0.1", 0, handler)
    except PermissionError:
        handler.turn_job_manager.shutdown()
        pytest.skip("loopback unavailable")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    def request(method, body=None, headers=None):
        client = HTTPConnection(*server.server_address[:2], timeout=5)
        try:
            client.request(method, "/api/dictation", body=body, headers=headers or {})
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()
    try:
        status, data = request("GET")
        assert status == 200 and data["ready"]
        headers = {"Content-Type": "application/octet-stream", "X-Dictation-Language":"de"}
        audio = bytes(16000)
        assert request("POST", audio, headers)[0] == 403
        headers["X-OriginRAG-Local-Token"] = data["token"]
        assert request("POST", b"", headers)[0] == 400
        assert request("POST", audio, headers) == (200, {"text": "Welche Studien?"})
        assert calls == [(16000, "de")]
        assert not list(tmp_path.rglob("turn_*"))
        assert not web.app_profile("write").permits("/api/dictation")
    finally:
        server.shutdown()
        server.server_close()
        handler.turn_job_manager.shutdown()


@pytest.fixture(params=("chromium", "firefox"))
def dictation_page(tmp_path, request):
    playwright = pytest.importorskip("playwright.sync_api")
    import web_chat as web
    handler = web.build_handler(web.WebChatApp(web.WebChatSettings(), str(tmp_path / "sessions")))
    server = web.create_server("127.0.0.1", 0, handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as driver:
            browser = getattr(driver, request.param).launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}")
            yield page
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        handler.turn_job_manager.shutdown()


def test_dictation_records_retries_and_inserts_without_sending(dictation_page, tmp_path):
    page = dictation_page
    page.locator("#guideSkip").click()
    attempts = []
    def speech(route):
        if route.request.method == "GET":
            route.fulfill(json={"ready": True, "token": "speech-test"})
            return
        assert route.request.headers["x-originrag-local-token"] == "speech-test"
        assert route.request.headers["x-dictation-language"] == "de"
        attempts.append(route.request.post_data_buffer)
        route.fulfill(status=429 if len(attempts) == 1 else 200,
                      json={"error":"Busy"} if len(attempts) == 1 else {"text":"Welche Studien?"})
    page.route("**/api/dictation", speech)
    page.evaluate("""() => { navigator.mediaDevices.getUserMedia = async () => {
      const ctx = new AudioContext(); await ctx.resume();
      const osc = ctx.createOscillator(), dest = ctx.createMediaStreamDestination();
      osc.connect(dest); osc.start(); window.testMic = dest.stream; return dest.stream;
    }; }""")
    field = page.locator("#message")
    field.fill("My draft")
    page.get_by_role("button", name="Dictate question").click()
    page.locator("#dictationLanguage").select_option("de")
    page.get_by_role("button", name="Finish dictation").wait_for()
    field.fill("Still editing")
    field.press("Enter")
    page.wait_for_timeout(650)
    page.get_by_role("button", name="Finish dictation").click()
    page.get_by_role("button", name="Retry", exact=True).click()
    from playwright.sync_api import expect
    expect(field).to_have_value("Still editing Welche Studien?")
    assert len(attempts) == 2 and attempts[0] == attempts[1] and len(attempts[0]) >= 8000
    assert page.evaluate("window.testMic.getTracks()[0].readyState") == "ended"
    assert not list(tmp_path.rglob("turn_*"))
    page.get_by_role("button", name="Dictate question").click()
    page.get_by_role("button", name="Finish dictation").wait_for()
    page.keyboard.press("Escape")
    assert field.input_value() == "Still editing Welche Studien?"
    assert page.evaluate("window.testMic.getTracks()[0].readyState") == "ended"
    assert len(attempts) == 2


def test_send_button_stays_fixed_as_draft_grows(dictation_page):
    page = dictation_page
    page.locator("#guideSkip").click()
    field, button = page.locator("#message"), page.locator("#send")
    field.fill("Short draft")
    before, initial = button.bounding_box(), field.bounding_box()
    field.fill("\n".join(["Another line"] * 10))
    assert field.bounding_box()["height"] > initial["height"]
    assert button.bounding_box()["height"] == before["height"]
    assert button.bounding_box()["width"] == before["width"]


def test_microphone_denied_keeps_keyboard_available(dictation_page):
    from playwright.sync_api import expect
    page = dictation_page
    page.locator("#guideSkip").click()
    page.route("**/api/dictation", lambda r: r.fulfill(json={"ready":True, "token":"test"}))
    page.evaluate("""() => { navigator.mediaDevices.getUserMedia = async () => {
        throw new DOMException("Denied", "NotAllowedError");
    }; }""")
    field = page.locator("#message")
    field.fill("Keep this draft.")
    page.locator("#dictate").click()
    expect(page.locator("#dictationNote")).to_contain_text("access denied")
    expect(field).to_have_value("Keep this draft.")
    expect(field).to_be_editable()
