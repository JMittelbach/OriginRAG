"""Refresh the offline chat renderer from the existing Folio npm lock (run npm ci there first)."""

import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "assets/folio/editor-build"
OUT = ROOT / "assets/vendor/markdown-it"


def main():
    lock = json.loads((BUILD / "package-lock.json").read_text())["packages"]
    notices = []
    for package in ("markdown-it", "entities", "linkify-it", "mdurl", "punycode.js", "uc.micro"):
        key = f"node_modules/{package}"
        folder = BUILD / key
        version = json.loads((folder / "package.json").read_text())["version"]
        if version != lock[key]["version"]:
            raise ValueError(f"{package}: run npm ci against the lockfile first.")
        license_file = next(folder.glob("LICENSE*"))
        notices.append(f"{package} {version}\n{license_file.read_text().strip()}")
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BUILD / "node_modules/markdown-it/dist/markdown-it.min.js", OUT / "markdown-it.min.js")
    (OUT / "LICENSES.txt").write_text("\n\n".join(notices) + "\n")


if __name__ == "__main__":
    main()
