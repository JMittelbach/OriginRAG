from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.retrieval.paper_retrieval import PAPER_FIELDS, search_papers  # noqa: E402


def main() -> int:
    args = parse_args()
    results = search_papers(
        args.query,
        papers_tsv=resolve_repo_path(args.papers),
        limit=args.limit,
        category=args.category,
        task_mode=args.task_mode,
        local_pdf_root=REPO_ROOT / "bib",
        chunks_dir=REPO_ROOT / "data/chunks",
    )
    if args.out:
        write_tsv(resolve_repo_path(args.out), results)
    for result in results:
        print(
            f"[{result.get('rank')}] score={result['score']} | {result['paper_id']} | "
            f"{result.get('author', '')} {result.get('year', '')} | {result.get('title', '')}"
        )
        if result.get("rationale"):
            print(f"    rationale: {result['rationale']}")
    if not results:
        print("No matching papers found.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search OriginRAG paper metadata without calling a language model."
    )
    parser.add_argument("query")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--category")
    parser.add_argument("--task-mode", default="evidence_qa")
    parser.add_argument("--out")
    return parser.parse_args()


def write_tsv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=["rank", *PAPER_FIELDS])
        writer.writeheader()
        writer.writerows(rows)


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
