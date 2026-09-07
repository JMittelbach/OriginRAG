from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from originrag.claims.audit import audit_claim_database, format_claim_audit_markdown  # noqa: E402
from originrag.claims.store import DEFAULT_DB_PATH  # noqa: E402
from originrag.utils.io import write_json  # noqa: E402


def main() -> int:
    args = parse_args()
    db_path = resolve_repo_path(args.db)
    out_md = resolve_repo_path(args.out_md)
    out_json = resolve_repo_path(args.out_json)
    audit = audit_claim_database(db_path)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(format_claim_audit_markdown(audit), encoding="utf-8")
    write_json(out_json, audit)
    print(f"Wrote claim audit report to {display_path(out_md)}")
    print(f"Wrote claim audit JSON to {display_path(out_json)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit local claim database quality and coverage.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH.relative_to(REPO_ROOT)))
    parser.add_argument("--out-md", default="outputs/claim_eval/claim_database_audit.md")
    parser.add_argument("--out-json", default="outputs/claim_eval/claim_database_audit.json")
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
