"""Local terminal conversations, atomic snapshots and per-chat write locks."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from pfempire.analysis.chat import ChatTurnContext


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def read_object(path: Path) -> dict:
    if path.stat().st_size > 20_000_000:
        raise ValueError(f"Chat file is too large: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Invalid chat file: {path.name}")
    return value


def atomic_json(path: Path, value: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".chat-", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def local_path(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Chat file points outside its session directory")
    return path


def visible_answer(result: dict, root: Path) -> tuple[str, str]:
    for key, status in (("answer_path", "answer"), ("partial_answer_path", "partial"),
                        ("withheld_answer_path", "withheld")):
        raw = result.get(key)
        if not raw:
            continue
        path = local_path(root, str(raw))
        if path.is_file():
            if path.stat().st_size > 2_000_000:
                raise ValueError("Saved answer is too large")
            if raw == result.get("partial_answer_path"):
                status = "partial"
            return path.read_text(encoding="utf-8").strip(), status
    return "", "unavailable"


def snapshot_result(result: dict, root: Path) -> dict:
    saved = {"pack": {"chunks": [
        {key: str(chunk.get(key) or "") for key in
         ("paper_id", "title", "citation_label", "doi", "author", "year")}
        for chunk in (result.get("pack") or {}).get("chunks", [])
    ]}, "answer_qc": {"passed": (result.get("answer_qc") or {}).get("passed")},
        "answer_attempts": [{"kind": item.get("kind")} for item in result.get("answer_attempts", [])]}
    for key in ("answer_path", "partial_answer_path", "withheld_answer_path", "evidence_table_tsv_path"):
        if result.get(key):
            saved[key] = str(local_path(root, str(result[key])).relative_to(root.resolve()))
    return saved


@dataclass
class TerminalSession:
    path: Path
    title: str
    settings: dict
    created: str
    updated: str
    turns: list[dict] = field(default_factory=list)
    next_turn: int = 1
    auto_title: bool = True

    @property
    def id(self) -> str:
        return self.path.name

    def contexts(self) -> list[ChatTurnContext]:
        contexts = []
        for turn in self.turns:
            if turn.get("status") == "pending":
                continue
            chunks = (turn.get("result", {}).get("pack") or {}).get("chunks", [])
            contexts.append(ChatTurnContext(
                user_question=turn["question"], retrieval_question=turn.get("retrieval_question", ""),
                answer_summary=turn.get("answer", "")[:1500], resolved_task=turn.get("task", ""),
                source_labels=tuple(dict.fromkeys(c["citation_label"] for c in chunks if c.get("citation_label")))[:8],
                source_titles=tuple(dict.fromkeys(c["title"] for c in chunks if c.get("title")))[:6],
            ))
        return contexts

    def last_result(self) -> tuple[dict | None, Path | None]:
        for turn in reversed(self.turns):
            if "result" in turn:
                result = dict(turn["result"])
                for key in ("answer_path", "partial_answer_path", "withheld_answer_path", "evidence_table_tsv_path"):
                    if result.get(key):
                        result[key] = local_path(self.path, result[key])
                return result, local_path(self.path, turn["directory"])
        return None, None


class SessionStore:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.current: TerminalSession | None = None
        self._lock = None

    def create(self, settings: dict, title: str = "") -> TerminalSession:
        directory = self.root / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid4().hex[:6])
        directory.mkdir(mode=0o700)
        stamp = now()
        session = TerminalSession(directory, title or "New chat", dict(settings), stamp, stamp,
                                  auto_title=not bool(title))
        self._activate(session)
        self.save()

        atomic_json(directory / "session_config.json", {"state": settings})
        return session

    def _path(self, identifier: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise ValueError("Use a chat number from /chats or its exact ID")
        path = self.root / identifier
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Chat not found")
        return path

    def read(self, identifier: str) -> TerminalSession:
        path = self._path(identifier)
        if (path / "chat.json").exists():
            data = read_object(local_path(path, "chat.json"))
            if data.get("version") != 1 or not isinstance(data.get("turns"), list):
                raise ValueError("Unsupported or damaged chat snapshot")
            required = ("title", "settings", "created", "updated", "turns", "next_turn", "auto_title")
            if not all(key in data for key in required) or not isinstance(data["settings"], dict):
                raise ValueError("Incomplete chat snapshot")
            if (not all(isinstance(data[key], str) for key in ("title", "created", "updated"))
                    or type(data["next_turn"]) is not int or data["next_turn"] < 1
                    or not isinstance(data["auto_title"], bool)):
                raise ValueError("Invalid chat metadata")
            for turn in data["turns"]:
                if not isinstance(turn, dict) or not isinstance(turn.get("question"), str):
                    raise ValueError("Damaged chat turn")
                if not isinstance(turn.get("answer", ""), str) or not isinstance(turn.get("directory"), str):
                    raise ValueError("Damaged chat answer")
                local_path(path, turn["directory"])
                result = turn.get("result", {})
                if not isinstance(result, dict):
                    raise ValueError("Damaged saved result")
                pack = result.get("pack", {})
                if not isinstance(pack, dict) or not isinstance(pack.get("chunks", []), list):
                    raise ValueError("Damaged saved sources")
                if any(not isinstance(chunk, dict) or any(not isinstance(value, str) for value in chunk.values())
                       for chunk in pack.get("chunks", [])):
                    raise ValueError("Damaged saved source metadata")
                for key in ("answer_path", "partial_answer_path", "withheld_answer_path", "evidence_table_tsv_path"):
                    if result.get(key):
                        local_path(path, result[key])
            session = TerminalSession(path=path, **{key: data[key] for key in required})
            for directory in path.glob("turn_*"):
                match = re.match(r"turn_(\d+)_", directory.name)
                if match:
                    session.next_turn = max(session.next_turn, int(match[1]) + 1)
            return session
        return self._read_legacy(path)

    def _read_legacy(self, path: Path) -> TerminalSession:
        config = read_object(local_path(path, "session_config.json"))
        stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        session = TerminalSession(path, "Earlier chat", config.get("state", {}), stamp, stamp)
        for directory in sorted(path.glob("turn_*")):
            if directory.is_symlink() or not directory.is_dir():
                continue
            match = re.match(r"turn_(\d+)_", directory.name)
            if match:
                session.next_turn = max(session.next_turn, int(match[1]) + 1)
            if not (directory / "run_config.json").is_file():
                continue
            config = read_object(local_path(path, f"{directory.name}/run_config.json"))
            question = str(config.get("question") or "")
            if not question:
                continue
            result = {"answer_qc": {"passed": config.get("answer_qc_passed")}}


            if config.get("answer_withheld_by_lineage_certificate"):
                result["withheld_answer_path"] = directory / "answer_withheld.md"
            elif config.get("answer_partial_by_lineage_certificate"):
                result["partial_answer_path"] = directory / "answer_partial.md"
            elif not config.get("answer_rejected_by_grounding") and not config.get("answer_rejected_by_qc"):
                result["answer_path"] = directory / "answer.md"
            pack = directory / "evidence_pack.json"
            if pack.is_file():
                result["pack"] = read_object(local_path(path, f"{directory.name}/evidence_pack.json"))
            answer, status = visible_answer(result, path)
            session.turns.append({"question": question, "retrieval_question": config.get("retrieval_question", question),
                                  "task": config.get("task", "answer"), "answer": answer, "status": status,
                                  "directory": directory.name, "result": snapshot_result(result, path)})
        if session.turns:
            session.title = " ".join(session.turns[0]["question"].split())[:80]
            session.auto_title = False
        return session

    def _activate(self, session: TerminalSession, *, reread: bool = False) -> None:
        handle = local_path(session.path, ".terminal.lock").open("a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise ValueError("This chat is open in another terminal; close it there before switching") from exc
        if reread:
            try:
                session = self.read(session.id)
            except BaseException:
                handle.close()
                raise
        self.close()
        self.current, self._lock = session, handle

    def switch(self, identifier: str) -> TerminalSession:
        if self.current and self.current.id == identifier:
            return self.current
        candidate = self.read(identifier)

        self._activate(candidate, reread=True)
        return self.current

    def save(self) -> None:
        if self.current is None or self._lock is None:
            raise ValueError("No active chat")
        session = self.current
        session.updated = now()
        atomic_json(session.path / "chat.json", {
            "version": 1, "title": session.title, "settings": session.settings,
            "created": session.created, "updated": session.updated, "turns": session.turns,
            "next_turn": session.next_turn, "auto_title": session.auto_title,
        })

    def list(self) -> list[dict]:
        items = []
        for path in self.root.iterdir():
            if path.is_symlink() or not path.is_dir():
                continue
            if not (path / "chat.json").exists() and not (path / "session_config.json").exists():
                continue
            try:
                session = self.read(path.name)
                items.append({"id": session.id, "title": session.title, "turns": len(session.turns),
                              "updated": session.updated, "available": True})
            except (OSError, ValueError, TypeError, KeyError) as exc:
                items.append({"id": path.name, "title": "Unreadable chat (files retained)", "turns": 0,
                              "updated": "", "available": False, "error": str(exc)})
        return sorted(items, key=lambda item: (item["updated"], item["id"]), reverse=True)

    def close(self) -> None:
        if self._lock is not None:
            self._lock.close()
        self._lock, self.current = None, None
