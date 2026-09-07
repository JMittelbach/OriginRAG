from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
from originrag.paths import get_bib_dir  # noqa: E402

METADATA_DIR = REPO_ROOT / "data/metadata"
RELPATH_FIELDS = {"local_pdf_relpath", "matched_pdf_relpath"}
EXCLUDED_LIBRARY_DIRS = {"_archive", "archive", "duplicate_archive"}


def main() -> int:
    args = parse_args()
    bib_dir = resolve_repo_path(args.bib_dir) if args.bib_dir else get_bib_dir().resolve()
    papers_dir = bib_dir / args.papers_dir
    keys_dir = bib_dir / args.keys_dir
    registry_path = resolve_repo_path(args.registry)

    registry_rows = read_tsv(registry_path)
    registry_by_relpath = {
        row.get("local_pdf_relpath", ""): row
        for row in registry_rows
        if row.get("local_pdf_relpath")
    }
    pdfs = discover_pdfs(bib_dir, papers_dir=papers_dir, keys_dir=keys_dir)
    path_map: dict[str, str] = {}

    if args.flatten or args.categorize:
        if not args.dry_run:
            papers_dir.mkdir(parents=True, exist_ok=True)
        for pdf_path in pdfs:
            old_relpath = pdf_path.relative_to(bib_dir).as_posix()
            row = registry_by_relpath.get(old_relpath, {})
            target_dir = category_directory(row) if args.categorize else args.papers_dir
            new_relpath = unique_target_relpath(
                bib_dir=bib_dir,
                target_dir_name=target_dir,
                row=row,
                fallback_path=pdf_path,
                taken=set(path_map.values()),
            )
            target_path = bib_dir / new_relpath
            if old_relpath == new_relpath:
                continue
            if not args.dry_run:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(pdf_path), str(target_path))
            path_map[old_relpath] = new_relpath

    if path_map:
        update_metadata_relpaths(path_map, dry_run=args.dry_run)
        for row in registry_rows:
            relpath = row.get("local_pdf_relpath", "")
            if relpath in path_map:
                row["local_pdf_relpath"] = path_map[relpath]
                row["filename"] = Path(path_map[relpath]).name
        if not args.dry_run:
            write_tsv(
                registry_path, registry_rows, list(registry_rows[0].keys()) if registry_rows else []
            )

    final_registry_rows = (
        read_tsv(registry_path) if not args.dry_run else remap_rows(registry_rows, path_map)
    )
    if not args.dry_run:
        keys_dir.mkdir(parents=True, exist_ok=True)
        export_bib_files(keys_dir, final_registry_rows)
        move_root_metadata_files(bib_dir, keys_dir)
        remove_empty_category_dirs(bib_dir, keep={args.papers_dir, args.keys_dir})

    print(f"PDFs discovered: {len(pdfs)}")
    print(f"PDFs moved to {bib_dir.name}/{args.papers_dir}: {len(path_map)}")
    print(f"Registry rows: {len(final_registry_rows)}")
    print(f"DOI-backed rows: {sum(1 for row in final_registry_rows if row.get('doi'))}")
    print(
        f"Keys dir: {keys_dir.relative_to(REPO_ROOT) if keys_dir.is_relative_to(REPO_ROOT) else keys_dir}"
    )
    if args.dry_run:
        print("Dry run: no PDFs or TSV files were modified.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Flatten bib PDFs and export citation/library files."
    )
    parser.add_argument("--bib-dir", help="Override the configured ORIGINRAG_BIB_DIR.")
    parser.add_argument("--papers-dir", default="papers")
    parser.add_argument("--keys-dir", default="keys")
    parser.add_argument("--registry", default="data/metadata/paper_registry.tsv")
    parser.add_argument("--flatten", action="store_true", help="Move PDFs into bib/papers/.")
    parser.add_argument(
        "--categorize",
        action="store_true",
        help="Move PDFs into stable topic subfolders under bib/papers/.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def discover_pdfs(bib_dir: Path, *, papers_dir: Path, keys_dir: Path) -> list[Path]:
    pdfs = []
    for path in sorted(bib_dir.rglob("*.pdf")):
        if keys_dir in path.parents:
            continue
        if any(parent.name in EXCLUDED_LIBRARY_DIRS for parent in path.parents):
            continue
        if path.name.endswith(".tmp"):
            continue
        pdfs.append(path)
    return pdfs


def unique_target_relpath(
    *,
    bib_dir: Path,
    target_dir_name: str,
    row: dict[str, str],
    fallback_path: Path,
    taken: set[str],
) -> str:
    base_name = pdf_basename(row, fallback_path)
    candidate = Path(target_dir_name) / f"{base_name}.pdf"
    suffix = 2
    while (
        candidate.as_posix() in taken
        or (bib_dir / candidate).exists()
        and (bib_dir / candidate) != fallback_path
    ):
        candidate = Path(target_dir_name) / f"{base_name}_{suffix}.pdf"
        suffix += 1
    return candidate.as_posix()


def category_directory(row: dict[str, str]) -> str:
    category = str(row.get("category") or "").strip()
    if category.startswith("topic_"):
        topic = re.sub(r"[^a-z0-9]+", "_", category[6:].lower()).strip("_")
        return f"papers/topics/{topic}" if topic else "papers/other"
    title = normalize_key(str(row.get("title") or ""))
    foundation_phrase = any(
        term in title
        for term in [
            "foundation model",
            "transformer",
            "large language model",
            "geneformer",
            "pretrain",
            "pre train",
            "language model",
        ]
    )
    foundation_token = bool(re.search(r"\b(?:llm|gpt|bert)\b", title, flags=re.IGNORECASE))
    if category == "foundation_models" or foundation_phrase or foundation_token:
        return "papers/foundation_models"
    mapping = {
        "scrna_annotation": "papers/single_cell/annotation",
        "sc_harmonization": "papers/single_cell/harmonization_integration",
        "sc_tools": "papers/single_cell/tools_methods",
        "tools_ml": "papers/single_cell/tools_methods",
        "var_pfemp1": "papers/malaria/var_genes",
        "general_malaria": "papers/malaria/general_biology",
        "parasite_biology": "papers/malaria/general_biology",
        "resistance_dx": "papers/malaria/resistance_diagnostics",
        "malaria_omics": "papers/malaria/omics",
        "malaria_host_genetics": "papers/malaria/host_genetics",
        "malaria_host_omics": "papers/malaria/host_omics",
        "malaria_tools": "papers/malaria/tools_methods",
        "host_immunity": "papers/immunology",
        "sex_diff": "papers/immunology",
        "covid_host_response": "papers/host_biology/covid_19",
        "host_tissue_atlas": "papers/host_biology/tissue_atlases",
        "host_immunity_general": "papers/host_biology/immunology",
        "infectious_disease_host_response": "papers/host_biology/infectious_diseases",
        "noninfectious_disease_biology": "papers/host_biology/noninfectious_diseases",
        "general_methods": "papers/methods/general",
    }
    return mapping.get(category, "papers/other")


def pdf_basename(row: dict[str, str], fallback_path: Path) -> str:
    bib_key = sanitize_filename(row.get("bib_key", ""))
    if bib_key and bib_key.lower() != "unknownnd":
        return bib_key
    author = sanitize_filename(row.get("author", "")) or "Unknown"
    year = row.get("year", "") if re.fullmatch(r"\d{4}", row.get("year", "")) else "nd"
    title = sanitize_filename(" ".join((row.get("title", "") or fallback_path.stem).split()[:4]))
    return "_".join(part for part in [author, year, title] if part)[:120] or sanitize_filename(
        fallback_path.stem
    )


def update_metadata_relpaths(path_map: dict[str, str], *, dry_run: bool) -> None:
    for tsv_path in sorted(METADATA_DIR.glob("*.tsv")):
        rows = read_tsv(tsv_path)
        if not rows:
            continue
        fieldnames = list(rows[0].keys())
        changed = False
        for row in rows:
            for field in RELPATH_FIELDS:
                old_value = row.get(field, "")
                if old_value in path_map:
                    row[field] = path_map[old_value]
                    changed = True
        if changed and not dry_run:
            write_tsv(tsv_path, rows, fieldnames)


def export_bib_files(keys_dir: Path, registry_rows: list[dict[str, str]]) -> None:
    rows = sorted(
        registry_rows,
        key=lambda row: (row.get("author", ""), row.get("year", ""), row.get("title", "")),
    )
    write_tsv(keys_dir / "all_papers.tsv", rows, list(rows[0].keys()) if rows else [])
    write_tsv(
        keys_dir / "no_doi_review.tsv",
        [row for row in rows if not row.get("doi")],
        list(rows[0].keys()) if rows else [],
    )
    write_tsv(
        keys_dir / "duplicate_candidates.tsv",
        duplicate_rows(rows),
        ["duplicate_type", "value", "bib_key", "local_pdf_relpath", "title", "doi"],
    )
    (keys_dir / "pfempire_library.bib").write_text(format_bibtex(rows), encoding="utf-8")


def format_bibtex(rows: list[dict[str, str]]) -> str:
    entries = []
    used_keys = Counter()
    for row in rows:
        doi = row.get("doi", "")
        landing_url = row.get("landing_url", "")
        if not doi and not landing_url:
            continue
        key = bibtex_key(row, used_keys)
        journal = row.get("journal", "")
        entry_type = (
            "inproceedings"
            if not doi and "proceedings of machine learning research" in journal.casefold()
            else "article"
        )
        venue_field = "booktitle" if entry_type == "inproceedings" else "journal"
        fields = {
            "title": row.get("title", ""),
            "author": bibtex_author(row.get("author", "")),
            "year": row.get("year", ""),
            venue_field: journal,
            "doi": doi,
            "pmid": row.get("pmid", ""),
            "pmcid": row.get("pmcid", ""),
            "url": landing_url or doi_url(doi),
            "file": row.get("local_pdf_relpath", ""),
            "keywords": row.get("category", ""),
        }
        lines = [f"@{entry_type}{{{key},"]
        for field, value in fields.items():
            if value:
                lines.append(f"  {field} = {{{escape_bibtex(value)}}},")
        if lines[-1].endswith(","):
            lines[-1] = lines[-1].rstrip(",")
        lines.append("}")
        entries.append("\n".join(lines))
    return "\n\n".join(entries) + ("\n" if entries else "")


def bibtex_key(row: dict[str, str], used_keys: Counter[str]) -> str:
    base = sanitize_bibtex_key(row.get("bib_key", "")) or make_fallback_key(row)
    used_keys[base] += 1
    if used_keys[base] == 1:
        return base
    return f"{base}_{used_keys[base]}"


def make_fallback_key(row: dict[str, str]) -> str:
    author = sanitize_bibtex_key(row.get("author", "")) or "Unknown"
    year = row.get("year", "") if re.fullmatch(r"\d{4}", row.get("year", "")) else "nd"
    title_word = sanitize_bibtex_key((row.get("title", "") or "Paper").split()[0])
    return f"{author}{year}{title_word}"


def duplicate_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    by_doi: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    by_title_year: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    by_hash: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    bib_dir = REPO_ROOT / "bib"
    for row in rows:
        doi = row.get("doi", "").strip().lower()
        if doi:
            by_doi[doi].append(row)
        title_year = normalize_key(f"{row.get('title', '')} {row.get('year', '')}")
        if title_year:
            by_title_year[title_year].append(row)
        pdf_path = bib_dir / row.get("local_pdf_relpath", "")
        if pdf_path.is_file():
            by_hash[file_sha256(pdf_path)].append(row)

    duplicates = []
    for duplicate_type, groups in [
        ("doi", by_doi),
        ("title_year", by_title_year),
        ("sha256", by_hash),
    ]:
        for value, group in groups.items():
            if len(group) < 2:
                continue
            for row in group:
                duplicates.append(
                    {
                        "duplicate_type": duplicate_type,
                        "value": value,
                        "bib_key": row.get("bib_key", ""),
                        "local_pdf_relpath": row.get("local_pdf_relpath", ""),
                        "title": row.get("title", ""),
                        "doi": row.get("doi", ""),
                    }
                )
    return duplicates


def remove_empty_category_dirs(bib_dir: Path, *, keep: set[str]) -> None:
    for path in sorted((item for item in bib_dir.iterdir() if item.is_dir()), reverse=True):
        if path.name in keep:
            continue
        try:
            path.rmdir()
        except OSError:
            pass


def move_root_metadata_files(bib_dir: Path, keys_dir: Path) -> None:
    legacy_dir = keys_dir / "legacy_inputs"
    keep_names = {".gitkeep", "README.md"}
    for path in sorted(bib_dir.iterdir()):
        if path.is_dir() or path.name in keep_names:
            continue
        if path.suffix.lower() == ".pdf":
            continue
        legacy_dir.mkdir(parents=True, exist_ok=True)
        target = unique_legacy_path(legacy_dir / path.name)
        shutil.move(str(path), str(target))


def unique_legacy_path(path: Path) -> Path:
    if not path.exists():
        return path
    suffix = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{suffix}{path.suffix}")
        if not candidate.exists():
            return candidate
        suffix += 1


def remap_rows(rows: list[dict[str, str]], path_map: dict[str, str]) -> list[dict[str, str]]:
    remapped = []
    for row in rows:
        copy = dict(row)
        relpath = copy.get("local_pdf_relpath", "")
        if relpath in path_map:
            copy["local_pdf_relpath"] = path_map[relpath]
            copy["filename"] = Path(path_map[relpath]).name
        remapped.append(copy)
    return remapped


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def doi_url(doi: str) -> str:
    return f"https://doi.org/{doi}" if doi else ""


def bibtex_author(author: str) -> str:
    return author if author else "Unknown"


def escape_bibtex(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def sanitize_filename(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"[^A-Za-z0-9]+", "_", ascii_value).strip("_")


def sanitize_bibtex_key(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"[^A-Za-z0-9:_-]+", "", ascii_value)


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
