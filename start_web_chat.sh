#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQUESTED_ENV="${ORIGINRAG_CONDA_ENV:-${PFEMPIRE_CONDA_ENV:-${HYPORAG_CONDA_ENV:-}}}"

cd "$ROOT_DIR"

if ! command -v conda >/dev/null 2>&1; then
  echo "conda was not found. Run ./setup.sh first, or install Miniconda/Miniforge." >&2
  exit 1
fi

eval "$(conda shell.bash hook)"
if [ -n "$REQUESTED_ENV" ]; then
  ENV_NAME="$REQUESTED_ENV"
elif conda env list | awk '{print $1}' | grep -qx "originrag"; then
  ENV_NAME="originrag"
elif conda env list | awk '{print $1}' | grep -qx "pfempire"; then
  ENV_NAME="pfempire"
elif conda env list | awk '{print $1}' | grep -qx "hyporag"; then
  ENV_NAME="hyporag"
else
  ENV_NAME="originrag"
fi
conda activate "$ENV_NAME"

exec python scripts/start_web_chat.py "$@"
