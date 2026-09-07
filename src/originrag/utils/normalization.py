from __future__ import annotations

import json
import math
from collections.abc import Iterable


def normalize_string_list(value: object) -> list[str]:
    """Normalize list-like JSON/TSV values without splitting strings into characters."""

    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, list):
                return _unique_strings(decoded)
        delimiter = ";" if ";" in text else "," if "," in text else None
        return _unique_strings(text.split(delimiter) if delimiter else [text])
    if isinstance(value, dict):
        return _unique_strings(value.keys())
    if isinstance(value, Iterable):
        return _unique_strings(value)
    return _unique_strings([value])


def finite_float(value: object, default: float = 0.0) -> float:
    """Return a finite float or a finite fallback for malformed external values."""

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    if not math.isfinite(parsed):
        parsed = float(default)
    if not math.isfinite(parsed):
        raise ValueError("finite_float requires a finite default")
    return parsed


def is_finite_number(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def safe_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _unique_strings(values: Iterable[object]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
