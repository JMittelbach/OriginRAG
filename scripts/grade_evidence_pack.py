from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.analysis.evidence_quality import score_evidence_pack  # noqa: E402


def main() -> int:
    args = parse_args()
    pack_path = resolve_repo_path(args.evidence_pack)
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    grade = score_evidence_pack(pack, expected_sources=args.expected_source or None)
    if args.out:
        out_path = resolve_repo_path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(grade, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote evidence grade to {display_path(out_path)}")
    else:
        print(json.dumps(grade, indent=2, ensure_ascii=False))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grade an adaptive retrieval evidence pack.")
    parser.add_argument("--evidence-pack", required=True)
    parser.add_argument("--expected-source", action="append")
    parser.add_argument("--out")
    return parser.parse_args()


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
