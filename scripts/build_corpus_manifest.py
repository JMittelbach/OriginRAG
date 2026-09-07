from __future__ import annotations

import argparse
import csv
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

FIELDNAMES = [
    "paper_id",
    "activation_status",
    "priority",
    "domain",
    "collection",
    "category",
    "topics",
    "methods",
    "systems",
    "author",
    "year",
    "title",
    "journal",
    "paper_type",
    "match_score",
    "local_pdf_relpath",
    "fulltext_status",
    "notes",
]

PREFIXES = {
    "var_pfemp1": "MAL_VAR",
    "host_immunity": "MAL_HOST",
    "sex_diff": "MAL_SEX",
    "tools_ml": "BIO_ML",
    "malaria_omics": "MAL_OMX",
    "malaria_tools": "MAL_TOOL",
    "scrna_annotation": "SCRNA_ANN",
    "resistance_dx": "MAL_RES",
    "parasite_biology": "MAL_PAR",
    "general_malaria": "MAL_GEN",
    "unknown": "UNK",
}

DOMAIN_BY_CATEGORY = {
    "scrna_annotation": "single_cell_bioinformatics",
    "tools_ml": "bioinformatics",
}

COLLECTION_BY_CATEGORY = {
    "scrna_annotation": "scrna_annotation",
    "tools_ml": "bioinformatics_tools",
}


def main() -> int:
    args = parse_args()
    candidates_path = resolve_repo_path(args.candidates)
    out_path = resolve_repo_path(args.out)
    candidates = read_tsv(candidates_path)
    counters: dict[str, int] = {}
    manifest_rows = []
    skipped = 0

    for candidate in candidates:
        match_status = candidate.get("match_status", "")
        if match_status == "not_found" and not args.include_not_found:
            skipped += 1
            continue
        category = normalize_category(candidate.get("category", "unknown"))
        activation_status = {
            "matched": "candidate",
            "ambiguous": "needs_review",
            "not_found": "needs_review",
        }.get(match_status, "needs_review")
        manifest_rows.append(
            {
                "paper_id": next_paper_id(category, counters),
                "activation_status": activation_status,
                "priority": "",
                "domain": DOMAIN_BY_CATEGORY.get(category, "malaria"),
                "collection": COLLECTION_BY_CATEGORY.get(category, "malaria"),
                "category": category,
                "topics": category if category != "unknown" else "",
                "methods": "",
                "systems": "",
                "author": candidate.get("author", ""),
                "year": candidate.get("year", ""),
                "title": candidate.get("title", ""),
                "journal": candidate.get("journal", ""),
                "paper_type": "",
                "match_score": candidate.get("match_score", ""),
                "local_pdf_relpath": candidate.get("matched_pdf_relpath", ""),
                "fulltext_status": "pdf_available" if match_status in {"matched", "ambiguous"} else "missing",
                "notes": build_notes(candidate),
            }
        )

    write_tsv(out_path, manifest_rows)
    print(
        f"Wrote {len(manifest_rows)} manifest rows to {out_path.relative_to(REPO_ROOT)} "
        f"({skipped} not_found rows skipped)."
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build corpus_manifest.tsv from corpus candidates.")
    parser.add_argument("--candidates", default="data/metadata/corpus_candidates.tsv")
    parser.add_argument("--out", default="data/metadata/corpus_manifest.tsv")
    parser.add_argument("--include-not-found", action="store_true")
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


def normalize_category(category: str) -> str:
    category = (category or "unknown").strip() or "unknown"
    return category if category in PREFIXES else "unknown"


def next_paper_id(category: str, counters: dict[str, int]) -> str:
    prefix = PREFIXES.get(category, PREFIXES["unknown"])
    counters[prefix] = counters.get(prefix, 0) + 1
    return f"{prefix}_{counters[prefix]:04d}"


def build_notes(candidate: dict[str, str]) -> str:
    bits = [f"candidate_id={candidate.get('candidate_id', '')}"]
    if candidate.get("match_status"):
        bits.append(f"match_status={candidate['match_status']}")
    if candidate.get("message"):
        bits.append(candidate["message"])
    return "; ".join(bit for bit in bits if bit)


if __name__ == "__main__":
    raise SystemExit(main())
