from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if subprocess.run(["git", "rev-parse", "--git-dir"], cwd=ROOT, capture_output=True).returncode:
        return 0
    result = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=ROOT, capture_output=True, text=True)
    if result.stdout.strip() not in {"", ".githooks"}:
        print("Existing Git hooks kept. Add scripts/check_private_files.py to your hooks; see CONTRIBUTING.md.")
        return 0
    hooks = subprocess.check_output(["git", "rev-parse", "--git-path", "hooks"], cwd=ROOT, text=True).strip()
    if not result.stdout.strip() and any((ROOT / hooks / name).exists() for name in ("pre-commit", "pre-push")):
        print("Existing Git hooks kept. Add scripts/check_private_files.py to your hooks; see CONTRIBUTING.md.")
        return 0
    for name in ("pre-commit", "pre-push"):
        hook = ROOT / ".githooks" / name
        hook.chmod(hook.stat().st_mode | 0o111)
    subprocess.run(["git", "config", "--local", "core.hooksPath", ".githooks"], cwd=ROOT, check=True)
    print("Local-data protection enabled for commits and pushes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
