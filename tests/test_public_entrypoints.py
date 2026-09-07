from __future__ import annotations

import ast
import subprocess
import tomllib
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_public_launcher_help_exposes_first_use_commands() -> None:
    result = subprocess.run(
        [str(REPO_ROOT / "originrag"), "help"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "./originrag doctor" in result.stdout
    assert "./originrag search-papers" in result.stdout
    assert "./originrag import-local" in result.stdout
    assert "./originrag prepare-corpus" in result.stdout


def test_public_launcher_version_matches_package_metadata() -> None:
    result = subprocess.run(
        [str(REPO_ROOT / "originrag"), "--version"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    package = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert result.stdout.strip() == f"OriginRAG {package['project']['version']}"


def test_publication_metadata_has_repository_and_author() -> None:
    payload = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = payload["project"]

    assert project["name"] == "originrag"
    assert project["authors"] == [{"name": "Jannes Mittelbach"}]
    assert project["urls"]["Repository"].endswith("JMittelbach/OriginRAG.git")


def test_citation_metadata_matches_package_version() -> None:
    citation = yaml.safe_load((REPO_ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    package = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert citation["title"] == "OriginRAG"
    assert citation["version"] == package["project"]["version"]
    assert citation["repository-code"] == "https://github.com/JMittelbach/OriginRAG"


def test_source_package_uses_the_product_name() -> None:
    import originrag

    assert Path(originrag.__file__).resolve() == REPO_ROOT / "src/originrag/__init__.py"
    assert not (REPO_ROOT / "src/pfempire").exists()
    assert not (REPO_ROOT / "pfempire").exists()


def test_application_does_not_import_the_old_package() -> None:
    for directory in ("src", "scripts", "tests"):
        for source in (REPO_ROOT / directory).rglob("*.py"):
            for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                assert all(module.split(".")[0] != "pfempire" for module in modules), source
