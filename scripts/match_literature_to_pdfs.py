from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

try:
    from rapidfuzz import fuzz
except ImportError:
    from difflib import SequenceMatcher

    class _FuzzFallback:
        @staticmethod
        def WRatio(left: str, right: str) -> float:
            return SequenceMatcher(None, left, right).ratio() * 100

        @staticmethod
        def partial_ratio(left: str, right: str) -> float:
            if not left or not right:
                return 0.0
            if left in right or right in left:
                return 100.0
            return SequenceMatcher(None, left, right).ratio() * 100

        @staticmethod
        def token_set_ratio(left: str, right: str) -> float:
            left_tokens = set(left.split())
            right_tokens = set(right.split())
            if not left_tokens or not right_tokens:
                return 0.0
            shared = left_tokens & right_tokens
            if left_tokens <= right_tokens or right_tokens <= left_tokens:
                return 100.0
            left_coverage = len(shared) / len(left_tokens)
            right_coverage = len(shared) / len(right_tokens)
            dice = (2 * len(shared)) / (len(left_tokens) + len(right_tokens))
            return max(left_coverage, right_coverage, dice) * 100

    fuzz = _FuzzFallback()


REPO_ROOT = Path(__file__).resolve().parents[1]

FIELDNAMES = [
    "candidate_id",
    "match_status",
    "match_score",
    "author",
    "year",
    "title",
    "journal",
    "category",
    "matched_pdf_relpath",
    "matched_filename",
    "matched_pdf_id",
    "alternative_matches",
    "message",
]


def main() -> int:
    args = parse_args()
    literature = read_tsv(resolve_repo_path(args.literature))
    inventory = read_tsv(resolve_repo_path(args.inventory))
    out_path = resolve_repo_path(args.out)
    rows = []

    for index, entry in enumerate(literature, start=1):
        scored = sorted(
            (score_match(entry, pdf) for pdf in inventory),
            key=lambda item: item["score"],
            reverse=True,
        )
        best = scored[0] if scored else None
        second = scored[1] if len(scored) > 1 else None
        status, message = decide_status(best, second)
        matched_pdf = best["pdf"] if best and status in {"matched", "ambiguous"} else {}
        alternatives = [
            {
                "pdf_id": item["pdf"].get("pdf_id", ""),
                "score": round(float(item["score"]), 1),
                "relpath": item["pdf"].get("relpath_from_bib", ""),
            }
            for item in scored[:5]
        ]
        rows.append(
            {
                "candidate_id": f"CAND_{index:04d}",
                "match_status": status,
                "match_score": f"{float(best['score']):.1f}" if best else "0.0",
                "author": entry.get("author", ""),
                "year": entry.get("year", ""),
                "title": entry.get("title", ""),
                "journal": entry.get("journal", ""),
                "category": entry.get("category", "") or "unknown",
                "matched_pdf_relpath": matched_pdf.get("relpath_from_bib", ""),
                "matched_filename": matched_pdf.get("filename", ""),
                "matched_pdf_id": matched_pdf.get("pdf_id", ""),
                "alternative_matches": json.dumps(alternatives, ensure_ascii=False),
                "message": message,
            }
        )

    write_tsv(out_path, rows)
    counts = count_by(rows, "match_status")
    print(
        f"Wrote {len(rows)} corpus candidate rows to {out_path.relative_to(REPO_ROOT)} "
        f"({counts.get('matched', 0)} matched, {counts.get('ambiguous', 0)} ambiguous, "
        f"{counts.get('not_found', 0)} not_found)."
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Match literature TSV entries to local PDF inventory.")
    parser.add_argument("--literature", default="data/metadata/literature_list.tsv")
    parser.add_argument("--inventory", default="data/metadata/local_pdf_inventory.tsv")
    parser.add_argument("--out", default="data/metadata/corpus_candidates.tsv")
    return parser.parse_args()


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def score_match(entry: dict[str, str], pdf: dict[str, str]) -> dict[str, object]:
    title = normalize(entry.get("title", ""))
    author = normalize(entry.get("author", ""))
    year = normalize(entry.get("year", ""))
    stem = normalize(pdf.get("stem", ""))
    filename = normalize(pdf.get("filename", ""))
    metadata_title = normalize(pdf.get("pdf_metadata_title", ""))
    guessed_title = normalize(pdf.get("guessed_title", ""))
    sample = normalize(pdf.get("first_page_text_sample", ""))

    title_candidates = [metadata_title, guessed_title, stem, sample]
    title_score = max((_title_similarity(title, candidate) for candidate in title_candidates), default=0.0)
    filename_score = max(fuzz.WRatio(title, stem), fuzz.WRatio(title, filename)) if title else 0.0
    author_score = (
        max(
            fuzz.WRatio(author, normalize(pdf.get("guessed_author", ""))),
            fuzz.partial_ratio(author, stem),
        )
        if author
        else 0.0
    )
    year_score = (
        100.0
        if year
        and (
            year == normalize(pdf.get("guessed_year", ""))
            or year in stem.split()
            or year in filename.split()
        )
        else 0.0
    )

    score = (0.72 * title_score) + (0.10 * filename_score) + (0.10 * author_score) + (0.08 * year_score)
    return {
        "score": min(score, 100.0),
        "pdf": pdf,
        "title_score": title_score,
        "author_score": author_score,
        "year_score": year_score,
    }


def _title_similarity(title: str, candidate: str) -> float:
    if not title or not candidate:
        return 0.0
    if len(candidate) > 400:
        return max(fuzz.partial_ratio(title, candidate), fuzz.token_set_ratio(title, candidate))
    return max(fuzz.WRatio(title, candidate), fuzz.token_set_ratio(title, candidate))


def decide_status(
    best: dict[str, object] | None,
    second: dict[str, object] | None,
) -> tuple[str, str]:
    if not best:
        return "not_found", "No PDFs available in inventory."
    top_score = float(best["score"])
    second_score = float(second["score"]) if second else 0.0
    gap = top_score - second_score
    if top_score >= 82.0 and gap >= 6.0:
        return "matched", "High-scoring match with clear separation from next candidate."
    if top_score >= 72.0:
        return "ambiguous", "Top match is plausible but not clearly separated from alternatives."
    return "not_found", "No local PDF reached the conservative match threshold."


def normalize(value: str) -> str:
    value = (value or "").lower()
    value = value.replace("α", "alpha").replace("β", "beta")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def count_by(rows: list[dict[str, str]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key, "")
        counts[value] = counts.get(value, 0) + 1
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
