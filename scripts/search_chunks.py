from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from rich.console import Console
except ImportError:
    Console = None

from originrag.retrieval.fts_index import search_chunks_bm25  # noqa: E402
from originrag.retrieval.keyword import search_chunks  # noqa: E402


OUT_FIELDS = [
    "rank",
    "score",
    "raw_score",
    "section_weight",
    "quality_penalty",
    "chunk_id",
    "paper_id",
    "chunk_index",
    "page_start",
    "page_end",
    "section",
    "section_confidence",
    "section_markers",
    "quality_flags",
    "author",
    "year",
    "title",
    "journal",
    "category",
    "domain",
    "topics",
    "methods",
    "systems",
    "paper_type",
    "local_pdf_relpath",
    "snippet",
    "text_preview",
    "source_file",
    "line_number",
]


def main() -> int:
    args = parse_args()
    chunks_dir = resolve_repo_path(args.chunks_dir)
    papers_tsv = resolve_repo_path(args.papers) if args.papers else None

    search_backend = search_chunks_bm25 if args.retriever == "bm25" else search_chunks
    search_kwargs = {"index_path": resolve_repo_path(args.retrieval_index)} if args.retriever == "bm25" else {}
    results = search_backend(
        query=args.query,
        chunks_dir=chunks_dir,
        papers_tsv=papers_tsv,
        limit=args.limit,
        include_low_quality=args.include_low_quality,
        exclude_references=args.exclude_references,
        exclude_tables=args.exclude_tables,
        exclude_figures=args.exclude_figures,
        exclude_supplement=args.exclude_supplement,
        category=args.category,
        paper_id=args.paper_id,
        min_score=args.min_score,
        max_per_paper=args.max_per_paper,
        query_mode=args.query_mode,
        prefer_sections=args.prefer_sections,
        exclude_sections=args.exclude_sections,
        include_sections=args.include_sections,
        include_inactive=args.include_inactive,
        **search_kwargs,
    )

    if args.out:
        write_results_tsv(resolve_repo_path(args.out), results)

    print_results(args.query, results, args)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search local OriginRAG chunk JSONL files.")
    parser.add_argument("query", help="Keyword query")
    parser.add_argument("--limit", type=int, default=20, help="Maximum results to print")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--retriever", choices=["keyword", "bm25"], default="keyword")
    parser.add_argument("--retrieval-index", default="data/indexes/pfempire_fts.sqlite")
    parser.add_argument("--max-per-paper", type=int, default=2, help="Maximum chunks per paper. Use 0 for no cap.")
    parser.add_argument("--include-low-quality", action="store_true")
    parser.add_argument("--exclude-references", dest="exclude_references", action="store_true")
    parser.add_argument("--include-references", dest="exclude_references", action="store_false")
    parser.set_defaults(exclude_references=True)
    parser.add_argument("--exclude-tables", action="store_true")
    parser.add_argument("--exclude-figures", action="store_true")
    parser.add_argument("--exclude-supplement", action="store_true")
    parser.add_argument("--query-mode", choices=["evidence", "methods", "background"], default="evidence")
    parser.add_argument("--prefer-sections", nargs="+", help="Boost these sections, for example: results discussion abstract")
    parser.add_argument("--exclude-sections", nargs="+", help="Exclude these sections.")
    parser.add_argument("--include-sections", nargs="+", help="Only include these sections.")
    parser.add_argument("--include-inactive", action="store_true", help="Also search duplicate/archived/excluded papers.")
    parser.add_argument("--show-section", action="store_true")
    parser.add_argument("--category")
    parser.add_argument("--paper-id")
    parser.add_argument("--min-score", type=float, default=0.0)
    parser.add_argument("--show-full", action="store_true")
    parser.add_argument("--out", help="Optional TSV output path")
    return parser.parse_args()


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def print_results(query: str, results: list[dict], args: argparse.Namespace) -> None:
    console = Console() if Console else None
    write = console.print if console else print
    if not results:
        write("No matching chunks found.")
        return

    for result in results:
        flags = ", ".join(result.get("quality_flags") or []) or "none"
        section = str(result.get("section") or "unknown")
        page_range = format_page_range(result.get("page_start"), result.get("page_end"))
        author_year = " ".join(
            value for value in [result.get("author", ""), result.get("year", "")] if value
        )
        if not author_year:
            author_year = str(result.get("year") or "").strip()
        section_part = f" | section={section}" if args.show_section else ""
        heading = (
            f"[{result['rank']}] score={result['score']} "
            f"(raw={result['raw_score']}, section_weight={result.get('section_weight')}, "
            f"penalty={result['quality_penalty']}){section_part} | "
            f"{result['paper_id']} | {author_year or 'metadata missing'} | pages {page_range}"
        )
        write(heading)
        if result.get("title"):
            write(f"Title: {result['title']}")
        if result.get("journal") or result.get("category") or result.get("topics"):
            write(
                "Meta: "
                f"journal={result.get('journal') or '-'} | "
                f"category={result.get('category') or '-'} | "
                f"topics={result.get('topics') or '-'}"
            )
        if args.show_section:
            markers = ";".join(result.get("section_markers") or []) or "-"
            write(
                "Section: "
                f"{section} | confidence={result.get('section_confidence')} | markers={markers}"
            )
        write(f"Flags: {flags}")
        write("Snippet:")
        write(result["text_preview"] if args.show_full else result["snippet"])
        write("")

    unique_papers = {result.get("paper_id", "") for result in results}
    flagged = sum(1 for result in results if result.get("quality_flags"))
    filters = [
        f"max_per_paper={args.max_per_paper}",
        f"query_mode={args.query_mode}",
        f"exclude_references={args.exclude_references}",
        f"exclude_tables={args.exclude_tables}",
        f"exclude_figures={args.exclude_figures}",
        f"exclude_supplement={args.exclude_supplement}",
        f"include_low_quality={args.include_low_quality}",
    ]
    if args.category:
        filters.append(f"category={args.category}")
    if args.paper_id:
        filters.append(f"paper_id={args.paper_id}")
    if args.min_score:
        filters.append(f"min_score={args.min_score}")
    if args.prefer_sections:
        filters.append(f"prefer_sections={','.join(args.prefer_sections)}")
    if args.exclude_sections:
        filters.append(f"exclude_sections={','.join(args.exclude_sections)}")
    if args.include_sections:
        filters.append(f"include_sections={','.join(args.include_sections)}")

    write("Summary:")
    write(f"- query: {query}")
    write(f"- results shown: {len(results)}")
    write(f"- unique papers: {len(unique_papers)}")
    write(f"- results with quality flags: {flagged}")
    write(f"- filters: {', '.join(filters)}")


def write_results_tsv(path: Path, results: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=OUT_FIELDS)
        writer.writeheader()
        for result in results:
            row = {field: result.get(field, "") for field in OUT_FIELDS}
            row["quality_flags"] = ";".join(result.get("quality_flags") or [])
            row["section_markers"] = ";".join(result.get("section_markers") or [])
            writer.writerow(row)
    print(f"Wrote search results to {path.relative_to(REPO_ROOT)}")


def format_page_range(page_start: object, page_end: object) -> str:
    if page_start is None and page_end is None:
        return "?"
    if page_start == page_end:
        return str(page_start)
    return f"{page_start}-{page_end}"


if __name__ == "__main__":
    raise SystemExit(main())
