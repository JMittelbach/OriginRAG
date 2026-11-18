#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="${ORIGINRAG_CONDA_ENV:-${PFEMPIRE_CONDA_ENV:-${HYPORAG_CONDA_ENV:-originrag}}}"
RECREATE=0
RUN_CHECKS=1
APP_PROFILE=full
RUNTIMES=1

usage() {
  cat <<'EOF'
Usage:
  ./setup.sh [--profile full|rag|write] [--apps-only] [--recreate] [--no-checks]

Installs the selected modules and records the profile in .env. Existing private
settings are preserved. Without --profile, installs the full suite, including
a local answer model and isolated PDF preview. --apps-only skips these runtimes.

  full   OriginRAG + Quill + Folio, with shared Library, Bookshelf and Reader
  rag    OriginRAG, with shared Library, Bookshelf and Reader
  write  Quill, with shared Library, Bookshelf and Reader; no LLM required

After setup, run ./web. No environment activation is needed.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile)
      if [ "$#" -lt 2 ]; then
        echo "--profile needs full, rag or write." >&2
        exit 2
      fi
      APP_PROFILE="$2"
      shift
      ;;
    --profile=*)
      APP_PROFILE="${1#*=}"
      ;;
    --apps-only)
      RUNTIMES=0
      ;;
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

case "$APP_PROFILE" in
  write) ENVIRONMENT_FILE=environment-write.yml ;;
  full|rag) ENVIRONMENT_FILE=environment.yml ;;
  *) echo "Choose an application profile: full, rag or write." >&2; exit 2 ;;
esac

if [[ ! "$ENV_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
  echo "Use a Conda environment name containing letters, digits, dots, underscores or hyphens." >&2
  exit 2
fi

if [ ! -f "$ENVIRONMENT_FILE" ]; then
  echo "Missing $ENVIRONMENT_FILE in $REPO_ROOT" >&2
  exit 1
fi

if ! command -v conda >/dev/null 2>&1; then
  bash scripts/bootstrap_miniforge.sh "$REPO_ROOT/runtime/miniforge"
  export PATH="$REPO_ROOT/runtime/miniforge/bin:$PATH"
fi
echo "Installing application profile: $APP_PROFILE"

env_exists() {
  conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"
}

if [ "$RECREATE" -eq 1 ] && env_exists; then
  if [ "$ENV_NAME" = base ] || [ "$ENV_NAME" = root ]; then
    echo "Refusing to recreate the base/root Conda environment." >&2
    exit 2
  fi
  echo "Removing existing Conda environment: $ENV_NAME"
  conda env remove -n "$ENV_NAME" -y
fi

if env_exists; then
  echo "Updating Conda environment: $ENV_NAME"
  conda env update -n "$ENV_NAME" -f "$ENVIRONMENT_FILE"
else
  echo "Creating Conda environment: $ENV_NAME"
  conda env create -n "$ENV_NAME" -f "$ENVIRONMENT_FILE"
fi

echo "Installing OriginRAG as an editable local package."
conda run -n "$ENV_NAME" python -m pip install -e ".[${APP_PROFILE}]" --no-deps
conda run -n "$ENV_NAME" python -m pip check
conda run -n "$ENV_NAME" python scripts/configure_app_profile.py --profile "$APP_PROFILE"
conda run -n "$ENV_NAME" python scripts/verify_workspace_install.py "$APP_PROFILE"

if [ "$APP_PROFILE" != write ]; then
  conda run -n "$ENV_NAME" python scripts/init_local_data.py
fi
conda run -n "$ENV_NAME" python scripts/install_git_hooks.py

if [ "$RUN_CHECKS" -eq 1 ]; then
  echo "Running environment checks."
  if [ "$APP_PROFILE" = write ]; then
    conda run -n "$ENV_NAME" python -c 'from originrag.writing_compile import compiler_status; import fitz, dotenv; print("Quill installed."); print(compiler_status()["message"])'
  else
    conda run -n "$ENV_NAME" python scripts/validate_metadata.py
    if [ ! -f data/indexes/pfempire_fts.sqlite ]; then
      echo "Full-text search starts when you add your own PDFs in the web app."
    fi
  fi
fi

if [ "$APP_PROFILE" = full ] && [ "$RUNTIMES" -eq 1 ]; then
  conda run --no-capture-output -n "$ENV_NAME" python scripts/setup_workspace_runtimes.py
fi
echo "Setup complete ($APP_PROFILE). Start with: ./web"
