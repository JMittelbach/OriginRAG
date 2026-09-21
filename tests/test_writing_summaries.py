import json
import sqlite3

import pytest

from originrag.writing import WritingConflict, WritingStore


CATALOGUE = [{'paper_id': 'P1', 'title': 'An example paper'}]


def create(store, action='create_project', **fields):
    return store.change({'action': action, 'name': 'Test manuscript', **fields}, CATALOGUE)


def reject_source_reads(db):
    def authorise(action, table, column, *_):
        if action == sqlite3.SQLITE_READ and table == 'documents' and column == 'data':
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    db.set_authorizer(authorise)
    return db


def test_dashboard_and_project_summary_do_not_read_sources(tmp_path, monkeypatch):
    store = WritingStore(tmp_path)
    collection = create(store, 'create_collection', name='Personal library')
    store.change({**collection, 'action': 'collection_paper', 'paper_id': 'P1'}, CATALOGUE)
    project = create(store, collection=collection['id'])
    project = store.change({**project, 'action': 'save_project',
                           'files': {'main.tex': 'x' * 2_000_000, 'references.bib': ''}}, CATALOGUE)
    connect = store.connect
    monkeypatch.setattr(store, 'connect', lambda: reject_source_reads(connect()))
    snapshot = store.snapshot()
    summary = snapshot['projects'][0]
    assert summary == store.summary(project['id'])
    assert summary['name'] == project['name'] and summary['count'] == 2
    assert summary['revision'] == project['revision'] and summary['collection'] == collection['id']
    assert summary['template'] == '' and summary['updated'] > 0
    assert snapshot['collections'][0]['count'] == 1
    assert len(json.dumps(snapshot['projects'])) < 500
    summary['name'] = 'Changed only in memory'
    assert store.summary(project['id'])['name'] == project['name']
    with pytest.raises(KeyError):
        store.summary(collection['id'])


def test_legacy_database_backfills_without_changing_manuscripts(tmp_path, monkeypatch):
    store = WritingStore(tmp_path)
    project = create(store)
    collection = create(store, 'create_collection')
    db = store.connect()
    try:
        before = db.execute('SELECT * FROM documents ORDER BY id').fetchall()
        with db:
            db.execute('DROP TABLE writing_summaries')
    finally:
        db.close()
    snapshot = store.snapshot()
    assert snapshot['projects'][0]['id'] == project['id']
    assert snapshot['collections'][0]['id'] == collection['id']
    assert store.get(project['id']) == project
    db = store.connect()
    try:
        assert db.execute('SELECT * FROM documents ORDER BY id').fetchall() == before
        assert db.execute('SELECT COUNT(*) FROM writing_summaries').fetchone()[0] == 2
    finally:
        db.close()
    connect = store.connect
    monkeypatch.setattr(store, 'connect', lambda: reject_source_reads(connect()))
    assert store.snapshot() == snapshot


def test_older_writer_revision_invalidates_cached_metadata(tmp_path):
    store = WritingStore(tmp_path)
    project = create(store)
    db = store.connect()
    try:
        with db:
            data = {**project, 'name': 'Updated by another software version', 'files': {'main.tex': 'Updated'}}
            db.execute('UPDATE documents SET data=?,revision=revision+1 WHERE id=?', (json.dumps(data), project['id']))
    finally:
        db.close()
    summary = store.summary(project['id'])
    assert summary['revision'] == 2 and summary['count'] == 1
    assert summary['name'] == 'Updated by another software version'


def test_backfill_rereads_after_concurrent_edit(tmp_path, monkeypatch):
    store = WritingStore(tmp_path)
    writer = WritingStore(tmp_path)
    project = create(store)
    db = store.connect()
    try:
        with db:
            db.execute('DELETE FROM writing_summaries')
    finally:
        db.close()
    connect = store.connect
    edited = []

    def connection():
        db = connect()

        def trace(sql):
            if sql == 'BEGIN IMMEDIATE' and not edited:
                edited.append(writer.change({**project, 'action': 'save_project', 'files': {'main.tex': 'Latest'}}, CATALOGUE))
        db.set_trace_callback(trace)
        return db

    monkeypatch.setattr(store, 'connect', connection)
    summary = store.summary(project['id'])
    assert edited and summary['revision'] == edited[0]['revision']
    assert summary['count'] == 1


def test_summary_updates_and_deletion_are_atomic(tmp_path, monkeypatch):
    store = WritingStore(tmp_path)
    project = create(store)
    original = store.summary(project['id'])

    def fail_summary(*args):
        raise sqlite3.OperationalError('Simulated storage failure')

    with monkeypatch.context() as patch:
        patch.setattr(store, '_remember_summary', fail_summary)
        with pytest.raises(sqlite3.OperationalError):
            store.change({**project, 'action': 'save_project', 'files': {'main.tex': 'Do not save'}}, CATALOGUE)
    assert store.get(project['id']) == project
    assert store.summary(project['id']) == original
    saved = store.change({**project, 'action': 'save_project', 'files': {'main.tex': 'Saved'}}, CATALOGUE)
    assert store.summary(project['id'])['count'] == 1
    with pytest.raises(WritingConflict):
        store.change({**project, 'action': 'save_project'}, CATALOGUE)
    assert store.summary(project['id'])['revision'] == saved['revision']
    store.change({**saved, 'action': 'delete_project', 'confirm_name': saved['name'], 'confirm_delete': True}, CATALOGUE)
    assert store.snapshot()['projects'] == []
    with pytest.raises(KeyError):
        store.summary(project['id'])
    db = store.connect()
    try:
        assert db.execute('SELECT COUNT(*) FROM writing_summaries').fetchone()[0] == 0
    finally:
        db.close()


def test_collection_rename_membership_and_bibliography_keep_summaries_current(tmp_path):
    store = WritingStore(tmp_path)
    collection = create(store, 'create_collection')
    collection = store.change({**collection, 'action': 'rename_collection', 'name': 'New collection'}, CATALOGUE)
    collection = store.change({**collection, 'action': 'collection_paper', 'paper_id': 'P1'}, CATALOGUE)
    project = create(store, collection=collection['id'])
    preview = store.bibliography_preview(project['id'], CATALOGUE)
    project = store.change({**project, 'action': 'sync_bibliography', 'collection_revision': collection['revision'],
                            'preview_token': preview['preview_token']}, CATALOGUE)
    snapshot = store.snapshot()
    assert snapshot['collections'][0]['name'] == 'New collection'
    assert snapshot['collections'][0]['count'] == 1
    assert snapshot['projects'][0]['revision'] == project['revision']
    assert snapshot['projects'][0]['count'] == len(project['files'])
    store.change({**collection, 'action': 'collection_paper', 'paper_id': 'P1', 'remove': True}, CATALOGUE)
    assert store.snapshot()['collections'][0]['count'] == 0
