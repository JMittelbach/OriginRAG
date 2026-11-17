from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_private_files as guard  # noqa: E402
import init_local_data  # noqa: E402
import install_git_hooks  # noqa: E402


PRIVATE = [
    "data/metadata/papers.tsv", "data/metadata/paper_registry.tsv", "data/metadata/paper_requests.tsv",
    "data/indexes/index.bin", "data/new-format/cache.json", "bib/new-paper.pdf", "bib/bibliography.tsv",
    "outputs/chat_sessions/a/chat.json", "outputs/answers/export.txt", "runtime/server.json",
    ".env", ".env.production", "config/paths.yaml", "config/preferences.local.yaml",
    "users.sqlite", "users.sqlite-wal", "users.sqlite3-shm", "private.db",
    "my-export.pdf", "my-export.PDF", "chat.json", "sessions/a.json", "exports/papers.csv", "my.bib", "private.key",
    "nested/OriginRAG/data/metadata/local_pdf_inventory.tsv",
    "nested/OriginRAG/data/indexes/search.bin",
    "nested/OriginRAG/runtime/maintenance/discovery.tsv",
    "nested/OriginRAG/bib/papers/local.txt", "nested/OriginRAG/outputs/session/result.txt",
    "nested/OriginRAG/external/private-model/config.json",
]


def run(root, *arguments, **kwargs):
    return subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, text=True, **kwargs)


@pytest.fixture
def repository(tmp_path):
    run(tmp_path, "init", check=True)
    run(tmp_path, "config", "user.email", "test@example.invalid", check=True)
    run(tmp_path, "config", "user.name", "Privacy test", check=True)
    (tmp_path / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    run(tmp_path, "add", ".gitignore", check=True)
    run(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-m", "Synthetic clean baseline", check=True)
    return tmp_path


@pytest.mark.parametrize("name", PRIVATE)
def test_local_outputs_are_ignored_and_guarded(repository, name):
    assert run(repository, "check-ignore", "--no-index", "--", name).returncode == 0
    assert guard.private_path(name)


@pytest.mark.parametrize("name", ["src/originrag/paths.py", "tests/test_private_data_guard.py", "assets/catalogue/papers.tsv", ".env.example", "config/paths.example.yaml", "data/metadata/.gitkeep", "bib/README.md"])
def test_public_source_and_examples_remain_committable(repository, name):
    assert run(repository, "check-ignore", "--no-index", "--", name).returncode == 1
    assert not guard.private_path(name)


def test_force_added_private_file_is_detected(repository, monkeypatch):
    path = repository / "data" / "metadata" / "papers.tsv"
    path.parent.mkdir(parents=True)
    path.write_text("synthetic private catalogue\n")
    run(repository, "add", "-f", str(path), check=True)
    monkeypatch.chdir(repository)
    assert guard.inspect_index() == {"data/metadata/papers.tsv"}


def test_nested_force_added_catalogue_is_detected(repository, monkeypatch):
    name = 'nested/OriginRAG/data/metadata/local_pdf_inventory.tsv'
    path = repository / name
    path.parent.mkdir(parents=True)
    path.write_text('synthetic private inventory\n')
    run(repository, 'add', '-f', name, check=True)
    monkeypatch.chdir(repository)
    assert guard.inspect_index() == {name}


def test_push_checks_removed_files_in_outgoing_history(repository, monkeypatch):
    base = run(repository, "rev-parse", "HEAD", check=True).stdout.strip()
    path = repository / "outputs" / "chat.txt"
    path.parent.mkdir()
    path.write_text("synthetic private conversation\n")
    run(repository, "add", "-f", str(path), check=True)
    run(repository, "-c", "core.hooksPath=/dev/null", "commit", "-m", "Synthetic private file", check=True)
    run(repository, "rm", str(path), check=True)
    run(repository, "-c", "core.hooksPath=/dev/null", "commit", "-m", "Remove synthetic file", check=True)
    tip = run(repository, "rev-parse", "HEAD", check=True).stdout.strip()
    monkeypatch.chdir(repository)
    assert guard.inspect_index() == set()
    assert guard.inspect_push(f"refs/heads/main {tip} refs/heads/main {base}\n", "origin") == {"outputs/chat.txt"}
    assert guard.inspect_push(f"refs/heads/main {'0' * 40} refs/heads/main {tip}\n", "origin") == set()


def test_hook_installer_preserves_custom_hooks(repository, monkeypatch):
    monkeypatch.setattr(install_git_hooks, "ROOT", repository)
    run(repository, "config", "core.hooksPath", "custom-hooks", check=True)
    assert install_git_hooks.main() == 0
    assert run(repository, "config", "--get", "core.hooksPath", check=True).stdout.strip() == "custom-hooks"
    run(repository, "config", "--unset", "core.hooksPath", check=True)
    (repository / ".git/hooks/pre-commit").write_text("custom hook")
    assert install_git_hooks.main() == 0
    assert run(repository, "config", "--get", "core.hooksPath").returncode == 1


def test_installed_hook_blocks_actual_commit(repository, monkeypatch):
    shutil.copytree(ROOT / ".githooks", repository / ".githooks")
    (repository / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/check_private_files.py", repository / "scripts/check_private_files.py")
    monkeypatch.setattr(install_git_hooks, "ROOT", repository)
    assert install_git_hooks.main() == 0
    secret = repository / ".env"
    secret.write_text("SYNTHETIC_TEST_KEY=not-a-real-key\n")
    run(repository, "add", "-f", ".env", check=True)
    result = run(repository, "commit", "-m", "Must be blocked")
    assert result.returncode != 0
    assert "Local data must not be committed" in result.stderr


def test_push_hook_blocks_upload_of_deleted_private_history(repository, monkeypatch, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    run(repository, "remote", "add", "origin", str(remote), check=True)
    run(repository, "push", "origin", "HEAD:refs/heads/main", check=True)
    secret = repository / ".env"
    secret.write_text("SYNTHETIC_TEST_KEY=not-a-real-key\n")
    run(repository, "add", "-f", ".env", check=True)
    run(repository, "-c", "core.hooksPath=/dev/null", "commit", "-m", "Synthetic private history", check=True)
    run(repository, "rm", ".env", check=True)
    run(repository, "-c", "core.hooksPath=/dev/null", "commit", "-m", "Remove synthetic private file", check=True)
    shutil.copytree(ROOT / ".githooks", repository / ".githooks")
    (repository / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/check_private_files.py", repository / "scripts/check_private_files.py")
    monkeypatch.setattr(install_git_hooks, "ROOT", repository)
    install_git_hooks.main()
    before = run(remote, "rev-parse", "refs/heads/main", check=True).stdout
    result = run(repository, "push", "origin", "HEAD:refs/heads/main")
    assert result.returncode != 0
    assert "Local data must not be committed or pushed" in result.stderr
    assert run(remote, "rev-parse", "refs/heads/main", check=True).stdout == before


def test_example_catalogue_is_copied_not_shared_or_overwritten(tmp_path):
    source = tmp_path / "assets/catalogue"
    source.mkdir(parents=True)
    for name in ("papers.tsv", "paper_registry.tsv"):
        (source / name).write_text("example\n")
    init_local_data.initialise(tmp_path)
    private = tmp_path / "data/metadata/papers.tsv"
    assert private.read_text() == "example\n"
    assert private.stat().st_ino != (source / "papers.tsv").stat().st_ino
    private.write_text("personal\n")
    init_local_data.initialise(tmp_path)
    assert private.read_text() == "personal\n"
    assert (source / "papers.tsv").read_text() == "example\n"


def test_partial_user_catalogue_is_not_mixed_with_examples(tmp_path):
    target = tmp_path / "data/metadata"
    target.mkdir(parents=True)
    (target / "papers.tsv").write_text("personal\n")
    init_local_data.initialise(tmp_path)
    assert not (target / "paper_registry.tsv").exists()


def test_product_index_contains_no_working_data():
    assert guard.inspect_index() == set()
