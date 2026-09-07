from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.retrieval.fts_index import (  # noqa: E402
    DEFAULT_INDEX_PATH,
    RetrievalIndexError,
    build_fts_index,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the persistent OriginRAG SQLite FTS5/BM25 index.")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--out", default=str(DEFAULT_INDEX_PATH.relative_to(REPO_ROOT)))
    args = parser.parse_args()
    try:
        result = build_fts_index(
            resolve(args.chunks_dir), resolve(args.papers), resolve(args.out)
        )
    except RetrievalIndexError as exc:
        print(f"OriginRAG index error: {exc}", file=sys.stderr)
        return 2
    print(f"Index: {display(result.index_path)}")
    print(f"Chunks: {result.chunk_count}")
    print(f"Papers: {result.paper_count}")
    print(f"Fingerprint: {result.corpus_fingerprint}")
    if result.corpus_content_digest:
        print(f"Content digest: {result.corpus_content_digest}")
    return 0


def resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def display(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
