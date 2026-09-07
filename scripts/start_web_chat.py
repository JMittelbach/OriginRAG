from __future__ import annotations

import os
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import web_chat  # noqa: E402


def main() -> int:
    defaults = [
        "--backend",
        "qwen",
        "--preset",
        "pro",
        "--planner-backend",
        "rule_based",
        "--task",
        "auto",
        "--repair-attempts",
        "1",
    ]
    if environment_flag("ORIGINRAG_WEB_OPEN", default=True):
        defaults.append("--open")
    return web_chat.main([*defaults, *sys.argv[1:]])


def environment_flag(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None and name.startswith("ORIGINRAG_"):
        value = os.environ.get(f"PFEMPIRE_{name.removeprefix('ORIGINRAG_')}")
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    raise SystemExit(main())
