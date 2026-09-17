import importlib.util
from pathlib import Path

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
