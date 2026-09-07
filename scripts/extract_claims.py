from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.analysis.ollama_client import call_ollama_generate  # noqa: E402
from pfempire.claims.extraction import extract_claims_from_chunk  # noqa: E402
from pfempire.retrieval.llm_query_planner import parse_json_object  # noqa: E402


ALLOWED_TYPES = {
    "finding", "method", "dataset", "limitation", "speculation", "hypothesis",
    "background", "negative_result", "contradiction",
}
ALLOWED_STRENGTHS = {"strong", "moderate", "weak", "unclear"}


def main() -> int:
    args = parse_args()
    papers = read_papers(resolve_repo_path(args.papers))
    chunks = select_chunks(
        chunks_dir=resolve_repo_path(args.chunks_dir),
        papers=papers,
        limit=args.limit,
        prefer_sections=set(args.prefer_sections or []),
    )
    out_path = resolve_repo_path(args.out)
    report_path = resolve_repo_path(args.report)
    raw_dir = out_path.parent / "raw_claim_outputs"
    invalid_path = out_path.parent / "invalid_claim_outputs.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    valid_claims: list[dict] = []
    invalid_records: list[dict] = []
    chunks_processed = 0
    for chunk in chunks:
        chunks_processed += 1
        paper_meta = papers.get(chunk.get("paper_id", ""), {})
        if args.backend == "none":
            claims = extract_claims_from_chunk(chunk, paper_meta=paper_meta)
            valid_claims.extend(claims)
            continue

        prompt = build_claim_prompt(chunk, paper_meta)
        result = call_ollama_generate(
            prompt,
            model=args.model,
            temperature=args.temperature,
            timeout=args.timeout,
            num_predict=1400,
        )
        raw_file = raw_dir / f"{chunk.get('chunk_id', 'chunk')}.txt"
        raw_file.write_text(result.text or result.error or "", encoding="utf-8")
        if result.error or not result.text:
            invalid_records.append(invalid_record(chunk, result.error or "empty_model_output", result.text))
            continue
        try:
            payload = parse_json_object(result.text)
            claims = normalize_claims(payload, chunk, paper_meta)
        except Exception as exc:  # noqa: BLE001 - batch extraction should continue
            invalid_records.append(invalid_record(chunk, str(exc), result.text))
            continue
        valid_claims.extend(claims)

    write_jsonl(out_path, valid_claims)
    write_jsonl(invalid_path, invalid_records)
    report_path.write_text(format_report(chunks_processed, valid_claims, invalid_records), encoding="utf-8")
    print(f"Chunks processed: {chunks_processed}")
    print(f"Valid claims: {len(valid_claims)}")
    print(f"Invalid outputs: {len(invalid_records)}")
    print(f"Wrote claims: {display_path(out_path)}")
    print(f"Wrote report: {display_path(report_path)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract scientific claims from local chunks.")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--backend", choices=["none", "ollama"], default="ollama")
    parser.add_argument("--model", default="qwen3:30b")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--prefer-sections", nargs="*", default=["results", "discussion", "abstract"])
    parser.add_argument("--out", default="outputs/claims/claims_v1.jsonl")
    parser.add_argument("--report", default="outputs/claims/claims_v1_report.md")
    return parser.parse_args()


def build_claim_prompt(chunk: dict, paper_meta: dict[str, str]) -> str:
    template = (REPO_ROOT / "config/prompts/extract_claims.md").read_text(encoding="utf-8")
    return f"""{template}

Paper:
- paper_id: {chunk.get('paper_id', '')}
- title: {paper_meta.get('title', '')}
- year: {paper_meta.get('year', '')}
- doi: {paper_meta.get('doi', '')}

Chunk:
- chunk_id: {chunk.get('chunk_id', '')}
- section: {chunk.get('section', 'unknown')}
- pages: {chunk.get('page_start', '')}-{chunk.get('page_end', '')}

Text:
{str(chunk.get('text') or '')[:4500]}
"""


def normalize_claims(payload: dict, chunk: dict, paper_meta: dict[str, str]) -> list[dict]:
    raw_claims = payload.get("claims", [])
    if not isinstance(raw_claims, list):
        raise ValueError("claims_not_list")
    claims = []
    for index, raw in enumerate(raw_claims, start=1):
        if not isinstance(raw, dict):
            continue
        text = " ".join(str(raw.get("claim_text") or "").split())
        if not text:
            continue
        claim_type = str(raw.get("claim_type") or "finding").strip()
        strength = str(raw.get("evidence_strength") or "unclear").strip()
        if claim_type not in ALLOWED_TYPES:
            claim_type = "finding"
        if strength not in ALLOWED_STRENGTHS:
            strength = "unclear"
        source_pages = page_range(chunk)
        claim_id = f"{chunk.get('paper_id', 'PAPER')}_{chunk.get('chunk_id', 'CHUNK')}_LLM_{index:02d}"
        claims.append(
            {
                "claim_id": claim_id,
                "claim_text": text,
                "claim_type": claim_type,
                "evidence_strength": strength,
                "evidence_type": str(raw.get("evidence_type") or ""),
                "direction": str(raw.get("direction") or "unknown"),
                "uncertainty": str(raw.get("uncertainty") or "unclear"),
                "organism": str(raw.get("organism") or ""),
                "pathogen": str(raw.get("pathogen") or ""),
                "host_system": str(raw.get("host_system") or ""),
                "disease_context": str(raw.get("disease_context") or ""),
                "phenotype": str(raw.get("phenotype") or ""),
                "parasite_feature": str(raw.get("parasite_feature") or ""),
                "host_feature": str(raw.get("host_feature") or ""),
                "method": str(raw.get("method") or ""),
                "dataset_or_cohort": str(raw.get("dataset_or_cohort") or ""),
                "source_paper_id": str(chunk.get("paper_id") or ""),
                "source_chunk_id": str(chunk.get("chunk_id") or ""),
                "source_section": str(chunk.get("section") or "unknown"),
                "source_pages": source_pages,
                "entities": raw.get("entities") if isinstance(raw.get("entities"), list) else [],
                "title": paper_meta.get("title", ""),
                "year": paper_meta.get("year", ""),
                "doi": paper_meta.get("doi", ""),
            }
        )
    return claims


def select_chunks(chunks_dir: Path, papers: dict[str, dict[str, str]], limit: int, prefer_sections: set[str]) -> list[dict]:
    selected = []
    for paper_id in sorted(papers):
        path = chunks_dir / f"{paper_id}.jsonl"
        if not path.exists():
            continue
        for chunk in read_jsonl(path):
            section = str(chunk.get("section") or "unknown")
            if prefer_sections and section not in prefer_sections:
                continue
            selected.append(chunk)
            if limit and len(selected) >= limit:
                return selected
    return selected


def read_papers(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {row["paper_id"]: dict(row) for row in csv.DictReader(handle, delimiter="\t") if row.get("paper_id")}


def read_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def invalid_record(chunk: dict, error: str, raw_output: str | None) -> dict:
    return {
        "paper_id": chunk.get("paper_id", ""),
        "chunk_id": chunk.get("chunk_id", ""),
        "error": error,
        "raw_output": raw_output or "",
    }


def page_range(chunk: dict) -> str:
    start = chunk.get("page_start")
    end = chunk.get("page_end")
    if start in ("", None) and end in ("", None):
        return ""
    return str(start) if start == end else f"{start}-{end}"


def format_report(chunks_processed: int, claims: list[dict], invalid: list[dict]) -> str:
    type_counts = Counter(claim.get("claim_type", "") for claim in claims)
    strength_counts = Counter(claim.get("evidence_strength", "") for claim in claims)
    entities = Counter()
    for claim in claims:
        for entity in claim.get("entities", []):
            entities[str(entity)] += 1
    lines = [
        "# Claim Extraction v1 Report",
        "",
        f"- Chunks processed: {chunks_processed}",
        f"- Valid claims: {len(claims)}",
        f"- Invalid outputs: {len(invalid)}",
        "",
        "## Claims By Type",
        *[f"- {key}: {value}" for key, value in sorted(type_counts.items())],
        "",
        "## Evidence Strength",
        *[f"- {key}: {value}" for key, value in sorted(strength_counts.items())],
        "",
        "## Top Entities",
        *[f"- {key}: {value}" for key, value in entities.most_common(20)],
        "",
    ]
    return "\n".join(lines)


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
