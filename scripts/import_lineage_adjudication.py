from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pfempire.trace.io import read_tsv, write_tsv  # noqa: E402


FINE_RELATIONS = {
    "same_publication",
    "same_study_or_cohort",
    "publication_family",
    "reanalysis_or_shared_dataset",
    "distinct_origin",
    "unresolved",
}
ORIGIN_MAPPINGS = {"same_origin", "distinct_origin", "unresolved"}
OVERRIDE_FIELDS = [
    "paper_id_a",
    "paper_id_b",
    "action",
    "dependence_score_override",
    "distinct_dependence_lower_override",
    "distinct_dependence_upper_override",
    "relationship_type",
    "reason",
    "reviewer",
    "origin_review_status",
    "adjudicator",
    "review_timestamp",
    "evidence_locator",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate one question's double-reviewed origin pairs and emit strict "
            "TRACE lineage overrides."
        )
    )
    parser.add_argument("--review", required=True)
    parser.add_argument("--question-id", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rows = [
        row
        for row in read_tsv(resolve(args.review))
        if str(row.get("question_id") or "").strip() == args.question_id
    ]
    overrides = adjudication_overrides(rows, question_id=args.question_id)
    write_tsv(resolve(args.out), overrides, OVERRIDE_FIELDS)
    print(
        f"Validated {len(overrides)} adjudicated pairs for {args.question_id}; "
        f"overrides: {display(resolve(args.out))}"
    )
    return 0


def adjudication_overrides(
    rows: list[dict[str, str]], *, question_id: str
) -> list[dict[str, str]]:
    if not rows:
        raise ValueError(f"no review rows found for question_id {question_id!r}")
    paper_ids = sorted(
        {
            str(row.get(field) or "").strip()
            for row in rows
            for field in ("paper_id_a", "paper_id_b")
            if str(row.get(field) or "").strip()
        }
    )
    expected_pairs = {
        (paper_ids[left], paper_ids[right])
        for left in range(len(paper_ids))
        for right in range(left + 1, len(paper_ids))
    }
    actual_pairs: set[tuple[str, str]] = set()
    overrides = []
    for row_number, row in enumerate(rows, start=2):
        left = required(row, "paper_id_a", row_number)
        right = required(row, "paper_id_b", row_number)
        if left == right:
            raise ValueError(f"self-pair at review row {row_number}")
        pair = tuple(sorted((left, right)))
        if pair in actual_pairs:
            raise ValueError(f"duplicate pair {pair[0]} / {pair[1]}")
        actual_pairs.add(pair)
        overrides.append(adjudicated_row(row, row_number, pair))
    missing = sorted(expected_pairs - actual_pairs)
    extra = sorted(actual_pairs - expected_pairs)
    if missing or extra or len(paper_ids) != 5:
        raise ValueError(
            f"{question_id} must contain all 10 pairs for exactly five papers; "
            f"papers={len(paper_ids)}, rows={len(rows)}, missing={missing}, extra={extra}"
        )
    return sorted(overrides, key=lambda row: (row["paper_id_a"], row["paper_id_b"]))


def adjudicated_row(
    row: dict[str, str], row_number: int, pair: tuple[str, str]
) -> dict[str, str]:
    reviewer_1 = required(row, "reviewer_1_id", row_number)
    reviewer_2 = required(row, "reviewer_2_id", row_number)
    if reviewer_1 == reviewer_2:
        raise ValueError(f"reviewers must be distinct at review row {row_number}")
    for prefix in ("reviewer_1", "reviewer_2"):
        relation = required(row, f"{prefix}_relation", row_number)
        if relation not in FINE_RELATIONS:
            raise ValueError(f"invalid {prefix}_relation at review row {row_number}")
        validate_timestamp(required(row, f"{prefix}_timestamp", row_number), row_number)
        required(row, f"{prefix}_evidence_locator", row_number)
        required(row, f"{prefix}_rationale", row_number)

    relation = required(row, "adjudicated_relation", row_number)
    if relation not in FINE_RELATIONS:
        raise ValueError(f"invalid adjudicated_relation at review row {row_number}")
    mapping = required(row, "adjudicated_origin_mapping", row_number)
    if mapping not in ORIGIN_MAPPINGS:
        raise ValueError(f"invalid adjudicated_origin_mapping at review row {row_number}")
    expected_mapping = {
        "same_publication": "same_origin",
        "same_study_or_cohort": "same_origin",
        "distinct_origin": "distinct_origin",
        "unresolved": "unresolved",
    }.get(relation)
    if expected_mapping and mapping != expected_mapping:
        raise ValueError(
            f"{relation} must map to {expected_mapping} at review row {row_number}"
        )
    if mapping == "unresolved" or relation == "unresolved":
        raise ValueError(
            f"unresolved adjudication at review row {row_number}; trusted graph remains blocked"
        )
    if required(row, "review_status", row_number) != "adjudicated":
        raise ValueError(f"review_status must be adjudicated at review row {row_number}")

    adjudicator = required(row, "adjudicator_id", row_number)
    timestamp = required(row, "adjudication_timestamp", row_number)
    validate_timestamp(timestamp, row_number)
    evidence_locator = required(row, "adjudication_evidence_locator", row_number)
    rationale = required(row, "adjudication_rationale", row_number)

    if mapping == "same_origin":
        action = (
            "mark_same_publication"
            if relation == "same_publication"
            else "mark_same_study"
        )
        lower = upper = ""
    else:
        action = "mark_distinct_origin"
        lower = bounded_number(row, "distinct_dependence_lower", row_number)
        upper = bounded_number(row, "distinct_dependence_upper", row_number)
        if float(lower) > float(upper):
            raise ValueError(
                f"distinct dependence lower exceeds upper at review row {row_number}"
            )

    return {
        "paper_id_a": pair[0],
        "paper_id_b": pair[1],
        "action": action,
        "dependence_score_override": "",
        "distinct_dependence_lower_override": lower,
        "distinct_dependence_upper_override": upper,
        "relationship_type": relation,
        "reason": rationale,
        "reviewer": f"{reviewer_1};{reviewer_2}",
        "origin_review_status": "adjudicated",
        "adjudicator": adjudicator,
        "review_timestamp": timestamp,
        "evidence_locator": evidence_locator,
    }


def required(row: dict[str, str], field: str, row_number: int) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} is required at review row {row_number}")
    return value


def bounded_number(row: dict[str, str], field: str, row_number: int) -> str:
    value = required(row, field, row_number)
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be numeric at review row {row_number}") from exc
    if not 0.0 <= parsed <= 1.0:
        raise ValueError(f"{field} must be within [0, 1] at review row {row_number}")
    return value


def validate_timestamp(value: str, row_number: int) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"adjudication_timestamp must be ISO-8601 at review row {row_number}"
        ) from exc


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
