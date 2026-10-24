import importlib.util
from pathlib import Path
import sqlite3

import pytest

from originrag.writing import WritingStore


def test_explicit_migration_preserves_project_and_refuses_silent_empty_store(tmp_path, monkeypatch):
    monkeypatch.delenv('ORIGINRAG_WORKSPACE_STATE_DIR', raising=False)
    source = tmp_path / 'shared'
    owner = source / 'users' / 'alice'
    store = WritingStore(owner)
    project = store.change({'action': 'create_project', 'name': 'Keep this'}, [])
    local = tmp_path / 'local'
    monkeypatch.setenv('ORIGINRAG_WORKSPACE_STATE_DIR', str(local))
    with pytest.raises(OSError, match='Migrate'):
        WritingStore(owner)
    script = Path(__file__).resolve().parents[1] / 'scripts/migrate_workspace_storage.py'
    spec = importlib.util.spec_from_file_location('migration', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.migrate(source, local)
    assert len(result) == 1 and result[0]['verified']
    moved = WritingStore(owner)
    assert moved.get(project['id']) == project
    assert moved.path.is_relative_to(local)
    assert moved.path.stat().st_mode & 0o777 == 0o600
    assert store.path.exists()
    with pytest.raises(FileExistsError):
        module.migrate(source, local)
    with pytest.raises(KeyError):
        WritingStore(source / 'users' / 'bob').get(project['id'])


def test_local_workspace_rejects_symlink_root(tmp_path, monkeypatch):
    real = tmp_path / 'real'
    real.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv('ORIGINRAG_WORKSPACE_STATE_DIR', str(link))
    with pytest.raises(OSError):
        WritingStore(tmp_path / 'owner')


def test_folio_database_migration_is_explicit_and_preserves_private_state(tmp_path, monkeypatch):
    from originrag.workspace_storage import workspace_database

    monkeypatch.delenv('ORIGINRAG_WORKSPACE_STATE_DIR', raising=False)
    source = tmp_path / 'shared'
    owner = source / 'users' / 'alice'
    owner.mkdir(parents=True)
    original = owner / '.folio.sqlite3'
    with sqlite3.connect(original) as connection:
        connection.execute('CREATE TABLE sample (title TEXT)')
        connection.execute('INSERT INTO sample VALUES (?)', ('Private document',))
    local = tmp_path / 'local'
    monkeypatch.setenv('ORIGINRAG_WORKSPACE_STATE_DIR', str(local))
    with pytest.raises(OSError, match='Migrate'):
        workspace_database(owner, '.folio.sqlite3')
    script = Path(__file__).resolve().parents[1] / 'scripts/migrate_workspace_storage.py'
    spec = importlib.util.spec_from_file_location('migration_folio', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.migrate(source, local)
    assert len(result) == 1 and result[0]['verified']
    target = workspace_database(owner, '.folio.sqlite3')
    assert target.is_relative_to(local)
    assert target.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(target) as connection:
        assert connection.execute('SELECT title FROM sample').fetchall() == [('Private document',)]
    assert original.is_file()
    assert workspace_database(source / 'users' / 'bob', '.folio.sqlite3') != target
