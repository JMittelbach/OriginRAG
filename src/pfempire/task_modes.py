from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASK_MODES_PATH = REPO_ROOT / "config/task_modes.yaml"


class TaskModeConfigError(ValueError):
    """Raised when task-mode configuration is missing or invalid."""


def load_task_modes(path: Path = DEFAULT_TASK_MODES_PATH) -> dict[str, dict[str, Any]]:
    """Load task modes keyed by mode name."""
    return dict(_load_task_modes_cached(str(Path(path).resolve())))


@lru_cache(maxsize=8)
def _load_task_modes_cached(path_text: str) -> dict[str, dict[str, Any]]:
    path = Path(path_text)
    if not path.exists():
        raise TaskModeConfigError(f"Task mode config not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    task_modes = payload.get("task_modes")
    if not isinstance(task_modes, dict) or not task_modes:
        raise TaskModeConfigError(f"No task_modes mapping found in {path}")

    normalized: dict[str, dict[str, Any]] = {}
    for name, config in task_modes.items():
        if not isinstance(config, dict):
            raise TaskModeConfigError(f"Task mode {name!r} must be a mapping")
        normalized[str(name)] = _validate_task_mode(str(name), config)
    return normalized


def get_task_mode(name: str, path: Path = DEFAULT_TASK_MODES_PATH) -> dict[str, Any]:
    """Return one task-mode config by name."""
    task_modes = load_task_modes(path)
    if name not in task_modes:
        available = ", ".join(sorted(task_modes))
        raise TaskModeConfigError(f"Unknown task mode {name!r}. Available: {available}")
    return dict(task_modes[name])


def _validate_task_mode(name: str, config: dict[str, Any]) -> dict[str, Any]:
    required = ["description", "preferred_sections", "excluded_sections", "max_chunks", "max_per_paper", "output_schema"]
    missing = [field for field in required if field not in config]
    if missing:
        raise TaskModeConfigError(f"Task mode {name!r} is missing: {', '.join(missing)}")

    preferred_sections = _string_list(config["preferred_sections"])
    excluded_sections = _string_list(config["excluded_sections"])
    max_chunks = _positive_int(config["max_chunks"], field="max_chunks", mode=name)
    max_per_paper = _positive_int(config["max_per_paper"], field="max_per_paper", mode=name)
    output_schema = str(config["output_schema"]).strip()
    description = str(config["description"]).strip()
    if not description or not output_schema:
        raise TaskModeConfigError(f"Task mode {name!r} has blank description or output_schema")

    return {
        "name": name,
        "description": description,
        "preferred_sections": preferred_sections,
        "excluded_sections": excluded_sections,
        "max_chunks": max_chunks,
        "max_per_paper": max_per_paper,
        "output_schema": output_schema,
    }


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise TaskModeConfigError("Expected a string or list of strings")
    return [str(item).strip() for item in items if str(item).strip()]


def _positive_int(value: Any, *, field: str, mode: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TaskModeConfigError(f"Task mode {mode!r} field {field} must be an integer") from exc
    if parsed <= 0:
        raise TaskModeConfigError(f"Task mode {mode!r} field {field} must be positive")
    return parsed
