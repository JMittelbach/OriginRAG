from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from originrag.analysis.faithfulness import load_and_check, write_grounding_report  # noqa: E402
from originrag.utils.io import write_json  # noqa: E402


def main() -> int:
    args = parse_args()
    answer_path = resolve_repo_path(args.answer)
    evidence_pack_path = resolve_repo_path(args.evidence_pack)
    out_md = resolve_repo_path(args.out) if args.out else answer_path.with_name("answer_grounding_report.md")
    out_tsv = (
        resolve_repo_path(args.issues_out)
        if args.issues_out
        else answer_path.with_name("answer_grounding_issues.tsv")
    )
    report = load_and_check(answer_path, evidence_pack_path)
    write_grounding_report(report, out_md, out_tsv)
    write_json(out_md.with_suffix(".json"), report)
    print(f"Wrote grounding report: {display_path(out_md)}")
    print(f"Wrote grounding issues: {display_path(out_tsv)}")
    print(f"Passed: {report['passed']}")
    return 0 if report["passed"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Heuristic citation/faithfulness check for an answer.")
    parser.add_argument("--answer", required=True)
    parser.add_argument("--evidence-pack", required=True)
    parser.add_argument("--out")
    parser.add_argument("--issues-out")
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
