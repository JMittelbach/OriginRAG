from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from originrag.personal_shelf import FIELDS, PersonalShelf


MAX_READ_PAPERS = 10000


def paper_identifier(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 160 or not value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("A valid paper_id is required.")
    return value


def list_options(offset: int = 0, limit: int = 24, query: str = "") -> dict:
    if type(offset) is not int or not 0 <= offset <= 10000:
        raise ValueError("offset must be an integer between 0 and 10000.")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100.")
    if not isinstance(query, str) or len(query) > 300 or any(ord(character) < 32 and not character.isspace() for character in query):
        raise ValueError("Search text must contain at most 300 characters.")
    return {"offset": offset, "limit": limit, "query": query.strip()}


def reading_query(query: str) -> dict:
    params = parse_qs(query, keep_blank_values=True, max_num_fields=8)
    if any(len(values) != 1 for values in params.values()):
        raise ValueError("Supply each reading-history parameter only once.")
    if "paper_id" in params:
        if set(params) != {"paper_id"}:
            raise ValueError("Supply paper_id on its own to check reading status.")
        return {"paper_id": paper_identifier(params["paper_id"][0])}
    if not set(params) <= {"offset", "limit", "query"}:
        raise ValueError("Unsupported reading-history parameter.")
    for key in ("offset", "limit"):
        if key in params and not re.fullmatch(r"[0-9]{1,5}", params[key][0]):
            raise ValueError(f"{key} must be an integer.")
    return list_options(int(params.get("offset", ["0"])[0]), int(params.get("limit", ["24"])[0]), params.get("query", [""])[0])


def _metadata(paper: dict, paper_id: str) -> dict:
    fields = {key: " ".join(str(paper.get(key) or "").split())[:2000] for key in FIELDS}
    fields["paper_id"] = paper_id
    fields["title"] = fields["title"] or paper_id
    try:
        url = urlparse(fields["doi_url"])
        safe_url = url.scheme in {"http", "https"} and url.hostname in {"doi.org", "dx.doi.org"} and not url.username and not url.password
    except ValueError:
        safe_url = False
    if not safe_url:
        fields["doi_url"] = ""
    return fields


class ReadingHistory:
    def __init__(self, owner_directory: Path):
        self.shelf = PersonalShelf(owner_directory)
        self.path = self.shelf.path

    @staticmethod
    def _status(connection, paper_id: str) -> dict:
        read_at, on_shelf, count = connection.execute(
            "SELECT (SELECT read_at FROM reading WHERE paper_id=?), EXISTS(SELECT 1 FROM books WHERE paper_id=?), (SELECT COUNT(*) FROM reading)",
            (paper_id, paper_id),
        ).fetchone()
        return {"paper_id": paper_id, "is_read": read_at is not None, "read_at": read_at, "on_shelf": bool(on_shelf), "read_count": count}

    def status(self, paper_id: str) -> dict:
        paper_identifier(paper_id)
        connection = self.shelf.connect()
        try:
            return self._status(connection, paper_id)
        finally:
            connection.close()

    def snapshot(self, catalogue: tuple | list, *, offset: int = 0, limit: int = 24, query: str = "") -> dict:
        options = list_options(offset, limit, query)
        terms = options["query"].casefold().split()
        current = {str(paper["paper_id"]): paper for paper in catalogue}
        connection = self.shelf.connect()
        try:
            with connection:
                connection.execute("BEGIN")
                count = connection.execute("SELECT COUNT(*) FROM reading").fetchone()[0]
                sql = "SELECT r.paper_id, r.metadata, r.read_at, b.paper_id IS NOT NULL FROM reading r LEFT JOIN books b ON b.paper_id=r.paper_id ORDER BY r.read_at DESC, r.paper_id"
                rows = connection.execute(sql if terms else sql + " LIMIT ? OFFSET ?", () if terms else (limit, offset))
                items, matched = [], 0
                for paper_id, stored, read_at, on_shelf in rows:
                    paper = current.get(paper_id)
                    fields = _metadata(paper if paper is not None else json.loads(stored), paper_id)
                    searchable = " ".join(fields.values()).casefold()
                    if terms and not all(term in searchable for term in terms):
                        continue
                    position = matched
                    matched += 1
                    if terms and not offset <= position < offset + limit:
                        continue
                    items.append({**fields, "available": paper is not None,
                                  "pdf_url": f"/api/library/pdf?paper_id={quote(paper_id, safe='')}" if paper is not None and paper.get("local_pdf") else "",
                                  "read_at": read_at, "is_read": True, "on_shelf": bool(on_shelf)})
        finally:
            connection.close()
        return {"items": items, "total": matched if terms else count, "read_count": count, "offset": offset, "limit": limit}

    def change(self, payload: object, catalogue: tuple | list) -> dict:
        fields = {"action", "paper_id"}
        if not isinstance(payload, dict) or not fields <= set(payload) <= fields | {"remove_from_shelf"}:
            raise ValueError("Supply action, paper_id and optionally remove_from_shelf only.")
        action, paper_id = payload["action"], paper_identifier(payload["paper_id"])
        remove = payload.get("remove_from_shelf", False)
        if not isinstance(action, str) or action not in {"mark_read", "mark_unread"} or type(remove) is not bool or (remove and action != "mark_read"):
            raise ValueError("Choose mark_read or mark_unread. Shelf removal is optional only when marking a paper as read.")
        paper = next((paper for paper in catalogue if str(paper["paper_id"]) == paper_id), None)
        connection = self.shelf.connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if action == "mark_unread":
                    connection.execute("DELETE FROM reading WHERE paper_id=?", (paper_id,))
                else:
                    previous = connection.execute("SELECT metadata FROM reading WHERE paper_id=?", (paper_id,)).fetchone()
                    stored = previous or connection.execute("SELECT metadata FROM books WHERE paper_id=?", (paper_id,)).fetchone()
                    if paper is None and stored is None:
                        raise ValueError("This paper is not in your library or saved papers.")
                    if previous is None and connection.execute("SELECT COUNT(*) FROM reading").fetchone()[0] >= MAX_READ_PAPERS:
                        raise ValueError(f"Your reading history holds up to {MAX_READ_PAPERS} papers. Mark an entry as unread before adding another.")
                    metadata = _metadata(paper if paper is not None else json.loads(stored[0]), paper_id)
                    connection.execute("INSERT INTO reading (paper_id, metadata, read_at) VALUES (?, ?, ?) ON CONFLICT(paper_id) DO UPDATE SET metadata=excluded.metadata",
                                       (paper_id, json.dumps(metadata), time.time_ns() // 1_000_000))
                    if remove:
                        connection.execute("DELETE FROM books WHERE paper_id=?", (paper_id,))
                result = self._status(connection, paper_id)
        finally:
            connection.close()
        return result
