#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'EOF'
Usage:
  ./originrag bib
      Refresh inventory, registry, papers.tsv, statuses, and bib/keys exports.

  ./originrag scrape [discover args...]
      Search candidate papers only. No PDFs are downloaded.

  ./originrag scrape-download [N] [discover args...]
      Download up to N DOI-backed open-access PDFs, then refresh and index.
      Default N: 50.

  ./originrag prepare-corpus
      Extract available PDFs, build chunks, and rebuild the retrieval index.

Examples:
  ./originrag scrape --sources all --since-days 3650 --max-per-query 100
  ./originrag scrape-download 50 --sources all --since-days 3650 --download-strategy diverse
EOF
}

refresh_library() {
  python scripts/inventory_bib_pdfs.py --out data/metadata/local_pdf_inventory.tsv
  python scripts/build_paper_registry.py --discovery data/metadata/open_access_discovery_10y.tsv --out data/metadata/paper_registry.tsv
  python scripts/sync_registry_to_papers.py --registry data/metadata/paper_registry.tsv --papers data/metadata/papers.tsv
  python scripts/update_fulltext_status_from_artifacts.py --papers data/metadata/papers.tsv --extracted-dir data/extracted_text --chunks-dir data/chunks
  python scripts/organize_bib_library.py --categorize


  python scripts/inventory_bib_pdfs.py --out data/metadata/local_pdf_inventory.tsv
  python scripts/build_paper_registry.py --discovery data/metadata/open_access_discovery_10y.tsv --out data/metadata/paper_registry.tsv
  python scripts/sync_registry_to_papers.py --registry data/metadata/paper_registry.tsv --papers data/metadata/papers.tsv
  python scripts/report_discovery_run.py \
    --discovery data/metadata/open_access_discovery_10y.tsv \
    --registry data/metadata/paper_registry.tsv \
    --out outputs/curation/discovery_10y_report.md \
    --no-doi-out data/metadata/no_doi_review.tsv
  python scripts/audit_corpus_metadata.py
}

extract_and_chunk() {
  python scripts/ingest_pdfs.py --only-status pdf_available --limit 0
  python scripts/build_chunks.py --only-status pdf_available --limit 0
  python scripts/update_fulltext_status_from_artifacts.py \
    --papers data/metadata/papers.tsv \
    --extracted-dir data/extracted_text \
    --chunks-dir data/chunks
}

prepare_corpus() {
  extract_and_chunk
  python scripts/build_retrieval_index.py
}

discover_only() {
  python scripts/inventory_bib_pdfs.py --out data/metadata/local_pdf_inventory.tsv
  python scripts/discover_open_access_papers.py \
    --out data/metadata/open_access_discovery_10y.tsv \
    --inventory data/metadata/local_pdf_inventory.tsv \
    --sources all \
    --since-days 3650 \
    --max-per-query 100 \
    "$@"
  refresh_library
}

download_and_ingest() {
  local limit="${1:-50}"
  if [[ "$limit" =~ ^[0-9]+$ ]]; then
    shift || true
  else
    limit=50
  fi
  python scripts/inventory_bib_pdfs.py --out data/metadata/local_pdf_inventory.tsv
  python scripts/discover_open_access_papers.py \
    --out data/metadata/open_access_discovery_10y.tsv \
    --inventory data/metadata/local_pdf_inventory.tsv \
    --sources all \
    --since-days 3650 \
    --max-per-query 100 \
    --download \
    --download-min-tier candidate \
    --download-limit "$limit" \
    --download-strategy diverse \
    "$@"
  refresh_library
  extract_and_chunk
  python scripts/organize_bib_library.py --categorize
  python scripts/inventory_bib_pdfs.py --out data/metadata/local_pdf_inventory.tsv
  python scripts/build_paper_registry.py --discovery data/metadata/open_access_discovery_10y.tsv --out data/metadata/paper_registry.tsv
  python scripts/sync_registry_to_papers.py --registry data/metadata/paper_registry.tsv --papers data/metadata/papers.tsv
  python scripts/update_fulltext_status_from_artifacts.py --papers data/metadata/papers.tsv --extracted-dir data/extracted_text --chunks-dir data/chunks
  python scripts/organize_bib_library.py --categorize
  python scripts/build_retrieval_index.py
}

cmd="${1:-refresh}"
shift || true

case "$cmd" in
  refresh|bib)
    refresh_library "$@"
    ;;
  discover|scrape)
    discover_only "$@"
    ;;
  download|scrape-download)
    download_and_ingest "$@"
    ;;
  prepare|prepare-corpus|index)
    prepare_corpus "$@"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown bib workflow command: $cmd" >&2
    usage >&2
    exit 2
    ;;
esac
