#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="${ORIGINRAG_CONDA_ENV:-${PFEMPIRE_CONDA_ENV:-${HYPORAG_CONDA_ENV:-originrag}}}"
RECREATE=0
RUN_CHECKS=1
APP_PROFILE=""

usage() {
  cat <<'EOF'
Usage:
  ./setup.sh [--profile full|rag|write] [--recreate] [--no-checks]

Installs the selected modules and records the profile in .env. Existing private
settings are preserved. Without --profile, the saved profile is reused (full
for a new installation). No language model is downloaded by setup.

  full   OriginRAG + Quill, with shared Library, Bookshelf and Reader
  rag    OriginRAG, with shared Library, Bookshelf and Reader
  write  Quill, with shared Library, Bookshelf and Reader; no LLM required

After setup, activate the environment with:
  conda activate originrag
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

if [ -n "$APP_PROFILE" ]; then
  APP_PROFILE="$(conda run -n base python scripts/configure_app_profile.py --read --profile "$APP_PROFILE")"
else
  APP_PROFILE="$(conda run -n base python scripts/configure_app_profile.py --read)"
fi
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
conda run -n "$ENV_NAME" python scripts/configure_app_profile.py --profile "$APP_PROFILE"

if [ "$APP_PROFILE" != write ]; then
  conda run -n "$ENV_NAME" python scripts/init_local_data.py
fi
conda run -n "$ENV_NAME" python scripts/install_git_hooks.py

if [ "$RUN_CHECKS" -eq 1 ]; then
  echo "Running environment checks."
  if [ "$APP_PROFILE" = write ]; then
    conda run -n "$ENV_NAME" python -c 'from originrag.writing_compile import compiler_status; import fitz, dotenv; print("Quill installed."); print(compiler_status()["message"])'
  else
    conda run -n "$ENV_NAME" python scripts/check_environment.py
    conda run -n "$ENV_NAME" python scripts/validate_metadata.py
  fi
fi

cat <<EOF

Setup complete.

Activate it in this terminal with:
  conda activate $ENV_NAME

Open the selected workspace:
  ./web

For generated answers (full/rag only), configure your model separately:
  ./originrag setup-model

Quill PDF compilation needs an isolated TeX compiler. See docs/writing.md.
EOF
