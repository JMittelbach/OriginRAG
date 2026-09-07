from __future__ import annotations

from pathlib import Path

import pytest

from originrag.paths import OriginRAGConfigError, get_bib_dir, resolve_bib_path


def test_get_bib_dir_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGINRAG_BIB_DIR", "")
    monkeypatch.setenv("PFEMPIRE_BIB_DIR", "")
    monkeypatch.setenv("HYPORAG_BIB_DIR", "")

    with pytest.raises(OriginRAGConfigError):
        get_bib_dir()


def test_resolve_bib_path_under_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGINRAG_BIB_DIR", str(tmp_path))

    resolved = resolve_bib_path("malaria/var_pfemp1/paper.pdf")

    assert resolved == tmp_path / "malaria" / "var_pfemp1" / "paper.pdf"


def test_get_bib_dir_accepts_legacy_hyporag_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORIGINRAG_BIB_DIR", "")
    monkeypatch.delenv("PFEMPIRE_BIB_DIR", raising=False)
    monkeypatch.setenv("HYPORAG_BIB_DIR", str(tmp_path))

    assert get_bib_dir() == tmp_path


def test_resolve_bib_path_rejects_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORIGINRAG_BIB_DIR", str(tmp_path))

    with pytest.raises(ValueError):
        resolve_bib_path("/tmp/paper.pdf")


def test_resolve_bib_path_rejects_parent_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORIGINRAG_BIB_DIR", str(tmp_path))

    with pytest.raises(ValueError):
        resolve_bib_path("../outside.pdf")


def test_get_bib_dir_accepts_pre_rename_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORIGINRAG_BIB_DIR", "")
    monkeypatch.setenv("PFEMPIRE_BIB_DIR", str(tmp_path))
    monkeypatch.setenv("HYPORAG_BIB_DIR", "")

    assert get_bib_dir() == tmp_path
