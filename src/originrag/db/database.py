from __future__ import annotations

import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = REPO_ROOT / "data/local_db/pfempire.sqlite"


def initialize_database(db_path: Path = DEFAULT_DB_PATH, schema_path: Path = SCHEMA_PATH) -> list[str]:
    """Initialize the local SQLite research database and return table names."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema = Path(schema_path).read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _ensure_indexed_claim_columns(connection)
        connection.executescript(schema)
        connection.commit()
        return list_tables(connection)


def _ensure_indexed_claim_columns(connection: sqlite3.Connection) -> None:
    """Make old claim tables compatible with indexes created by the base schema."""

    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'claims'"
    ).fetchone()
    if not exists:
        return
    columns = {row[1] for row in connection.execute("PRAGMA table_info(claims)")}
    required = {
        "claim_type": "TEXT",
        "source_paper_id": "TEXT",
        "source_chunk_id": "TEXT",
        "claim_hash": "TEXT",
        "doi": "TEXT",
        "claim_status": "TEXT DEFAULT 'auto_extracted'",
    }
    for name, sql_type in required.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE claims ADD COLUMN {name} {sql_type}")


def list_tables(connection_or_path: sqlite3.Connection | Path = DEFAULT_DB_PATH) -> list[str]:
    """List user tables in a SQLite database."""
    if isinstance(connection_or_path, sqlite3.Connection):
        return _list_tables(connection_or_path)
    with sqlite3.connect(connection_or_path) as connection:
        return _list_tables(connection)


def _list_tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]
