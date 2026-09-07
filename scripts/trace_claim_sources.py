from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.db.database import DEFAULT_DB_PATH  # noqa: E402
from pfempire.paths import OriginRAGConfigError, get_bib_dir, load_project_env  # noqa: E402
from pfempire.provenance.library import LibraryStatusResolver  # noqa: E402
from pfempire.provenance.references import (  # noqa: E402
    extract_jats_citation_map,
    extract_pdf_citation_map,
    trace_claim_sources,
)
from pfempire.provenance.requests import queue_missing_sources  # noqa: E402
from pfempire.provenance.resolver import (  # noqa: E402
    enrich_reference_online,
    resolve_reference_against_library,
    resolve_trace_against_library,
)
from pfempire.provenance.store import upsert_source_trace  # noqa: E402
from pfempire.utils.io import write_json  # noqa: E402


def main() -> int:
    args = parse_args()
    load_project_env(REPO_ROOT / ".env")
    db_path = resolve_repo_path(args.db)
    claim = read_claim(db_path, args.claim_id) if args.claim_id else {}
    claimed_paper_id = str(claim.get("source_paper_id") or "")
    if args.claim_id and args.paper_id and args.paper_id != claimed_paper_id:
        raise ValueError(
            f"--paper-id {args.paper_id!r} does not match claim carrier {claimed_paper_id!r}."
        )
    if args.claim_id and (args.claim_text or args.claim_file):
        raise ValueError(
            "Do not combine --claim-id with positional claim text or --claim-file; the stored "
            "claim text is the only text that may create claim-source edges."
        )
    if args.claim_id and args.all_mentions and not args.no_store:
        raise ValueError("--all-mentions with --claim-id requires --no-store.")
    if args.claim_id and (args.pdf or args.jats) and not args.no_store:
        raise ValueError(
            "Explicit --pdf/--jats input with --claim-id requires --no-store because the artifact "
            "cannot be verified against the stored claim carrier."
        )
    paper_id = args.paper_id or str(claim.get("source_paper_id") or "")
    claim_text = read_claim_text(args, claim)
    source_chunk_id = str(claim.get("source_chunk_id") or "")
    if not paper_id:
        raise ValueError("--paper-id is required when --claim-id is not used.")
    if not claim_text and not args.all_mentions:
        raise ValueError("Provide claim text, --claim-file, --claim-id, or --all-mentions.")

    registry_path = resolve_repo_path(args.registry)
    bib_dir = resolve_bib_dir(args.bib_dir)
    library = LibraryStatusResolver(
        registry_path=registry_path,
        bib_dir=bib_dir,
        chunks_dir=resolve_repo_path(args.chunks_dir),
    )
    if args.jats:
        xml_text = resolve_repo_path(args.jats).read_text(encoding="utf-8")
        citation_map = extract_jats_citation_map(xml_text, paper_id)
        source_path = resolve_repo_path(args.jats)
    else:
        source_path = resolve_pdf(args.pdf, paper_id, library)
        citation_map = extract_pdf_citation_map(source_path, paper_id)

    trace = trace_claim_sources(
        citation_map,
        "" if args.all_mentions else claim_text,
        min_score=0.0 if args.all_mentions else args.min_score,
        limit=len(citation_map.get("mentions", [])) if args.all_mentions else args.limit,
    )
    trace["carrier"] = carrier_record(paper_id, library)
    trace["source_artifact"] = display_path(source_path)
    trace["claim_id"] = args.claim_id or ""
    trace["source_chunk_id"] = source_chunk_id
    trace["run_configuration"] = {
        "all_mentions": bool(args.all_mentions),
        "online": bool(args.online),
        "fuzzy_library_candidates": True,
    }
    trace = resolve_trace_against_library(trace, library)
    if args.online:
        trace = enrich_unresolved_trace(trace, library, args)

    out_json, out_markdown = output_paths(
        args.out,
        paper_id,
        args.claim_id,
        claim_text,
        all_mentions=args.all_mentions,
    )
    write_json(out_json, trace)
    out_markdown.parent.mkdir(parents=True, exist_ok=True)
    out_markdown.write_text(format_trace_markdown(trace), encoding="utf-8")

    store_result = None
    if not args.no_store:
        store_result = upsert_source_trace(
            trace,
            db_path,
            claim_id=args.claim_id or "",
            source_chunk_id=source_chunk_id,
        )

    queue_result = None
    queueable = queueable_references(trace)
    if args.queue_missing:
        if not args.yes:
            print(f"Queue preview: {len(queueable)} uniquely resolved missing source(s).")
            print("No request row was written. Re-run with --queue-missing --yes to confirm.")
        else:
            queue_result = queue_missing_sources(
                queueable,
                resolve_repo_path(args.requests),
                category=args.category,
                note=f"claim_source_trace:{paper_id}",
            )

    print(format_console_summary(trace, out_json, out_markdown, store_result, queue_result))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Trace a claim through an inline citation marker to a referenced work and current-machine "
            "library status. PDF-derived semantic support remains review-required."
        )
    )
    parser.add_argument("claim_text", nargs="?", default="")
    parser.add_argument("--claim-file")
    parser.add_argument("--claim-id")
    parser.add_argument("--paper-id")
    parser.add_argument("--pdf", help="Explicit carrier PDF path; otherwise resolve it from the registry.")
    parser.add_argument("--jats", help="JATS XML path. Exact rid links are preferred over PDF heuristics.")
    parser.add_argument("--registry", default="data/metadata/paper_registry.tsv")
    parser.add_argument("--bib-dir", default="")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH.relative_to(REPO_ROOT)))
    parser.add_argument("--out", default="")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--min-score", type=float, default=55.0)
    parser.add_argument("--all-mentions", action="store_true")
    parser.add_argument("--online", action="store_true", help="Query Europe PMC/Crossref for unresolved metadata.")
    parser.add_argument("--crossref-mailto", default="")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--no-store", action="store_true")
    parser.add_argument("--queue-missing", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Confirm writes requested by --queue-missing.")
    parser.add_argument("--requests", default="data/metadata/paper_requests.tsv")
    parser.add_argument("--category", default="general_malaria")
    return parser.parse_args()


def read_claim(db_path: Path, claim_id: str) -> dict[str, object]:
    if not db_path.exists():
        raise FileNotFoundError(f"Claim database not found: {db_path}")
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
    if row is None:
        raise ValueError(f"Claim not found: {claim_id}")
    return dict(row)


def read_claim_text(args: argparse.Namespace, claim: dict[str, object]) -> str:
    if args.claim_file:
        return " ".join(resolve_repo_path(args.claim_file).read_text(encoding="utf-8").split())
    return " ".join(
        str(args.claim_text or claim.get("source_text") or claim.get("claim_text") or "").split()
    )


def resolve_bib_dir(value: str) -> Path:
    if value:
        return resolve_repo_path(value).expanduser().resolve()
    try:
        return get_bib_dir().expanduser().resolve()
    except OriginRAGConfigError:
        return (REPO_ROOT / "bib").resolve()


def resolve_pdf(value: str, paper_id: str, library: LibraryStatusResolver) -> Path:
    if value:
        path = resolve_repo_path(value)
        if not path.is_file():
            raise FileNotFoundError(f"Carrier PDF not found: {path}")
        return path
    status = library.resolve({"paper_id": paper_id})
    path_text = str((status.get("local_pdf") or {}).get("path") or "")
    path = Path(path_text) if path_text else None
    if not path or not path.is_file():
        raise FileNotFoundError(
            f"The registry entry for {paper_id} has no PDF on this machine. "
            "Pass --pdf or restore the local bibliography file."
        )
    return path


def carrier_record(paper_id: str, library: LibraryStatusResolver) -> dict[str, object]:
    status = library.resolve({"paper_id": paper_id})
    record = (status.get("registry") or {}).get("record") or {}
    return {
        "paper_id": paper_id,
        "citation": record.get("citation_apa") or record.get("citation_short") or paper_id,
        "title": record.get("title") or "",
        "doi": record.get("doi") or "",
        "library_status": status,
        "role": "claim_carrier",
    }


def enrich_unresolved_trace(
    trace: dict[str, object],
    library: LibraryStatusResolver,
    args: argparse.Namespace,
) -> dict[str, object]:
    output = dict(trace)
    mentions = []
    cache: dict[str, dict[str, object]] = {}
    for raw_mention in trace.get("mentions", []):
        mention = dict(raw_mention)
        reference = dict(mention.get("reference") or {})
        cache_key = str(reference.get("work_id") or reference.get("citation_text") or "")
        if reference.get("bibliographic_resolution_status") in {
            "unresolved",
            "identifier_extracted_unverified",
        }:
            if cache_key not in cache:
                cache[cache_key] = enrich_reference_online(
                    reference,
                    timeout_seconds=args.timeout,
                    mailto=args.crossref_mailto,
                )
            reference = dict(cache[cache_key])
            if reference.get("bibliographic_resolution_status") == "exact_id":
                reference = resolve_reference_against_library(reference, library)
            else:
                reference["library_status"] = library.resolve(reference)
        mention["reference"] = reference
        mention["bibliographic_resolution_status"] = reference.get(
            "bibliographic_resolution_status", "unresolved"
        )
        mentions.append(mention)
    output["mentions"] = mentions
    output["online_enrichment_requested"] = True
    output["n_exactly_resolved"] = sum(
        1
        for mention in mentions
        if mention.get("bibliographic_resolution_status") in {"exact_id", "exact_title"}
    )
    output["n_review_candidates"] = sum(
        1
        for mention in mentions
        if mention.get("bibliographic_resolution_status") == "candidate_match"
    )
    return output


def queueable_references(trace: dict[str, object]) -> list[dict[str, object]]:
    output: dict[str, dict[str, object]] = {}
    for mention in trace.get("mentions", []):
        if not isinstance(mention, dict):
            continue
        reference = dict(mention.get("reference") or {})
        resolution = str(reference.get("bibliographic_resolution_status") or "")
        library_state = str((reference.get("library_status") or {}).get("state") or "")
        verified_identifiers = reference.get("verified_identifiers") or {}
        has_verified_identifier = any(
            str(verified_identifiers.get(field) or "").strip()
            for field in ["doi", "pmid", "pmcid"]
        )
        if resolution not in {"exact_id", "exact_title"} or not has_verified_identifier:
            continue
        if library_state not in {"registry_only", "oa_available", "not_in_library"}:
            continue
        queue_reference = dict(reference)
        for field in ["doi", "pmid", "pmcid"]:
            queue_reference[field] = str(verified_identifiers.get(field) or "")
        output.setdefault(str(reference.get("work_id") or ""), queue_reference)
    return list(output.values())


def format_trace_markdown(trace: dict[str, object]) -> str:
    carrier = trace.get("carrier") or {}
    lines = [
        "# Claim Source Trace",
        "",
        f"- Carrier: {carrier.get('citation') or carrier.get('paper_id') or 'unknown'}",
        "- Carrier role: claim carrier (the paper containing the retrieved statement)",
        f"- Claim ID: {trace.get('claim_id') or 'not supplied'}",
        f"- Status: {trace.get('status')}",
        f"- Parser: {trace.get('parser_version')}",
        f"- Dependencies: `{json.dumps(trace.get('runtime_dependencies') or {}, sort_keys=True)}`",
        f"- Trace parameters: `{json.dumps(trace.get('trace_parameters') or {}, sort_keys=True)}`",
        f"- Source SHA-256: `{trace.get('source_sha256')}`",
        "",
        "## Claim",
        "",
        str(trace.get("claim_text") or "All citation mentions requested."),
        "",
        "## Citation trail candidates",
        "",
    ]
    mentions = trace.get("mentions") or []
    if not mentions:
        lines.append(
            "No claim-local citation could be linked at the selected threshold. This is an abstention, "
            "not evidence that the carrier paper is the primary source."
        )
    for index, mention in enumerate(mentions, start=1):
        reference = mention.get("reference") or {}
        library_status = reference.get("library_status") or {}
        citation = reference.get("citation_display") or reference.get("citation_text") or "Unresolved reference"
        lines.extend(
            [
                f"### {index}. {library_status.get('display') or '? Source unresolved'}",
                "",
                f"- Registry citation: {citation}",
                f"- Printed reference: {reference.get('citation_text') or 'not available'}",
                f"- DOI: {reference.get('doi') or 'not resolved'}",
                f"- Link: {reference.get('landing_url') or 'not available'}",
                f"- Locator: page {mention.get('source_page') or '?'}; marker {mention.get('marker_text') or '?'}",
                f"- Citation linkage: {mention.get('citation_link_status')}",
                f"- Bibliographic resolution: {reference.get('bibliographic_resolution_status') or 'unresolved'}",
                "- Verified identifiers: "
                + (
                    ", ".join(
                        f"{field.upper()} {value}"
                        for field, value in (reference.get("verified_identifiers") or {}).items()
                        if value
                    )
                    or "none"
                ),
                f"- Suggested role: {mention.get('support_role')} / {mention.get('support_relation')}",
                f"- Claim-context score: {mention.get('claim_match_score')}",
                f"- Review status: {mention.get('origin_review_status')}",
                "",
                f"> {mention.get('context_text') or ''}",
                "",
            ]
        )
    lines.extend(
        [
            "## Interpretation boundary",
            "",
            "An exact citation marker or bibliographic identity does not establish that a cited work supports "
            "the claim, is the primary study, or represents an independent dataset. PDF-derived scope and all "
            "study/dataset-origin edges remain review-required until explicitly adjudicated.",
            "",
        ]
    )
    return "\n".join(lines)


def format_console_summary(
    trace: dict[str, object],
    out_json: Path,
    out_markdown: Path,
    store_result: dict[str, int] | None,
    queue_result: dict[str, object] | None,
) -> str:
    lines = [
        f"Linked {trace.get('n_linked_candidates', 0)} citation candidate(s).",
        f"JSON: {display_path(out_json)}",
        f"Report: {display_path(out_markdown)}",
    ]
    if store_result is not None:
        lines.append(f"Local graph: {json.dumps(store_result, sort_keys=True)}")
    if queue_result is not None:
        lines.append(
            f"Queue: {len(queue_result.get('added', []))} added, "
            f"{len(queue_result.get('skipped', []))} skipped."
        )
    return "\n".join(lines)


def output_paths(
    value: str,
    paper_id: str,
    claim_id: str,
    claim_text: str,
    *,
    all_mentions: bool,
) -> tuple[Path, Path]:
    if value:
        path = resolve_repo_path(value)
        if path.suffix.lower() == ".json":
            return path, path.with_suffix(".md")
        return path.with_suffix(".json"), path.with_suffix(".md")
    claim_digest = hashlib.sha256(claim_text.encode("utf-8")).hexdigest()[:12]
    suffix = claim_id or ("all_mentions" if all_mentions else f"claim_{claim_digest}")
    safe_paper_id = safe_filename_token(paper_id)
    safe_suffix = safe_filename_token(suffix)
    base = REPO_ROOT / "outputs" / "source_traces" / f"{safe_paper_id}_{safe_suffix}"
    return base.with_suffix(".json"), base.with_suffix(".md")


def safe_filename_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._-")
    return token or "unknown"


def resolve_repo_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def display_path(path: Path) -> str:
    resolved = Path(path).resolve()
    return resolved.relative_to(REPO_ROOT).as_posix() if resolved.is_relative_to(REPO_ROOT) else str(resolved)


if __name__ == "__main__":
    raise SystemExit(main())
