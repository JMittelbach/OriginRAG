import base64
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import io
import random
import sqlite3
import zipfile

import pytest

from originrag.writing import WritingConflict, WritingStore
from originrag.writing_merge import MergeConflict, merge_text, project_basis_digest, text_changes


def source_request(project, **files):
    return {'action': 'save_project', 'id': project['id'], 'revision': project['revision'],
            'main': project['main'], 'collection': project.get('collection', ''),
            'files': {**project['files'], **files},
            'base': {key: deepcopy(project.get(key, '' if key == 'collection' else None))
                     for key in ('revision', 'files', 'main', 'collection')}}


def manuscript(store, text='A first section.\nA middle section.\nA final section.\n'):
    project = store.change({'action': 'create_project', 'name': 'A shared manuscript'}, [])
    return store.change(source_request(project, **{'main.tex': text}), [])


@pytest.mark.parametrize(('base', 'local', 'remote', 'expected'), [
    ('alpha beta gamma', 'Alpha beta gamma', 'alpha beta Gamma', 'Alpha beta Gamma'),
    ('one\ntwo\nthree\n', 'new\none\ntwo\nthree\n', 'one\ntwo\nthree\nend\n', 'new\none\ntwo\nthree\nend\n'),
    ('abcde', 'abXcde', 'abcdeY', 'abXcdeY'),
    ('abcde', 'acde', 'abcd', 'acd'),
    ('😀 alpha λ omega', '😀 ALPHA λ omega', '😀 alpha λ OMEGA', '😀 ALPHA λ OMEGA'),
    ('abcdef', 'aXbcdeYf', 'abcZdef', 'aXbcZdeYf'),
    ('abc', 'abc!', 'abc!', 'abc!'),
    ('abc', 'abc', 'ABC', 'ABC'),
])
def test_non_overlapping_merges_are_symmetric(base, local, remote, expected):
    assert merge_text(base, local, remote) == expected
    assert merge_text(base, remote, local) == expected


@pytest.mark.parametrize(('base', 'local', 'remote'), [
    ('abc', 'aXbc', 'aYbc'), ('abc', 'aXc', 'aYc'), ('abc', 'ac', 'aBc'),
    ('abc', 'aXc', 'abYc'), ('abc', '', 'abcd'),
])
def test_overlap_is_never_silently_resolved(base, local, remote):
    with pytest.raises(MergeConflict):
        merge_text(base, local, remote)


def test_diff_reconstructs_fuzzed_text_including_repeated_and_unicode_characters():
    rng = random.Random(72391)
    for _ in range(1500):
        base = ''.join(rng.choice('abc\n λ😀') for _ in range(rng.randrange(100)))
        changed = base
        for _ in range(rng.randrange(1, 6)):
            start = rng.randrange(len(changed) + 1)
            end = rng.randrange(start, min(len(changed), start + 10) + 1)
            changed = changed[:start] + ''.join(rng.choice('xyz😀') for _ in range(rng.randrange(10))) + changed[end:]
        pieces, position = [], 0
        for start, end, insertion in text_changes(base, changed):
            assert position <= start <= end <= len(base)
            pieces.extend((base[position:start], insertion))
            position = end
        pieces.append(base[position:])
        assert ''.join(pieces) == changed


def test_diff_budget_fails_closed():
    with pytest.raises(MergeConflict):
        text_changes('abc', 'xyz', budget=[0])
    with pytest.raises(MergeConflict):
        text_changes('a' * 3000, 'b' * 3000)


def test_two_concurrent_edits_same_file_both_persist(tmp_path):
    store = WritingStore(tmp_path)
    project = manuscript(store)
    first = source_request(project, **{'main.tex': project['files']['main.tex'].replace('first', 'FIRST')})
    second = source_request(project, **{'main.tex': project['files']['main.tex'].replace('final', 'FINAL')})
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda payload: WritingStore(tmp_path).change(payload, []), [first, second]))
    saved = store.get(project['id'])
    assert saved['revision'] == project['revision'] + 2
    assert sorted(item['revision'] for item in results) == [project['revision'] + 1, project['revision'] + 2]
    assert 'FIRST' in saved['files']['main.tex'] and 'FINAL' in saved['files']['main.tex']
    assert store.revision(project['id']) == saved['revision']


def test_overlap_and_forged_basis_leave_saved_project_unchanged(tmp_path):
    store = WritingStore(tmp_path)
    project = manuscript(store, 'hello world')
    current = store.change(source_request(project, **{'main.tex': 'hello Alice'}), [])
    with pytest.raises(WritingConflict, match='Concurrent changes'):
        store.change(source_request(project, **{'main.tex': 'hello Bob'}), [])
    fake = source_request(project, **{'main.tex': 'hello Mallory'})
    fake['base']['files']['main.tex'] = 'hello fabricated'
    with pytest.raises(WritingConflict, match='basis'):
        store.change(fake, [])
    assert store.get(project['id']) == current


def test_source_and_binary_additions_export_together_and_zip_roundtrips(tmp_path):
    store = WritingStore(tmp_path)
    project = manuscript(store)
    data = b'\x89PNG\r\n\x1a\n\x00binary-synthetic'
    one = source_request(project, **{'sections/new.tex': '\\section{Results}'} )
    two = source_request(project, **{'references.bib': '@article{x,title={A paper}}',
                                    'figures/example.png': base64.b64encode(data).decode()})
    store.change(one, [])
    merged = store.change(two, [])
    archive = store.export(project['id'])
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        assert zipped.read('figures/example.png') == data
        assert zipped.read('references.bib').decode() == merged['files']['references.bib']
        assert set(zipped.namelist()) == set(merged['files'])
    restored = store.change({'action': 'create_project', 'name': 'Restored', 'zip': base64.b64encode(archive).decode()}, [])
    assert restored['files'] == merged['files']


def test_deleting_a_file_conflicts_with_concurrent_edit(tmp_path):
    store = WritingStore(tmp_path)
    project = manuscript(store)
    project = store.change(source_request(project, **{'extra.tex': 'first'}), [])
    deletion = source_request(project)
    del deletion['files']['extra.tex']
    store.change(source_request(project, **{'extra.tex': 'second'}), [])
    with pytest.raises(WritingConflict, match='extra.tex'):
        store.change(deletion, [])


def test_no_basis_preserves_existing_optimistic_locking(tmp_path):
    store = WritingStore(tmp_path)
    project = manuscript(store)
    request = source_request(project)
    del request['base']
    store.change(source_request(project, **{'main.tex': 'latest'}), [])
    with pytest.raises(WritingConflict, match='Changed in another window'):
        store.change(request, [])


def test_revision_basis_history_is_bounded_and_stale_merge_is_rejected(tmp_path):
    store = WritingStore(tmp_path)
    initial = current = manuscript(store, 'start')
    for i in range(70):
        current = store.change(source_request(current, **{'main.tex': str(i)}), [])
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM writing_bases').fetchone()[0] == 64
    with pytest.raises(WritingConflict, match='basis'):
        store.change(source_request(initial, **{'main.tex': 'old browser'}), [])
    assert store.get(current['id']) == current


def test_digest_is_filename_order_independent_and_collection_private():
    one = {'files': {'main.tex': 'x', 'a.bib': 'y'}, 'main': 'main.tex', 'collection': 'private'}
    two = {'files': {'a.bib': 'y', 'main.tex': 'x'}, 'main': 'main.tex', 'collection': ''}
    assert project_basis_digest(one) == project_basis_digest(two)
    two['files']['main.tex'] = 'xy'
    assert project_basis_digest(one) != project_basis_digest(two)


def test_pending_source_edit_survives_owner_bibliography_sync(tmp_path):
    from test_writing_references import CATALOGUE, prepared, sync_payload
    store, project, _ = prepared(tmp_path)
    pending = source_request(project, **{'main.tex': project['files']['main.tex'].replace('Your manuscript starts here.', 'A pending paragraph.')})
    preview = store.bibliography_preview(project['id'], CATALOGUE, connect_document=True)
    synced = store.change(sync_payload(preview), CATALOGUE)
    merged = store.change(pending, CATALOGUE)
    assert 'A pending paragraph.' in merged['files']['main.tex']
    assert merged['files']['references.bib'] == synced['files']['references.bib']
    assert merged['bibliography_sync'] == synced['bibliography_sync']
    assert r'\bibliography{references}' in merged['files']['main.tex']


def test_legacy_database_without_hashes_is_migrated_on_first_save(tmp_path):
    store = WritingStore(tmp_path)
    project = manuscript(store)
    with sqlite3.connect(store.path) as db:
        db.execute('DROP TABLE writing_bases')
    store.change(source_request(project, **{'main.tex': project['files']['main.tex'].replace('first', 'FIRST')}), [])
    latest = store.change(source_request(project, **{'main.tex': project['files']['main.tex'].replace('final', 'FINAL')}), [])
    assert 'FIRST' in latest['files']['main.tex'] and 'FINAL' in latest['files']['main.tex']


def test_revision_poll_uses_a_covering_index_without_loading_source(tmp_path):
    store = WritingStore(tmp_path)
    project = manuscript(store)
    with sqlite3.connect(store.path) as db:
        plan = db.execute("EXPLAIN QUERY PLAN SELECT revision FROM documents INDEXED BY documents_revision "
                          "WHERE id=? AND kind='projects'", (project['id'],)).fetchall()
    assert 'COVERING INDEX documents_revision' in str(plan)
    assert store.revision(project['id']) == project['revision']
