from __future__ import annotations

import io
from pathlib import Path
import sys

import pytest
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import browse_library as library  # noqa: E402


ROWS = [
    {"paper_id": "A", "title": "A paper on Evidence", "author": "Müller, Jane", "year": "2024", "doi": "10.1000/a"},
    {"paper_id": "B", "title": "Another evidence paper", "author": "Turner, Bob", "year": "2026"},
    {"paper_id": "C", "title": "Other work", "author": "Turner, Bob", "year": "2020"},
]


def test_filters_author_title_and_general_terms():
    assert library.filter_papers(ROWS, author="muller") == [ROWS[0]]
    assert library.filter_papers(ROWS, title="EVIDENCE") == [ROWS[1], ROWS[0]]
    assert library.filter_papers(ROWS, query="turner evidence") == [ROWS[1]]
    assert library.filter_papers(ROWS, query="10.1000/a") == [ROWS[0]]
    assert library.filter_papers(ROWS, author="turner", title="other work") == [ROWS[2]]
    assert library.filter_papers(ROWS, title="no match") == []


def test_local_pdf_checks_real_file_and_prevents_path_escape(tmp_path):
    root = tmp_path / "bib"
    root.mkdir()
    (root / "yes.pdf").write_bytes(b"example")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"example")
    (root / "linked.pdf").symlink_to(outside)
    assert library.local_pdf({"local_pdf_relpath": "yes.pdf"}, root)
    for path in ("", "missing.pdf", "../outside.pdf", str(outside), "linked.pdf"):
        assert not library.local_pdf({"local_pdf_relpath": path}, root)


def test_library_prints_complete_titles_and_no_terminal_controls(tmp_path):
    stream = io.StringIO()
    title = "A very long paper title " * 10
    library.print_library(Console(file=stream, width=50),
                          [{"title": title, "author": "Name\x1b[2J", "doi": "10.1000/test"}],
                          total=5, bib_root=tmp_path)
    text = stream.getvalue()
    assert "1 of 5 catalogued papers" in text
    assert "10.1000/test" in text
    assert text.count("long paper title") == 10
    assert "No local PDF" in text
    assert "\x1b" not in text


def test_browser_uses_enriched_authors_and_configured_pdf_root(tmp_path, monkeypatch, capsys):
    papers = tmp_path / "papers.tsv"
    papers.write_text("paper_id\ttitle\tlocal_pdf_relpath\nA\tExample title\tlocal.pdf\n")
    (tmp_path / "corpus_manifest.tsv").write_text("paper_id\tauthor\nA\tMüller, Jane\n")
    (tmp_path / "local.pdf").write_bytes(b"example")
    monkeypatch.setenv("ORIGINRAG_BIB_DIR", str(tmp_path))
    original = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    monkeypatch.setattr(library.LibraryBrowser, "run", lambda *_: pytest.fail("Piped output invoked browser"))
    assert library.main(["--papers", str(papers), "--author", "muller", "--local-only"]) == 0
    output = capsys.readouterr().out
    assert "Müller, Jane" in output
    assert "PDF local" in output
    assert original == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_missing_catalogue_is_not_reported_as_empty(tmp_path, capsys):
    with pytest.raises(SystemExit, match="2"):
        library.main(["--papers", str(tmp_path / "missing.tsv")])
    assert "not found" in capsys.readouterr().err


def test_library_includes_registered_papers_without_retrieval_ids(tmp_path):
    papers = tmp_path / "papers.tsv"
    papers.write_text("paper_id\ttitle\tlocal_pdf_relpath\nA\tIndexed\ta.pdf\n")
    (tmp_path / "paper_registry.tsv").write_text(
        "paper_id\tbib_key\ttitle\tlocal_pdf_relpath\n"
        "A\tAlpha\tIndexed\ta.pdf\n"
        "\tBeta\tNot indexed yet\tb.pdf\n"
        "\tAlias\tSame local file\ta.pdf\n"
    )
    rows = library.load_catalogue(papers)
    assert len(rows) == 2
    assert rows[1]["title"] == "Not indexed yet"


def test_browser_live_filter_clear_and_help(tmp_path):
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.output import DummyOutput

    browser = library.LibraryBrowser(ROWS, total=3, bib_root=tmp_path,
                                     input=DummyInput(), output=DummyOutput())
    assert len(browser.matches) == 3
    browser.search.text = "muller"
    assert browser.matches == [ROWS[0]]
    assert "Müller" in browser.results.text
    browser.search.text = "turner evidence"
    assert browser.matches == [ROWS[1]]
    browser.search.text = "does not exist"
    assert not browser.matches
    assert "No matching papers" in browser.results.text
    browser.search.text = "/help"
    assert "LIBRARY HELP" in browser.results.text
    assert "/exit + Enter" in browser.results.text
    browser.search.text = ""
    assert len(browser.matches) == 3


@pytest.mark.parametrize("exit_keys", ["/exit\r", "exit\r", "/quit\r", "q\r", "\x1b", "\x03", "\x04", "\t\t\r"])
def test_interactive_search_and_every_exit_route(tmp_path, exit_keys):
    import asyncio
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe:
        browser = library.LibraryBrowser(ROWS, total=3, bib_root=tmp_path,
                                         input=pipe, output=DummyOutput())

        async def run():
            task = asyncio.create_task(browser.app.run_async())
            try:
                await asyncio.sleep(0.02)
                pipe.send_text("Müller")
                await asyncio.sleep(0.03)
                assert browser.matches == [ROWS[0]]
                pipe.send_text("\x15Turner evidence")
                await asyncio.sleep(0.03)
                assert browser.matches == [ROWS[1]]
                pipe.send_text("\x1b[6~\x1b[5~\x15" + exit_keys)
                await asyncio.wait_for(task, timeout=3)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        asyncio.run(run())


def test_typing_after_scrolling_or_focusing_results_still_searches(tmp_path):
    import asyncio
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe:
        browser = library.LibraryBrowser(ROWS, total=3, bib_root=tmp_path,
                                         input=pipe, output=DummyOutput())

        async def run():
            task = asyncio.create_task(browser.app.run_async())
            try:
                await asyncio.sleep(0.02)
                pipe.send_text("\x1b[B\x1b[B")
                await asyncio.sleep(0.03)
                assert browser.results.buffer.cursor_position > 0
                pipe.send_text("\tTurner")
                await asyncio.sleep(0.03)
                assert len(browser.matches) == 2
                assert browser.app.layout.has_focus(browser.search)
                pipe.send_text("\x15/exit\r")
                await asyncio.wait_for(task, timeout=3)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        asyncio.run(run())
