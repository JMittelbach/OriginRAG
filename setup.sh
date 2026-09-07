#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="${ORIGINRAG_CONDA_ENV:-${PFEMPIRE_CONDA_ENV:-${HYPORAG_CONDA_ENV:-originrag}}}"
RECREATE=0
RUN_CHECKS=1

usage() {
  cat <<'EOF'
Usage:
  ./setup.sh [--recreate] [--no-checks]

Creates or updates the Conda environment from environment.yml, installs OriginRAG
as an editable local package, and prepares a local .env file if one is missing.

After setup, activate the environment with:
  conda activate originrag
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --recreate)
      RECREATE=1
      ;;
    --no-checks)
      RUN_CHECKS=0
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

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if ! command -v conda >/dev/null 2>&1; then
  echo "Conda was not found on PATH. Install Miniforge/Mambaforge or initialize conda first." >&2
  exit 1
fi

if [ ! -f environment.yml ]; then
  echo "Missing environment.yml in $REPO_ROOT" >&2
  exit 1
fi

env_exists() {
  conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"
}

if [ "$RECREATE" -eq 1 ] && env_exists; then
  echo "Removing existing Conda environment: $ENV_NAME"
  conda env remove -n "$ENV_NAME" -y
fi

if env_exists; then
  echo "Updating Conda environment: $ENV_NAME"
  conda env update -n "$ENV_NAME" -f environment.yml --prune
else
  echo "Creating Conda environment: $ENV_NAME"
  conda env create -f environment.yml
fi

echo "Installing OriginRAG as an editable local package."
conda run -n "$ENV_NAME" python -m pip install -e . --no-deps

if [ ! -f .env ]; then
  if [ -d ../bib ]; then
    BIB_DIR="$(cd ../bib && pwd)"
    {
      echo "ORIGINRAG_BIB_DIR=$BIB_DIR"
      echo "ORIGINRAG_DOMAIN=malaria"
      echo "ORIGINRAG_OLLAMA_BASE_URL=http://localhost:11434"
      echo "ORIGINRAG_OLLAMA_MODEL=qwen3:30b"
      echo "ORIGINRAG_QWEN_BASE_URL=http://localhost:11434"
      echo "ORIGINRAG_QWEN_MODEL=qwen3:30b"
      echo "ORIGINRAG_QUEUE_MODEL=qwen3:30b"
      echo "ORIGINRAG_LLM_TIMEOUT_SECONDS=1200"
      echo "ORIGINRAG_OLLAMA_NUM_CTX=65536"
      echo "ORIGINRAG_OLLAMA_NUM_PREDICT=2200"
    } > .env
    echo "Created .env using sibling bib directory: $BIB_DIR"
  else
    cp .env.example .env
    echo "Created .env from .env.example. Edit ORIGINRAG_BIB_DIR before ingesting PDFs."
  fi
fi

conda run -n "$ENV_NAME" python scripts/init_local_data.py
conda run -n "$ENV_NAME" python scripts/install_git_hooks.py

if [ "$RUN_CHECKS" -eq 1 ]; then
  echo "Running environment checks."
  conda run -n "$ENV_NAME" python scripts/check_environment.py
  conda run -n "$ENV_NAME" python scripts/validate_metadata.py
fi

cat <<EOF

Setup complete.

Activate it in this terminal with:
  conda activate $ENV_NAME

Then run, for example:
  ./originrag setup-model
  ./originrag doctor
  ./originrag search-papers "EPCR-binding PfEMP1"
  ./web
EOF
