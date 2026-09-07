from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from pfempire.analysis.terminal_sessions import SessionStore, snapshot_result, visible_answer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import chat_corpus  # noqa: E402
from pfempire.utils.terminal_ui import TerminalUI  # noqa: E402


def test_unique_sessions_persist_and_do_not_share_settings(tmp_path):
    store = SessionStore(tmp_path)
    first = store.create({"task": "answer"}, "First")
    first.settings["task"] = "gaps"
    store.save()
    second = store.create({"task": "answer"}, "Second")
    assert first.id != second.id
    assert store.switch(first.id).settings["task"] == "gaps"
    assert store.switch(second.id).settings["task"] == "answer"
    store.close()
    assert SessionStore(tmp_path).read(first.id).title == "First"


def test_active_session_lock_prevents_cross_terminal_overwrite(tmp_path):
    one, two = SessionStore(tmp_path), SessionStore(tmp_path)
    first = one.create({}, "First")
    second = two.create({}, "Second")
    try:
        with pytest.raises(ValueError, match="another terminal"):
            two.switch(first.id)
        assert two.current.id == second.id
        one.close()
        assert two.switch(first.id).title == "First"
    finally:
        one.close()
        two.close()


def test_corrupt_snapshot_and_foreign_paths_are_retained_but_not_loaded(tmp_path):
    store = SessionStore(tmp_path)
    first = store.create({}, "First")
    original = (first.path / "chat.json").read_bytes()
    second = store.create({}, "Second")
    (first.path / "chat.json").write_text("{broken")
    try:
        assert not next(item for item in store.list() if item["id"] == first.id)["available"]
        with pytest.raises(ValueError):
            store.switch(first.id)
        assert store.current.id == second.id
        with pytest.raises(ValueError):
            store.switch("../outside")
        data = json.loads(original)
        data["turns"] = [{"question": "Q", "directory": "../outside", "answer": "bad"}]
        (first.path / "chat.json").write_text(json.dumps(data))
        with pytest.raises(ValueError, match="outside"):
            store.read(first.id)
    finally:
        store.close()


def test_failed_atomic_save_preserves_previous_snapshot(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    session = store.create({}, "First")
    original = (session.path / "chat.json").read_bytes()
    session.title = "Changed"

    def fail(*args):
        raise OSError("Disk write failed")

    monkeypatch.setattr("pfempire.analysis.terminal_sessions.os.replace", fail)
    try:
        with pytest.raises(OSError, match="Disk write failed"):
            store.save()
        assert (session.path / "chat.json").read_bytes() == original
        assert not list(session.path.glob(".chat-*"))
    finally:
        store.close()


def test_pending_turn_number_survives_restart_without_overwriting(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create({})
    session.next_turn = 2
    session.turns.append({"question": "Unfinished", "status": "pending", "directory": "turn_001_unfinished"})
    store.save()
    store.close()
    restored = store.switch(session.id)
    assert restored.next_turn == 2
    assert restored.contexts() == []
    store.close()


def test_only_visible_answers_are_saved_as_context(tmp_path):
    rejected = tmp_path / "answer_rejected.md"
    rejected.write_text("REJECTED DRAFT")
    withheld = tmp_path / "answer_withheld.md"
    withheld.write_text("More evidence needed")
    result = {"rejected_answer_path": rejected, "withheld_answer_path": withheld}
    assert visible_answer(result, tmp_path) == ("More evidence needed", "withheld")
    assert "rejected_answer_path" not in snapshot_result(result, tmp_path)
    assert visible_answer({"rejected_answer_path": rejected}, tmp_path) == ("", "unavailable")
    with pytest.raises(ValueError, match="outside"):
        visible_answer({"answer_path": tmp_path.parent / "secret.txt"}, tmp_path)


def test_legacy_session_restores_only_final_outputs_without_rewriting(tmp_path):
    old = tmp_path / "20260801_101010"
    turn = old / "turn_001_example"
    turn.mkdir(parents=True)
    (old / "session_config.json").write_text('{"state": {"task": "answer"}}')
    (turn / "run_config.json").write_text(json.dumps({
        "question": "Earlier question", "task": "answer", "answer_qc_passed": True,
        "answer_withheld_by_lineage_certificate": True,
    }))
    (turn / "answer.md").write_text("OLD ANSWER MUST NOT BE REUSED")
    (turn / "answer_rejected.md").write_text("REJECTED DRAFT")
    (turn / "answer_withheld.md").write_text("More evidence needed")
    (turn / "evidence_pack.json").write_text('{"chunks": [{"paper_id": "A", "title": "Example"}]}')
    original = {path: path.read_bytes() for path in old.rglob("*") if path.is_file()}
    store = SessionStore(tmp_path)
    restored = store.read(old.name)
    assert restored.next_turn == 2
    assert restored.contexts()[0].answer_summary == "More evidence needed"
    assert restored.contexts()[0].source_titles == ("Example",)
    assert all(path.read_bytes() == content for path, content in original.items())
    assert not (old / "chat.json").exists()


def test_switch_and_restart_keep_model_context_and_sources_isolated(tmp_path, monkeypatch, capsys):
    root = tmp_path / "sessions"
    monkeypatch.setattr(sys, "argv", ["chat_corpus.py", "--plain", "--backend", "none", "--out-dir", str(root)])
    inputs = iter(["/rename Alpha", "Alpha question", "/task gaps", "/new Beta", "/task answer",
                   "Beta question", "/chats", "And explain that?", "/history", "/exit"])
    monkeypatch.setattr(TerminalUI, "prompt", lambda _: next(inputs))
    monkeypatch.setattr(TerminalUI, "choose_chat",
                        lambda self, chats, current: next(chat["id"] for chat in chats if chat["title"] == "Alpha"))
    calls = []

    def run(args, directory, progress_callback):
        calls.append((args.question, args.conversation_context, args.task, directory.name))
        directory.mkdir(parents=True)
        answer = directory / "answer.md"
        answer.write_text("Visible answer to " + args.question)
        return {"answer_path": answer, "pack": {"chunks": [{"paper_id": "A", "title": args.question + " paper"}]}}

    monkeypatch.setattr(chat_corpus.ask_corpus, "run_question", run)
    assert chat_corpus.main() == 0
    assert calls[0][1] == calls[1][1] == []
    assert calls[2][1][0]["user"] == "Alpha question"
    assert calls[2][1][0]["assistant"] == "Visible answer to Alpha question"
    assert "Beta" not in str(calls[2][1])
    assert calls[2][2] == "gaps"
    assert calls[2][3].startswith("turn_002_")
    alpha = next(item for item in SessionStore(root).list() if item["title"] == "Alpha")
    monkeypatch.setattr(sys, "argv", ["chat_corpus.py", "--plain", "--out-dir", str(root), "--resume", alpha["id"]])
    inputs = iter(["/sources", "Why?", "/exit"])
    capsys.readouterr()
    assert chat_corpus.main() == 0
    output = capsys.readouterr().out
    assert "And explain that? paper" in output
    assert calls[-1][3].startswith("turn_003_")
    assert len(calls[-1][1]) == 2
    assert "Beta" not in str(calls[-1][1])
    assert calls[-1][2] == "gaps"


def test_session_commands_never_call_model_and_number_switch_works(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["chat_corpus.py", "--plain", "--out-dir", str(tmp_path)])
    commands = iter(["/rename First", "/new Second", "/chats", "/switch 2", "/status", "/exit"])
    monkeypatch.setattr(TerminalUI, "prompt", lambda _: next(commands))
    monkeypatch.setattr(chat_corpus.ask_corpus, "run_question", lambda *a, **k: pytest.fail("model called"))
    assert chat_corpus.main() == 0
    text = capsys.readouterr().out
    assert "Chat: First" in text
    assert "1 *. Second" in text
    assert len(SessionStore(tmp_path).list()) == 2
