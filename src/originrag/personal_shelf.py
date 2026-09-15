from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

FIELDS = ("paper_id", "title", "author", "year", "journal", "doi", "doi_url")
MAX_BOOKS = 200


class PersonalShelf:
    def __init__(self, owner_directory: Path):
        self.path = Path(owner_directory) / ".personal_shelf.sqlite3"

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            os.chmod(self.path, 0o600)
            connection.execute("CREATE TABLE IF NOT EXISTS books (paper_id TEXT PRIMARY KEY, metadata TEXT NOT NULL, saved_at INTEGER NOT NULL)")
        except Exception:
            connection.close()
            raise
        return connection

    def snapshot(self, catalogue: tuple | list) -> dict:
        connection = self.connect()
        try:
            rows = connection.execute("SELECT paper_id, metadata, saved_at FROM books ORDER BY saved_at DESC, paper_id").fetchall()
        finally:
            connection.close()
        current = {str(p["paper_id"]): p for p in catalogue}
        items = []
        for paper_id, metadata, saved_at in rows:
            paper = dict(current.get(paper_id) or json.loads(metadata))
            items.append({**{key: str(paper.get(key) or "") for key in FIELDS},
                          "available": paper_id in current,
                          "summary_available": bool(paper.get("summary_available") or paper.get("searchable")) if paper_id in current else False,
                          "saved_at": saved_at})
        return {"items": items, "limit": MAX_BOOKS}

    def change(self, payload: object, catalogue: tuple | list) -> dict:
        if not isinstance(payload, dict) or set(payload) != {"action", "paper_id"}:
            raise ValueError("Supply action and paper_id only.")
        action, paper_id = payload["action"], payload["paper_id"]
        if not isinstance(action, str) or action not in {"add", "remove"} or not isinstance(paper_id, str) or not 1 <= len(paper_id) <= 160:
            raise ValueError("Invalid shelf action or paper identifier.")
        paper = next((p for p in catalogue if p["paper_id"] == paper_id), None)
        if action == "add" and not paper:
            raise ValueError("This paper is not in the library.")
        connection = self.connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if action == "remove":
                    connection.execute("DELETE FROM books WHERE paper_id = ?", (paper_id,))
                else:
                    exists = connection.execute("SELECT 1 FROM books WHERE paper_id = ?", (paper_id,)).fetchone()
                    if not exists and connection.execute("SELECT COUNT(*) FROM books").fetchone()[0] >= MAX_BOOKS:
                        raise ValueError(f"Your shelf holds up to {MAX_BOOKS} papers. Remove one before adding another.")
                    metadata = json.dumps({key: str(paper.get(key) or "")[:2000] for key in FIELDS})
                    connection.execute("INSERT INTO books VALUES (?, ?, ?) ON CONFLICT(paper_id) DO UPDATE SET metadata=excluded.metadata",
                                       (paper_id, metadata, time.time_ns()))
        finally:
            connection.close()
        return self.snapshot(catalogue)
