from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path


BENCHMARK_FIELDS = [
    "item_id",
    "split",
    "review_status",
    "task_mode",
    "difficulty",
    "question",
    "expected_answer_nuggets",
    "expected_facets",
    "expected_paper_ids",
    "expected_chunk_ids",
    "expected_stance",
    "abstain_expected",
    "provenance",
    "generator_model",
    "reviewer",
    "review_notes",
]

ALLOWED_TASK_MODES = {
    "evidence_qa",
    "mechanism_mapping",
    "dataset_finder",
    "method_comparison",
    "gap_finder",
    "therapeutic_target_finder",
    "hypothesis_generator",
    "contradiction_finder",
    "source_lookup",
}
ALLOWED_REVIEW_STATUS = {"candidate", "accepted", "rejected"}
ALLOWED_SPLITS = {"candidate", "development", "test"}
ALLOWED_DIFFICULTIES = {"easy", "medium", "hard"}
ALLOWED_STANCES = {"supports", "refutes", "mixed", "not_enough_evidence", "not_applicable"}


def empty_item() -> dict[str, str]:
    return {field: "" for field in BENCHMARK_FIELDS}


def normalize_item(raw: dict[str, object]) -> dict[str, str]:
    item = empty_item()
    multi_value_fields = {
        "expected_answer_nuggets",
        "expected_facets",
        "expected_paper_ids",
        "expected_chunk_ids",
    }
    for field in BENCHMARK_FIELDS:
        if field in multi_value_fields:
            continue
        item[field] = clean(raw.get(field))
    item["split"] = item["split"] or "candidate"
    item["review_status"] = item["review_status"] or "candidate"
    item["task_mode"] = item["task_mode"] or "evidence_qa"
    item["difficulty"] = item["difficulty"] or "medium"
    item["expected_stance"] = item["expected_stance"] or "not_applicable"
    item["abstain_expected"] = normalize_bool(item["abstain_expected"])
    for field in multi_value_fields:
        item[field] = join_semicolon(split_semicolon(raw.get(field)))
    return item


def validate_benchmark(
    rows: list[dict[str, str]],
    *,
    papers_tsv: Path,
    chunks_dir: Path,
    require_gold: bool = False,
) -> dict:
    paper_ids = load_paper_ids(papers_tsv)
    chunk_ids = load_chunk_ids(chunks_dir)
    issues: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_questions: dict[str, str] = {}

    for index, raw in enumerate(rows, start=2):
        item = normalize_item(raw)
        item_id = item["item_id"]
        if not item_id:
            add_issue(issues, index, item_id, "error", "missing_item_id")
        elif item_id in seen_ids:
            add_issue(issues, index, item_id, "error", "duplicate_item_id")
        seen_ids.add(item_id)

        question_key = normalize_question(item["question"])
        if not question_key:
            add_issue(issues, index, item_id, "error", "missing_question")
        elif question_key in seen_questions:
            add_issue(
                issues,
                index,
                item_id,
                "warning",
                f"duplicate_question:{seen_questions[question_key]}",
            )
        else:
            seen_questions[question_key] = item_id

        check_choice(issues, index, item_id, "split", item["split"], ALLOWED_SPLITS)
        check_choice(
            issues,
            index,
            item_id,
            "review_status",
            item["review_status"],
            ALLOWED_REVIEW_STATUS,
        )
        check_choice(issues, index, item_id, "task_mode", item["task_mode"], ALLOWED_TASK_MODES)
        check_choice(issues, index, item_id, "difficulty", item["difficulty"], ALLOWED_DIFFICULTIES)
        check_choice(
            issues,
            index,
            item_id,
            "expected_stance",
            item["expected_stance"],
            ALLOWED_STANCES,
        )

        for paper_id in split_semicolon(item["expected_paper_ids"]):
            if paper_id not in paper_ids:
                add_issue(issues, index, item_id, "error", f"unknown_paper_id:{paper_id}")
        for chunk_id in split_semicolon(item["expected_chunk_ids"]):
            if chunk_id not in chunk_ids:
                add_issue(issues, index, item_id, "error", f"unknown_chunk_id:{chunk_id}")

        is_gold = item["review_status"] == "accepted"
        if require_gold and not is_gold:
            add_issue(issues, index, item_id, "error", "non_accepted_item_in_gold_export")
        if is_gold:
            if item["split"] == "candidate":
                add_issue(issues, index, item_id, "error", "accepted_item_missing_eval_split")
            if not item["reviewer"]:
                add_issue(issues, index, item_id, "error", "accepted_item_missing_reviewer")
            if not item["expected_answer_nuggets"]:
                add_issue(issues, index, item_id, "error", "accepted_item_missing_answer_nuggets")
            if item["abstain_expected"] != "true" and not (
                item["expected_paper_ids"] or item["expected_chunk_ids"]
            ):
                add_issue(issues, index, item_id, "error", "accepted_item_missing_gold_evidence")

    severity_counts = Counter(issue["severity"] for issue in issues)
    status_counts = Counter(normalize_item(row)["review_status"] for row in rows)
    task_counts = Counter(normalize_item(row)["task_mode"] for row in rows)
    return {
        "valid": severity_counts["error"] == 0,
        "n_items": len(rows),
        "n_papers": len(paper_ids),
        "n_chunks": len(chunk_ids),
        "status_counts": dict(sorted(status_counts.items())),
        "task_counts": dict(sorted(task_counts.items())),
        "severity_counts": dict(sorted(severity_counts.items())),
        "issues": issues,
    }


def accepted_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [normalize_item(row) for row in rows if normalize_item(row)["review_status"] == "accepted"]


def benchmark_coverage(rows: list[dict[str, str]]) -> dict:
    normalized = [normalize_item(row) for row in rows]
    facets = Counter(
        facet
        for row in normalized
        for facet in split_semicolon(row["expected_facets"])
    )
    return {
        "n_items": len(normalized),
        "task_modes": dict(sorted(Counter(row["task_mode"] for row in normalized).items())),
        "difficulties": dict(sorted(Counter(row["difficulty"] for row in normalized).items())),
        "splits": dict(sorted(Counter(row["split"] for row in normalized).items())),
        "review_status": dict(
            sorted(Counter(row["review_status"] for row in normalized).items())
        ),
        "provenance": dict(sorted(Counter(row["provenance"] for row in normalized).items())),
        "facets": dict(sorted(facets.items(), key=lambda pair: (-pair[1], pair[0]))),
        "n_abstention": sum(row["abstain_expected"] == "true" for row in normalized),
        "n_with_paper_gold": sum(bool(row["expected_paper_ids"]) for row in normalized),
        "n_with_chunk_gold": sum(bool(row["expected_chunk_ids"]) for row in normalized),
        "n_with_reviewer": sum(bool(row["reviewer"]) for row in normalized),
    }


def write_coverage_report(path: Path, coverage: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n_items = int(coverage.get("n_items", 0))
    recommendations = []
    if int(coverage.get("n_with_reviewer", 0)) == 0:
        recommendations.append("Expert-review and accept the first 20-30 candidate items.")
    if int(coverage.get("n_abstention", 0)) == 0:
        recommendations.append("Add answerable-looking negative items that require abstention.")
    if len(coverage.get("task_modes", {})) < 6:
        recommendations.append("Expand task-mode coverage before freezing the test split.")
    if not coverage.get("facets"):
        recommendations.append("Annotate phenotype, mechanism, dataset, and method facets.")
    if int(coverage.get("n_with_chunk_gold", 0)) < max(1, n_items // 2):
        recommendations.append("Bind accepted answer nuggets to verified chunk-level evidence.")

    lines = [
        "# MalariaEvidenceBench Coverage",
        "",
        f"- Items: {n_items}",
        f"- Reviewed: {coverage.get('n_with_reviewer', 0)}",
        f"- With paper gold: {coverage.get('n_with_paper_gold', 0)}",
        f"- With chunk gold: {coverage.get('n_with_chunk_gold', 0)}",
        f"- Abstention items: {coverage.get('n_abstention', 0)}",
        f"- Task modes: {format_counts(coverage.get('task_modes', {}))}",
        f"- Difficulty: {format_counts(coverage.get('difficulties', {}))}",
        f"- Review status: {format_counts(coverage.get('review_status', {}))}",
        f"- Provenance: {format_counts(coverage.get('provenance', {}))}",
        "",
        "## Facets",
        "",
    ]
    facets = coverage.get("facets", {})
    lines.extend(f"- {facet}: {count}" for facet, count in facets.items())
    if not facets:
        lines.append("- none annotated")
    lines.extend(["", "## Next Actions", ""])
    lines.extend(f"{index}. {value}" for index, value in enumerate(recommendations, start=1))
    if not recommendations:
        lines.append("1. Freeze the test split and run baselines plus ablations.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_tsv(path: Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return [normalize_item(dict(row)) for row in csv.DictReader(handle, delimiter="\t")]


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=BENCHMARK_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(normalize_item(row))


def write_validation_report(path: Path, report: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# MalariaEvidenceBench Validation",
        "",
        f"- Valid: {report.get('valid')}",
        f"- Items: {report.get('n_items', 0)}",
        f"- Corpus papers: {report.get('n_papers', 0)}",
        f"- Corpus chunks: {report.get('n_chunks', 0)}",
        f"- Review status: {format_counts(report.get('status_counts', {}))}",
        f"- Task modes: {format_counts(report.get('task_counts', {}))}",
        f"- Issues: {format_counts(report.get('severity_counts', {}))}",
        "",
        "| row | item | severity | issue |",
        "| ---: | --- | --- | --- |",
    ]
    for issue in report.get("issues", []):
        lines.append(
            f"| {issue.get('row', '')} | {issue.get('item_id', '') or '-'} | "
            f"{issue.get('severity', '')} | {issue.get('issue', '')} |"
        )
    if not report.get("issues"):
        lines.append("| - | - | - | none |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_paper_ids(path: Path) -> set[str]:
    if not Path(path).exists():
        return set()
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return {
            clean(row.get("paper_id"))
            for row in csv.DictReader(handle, delimiter="\t")
            if clean(row.get("paper_id"))
        }


def load_chunk_ids(chunks_dir: Path) -> set[str]:
    chunk_ids: set[str] = set()
    for path in sorted(Path(chunks_dir).glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                chunk_id = clean(row.get("chunk_id"))
                if chunk_id:
                    chunk_ids.add(chunk_id)
    return chunk_ids


def split_semicolon(value: object) -> list[str]:
    if isinstance(value, list):
        values = value
    else:
        values = str(value or "").split(";")
    kept: list[str] = []
    seen: set[str] = set()
    for value_item in values:
        item = clean(value_item)
        if item and item not in seen:
            kept.append(item)
            seen.add(item)
    return kept


def join_semicolon(values: list[str]) -> str:
    return ";".join(values)


def normalize_bool(value: object) -> str:
    return "true" if str(value or "").strip().lower() in {"1", "true", "yes", "y"} else "false"


def normalize_question(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def clean(value: object) -> str:
    return " ".join(str(value or "").split())


def check_choice(
    issues: list[dict[str, str]],
    row: int,
    item_id: str,
    field: str,
    value: str,
    allowed: set[str],
) -> None:
    if value not in allowed:
        add_issue(issues, row, item_id, "error", f"invalid_{field}:{value}")


def add_issue(
    issues: list[dict[str, str]],
    row: int,
    item_id: str,
    severity: str,
    issue: str,
) -> None:
    issues.append({"row": str(row), "item_id": item_id, "severity": severity, "issue": issue})


def format_counts(values: dict[str, int]) -> str:
    return ", ".join(f"{key}={value}" for key, value in values.items()) or "none"
