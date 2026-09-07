from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.db import initialize_database  # noqa: E402


def main() -> int:
    args = parse_args()
    db_path = resolve_repo_path(args.db)
    tables = initialize_database(db_path)
    print(f"Initialized research DB: {display_path(db_path)}")
    print("Tables:")
    for table in tables:
        print(f"- {table}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize the local OriginRAG research SQLite database.")
    parser.add_argument("--db", default="data/local_db/pfempire.sqlite")
    return parser.parse_args()


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def display_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
