from __future__ import annotations

import argparse
import csv
import hashlib
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFLICT_ACTION = "same_path_hash_conflict_preserved"


def main() -> int:
    args = parse_args()
    reconciliation = read_tsv(resolve(args.reconciliation))
    decisions = read_tsv(resolve(args.decisions))
    bib_dir = Path(args.bib_dir).expanduser().resolve()
    version_archive = Path(args.version_archive).expanduser().resolve()
    bundle_path = Path(args.zip).expanduser().resolve()

    conflicts = {
        row["local_pdf_relpath"]: row
        for row in reconciliation
        if row.get("action") == CONFLICT_ACTION
    }
    decision_map = {row["local_pdf_relpath"]: row for row in decisions}
    if set(conflicts) != set(decision_map):
        missing = sorted(set(conflicts) - set(decision_map))
        extra = sorted(set(decision_map) - set(conflicts))
        raise ValueError(f"Decision coverage mismatch; missing={missing}, extra={extra}")

    promoted = 0
    retained = 0
    with zipfile.ZipFile(bundle_path) as bundle:
        for relpath, conflict in conflicts.items():
            decision = decision_map[relpath]
            source = decision["retained_source"]
            if source not in {"archive", "destination"}:
                raise ValueError(f"Invalid retained_source for {relpath}: {source}")
            expected_retained = conflict[f"{source}_sha256"]
            other_source = "destination" if source == "archive" else "archive"
            expected_archived = conflict[f"{other_source}_sha256"]
            if decision["retained_sha256"] != expected_retained:
                raise ValueError(f"Retained hash decision mismatch for {relpath}")
            if decision["archived_sha256"] != expected_archived:
                raise ValueError(f"Archived hash decision mismatch for {relpath}")
            verify_file_hash(bib_dir / relpath, expected_retained)
            archive_payload_hash = hashlib.sha256(bundle.read(relpath)).hexdigest()
            if archive_payload_hash != conflict["archive_sha256"]:
                raise ValueError(f"Source ZIP hash mismatch for {relpath}")
            if source == "archive":
                verify_file_hash(version_archive / relpath, conflict["destination_sha256"])
                promoted += 1
            else:
                retained += 1

    print(f"Validated {len(conflicts)} PDF version decisions.")
    print(f"- archive versions promoted: {promoted}")
    print(f"- destination versions retained: {retained}")
    print("- every promoted former destination is recoverable from the version archive")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate applied same-path PDF version decisions.")
    parser.add_argument(
        "--reconciliation",
        default="outputs/curation/papers_zip_reconciliation_20260803.tsv",
    )
    parser.add_argument(
        "--decisions",
        default="outputs/curation/pdf_version_conflict_decisions_20260804.tsv",
    )
    parser.add_argument("--zip", required=True)
    parser.add_argument("--bib-dir", default="bib")
    parser.add_argument(
        "--version-archive",
        default="bib/keys/version_archive/20260804_papers_zip_conflicts",
    )
    return parser.parse_args()


def verify_file_hash(path: Path, expected: str) -> None:
    if not path.is_file():
        raise ValueError(f"Required file missing: {path}")
    observed = hashlib.sha256(path.read_bytes()).hexdigest()
    if observed != expected:
        raise ValueError(f"SHA-256 mismatch for {path}: expected {expected}, observed {observed}")


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def resolve(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
