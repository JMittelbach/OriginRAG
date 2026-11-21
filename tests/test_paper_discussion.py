import io
import json
import sqlite3
import sys
import threading
from pathlib import Path

import fitz
import pytest

from originrag.analysis.context_budget import estimate_prompt_tokens
from originrag.paper_discussion import bibliography, discussion_prompt, reference_request
from originrag.paper_discussion import mentioned_papers, refers_to_paper, wants_corpus

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import import_paper_inbox as inbox
import web_chat as web


def article(title="Measuring variation across repeated observations"):
    doc = fitz.open()
    doc.set_metadata({"title": title, "author": "A. Example"})
    doc.new_page().insert_textbox(fitz.Rect(32, 32, 540, 780),
        title + "\nA. Example\nAbstract\nIntroduction\n" +
        "Repeated observations can characterize measurement variability. " * 11 +
        "\nMethods\nWe compared duplicate readings.\nResults\nThe estimates agreed.")
    doc.new_page().insert_text((50, 100), "References\n1. Avery. Precision. 2007.\n2. Banks. Sampling. 2011.")
    raw = doc.tobytes()
    doc.close()
    return raw


def test_discussion_upload_indexes_once_resolves_duplicate_and_declines_other_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "REPO_ROOT", tmp_path)
    mgr = web.LibrarySyncManager(tmp_path / "bib/inbox", auto_worker=False,
                                 read_cache_refresh=lambda: None, refresh_callback=lambda: None)
    def submit(raw):
        token = mgr.store_upload(io.BytesIO(raw), content_length=len(raw), filename="input.pdf",
                                 discussion=True, schedule_import=False)["upload_id"]
        mgr.run_pending_now()
        return json.loads((mgr.inbox / ".discussion" / (token + ".json")).read_text())
    raw = article()
    first = submit(raw)
    assert first["status"] == "accepted", first
    paper = first["paper_id"]
    with sqlite3.connect(tmp_path / "data/indexes/pfempire_fts.sqlite") as db:
        assert db.execute("SELECT count(*) FROM chunk_fts WHERE paper_id=?", (paper,)).fetchone()[0]
    second = submit(raw)
    assert (second["status"], second["paper_id"]) == ("duplicate", paper)
    doc = fitz.open()
    doc.new_page().insert_text((50, 50), "Holiday photo")
    assert submit(doc.tobytes())["status"] == "review_required"
    doc.close()
    with pytest.raises(ValueError, match="signature"):
        submit(b"not a pdf image")
    assert len(list((tmp_path / "data/chunks").glob("*.jsonl"))) == 1
    assert "Banks" in bibliography(tmp_path / "data/extracted_text" / (paper + ".json"), paper)


def test_context_compacts_and_keeps_source_and_recent_turn():
    history = [("Old question " + str(i), "Old response. " * 220) for i in range(24)]
    history.append(("What did the survey measure?", "Height."))
    chunks = [{"page_start": 7, "text": "The survey measured height." * 80}]
    prompt, compacted = discussion_prompt("How?", "Survey", chunks, history, 2048, 400)
    assert compacted and "What did the survey measure?" in prompt and '"page": 7' in prompt
    assert estimate_prompt_tokens(prompt) + 400 + 128 <= 2048
    with pytest.raises(ValueError, match="new chat"):
        discussion_prompt("question " * 700, "Title", chunks, [], 1024, 256)


def test_reference_scope_and_unreadable_list(tmp_path):
    assert reference_request("Zeig mir alle Quellen daraus.")
    assert reference_request("Show all the references.")
    assert not reference_request("What did their survey measure?")
    assert "couldn't reliably" in bibliography(tmp_path / "missing", "ONE")


def test_natural_paper_references_resolve_only_unambiguous_identities():
    a = dict(title="Measuring variation across observations", author="Klein", year="2017", doi="10.1234/aa")
    b = dict(title="Methods for ocean surveys", author="Klein", year="2019", doi="10.1234/bb")
    for question in ("Was sagt Klein (2017)?", "Was steht in „Measuring variation“?",
                     "How did they measure it in https://doi.org/10.1234/aa?"):
        assert mentioned_papers(question, [a, b]) == [a]
    assert mentioned_papers("Tell me about the paper by Klein", [a, b]) == [a, b]
    assert not mentioned_papers("What are ocean surveys?", [a, b])
    assert refers_to_paper("Was waren die wichtigsten Ergebnisse?")
    assert not refers_to_paper("What is quantum tunnelling?")
    assert wants_corpus("Welche anderen Studien zeigen das auch?")


def test_clarifying_a_title_keeps_the_original_question(tmp_path):
    papers = tmp_path / "papers.tsv"
    papers.write_text("paper_id\ttitle\tauthor\tyear\nA\tA first survey\tKlein\t2017\nB\tA second survey\tKlein\t2017\n")
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    extracted = tmp_path / "extracted_text"
    extracted.mkdir()
    for key in ("A", "B"):
        (chunks / (key + ".jsonl")).write_text(json.dumps({"paper_id": key, "text": "Example."}))
        (extracted / (key + ".json")).write_text(json.dumps({"paper_id": key, "pages": [
            {"page": 2, "text": "References\n1. Reference for " + key}]}))
    app = web.WebChatApp(web.WebChatSettings(papers=str(papers), chunks_dir=str(chunks)), str(tmp_path / "sessions"))
    assert "Which title" in app.run_turn("Show all references in Klein (2017).", {})["answer"]
    result = app.run_turn("The second one", {})
    assert result["paper_id"] == "B" and "Reference for B" in result["answer"]


def test_cited_doi_cannot_identify_an_unrelated_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(inbox, "fetch_crossref", lambda *a, **kw: {
        "title": "Completely different article", "paper_type": "journal article"})
    mgr = web.LibrarySyncManager(tmp_path / "bib/inbox", auto_worker=False, read_cache_refresh=lambda: None,
                                 refresh_callback=lambda: None)
    raw = article()
    mgr.store_upload(io.BytesIO(raw), content_length=len(raw), filename="paper.pdf",
                     discussion=True, doi="10.1234/unrelated", schedule_import=False)
    result = mgr.run_pending_now()
    assert result["review_required"] == 1 and not result["imported"]


def test_browser_attach_upload_read_reload_and_detach(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    monkeypatch.setattr(inbox, "REPO_ROOT", tmp_path)
    settings = web.WebChatSettings(papers=str(tmp_path / "data/metadata/papers.tsv"),
                                   chunks_dir=str(tmp_path / "data/chunks"))
    catalogue = web.library_catalog
    monkeypatch.setattr(web, "library_catalog", lambda *args, **kw: catalogue(
        str(tmp_path / "data/metadata/paper_registry.tsv"), settings.papers,
        str(tmp_path / "bib"), settings.chunks_dir, for_chat=kw.get("for_chat", False)))
    app = web.WebChatApp(settings, str(tmp_path / "sessions"))
    corpus_requests = []
    def corpus_reply(args, turn_dir, progress_callback=None):
        corpus_requests.append(args.retrieval_question)
        turn_dir.mkdir(parents=True)
        path = turn_dir / "answer.md"
        path.write_text("Searched across the library.")
        return {"answer_path": path, "pack": {"chunks": []}, "answer_qc": {"passed": True}}
    monkeypatch.setattr(web.ask_corpus, "run_question", corpus_reply)
    mgr = web.LibrarySyncManager(tmp_path / "bib/inbox", auto_worker=False, read_cache_refresh=lambda: None,
                                 refresh_callback=lambda: None)
    handler = web.build_handler(app, library_manager=mgr)
    try:
        server = web.create_server("127.0.0.1", 0, handler)
    except PermissionError:
        handler.turn_job_manager.shutdown()
        pytest.skip("loopback unavailable")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pw.sync_playwright() as driver:
            browser = driver.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.locator("#guideSkip").click()
            pw.expect(page.locator("#focusUpload")).to_be_visible()
            with page.expect_response(lambda r: "/api/library-upload" in r.url) as response:
                page.locator("#focusFile").set_input_files(dict(name="research.pdf", mimeType="application/pdf", buffer=article()))
            assert response.value.status == 202, response.value.text()
            mgr.run_pending_now()
            catalogue.cache_clear()
            pw.expect(page.locator("#focusTitle")).to_contain_text("Measuring variation")
            page.locator("#message").fill("Show all references.")
            page.locator("#send").click()
            pw.expect(page.locator("#chat")).to_contain_text("Banks. Sampling.", timeout=15000)
            page.reload()
            pw.expect(page.locator("#focusClear")).to_be_hidden()
            pw.expect(page.locator("#chat")).to_contain_text("Banks. Sampling.")
            page.locator("#message").fill("Zeig mir nochmal alle Quellen daraus.")
            page.locator("#send").click()
            pw.expect(page.locator("#chat .paper-discussion")).to_have_count(2)
            page.locator("#message").fill("Welche anderen Studien zeigen das auch?")
            page.locator("#send").click()
            pw.expect(page.locator("#chat")).to_contain_text("Searched across the library.", timeout=15000)
            assert len(corpus_requests) == 1 and "Measuring variation" in corpus_requests[0]
            page.locator("#newChat").click()
            pw.expect(page.locator("#focusClear")).to_be_hidden()
            page.locator("#message").fill("Show all references from Measuring variation across repeated observations.")
            page.locator("#send").click()
            pw.expect(page.locator("#chat")).to_contain_text("Banks. Sampling.")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        handler.turn_job_manager.shutdown()
