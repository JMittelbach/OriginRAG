from concurrent.futures import ThreadPoolExecutor
import sqlite3
import stat
import threading

import pytest

from originrag import reader_notes
from originrag.reader_notes import NoteConflict, ReaderNotes
from test_reader_notes import FINGERPRINT, MARK


def save(store, text='A personal thought.', revision=0, paper_id='P1', **extra):
    return store.change_notebook({'paper_id': paper_id, 'text': text, 'revision': revision, **extra})


def test_notebook_is_private_persistent_and_paper_specific(tmp_path, monkeypatch):
    alice, bob = ReaderNotes(tmp_path / 'alice'), ReaderNotes(tmp_path / 'bob')
    empty = {'paper_id': 'P1', 'text': '', 'revision': 0, 'updated_at': None}
    assert alice.notebook_snapshot('P1') == bob.notebook_snapshot('P1') == empty
    monkeypatch.setattr(reader_notes.time, 'time_ns', lambda: 1_750_000_123_456_789_000)
    saved = save(alice)
    assert saved == {**empty, 'text': 'A personal thought.', 'revision': 1, 'updated_at': 1_750_000_123_456}
    assert ReaderNotes(tmp_path / 'alice').notebook_snapshot('P1') == saved
    assert bob.notebook_snapshot('P1') == empty
    assert alice.notebook_snapshot('P2') == {**empty, 'paper_id': 'P2'}
    assert stat.S_IMODE(alice.path.stat().st_mode) == 0o600


def test_notebook_updates_and_clears_retain_revisions(tmp_path):
    store = ReaderNotes(tmp_path)
    assert save(store, '')['revision'] == 1
    assert save(store, 'First draft', 1)['revision'] == 2
    for revision in (0, 1, 3):
        with pytest.raises(NoteConflict):
            save(store, 'Stale or guessed version', revision)
    cleared = save(store, '', 2)
    assert cleared['text'] == '' and cleared['revision'] == 3
    with pytest.raises(NoteConflict):
        save(store, 'Must not resurrect a cleared draft', 0)
    assert save(store, '', 3)['revision'] == 4
    assert save(store, 'New draft', 4)['revision'] == 5


def test_notes_preserve_unicode_whitespace_and_inert_markup(tmp_path):
    store = ReaderNotes(tmp_path)
    text = '  \n日本語 😀 café e\u0301\x00\n</textarea><script>alert("xss")</script>\n '
    saved = save(store, text)
    assert saved['text'] == text
    assert store.notebook_snapshot('P1')['text'] == text
    assert save(store, '😀' * 50_000, 1)['text'] == '😀' * 50_000


@pytest.mark.parametrize('payload', [
    None, [], 'text', {},
    {'paper_id': 'P1', 'text': ''},
    {'paper_id': 'P1', 'text': '', 'revision': 0, 'fingerprint': FINGERPRINT},
    {'paper_id': 'P1', 'text': '', 'revision': 0, 'owner': 'bob'},
    *({'paper_id': value, 'text': '', 'revision': 0}
      for value in (None, True, 1, [], '', ' ', '\x00', '\x7f', 'P' * 161, '\ud800')),
    *({'paper_id': 'P1', 'text': value, 'revision': 0}
      for value in (None, False, 1, [], {}, 'x' * 50_001, '\ud800')),
    *({'paper_id': 'P1', 'text': '', 'revision': value}
      for value in (None, False, True, -1, 1.0, '0', [], {}, 2 ** 53 - 1, 2 ** 80)),
])
def test_invalid_notebook_writes_never_touch_storage(tmp_path, payload):
    store = ReaderNotes(tmp_path)
    with pytest.raises(ValueError):
        store.change_notebook(payload)
    assert not store.path.exists()


@pytest.mark.parametrize('paper_id', [None, False, 3, [], {}, '', ' ', '\n', 'P' * 161])
def test_invalid_notebook_reads_never_touch_storage(tmp_path, paper_id):
    store = ReaderNotes(tmp_path)
    with pytest.raises(ValueError):
        store.notebook_snapshot(paper_id)
    assert not store.path.exists()


def test_concurrent_first_writes_have_one_winner(tmp_path):
    store = ReaderNotes(tmp_path)
    store.notebook_snapshot('P1')
    barrier = threading.Barrier(4)

    def write(index):
        barrier.wait()
        try:
            return save(store, f'Writer {index}')
        except NoteConflict:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(write, range(4)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert store.notebook_snapshot('P1') == winners[0]


def test_concurrent_updates_have_one_winner(tmp_path):
    store = ReaderNotes(tmp_path)
    save(store)
    barrier = threading.Barrier(4)

    def update(index):
        barrier.wait()
        try:
            return save(store, f'Update {index}', revision=1)
        except NoteConflict:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(update, range(4)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1 and winners[0]['revision'] == 2
    assert store.notebook_snapshot('P1') == winners[0]


def test_notebook_quotas_count_retained_papers_and_utf8_bytes(tmp_path, monkeypatch):
    # Production limits: 10,000 retained paper rows and 20,000,000 UTF-8 bytes
    # per owner, independently of the 50,000-Unicode-character per-paper limit.
    monkeypatch.setattr(reader_notes, 'MAX_NOTEBOOK_PAPERS', 2)
    monkeypatch.setattr(reader_notes, 'MAX_NOTEBOOK_BYTES', 12)
    store = ReaderNotes(tmp_path)
    save(store, '😀😀')  # Eight UTF-8 bytes, two characters.
    save(store, '\x00abc', paper_id='P2')  # NUL must not hide bytes from the quota.
    with pytest.raises(ValueError, match='storage limit'):
        save(store, '😀😀x', 1)
    assert store.notebook_snapshot('P1')['revision'] == 1
    save(store, '', 1)
    assert save(store, '😀😀😀', 1, paper_id='P2')['revision'] == 2
    with pytest.raises(ValueError, match='notebook limit'):
        save(store, '', paper_id='P3')
    assert store.notebook_snapshot('P3')['revision'] == 0
    assert save(ReaderNotes(tmp_path / 'another-owner'), '😀😀😀')['revision'] == 1


def test_concurrent_papers_cannot_overrun_owner_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(reader_notes, 'MAX_NOTEBOOK_BYTES', 16)
    store = ReaderNotes(tmp_path)
    store.notebook_snapshot('P1')
    barrier = threading.Barrier(4)

    def write(index):
        barrier.wait()
        try:
            return save(store, '😀😀', paper_id=f'P{index}')
        except ValueError as exc:
            assert 'storage limit' in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(write, range(4)))
    assert len([result for result in results if result is not None]) == 2
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*), sum(length(CAST(text AS BLOB))) FROM paper_notes').fetchone() == (2, 16)


def test_notebook_migrates_existing_highlight_store_and_ignores_pdf_versions(tmp_path):
    store = ReaderNotes(tmp_path)
    original = store.change({'action': 'add', 'paper_id': 'P1', 'fingerprint': FINGERPRINT, 'mark': MARK})
    # Simulate a pre-notebook database. Its existing annotations must survive.
    with sqlite3.connect(store.path) as db:
        db.execute('DROP TABLE paper_notes')
    saved = save(store)
    assert store.snapshot('P1', FINGERPRINT) == original
    assert store.snapshot('P1', 'b' * 64) == {'items': [], 'other_version_count': 1}
    assert store.notebook_snapshot('P1') == saved
    mark = original['items'][0]
    store.change({'action': 'delete', 'paper_id': 'P1', 'fingerprint': FINGERPRINT, 'id': mark['id'], 'revision': 1})
    assert store.notebook_snapshot('P1') == saved


def test_failed_notebook_write_rolls_back_text_and_revision(tmp_path):
    store = ReaderNotes(tmp_path)
    before = save(store)
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER fail_notebook_update BEFORE UPDATE ON paper_notes BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
    with pytest.raises(sqlite3.DatabaseError):
        save(store, 'Must not replace saved draft', 1)
    assert store.notebook_snapshot('P1') == before
    with sqlite3.connect(store.path) as db:
        db.execute('DROP TRIGGER fail_notebook_update')
    assert save(store, 'Retry succeeds', 1)['revision'] == 2
