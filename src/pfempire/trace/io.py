from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import yaml


def read_tsv(path: Path) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        try:
            header = next(reader)
        except StopIteration:
            return []
        duplicates = sorted({name for name in header if header.count(name) > 1})
        if duplicates:
            raise ValueError(
                f"duplicate TSV headers in {path}: {', '.join(duplicates)}"
            )
        rows = []
        for line_number, values in enumerate(reader, start=2):
            if len(values) != len(header):
                raise ValueError(
                    f"TSV row width mismatch in {path}:{line_number}: "
                    f"expected {len(header)}, found {len(values)}"
                )
            rows.append(dict(zip(header, values, strict=True)))
        return rows


def write_tsv(path: Path, rows: Iterable[dict], fieldnames: list[str] | None = None) -> None:
    rows = list(rows)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(fieldnames or [])
    if not fields:
        seen = set()
        for row in rows:
            for field in row:
                if field not in seen:
                    fields.append(field)
                    seen.add(field)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=fields,
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
            default=str,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        rows = []
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL in {path}:{line_number}: {exc.msg}") from exc
        return rows


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, default=str, allow_nan=False) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def read_yaml(path: Path) -> dict:
    return dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def config_hash(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()[:16]
