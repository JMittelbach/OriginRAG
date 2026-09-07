from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from originrag.paths import OriginRAGConfigError, get_bib_dir, load_project_env  # noqa: E402


MANIFEST_FIELDS = [
    "group_id",
    "reason",
    "kept_relpath",
    "archived_relpath",
    "archive_relpath",
    "doi",
    "sha256",
    "author",
    "year",
    "title",
]


def main() -> int:
    args = parse_args()
    load_project_env(REPO_ROOT / ".env")
    try:
        bib_dir = get_bib_dir().resolve()
    except OriginRAGConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    registry_path = resolve_repo_path(args.registry)
    papers_path = resolve_repo_path(args.papers)
    manifest_path = resolve_repo_path(args.manifest)
    archive_root = bib_dir / args.archive_dir / datetime.now().strftime("%Y%m%d_%H%M%S")

    rows = [row for row in read_tsv(registry_path) if row.get("local_pdf_relpath")]
    rows = [row for row in rows if (bib_dir / row["local_pdf_relpath"]).exists()]
    components = duplicate_components(rows, bib_dir)

    manifest_rows: list[dict[str, str]] = []
    archived_by_relpath: dict[str, str] = {}
    for index, component in enumerate(components, start=1):
        keeper = choose_keeper(component)
        kept_relpath = keeper.get("local_pdf_relpath", "")
        group_id = f"DUP_{index:04d}"
        for row in component:
            relpath = row.get("local_pdf_relpath", "")
            if relpath == kept_relpath:
                continue
            source_path = bib_dir / relpath
            target_path = unique_archive_path(archive_root / Path(relpath).name)
            archive_relpath = target_path.relative_to(bib_dir).as_posix()
            if args.apply:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source_path), str(target_path))
            archived_by_relpath[relpath] = archive_relpath
            manifest_rows.append(
                {
                    "group_id": group_id,
                    "reason": duplicate_reason(component, bib_dir),
                    "kept_relpath": kept_relpath,
                    "archived_relpath": relpath,
                    "archive_relpath": archive_relpath,
                    "doi": row.get("doi", ""),
                    "sha256": file_sha256(target_path if args.apply else source_path),
                    "author": row.get("author", ""),
                    "year": row.get("year", ""),
                    "title": row.get("title", ""),
                }
            )

    if args.apply:
        update_papers_for_archived_duplicates(papers_path, archived_by_relpath)
    write_duplicate_manifest(manifest_path, manifest_rows, append=True, dry_run=not args.apply)

    print(f"Duplicate groups found: {len(components)}")
    print(f"PDFs to archive: {len(manifest_rows)}")
    print(f"Archive root: {archive_root.relative_to(bib_dir) if archive_root.is_relative_to(bib_dir) else archive_root}")
    print(f"Manifest: {display_path(manifest_path)}")
    if not args.apply:
        print("Dry run: no PDFs or metadata were modified. Re-run with --apply to archive duplicates.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive exact DOI/SHA duplicate PDFs outside the active bib/papers library.")
    parser.add_argument("--registry", default="data/metadata/paper_registry.tsv")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--archive-dir", default="keys/duplicate_archive")
    parser.add_argument("--manifest", default="bib/keys/duplicate_archive_manifest.tsv")
    parser.add_argument("--apply", action="store_true", help="Move duplicate files and mark matching papers.tsv rows as duplicate.")
    return parser.parse_args()


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, value: str) -> None:
        self.parent.setdefault(value, value)

    def find(self, value: str) -> str:
        self.add(value)
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left != root_right:
            self.parent[root_right] = root_left


def duplicate_components(rows: list[dict[str, str]], bib_dir: Path) -> list[list[dict[str, str]]]:
    by_relpath = {row["local_pdf_relpath"]: row for row in rows}
    uf = UnionFind()
    for relpath in by_relpath:
        uf.add(relpath)

    by_doi: defaultdict[str, list[str]] = defaultdict(list)
    by_hash: defaultdict[str, list[str]] = defaultdict(list)
    for row in rows:
        relpath = row["local_pdf_relpath"]
        doi = normalize_doi(row.get("doi", ""))
        if doi:
            by_doi[doi].append(relpath)
        pdf_path = bib_dir / relpath
        if pdf_path.exists():
            by_hash[file_sha256(pdf_path)].append(relpath)

    for group in list(by_doi.values()) + list(by_hash.values()):
        if len(group) < 2:
            continue
        first = group[0]
        for relpath in group[1:]:
            uf.union(first, relpath)

    grouped: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    for relpath, row in by_relpath.items():
        grouped[uf.find(relpath)].append(row)
    return [sorted(group, key=keeper_sort_key) for group in grouped.values() if len(group) > 1]


def choose_keeper(rows: list[dict[str, str]]) -> dict[str, str]:
    return sorted(rows, key=keeper_sort_key)[0]


def keeper_sort_key(row: dict[str, str]) -> tuple[float, int, int, str]:
    score = parse_float(row.get("quality_score"))
    relpath = row.get("local_pdf_relpath", "")
    filename = Path(relpath).stem
    suffix_penalty = 1 if filename.endswith(("_2", "_3", "_4", "_5")) else 0
    return (-score, suffix_penalty, len(relpath), relpath)


def duplicate_reason(rows: list[dict[str, str]], bib_dir: Path) -> str:
    dois = {normalize_doi(row.get("doi", "")) for row in rows if normalize_doi(row.get("doi", ""))}
    hashes = {file_sha256(bib_dir / row["local_pdf_relpath"]) for row in rows if (bib_dir / row["local_pdf_relpath"]).exists()}
    reasons = []
    if len(dois) == 1 and len(rows) > 1:
        reasons.append("same_doi")
    if len(hashes) < len(rows):
        reasons.append("same_sha256")
    return ";".join(reasons) or "linked_duplicate_group"


def update_papers_for_archived_duplicates(papers_path: Path, archived_by_relpath: dict[str, str]) -> None:
    if not archived_by_relpath or not papers_path.exists():
        return
    rows = read_tsv(papers_path)
    fieldnames = list(rows[0].keys()) if rows else []
    for row in rows:
        relpath = row.get("local_pdf_relpath", "")
        archive_relpath = archived_by_relpath.get(relpath)
        if not archive_relpath:
            continue
        row["local_pdf_relpath"] = archive_relpath
        row["fulltext_status"] = "duplicate"
        row["notes"] = append_note(row.get("notes", ""), "duplicate_pdf_archived")
    write_tsv(papers_path, rows, fieldnames)


def write_duplicate_manifest(path: Path, rows: list[dict[str, str]], *, append: bool, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_tsv(path) if append and path.exists() else []
    write_tsv(path, existing + rows, MANIFEST_FIELDS)


def unique_archive_path(path: Path) -> Path:
    if not path.exists():
        return path
    suffix = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{suffix}{path.suffix}")
        if not candidate.exists():
            return candidate
        suffix += 1


def normalize_doi(value: str) -> str:
    value = (value or "").strip().lower()
    for prefix in ["https://doi.org/", "http://doi.org/", "doi:"]:
        if value.startswith(prefix):
            value = value.removeprefix(prefix)
    return value.strip().rstrip(".")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_float(value: str | None) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def append_note(existing: str, note: str) -> str:
    values = [value.strip() for value in [existing, note] if value and value.strip()]
    seen = set()
    deduped = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return "; ".join(deduped)


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def write_tsv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def display_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
