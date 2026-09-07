#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/sync_bib_rsync.sh pull REMOTE_BIB_PATH [--dry-run]
  ./scripts/sync_bib_rsync.sh push REMOTE_BIB_PATH [--dry-run]

Examples:
  ./originrag sync-bib pull USER@SERVER:/path/to/bib
  ./originrag sync-bib push USER@SERVER:/path/to/bib

This uses rsync without --delete, so it copies new/changed PDFs but does not
delete files from either machine.
EOF
}

read_env_value() {
  local key="$1"
  if [ -f .env ]; then
    awk -F= -v key="$key" '$1 == key {print substr($0, length(key) + 2); exit}' .env
  fi
}

mode="${1:-}"
remote="${2:-}"
dry_run=0

if [ -z "$mode" ] || [ "$mode" = "-h" ] || [ "$mode" = "--help" ]; then
  usage
  exit 0
fi
shift || true
shift || true

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      dry_run=1
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [ "$mode" != "pull" ] && [ "$mode" != "push" ]; then
  echo "Mode must be pull or push." >&2
  usage >&2
  exit 2
fi
if [ -z "$remote" ]; then
  echo "Missing REMOTE_BIB_PATH." >&2
  usage >&2
  exit 2
fi

local_bib="${ORIGINRAG_BIB_DIR:-${PFEMPIRE_BIB_DIR:-}}"
if [ -z "$local_bib" ]; then
  local_bib="$(read_env_value ORIGINRAG_BIB_DIR || true)"
fi
if [ -z "$local_bib" ]; then
  local_bib="$(read_env_value PFEMPIRE_BIB_DIR || true)"
fi
local_bib="${local_bib:-$ROOT_DIR/bib}"
mkdir -p "$local_bib"

rsync_args=(-avh --progress)
if [ "$dry_run" -eq 1 ]; then
  rsync_args+=(--dry-run)
fi

if [ "$mode" = "pull" ]; then
  echo "Pulling PDFs into: $local_bib"
  rsync "${rsync_args[@]}" "${remote%/}/" "${local_bib%/}/"
else
  echo "Pushing PDFs from: $local_bib"
  rsync "${rsync_args[@]}" "${local_bib%/}/" "${remote%/}/"
fi
