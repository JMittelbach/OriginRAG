"""Copy writing databases to local storage with the web service stopped."""
import argparse
import json
from itertools import zip_longest
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from originrag.workspace_storage import workspace_database


def migrate(source, destination):
    source = Path(source).resolve()
    paths = [source / '.writing-collaboration.sqlite3', source / '.writing.sqlite3',
             source / '.folio.sqlite3', *sorted(source.glob('users/*/.writing.sqlite3')),
             *sorted(source.glob('users/*/.folio.sqlite3'))]
    results = []
    for path in paths:
        if not path.exists():
            continue
        if path.is_symlink() or path.parent.is_symlink():
            raise OSError('Refusing a symlinked source database.')
        target = workspace_database(path.parent, path.name, root=destination, migrating=True)
        if target.exists():
            raise FileExistsError('Destination already exists: ' + str(target))
        temporary = target.with_suffix('.migrating')
        if temporary.exists():
            raise FileExistsError(str(temporary))
        temporary.touch(mode=0o600, exist_ok=False)
        old = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=30)
        new = sqlite3.connect(temporary)
        try:
            old.backup(new)
            assert new.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert all(a == b for a, b in zip_longest(old.iterdump(), new.iterdump())), 'Backup content differs'
            new.close()
            temporary.replace(target)
            results.append({'source': str(path), 'destination': str(target), 'verified': True})
        finally:
            old.close()
            new.close()
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--service-stopped', required=True, action='store_true')
    args = parser.parse_args()
    print(json.dumps(migrate(args.source_root, args.destination), indent=2))
