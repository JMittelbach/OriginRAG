from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import tempfile
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


def parse_args() -> argparse.Namespace:
    web_data = Path(
        os.environ.get(
            "ORIGINRAG_WEB_DATA_DIR",
            os.environ.get("PFEMPIRE_WEB_DATA_DIR", "outputs/web_chat_sessions"),
        )
    )
    export_root = Path(
        os.environ.get(
            "ORIGINRAG_FEEDBACK_EXPORT_DIR",
            os.environ.get("PFEMPIRE_FEEDBACK_EXPORT_DIR", web_data.parent / "exports"),
        )
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    parser = argparse.ArgumentParser(
        description="Export pseudonymous OriginRAG web turns for internal product evaluation."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=web_data / "admin_review" / "anonymous_turns.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=export_root / f"originrag_feedback_{stamp}.zip",
    )
    return parser.parse_args()


def read_events(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"No central feedback log exists yet: {path}")
    events: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on feedback line {line_number}: {exc}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"Feedback line {line_number} is not a JSON object.")
            events.append(event)
    return events


def create_export(source: Path, output: Path) -> dict:
    events = read_events(source)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing export: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = summarize(events)
    with tempfile.TemporaryDirectory(prefix="pfempire-feedback-") as tmp:
        root = Path(tmp)
        jsonl_path = root / "turns.jsonl"
        csv_path = root / "turns.csv"
        summary_path = root / "summary.json"
        write_jsonl(jsonl_path, events)
        write_csv(csv_path, events)
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        payloads = [jsonl_path, csv_path, summary_path]
        manifest = {
            "schema_version": "pfempire_feedback_export_v1",
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "privacy": (
                "Application-level pseudonymous export without account IDs, usernames, "
                "passwords, authentication tokens, or user storage paths. User-entered free "
                "text may still contain identifying information."
            ),
            "files": {
                path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
                for path in payloads
            },
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in [*payloads, manifest_path]:
                archive.write(path, arcname=path.name)
    try:
        output.chmod(0o600)
    except OSError:
        pass
    return {"output": str(output), **summary}


def summarize(events: list[dict]) -> dict:
    tasks = Counter(str(event.get("task") or "unknown") for event in events)
    errors = sum(bool(event.get("error")) for event in events)
    conversations = {
        str(event.get("conversation_id"))
        for event in events
        if event.get("conversation_id")
    }
    durations = [
        float(event["elapsed_seconds"])
        for event in events
        if isinstance(event.get("elapsed_seconds"), (int, float))
    ]
    return {
        "schema_version": "pfempire_feedback_summary_v1",
        "turns": len(events),
        "conversations": len(conversations),
        "turns_with_errors": errors,
        "turns_with_duration": len(durations),
        "mean_elapsed_seconds": (
            round(statistics.fmean(durations), 3) if durations else None
        ),
        "median_elapsed_seconds": (
            round(statistics.median(durations), 3) if durations else None
        ),
        "tasks": dict(sorted(tasks.items())),
    }


def write_jsonl(path: Path, events: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, events: list[dict]) -> None:
    fields = [
        "recorded_at",
        "conversation_id",
        "task",
        "evidence_grade",
        "evidence_score",
        "grounding_passed",
        "elapsed_seconds",
        "error",
        "question",
        "answer",
        "source_dois",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for event in events:
            writer.writerow(
                {
                    **{field: event.get(field, "") for field in fields if field != "source_dois"},
                    "source_dois": ";".join(
                        str(source.get("doi") or "")
                        for source in event.get("sources") or []
                        if isinstance(source, dict) and source.get("doi")
                    ),
                }
            )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    result = create_export(args.source.expanduser(), args.output.expanduser())
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
