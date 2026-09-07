from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path

import fitz


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFLICT_ACTION = "same_path_hash_conflict_preserved"
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
FIELDS = [
    "local_pdf_relpath",
    "archive_sha256",
    "destination_sha256",
    "archive_pages",
    "destination_pages",
    "archive_tokens",
    "destination_tokens",
    "archive_text_sha256",
    "destination_text_sha256",
    "archive_dois",
    "destination_dois",
    "shingle_jaccard",
    "classification",
    "recommended_action",
    "rationale",
]


def main() -> int:
    args = parse_args()
    reconciliation = resolve(args.reconciliation)
    archive = Path(args.zip).expanduser().resolve()
    bib_dir = Path(args.bib_dir).expanduser().resolve()
    out = resolve(args.out)

    conflicts = [
        row
        for row in read_tsv(reconciliation)
        if row.get("action") == CONFLICT_ACTION
    ]
    rows: list[dict[str, str]] = []
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        for conflict in conflicts:
            relpath = conflict["local_pdf_relpath"]
            if relpath not in names:
                raise ValueError(f"Archive member missing: {relpath}")
            destination = bib_dir / relpath
            if not destination.is_file():
                raise ValueError(f"Destination PDF missing: {destination}")
            archive_bytes = bundle.read(relpath)
            destination_bytes = destination.read_bytes()
            verify_hash(archive_bytes, conflict["archive_sha256"], f"archive:{relpath}")
            verify_hash(
                destination_bytes,
                conflict["destination_sha256"],
                f"destination:{relpath}",
            )
            rows.append(compare_pair(conflict, archive_bytes, destination_bytes))

    write_tsv(out, rows)
    counts = Counter(row["classification"] for row in rows)
    print(f"Audited {len(rows)} same-path PDF conflicts.")
    for classification, count in sorted(counts.items()):
        print(f"- {classification}: {count}")
    print(f"Report: {display(out)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare preserved incoming and current PDFs without overwriting either version."
    )
    parser.add_argument(
        "--reconciliation",
        default="outputs/curation/papers_zip_reconciliation_20260803.tsv",
    )
    parser.add_argument("--zip", required=True, help="Source papers.zip containing incoming versions.")
    parser.add_argument("--bib-dir", default="bib")
    parser.add_argument(
        "--out",
        default="outputs/curation/pdf_version_conflict_audit.tsv",
    )
    return parser.parse_args()


def compare_pair(
    conflict: dict[str, str], archive_bytes: bytes, destination_bytes: bytes
) -> dict[str, str]:
    archive_pdf = inspect_pdf(archive_bytes)
    destination_pdf = inspect_pdf(destination_bytes)
    similarity = shingle_jaccard(archive_pdf["tokens"], destination_pdf["tokens"])
    same_dois = archive_pdf["dois"] == destination_pdf["dois"]
    same_text = archive_pdf["text_sha256"] == destination_pdf["text_sha256"]

    if same_text:
        classification = "same_extracted_text"
        action = "retain_destination_content_equivalent"
        rationale = "Normalized extracted text is identical; byte differences are non-textual."
    elif same_dois and similarity >= 0.995:
        classification = "same_article_minor_pdf_difference"
        action = "retain_destination_content_equivalent"
        rationale = (
            "DOI sets match and seven-token shingle Jaccard is at least 0.995; "
            "differences are consistent with rendering or front-matter changes."
        )
    elif same_dois and similarity >= 0.90:
        classification = "same_article_version_difference"
        action = "manual_version_review_required"
        rationale = (
            "DOI sets match but text differs materially enough to require version-date and "
            "correction review."
        )
    else:
        classification = "identity_or_content_conflict"
        action = "manual_identity_review_required"
        rationale = "DOI evidence or extracted content is not close enough for automatic retention."

    return {
        "local_pdf_relpath": conflict["local_pdf_relpath"],
        "archive_sha256": conflict["archive_sha256"],
        "destination_sha256": conflict["destination_sha256"],
        "archive_pages": str(archive_pdf["pages"]),
        "destination_pages": str(destination_pdf["pages"]),
        "archive_tokens": str(len(archive_pdf["tokens"])),
        "destination_tokens": str(len(destination_pdf["tokens"])),
        "archive_text_sha256": archive_pdf["text_sha256"],
        "destination_text_sha256": destination_pdf["text_sha256"],
        "archive_dois": ";".join(sorted(archive_pdf["dois"])),
        "destination_dois": ";".join(sorted(destination_pdf["dois"])),
        "shingle_jaccard": f"{similarity:.6f}",
        "classification": classification,
        "recommended_action": action,
        "rationale": rationale,
    }


def inspect_pdf(payload: bytes) -> dict[str, object]:
    document = fitz.open(stream=io.BytesIO(payload), filetype="pdf")
    text = "\n".join(page.get_text("text") for page in document)
    normalized = normalize_text(text)
    tokens = normalized.split()
    dois = {
        match.group(0).rstrip(".,;:)]}").lower()
        for match in DOI_PATTERN.finditer(text)
    }
    return {
        "pages": len(document),
        "tokens": tokens,
        "text_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "dois": dois,
    }


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def shingle_jaccard(left_tokens: list[str], right_tokens: list[str], width: int = 7) -> float:
    left = token_shingles(left_tokens, width)
    right = token_shingles(right_tokens, width)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def token_shingles(tokens: list[str], width: int) -> set[str]:
    if len(tokens) < width:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[index : index + width]) for index in range(len(tokens) - width + 1)}


def verify_hash(payload: bytes, expected: str, label: str) -> None:
    observed = hashlib.sha256(payload).hexdigest()
    if observed != expected:
        raise ValueError(f"SHA-256 mismatch for {label}: expected {expected}, observed {observed}")


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def resolve(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def display(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
