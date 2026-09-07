from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    args = parse_args()
    inventory = read_tsv(REPO_ROOT / "data/metadata/local_pdf_inventory.tsv")
    candidates = read_tsv(REPO_ROOT / "data/metadata/corpus_candidates.tsv")
    manifest = read_tsv(REPO_ROOT / "data/metadata/corpus_manifest.tsv")
    override_results = read_tsv(REPO_ROOT / "outputs/curation/active_mvp_override_results.tsv")
    out_path = resolve_repo_path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        build_report(inventory, candidates, manifest, override_results),
        encoding="utf-8",
    )
    print(f"Wrote corpus status report to {out_path.relative_to(REPO_ROOT)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write corpus curation status report.")
    parser.add_argument("--out", default="outputs/curation/corpus_status_report.md")
    return parser.parse_args()


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def build_report(
    inventory: list[dict[str, str]],
    candidates: list[dict[str, str]],
    manifest: list[dict[str, str]],
    override_results: list[dict[str, str]],
) -> str:
    candidate_status = Counter(row.get("match_status", "") for row in candidates)
    manifest_status = Counter(row.get("activation_status", "") for row in manifest)
    categories = Counter(row.get("category", "unknown") or "unknown" for row in manifest)
    failed_pdfs = [row for row in inventory if row.get("pdf_read_status") != "ok"]
    unknown_manifest = [row for row in manifest if row.get("category") in {"", "unknown"}]
    matched_pdf_ids = {
        row.get("matched_pdf_id", "")
        for row in candidates
        if row.get("match_status") in {"matched", "ambiguous"} and row.get("matched_pdf_id")
    }
    unmatched_pdfs = [row for row in inventory if row.get("pdf_id") not in matched_pdf_ids]
    active = [row for row in manifest if row.get("activation_status") == "active_mvp"]
    needs_review_candidate_ids = {
        candidate_id_from_notes(row.get("notes", ""))
        for row in manifest
        if row.get("activation_status") == "needs_review"
    }
    needs_review_candidate_ids.discard("")
    ambiguous = [
        row
        for row in candidates
        if row.get("match_status") == "ambiguous"
        and row.get("candidate_id", "") in needs_review_candidate_ids
    ]
    excluded = [row for row in manifest if row.get("activation_status") == "excluded"]
    forced_missing = [
        row
        for row in override_results
        if row.get("action") == "force_include" and row.get("result") != "forced_included"
    ]
    exclude_not_found = [
        row
        for row in override_results
        if row.get("action") == "force_exclude" and row.get("result", "").endswith("_not_found")
    ]
    active_categories = Counter(row.get("category", "unknown") or "unknown" for row in active)
    year_bins = count_year_bins(active)

    lines = [
        "# Corpus Status Report",
        "",
        "## Summary",
        "",
        f"- PDFs in bib: {len(inventory)}",
        f"- Literature entries: {len(candidates)}",
        f"- Matched: {candidate_status.get('matched', 0)}",
        f"- Ambiguous: {candidate_status.get('ambiguous', 0)}",
        f"- Not found: {candidate_status.get('not_found', 0)}",
        f"- Candidate: {manifest_status.get('candidate', 0)}",
        f"- Active MVP: {manifest_status.get('active_mvp', 0)}",
        f"- Excluded: {manifest_status.get('excluded', 0)}",
        f"- Needs review: {manifest_status.get('needs_review', 0)}",
        "",
        "## Counts By Category",
        "",
        "| category | count |",
        "| --- | ---: |",
    ]
    for category, count in sorted(categories.items()):
        lines.append(f"| {category} | {count} |")

    lines.extend(
        [
            "",
            "## Active MVP Composition Check",
            "",
            f"- Total active_mvp: {len(active)}",
            f"- var_pfemp1: {active_categories.get('var_pfemp1', 0)}",
            f"- tools_ml: {active_categories.get('tools_ml', 0)}",
            f"- sex_diff: {active_categories.get('sex_diff', 0)}",
            f"- host_immunity: {active_categories.get('host_immunity', 0)}",
            "",
            "| active_mvp category | count |",
            "| --- | ---: |",
        ]
    )
    for category, count in sorted(active_categories.items()):
        lines.append(f"| {category} | {count} |")

    lines.extend(
        [
            "",
            "| year bin | count |",
            "| --- | ---: |",
        ]
    )
    for label in ["before_2010", "2010_2019", "2020_plus", "unknown"]:
        lines.append(f"| {label} | {year_bins.get(label, 0)} |")

    composition_warnings = composition_check_warnings(active_categories)
    lines.extend(["", "### Composition Warnings", ""])
    if composition_warnings:
        lines.extend(f"- {warning}" for warning in composition_warnings)
    else:
        lines.append("- Active MVP composition passes the current minimum checks.")

    lines.extend(
        [
            "",
            "## Forced Includes Missing or Not Matched",
            "",
            "| author | year | title_contains | result | message | reason |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    if forced_missing:
        for row in forced_missing:
            lines.append(
                "| {author} | {year} | {title} | {result} | {message} | {reason} |".format(
                    author=escape_md(row.get("author", "")),
                    year=row.get("year", ""),
                    title=escape_md(row.get("title_contains", "")),
                    result=escape_md(row.get("result", "")),
                    message=escape_md(row.get("message", "")),
                    reason=escape_md(row.get("reason", "")),
                )
            )
    else:
        lines.append("|  |  |  | none |  |  |")

    lines.extend(
        [
            "",
            "## Excluded From MVP",
            "",
            "| paper_id | category | year | author | title | notes |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    if excluded:
        for row in sorted(excluded, key=lambda item: (item.get("year", ""), item.get("author", "")), reverse=True):
            lines.append(
                "| {paper_id} | {category} | {year} | {author} | {title} | {notes} |".format(
                    paper_id=row.get("paper_id", ""),
                    category=row.get("category", ""),
                    year=row.get("year", ""),
                    author=escape_md(row.get("author", "")),
                    title=escape_md(row.get("title", "")),
                    notes=escape_md(row.get("notes", "")),
                )
            )
    else:
        lines.append("|  |  |  |  | none |  |")

    if exclude_not_found:
        lines.extend(
            [
                "",
                "### Exclude Overrides Not Found",
                "",
                "| author | year | title_contains | reason |",
                "| --- | --- | --- | --- |",
            ]
        )
        for row in exclude_not_found:
            lines.append(
                "| {author} | {year} | {title} | {reason} |".format(
                    author=escape_md(row.get("author", "")),
                    year=row.get("year", ""),
                    title=escape_md(row.get("title_contains", "")),
                    reason=escape_md(row.get("reason", "")),
                )
            )

    lines.extend(
        [
            "",
            "## Active MVP Papers",
            "",
            "| priority | paper_id | category | year | author | title |",
            "| ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for row in sorted(active, key=priority_sort_key):
        lines.append(
            "| {priority} | {paper_id} | {category} | {year} | {author} | {title} |".format(
                priority=row.get("priority", ""),
                paper_id=row.get("paper_id", ""),
                category=row.get("category", ""),
                year=row.get("year", ""),
                author=escape_md(row.get("author", "")),
                title=escape_md(row.get("title", "")),
            )
        )

    lines.extend(
        [
            "",
            "## Ambiguous Papers Needing Review",
            "",
            "| candidate_id | score | year | author | title | top alternatives |",
            "| --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for row in ambiguous[:100]:
        lines.append(
            "| {candidate_id} | {score} | {year} | {author} | {title} | {alts} |".format(
                candidate_id=row.get("candidate_id", ""),
                score=row.get("match_score", ""),
                year=row.get("year", ""),
                author=escape_md(row.get("author", "")),
                title=escape_md(row.get("title", "")),
                alts=escape_md(format_alternatives(row.get("alternative_matches", ""))),
            )
        )

    lines.extend(
        [
            "",
            "## PDFs Not Matched To Literature List",
            "",
            "| relpath | read_status | message |",
            "| --- | --- | --- |",
        ]
    )
    for row in unmatched_pdfs[:150]:
        lines.append(
            f"| {escape_md(row.get('relpath_from_bib', ''))} | "
            f"{row.get('pdf_read_status', '')} | {escape_md(row.get('message', ''))} |"
        )
    if len(unmatched_pdfs) > 150:
        lines.append(f"| ... | ... | {len(unmatched_pdfs) - 150} more omitted |")

    warnings = []
    if failed_pdfs:
        warnings.append(f"{len(failed_pdfs)} PDFs could not be read.")
    if len(unknown_manifest) > max(10, len(manifest) * 0.15):
        warnings.append(f"{len(unknown_manifest)} manifest rows have unknown category.")
    if candidate_status.get("ambiguous", 0) > max(10, len(candidates) * 0.15):
        warnings.append("Many literature entries are ambiguous; review matching thresholds or duplicates.")
    warnings.extend(composition_warnings)

    lines.extend(["", "## Warnings", ""])
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- No major curation warnings.")

    lines.append("")
    return "\n".join(lines)


def count_year_bins(rows: list[dict[str, str]]) -> Counter[str]:
    bins: Counter[str] = Counter()
    for row in rows:
        year = safe_int(row.get("year"), None)
        if year is None:
            bins["unknown"] += 1
        elif year < 2010:
            bins["before_2010"] += 1
        elif year < 2020:
            bins["2010_2019"] += 1
        else:
            bins["2020_plus"] += 1
    return bins


def composition_check_warnings(active_categories: Counter[str]) -> list[str]:
    warnings = []
    if active_categories.get("var_pfemp1", 0) < 20:
        warnings.append("var_pfemp1 count is below 20.")
    if active_categories.get("tools_ml", 0) < 5:
        warnings.append("tools_ml count is below 5.")
    if active_categories.get("host_immunity", 0) < 5:
        warnings.append("host_immunity count is below 5.")
    return warnings


def priority_sort_key(row: dict[str, str]) -> tuple[int, str]:
    return safe_int(row.get("priority"), 9999) or 9999, row.get("paper_id", "")


def candidate_id_from_notes(notes: str) -> str:
    for part in (notes or "").split(";"):
        part = part.strip()
        if part.startswith("candidate_id="):
            return part.removeprefix("candidate_id=").strip()
    return ""


def safe_int(value: str | None, default: int | None) -> int | None:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return default


def escape_md(value: str) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")


def format_alternatives(raw_json: str) -> str:
    try:
        alternatives = json.loads(raw_json or "[]")
    except json.JSONDecodeError:
        return raw_json
    return "; ".join(
        f"{item.get('score', '')}:{item.get('relpath', '')}" for item in alternatives[:3]
    )


if __name__ == "__main__":
    raise SystemExit(main())
