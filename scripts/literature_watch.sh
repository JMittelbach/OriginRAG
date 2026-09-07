#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DOWNLOAD=1
DISCOVER_ARGS=()

usage() {
  cat <<'EOF'
Usage:
  ./scripts/literature_watch.sh [--no-download] [discover args...]

Runs one literature discovery and metadata-refresh pass:
  1. refresh local_pdf_inventory.tsv before discovery
  2. discover recent open-access papers across configured sources
  3. optionally download ranked OA PDFs into PFEMPIRE_BIB_DIR
  4. refresh local_pdf_inventory.tsv after downloads
  5. refresh corpus_candidates.tsv and corpus_manifest.tsv

This does not download paywalled PDFs, monitor folders, or rebuild the
retrieval index. Recurring runs require an external scheduler.
Use ./originrag scrape-download N to download and index in one workflow.
Stop the web service before command-line corpus updates, then restart it.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-download)
      DOWNLOAD=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      DISCOVER_ARGS+=("$1")
      ;;
  esac
  shift
done

python scripts/inventory_bib_pdfs.py --out data/metadata/local_pdf_inventory.tsv

if [ "$DOWNLOAD" -eq 1 ]; then
  python scripts/discover_open_access_papers.py --download "${DISCOVER_ARGS[@]}"
else
  python scripts/discover_open_access_papers.py "${DISCOVER_ARGS[@]}"
fi

python scripts/inventory_bib_pdfs.py --out data/metadata/local_pdf_inventory.tsv

if [ -f data/metadata/literature_list.tsv ]; then
  python scripts/match_literature_to_pdfs.py \
    --literature data/metadata/literature_list.tsv \
    --inventory data/metadata/local_pdf_inventory.tsv \
    --out data/metadata/corpus_candidates.tsv

  python scripts/build_corpus_manifest.py \
    --candidates data/metadata/corpus_candidates.tsv \
    --out data/metadata/corpus_manifest.tsv
fi

python scripts/build_paper_registry.py --out data/metadata/paper_registry.tsv

echo
echo "Literature watch complete."
