from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from originrag.retrieval.chunk_quality import classify_chunk_quality  # noqa: E402
from originrag.retrieval.metadata import load_paper_metadata  # noqa: E402
from originrag.retrieval.section_detection import (  # noqa: E402
    carries_forward_section,
    detect_section_for_chunk,
    stored_section_needs_redetection,
)


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
    "section",
    "section_confidence",
    "section_markers",
    "quality_flags",
    "snippet",
]

PAPER_FIELDS = [
    "paper_id",
    "author",
    "year",
    "title",
    "journal",
    "category",
    "total_chunks",
    "unknown_chunks",
    "reference_chunks",
    "table_chunks",
    "figure_legend_chunks",
    "supplement_chunks",
    "dominant_section",
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

    print(f"Wrote section QC report to {out_md.relative_to(REPO_ROOT)}")
    print(f"Wrote per-paper section TSV to {out_paper_tsv.relative_to(REPO_ROOT)}")
    print(f"Wrote per-chunk section TSV to {out_chunk_tsv.relative_to(REPO_ROOT)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create section-detection QC reports for local chunks.")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--out-md", default="outputs/retrieval_eval/section_qc_report.md")
    parser.add_argument("--out-paper-tsv", default="outputs/retrieval_eval/section_qc_by_paper.tsv")
    parser.add_argument("--out-chunk-tsv", default="outputs/retrieval_eval/section_qc_by_chunk.tsv")
    return parser.parse_args()


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def analyze_chunks(chunks_dir: Path, metadata: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for chunk_file in sorted(chunks_dir.glob("*.jsonl")):
        previous_section: str | None = None
        with chunk_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                paper_id = str(chunk.get("paper_id") or "")
                text = str(chunk.get("text") or "")
                section = str(chunk.get("section") or "")
                if stored_section_needs_redetection(chunk):
                    detected = detect_section_for_chunk(text, previous_section=previous_section)
                    section = detected["section"]
                    confidence = detected["section_confidence"]
                    markers = detected["section_markers"]
                else:
                    confidence = chunk.get("section_confidence", "")
                    markers = chunk.get("section_markers") or []
                if carries_forward_section(section):
                    previous_section = section
                quality = classify_chunk_quality(text)
                paper_meta = metadata.get(paper_id, {})
                rows.append(
                    {
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
                        "section": section or "unknown",
                        "section_confidence": str(confidence),
                        "section_markers": ";".join(str(marker) for marker in markers),
                        "quality_flags": ";".join(quality["flags"]),
                        "snippet": clean_snippet(text),
                    }
                )
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
        section_counts = Counter(row["section"] for row in rows)
        dominant_section, _ = section_counts.most_common(1)[0]
        paper_rows.append(
            {
                "paper_id": paper_id,
                "author": paper_meta.get("author", ""),
                "year": paper_meta.get("year", ""),
                "title": paper_meta.get("title", ""),
                "journal": paper_meta.get("journal", ""),
                "category": paper_meta.get("category", ""),
                "total_chunks": str(len(rows)),
                "unknown_chunks": str(section_counts.get("unknown", 0)),
                "reference_chunks": str(section_counts.get("references", 0)),
                "table_chunks": str(section_counts.get("table", 0)),
                "figure_legend_chunks": str(section_counts.get("figure_legend", 0)),
                "supplement_chunks": str(section_counts.get("supplement", 0)),
                "dominant_section": dominant_section,
            }
        )
    return paper_rows


def build_markdown_report(chunk_rows: list[dict[str, str]], paper_rows: list[dict[str, str]]) -> str:
    total_chunks = len(chunk_rows)
    section_counts = Counter(row["section"] for row in chunk_rows)
    unknown_count = section_counts.get("unknown", 0)
    unknown_percent = (unknown_count / total_chunks * 100) if total_chunks else 0.0

    warning_lines: list[str] = []
    if unknown_percent > 50:
        warning_lines.append(f"- Warning: {unknown_percent:.1f}% of chunks are unknown.")
    heavy_problem_papers = [
        row
        for row in paper_rows
        if int(row["reference_chunks"]) + int(row["table_chunks"]) + int(row["figure_legend_chunks"]) > int(row["total_chunks"]) * 0.5
    ]
    if heavy_problem_papers:
        warning_lines.append(f"- Warning: {len(heavy_problem_papers)} papers are dominated by reference/table/figure chunks.")

    lines = [
        "# Section QC Report",
        "",
        "## Summary",
        "",
        f"- Papers with chunks: {len(paper_rows)}",
        f"- Total chunks: {total_chunks}",
        f"- Unknown chunks: {unknown_count} ({unknown_percent:.1f}%)",
    ]
    if warning_lines:
        lines.extend(["", "## Warnings", "", *warning_lines])

    lines.extend(["", "## Section Counts", "", "| section | chunks | percent |", "| --- | ---: | ---: |"])
    for section, count in sorted(section_counts.items()):
        percent = (count / total_chunks * 100) if total_chunks else 0.0
        lines.append(f"| {section} | {count} | {percent:.1f}% |")

    mostly_unknown = sorted(
        [
            row
            for row in paper_rows
            if int(row["total_chunks"]) and int(row["unknown_chunks"]) / int(row["total_chunks"]) >= 0.7
        ],
        key=lambda row: (-int(row["unknown_chunks"]), row["paper_id"]),
    )[:20]
    lines.extend(["", "## Papers Mostly Unknown", "", "| paper_id | author | year | unknown/total | title |", "| --- | --- | --- | ---: | --- |"])
    if mostly_unknown:
        for row in mostly_unknown:
            lines.append(
                f"| {row['paper_id']} | {escape_md(row['author'])} | {row['year']} | {row['unknown_chunks']}/{row['total_chunks']} | {escape_md(row['title'])} |"
            )
    else:
        lines.append("|  |  |  | 0 | none |")

    examples = _examples_by_section(chunk_rows)
    lines.extend(["", "## Example Chunks By Section", ""])
    for section, row in examples.items():
        lines.extend(
            [
                f"### {section}",
                "",
                f"- {row['paper_id']} {row['author']} {row['year']} pages {format_page_range(row['page_start'], row['page_end'])}",
                f"- {row['snippet']}",
                "",
            ]
        )

    lines.append("")
    return "\n".join(lines)


def _examples_by_section(chunk_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    examples: dict[str, dict[str, str]] = {}
    preferred = [
        "abstract",
        "introduction",
        "methods",
        "results",
        "discussion",
        "figure_legend",
        "table",
        "supplement",
        "references",
        "data_availability",
        "unknown",
    ]
    for section in preferred:
        for row in chunk_rows:
            if row["section"] == section:
                examples[section] = row
                break
    return examples


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
    if page_start in ("", None) and page_end in ("", None):
        return "?"
    if page_start == page_end:
        return str(page_start)
    return f"{page_start}-{page_end}"


if __name__ == "__main__":
    raise SystemExit(main())
