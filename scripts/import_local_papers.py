from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import sys
import unicodedata
from pathlib import Path

try:
    import fitz
except ImportError:
    fitz = None


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
from originrag.paths import get_bib_dir  # noqa: E402

BIB_DIR = REPO_ROOT / "bib"
MANIFEST_FIELDS = [
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
REPORT_FIELDS = [
    "source_path",
    "filename",
    "sha256",
    "status",
    "category",
    "author",
    "year",
    "title",
    "doi",
    "local_pdf_relpath",
    "reason",
]
INTERNAL_DOCUMENT_MARKERS = {
    "blueprint",
    "protocol",
    "method_concept",
    "idea_portfolio",
    "guide",
    "manifest",
    "multi-label parasite-state modelling",
}


def main() -> int:
    global BIB_DIR
    args = parse_args()
    BIB_DIR = get_bib_dir().resolve()
    if fitz is None:
        raise RuntimeError("PyMuPDF is required; activate the originrag environment")
    source_dir = Path(args.source_dir).expanduser().resolve()
    if args.apply and source_dir.is_relative_to(BIB_DIR.resolve()):
        raise SystemExit(
            "Refusing --apply for a source inside bib/: this would copy already-local PDFs "
            "and create duplicates. Use a dry run or reconcile the existing paths in place."
        )
    metadata_path = Path(args.metadata_tsv).expanduser().resolve() if args.metadata_tsv else None
    manifest_path = resolve(args.manifest)
    report_path = resolve(args.report)
    manifest = read_tsv(manifest_path)
    registry = read_tsv(resolve(args.registry))
    metadata_by_path = metadata_lookup(metadata_path)
    source_paths = {
        path.resolve()
        for path in (source_dir.glob("*.pdf") if args.top_level_only else source_dir.rglob("*.pdf"))
    }
    known_hashes = active_pdf_hashes(BIB_DIR, exclude_paths=source_paths)
    known_dois = registry_dois(registry) | {
        normalize_doi(row.get("doi", "")) for row in manifest if normalize_doi(row.get("doi", ""))
    }
    known_titles = registry_titles(registry) | {
        normalize_title_identity(row.get("title", ""))
        for row in manifest
        if reliable_identity_title(row.get("title", ""))
    }
    known_sources = {row.get("source_sha256", "") for row in manifest if row.get("source_sha256")}
    rows: list[dict[str, str]] = []
    imported_rows: list[dict[str, str]] = []

    source_files = source_dir.glob("*.pdf") if args.top_level_only else source_dir.rglob("*.pdf")
    for path in sorted(source_files):
        metadata = metadata_by_path.get(path.resolve(), {})
        report, import_row = inspect_candidate(
            path,
            metadata=metadata,
            known_hashes=known_hashes,
            known_dois=known_dois,
            known_titles=known_titles,
            known_sources=known_sources,
            apply=args.apply,
        )
        rows.append(report)
        if import_row:
            imported_rows.append(import_row)
            known_hashes.add(import_row["source_sha256"])
            known_sources.add(import_row["source_sha256"])
            if import_row.get("doi"):
                known_dois.add(normalize_doi(import_row["doi"]))
            if reliable_identity_title(import_row.get("title", "")):
                known_titles.add(normalize_title_identity(import_row["title"]))

    if args.apply and imported_rows:
        write_tsv(manifest_path, manifest + imported_rows, MANIFEST_FIELDS)
    write_tsv(report_path, rows, REPORT_FIELDS)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print(f"Scanned PDFs: {len(rows)}")
    for status, count in sorted(counts.items()):
        print(f"- {status}: {count}")
    print(f"New imports: {len(imported_rows)}")
    print(f"Report: {display(report_path)}")
    if not args.apply:
        print("Dry run: no PDFs or manifest rows were written.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import local scholarly PDFs with DOI/hash deduplication.")
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--metadata-tsv", help="Optional scBENCH-style TSV containing PDF_File metadata.")
    parser.add_argument("--registry", default="data/metadata/paper_registry.tsv")
    parser.add_argument("--manifest", default="data/metadata/local_paper_imports.tsv")
    parser.add_argument("--report", default="outputs/curation/local_paper_import_report.tsv")
    parser.add_argument(
        "--top-level-only",
        action="store_true",
        help="Scan only PDFs directly in --source-dir; useful for a bib/papers dump.",
    )
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def inspect_candidate(
    path: Path,
    *,
    metadata: dict[str, str],
    known_hashes: set[str],
    known_dois: set[str],
    known_titles: set[str],
    known_sources: set[str],
    apply: bool,
) -> tuple[dict[str, str], dict[str, str] | None]:
    digest = sha256(path)
    report = {field: "" for field in REPORT_FIELDS}
    report.update(source_path=str(path), filename=path.name, sha256=digest)
    if is_internal_document(path.name):
        report.update(status="excluded_internal", reason="project blueprint/protocol/concept, not a paper")
        return report, None
    if digest in known_hashes or digest in known_sources:
        report.update(status="duplicate_hash", reason="identical PDF already present or imported")
        return report, None

    pdf = read_pdf_metadata(path)
    doi = canonicalize_doi_against_known(
        first_nonempty(metadata.get("DOI"), pdf.get("doi")),
        known_dois,
    )
    title = first_nonempty(metadata.get("Primary_Paper"), pdf.get("title"), path.stem.replace("_", " "))
    author = first_author_family(first_nonempty(metadata.get("First_Author"), pdf.get("author")))
    year = normalize_year(first_nonempty(metadata.get("Year"), pdf.get("year"), title))
    category = classify_category(title, path.name, pdf.get("sample", ""))
    report.update(category=category, author=author, year=year, title=title, doi=doi)

    if doi and doi in known_dois:
        report.update(status="duplicate_doi", reason="DOI already present in OriginRAG registry")
        return report, None
    if reliable_identity_title(title) and normalize_title_identity(title) in known_titles:
        report.update(status="duplicate_title", reason="normalized title already present in OriginRAG registry")
        return report, None
    if not doi and not metadata:
        report.update(status="review_no_doi", reason="no DOI or curated source metadata; not imported automatically")
        return report, None
    if not looks_scholarly(pdf.get("sample", ""), title):
        report.update(status="review_non_scholarly", reason="could not verify scholarly article structure")
        return report, None

    destination_dir = BIB_DIR / category_directory(category)
    basename = safe_filename("_".join(part for part in [author or "Unknown", year or "nd", "_".join(title.split()[:5])] if part))
    destination = unique_path(destination_dir / f"{basename[:140]}.pdf")
    relpath = destination.relative_to(BIB_DIR).as_posix()
    if apply:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    report.update(status="imported" if apply else "would_import", local_pdf_relpath=relpath, reason="new scholarly PDF")
    import_row = {
        "source_path": str(path),
        "source_sha256": digest,
        "collection": collection_for_category(category),
        "category": category,
        "author": author,
        "year": year,
        "title": title,
        "journal": "",
        "doi": doi,
        "pmid": "",
        "pmcid": "",
        "landing_url": f"https://doi.org/{doi}" if doi else "",
        "local_pdf_relpath": relpath,
        "notes": "local_import; source=scBENCH" if metadata else "local_import; source=Downloads",
    }
    return report, import_row


def metadata_lookup(path: Path | None) -> dict[Path, dict[str, str]]:
    if not path or not path.exists():
        return {}
    lookup: dict[Path, dict[str, str]] = {}
    for row in read_tsv(path):
        relpath = row.get("PDF_File", "").strip()
        if relpath:
            lookup[(path.parent / relpath).resolve()] = row
    return lookup


def read_pdf_metadata(path: Path) -> dict[str, str]:
    with fitz.open(path) as document:
        metadata = document.metadata or {}
        pages = [document[index].get_text("text") for index in range(min(3, document.page_count))]
        sample = clean(" ".join(pages))
        title = clean(metadata.get("title"))
        if weak_title(title):
            title = first_title_line(pages[0] if pages else "")
        return {
            "title": title,
            "author": clean(metadata.get("author")),
            "year": first_nonempty(metadata.get("creationDate"), sample[:1000]),
            "doi": extract_doi(f"{title} {sample[:16000]}"),
            "sample": sample,
        }


def classify_category(title: str, filename: str = "", sample: str = "") -> str:
    heading = f" {clean(f'{title} {filename}').lower()} "
    value = f" {clean(f'{title} {filename} {sample[:4000]}').lower()} "
    if contains_foundation_term(heading):
        return "foundation_models"
    malaria_context = any(term in value for term in ["malaria", "plasmodium", "pfemp1", "var gene"])
    if malaria_context and any(term in value for term in ["pfemp1", "var gene", "variant surface antigen", "cytoadherence"]):
        return "var_pfemp1"
    if malaria_context and any(term in value for term in ["immunity", "immune", "antibody", "t cell", "b cell", "cytokine", "vaccin"]):
        return "host_immunity"
    if malaria_context and any(term in value for term in ["drug resistance", "artemisinin", "diagnostic", "pfhrp2", "pfhrp3"]):
        return "resistance_dx"
    explicit_immunology = any(
        term in heading
        for term in ["immune response", "immunity", "inflammation", "antibody", "vaccination", "vaccine"]
    )
    annotation_heading = any(
        term in heading
        for term in ["annotation", "cell classification", "cell identification", "cell identity"]
    )
    if explicit_immunology and not annotation_heading:
        return "host_immunity"
    if any(term in value for term in ["harmonization", "harmonisation", "data integration", "batch correction", "reference mapping", "atlas mapping", "label transfer", "transfer learning"]):
        return "sc_harmonization"
    if any(term in value for term in ["cell type annotation", "cell-type annotation", "cell annotation", "cell classification", "cell identity", "automated annotation"]):
        return "scrna_annotation"
    if any(term in value for term in ["single cell", "single-cell", "scrna", "transcriptomic tool", "bioinformatics"]):
        return "sc_tools"
    if any(term in value for term in ["immunity", "immune", "antibody", "t cell", "b cell", "cytokine", "vaccin"]):
        return "host_immunity"
    if any(term in value for term in ["drug resistance", "artemisinin", "diagnostic", "pfhrp2", "pfhrp3"]):
        return "resistance_dx"
    if any(term in value for term in ["malaria", "plasmodium"]):
        return "general_malaria"
    return "other"


def contains_foundation_term(text: str) -> bool:
    if any(term in text for term in ["foundation model", "transformer", "large language model", "geneformer", "pretrain", "pre-train", "language model"]):
        return True
    return bool(re.search(r"\b(?:llm|gpt|bert)\b", text, flags=re.IGNORECASE))


def category_directory(category: str) -> str:
    return {
        "scrna_annotation": "papers/single_cell/annotation",
        "sc_harmonization": "papers/single_cell/harmonization_integration",
        "sc_tools": "papers/single_cell/tools_methods",
        "foundation_models": "papers/foundation_models",
        "var_pfemp1": "papers/malaria/var_genes",
        "general_malaria": "papers/malaria/general_biology",
        "host_immunity": "papers/immunology",
        "resistance_dx": "papers/malaria/resistance_diagnostics",
    }.get(category, "papers/other")


def collection_for_category(category: str) -> str:
    if category == "other" or category.startswith("topic_"):
        return "general_literature"
    if category.startswith("sc_") or category == "scrna_annotation":
        return "scrna_annotation"
    if category == "foundation_models":
        return "bioinformatics_tools"
    return "malaria"


def looks_scholarly(sample: str, title: str) -> bool:
    value = f"{title} {sample[:8000]}".lower()
    return len(value) >= 500 and any(term in value for term in ["abstract", "introduction", "doi", "methods", "results", "bioinformatics", "malaria", "single-cell", "single cell"])


def is_internal_document(filename: str) -> bool:
    lower = filename.lower()
    return any(marker in lower for marker in INTERNAL_DOCUMENT_MARKERS)


def active_pdf_hashes(bib_dir: Path, *, exclude_paths: set[Path] | None = None) -> set[str]:
    excluded = {path.resolve() for path in (exclude_paths or set())}
    hashes = set()
    for path in bib_dir.rglob("*.pdf"):
        if path.resolve() in excluded:
            continue
        relparts = path.relative_to(bib_dir).parts
        if "keys" in relparts or "duplicate_archive" in relparts:
            continue
        hashes.add(sha256(path))
    return hashes


def registry_dois(rows: list[dict[str, str]]) -> set[str]:
    return {normalize_doi(row.get("doi", "")) for row in rows if normalize_doi(row.get("doi", ""))}


def registry_titles(rows: list[dict[str, str]]) -> set[str]:
    return {
        normalize_title_identity(row.get("title", ""))
        for row in rows
        if reliable_identity_title(row.get("title", ""))
    }


def extract_doi(text: str) -> str:
    matches = re.findall(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text or "", flags=re.IGNORECASE)
    for match in matches:
        doi = normalize_doi(match)
        suffix = doi.split("/", 1)[-1]
        if any(char.isdigit() for char in suffix):
            return doi
    return ""


def normalize_doi(value: object) -> str:
    doi = clean(value).lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"/-/dcsupplemental(?:/.*)?$", "", doi, flags=re.IGNORECASE)
    return doi.rstrip(".,;:)]}")


def canonicalize_doi_against_known(value: object, known_dois: set[str]) -> str:
    doi = normalize_doi(value)
    if doi in known_dois:
        return doi
    if doi.endswith(".001") and doi[:-4] in known_dois:
        return doi[:-4]
    return doi


def normalize_title_identity(value: object) -> str:
    ascii_value = unicodedata.normalize("NFKD", clean(value)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).strip()


def reliable_identity_title(value: object) -> bool:
    normalized = normalize_title_identity(value)
    if len(normalized.split()) < 5:
        return False
    return not any(
        normalized.startswith(prefix)
        for prefix in (
            "published online",
            "science journals",
            "university of",
        )
    )


def first_author_family(value: object) -> str:
    text = clean(value)
    if not text:
        return ""
    first = re.split(r"\s*(?:;|\band\b|&)\s*", text, maxsplit=1)[0]
    if "," in first:
        return first.split(",", 1)[0].strip()
    parts = first.split()
    return parts[-1] if len(parts) > 1 else first


def normalize_year(value: object) -> str:
    match = re.search(r"(?:19|20)\d{2}", clean(value))
    return match.group(0) if match else ""


def first_title_line(text: str) -> str:
    for line in text.splitlines()[:30]:
        line = clean(line)
        if 20 <= len(line) <= 300 and not re.match(r"^(doi|http|www\.|research article)$", line, re.I):
            return line
    return ""


def weak_title(value: str) -> bool:
    return not value or value.lower() in {"untitled", "microsoft word", "pdf update"} or len(value.split()) < 3


def safe_filename(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "_", ascii_value).strip("_") or "UnknownPaper"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    number = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{number}{path.suffix}")
        if not candidate.exists():
            return candidate
        number += 1


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def first_nonempty(*values: object) -> str:
    for value in values:
        text = clean(value)
        if text:
            return text
    return ""


def clean(value: object) -> str:
    return " ".join(str(value or "").split())


def resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def display(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix() if path.is_relative_to(REPO_ROOT) else str(path)


if __name__ == "__main__":
    raise SystemExit(main())
