import io
import json
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

from originrag.analysis.context_budget import estimate_prompt_tokens
from originrag.paper_discussion import align_quoted_pages, bibliography, discussion_prompt, extracted_pages, reference_request
from originrag.paper_discussion import mentioned_papers, refers_to_paper, unresolved_report_reference, wants_corpus

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import import_paper_inbox as inbox
import web_chat as web


def test_pdf_fallback_is_complete_or_empty_and_preserves_page_numbers():
    from originrag.paper_discussion import pdf_text_pages
    with fitz.open() as document:
        document.new_page().insert_text((50, 60), "Observations from the first page.")
        document.new_page()
        document.new_page().insert_text((50, 60), "Important limitations on the third page.")
        data = document.tobytes()
    assert [p["page"] for p in pdf_text_pages(io.BytesIO(data), 1000)] == [1, 3]
    assert pdf_text_pages(io.BytesIO(data), 20) == []
    assert pdf_text_pages(io.BytesIO(data), 1000, max_bytes=100) == []
    assert pdf_text_pages(io.BytesIO(b"not a PDF"), 1000) == []


def article(title="Measuring variation across repeated observations"):
    doc = fitz.open()
    doc.set_metadata({"title": title, "author": "A. Example"})
    doc.new_page().insert_textbox(fitz.Rect(32, 32, 540, 780),
        title + "\nA. Example\nAbstract\nIntroduction\n" +
        "Repeated observations can characterize measurement variability. " * 11 +
        "\nMethods\nWe enrolled 39 participants and compared 12 matched pairs.\nResults\nThe estimates agreed.")
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


def test_journal_and_identifier_are_joint_clues_not_a_topic_search():
    a = dict(title="A comparison of X9B responses", journal="Nature Communications")
    b = dict(title="X9B and its seasonal variability", journal="Ecology")
    c = dict(title="Different X9B outcomes", journal="Nature Communications")
    question = "Kennst du das neue x9b Nature Comm Paper? Wie waren die Kohorten?"
    assert mentioned_papers(question, [a, b]) == [a]
    assert mentioned_papers(question, [a, b, c]) == [a, c]
    a.update(year="2024")
    c.update(year="2018")
    assert mentioned_papers(question, [a, b, c]) == [a]
    c.update(year="2024")
    assert mentioned_papers(question, [a, b, c]) == [a, c]
    assert not mentioned_papers("Find X9B across studies", [a, b])
    assert refers_to_paper("Und die Kohortenstruktur?")


def test_whole_extraction_or_budgeted_chunks_never_a_different_paper(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps(dict(paper_id="p", pages=[dict(page=1, text="First page."),
                                                        dict(page=2, text="Final page.")])))
    assert not extracted_pages(path, "other", 4096)
    chunks = [dict(text="Indexed fallback.")]
    pages = extracted_pages(path, "p", 4096)
    prompt, _ = discussion_prompt("Who?", "Study", chunks, [], 4096, 400, pages=pages)
    assert "First page." in prompt and "Final page." in prompt and "Indexed fallback." not in prompt
    assert "requested_detail" not in prompt
    question = "Was war das Ergebnis? Erklär die Kohrotne."
    prompt, _ = discussion_prompt(question, "Study", chunks, [], 4096, 400, pages=pages)
    payload = json.loads(prompt.split("\n", 1)[1])
    assert payload["question"] == question and "case and control counts" in payload["requested_detail"]
    pages[0]["text"] *= 4000
    prompt, _ = discussion_prompt("Who?", "Study", chunks, [], 2048, 400, pages=pages)
    assert "Indexed fallback." in prompt and estimate_prompt_tokens(prompt) + 528 <= 2048


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


@pytest.mark.parametrize("question,expected", [
    ("Were there 2020 participants in this study?", False),
    ("Did they recruit participants in 2020?", False),
    ("How did this compare with Smith (2020)?", True),
    ("Explain the paper by Smith", True),
    ("What did the 2020 paper show?", True),
    ("What about the paper published in 2020?", True),
    ("What did they report in 10.1234/unknown?", True),
])
def test_counts_and_study_dates_are_not_new_paper_identifiers(question, expected):
    assert unresolved_report_reference(question) == expected


@pytest.mark.parametrize("question", [
    "Und die 12 Paare – sind die zusätzlich? Zeig mir die Stelle.",
    "How were the groups recruited?",
    "Where exactly does it say that?",
    "Wie waren die Kohorten aufgebaut?",
])
def test_natural_design_followups_keep_focus(question):
    assert refers_to_paper(question) and not wants_corpus(question)
    assert wants_corpus("Can you find other papers with those 12 pairs?")
    assert not refers_to_paper("Explain the 12 principles of design.")


def test_design_source_notes_are_from_supplied_pages_only():
    primary = "We performed unmatched comparisons of 39 patients and 65 controls and paired comparisons of 12 pairs."
    pages = [{"page": 4, "text": primary}, {"page": 7, "text": primary}]
    notes = []
    prompt, _ = discussion_prompt("Which control groups?", "Example", [], [], 4096, 450,
                                  pages=pages, source_notes=notes)
    payload = json.loads(prompt.split("\n", 1)[1])
    assert notes == payload["study_design_passages"] == [{"page": 4, "text": primary}]
    assert estimate_prompt_tokens(prompt) <= 4096 - 450 - 128
    notes.clear()
    prompt, _ = discussion_prompt("How was rainfall measured?", "Example", [], [], 4096, 450,
                                  pages=pages, source_notes=notes)
    assert not notes and "study_design_passages" not in prompt


def test_only_unique_verbatim_quote_can_correct_an_adjacent_page():
    sentence = "We compared twelve pairs selected from the full sample."
    pages = [{"page": 2, "text": sentence.replace("selected from", "selected\nfrom")}]
    answer = 'Explanation [Seite 6]. "' + sentence.replace("twelve", "**twelve**") + '" [Seite 4].'
    assert align_quoted_pages(answer, pages) == answer.replace("Seite 4", "Seite 2")
    assert align_quoted_pages(answer, pages + [{"page": 5, "text": sentence}]) == answer
    paraphrase = answer.replace("full sample", "enrolled sample")
    assert align_quoted_pages(paraphrase, pages) == paraphrase


def test_printed_page_correction_requires_consistent_original_page_headers():
    from originrag.paper_discussion import align_printed_page_citations
    pages = [{"page": p, "text": f"Journal volume 6\n{122+p}\nPerspective\nAuthors\n"
              "Introduction\nFirst paragraph\nSecond paragraph\nThird paragraph\nConclusion"} for p in range(1, 5)]
    answer = "Observation [PDF p. 123]. Another [PDF p. 126]. Already correct [PDF p. 2]. Unknown [PDF p. 999]."
    expected = answer.replace("p. 123", "p. 1").replace("p. 126", "p. 4")
    assert align_printed_page_citations(answer, pages) == expected
    assert align_printed_page_citations(answer, pages[:2]) == answer
    assert align_printed_page_citations(answer, [pages[0], pages[2], pages[3]]) == answer
    assert align_printed_page_citations(answer, [{"page": 1, "text": "One paragraph ending in 123."}]) == answer
    code = '`[PDF p. 123]`\n```text\n[PDF p. 126]\n```\n[PDF p. 123](https://example.org/)'
    assert align_printed_page_citations(code, pages) == code


def test_ambiguous_marginal_page_numbers_are_not_relabelled():
    from originrag.paper_discussion import align_printed_page_citations
    pages = [{"page": p, "text": f"{122+p}\n{124+p}\nJournal\nArticle\nIntroduction\n"
              "First paragraph\nSecond paragraph\nThird paragraph\nConclusion"} for p in range(1, 7)]
    answer = "Ambiguous [PDF p. 125]. Unambiguous [PDF p. 123]."
    assert align_printed_page_citations(answer, pages) == answer.replace("p. 123", "p. 1")
    fixed_year = [{"page": p, "text": "2026\nJournal\nArticle\nIntroduction\n"
                   "First paragraph\nSecond paragraph\nThird paragraph\nConclusion"} for p in range(1, 7)]
    assert align_printed_page_citations("Statement [PDF p. 2026].", fixed_year) == "Statement [PDF p. 2026]."


def test_group_breakdown_is_not_crowded_out_by_repeated_comparisons():
    breakdown = "We analyzed 84 samples (from 60 standard controls and 24 controls with previous exposure) of adequate quality."
    pages = [{"page": i, "text": f"We performed matched comparisons between 20 cases and 20 controls at site {i}."}
             for i in range(1, 9)]
    pages.append({"page": 10, "text": breakdown})
    notes = []
    discussion_prompt("Explain the cohort structure.", "A survey", [], [], 8192, 600, pages=pages, source_notes=notes)
    assert {"page": 10, "text": breakdown} in notes
    assert len(notes) <= 6


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


@pytest.mark.parametrize("second_cited", [False, True])
def test_this_paper_after_regular_answer_uses_citations_not_retrieval(tmp_path, monkeypatch, second_cited):
    papers = tmp_path / "papers.tsv"
    papers.write_text("paper_id\ttitle\nA\tFirst survey report\nB\tSecond survey report\n")
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    for pid in ("A", "B"):
        (chunks / (pid + ".jsonl")).write_text(json.dumps({"paper_id": pid, "text": "Example."}))
    app = web.WebChatApp(web.WebChatSettings(papers=str(papers), chunks_dir=str(chunks)), str(tmp_path / "sessions"))
    turn = app.session_dir / "turn_001_example"
    turn.mkdir()
    (turn / "public_result.json").write_text(json.dumps({"task": "answer", "answer": "Earlier answer.",
        "sources": [{"paper_id": "A", "cited": True}, {"paper_id": "B", "cited": second_cited}]}))
    web.append_turn_summary(app.session_dir, "Earlier question", "Earlier question", "answer", {}, turn)
    monkeypatch.setattr(app, "discuss", lambda session, question, settings, pid, *args, **kw: {"paper_id": pid})
    result = app.run_turn("rklr mir bie dem paper genauer bitte was das mit dem Vergleich auf sich hat", {})
    if second_cited:
        assert "Which title" in result["answer"]
    else:
        assert result["paper_id"] == "A"


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


@pytest.mark.parametrize("browser_name", ["chromium", "firefox"])
def test_browser_drop_upload_read_reload_and_detach(tmp_path, monkeypatch, browser_name):
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
    monkeypatch.setattr(web.ask_corpus, "effective_model_context", lambda *a, **kw: 8192)
    monkeypatch.setattr(web.ask_corpus, "generate_answer_with_backend", lambda *a, **kw:
                        SimpleNamespace(llm_result=SimpleNamespace(answer="**Result:** *paired* subset. [PDF p. 1]\n\n"
                            "* Literal: `*paired*`\n* [Paper](https://doi.org/10.1234/example)", error=None)))
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
            browser = getattr(driver, browser_name).launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.locator("#guideSkip").click()
            pw.expect(page.locator("#paperUploadButton")).to_be_visible()
            pw.expect(page.locator("#form button[aria-label='Attach PDF'], #form input[type=file]")).to_have_count(0)
            draft = page.locator("#message")
            draft.fill("Show all references.")
            def drag(filename, content, mime):
                return page.evaluate_handle("""([name, bytes, type]) => {
                    const data = new DataTransfer();
                    data.items.add(new File([new Uint8Array(bytes)], name, {type}));
                    return data;
                }""", [filename, list(content), mime])
            bad = drag("picture.png", b"test", "image/png")
            draft.dispatch_event("drop", {"dataTransfer": bad})
            pw.expect(page.locator("#paperDropNote")).to_have_text("Drop one research PDF at a time.")
            pdf = drag("research.pdf", article(), "application/pdf")
            page.locator("#chat").dispatch_event("dragenter", {"dataTransfer": pdf})
            pw.expect(page.locator("#paperDropNote")).to_contain_text("Drop a research PDF")
            with page.expect_response(lambda r: "/api/library-upload" in r.url) as response:
                draft.dispatch_event("drop", {"dataTransfer": pdf})
            assert response.value.status == 202, response.value.text()
            mgr.run_pending_now()
            catalogue.cache_clear()
            pw.expect(page.locator("#focusTitle")).to_contain_text("Measuring variation")
            pw.expect(draft).to_have_value("Show all references.")
            pw.expect(page.locator("#chat .msg.user")).to_have_count(0)
            page.locator("#send").click()
            pw.expect(page.locator("#chat")).to_contain_text("Banks. Sampling.", timeout=15000)
            page.reload()
            pw.expect(page.locator("#focusClear")).to_be_hidden()
            pw.expect(page.locator("#chat")).to_contain_text("Banks. Sampling.")
            page.locator("#message").fill("Zeig mir nochmal alle Quellen daraus.")
            page.locator("#send").click()
            pw.expect(page.locator("#chat .paper-discussion")).to_have_count(2)
            page.locator("#message").fill("Und die 12 Paare – waren die zusätzlich?")
            page.locator("#send").click()
            design = page.locator("#chat details").filter(has_text="Study design in the original paper")
            pw.expect(design.locator("summary")).to_be_visible()
            design.locator("summary").click()
            pw.expect(design.locator("blockquote").first).to_contain_text("12 matched pairs")
            response = page.locator("#chat .paper-discussion").last
            pw.expect(response.locator("em")).to_have_text("paired")
            pw.expect(response.locator("strong")).to_have_text("Result:")
            pw.expect(response.locator("li code")).to_have_text("*paired*")
            pw.expect(response.locator("li a")).to_have_attribute("href", "https://doi.org/10.1234/example")
            page_link = response.locator(".paper-page-citation")
            pw.expect(page_link).to_have_attribute("target", "_blank")
            assert page_link.get_attribute("href").endswith("&page=1")
            page.reload()
            pw.expect(page.locator("#chat .paper-discussion").last.locator("em")).to_have_text("paired")
            assert page.evaluate("""() => {
              const box = document.createElement('div');
              renderMarkdown(box, '**Report:** *var2csa* ([[cite:E001]]) and `[[cite:E001]]`.', {
                sources: [{evidence_ids: ['E001'], citation_short: 'Example et al.', url: 'https://doi.org/10.1234/test'}]
              });
              return [box.querySelector('em').textContent, box.querySelector('a.inline-citation').textContent,
                      box.querySelector('code').textContent, box.querySelectorAll('a').length];
            }""") == ["var2csa", "Example et al.", "[[cite:E001]]", 1]
            assert page.evaluate("""() => {
              const box = document.createElement('div');
              renderMarkdown(box, '[PDF p. 2] [PDF p. 99] `[PDF p. 1]` **[Seite 1]**', {
                task: 'paper_discussion', paper_id: 'P1', source_pages: [1, 2]
              });
              return [box.querySelectorAll('a').length,
                      box.querySelector('a').getAttribute('href'), box.querySelector('code').textContent,
                      box.querySelector('strong a').textContent, box.textContent.includes('[PDF p. 99]')];
            }""") == [2, "/reader?paper_id=P1&page=2", "[PDF p. 1]", "[Seite 1]", True]
            assert page.evaluate("""() => {
              const box = document.createElement('div');
              renderMarkdown(box, '[PDF p. 1; PDF p. 2] [PDF p. 1, 99] `[PDF p. 1, 2]`', {
                task: 'paper_discussion', paper_id: 'P1', source_pages: [1, 2]
              });
              return [box.querySelectorAll('a').length, box.querySelector('code').textContent,
                      box.textContent.includes('[PDF p. 99]')];
            }""") == [3, "[PDF p. 1, 2]", True]
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
