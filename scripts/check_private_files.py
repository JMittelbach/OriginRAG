from __future__ import annotations

import argparse
from pathlib import PurePosixPath
import subprocess
import sys


SAFE = {"data/metadata/.gitkeep", "outputs/.gitkeep", "bib/.gitkeep", "bib/README.md", "bib/papers/.gitkeep"}
LOCAL_DIRS = {"runtime", "external", "logs", "uploads", "inbox", "sessions", "chat_sessions", "web_chat", "answers", "exports", ".cache", ".local", "chunks", "extracted_text", "indexes", "embeddings", "chroma", "faiss"}
CATALOGUES = {"papers.tsv", "paper_registry.tsv", "corpus_manifest.tsv", "local_pdf_inventory.tsv", "all_papers.tsv"}


def private_path(name: str) -> bool:
    path = PurePosixPath(name)
    if name in SAFE:
        return False
    if ({"data", "bib", "outputs"} | LOCAL_DIRS).intersection(part.lower() for part in path.parts[:-1]):
        return True
    base = path.name.lower()
    if base in CATALOGUES or name.startswith("assets/catalogue/"):
        return True
    if base.endswith('.origin1') or 'Origin Documents' in path.parts:
        return True
    if base.startswith(".env") and base != ".env.example":
        return True
    if base in {"chat.json", "session_config.json"} or name in {"config/paths.yaml", "config/paths.yml"}:
        return True
    if name.startswith("config/") and ".local." in base:
        return True
    if any(base.endswith(suffix) for suffix in (".pdf", ".epub", ".xpdf", ".jsonl", ".bib", ".log", ".db", ".pem", ".key", ".p12", ".pfx")):
        return True
    return any(base.endswith(suffix) or suffix + "-" in base for suffix in (".sqlite", ".sqlite3"))


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], stderr=subprocess.PIPE)


def split_paths(raw: bytes) -> list[str]:
    return [name.decode("utf-8", "surrogateescape") for name in raw.split(b"\0") if name]


def inspect_index() -> set[str]:
    return {path for path in split_paths(git("ls-files", "-z")) if private_path(path)}


def inspect_push(updates: str, remote: str) -> set[str]:
    blocked = set()
    commits = set()
    for line in updates.splitlines():
        local_ref, local_sha, remote_ref, remote_sha = line.split()
        if set(local_sha) == {"0"}:
            continue
        revisions = [local_sha]
        if set(remote_sha) != {"0"}:
            revisions.append("^" + remote_sha)
        else:
            revisions.extend(["--not", "--remotes=" + remote])
        commits.update(git("rev-list", *revisions).decode().split())
    for commit in commits:
        changed = git("diff-tree", "--root", "-m", "--no-commit-id", "--name-only", "--diff-filter=ACMRT", "-r", "-z", commit)
        blocked.update(path for path in split_paths(changed) if private_path(path))
    return blocked


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Block local research data and credentials from Git.")
    parser.add_argument("--push", metavar="REMOTE")
    args = parser.parse_args(argv)
    try:
        blocked = inspect_push(sys.stdin.read(), args.push) if args.push else inspect_index()
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"Privacy check could not complete ({type(exc).__name__}); stopping.", file=sys.stderr)
        return 1
    if blocked:
        print("Local data must not be committed or pushed:", file=sys.stderr)
        for name in sorted(blocked):
            print(f"  {name!r}", file=sys.stderr)
        print("Keep personal files under ignored data/, bib/ or outputs/. Unstage them without deleting your local copy. If already committed, remove them from the unpushed commits too.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
