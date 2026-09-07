from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.retrieval.section_detection import carries_forward_section, detect_section_for_chunk  # noqa: E402


def main() -> int:
    args = parse_args()
    chunks_dir = resolve_repo_path(args.chunks_dir)
    out_dir = resolve_repo_path(args.out_dir)

    if args.in_place:
        out_dir = chunks_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(chunks_dir.glob("*.jsonl"))
    if not files:
        print(f"No chunk JSONL files found in {chunks_dir}")
        return 1

    total_chunks = 0
    for input_path in files:
        output_path = out_dir / input_path.name
        chunks = annotate_file(input_path)
        write_jsonl(output_path, chunks)
        total_chunks += len(chunks)
        print(f"ok: {input_path.name}: {len(chunks)} chunks")

    print()
    print(f"Section annotation complete: {len(files)} files, {total_chunks} chunks.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Annotate existing OriginRAG chunks with section labels.")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--out-dir", default="data/chunks")
    parser.add_argument("--in-place", action="store_true")
    return parser.parse_args()


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def annotate_file(path: Path) -> list[dict]:
    chunks: list[dict] = []
    previous_section: str | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            chunk = json.loads(line)
            text = str(chunk.get("text") or "")
            detected = detect_section_for_chunk(text, previous_section=previous_section)
            section = detected["section"]
            if carries_forward_section(section):
                previous_section = section
            chunk["section"] = section
            chunk["section_confidence"] = detected["section_confidence"]
            chunk["section_markers"] = detected["section_markers"]
            chunks.append(chunk)
    return chunks


def write_jsonl(path: Path, chunks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False))
            handle.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
