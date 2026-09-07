from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.claims.extraction import extract_claims_from_chunks  # noqa: E402
from pfempire.claims.audit import audit_claim_database  # noqa: E402
from pfempire.claims.store import DEFAULT_DB_PATH, claim_counts, initialize_claim_store, upsert_claims  # noqa: E402
from pfempire.retrieval.metadata import load_paper_metadata  # noqa: E402


def main() -> int:
    args = parse_args()
    chunks_dir = resolve_repo_path(args.chunks_dir)
    papers_path = resolve_repo_path(args.papers)
    db_path = resolve_repo_path(args.db)
    report_path = resolve_repo_path(args.report)

    metadata = load_paper_metadata(papers_path)
    chunks = iter_chunks(chunks_dir, limit=args.limit_chunks)
    claims = extract_claims_from_chunks(
        chunks,
        metadata=metadata,
        max_claims_per_chunk=args.max_claims_per_chunk,
    )
    initialize_claim_store(db_path)
    written = upsert_claims(db_path, claims)
    counts = claim_counts(db_path)
    audit = audit_claim_database(db_path)
    write_report(
        report_path,
        chunks_dir=chunks_dir,
        papers_path=papers_path,
        db_path=db_path,
        chunks=chunks,
        claims=claims,
        written=written,
        counts=counts,
        audit=audit,
    )
    print(f"Chunks inspected: {len(chunks)}")
    print(f"Claims extracted/upserted: {written}")
    print(f"Claim DB: {display_path(db_path)}")
    print(f"Report: {display_path(report_path)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the local OriginRAG evidence-claim database from chunk JSONL files.")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH.relative_to(REPO_ROOT)))
    parser.add_argument("--max-claims-per-chunk", type=int, default=3)
    parser.add_argument("--limit-chunks", type=int, default=0)
    parser.add_argument("--report", default="outputs/claim_layer/claim_database_report.md")
    return parser.parse_args()


def iter_chunks(chunks_dir: Path, *, limit: int = 0) -> list[dict]:
    chunks = []
    if not chunks_dir.exists():
        return chunks
    for path in sorted(chunks_dir.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                chunk.setdefault("source_file", str(path))
                chunks.append(chunk)
                if limit and len(chunks) >= limit:
                    return chunks
    return chunks


def write_report(
    path: Path,
    *,
    chunks_dir: Path,
    papers_path: Path,
    db_path: Path,
    chunks: list[dict],
    claims: list[dict],
    written: int,
    counts: dict[str, object],
    audit: dict[str, object],
) -> None:
    by_paper = Counter(str(claim.get("source_paper_id") or "") for claim in claims)
    by_type = counts.get("by_claim_type", {})
    by_strength = counts.get("by_evidence_strength", {})
    lines = [
        "# Claim Database Report",
        "",
        f"- Chunks dir: `{display_path(chunks_dir)}`",
        f"- Papers metadata: `{display_path(papers_path)}`",
        f"- Claim DB: `{display_path(db_path)}`",
        f"- Chunks inspected: {len(chunks)}",
        f"- Claims extracted/upserted this run: {written}",
        f"- Total claims in DB: {counts.get('total_claims', 0)}",
        f"- Papers with claims in DB: {counts.get('unique_papers', 0)}",
        f"- Claims missing DOI: {counts.get('missing_doi_claims', 0)}",
        f"- Claims without entities: {counts.get('no_entity_claims', 0)}",
        f"- Reviewed claims: {counts.get('reviewed_claims', 0)}",
        "- Model calls performed: 0",
        "",
        "## Audit Warnings",
        "",
        *[f"- {warning}" for warning in audit.get("warnings", [])],
        "",
        "## Audit Recommendations",
        "",
        *[f"- {item}" for item in audit.get("recommendations", [])],
        "",
        "## Claim Types",
        "",
        *[f"- {key}: {value}" for key, value in sorted(dict(by_type).items())],
        "",
        "## Evidence Strength",
        "",
        *[f"- {key}: {value}" for key, value in sorted(dict(by_strength).items())],
        "",
        "## Top Papers By Extracted Claims This Run",
        "",
        *[f"- {paper_id}: {count}" for paper_id, count in by_paper.most_common(20) if paper_id],
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def display_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
