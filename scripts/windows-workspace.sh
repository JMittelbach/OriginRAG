#!/usr/bin/env bash
set -euo pipefail

start_workspace() {
  local source_dir="$1" target="$2" repo=https://github.com/JMittelbach/OriginRAG.git python=""
  if [[ ! -e "$target" && ! -L "$target" ]]; then
    # Clone only committed source, never Windows-side papers, settings or environments.
    if [[ -d "$source_dir/.git" || -f "$source_dir/.git" ]]; then
      git clone --no-local -- "$source_dir" "$target"
      git -C "$target" remote set-url origin "$repo"
    else
      git clone -- "$repo" "$target"
    fi
  fi
  if [[ ! -f "$target/scripts/setup_workspace_runtimes.py" || ! -x "$target/web" || ! -x "$target/setup.sh" ]]; then
    echo "Cannot use $target as a full OriginRAG checkout. Nothing was replaced." >&2
    echo "If this is an older OriginRAG clone, run git pull --ff-only there in Ubuntu, then retry." >&2
    return 1
  fi
  cd "$target"
  mkdir -p runtime
  exec 9>runtime/windows-launch.lock
  if ! flock -n 9; then
    echo "OriginRAG is already starting or running. Use the address printed in its first window."
    return 0
  fi
  if [[ -s runtime/setup-python ]]; then IFS= read -r python < runtime/setup-python; fi
  if [[ ! -f runtime/windows-full-ready || ! -x "$python" || ! -f .env ]] ||
     [[ "$(env -u ORIGINRAG_APP_PROFILE "$python" scripts/configure_app_profile.py --read)" != full ]]; then
    rm -f -- runtime/windows-full-ready
    ./setup.sh --profile full
    touch runtime/windows-full-ready
  fi
  if command -v rundll32.exe >/dev/null; then
    export BROWSER='rundll32.exe url.dll,FileProtocolHandler %s &'
  fi
  echo "Keep this window open while using OriginRAG. Press Ctrl+C to stop."
  ORIGINRAG_WEB_OPEN=1 exec ./web --host 127.0.0.1 --strict-port
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  case "$(uname -r)" in
    *[Mm]icrosoft*[Ww][Ss][Ll]2*) ;;
    *) echo "Use Ubuntu on WSL 2. Check the distribution version in PowerShell: wsl -l -v" >&2; exit 1 ;;
  esac
  if [[ "$(id -u)" == 0 ]]; then
    echo "Open Ubuntu and set up a regular user first. Do not run the workspace as root." >&2
    exit 1
  fi
  for tool in git curl flock; do
    if ! command -v "$tool" >/dev/null; then
      echo "Missing $tool in Ubuntu. Install the prerequisites there: sudo apt install git curl util-linux" >&2
      exit 1
    fi
  done
  start_workspace "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" "$HOME/OriginRAG"
fi
