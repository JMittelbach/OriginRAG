#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODEL="${ORIGINRAG_QWEN_MODEL:-${PFEMPIRE_QWEN_MODEL:-qwen3:30b}}"
NUM_CTX="${ORIGINRAG_OLLAMA_NUM_CTX:-${PFEMPIRE_OLLAMA_NUM_CTX:-65536}}"
NUM_PREDICT="${ORIGINRAG_OLLAMA_NUM_PREDICT:-${PFEMPIRE_OLLAMA_NUM_PREDICT:-2200}}"
PULL=1

usage() {
  cat <<'EOF'
Usage:
  ./scripts/setup_local_qwen.sh [--model MODEL] [--large] [--no-pull]

Defaults to qwen3:30b, the preferred long-context RAG model for a 48 GB Apple
Silicon Mac. Use --balanced for qwen3:14b, or --dense for qwen3:32b.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --model)
      MODEL="${2:?Missing model after --model}"
      shift
      ;;
    --balanced)
      MODEL="qwen3:14b"
      NUM_CTX="${ORIGINRAG_OLLAMA_NUM_CTX:-${PFEMPIRE_OLLAMA_NUM_CTX:-16384}}"
      NUM_PREDICT="${ORIGINRAG_OLLAMA_NUM_PREDICT:-${PFEMPIRE_OLLAMA_NUM_PREDICT:-1800}}"
      ;;
    --dense|--large)
      MODEL="qwen3:32b"
      NUM_CTX="${ORIGINRAG_OLLAMA_NUM_CTX:-${PFEMPIRE_OLLAMA_NUM_CTX:-32768}}"
      ;;
    --long-context)
      MODEL="qwen3:30b"
      NUM_CTX="${ORIGINRAG_OLLAMA_NUM_CTX:-${PFEMPIRE_OLLAMA_NUM_CTX:-65536}}"
      ;;
    --no-pull)
      PULL=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if ! command -v ollama >/dev/null 2>&1; then
  cat >&2 <<'EOF'
ollama was not found.

Install it first, for example:
  brew install ollama

Then rerun:
  ./originrag setup-model
EOF
  exit 1
fi

if [ "$PULL" -eq 1 ] && ! ollama list >/dev/null 2>&1; then
  LOG_PATH="outputs/runtime/ollama.log"
  if ! mkdir -p outputs/runtime 2>/dev/null; then
    LOG_PATH="/tmp/originrag_ollama.log"
  fi
  echo "Starting local Ollama server in the background..."
  nohup ollama serve > "$LOG_PATH" 2>&1 &
  sleep 2
fi

if [ "$PULL" -eq 1 ]; then
  if ollama list 2>/dev/null | awk 'NR > 1 {print $1}' | grep -qx "$MODEL"; then
    echo "Model already installed: $MODEL"
  else
    echo "Pulling local Qwen model: $MODEL"
    ollama pull "$MODEL"
  fi
fi

set_env_key() {
  local key="$1"
  local value="$2"
  local tmp
  touch .env
  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { done = 0 }
    index($0, key "=") == 1 { print key "=" value; done = 1; next }
    { print }
    END { if (!done) print key "=" value }
  ' .env > "$tmp"
  mv "$tmp" .env
}

if ! grep -q '^ORIGINRAG_BIB_DIR=' .env 2>/dev/null; then
  if [ -d "$ROOT_DIR/bib" ]; then
    set_env_key "ORIGINRAG_BIB_DIR" "$ROOT_DIR/bib"
  elif [ -d ../bib ]; then
    set_env_key "ORIGINRAG_BIB_DIR" "$(cd ../bib && pwd)"
  else
    set_env_key "ORIGINRAG_BIB_DIR" "$ROOT_DIR/bib"
  fi
fi

set_env_key "ORIGINRAG_DOMAIN" "malaria"
set_env_key "ORIGINRAG_OLLAMA_BASE_URL" "http://localhost:11434"
set_env_key "ORIGINRAG_OLLAMA_MODEL" "$MODEL"
set_env_key "ORIGINRAG_QWEN_BASE_URL" "http://localhost:11434"
set_env_key "ORIGINRAG_QWEN_MODEL" "$MODEL"
set_env_key "ORIGINRAG_QUEUE_MODEL" "$MODEL"
set_env_key "ORIGINRAG_LLM_TIMEOUT_SECONDS" "1200"
set_env_key "ORIGINRAG_OLLAMA_NUM_CTX" "$NUM_CTX"
set_env_key "ORIGINRAG_OLLAMA_NUM_PREDICT" "$NUM_PREDICT"
set_env_key "ORIGINRAG_OLLAMA_CLI_FALLBACK" "1"
set_env_key "ORIGINRAG_OLLAMA_FORCE_CLI" "0"
set_env_key "ORIGINRAG_LLM_QUEUE_DIR" "outputs/llm_queue"

cat <<EOF

Local Qwen is configured.

Model:
  $MODEL

Start terminal chat:
  ./terminal

Start web chat:
  ./web
EOF
