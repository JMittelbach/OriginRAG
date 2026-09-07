from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from originrag.retrieval.chunk_quality import classify_chunk_quality  # noqa: E402
from originrag.retrieval.metadata import load_paper_metadata  # noqa: E402


FLAG_FIELDS = [
    "empty",
    "too_short",
    "reference_like",
    "table_like",
    "figure_like",
    "supplement_like",
    "methods_like",
    "low_text_quality",
]

CHUNK_FIELDS = [
    "chunk_id",
    "paper_id",
    "chunk_index",
    "page_start",
    "page_end",
    "author",
    "year",
    "title",
    "journal",
    "category",
    "topics",
    "word_count",
    "char_count",
    "digit_fraction",
    "punctuation_fraction",
    "uppercase_fraction",
    "quality_penalty",
    "flags",
    "snippet",
]

PAPER_FIELDS = [
    "paper_id",
    "author",
    "year",
    "title",
    "journal",
    "category",
    "topics",
    "total_chunks",
    "avg_quality_penalty",
    *[f"{field}_chunks" for field in FLAG_FIELDS],
]


def main() -> int:
    args = parse_args()
    chunks_dir = resolve_repo_path(args.chunks_dir)
    papers_tsv = resolve_repo_path(args.papers)
    out_md = resolve_repo_path(args.out_md)
    out_paper_tsv = resolve_repo_path(args.out_paper_tsv)
    out_chunk_tsv = resolve_repo_path(args.out_chunk_tsv)

    metadata = load_paper_metadata(papers_tsv)
    chunk_rows = analyze_chunks(chunks_dir, metadata)
    paper_rows = summarize_by_paper(chunk_rows, metadata)

    write_tsv(out_chunk_tsv, chunk_rows, CHUNK_FIELDS)
    write_tsv(out_paper_tsv, paper_rows, PAPER_FIELDS)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(build_markdown_report(chunk_rows, paper_rows), encoding="utf-8")

    print(f"Wrote chunk QC report to {out_md.relative_to(REPO_ROOT)}")
    print(f"Wrote per-paper QC TSV to {out_paper_tsv.relative_to(REPO_ROOT)}")
    print(f"Wrote per-chunk QC TSV to {out_chunk_tsv.relative_to(REPO_ROOT)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create retrieval chunk quality reports.")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--out-md", default="outputs/retrieval_eval/chunk_qc_report.md")
    parser.add_argument("--out-paper-tsv", default="outputs/retrieval_eval/chunk_qc_by_paper.tsv")
    parser.add_argument("--out-chunk-tsv", default="outputs/retrieval_eval/chunk_qc_by_chunk.tsv")
    return parser.parse_args()


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def analyze_chunks(chunks_dir: Path, metadata: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for chunk_file in sorted(chunks_dir.glob("*.jsonl")):
        with chunk_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                paper_id = str(chunk.get("paper_id") or "")
                text = str(chunk.get("text") or "")
                quality = classify_chunk_quality(text)
                paper_meta = metadata.get(paper_id, {})
                row = {
                    "chunk_id": str(chunk.get("chunk_id") or ""),
                    "paper_id": paper_id,
                    "chunk_index": str(chunk.get("chunk_index") or ""),
                    "page_start": str(chunk.get("page_start") or ""),
                    "page_end": str(chunk.get("page_end") or ""),
                    "author": paper_meta.get("author", ""),
                    "year": paper_meta.get("year", ""),
                    "title": paper_meta.get("title", ""),
                    "journal": paper_meta.get("journal", ""),
                    "category": paper_meta.get("category", ""),
                    "topics": paper_meta.get("topics", ""),
                    "word_count": str(quality["word_count"]),
                    "char_count": str(quality["char_count"]),
                    "digit_fraction": str(quality["digit_fraction"]),
                    "punctuation_fraction": str(quality["punctuation_fraction"]),
                    "uppercase_fraction": str(quality["uppercase_fraction"]),
                    "quality_penalty": str(quality["quality_penalty"]),
                    "flags": ";".join(quality["flags"]),
                    "snippet": clean_snippet(text),
                }
                rows.append(row)
    return rows


def summarize_by_paper(
    chunk_rows: list[dict[str, str]],
    metadata: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    rows_by_paper: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in chunk_rows:
        rows_by_paper[row["paper_id"]].append(row)

    paper_rows: list[dict[str, str]] = []
    for paper_id, rows in sorted(rows_by_paper.items()):
        paper_meta = metadata.get(paper_id, {})
        penalties = [float(row["quality_penalty"] or 0) for row in rows]
        summary = {
            "paper_id": paper_id,
            "author": paper_meta.get("author", ""),
            "year": paper_meta.get("year", ""),
            "title": paper_meta.get("title", ""),
            "journal": paper_meta.get("journal", ""),
            "category": paper_meta.get("category", ""),
            "topics": paper_meta.get("topics", ""),
            "total_chunks": str(len(rows)),
            "avg_quality_penalty": f"{statistics.mean(penalties):.3f}" if penalties else "0.000",
        }
        for flag in FLAG_FIELDS:
            summary[f"{flag}_chunks"] = str(sum(flag in row["flags"].split(";") for row in rows))
        paper_rows.append(summary)
    return paper_rows


def build_markdown_report(chunk_rows: list[dict[str, str]], paper_rows: list[dict[str, str]]) -> str:
    total_chunks = len(chunk_rows)
    flag_counts = Counter()
    for row in chunk_rows:
        for flag in [flag for flag in row["flags"].split(";") if flag]:
            flag_counts[flag] += 1

    chunk_counts = [int(row["total_chunks"]) for row in paper_rows]
    suspicious = sorted(
        chunk_rows,
        key=lambda row: (
            -float(row["quality_penalty"] or 0),
            -len([flag for flag in row["flags"].split(";") if flag]),
            row["paper_id"],
        ),
    )[:20]
    top_papers = sorted(paper_rows, key=lambda row: int(row["total_chunks"]), reverse=True)[:15]

    lines = [
        "# Chunk QC Report",
        "",
        "## Summary",
        "",
        f"- Papers with chunks: {len(paper_rows)}",
        f"- Total chunks: {total_chunks}",
    ]
    if chunk_counts:
        lines.extend(
            [
                f"- Min chunks per paper: {min(chunk_counts)}",
                f"- Median chunks per paper: {statistics.median(chunk_counts):.1f}",
                f"- Max chunks per paper: {max(chunk_counts)}",
            ]
        )

    lines.extend(["", "## Quality Flags", "", "| flag | chunks | percent |", "| --- | ---: | ---: |"])
    for flag in FLAG_FIELDS:
        count = flag_counts.get(flag, 0)
        percent = (count / total_chunks * 100) if total_chunks else 0
        lines.append(f"| {flag} | {count} | {percent:.1f}% |")

    lines.extend(["", "## Top Papers By Chunk Count", "", "| paper_id | author | year | chunks | title |", "| --- | --- | --- | ---: | --- |"])
    for row in top_papers:
        lines.append(
            "| {paper_id} | {author} | {year} | {chunks} | {title} |".format(
                paper_id=row["paper_id"],
                author=escape_md(row["author"]),
                year=row["year"],
                chunks=row["total_chunks"],
                title=escape_md(row["title"]),
            )
        )

    lines.extend(
        [
            "",
            "## Top Suspicious Chunks",
            "",
            "| chunk_id | paper_id | pages | penalty | flags | snippet |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in suspicious:
        pages = format_page_range(row["page_start"], row["page_end"])
        lines.append(
            "| {chunk_id} | {paper_id} | {pages} | {penalty} | {flags} | {snippet} |".format(
                chunk_id=row["chunk_id"],
                paper_id=row["paper_id"],
                pages=pages,
                penalty=row["quality_penalty"],
                flags=escape_md(row["flags"] or "none"),
                snippet=escape_md(row["snippet"]),
            )
        )

    lines.append("")
    return "\n".join(lines)


def write_tsv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def clean_snippet(text: str, limit: int = 220) -> str:
    snippet = " ".join((text or "").split())
    if len(snippet) > limit:
        return snippet[: limit - 3].rstrip() + "..."
    return snippet


def escape_md(value: str) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")


def format_page_range(page_start: object, page_end: object) -> str:
    if page_start is None and page_end is None:
        return "?"
    if page_start == page_end:
        return str(page_start)
    return f"{page_start}-{page_end}"


if __name__ == "__main__":
    raise SystemExit(main())
