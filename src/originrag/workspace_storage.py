import hashlib
import os
from pathlib import Path


def workspace_database(directory, filename, *, root=None, migrating=False):
    directory = Path(directory).resolve()
    configured = root or os.environ.get('ORIGINRAG_WORKSPACE_STATE_DIR')
    if not configured:
        return directory / filename
    if filename not in {'.writing.sqlite3', '.writing-collaboration.sqlite3'}:
        raise ValueError('Unknown workspace database.')
    root = Path(configured).expanduser()
    if not root.is_absolute() or root.is_symlink():
        raise OSError('Workspace state directory must be an absolute, non-symlink path.')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    key = hashlib.sha256(str(directory).encode()).hexdigest()
    target = root / key / filename
    if target.parent.is_symlink() or target.is_symlink():
        raise OSError('Unsafe workspace database path.')
    if not migrating and not target.exists() and (directory / filename).exists():
        raise OSError('Migrate existing workspace databases before enabling local workspace storage.')
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.chmod(0o700)
    return target
