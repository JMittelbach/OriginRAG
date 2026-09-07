from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.curation.relevance_policy import relevance_decision  # noqa: E402


FIELDS = [
    "paper_id",
    "reasons",
    "category",
    "author",
    "year",
    "title",
    "journal",
    "doi",
    "local_pdf_relpath",
]


def main() -> int:
    args = parse_args()
    papers_path = resolve_repo_path(args.papers)
    out_path = resolve_repo_path(args.out)
    rows = read_tsv(papers_path)
    flagged = []
    for row in rows:
        context = " ".join(
            value
            for value in [
                row.get("topics", ""),
                row.get("methods", ""),
                row.get("systems", ""),
                row.get("notes", ""),
            ]
            if value
        )
        decision = relevance_decision(
            title=row.get("title", ""),
            journal=row.get("journal", ""),
            category=row.get("domain", ""),
            query=context,
        )
        if decision.keep:
            continue
        flagged.append(
            {
                "paper_id": row.get("paper_id", ""),
                "reasons": ";".join(decision.reasons),
                "category": row.get("topics", ""),
                "author": first_author(row.get("title", ""), row.get("paper_id", "")),
                "year": row.get("year", ""),
                "title": row.get("title", ""),
                "journal": row.get("journal", ""),
                "doi": row.get("doi", ""),
                "local_pdf_relpath": row.get("local_pdf_relpath", ""),
            }
        )

    write_tsv(out_path, flagged)
    print(f"Corpus rows checked: {len(rows)}")
    print(f"Scope candidates flagged: {len(flagged)}")
    print(f"Wrote: {display_path(out_path)}")
    if args.fail_on_flagged and flagged:
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit papers.tsv against the OriginRAG molecular/evolution scope policy.")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--out", default="data/metadata/corpus_scope_audit.tsv")
    parser.add_argument("--fail-on-flagged", action="store_true")
    return parser.parse_args()


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def first_author(title: str, fallback: str) -> str:
    return (fallback.split("_", 1)[0] if fallback else "").strip() or title.split(" ", 1)[0]


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
