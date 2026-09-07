from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_: object, **__: object) -> bool:
        return False


REPO_ROOT = Path(__file__).resolve().parents[2]


class OriginRAGConfigError(RuntimeError):
    """Raised when required local configuration is missing."""



PfEMPireConfigError = OriginRAGConfigError
HypoRAGConfigError = OriginRAGConfigError


def load_project_env(env_path: Path | None = None) -> None:
    """Load local environment values, with a tiny fallback if python-dotenv is absent."""
    path = env_path or REPO_ROOT / ".env"
    loaded = load_dotenv(path, override=False)
    if not loaded:
        _load_simple_env(path)
    _synchronise_environment_names()


def get_bib_dir() -> Path:
    """Return the local bibliography root from ORIGINRAG_BIB_DIR."""
    environment_names = ("ORIGINRAG_BIB_DIR", "PFEMPIRE_BIB_DIR", "HYPORAG_BIB_DIR")
    if any(name in os.environ for name in environment_names):
        _synchronise_environment_names()
        raw_path = _env_value("ORIGINRAG_BIB_DIR", "PFEMPIRE_BIB_DIR", "HYPORAG_BIB_DIR")
    else:
        load_project_env()
        raw_path = _env_value("ORIGINRAG_BIB_DIR", "PFEMPIRE_BIB_DIR", "HYPORAG_BIB_DIR")
    if not raw_path:
        raise OriginRAGConfigError(
            "ORIGINRAG_BIB_DIR is not set. Set it in your environment or in a local .env file. "
            "Legacy PFEMPIRE_BIB_DIR and HYPORAG_BIB_DIR are still accepted."
        )
    return Path(raw_path).expanduser()


def resolve_bib_path(local_pdf_relpath: str) -> Path:
    """Resolve a metadata PDF path under the configured bibliography root."""
    if not local_pdf_relpath or not local_pdf_relpath.strip():
        raise ValueError("local_pdf_relpath must be a non-empty relative path.")

    relative_path = Path(local_pdf_relpath.strip())
    if relative_path.is_absolute():
        raise ValueError("local_pdf_relpath must be relative to ORIGINRAG_BIB_DIR.")

    bib_dir = get_bib_dir().resolve()
    resolved_path = (bib_dir / relative_path).resolve()

    try:
        resolved_path.relative_to(bib_dir)
    except ValueError as exc:
        raise ValueError("local_pdf_relpath resolves outside ORIGINRAG_BIB_DIR.") from exc

    return resolved_path


def _env_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _synchronise_environment_names() -> None:
    """Prefer OriginRAG variables while accepting pre-rename PFEMPIRE variables."""
    for key, value in tuple(os.environ.items()):
        if key.startswith("ORIGINRAG_") and value:
            os.environ[f"PFEMPIRE_{key.removeprefix('ORIGINRAG_')}"] = value
    for key, value in tuple(os.environ.items()):
        if key.startswith("PFEMPIRE_") and value:
            os.environ.setdefault(f"ORIGINRAG_{key.removeprefix('PFEMPIRE_')}", value)


def _load_simple_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
