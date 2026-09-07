from __future__ import annotations

import csv
import fcntl
import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from originrag.provenance.library import normalize_doi, normalize_pmcid, normalize_pmid


REQUEST_FIELDS = ["doi", "pmid", "pmcid", "title", "category", "pdf_url", "notes"]


def queue_missing_sources(
    references: Iterable[dict[str, object]],
    requests_path: Path,
    *,
    category: str = "general_malaria",
    note: str = "claim_source_trace",
) -> dict[str, object]:
    """Append identifier-backed source requests idempotently."""

    path = Path(requests_path)
    added: list[dict[str, str]] = []
    skipped: list[dict[str, object]] = []
    with request_queue_lock(path):
        existing = read_requests(path)
        known = {request_key(row) for row in existing}
        for reference in references:
            row = reference_to_request(reference, category=category, note=note)
            key = request_key(row)
            if not key:
                skipped.append(
                    {"reason": "no_stable_identifier", "reference": reference}
                )
                continue
            if key in known:
                skipped.append({"reason": "already_queued", "reference": reference})
                continue
            known.add(key)
            existing.append(row)
            added.append(row)
        if added:
            write_requests(path, existing)
    return {"added": added, "skipped": skipped, "path": str(path)}


def reference_to_request(
    reference: dict[str, object],
    *,
    category: str,
    note: str,
) -> dict[str, str]:
    return {
        "doi": normalize_doi(reference.get("doi")),
        "pmid": normalize_pmid(reference.get("pmid")),
        "pmcid": normalize_pmcid(reference.get("pmcid")),
        "title": " ".join(str(reference.get("title") or "").split()),
        "category": category,
        "pdf_url": str(reference.get("oa_pdf_url") or "").strip(),
        "notes": note,
    }


def request_key(row: dict[str, str]) -> str:
    doi = normalize_doi(row.get("doi"))
    if doi:
        return f"doi:{doi}"
    pmcid = normalize_pmcid(row.get("pmcid"))
    if pmcid:
        return f"pmcid:{pmcid}"
    pmid = normalize_pmid(row.get("pmid"))
    if pmid:
        return f"pmid:{pmid}"
    return ""


def read_requests(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [
            {field: str(row.get(field) or "").strip() for field in REQUEST_FIELDS}
            for row in reader
            if any(str(value or "").strip() for value in row.values())
        ]


def write_requests(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_text = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_text)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=REQUEST_FIELDS,
                delimiter="\t",
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def request_queue_lock(path: Path):
    """Serialize queue rewrites across web threads and local processes."""

    lock_root = Path(tempfile.gettempdir()) / "pfempire_request_locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(str(path.expanduser().resolve()).encode("utf-8")).hexdigest()
    lock_path = lock_root / f"{identity}.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
