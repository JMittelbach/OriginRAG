from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return an empty dict for empty files."""
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

