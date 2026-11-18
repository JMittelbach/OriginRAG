from __future__ import annotations

import argparse
import csv
import hashlib
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
OLD_STARTER = {
    "papers.tsv": "9de778d4a8924d34b0b289c369b42cd7b00bada092a1ab2a33beaff981ea0453",
    "paper_registry.tsv": "e76beedfe325bc474ebc85601028a34be18c0ae5a9d32b7ab83d7ec4d15a6528",
}


def check_catalogue(root: Path = ROOT) -> None:
    directory = root / "data" / "metadata"
    print(f"Local catalogue: {directory}")
    for name, expected in OLD_STARTER.items():
        path = directory / name
        if not path.exists():
            print(f"{name}: not present")
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for line in stream:
                digest.update(line.replace(b"\r\n", b"\n"))
        if digest.hexdigest() == expected:
            print(f"{name}: matches the old bundled starter catalogue")
        else:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, delimiter="\t")
                if not {"paper_id", "title"} <= set(reader.fieldnames or ()):
                    print(f"{name}: unrecognised table")
                    continue
                rows = sum(1 for _ in reader)
            print(f"{name}: {rows} local records; not the old starter copy")


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
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Report local catalogue provenance without changing files.")
    if parser.parse_args().check:
        check_catalogue()
    else:
        initialise()
