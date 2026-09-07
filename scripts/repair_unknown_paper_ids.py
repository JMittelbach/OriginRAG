from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from sync_registry_to_papers import (  # noqa: E402
    PAPERS_FIELDS,
    PREFIX_BY_CATEGORY,
    domain_for_category,
    next_numbers_by_prefix,
    next_paper_id,
)


def main() -> int:
    args = parse_args()
    papers_path = resolve_repo_path(args.papers)
    extracted_dir = resolve_repo_path(args.extracted_dir)
    chunks_dir = resolve_repo_path(args.chunks_dir)
    rows = read_tsv(papers_path)
    existing_ids = {row.get("paper_id", "") for row in rows if row.get("paper_id")}
    next_numbers = next_numbers_by_prefix(existing_ids)
    migrations: list[tuple[dict[str, str], str, str]] = []

    for row in rows:
        old_id = row.get("paper_id", "").strip()
        category = row.get("topics", "").strip()
        prefix = PREFIX_BY_CATEGORY.get(category, "UNK")
        if not old_id.startswith("UNK_") or prefix == "UNK":
            continue
        if (chunks_dir / f"{old_id}.jsonl").exists():
            raise RuntimeError(f"refusing to migrate chunked paper ID: {old_id}")
        new_id = next_paper_id(category, next_numbers, existing_ids)
        existing_ids.add(new_id)
        target = extracted_dir / f"{new_id}.json"
        if target.exists():
            raise RuntimeError(f"target extracted-text artifact already exists: {target}")
        migrations.append((row, old_id, new_id))

    for row, old_id, new_id in migrations:
        print(f"{old_id} -> {new_id} ({row.get('topics', '')})")
        if args.dry_run:
            continue
        source = extracted_dir / f"{old_id}.json"
        target = extracted_dir / f"{new_id}.json"
        if source.exists():
            payload = json.loads(source.read_text(encoding="utf-8"))
            payload["paper_id"] = new_id
            tmp_path = target.with_suffix(".json.tmp")
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp_path.replace(target)
            source.unlink()
        row["paper_id"] = new_id
        row["domain"] = domain_for_category(row.get("topics", ""))
        if row.get("extracted_text_relpath", "").endswith(f"/{old_id}.json"):
            row["extracted_text_relpath"] = f"data/extracted_text/{new_id}.json"

    if migrations and not args.dry_run:
        write_tsv(papers_path, rows)
    print(f"Migrated paper IDs: {len(migrations)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair newly assigned UNK IDs before their chunks are built."
    )
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--extracted-dir", default="data/extracted_text")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PAPERS_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def resolve_repo_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
