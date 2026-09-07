from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    args = parse_args()
    discovery_rows = read_tsv(resolve_repo_path(args.discovery))
    registry_rows = read_tsv(resolve_repo_path(args.registry))
    out_path = resolve_repo_path(args.out)
    no_doi_path = resolve_repo_path(args.no_doi_out)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_report(discovery_rows, registry_rows), encoding="utf-8")
    write_no_doi_tsv(no_doi_path, registry_rows)
    print(f"Wrote report: {display_path(out_path)}")
    print(f"Wrote no-DOI review TSV: {display_path(no_doi_path)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report discovery/download quality for a literature watch run.")
    parser.add_argument("--discovery", default="data/metadata/open_access_discovery_10y.tsv")
    parser.add_argument("--registry", default="data/metadata/paper_registry.tsv")
    parser.add_argument("--out", default="outputs/curation/discovery_10y_report.md")
    parser.add_argument("--no-doi-out", default="data/metadata/no_doi_review.tsv")
    return parser.parse_args()


def build_report(discovery_rows: list[dict[str, str]], registry_rows: list[dict[str, str]]) -> str:
    downloaded = [row for row in discovery_rows if row.get("pdf_status") == "downloaded"]
    candidates = [
        row
        for row in discovery_rows
        if row.get("pdf_status") == "not_downloaded_limit"
        and row.get("doi")
        and row.get("pdf_url")
        and row.get("quality_tier") in {"candidate", "high"}
    ]
    low_or_excluded = [
        row
        for row in discovery_rows
        if row.get("pdf_status") in {"below_quality_threshold", "missing_doi", "excluded", "no_pdf_url", "download_failed"}
    ]
    no_doi_registry = [row for row in registry_rows if not row.get("doi")]

    lines = [
        "# Discovery 10-Year Report",
        "",
        "Categories are metadata facets for curation and boosting. They are not hard retrieval partitions.",
        "",
        "## Summary",
        "",
        f"- Discovery rows: {len(discovery_rows)}",
        f"- Downloaded PDFs in this run: {len(downloaded)}",
        f"- Registry PDFs: {len(registry_rows)}",
        f"- Registry PDFs with DOI: {sum(1 for row in registry_rows if row.get('doi'))}",
        f"- Registry PDFs without DOI: {len(no_doi_registry)}",
        "",
        "## Discovery Status",
        "",
        *counter_lines(Counter(row.get("pdf_status", "") for row in discovery_rows)),
        "",
        "## Quality Tiers",
        "",
        *counter_lines(Counter(row.get("quality_tier", "") for row in discovery_rows)),
        "",
        "## Downloaded By Category",
        "",
        *counter_lines(Counter(row.get("category", "") for row in downloaded)),
        "",
        "## Strong New Candidates Not Downloaded Yet",
        "",
        *paper_lines(sorted(candidates, key=score_key, reverse=True)[:40]),
        "",
        "## Lower-Confidence Or Blocked Hits",
        "",
        *paper_lines(sorted(low_or_excluded, key=score_key, reverse=True)[:40], include_status=True),
        "",
        "## Local PDFs Still Missing DOI",
        "",
        "These should not be used as normal cited evidence until DOI metadata is resolved or an explicit exception is made.",
        "",
        *registry_lines(no_doi_registry[:60]),
        "",
    ]
    return "\n".join(lines)


def counter_lines(counter: Counter[str]) -> list[str]:
    if not counter:
        return ["- none"]
    return [f"- {key or 'unknown'}: {value}" for key, value in counter.most_common()]


def paper_lines(rows: list[dict[str, str]], *, include_status: bool = False) -> list[str]:
    if not rows:
        return ["- none"]
    lines = []
    for row in rows:
        status = f" [{row.get('pdf_status')}]" if include_status else ""
        doi = f" DOI: {row.get('doi')}" if row.get("doi") else " DOI: missing"
        lines.append(
            f"- {row.get('author') or 'Unknown'} {row.get('year') or 'n.d.'}. "
            f"{row.get('title') or 'Untitled'}. {row.get('journal') or ''}"
            f" ({row.get('category') or 'unknown'}, {row.get('quality_tier') or 'unknown'}, "
            f"score {row.get('quality_score') or '0'}{status}).{doi}"
        )
    return lines


def registry_lines(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["- none"]
    return [
        f"- {row.get('author') or 'Unknown'} {row.get('year') or 'n.d.'}. "
        f"{row.get('title') or 'Untitled'}. {row.get('journal') or ''} "
        f"({row.get('local_pdf_relpath') or 'missing path'})"
        for row in rows
    ]


def write_no_doi_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = ["local_pdf_relpath", "author", "year", "title", "journal", "category", "notes"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            if row.get("doi"):
                continue
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def score_key(row: dict[str, str]) -> tuple[int, int]:
    score = int(row.get("quality_score") or 0)
    year = int(row.get("year") or 0) if row.get("year", "").isdigit() else 0
    return score, year


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


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
