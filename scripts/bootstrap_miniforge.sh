#!/usr/bin/env bash
set -euo pipefail
prefix="${1:?missing installation directory}"
[ ! -x "$prefix/bin/conda" ] || exit 0
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) platform=Linux-x86_64; digest=bbaf16b3897da24c4be50315934ba74848f617b4495982841d6a0a60e0edbbdf ;;
  Linux-aarch64) platform=Linux-aarch64; digest=1adf18b530e4ffb00886ce6d5bd2eb1fa6311d2f14709b13bd154d8cd98d94c8 ;;
  Darwin-arm64) platform=MacOSX-arm64; digest=fb567612b76f4ef46de326f04425dbe381b6afc6a66c545f5043255a5ef34258 ;;
  Darwin-x86_64) platform=MacOSX-x86_64; digest=30e82ce68875fc50b917a3a541ea0516272866584187f7ea980ce8eaf715807d ;;
  *) echo "Unsupported platform for automatic Miniforge setup. Install Conda first." >&2; exit 1 ;;
esac
mkdir -p "$(dirname "$prefix")"
installer="$(mktemp)"
trap 'rm -f "$installer"' EXIT
echo "Installing a private Miniforge under $prefix (no sudo or shell-profile changes)."
curl --fail --location --retry 2 --proto '=https' --proto-redir '=https' \
  "https://github.com/conda-forge/miniforge/releases/download/26.9.4-2/Miniforge3-$platform.sh" -o "$installer"
if command -v sha256sum >/dev/null; then
  actual="$(sha256sum "$installer")"
else
  actual="$(shasum -a 256 "$installer")"
fi
[ "${actual%% *}" = "$digest" ] || { echo "Miniforge checksum mismatch." >&2; exit 1; }
bash "$installer" -b -p "$prefix"
