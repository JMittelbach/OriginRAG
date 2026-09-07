from __future__ import annotations

import argparse
import csv
import hashlib
import html
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIELDS = [
    "source_path",
    "source_sha256",
    "collection",
    "category",
    "author",
    "year",
    "title",
    "journal",
    "doi",
    "pmid",
    "pmcid",
    "landing_url",
    "local_pdf_relpath",
    "notes",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge successful requested-paper imports into the local-import manifest."
    )
    parser.add_argument("--report", action="append", required=True)
    parser.add_argument("--manifest", default="data/metadata/local_paper_imports.tsv")
    parser.add_argument("--bib-dir", default="bib")
    args = parser.parse_args()

    manifest_path = resolve(args.manifest)
    bib_dir = resolve(args.bib_dir)
    existing = read_tsv(manifest_path)
    known_dois = {normalize_doi(row.get("doi", "")) for row in existing if row.get("doi")}
    known_hashes = {row.get("source_sha256", "") for row in existing if row.get("source_sha256")}
    appended: list[dict[str, str]] = []

    for report_name in args.report:
        for row in read_tsv(resolve(report_name)):
            if row.get("status") != "downloaded":
                continue
            doi = normalize_doi(row.get("doi", ""))
            if doi and doi in known_dois:
                continue
            path = locate_pdf(bib_dir, row.get("local_pdf_relpath", ""))
            digest = sha256(path)
            if digest in known_hashes:
                continue
            category = row.get("category", "").strip() or "general_malaria"
            appended.append(
                {
                    "source_path": path.relative_to(REPO_ROOT).as_posix(),
                    "source_sha256": digest,
                    "collection": collection_for_category(category),
                    "category": category,
                    "author": row.get("author", "").strip(),
                    "year": row.get("year", "").strip(),
                    "title": clean_markup(row.get("title", "")),
                    "journal": clean_markup(row.get("journal", "")),
                    "doi": doi,
                    "pmid": row.get("pmid", "").strip(),
                    "pmcid": row.get("pmcid", "").strip(),
                    "landing_url": f"https://doi.org/{doi}" if doi else "",
                    "local_pdf_relpath": path.relative_to(bib_dir).as_posix(),
                    "notes": "requested_import; legal_open_access_fulltext; metadata_verified_via_europe_pmc",
                }
            )
            if doi:
                known_dois.add(doi)
            known_hashes.add(digest)

    write_tsv(manifest_path, existing + appended)
    print(f"Appended requested-import manifest rows: {len(appended)}")
    return 0


def locate_pdf(bib_dir: Path, relpath: str) -> Path:
    direct = bib_dir / relpath
    if direct.is_file():
        return direct
    name = Path(relpath).name
    matches = [
        path
        for path in bib_dir.rglob(name)
        if path.is_file() and "duplicate_archive" not in path.parts
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one active PDF named {name}, found {len(matches)}")
    return matches[0]


def collection_for_category(category: str) -> str:
    if category in {"scrna_annotation", "sc_harmonization", "sc_tools"}:
        return "scrna_annotation"
    if category in {"tools_ml", "foundation_models", "evidence_synthesis"}:
        return "bioinformatics_tools"
    if category in {
        "covid_host_response",
        "host_immunity_general",
        "host_tissue_atlas",
        "infectious_disease_host_response",
        "noninfectious_disease_biology",
    }:
        return "host_biology"
    if category == "general_methods":
        return "methods"
    return "malaria"


def clean_markup(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", value or "")).split())


def normalize_doi(value: str) -> str:
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", (value or "").strip(), flags=re.I).lower()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDS} for row in rows)


def resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
