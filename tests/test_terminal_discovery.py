import io
from pathlib import Path
import sys

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import find_papers  # noqa: E402
from originrag.utils.terminal_ui import TerminalUI  # noqa: E402


def terminal(monkeypatch, commands):
    output = io.StringIO()
    ui = TerminalUI(plain=True, animate=False)
    ui.console = Console(file=output, width=100, force_terminal=False)
    answers = iter(commands)
    monkeypatch.setattr(ui.console, "input", lambda *a, **k: next(answers))
    return ui, output


def test_finder_selects_journals_searches_and_adds_explicitly(monkeypatch):
    calls, imports = [], []
    ui, output = terminal(monkeypatch, ["/journals Nature", "/select 1", "graph theory", "/add 1", "/clear", "/exit"])
    monkeypatch.setattr(find_papers, "search_journals", lambda query: {"items": [{"title": "Nature", "publisher": "Publisher", "issns": ["0028-0836"]}]})

    def search(query, **kwargs):
        calls.append((query, kwargs))
        return {"items": [{"title": "Graph theory", "doi": "10.1234/test", "author": "Example", "year": "2025", "journal": "Nature", "url": "https://doi.org/10.1234/test"}]}

    monkeypatch.setattr(find_papers, "search_topic", search)
    monkeypatch.setattr(find_papers, "import_paper", lambda ui, **kwargs: imports.append(kwargs))
    find_papers.run_finder(ui)
    assert calls[0][0] == "graph theory"
    assert calls[0][1]["issns"] == ["0028-0836"]
    assert imports == [{"doi": "10.1234/test"}]
    assert "Journal restrictions cleared" in output.getvalue()


def test_import_cancellation_neither_downloads_nor_writes(tmp_path, monkeypatch):
    ui, _ = terminal(monkeypatch, ["n"])
    monkeypatch.setattr(find_papers, "ROOT", tmp_path)
    monkeypatch.setattr(find_papers, "download_open_pdf", lambda *a: (_ for _ in ()).throw(AssertionError("Must not download")))
    assert not find_papers.import_paper(ui, doi="10.1234/test")
    assert list(tmp_path.iterdir()) == []


def test_terminal_pdf_import_uses_real_indexer_without_moving_original(tmp_path, monkeypatch):
    from test_metadata_fulltext_upgrade import setup_library
    source, _ = setup_library(tmp_path, monkeypatch)
    ui, output = terminal(monkeypatch, ["yes"])
    monkeypatch.setattr(find_papers, "ROOT", tmp_path)
    assert find_papers.import_paper(ui, source=source)
    assert source.is_file()
    assert (tmp_path / "data/chunks/EXISTING_1.jsonl").is_file()
    assert "Added to your library and indexed" in output.getvalue()


def test_custom_corpus_cannot_be_written_to_wrong_location(monkeypatch):
    ui, output = terminal(monkeypatch, ["/import example.pdf", "/exit"])
    monkeypatch.setattr(find_papers, "import_paper", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Must not import")))
    find_papers.run_finder(ui, allow_import=False)
    assert "custom corpus paths" in output.getvalue()


def test_failed_search_clears_previous_add_targets(monkeypatch):
    ui, output = terminal(monkeypatch, ["anything", "/add 1", "/exit"])
    monkeypatch.setattr(find_papers, "search_topic", lambda *a, **k: (_ for _ in ()).throw(find_papers.DiscoveryUnavailable("Offline")))
    find_papers.run_finder(ui)
    assert "Offline" in output.getvalue()
    assert "Choose a number" in output.getvalue()


def test_chat_find_command_does_not_create_a_model_turn(tmp_path, monkeypatch):
    import chat_corpus
    from originrag.analysis.terminal_sessions import SessionStore
    monkeypatch.setattr(sys, "argv", ["chat_corpus.py", "--plain", "--backend", "none", "--out-dir", str(tmp_path)])
    commands = iter(["/find graph theory", "/exit"])
    monkeypatch.setattr(TerminalUI, "prompt", lambda _: next(commands))
    calls = []
    monkeypatch.setattr(chat_corpus.find_papers, "main", lambda arguments, **kwargs: calls.append((arguments, kwargs["allow_import"])))
    monkeypatch.setattr(chat_corpus.ask_corpus, "run_question", lambda *a, **k: (_ for _ in ()).throw(AssertionError("No model turn")))
    assert chat_corpus.main() == 0
    assert calls == [(["graph", "theory"], True)]
    store = SessionStore(tmp_path)
    try:
        assert store.read(store.list()[0]["id"]).turns == []
    finally:
        store.close()
