from __future__ import annotations

import argparse
import gzip
import hashlib
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from originrag.paths import OriginRAGConfigError, get_bib_dir, load_project_env  # noqa: E402


DERIVED_TEXT_GLOBS = {
    "extracted_text": "data/extracted_text/**/*.json",
    "chunks": "data/chunks/**/*.jsonl",
}


def main() -> int:
    args = parse_args()
    load_project_env(REPO_ROOT / ".env")
    try:
        bib_dir = get_bib_dir().resolve()
    except OriginRAGConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if not bib_dir.exists():
        print(f"PFEMPIRE_BIB_DIR does not exist: {bib_dir}", file=sys.stderr)
        return 2

    output_path = resolve_repo_path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(path for path in bib_dir.rglob("*.pdf") if path.is_file())
    duplicate_groups = find_duplicate_pdfs(pdfs)
    largest_pdfs = sorted(pdfs, key=lambda path: path.stat().st_size, reverse=True)[: args.top]
    derived_stats = collect_derived_stats()

    report = render_report(
        bib_dir=bib_dir,
        pdfs=pdfs,
        largest_pdfs=largest_pdfs,
        duplicate_groups=duplicate_groups,
        derived_stats=derived_stats,
    )
    output_path.write_text(report, encoding="utf-8")
    print(f"Wrote storage audit to {relative_output(output_path)}")
    print(f"PDFs: {len(pdfs)} | PDF size: {format_bytes(sum(path.stat().st_size for path in pdfs))}")
    print(f"Exact duplicate groups: {len(duplicate_groups)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit OriginRAG corpus storage without modifying PDFs.")
    parser.add_argument("--out", default="outputs/curation/storage_report.md")
    parser.add_argument("--top", type=int, default=20, help="Number of largest PDFs to list.")
    return parser.parse_args()


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def relative_output(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def find_duplicate_pdfs(pdfs: list[Path]) -> list[list[Path]]:
    by_size: dict[int, list[Path]] = defaultdict(list)
    for path in pdfs:
        by_size[path.stat().st_size].append(path)

    by_hash: dict[str, list[Path]] = defaultdict(list)
    for same_size_paths in by_size.values():
        if len(same_size_paths) < 2:
            continue
        for path in same_size_paths:
            by_hash[sha256_file(path)].append(path)

    groups = [paths for paths in by_hash.values() if len(paths) > 1]
    return sorted(groups, key=lambda paths: (-paths[0].stat().st_size, paths[0].name.lower()))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def collect_derived_stats() -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = {}
    for label, glob_pattern in DERIVED_TEXT_GLOBS.items():
        files = [path for path in REPO_ROOT.glob(glob_pattern) if path.is_file()]
        raw_size = sum(path.stat().st_size for path in files)
        gzip_size = sum(gzip_size_for_file(path) for path in files)
        stats[label] = {
            "files": len(files),
            "raw_size": raw_size,
            "gzip_size": gzip_size,
            "savings": max(0, raw_size - gzip_size),
        }
    vector_dir = REPO_ROOT / "data/vector_index"
    vector_files = [path for path in vector_dir.rglob("*") if path.is_file()] if vector_dir.exists() else []
    stats["vector_index"] = {
        "files": len(vector_files),
        "raw_size": sum(path.stat().st_size for path in vector_files),
        "gzip_size": 0,
        "savings": 0,
    }
    return stats


def gzip_size_for_file(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), compresslevel=6))


def render_report(
    bib_dir: Path,
    pdfs: list[Path],
    largest_pdfs: list[Path],
    duplicate_groups: list[list[Path]],
    derived_stats: dict[str, dict[str, int]],
) -> str:
    pdf_size = sum(path.stat().st_size for path in pdfs)
    lines = [
        "# OriginRAG Storage Audit",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Bib directory: `{bib_dir}`",
        "",
        "## Summary",
        "",
        f"- PDFs found: {len(pdfs)}",
        f"- Total PDF size: {format_bytes(pdf_size)}",
        f"- Exact duplicate PDF groups: {len(duplicate_groups)}",
        "",
        "PDF files are not modified by this audit. Scientific PDFs are often already internally compressed, so the safer scaling strategy is deduplication plus compression of generated text/chunk artifacts.",
        "",
        "## Derived Artifact Compression Estimate",
        "",
        "| Artifact | Files | Current size | Estimated gzip size | Possible saving |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for label, stat in derived_stats.items():
        gzip_display = "n/a" if not stat["gzip_size"] else format_bytes(stat["gzip_size"])
        saving_display = "n/a" if not stat["gzip_size"] else format_bytes(stat["savings"])
        lines.append(
            f"| {label} | {stat['files']} | {format_bytes(stat['raw_size'])} | {gzip_display} | {saving_display} |"
        )

    lines.extend(["", "## Largest PDFs", ""])
    if largest_pdfs:
        for path in largest_pdfs:
            lines.append(f"- {format_bytes(path.stat().st_size)} `{safe_relative(path, bib_dir)}`")
    else:
        lines.append("- none")

    lines.extend(["", "## Exact Duplicate PDF Groups", ""])
    if duplicate_groups:
        for index, group in enumerate(duplicate_groups, start=1):
            lines.append(f"### Group {index} ({format_bytes(group[0].stat().st_size)})")
            for path in group:
                lines.append(f"- `{safe_relative(path, bib_dir)}`")
            lines.append("")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Recommended Storage Policy",
            "",
            "- Keep original PDFs once in `PFEMPIRE_BIB_DIR`; do not commit them to Git.",
            "- Do not recompress PDFs automatically unless a manual visual/OCR check confirms the file remains usable.",
            "- Prefer exact-hash duplicate reports before deleting anything.",
            "- Generated extracted text and chunk JSON/JSONL can later be stored as `.gz` files because they compress well and are reproducible from PDFs.",
            "- Embeddings and vector indexes should remain rebuildable cache artifacts, not permanent Git data.",
            "",
        ]
    )
    return "\n".join(lines)


def safe_relative(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return str(path)


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


if __name__ == "__main__":
    raise SystemExit(main())
