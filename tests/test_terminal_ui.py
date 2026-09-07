from __future__ import annotations

import io
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import chat_corpus  # noqa: E402
from originrag.utils.terminal_ui import TerminalUI, helix, helix_dimensions, safe_text  # noqa: E402


def test_animation_cache_is_periodic_bounded_and_returns_independent_text():
    from originrag.utils.terminal_ui import animated_helix, _helix_frame

    _helix_frame.cache_clear()
    first = animated_helix(0.3)
    expected = first.plain
    first.append("changed")
    assert animated_helix(0.3 + math.tau).plain == expected
    assert _helix_frame.cache_info().hits == 1
    for i in range(300):
        animated_helix(i * 0.1, width=16 if i % 2 else 17)
    assert _helix_frame.cache_info().currsize <= 128
    before = _helix_frame.cache_info()
    animated_helix(0, width=20)
    assert _helix_frame.cache_info() == before


def test_rotation_does_not_restart_when_work_begins(monkeypatch):
    from originrag.utils.terminal_ui import WaitingDisplay

    clock = [100.0]
    monkeypatch.setattr("originrag.utils.terminal_ui.time.monotonic", lambda: clock[0])
    console = Console(file=io.StringIO(), force_terminal=True, width=80, height=24, record=True)
    ui = TerminalUI(console=console)
    clock[0] += 0.75
    console.print(ui._idle_display)
    idle = [line[:20] for line in console.export_text(clear=True).splitlines()[1:9]]
    console.print(WaitingDisplay(ui, "Working"))
    busy = [line[:20] for line in console.export_text(clear=True).splitlines()[1:9]]
    assert idle == busy


def test_helix_moves_without_changing_dimensions():
    first, second = helix(0).plain, helix(1).plain
    assert first != second
    assert len(first.splitlines()) == 8
    assert {len(line) for line in first.splitlines()} == {17}
    assert helix(0, ascii_only=True).plain.isascii()


def test_helix_front_strand_changes_at_crossing():
    console = Console(file=io.StringIO(), color_system="truecolor")
    cyan_front = helix(math.pi / 2).get_style_at_offset(console, 8).color.triplet
    purple_front = helix(3 * math.pi / 2).get_style_at_offset(console, 8).color.triplet
    assert cyan_front.green > cyan_front.red
    assert purple_front.red > purple_front.green


def test_helix_has_surface_shading_and_eight_dot_cell_resolution():
    frame = helix(0.37)
    colours = {span.style.color.triplet for span in frame.spans if span.style.color}
    assert len(colours) > 20
    assert all(span.style.bgcolor is None for span in frame.spans)
    masks = {ord(c) - 0x2800 for c in frame.plain if c not in " \n"}
    assert all(1 <= mask <= 255 for mask in masks)
    assert len(masks) > 20
    assert frame.plain == helix(0.37 + math.tau).plain


def test_helix_monochrome_preserves_depth_cues():
    frame = helix(0.37, ascii_only=True).plain
    assert len(set(frame) - {" ", "\n"}) >= 4


def test_no_colour_terminal_keeps_fine_unicode_geometry():
    ui = TerminalUI(console=Console(file=io.StringIO(), no_color=True))
    assert not ui.ascii_only
    frame = helix(0.37)
    assert any(span.style.dim for span in frame.spans)
    assert any(span.style.bold for span in frame.spans)


@pytest.mark.parametrize("columns,lines", [(48, 16), (56, 24), (80, 24), (120, 40)])
def test_helix_resolution_fits_viewport(columns, lines):
    width, rows = helix_dimensions(columns, lines)
    assert width + 30 <= columns
    assert rows + 8 <= lines
    assert len(helix(0.3, rows=rows, width=width).plain.splitlines()) == rows


def test_large_terminal_does_not_enlarge_the_animation():
    assert helix_dimensions(80, 24) == (17, 8)
    assert helix_dimensions(120, 40) == (17, 8)


def test_waiting_display_resizes_without_overflow(monkeypatch):
    from originrag.utils.terminal_ui import WaitingDisplay

    monkeypatch.setenv("TERM", "xterm-256color")
    console = Console(file=io.StringIO(), force_terminal=True, width=80, height=24, record=True)
    display = WaitingDisplay(TerminalUI(console=console), "Searching your library")
    console.print(display)
    assert len(console.export_text().splitlines()) == 10
    console.size = (56, 24)
    console.print(display)
    assert all(len(line) <= 56 for line in console.export_text().splitlines())


def test_waiting_helix_rotates_even_when_stage_does_not_change(monkeypatch):
    from originrag.utils.terminal_ui import WaitingDisplay

    monkeypatch.setenv("TERM", "xterm-256color")
    clock = [100.0]
    monkeypatch.setattr("originrag.utils.terminal_ui.time.monotonic", lambda: clock[0])
    console = Console(file=io.StringIO(), force_terminal=True, width=80, height=24, record=True)
    display = WaitingDisplay(TerminalUI(console=console), "Searching your library")
    console.print(display)
    before = [line[:20] for line in console.export_text().splitlines()[1:9]]
    clock[0] += 0.75
    console.print(display)
    after = [line[:20] for line in console.export_text().splitlines()[1:9]]
    assert before != after
    assert display.label == "Searching your library"


def test_disabled_intro_does_not_show_a_stationary_helix(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    console = Console(file=io.StringIO(), force_terminal=True, width=80, record=True)
    TerminalUI(console=console, animate=False).welcome()
    assert not any(0x2800 <= ord(c) <= 0x28ff for c in console.export_text())


def test_startup_does_not_delay_input(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=True, width=80, height=24, record=True)
    durations = []
    monkeypatch.setattr("originrag.utils.terminal_ui.time.sleep", durations.append)
    TerminalUI(console=console).welcome()
    output = console.export_text()
    assert "Ask a question" in output
    assert not durations
    assert "elapsed" not in output


def test_idle_toolbar_rotates_without_writing_over_input(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    stream = io.StringIO()

    clock = [100.0]
    monkeypatch.setattr("originrag.utils.terminal_ui.time.monotonic", lambda: clock[0])
    ui = TerminalUI(console=Console(file=stream, force_terminal=True, width=80, height=24))
    first = ui._idle_toolbar().value
    clock[0] += 0.5
    second = ui._idle_toolbar().value
    assert first != second
    assert any(0x2800 <= ord(c) <= 0x28ff for c in first)
    assert stream.getvalue() == ""


@pytest.mark.parametrize("keys,expected", [
    ("Müller ac\x1b[Db\r", "Müller abc"),
    ("\x1b[200~first line\nsecond line\x1b[201~\r", "first line\nsecond line"),
])
def test_animated_input_edits_unicode_and_paste(keys, expected):
    import asyncio
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    ui = TerminalUI(console=Console(file=io.StringIO(), force_terminal=True))
    with create_pipe_input() as pipe:
        session = ui._make_prompt_session(input=pipe, output=DummyOutput())
        refreshes = []
        session.app.before_render += lambda _: refreshes.append(1)

        async def run():
            task = asyncio.create_task(session.prompt_async())
            await asyncio.sleep(0.35)
            pipe.send_text(keys)
            result = await asyncio.wait_for(task, timeout=3)
            pipe.send_text("\x1b[A\r")
            previous = await asyncio.wait_for(session.prompt_async(), timeout=3)
            return result, previous

        result, previous = asyncio.run(run())
    assert result == expected
    assert previous == expected
    assert len(refreshes) >= 3


@pytest.mark.parametrize("key,error", [("\x03", KeyboardInterrupt), ("\x04", EOFError)])
def test_animated_input_interrupt_and_eof(key, error):
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    ui = TerminalUI(console=Console(file=io.StringIO(), force_terminal=True))
    with create_pipe_input() as pipe:
        session = ui._make_prompt_session(input=pipe, output=DummyOutput())
        pipe.send_text(key)
        with pytest.raises(error):
            session.prompt()
        pipe.send_text("still usable\r")
        assert session.prompt() == "still usable"


@pytest.mark.skipif(os.name != "posix", reason="PTY integration check for macOS/Linux")
@pytest.mark.parametrize("keys,result", [("Müller ac\x1b[Db\r", "Müller abc"),
                                         ("\x03", "KeyboardInterrupt"), ("\x04", "EOFError")])
def test_real_terminal_idle_animation_and_cleanup(keys, result):
    import fcntl
    import pty
    import select
    import struct
    import termios
    import time

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    original = termios.tcgetattr(slave)
    code = (
        "from originrag.utils.terminal_ui import TerminalUI\n"
        "try:\n    print('RESULT:', TerminalUI().prompt())\n"
        "except (KeyboardInterrupt, EOFError) as error:\n    print('RESULT:', type(error).__name__)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", code], stdin=slave, stdout=slave, stderr=slave,
                               env={**os.environ, "PYTHONPATH": str(ROOT / "src"),
                                    "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"})
    output = bytearray()
    sent = False
    started = time.monotonic()
    try:
        while time.monotonic() - started < 8:
            if select.select([master], [], [], 0.05)[0]:
                output.extend(os.read(master, 65536))
            if not sent and time.monotonic() - started > 0.8 and b"You" in output:
                os.write(master, keys.encode())
                sent = True
            if process.poll() is not None:
                break
        assert process.wait(timeout=1) == 0

        while select.select([master], [], [], 0)[0]:
            output.extend(os.read(master, 65536))
        text = output.decode("utf-8", errors="replace")
        assert f"RESULT: {result}" in text
        assert text.count("\x1b[?2004h") >= 1
        assert "\x1b[?2004l" in text
        assert "\x1b[?25h" in text

        assert sum(0x2800 <= ord(c) <= 0x28ff for c in text) > 136
        restored = termios.tcgetattr(slave)


        pending = getattr(termios, "PENDIN", 0)
        restored[3] &= ~pending
        original[3] &= ~pending
        assert restored == original
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
        os.close(master)
        os.close(slave)


def test_piped_preview_is_plain_and_does_not_sleep(monkeypatch):
    stream = io.StringIO()
    ui = TerminalUI(console=Console(file=stream, force_terminal=False, width=80))
    monkeypatch.setattr("originrag.utils.terminal_ui.time.sleep",
                        lambda _: pytest.fail("plain output must not wait for animation"))
    ui.preview()
    output = stream.getvalue()
    assert "INTERFACE PREVIEW" in output
    assert "Checking study overlap" in output
    assert "\x1b" not in output
    assert not ui.animate


@pytest.mark.parametrize("width", [32, 55, 80])
def test_preview_fits_narrow_terminal(width, monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    console = Console(file=io.StringIO(), force_terminal=True, width=width, record=True)
    TerminalUI(console=console, animate=False).preview()
    assert all(len(line) <= width for line in console.export_text().splitlines())


def test_interrupted_animation_restores_cursor(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    stream = io.StringIO()
    ui = TerminalUI(console=Console(file=stream, force_terminal=True, width=80))
    with pytest.raises(KeyboardInterrupt):
        with ui.waiting() as update:
            update("Writing a citation-grounded answer")
            raise KeyboardInterrupt
    output = stream.getvalue()
    assert "\x1b[?25l" in output
    assert "\x1b[?25h" in output
    ui.message("still usable")
    assert "still usable" in stream.getvalue()


def test_source_list_counts_reports_not_passages():
    stream = io.StringIO()
    ui = TerminalUI(console=Console(file=stream, force_terminal=False))
    ui.sources([{"paper_id": "A", "title": "Example A"},
                {"paper_id": "A", "title": "Example A"},
                {"paper_id": "B", "title": "Example B"}])
    assert "Selected papers · 2" in stream.getvalue()
    assert stream.getvalue().count("Example A") == 1


def test_imported_text_cannot_replay_terminal_controls():
    assert "\x1b" not in safe_text("title\x1b[2J")
    assert "\b" not in safe_text("title\b")


def test_withheld_response_is_visible_but_rejected_draft_is_not(tmp_path):
    withheld, rejected = tmp_path / "withheld.md", tmp_path / "rejected.md"
    withheld.write_text("Study overlap needs review.")
    rejected.write_text("SECRET REJECTED DRAFT")
    stream = io.StringIO()
    chat_corpus.print_turn_result(
        {"withheld_answer_path": withheld, "rejected_answer_path": rejected},
        tmp_path, TerminalUI(console=Console(file=stream, force_terminal=False)),
    )
    assert "Study overlap needs review" in stream.getvalue()
    assert "SECRET" not in stream.getvalue()


def test_demo_never_creates_session_or_runs_question(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["chat_corpus.py", "--demo", "--plain"])
    monkeypatch.setattr(chat_corpus, "SessionStore", lambda _: pytest.fail("session created"))
    monkeypatch.setattr(chat_corpus.ask_corpus, "run_question", lambda *a, **k: pytest.fail("inference"))
    assert chat_corpus.main() == 0


def test_live_chat_receives_progress_and_recovers_after_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["chat_corpus.py", "--plain", "--out-dir", str(tmp_path)])
    inputs = iter(["A question", "Try again", "/sources", "/details", "/exit"])
    monkeypatch.setattr(TerminalUI, "prompt", lambda _: next(inputs))
    calls = []

    def run(args, output, progress_callback):
        calls.append(args.question)
        progress_callback("Searching and ranking the local corpus")
        if len(calls) == 1:
            raise ValueError("Example corpus error")
        output.mkdir(parents=True)
        answer = output / "answer.md"
        answer.write_text("An example answer.")
        return {"answer_path": answer, "answer_qc": {"passed": True},
                "pack": {"chunks": [{"paper_id": "A", "title": "Example paper"}]}}

    monkeypatch.setattr(chat_corpus.ask_corpus, "run_question", run)
    assert chat_corpus.main() == 0
    output = capsys.readouterr().out
    assert calls == ["A question", "Try again"]
    assert "Example corpus error" in output
    assert "An example answer" in output
    assert "Saved files:" in output


def test_in_chat_library_preserves_context_and_selected_sources(monkeypatch, tmp_path, capsys):
    papers = tmp_path / "papers.tsv"
    papers.write_text("paper_id\ttitle\tauthor\tyear\nA\tLibrary example\tTurner\t2013\n")
    monkeypatch.setattr(sys, "argv", ["chat_corpus.py", "--plain", "--papers", str(papers),
                                     "--out-dir", str(tmp_path / "sessions")])
    inputs = iter(["First question", "/library Turner 2013", "/sources", "Second question", "/exit"])
    monkeypatch.setattr(TerminalUI, "prompt", lambda _: next(inputs))
    calls, contexts = [], []

    def run(args, output, progress_callback):
        calls.append(args.question)
        output.mkdir(parents=True)
        answer = output / "answer.md"
        answer.write_text("Example response")
        return {"answer_path": answer, "pack": {"chunks": [{"paper_id": "X", "title": "Selected source"}]}}

    def retrieval(question, previous):
        contexts.append(len(previous))
        return question

    monkeypatch.setattr(chat_corpus.ask_corpus, "run_question", run)
    monkeypatch.setattr(chat_corpus, "build_chat_retrieval_question", retrieval)
    assert chat_corpus.main() == 0
    output = capsys.readouterr().out
    assert "Library example" in output
    assert output.count("Selected source") == 3
    assert calls == ["First question", "Second question"]
    assert contexts == [0, 1]
    assert not list((tmp_path / "sessions").rglob("*turn_003*"))


@pytest.mark.parametrize("command", ["/library --help", "/library --bad-option",
                                      '/library --author "unterminated',
                                      "/library --papers /nonexistent/catalogue.tsv"])
def test_library_help_and_errors_keep_chat_open_without_inference(command, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["chat_corpus.py", "--plain", "--out-dir", str(tmp_path)])
    inputs = iter([command, "/help", "/exit"])
    monkeypatch.setattr(TerminalUI, "prompt", lambda _: next(inputs))
    monkeypatch.setattr(chat_corpus.ask_corpus, "run_question", lambda *a, **k: pytest.fail("inference"))
    assert chat_corpus.main() == 0
    assert "Open the library and type to search" in capsys.readouterr().out


def test_library_interrupt_returns_to_same_prompt(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["chat_corpus.py", "--plain", "--out-dir", str(tmp_path)])
    inputs = iter(["/library", "/help", "/exit"])
    monkeypatch.setattr(TerminalUI, "prompt", lambda _: next(inputs))

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(chat_corpus.browse_library, "main", interrupted)
    monkeypatch.setattr(chat_corpus.ask_corpus, "run_question", lambda *a, **k: pytest.fail("inference"))
    assert chat_corpus.main() == 0
    assert "Library closed" in capsys.readouterr().out


def test_chat_help_lists_every_command_and_setting(capsys):
    chat_corpus.print_chat_help(topic="all")
    text = capsys.readouterr().out
    for command in ("/help", "/library", "/sources", "/details", "/status", "/exit", "/quit", "/q",
                    "/task", "/backend", "/preset", "/selector", "/repair", "/chats", "/switch",
                    "/new", "/rename", "/history"):
        assert command in text
    for preset in chat_corpus.ask_corpus.PRESETS:
        assert preset in text
    assert "Esc" in text
    assert "--author" in text
    assert "--title" in text


@pytest.mark.parametrize("topic", ["import", "all"])
def test_import_help_distinguishes_live_uploads_from_shell_updates(topic):
    stream = io.StringIO()
    ui = TerminalUI(console=Console(file=stream, width=160, record=True))
    chat_corpus.print_chat_help(ui, topic=topic)
    text = " ".join(ui.console.export_text().split())
    for phrase in (
        "Add papers", "Sign in", "Automatic import", "no restart needed",
        "./originrag import-local --source-dir DIR --apply",
        "./originrag bib", "./originrag prepare-corpus",
        "./originrag scrape-download 10", "no continuous watch or indexing",
        "Folders are not continuously monitored",
        "stop the web service first", "external scheduler",
    ):
        assert phrase in text


def test_default_help_is_focused_and_links_to_detailed_guides(capsys):
    chat_corpus.print_chat_help()
    text = capsys.readouterr().out
    for command in ("/library", "/chats", "/new", "/switch", "/rename", "/history",
                    "/sources", "/details", "/status", "/exit"):
        assert command in text
    for topic in ("library", "import", "advanced", "all"):
        assert f"/help {topic}" in text
    assert "/backend" not in text
    assert "\x1b" not in text


@pytest.mark.parametrize("width", [32, 48, 63, 64, 80, 120])
@pytest.mark.parametrize("topic", ["", "library", "import", "advanced", "all"])
def test_styled_help_fits_terminal_without_truncating_commands(width, topic, monkeypatch):
    from rich.cells import cell_len

    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=True, width=width, record=True)
    ui = TerminalUI(console=console)
    chat_corpus.print_chat_help(ui, topic=topic)
    rendered = console.export_text()
    assert "OriginRAG" in rendered
    assert "\x1b[" in stream.getvalue()
    assert all(cell_len(line) <= width for line in rendered.splitlines())
    assert "…" not in rendered
    assert "╭" in rendered and "╰" in rendered
    if not topic:
        assert "/help advanced" in rendered
        assert "/sources" in rendered


def test_help_supports_ascii_only_terminals(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    stream = io.StringIO()
    ui = TerminalUI(console=Console(file=stream, force_terminal=True, width=80, record=True))
    ui.ascii_only = True
    chat_corpus.print_chat_help(ui, topic="all")
    assert ui.console.export_text().isascii()


@pytest.mark.parametrize("topic", ["", "library", "import", "advanced", "all", "unknown", "all extra"])
def test_help_topics_return_to_chat_without_inference(topic, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["chat_corpus.py", "--plain", "--out-dir", str(tmp_path)])
    inputs = iter([f"/help {topic}".strip(), "/status", "/exit"])
    monkeypatch.setattr(TerminalUI, "prompt", lambda _: next(inputs))
    monkeypatch.setattr(chat_corpus.ask_corpus, "run_question", lambda *a, **k: pytest.fail("inference"))
    assert chat_corpus.main() == 0
    text = capsys.readouterr().out
    assert "backend=" in text
    assert "\x1b" not in text
    if topic in {"unknown", "all extra"}:
        assert "Help topics:" in text


@pytest.mark.parametrize("keys,expected", [("\x1b[B \t\r", "B"), ("\x1b", None), ("\x03", None)])
def test_chat_picker_opens_selection_and_can_cancel(monkeypatch, keys, expected):
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    ui = TerminalUI(console=Console(file=io.StringIO(), force_terminal=True))
    ui.plain = False
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        pipe.send_text(keys)
        assert ui.choose_chat([{"id": "A", "title": "First", "turns": 1},
                               {"id": "B", "title": "Second", "turns": 2}], "A") == expected


def test_keyboard_history_is_reset_to_the_selected_chat():
    import asyncio
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    ui = TerminalUI(console=Console(file=io.StringIO()))
    for identifier, question in (("A", "Alpha question"), ("B", "Beta question")):
        ui.set_chat(identifier, identifier, [question])
        with create_pipe_input() as pipe:
            session = ui._make_prompt_session(input=pipe, output=DummyOutput())

            async def recall():
                task = asyncio.create_task(session.prompt_async())
                await asyncio.sleep(0.03)
                pipe.send_text("\x1b[A\r")
                return await asyncio.wait_for(task, timeout=3)

            assert asyncio.run(recall()) == question


@pytest.mark.parametrize("arguments", [["--demo", "--plain"], ["--help"],
                                        ["library", "--help"], ["library", "--author", "Turner", "--no-pager"],
                                        ["search-papers", "Turner"]])
def test_terminal_preview_and_help_do_not_start_ollama(tmp_path, arguments):
    marker = tmp_path / "ollama-was-called"
    fake = tmp_path / "ollama"
    fake.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 99\n")
    fake.chmod(0o755)
    result = subprocess.run(
        [str(ROOT / "terminal"), *arguments], cwd=tmp_path,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
             "ORIGINRAG_PYTHON": sys.executable},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert "Starting local Ollama" not in result.stdout
