import base64
from copy import deepcopy
import sqlite3

import pytest

from originrag.writing import WritingConflict, WritingStore
from test_writing_merge import manuscript, source_request


@pytest.mark.parametrize('with_basis', [False, True])
def test_unchanged_save_does_not_rewrite_project_or_revision(tmp_path, monkeypatch, with_basis):
    store = WritingStore(tmp_path)
    project = manuscript(store)
    project = store.change(source_request(project, **{
        'figure.png': base64.b64encode(b'x' * 1_000_000).decode(),
    }), [])
    before = store.path.read_bytes()
    changes = []

    class ObservedConnection(sqlite3.Connection):
        def close(self):
            changes.append(self.total_changes)
            super().close()

    monkeypatch.setattr(store, 'connect', lambda: sqlite3.connect(store.path, factory=ObservedConnection))
    request = source_request(project)
    if not with_basis:
        del request['base']
    for _ in range(5):
        assert store.change(request, []) == project
    assert changes == [0] * 5
    assert store.path.read_bytes() == before


def test_reordered_files_and_retried_merge_are_noops(tmp_path):
    store = WritingStore(tmp_path)
    project = manuscript(store)
    request = source_request(project, **{'main.tex': 'Updated text'})
    saved = store.change(request, [])
    assert saved['revision'] == project['revision'] + 1
    assert store.change(request, []) == saved
    reordered = source_request(saved)
    reordered['files'] = dict(reversed(list(reordered['files'].items())))
    assert store.change(reordered, []) == saved


def test_noop_still_validates_revision_basis_and_source(tmp_path):
    store = WritingStore(tmp_path)
    project = manuscript(store)
    saved = store.change(source_request(project, **{'main.tex': 'Updated text'}), [])
    stale = source_request(project)
    stale['files'] = deepcopy(saved['files'])
    del stale['base']
    with pytest.raises(WritingConflict):
        store.change(stale, [])
    tampered = source_request(saved)
    tampered['base']['files']['main.tex'] = 'Fabricated basis'
    with pytest.raises(WritingConflict):
        store.change(tampered, [])
    malformed = source_request(saved)
    malformed['files']['../outside.tex'] = ''
    with pytest.raises(ValueError):
        store.change(malformed, [])
    assert store.get(saved['id']) == saved


def test_legacy_noop_does_not_break_later_merge(tmp_path):
    store = WritingStore(tmp_path)
    project = manuscript(store, 'First line.\nLast line.\n')
    with store.connect() as db:
        db.execute('DELETE FROM writing_bases')
    assert store.change(source_request(project), []) == project
    first = store.change(source_request(project, **{'main.tex': 'FIRST line.\nLast line.\n'}), [])
    merged = store.change(source_request(project, **{'main.tex': 'First line.\nLAST line.\n'}), [])
    assert merged['revision'] == first['revision'] + 1
    assert merged['files']['main.tex'] == 'FIRST line.\nLAST line.\n'


def test_unchanged_save_still_reconciles_changed_library_metadata(tmp_path):
    from test_writing_auto_bibliography import CATALOGUE, setup
    store = WritingStore(tmp_path)
    project, _ = setup(store)
    assert store.change(source_request(project), CATALOGUE) == project
    changed_catalogue = deepcopy(CATALOGUE)
    changed_catalogue[0]['title'] = 'Corrected reference title'
    updated = store.change(source_request(project), changed_catalogue)
    assert updated['revision'] == project['revision'] + 1
    assert 'Corrected reference title' in updated['files']['references.bib']
    assert store.change(source_request(updated), changed_catalogue) == updated
