from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire import __version__  # noqa: E402
from pfempire.paths import OriginRAGConfigError, get_bib_dir, load_project_env  # noqa: E402


def main() -> int:
    load_project_env(REPO_ROOT / ".env")

    print(f"OriginRAG version: {__version__}")
    print(f"Python version: {sys.version.split()[0]}")
    print(f"Current working directory: {Path.cwd()}")

    try:
        bib_dir = get_bib_dir()
        print("ORIGINRAG_BIB_DIR set: yes")
        print(f"Bibliography directory: {bib_dir}")
        print(f"Bibliography directory exists: {'yes' if bib_dir.exists() else 'no'}")
    except OriginRAGConfigError:
        print("ORIGINRAG_BIB_DIR set: no")
        print("Bibliography directory exists: unknown")

    print()
    print("Metadata files:")
    for relative_path in [
        "data/metadata/papers.tsv",
        "data/metadata/papers.jsonl",
        "data/metadata/gold_standard_malaria.tsv",
    ]:
        path = REPO_ROOT / relative_path
        print(f"- {relative_path}: {'exists' if path.exists() else 'missing'}")

    print()
    print("Local corpus assets (not committed to Git):")
    for relative_path in [
        "data/extracted_text",
        "data/chunks",
        "data/indexes/pfempire_fts.sqlite",
    ]:
        path = REPO_ROOT / relative_path
        print(f"- {relative_path}: {'exists' if path.exists() else 'missing'}")

    chunks_dir = REPO_ROOT / "data/chunks"
    index_path = REPO_ROOT / "data/indexes/pfempire_fts.sqlite"
    full_rag_ready = chunks_dir.is_dir() and any(chunks_dir.glob("*.jsonl")) and index_path.is_file()
    print(f"- full local retrieval assets ready: {'yes' if full_rag_ready else 'no'}")
    if not full_rag_ready:
        print("  Build them after adding PDFs with: ./originrag prepare-corpus")

    if not (REPO_ROOT / "data/metadata/papers.tsv").exists():
        print()
        print("Critical: data/metadata/papers.tsv is missing.")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
