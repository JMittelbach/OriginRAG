from __future__ import annotations

import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def initialise(root: Path = ROOT) -> None:
    target = root / "data" / "metadata"
    names = ("papers.tsv", "paper_registry.tsv")
    if any((target / name).exists() for name in names):
        return
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        source = root / "assets" / "catalogue" / name
        if not source.is_file():
            continue
        descriptor, temporary = tempfile.mkstemp(prefix=".catalogue-", dir=target)
        temporary = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(source.read_bytes())
            try:
                os.link(temporary, target / name)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    initialise()
