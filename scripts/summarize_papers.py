from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.summaries.paper_summary import summarize_paper_with_ollama, write_jsonl  # noqa: E402


def main() -> int:
    args = parse_args()
    papers = read_papers(resolve_repo_path(args.papers))
    chunks_dir = resolve_repo_path(args.chunks_dir)
    out_path = resolve_repo_path(args.out)
    report_path = resolve_repo_path(args.report)
    raw_dir = out_path.parent / "raw_paper_summary_outputs"
    raw_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    invalid = []
    processed = 0
    for paper in papers:
        if args.limit and processed >= args.limit:
            break
        chunks = read_chunks(chunks_dir / f"{paper['paper_id']}.jsonl")
        if not chunks:
            continue
        processed += 1
        if args.backend == "none":
            summary = deterministic_summary(paper, chunks)
            summaries.append(summary)
            continue
        result = summarize_paper_with_ollama(
            paper,
            chunks,
            model=args.model,
            temperature=args.temperature,
            timeout=args.timeout,
            max_chunks=args.max_chunks,
        )
        (raw_dir / f"{paper['paper_id']}.txt").write_text(result.raw_output or result.error or "", encoding="utf-8")
        if result.summary:
            summaries.append(result.summary)
        else:
            invalid.append({"paper_id": paper["paper_id"], "error": result.error or "unknown_error"})

    write_jsonl(out_path, summaries)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(format_report(processed, summaries, invalid), encoding="utf-8")
    print(f"Papers processed: {processed}")
    print(f"Summaries created: {len(summaries)}")
    print(f"Invalid summaries: {len(invalid)}")
    print(f"Wrote summaries: {display_path(out_path)}")
    print(f"Wrote report: {display_path(report_path)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create structured local paper summaries.")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--backend", choices=["none", "ollama"], default="ollama")
    parser.add_argument("--model", default="qwen3:30b")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-chunks", type=int, default=8)
    parser.add_argument("--out", default="outputs/paper_summaries/paper_summaries_v1.jsonl")
    parser.add_argument("--report", default="outputs/paper_summaries/paper_summaries_v1_report.md")
    return parser.parse_args()


def read_papers(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t") if row.get("paper_id")]


def read_chunks(path: Path) -> list[dict]:
    if not path.exists():
        return []
    chunks = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                chunks.append(json.loads(line))
    return chunks


def deterministic_summary(paper: dict[str, str], chunks: list[dict]) -> dict:
    source_chunks = [str(chunk.get("chunk_id")) for chunk in chunks[:3] if chunk.get("chunk_id")]
    return {
        "paper_id": paper.get("paper_id", ""),
        "title": paper.get("title", ""),
        "year": paper.get("year", ""),
        "short_summary": "No summary generated: model generation is disabled (--backend none).",
        "background": "",
        "methods": "",
        "datasets_or_cohorts": [],
        "key_findings": [],
        "limitations": [],
        "host_angle": "",
        "parasite_angle": "",
        "immune_angle": "",
        "omics_angle": "",
        "computational_angle": "",
        "disease_context": "",
        "relevance_to_pfempire": "",
        "source_chunks": source_chunks,
    }


def format_report(processed: int, summaries: list[dict], invalid: list[dict]) -> str:
    return "\n".join(
        [
            "# Paper Summaries v1 Report",
            "",
            f"- Papers processed: {processed}",
            f"- Summaries created: {len(summaries)}",
            f"- Invalid summaries: {len(invalid)}",
            "",
            "## Invalid",
            *[f"- {item['paper_id']}: {item['error']}" for item in invalid],
            "",
        ]
    )


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
