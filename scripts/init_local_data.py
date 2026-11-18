from __future__ import annotations

import os
from pathlib import Path
import tempfile

if __package__:
    from .build_paper_registry import FIELDS as REGISTRY_FIELDS
    from .sync_registry_to_papers import PAPERS_FIELDS
else:
    from build_paper_registry import FIELDS as REGISTRY_FIELDS
    from sync_registry_to_papers import PAPERS_FIELDS


ROOT = Path(__file__).resolve().parents[1]


def initialise(root: Path = ROOT) -> None:
    target = root / "data" / "metadata"
    tables = {"papers.tsv": PAPERS_FIELDS, "paper_registry.tsv": REGISTRY_FIELDS}
    if any((target / name).exists() for name in tables):
        return
    target.mkdir(parents=True, exist_ok=True)
    for name, fields in tables.items():
        descriptor, temporary = tempfile.mkstemp(prefix=".catalogue-", dir=target)
        temporary = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write("\t".join(fields) + "\n")
            try:
                os.link(temporary, target / name)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    initialise()
