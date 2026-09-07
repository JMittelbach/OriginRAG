from __future__ import annotations

import argparse
import csv
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

PAPERS_FIELDS = [
    "paper_id",
    "title",
    "year",
    "doi",
    "pmid",
    "pmcid",
    "domain",
    "topics",
    "methods",
    "systems",
    "paper_type",
    "fulltext_status",
    "local_pdf_relpath",
    "extracted_text_relpath",
    "notes",
]


def main() -> int:
    args = parse_args()
    manifest_path = resolve_repo_path(args.manifest)
    papers_path = resolve_repo_path(args.papers)
    statuses = set(args.status)
    manifest_rows = read_tsv(manifest_path)
    paper_rows = read_tsv(papers_path) if papers_path.exists() else []

    existing_ids = {row.get("paper_id", "") for row in paper_rows}
    existing_relpaths = {row.get("local_pdf_relpath", "") for row in paper_rows if row.get("local_pdf_relpath")}
    appended = 0
    skipped = 0

    for row in manifest_rows:
        if row.get("activation_status") not in statuses:
            continue
        if row.get("paper_id") in existing_ids or row.get("local_pdf_relpath") in existing_relpaths:
            skipped += 1
            continue
        paper_rows.append(
            {
                "paper_id": row.get("paper_id", ""),
                "title": row.get("title", ""),
                "year": row.get("year", ""),
                "doi": "",
                "pmid": "",
                "pmcid": "",
                "domain": row.get("domain", "malaria"),
                "topics": row.get("topics", ""),
                "methods": row.get("methods", ""),
                "systems": row.get("systems", ""),
                "paper_type": row.get("paper_type", ""),
                "fulltext_status": "pdf_available",
                "local_pdf_relpath": row.get("local_pdf_relpath", ""),
                "extracted_text_relpath": "",
                "notes": f"{row.get('activation_status', 'active')}_from_corpus_manifest",
            }
        )
        existing_ids.add(row.get("paper_id", ""))
        existing_relpaths.add(row.get("local_pdf_relpath", ""))
        appended += 1

    papers_path.parent.mkdir(parents=True, exist_ok=True)
    with papers_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=PAPERS_FIELDS)
        writer.writeheader()
        writer.writerows(paper_rows)

    status_text = ",".join(sorted(statuses))
    print(
        f"Appended {appended} {status_text} rows to "
        f"{papers_path.relative_to(REPO_ROOT)}; skipped {skipped}."
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote selected manifest rows into papers.tsv.")
    parser.add_argument("--manifest", default="data/metadata/corpus_manifest.tsv")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument(
        "--status",
        action="append",
        default=["active_mvp"],
        help="Activation status to promote. Repeatable. Default: active_mvp.",
    )
    return parser.parse_args()


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


if __name__ == "__main__":
    raise SystemExit(main())
