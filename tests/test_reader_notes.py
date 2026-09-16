import copy
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest

from originrag.reader_notes import ReaderNotes, NoteConflict


FINGERPRINT = 'a' * 64
MARK = {'page': 1, 'rects': [[.1, .2, .5, .03]], 'quote': 'A useful passage.', 'note': '', 'color': 'yellow'}


def add(store, **overrides):
    return store.change({'action': 'add', 'paper_id': 'P1', 'fingerprint': FINGERPRINT, 'mark': copy.deepcopy(MARK), **overrides})


def test_notes_are_private_persistent_and_pdf_version_specific(tmp_path):
    alice, bob = ReaderNotes(tmp_path / 'alice'), ReaderNotes(tmp_path / 'bob')
    mark = add(alice)['items'][0]
    assert bob.snapshot('P1', FINGERPRINT)['items'] == []
    assert alice.snapshot('P2', FINGERPRINT)['items'] == []
    assert alice.snapshot('P1', 'b' * 64) == {'items': [], 'other_version_count': 1}
    assert ReaderNotes(tmp_path / 'alice').snapshot('P1', FINGERPRINT)['items'][0] == mark
    assert stat.S_IMODE(alice.path.stat().st_mode) == 0o600


def test_note_updates_are_atomic_and_reject_stale_windows(tmp_path):
    store = ReaderNotes(tmp_path)
    mark = add(store)['items'][0]
    update = {'action': 'update', 'paper_id': 'P1', 'fingerprint': FINGERPRINT, 'id': mark['id'], 'revision': 1, 'mark': {**MARK, 'note': 'Read the supplementary methods.'}}
    updated = store.change(update)['items'][0]
    assert updated['revision'] == 2 and updated['note'] == update['mark']['note']
    with pytest.raises(NoteConflict):
        store.change(update)
    remove = {k: v for k, v in update.items() if k != 'mark'}
    remove['action'] = 'delete'
    with pytest.raises(NoteConflict):
        store.change(remove)
    assert store.change({**remove, 'revision': 2})['items'] == []


@pytest.mark.parametrize('field,value', [
    ('page', True), ('page', 0), ('page', 20001), ('color', 'unknown'),
    ('quote', 'x' * 10001), ('note', None), ('rects', []),
    ('rects', [[0, 0, float('nan'), .1]]), ('rects', [[-.1, .1, .2, .2]]),
    ('rects', [[.9, .1, .5, .1]]), ('rects', [[0, 0, 0, .1]]),
    ('rects', [[0, 0, .1, .1]] * 201),
])
def test_invalid_annotations_rejected(tmp_path, field, value):
    store = ReaderNotes(tmp_path)
    with pytest.raises(ValueError):
        add(store, mark={**MARK, field: value})
    assert store.snapshot('P1', FINGERPRINT)['items'] == []


def test_parallel_additions_do_not_overwrite_each_other(tmp_path):
    store = ReaderNotes(tmp_path)
    store.snapshot('P1', FINGERPRINT)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: add(store), range(12)))
    assert len(store.snapshot('P1', FINGERPRINT)['items']) == 12


@pytest.mark.parametrize('color', ['yellow', 'mint', 'rose', 'red'])
def test_highlight_colors_are_saved(tmp_path, color):
    assert add(ReaderNotes(tmp_path), mark={**MARK, 'color': color})['items'][0]['color'] == color


def test_cannot_mutate_other_document_or_supply_owner(tmp_path):
    store = ReaderNotes(tmp_path)
    mark = add(store)['items'][0]
    with pytest.raises(ValueError):
        add(store, owner='bob')
    with pytest.raises(ValueError):
        add(store, fingerprint='../other')
    with pytest.raises(NoteConflict):
        store.change({'action': 'delete', 'paper_id': 'P2', 'fingerprint': FINGERPRINT, 'id': mark['id'], 'revision': 1})
    assert len(store.snapshot('P1', FINGERPRINT)['items']) == 1
