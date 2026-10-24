"""Install an isolated, pinned Colibri runtime without downloading model weights."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


REVISION = "e0db083cdc9e563e0766cc6fc661ef23c32298e0"
REPOSITORY = "https://github.com/JustVugg/colibri.git"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = (args.directory or Path(os.environ.get("ORIGINRAG_COLIBRI_HOME",
                                                  root / "runtime/colibri"))).resolve()
    for tool in ("git", "make"):
        if not shutil.which(tool):
            parser.error(f"{tool} is required. Install the platform's development tools first.")
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="colibri-install-", dir=target.parent))
        subprocess.run(["git", "init", str(staging)], check=True)
        subprocess.run(["git", "-C", str(staging), "fetch", "--depth", "1", REPOSITORY, REVISION],
                       check=True)
        subprocess.run(["git", "-C", str(staging), "checkout", "--detach", "FETCH_HEAD"], check=True)
        staging.rename(target)
    current = subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip()
    if current != REVISION:
        parser.error("The target contains a different Colibri revision; it was not changed.")
    if subprocess.check_output(["git", "-C", str(target), "diff", "--name-only", "HEAD"], text=True).strip():
        parser.error("The existing Colibri sources have local modifications; they were not changed.")
    venv = target / ".venv"
    if not venv.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    python = venv / "bin/python"
    subprocess.run([str(python), "-m", "pip", "install", "numpy>=2,<3", "safetensors>=0.6,<1"], check=True)
    subprocess.run(["make", "-C", str(target / "c"), "olmoe", "qwen36", "-j2"], check=True)
    print(f"Colibri {REVISION[:12]} is ready at {target}.")
    print("Open Settings → Models & runtimes → Colibri to download a model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
