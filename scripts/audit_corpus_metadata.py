from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WEAK_AUTHORS = {"", "administrator", "annaimport", "phd", "unknown", "nd", "systemadmin"}
GROUP_AUTHOR_MARKERS = ("consortium", "collaboration", "network", "study group")
TITLE_ARTIFACTS = (
    r"^abstract\b",
    r"blackwell (?:publishing|science)",
    r"published online.*doi",
    r"please cite this article",
    r"\bet al\.\s+elife\b",
    r"^contributed by\b",
    r"^received \d",
)
ISSUE_FIELDS = ["severity", "issue", "paper_id", "doi", "author", "year", "title", "local_pdf_relpath"]
INACTIVE_FULLTEXT_STATUSES = {"duplicate", "excluded_no_doi", "archived"}


def main() -> int:
    args = parse_args()
    registry = read_tsv(resolve(args.registry))
    papers = read_tsv(resolve(args.papers))
    issues = audit(registry, papers, bib_dir=resolve(args.bib_dir))
    write_tsv(resolve(args.out), issues, ISSUE_FIELDS)
    write_report(resolve(args.report), registry, papers, issues)
    print(f"Registry rows: {len(registry)}")
    print(f"Active paper rows: {active_paper_count(papers)}")
    print(f"Metadata issues: {len(issues)}")
    print(f"Issue TSV: {display(resolve(args.out))}")
    print(f"Report: {display(resolve(args.report))}")
    return 1 if args.fail_on_error and any(row["severity"] == "error" for row in issues) else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit corpus citation metadata and paper/registry consistency.")
    parser.add_argument("--registry", default="data/metadata/paper_registry.tsv")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--bib-dir", default="bib")
    parser.add_argument("--out", default="outputs/curation/corpus_metadata_issues.tsv")
    parser.add_argument("--report", default="outputs/curation/corpus_metadata_audit.md")
    parser.add_argument("--fail-on-error", action="store_true")
    return parser.parse_args()


def audit(registry: list[dict[str, str]], papers: list[dict[str, str]], *, bib_dir: Path) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    registry_ids = {row.get("paper_id", "") for row in registry if row.get("paper_id")}
    paper_status_by_id = {
        row.get("paper_id", ""): row.get("fulltext_status", "").strip().lower()
        for row in papers
        if row.get("paper_id")
    }
    for row in registry:
        author = row.get("author", "").strip()
        title = row.get("title", "").strip()
        year = row.get("year", "").strip()
        relpath = row.get("local_pdf_relpath", "").strip()
        if weak_author(author):
            add_issue(issues, "warning", "weak_author", row)
        if weak_title(title):
            add_issue(issues, "warning", "weak_title", row)
        if not re.fullmatch(r"(?:19|20)\d{2}", year):
            add_issue(issues, "warning", "invalid_year", row)
        if relpath and not (bib_dir / relpath).is_file():
            if paper_status_by_id.get(row.get("paper_id", "")) == "archived":
                add_issue(issues, "warning", "archived_pdf_unavailable", row)
            else:
                add_issue(issues, "error", "missing_pdf", row)

    by_doi: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in registry:
        doi = normalize_doi(row.get("doi", ""))
        if doi:
            by_doi[doi].append(row)
    for doi, rows in by_doi.items():
        if len(rows) > 1:
            for row in rows:
                add_issue(issues, "error", "duplicate_doi", row)

    for row in papers:
        status = row.get("fulltext_status", "").strip().lower()
        if status == "duplicate":
            continue
        if row.get("paper_id", "") not in registry_ids:
            add_issue(issues, "error", "paper_missing_from_registry", row)
        relpath = row.get("local_pdf_relpath", "").strip()
        if status not in INACTIVE_FULLTEXT_STATUSES and relpath and not (bib_dir / relpath).is_file():
            add_issue(issues, "error", "active_paper_missing_pdf", row)
    return sorted(issues, key=lambda row: (row["severity"] != "error", row["issue"], row["paper_id"], row["title"]))


def weak_author(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", value.lower())
    if normalized in WEAK_AUTHORS or normalized.startswith("annaimport"):
        return True
    if any(marker in value.lower() for marker in GROUP_AUTHOR_MARKERS):
        return False
    return bool(re.search(r"\d", value))


def weak_title(value: str) -> bool:
    if not value.strip():
        return True
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in TITLE_ARTIFACTS)


def add_issue(issues: list[dict[str, str]], severity: str, issue: str, row: dict[str, str]) -> None:
    issues.append({field: row.get(field, "") for field in ISSUE_FIELDS} | {"severity": severity, "issue": issue})


def write_report(path: Path, registry: list[dict[str, str]], papers: list[dict[str, str]], issues: list[dict[str, str]]) -> None:
    counts: dict[str, int] = defaultdict(int)
    for row in issues:
        counts[row["issue"]] += 1
    lines = [
        "# Corpus Metadata Audit",
        "",
        f"- Registry rows: {len(registry)}",
        f"- Active paper rows: {active_paper_count(papers)}",
        f"- Total issues: {len(issues)}",
        f"- Errors: {sum(row['severity'] == 'error' for row in issues)}",
        f"- Warnings: {sum(row['severity'] == 'warning' for row in issues)}",
        "",
        "## Issues by Type",
        "",
    ]
    lines.extend(f"- {name}: {count}" for name, count in sorted(counts.items()))
    lines.extend(["", "See `corpus_metadata_issues.tsv` for row-level details.", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def normalize_doi(value: str) -> str:
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", (value or "").strip().lower())


def active_paper_count(rows: list[dict[str, str]]) -> int:
    return sum(
        row.get("fulltext_status", "").strip().lower() not in INACTIVE_FULLTEXT_STATUSES
        for row in rows
    )


def resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def display(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix() if path.is_relative_to(REPO_ROOT) else str(path)


if __name__ == "__main__":
    raise SystemExit(main())
