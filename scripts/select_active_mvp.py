from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OVERRIDE_RESULT_FIELDS = [
    "action",
    "match_field",
    "match_value",
    "author",
    "year",
    "title_contains",
    "category",
    "result",
    "matched_paper_id",
    "matched_pdf_relpath",
    "message",
    "reason",
]

DEFAULT_COMPOSITION = {
    "var_pfemp1": 25,
    "host_or_general": 9,
    "sex_diff": 4,
    "tools_ml": 10,
    "resistance_dx": 2,
}

CLASSIC_VAR_AUTHORS = {
    "baruch",
    "bull",
    "gardner",
    "jespersen",
    "lavstsen",
    "miller",
    "newbold",
    "roberts",
    "rowe",
    "salanti",
    "smith",
    "su",
    "turner",
}


def main() -> int:
    args = parse_args()
    manifest_path = resolve_repo_path(args.manifest)
    rows = read_tsv(manifest_path)
    if not rows:
        print(f"No rows in manifest: {manifest_path}")
        return 1

    if not args.keep_existing:
        for row in rows:
            if row.get("activation_status") == "active_mvp":
                row["activation_status"] = "candidate"
                row["priority"] = ""

    selected_ids: set[str] = {
        row["paper_id"] for row in rows if row.get("activation_status") == "active_mvp"
    }
    seen_title_year: set[tuple[str, str]] = {
        title_year_key(row) for row in rows if row["paper_id"] in selected_ids
    }
    selection_order: list[str] = list(selected_ids)

    overrides = read_tsv(resolve_repo_path(args.overrides)) if args.overrides else []
    override_results = apply_overrides(rows, overrides, selected_ids, seen_title_year, selection_order)
    forced_count = len(selected_ids)
    if forced_count > args.target_size:
        print(
            f"Warning: forced includes selected {forced_count} papers, exceeding target "
            f"{args.target_size}; keeping all forced includes."
        )

    if args.var_centered and (args.allow_over_target or len(selected_ids) < args.target_size):
        select_category(rows, selected_ids, seen_title_year, selection_order, "var_pfemp1", DEFAULT_COMPOSITION["var_pfemp1"], args)
        select_combined(
            rows,
            selected_ids,
            seen_title_year,
            selection_order,
            ["host_immunity", "general_malaria"],
            DEFAULT_COMPOSITION["host_or_general"],
            args,
        )
        select_category(rows, selected_ids, seen_title_year, selection_order, "sex_diff", DEFAULT_COMPOSITION["sex_diff"], args)
        select_category(rows, selected_ids, seen_title_year, selection_order, "tools_ml", DEFAULT_COMPOSITION["tools_ml"], args)
        select_category(rows, selected_ids, seen_title_year, selection_order, "resistance_dx", DEFAULT_COMPOSITION["resistance_dx"], args)

    fill_remaining(rows, selected_ids, seen_title_year, selection_order, args.target_size, args)

    for row in rows:
        if row["paper_id"] in selected_ids:
            row["activation_status"] = "active_mvp"
            if row["paper_id"] not in selection_order:
                selection_order.append(row["paper_id"])
            row["priority"] = str(selection_order.index(row["paper_id"]) + 1)
        elif row.get("activation_status") == "active_mvp":
            row["activation_status"] = "candidate"
            row["priority"] = ""

    write_tsv(manifest_path, rows, list(rows[0].keys()))
    write_override_results(resolve_repo_path(args.override_results), override_results)

    active_count = sum(1 for row in rows if row.get("activation_status") == "active_mvp")
    print(f"Selected {active_count} active_mvp papers in {manifest_path.relative_to(REPO_ROOT)}")
    print(f"Wrote override results to {resolve_repo_path(args.override_results).relative_to(REPO_ROOT)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select a curated var-centered active MVP corpus.")
    parser.add_argument("--manifest", default="data/metadata/corpus_manifest.tsv")
    parser.add_argument("--target-size", type=int, default=50)
    parser.add_argument("--var-centered", action="store_true")
    parser.add_argument("--overrides", default="data/metadata/curation_overrides.tsv")
    parser.add_argument(
        "--override-results",
        default="outputs/curation/active_mvp_override_results.tsv",
    )
    parser.add_argument("--keep-existing", action="store_true")
    parser.add_argument("--allow-over-target", action="store_true")
    parser.add_argument("--allow-needs-review", action="store_true")
    parser.add_argument("--allow-low-quality-metadata", action="store_true")
    return parser.parse_args()


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def write_tsv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_override_results(path: Path, rows: list[dict[str, str]]) -> None:
    write_tsv(path, rows, OVERRIDE_RESULT_FIELDS)


def apply_overrides(
    rows: list[dict[str, str]],
    overrides: list[dict[str, str]],
    selected_ids: set[str],
    seen_title_year: set[tuple[str, str]],
    selection_order: list[str],
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for action in ["force_exclude", "needs_review", "force_include"]:
        for override in [row for row in overrides if row.get("action") == action]:
            matches = find_override_matches(rows, override)
            if action == "force_include":
                results.append(
                    apply_force_include(
                        rows,
                        matches,
                        override,
                        selected_ids,
                        seen_title_year,
                        selection_order,
                    )
                )
            elif action == "force_exclude":
                results.extend(apply_status_override(matches, override, "excluded", selected_ids, selection_order))
            elif action == "needs_review":
                results.extend(apply_status_override(matches, override, "needs_review", selected_ids, selection_order))
    return results


def apply_force_include(
    rows: list[dict[str, str]],
    matches: list[dict[str, str]],
    override: dict[str, str],
    selected_ids: set[str],
    seen_title_year: set[tuple[str, str]],
    selection_order: list[str],
) -> dict[str, str]:
    result = base_override_result(override)
    if not matches:
        result.update(
            {
                "result": "force_include_not_found",
                "message": "No matching corpus_manifest row.",
            }
        )
        return result

    with_pdf = [row for row in matches if row.get("local_pdf_relpath")]
    if not with_pdf:
        result.update(
            {
                "result": "force_include_without_matched_pdf",
                "matched_paper_id": ";".join(row.get("paper_id", "") for row in matches),
                "message": "Matched manifest row has no local_pdf_relpath.",
            }
        )
        return result

    row = sorted(with_pdf, key=lambda item: (-score(item), item.get("paper_id", "")))[0]
    if row.get("activation_status") == "excluded":
        result.update(
            {
                "result": "force_include_blocked_by_exclude",
                "matched_paper_id": row.get("paper_id", ""),
                "matched_pdf_relpath": row.get("local_pdf_relpath", ""),
                "message": "A force_exclude override matched this paper first.",
            }
        )
        return result

    row["activation_status"] = "active_mvp"
    selected_ids.add(row["paper_id"])
    seen_title_year.add(title_year_key(row))
    if row["paper_id"] not in selection_order:
        selection_order.append(row["paper_id"])
    result.update(
        {
            "result": "forced_included",
            "matched_paper_id": row.get("paper_id", ""),
            "matched_pdf_relpath": row.get("local_pdf_relpath", ""),
            "message": "Forced into active_mvp.",
        }
    )
    return result


def apply_status_override(
    matches: list[dict[str, str]],
    override: dict[str, str],
    status: str,
    selected_ids: set[str],
    selection_order: list[str],
) -> list[dict[str, str]]:
    result = base_override_result(override)
    if not matches:
        result["result"] = f"{status}_not_found"
        result["message"] = "No matching corpus_manifest row."
        return [result]

    rows = []
    for row in matches:
        row["activation_status"] = status
        row["priority"] = ""
        selected_ids.discard(row.get("paper_id", ""))
        if row.get("paper_id") in selection_order:
            selection_order.remove(row["paper_id"])
        one = base_override_result(override)
        one.update(
            {
                "result": status,
                "matched_paper_id": row.get("paper_id", ""),
                "matched_pdf_relpath": row.get("local_pdf_relpath", ""),
                "message": f"Set activation_status={status}.",
            }
        )
        rows.append(one)
    return rows


def base_override_result(override: dict[str, str]) -> dict[str, str]:
    return {
        "action": override.get("action", ""),
        "match_field": override.get("match_field", ""),
        "match_value": override.get("match_value", ""),
        "author": override.get("author", ""),
        "year": override.get("year", ""),
        "title_contains": override.get("title_contains", ""),
        "category": override.get("category", ""),
        "result": "",
        "matched_paper_id": "",
        "matched_pdf_relpath": "",
        "message": "",
        "reason": override.get("reason", ""),
    }


def find_override_matches(rows: list[dict[str, str]], override: dict[str, str]) -> list[dict[str, str]]:
    return [row for row in rows if override_matches(row, override)]


def override_matches(row: dict[str, str], override: dict[str, str]) -> bool:
    match_field = (override.get("match_field") or "auto").strip().lower()
    match_value = (override.get("match_value") or "").strip()
    if match_field == "paper_id":
        return row.get("paper_id") == match_value
    if match_field == "category":
        return row.get("category") == match_value
    if match_field == "author_year":
        return author_year_matches(row, override)
    if match_field == "title_contains":
        return title_contains_matches(row, override.get("title_contains") or match_value)
    if match_field == "author_year_title":
        return author_year_matches(row, override) and title_contains_matches(
            row, override.get("title_contains") or match_value
        )

    checks = []
    if override.get("author") or override.get("year"):
        checks.append(author_year_matches(row, override))
    if override.get("title_contains"):
        checks.append(title_contains_matches(row, override["title_contains"]))
    if override.get("category"):
        checks.append(row.get("category") == override["category"])
    if match_value and not checks:
        checks.append(title_contains_matches(row, match_value))
    return bool(checks) and all(checks)


def author_year_matches(row: dict[str, str], override: dict[str, str]) -> bool:
    author_ok = not override.get("author") or normalize(row.get("author", "")) == normalize(override["author"])
    year_ok = not override.get("year") or row.get("year", "") == override["year"]
    return author_ok and year_ok


def title_contains_matches(row: dict[str, str], needle: str) -> bool:
    if not needle:
        return True
    return normalize(needle) in normalize(row.get("title", ""))


def normalize(value: str) -> str:
    value = (value or "").lower()
    value = value.replace("α", "alpha").replace("β", "beta")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def select_category(
    rows: list[dict[str, str]],
    selected_ids: set[str],
    seen_title_year: set[tuple[str, str]],
    selection_order: list[str],
    category: str,
    target_count: int,
    args: argparse.Namespace,
) -> None:
    need = max(0, target_count - selected_count(rows, selected_ids, [category]))
    candidates = [
        row
        for row in eligible_rows(rows, selected_ids, seen_title_year, args)
        if row.get("category") == category
    ]
    if category == "var_pfemp1":
        classics = [row for row in candidates if is_classic_var(row)]
        for row in sorted(classics, key=var_classic_sort_key):
            if need <= 0 or selection_limit_reached(selected_ids, args):
                break
            if add_selection(row, selected_ids, seen_title_year, selection_order):
                need -= 1
        for row in sorted(candidates, key=var_general_sort_key):
            if need <= 0 or selection_limit_reached(selected_ids, args):
                break
            if add_selection(row, selected_ids, seen_title_year, selection_order):
                need -= 1
        return

    sort_key = recent_sort_key if category in {"tools_ml", "sex_diff", "resistance_dx"} else quality_sort_key
    for row in sorted(candidates, key=sort_key):
        if need <= 0 or selection_limit_reached(selected_ids, args):
            break
        if add_selection(row, selected_ids, seen_title_year, selection_order):
            need -= 1


def select_combined(
    rows: list[dict[str, str]],
    selected_ids: set[str],
    seen_title_year: set[tuple[str, str]],
    selection_order: list[str],
    categories: list[str],
    target_count: int,
    args: argparse.Namespace,
) -> None:
    need = max(0, target_count - selected_count(rows, selected_ids, categories))
    candidates = [
        row
        for row in eligible_rows(rows, selected_ids, seen_title_year, args)
        if row.get("category") in categories
    ]
    for row in sorted(candidates, key=quality_sort_key):
        if need <= 0 or selection_limit_reached(selected_ids, args):
            break
        if add_selection(row, selected_ids, seen_title_year, selection_order):
            need -= 1


def fill_remaining(
    rows: list[dict[str, str]],
    selected_ids: set[str],
    seen_title_year: set[tuple[str, str]],
    selection_order: list[str],
    target_size: int,
    args: argparse.Namespace,
) -> None:
    for row in sorted(eligible_rows(rows, selected_ids, seen_title_year, args), key=quality_sort_key):
        if len(selected_ids) >= target_size and not args.allow_over_target:
            break
        add_selection(row, selected_ids, seen_title_year, selection_order)


def selected_count(rows: list[dict[str, str]], selected_ids: set[str], categories: list[str]) -> int:
    return sum(1 for row in rows if row.get("paper_id") in selected_ids and row.get("category") in categories)


def selection_limit_reached(selected_ids: set[str], args: argparse.Namespace) -> bool:
    return len(selected_ids) >= args.target_size and not args.allow_over_target


def eligible_rows(
    rows: list[dict[str, str]],
    selected_ids: set[str],
    seen_title_year: set[tuple[str, str]],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    statuses = {"candidate"}
    if args.allow_needs_review:
        statuses.add("needs_review")
    return [
        row
        for row in rows
        if row.get("activation_status") in statuses
        and row.get("paper_id") not in selected_ids
        and row.get("local_pdf_relpath")
        and title_year_key(row) not in seen_title_year
        and (args.allow_low_quality_metadata or not low_quality_title(row.get("title", "")))
    ]


def add_selection(
    row: dict[str, str],
    selected_ids: set[str],
    seen_title_year: set[tuple[str, str]],
    selection_order: list[str],
) -> bool:
    key = title_year_key(row)
    if row.get("paper_id") in selected_ids or key in seen_title_year:
        return False
    selected_ids.add(row["paper_id"])
    seen_title_year.add(key)
    selection_order.append(row["paper_id"])
    return True


def title_year_key(row: dict[str, str]) -> tuple[str, str]:
    title = re.sub(r"\W+", " ", row.get("title", "").lower()).strip()
    return title, row.get("year", "")


def is_classic_var(row: dict[str, str]) -> bool:
    author = row.get("author", "").lower()
    title = row.get("title", "").lower()
    year = safe_int(row.get("year"), 9999)
    return (
        author in CLASSIC_VAR_AUTHORS
        or year <= 2016
        or any(term in title for term in ["cidr", "epcr", "icam", "var gene", "pfemp1", "var2csa"])
    )


def var_classic_sort_key(row: dict[str, str]) -> tuple[int, int, float]:
    year = safe_int(row.get("year"), 9999)
    author_bonus = 0 if row.get("author", "").lower() in CLASSIC_VAR_AUTHORS else 1
    return author_bonus, year, -score(row)


def var_general_sort_key(row: dict[str, str]) -> tuple[float, int]:
    year = safe_int(row.get("year"), 0)
    return -score(row), -year


def recent_sort_key(row: dict[str, str]) -> tuple[float, int]:
    return -score(row), -safe_int(row.get("year"), 0)


def quality_sort_key(row: dict[str, str]) -> tuple[float, int]:
    return -score(row), safe_int(row.get("year"), 9999)


def score(row: dict[str, str]) -> float:
    try:
        return float(row.get("match_score") or 0)
    except ValueError:
        return 0.0


def low_quality_title(title: str) -> bool:
    title_lower = (title or "").lower()
    markers = [
        "europe pmc funders group",
        "reporting summary",
        "contents lists available",
        "microsoft word",
        "supplementary information",
        "g:\\",
        "polyclonal antisera",
        "letters to nature similar",
        "s to nature",
        "bioinformatics\\bioinfo",
        "capns2 cd1b",
        "malaria journal biomed central research",
        "published 12 january",
        "reporting-summary",
        "supplementary figure",
        "pnas plus",
        "dd2 cross",
        "s innate immunity",
        "concise communication",
        "the embo journal vol",
        "christian w. wanga",
    ]
    if any(marker in title_lower for marker in markers):
        return True
    return bool(re.fullmatch(r"[a-z]+_\d{4}", title_lower.strip()))


def safe_int(value: str | None, default: int) -> int:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return default


if __name__ == "__main__":
    raise SystemExit(main())
