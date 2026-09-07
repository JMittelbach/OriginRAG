from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from originrag.claims.search import search_claims  # noqa: E402
from originrag.claims.store import DEFAULT_DB_PATH  # noqa: E402


def main() -> int:
    args = parse_args()
    db_path = resolve_repo_path(args.db)
    results = search_claims(
        args.query,
        db_path,
        limit=args.limit,
        claim_type=args.claim_type,
        evidence_strength=args.evidence_strength,
        require_doi=args.require_doi,
        min_confidence=args.min_confidence,
        claim_status=args.claim_status,
        source_paper_id=args.source_paper_id,
    )
    if not results:
        print("No matching claims found.")
        return 0
    for index, claim in enumerate(results, start=1):
        print(format_claim(index, claim))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search the local OriginRAG scientific-claim database.")
    parser.add_argument("query")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH.relative_to(REPO_ROOT)))
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--claim-type")
    parser.add_argument("--evidence-strength")
    parser.add_argument("--require-doi", action="store_true")
    parser.add_argument("--min-confidence", type=float, default=0.0)
    parser.add_argument("--claim-status")
    parser.add_argument("--source-paper-id")
    return parser.parse_args()


def format_claim(index: int, claim: dict) -> str:
    citation = claim.get("citation_short") or claim.get("source_paper_id") or "unknown source"
    doi = claim.get("doi") or "no DOI"
    section = claim.get("source_section") or "unknown section"
    pages = claim.get("source_pages") or "?"
    return "\n".join(
        [
            f"{index}. {citation} | {claim.get('claim_type')} | {claim.get('evidence_strength')} | score={claim.get('score')}",
            f"   {claim.get('claim_text')}",
            f"   Matched terms: {', '.join(claim.get('matched_query_terms') or []) or '-'}",
            f"   Source: {section}, pages {pages}; DOI: {doi}; claim_id: {claim.get('claim_id')}",
        ]
    )


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
