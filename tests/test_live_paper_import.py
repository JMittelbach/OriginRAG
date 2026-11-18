import csv
import json
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import import_paper_inbox as inbox  # noqa: E402
import web_chat  # noqa: E402


pytestmark = pytest.mark.skipif(
    os.environ.get("ORIGINRAG_LIVE_IMPORT_TESTS") != "1",
    reason="Opt-in live publisher downloads and Chromium: ORIGINRAG_LIVE_IMPORT_TESTS=1",
)
CONTROLS = [
    ("10.1093/bioinformatics/bty560", "fastp an ultra-fast all-in-one FASTQ preprocessor", "Bioinformatics", "fastp"),
    ("10.1093/bioinformatics/btw354", "MultiQC summarize analysis results for multiple tools and samples in a single report", "Bioinformatics", "MultiQC"),
    ("10.1038/s41586-021-03819-2", "Highly accurate protein structure prediction with AlphaFold", "Nature", "AlphaFold"),
    ("10.1038/s41586-024-07487-w", "Accurate structure prediction of biomolecular interactions with AlphaFold 3", "Nature", "AlphaFold"),
]


@pytest.mark.parametrize("doi,query,journal,term", CONTROLS, ids=["bioinformatics-fastp", "bioinformatics-multiqc", "nature-alphafold2", "nature-alphafold3"])
def test_live_find_click_import_and_search(tmp_path, monkeypatch, doi, query, journal, term):
    try:
        import playwright.sync_api as playwright
    except ImportError:
        pytest.fail("Live controls require playwright and its Chromium browser")
    monkeypatch.setattr(inbox, "REPO_ROOT", tmp_path)
    from init_local_data import initialise
    initialise(tmp_path)
    original_catalog = web_chat.library_catalog

    def catalogue():
        return original_catalog(str(tmp_path / "data/metadata/paper_registry.tsv"),
                                str(tmp_path / "data/metadata/papers.tsv"),
                                str(tmp_path / "bib"), str(tmp_path / "data/chunks"))

    monkeypatch.setattr(web_chat, "library_catalog", catalogue)
    library = web_chat.LibrarySyncManager(tmp_path / "bib/inbox", auto_worker=False,
                                         refresh_callback=original_catalog.cache_clear,
                                         read_cache_refresh=lambda: None)
    app = web_chat.WebChatApp(web_chat.WebChatSettings(backend="template"), tmp_path / "sessions")
    handler = web_chat.build_handler(app, library_manager=library)
    server = web_chat.create_server("127.0.0.1", 0, handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {"doi": doi, "expected_journal": journal, "query": query, "passed": False}
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page_errors = []
                uploads = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.on("request", lambda req: uploads.append(req.url) if "/api/library-upload" in req.url else None)
                page.goto(f"http://127.0.0.1:{server.server_address[1]}")
                page.locator("#guideSkip").click(timeout=20000)
                page.locator("#discoveryButton").click()
                page.locator("#discoveryQuery").fill(query)
                page.locator("#journalQuery").fill(journal)
                page.locator("#journalOptions label").filter(has_text=re.compile(r"^" + journal + r" ·")).locator("input").first.check(timeout=20000)
                with page.expect_response(lambda response: "/api/discover-papers?" in response.url, timeout=25000) as search:
                    page.locator("#discoverySearch").click()
                results = search.value.json()
                matching = [item for item in results.get("items", []) if item["doi"] == doi]
                report["search_status"] = search.value.status
                assert matching, results
                assert matching[0]["journal"] == journal, matching[0]
                report["title"] = matching[0]["title"]
                row = page.locator(".discovery-item").filter(has=page.locator(f'a[href="https://doi.org/{doi}"]'))
                start = time.monotonic()
                with page.expect_response(lambda response: response.url.endswith("/api/paper-import")) as added:
                    row.get_by_role("button", name="Add to library", exact=True).click()
                assert added.value.status == 202, added.value.text()
                job_id = added.value.json()["job_id"]
                page.wait_for_function("""doi => {
                    const link = [...document.querySelectorAll('.discovery-item > a')].find(a => a.href === 'https://doi.org/' + doi);
                    const button = link?.closest('.discovery-item').querySelector('button');
                    return button && ['In library', 'Retry import'].includes(button.textContent);
                }""", arg=doi, polling=500, timeout=125000)
                jobs = page.request.get(f"http://127.0.0.1:{server.server_address[1]}/api/paper-imports").json()["jobs"]
                job = next(item for item in jobs if item["job_id"] == job_id)
                report.update(job=job, import_seconds=round(time.monotonic() - start, 2))
                row.screenshot(path=str(tmp_path / "import-result.png"))
                assert job["state"] == "imported", job
                row.get_by_role("button", name="In library", exact=True).wait_for(timeout=10000)
                assert not uploads
                assert not page.locator("#paperUploadBackdrop").evaluate("el => el.classList.contains('open')")
                assert not page_errors, page_errors
            finally:
                browser.close()
        with (tmp_path / "data/metadata/papers.tsv").open() as stream:
            papers = list(csv.DictReader(stream, delimiter="\t"))
        assert len(papers) == 1
        paper = papers[0]
        with (tmp_path / "data/metadata/paper_registry.tsv").open() as stream:
            registry = list(csv.DictReader(stream, delimiter="\t"))
        assert len(registry) == 1
        assert registry[0]["journal"] == journal and registry[0]["doi"] == doi
        assert paper["doi"] == doi
        assert paper["fulltext_status"] == "chunked"
        pdf = tmp_path / "bib" / paper["local_pdf_relpath"]
        assert pdf.read_bytes().startswith(b"%PDF-")
        with sqlite3.connect(tmp_path / "data/indexes/pfempire_fts.sqlite") as db:
            chunks = db.execute("SELECT COUNT(*) FROM chunk_fts WHERE paper_id = ?", (paper["paper_id"],)).fetchone()[0]
            hits = db.execute("SELECT COUNT(*) FROM chunk_fts WHERE chunk_fts MATCH ? AND paper_id = ?", (term, paper["paper_id"])).fetchone()[0]
        assert chunks > 0 and hits > 0
        assert any(item["doi"] == doi and item["searchable"] and item["local_pdf"] for item in catalogue())
        report.update(passed=True, pdf_bytes=pdf.stat().st_size, chunks=chunks, search_hits=hits,
                      pdf_path=paper["local_pdf_relpath"])
    finally:
        server.shutdown()
        server.server_close()
        handler.paper_import_manager.shutdown()
        handler.turn_job_manager.shutdown()
        thread.join(timeout=5)
        original_catalog.cache_clear()
        (tmp_path / "control-result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report), flush=True)
