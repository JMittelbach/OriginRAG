from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid


# Notebook quotas are per owner and separate from PDF highlights. Empty rows count
# toward the paper limit: retaining their revisions prevents stale-write ABA bugs.
MAX_NOTEBOOK_CHARACTERS = 50_000
MAX_NOTEBOOK_PAPERS = 10_000
MAX_NOTEBOOK_BYTES = 20_000_000


class NoteConflict(ValueError):
    pass


def notebook_paper_key(paper_id):
    if (not isinstance(paper_id, str) or not 1 <= len(paper_id) <= 160
            or not paper_id.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in paper_id)):
        raise ValueError('A valid paper_id is required.')
    paper_id.encode('utf-8')
    return paper_id


def validate_notebook(payload):
    if not isinstance(payload, dict) or set(payload) != {'paper_id', 'text', 'revision'}:
        raise ValueError('Supply paper_id, text and revision only.')
    notebook_paper_key(payload['paper_id'])
    if not isinstance(payload['text'], str) or len(payload['text']) > MAX_NOTEBOOK_CHARACTERS:
        raise ValueError(f'Paper notes must contain at most {MAX_NOTEBOOK_CHARACTERS:,} characters.')
    payload['text'].encode('utf-8')
    # Keep both the supplied and next revision exactly representable in browsers.
    if type(payload['revision']) is not int or not 0 <= payload['revision'] < 2 ** 53 - 1:
        raise ValueError('A valid notebook revision is required.')
    return payload


def document_key(paper_id, fingerprint):
    if not isinstance(paper_id, str) or not 1 <= len(paper_id) <= 160:
        raise ValueError('A paper identifier is required.')
    if not isinstance(fingerprint, str) or not re.fullmatch(r'[a-f0-9]{64}', fingerprint):
        raise ValueError('A PDF fingerprint is required.')
    return paper_id, fingerprint


def validate_mark(mark):
    if not isinstance(mark, dict) or set(mark) != {'page', 'rects', 'quote', 'note', 'color'}:
        raise ValueError('Invalid annotation fields.')
    if type(mark['page']) is not int or not 1 <= mark['page'] <= 20000:
        raise ValueError('Invalid page number.')
    if mark['color'] not in ('yellow', 'mint', 'rose', 'red'):
        raise ValueError('Invalid highlight color.')
    for field, limit in (('quote', 10000), ('note', 10000)):
        if not isinstance(mark[field], str) or len(mark[field]) > limit:
            raise ValueError('Annotation text is too long.')
    if not isinstance(mark['rects'], list) or not 1 <= len(mark['rects']) <= 200:
        raise ValueError('Select a shorter passage.')
    for rect in mark['rects']:
        if not isinstance(rect, list) or len(rect) != 4 or any(type(n) not in (int, float) or not math.isfinite(n) for n in rect):
            raise ValueError('Invalid highlight coordinates.')
        x, y, width, height = rect
        if min(x, y) < 0 or min(width, height) <= 0 or x + width > 1.000001 or y + height > 1.000001:
            raise ValueError('Highlight must stay on its page.')
    return mark


class ReaderNotes:
    def __init__(self, owner_directory):
        self.path = Path(owner_directory) / '.reader_notes.sqlite3'

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS marks (paper TEXT NOT NULL, fingerprint TEXT NOT NULL, id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL, updated INTEGER NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS marks_document ON marks(paper, fingerprint)')
            db.execute('CREATE TABLE IF NOT EXISTS paper_notes (paper TEXT PRIMARY KEY, text TEXT NOT NULL, revision INTEGER NOT NULL, updated INTEGER NOT NULL)')
            yield db
        finally:
            db.close()

    def notebook_snapshot(self, paper_id):
        notebook_paper_key(paper_id)
        with self.connection() as db:
            row = db.execute('SELECT text, revision, updated FROM paper_notes WHERE paper=?', (paper_id,)).fetchone()
        text, revision, updated = row if row else ('', 0, None)
        return {'paper_id': paper_id, 'text': text, 'revision': revision, 'updated_at': updated}

    def change_notebook(self, payload):
        validate_notebook(payload)
        paper, text, revision = payload['paper_id'], payload['text'], payload['revision']
        with self.connection() as db, db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT revision, length(CAST(text AS BLOB)) FROM paper_notes WHERE paper=?', (paper,)).fetchone()
            if revision != (previous[0] if previous else 0):
                raise NoteConflict('These paper notes changed in another window. Reload notes before saving again.')
            count, total_bytes = db.execute('SELECT count(*), coalesce(sum(length(CAST(text AS BLOB))), 0) FROM paper_notes').fetchone()
            if previous is None and count >= MAX_NOTEBOOK_PAPERS:
                raise ValueError('Your paper notebook limit has been reached.')
            if total_bytes - (previous[1] if previous else 0) + len(text.encode('utf-8')) > MAX_NOTEBOOK_BYTES:
                raise ValueError('Your paper notes storage limit has been reached. Shorten notes before saving more.')
            updated = time.time_ns() // 1_000_000
            db.execute('INSERT INTO paper_notes (paper, text, revision, updated) VALUES (?, ?, ?, ?) ON CONFLICT(paper) DO UPDATE SET text=excluded.text, revision=excluded.revision, updated=excluded.updated',
                       (paper, text, revision + 1, updated))
        return {'paper_id': paper, 'text': text, 'revision': revision + 1, 'updated_at': updated}

    def snapshot(self, paper_id, fingerprint):
        document_key(paper_id, fingerprint)
        with self.connection() as db:
            rows = db.execute('SELECT id, revision, payload FROM marks WHERE paper=? AND fingerprint=? ORDER BY updated, id', (paper_id, fingerprint)).fetchall()
            other = db.execute('SELECT count(*) FROM marks WHERE paper=? AND fingerprint!=?', (paper_id, fingerprint)).fetchone()[0]
        return {'items': [{**json.loads(payload), 'id': identifier, 'revision': revision} for identifier, revision, payload in rows], 'other_version_count': other}

    def change(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('Invalid annotation request.')
        action = payload.get('action')
        base = {'action', 'paper_id', 'fingerprint'}
        fields = base | ({'mark'} if action == 'add' else {'id', 'revision', 'mark'} if action == 'update' else {'id', 'revision'} if action == 'delete' else set())
        if action not in ('add', 'update', 'delete') or set(payload) != fields:
            raise ValueError('Invalid annotation action.')
        paper, fingerprint = document_key(payload['paper_id'], payload['fingerprint'])
        if action != 'delete':
            mark = validate_mark(payload['mark'])
        if action != 'add' and (not isinstance(payload['id'], str) or not re.fullmatch(r'[a-f0-9]{32}', payload['id']) or type(payload['revision']) is not int or payload['revision'] < 1):
            raise ValueError('Invalid annotation identifier or revision.')
        with self.connection() as db, db:
            db.execute('BEGIN IMMEDIATE')
            if action == 'add':
                count = db.execute('SELECT count(*) FROM marks WHERE paper=? AND fingerprint=?', (paper, fingerprint)).fetchone()[0]
                total = db.execute('SELECT count(*) FROM marks').fetchone()[0]
                if count >= 1000 or total >= 10000:
                    raise ValueError('Your annotation limit has been reached. Export and remove notes you no longer need.')
                db.execute('INSERT INTO marks VALUES (?, ?, ?, 1, ?, ?)', (paper, fingerprint, uuid.uuid4().hex, json.dumps(mark), time.time_ns()))
            else:
                where = (paper, fingerprint, payload['id'], payload['revision'])
                if action == 'delete':
                    cursor = db.execute('DELETE FROM marks WHERE paper=? AND fingerprint=? AND id=? AND revision=?', where)
                else:
                    cursor = db.execute('UPDATE marks SET revision=revision+1, payload=?, updated=? WHERE paper=? AND fingerprint=? AND id=? AND revision=?', (json.dumps(mark), time.time_ns(), *where))
                if cursor.rowcount != 1:
                    raise NoteConflict('This note changed in another window. Reload notes before saving again.')
        return self.snapshot(paper, fingerprint)
