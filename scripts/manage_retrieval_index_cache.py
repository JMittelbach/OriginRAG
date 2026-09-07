from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.retrieval.index_cache import (  # noqa: E402
    DEFAULT_PRUNE_MIN_AGE_SECONDS,
    DEFAULT_RETAIN_GENERATIONS,
    configured_cache_path,
    prime_read_index_cache,
    prune_read_bundle_cache,
    resolve_read_bundle,
    verify_read_bundle_cache,
)


DEFAULT_SOURCE_INDEX = REPO_ROOT / "data/indexes/pfempire_fts.sqlite"
DEFAULT_CHUNKS_DIR = REPO_ROOT / "data/chunks"
DEFAULT_PAPERS_TSV = REPO_ROOT / "data/metadata/papers.tsv"
CACHE_ENVIRONMENT_VARIABLE = "PFEMPIRE_RETRIEVAL_INDEX_READ_CACHE"


def add_index_arguments(parser: argparse.ArgumentParser, *, include_corpus: bool) -> None:
    parser.add_argument(
        "--source-index",
        type=Path,
        default=DEFAULT_SOURCE_INDEX,
        help="Canonical writable retrieval index (default: repository index).",
    )
    if include_corpus:
        parser.add_argument(
            "--chunks-dir",
            type=Path,
            default=DEFAULT_CHUNKS_DIR,
            help="Canonical chunk directory (default: repository chunks).",
        )
        parser.add_argument(
            "--papers-tsv",
            type=Path,
            default=DEFAULT_PAPERS_TSV,
            help="Canonical paper metadata table (default: repository papers.tsv).",
        )
    parser.add_argument(
        "--cache-path",
        type=Path,
        help=(
            "Node-local retrieval-bundle anchor path. When omitted, use "
            "PFEMPIRE_RETRIEVAL_INDEX_READ_CACHE."
        ),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prime, resolve startup readiness, strongly verify, or prune the "
            "node-local retrieval-bundle cache."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    prime = commands.add_parser(
        "prime",
        help="Atomically publish and validate a complete local read generation.",
    )
    add_index_arguments(prime, include_corpus=True)

    check = commands.add_parser(
        "check",
        help="Strongly hash and validate the configured generation and canonical corpus.",
    )
    add_index_arguments(check, include_corpus=True)

    ready = commands.add_parser(
        "ready",
        help="Perform bounded fail-closed startup validation of the active generation.",
    )
    add_index_arguments(ready, include_corpus=True)

    prune = commands.add_parser(
        "prune",
        help="Delete old inactive generations only when no reader process is active.",
    )
    add_index_arguments(prune, include_corpus=False)
    prune.add_argument(
        "--retain-generations",
        type=int,
        default=DEFAULT_RETAIN_GENERATIONS,
    )
    prune.add_argument(
        "--min-age-seconds",
        type=int,
        default=DEFAULT_PRUNE_MIN_AGE_SECONDS,
    )
    prune.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def compact_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def run(args: argparse.Namespace) -> dict[str, object]:
    source_index = args.source_index.expanduser()
    cache_index = configured_cache_path(cache_path=args.cache_path)
    if args.command == "prime":
        prime_read_index_cache(
            source_index,
            args.chunks_dir.expanduser(),
            args.papers_tsv.expanduser(),
            cache_path=args.cache_path,
        )
        bundle = resolve_read_bundle(
            source_index,
            args.chunks_dir.expanduser(),
            args.papers_tsv.expanduser(),
            cache_path=args.cache_path,
        )
    elif args.command == "check":
        bundle = verify_read_bundle_cache(
            source_index,
            args.chunks_dir.expanduser(),
            args.papers_tsv.expanduser(),
            cache_path=args.cache_path,
        )
    elif args.command == "ready":
        bundle = resolve_read_bundle(
            source_index,
            args.chunks_dir.expanduser(),
            args.papers_tsv.expanduser(),
            cache_path=args.cache_path,
        )
    elif args.command == "prune":
        result = prune_read_bundle_cache(
            cache_path=args.cache_path,
            retain_generations=args.retain_generations,
            min_age_seconds=args.min_age_seconds,
            dry_run=args.dry_run,
        )
        return {
            "cache_anchor": str(cache_index) if cache_index is not None else None,
            "command": args.command,
            "ok": True,
            **result,
        }
    else:
        raise ValueError(f"Unsupported command: {args.command}")
    return {
        "cache_enabled": cache_index is not None,
        "cache_anchor": str(cache_index) if cache_index is not None else None,
        "command": args.command,
        "ok": True,
        "ready": True,
        "generation": bundle.cache_generation,
        "read_chunks_dir": (None if bundle.chunks_dir is None else str(bundle.chunks_dir)),
        "read_index": str(bundle.index_path),
        "read_papers_tsv": (None if bundle.papers_tsv is None else str(bundle.papers_tsv)),
        "source_index": str(source_index),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except Exception as exc:
        configured_value = args.cache_path or os.environ.get(CACHE_ENVIRONMENT_VARIABLE)
        print(
            compact_json(
                {
                    "cache_anchor": (
                        str(Path(configured_value).expanduser()) if configured_value else None
                    ),
                    "command": args.command,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "ok": False,
                    "ready": False,
                    "source_index": str(args.source_index.expanduser()),
                }
            ),
            file=sys.stderr,
        )
        return 2
    print(compact_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
