from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid


class NoteConflict(ValueError):
    pass


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
    if mark['color'] not in ('yellow', 'mint', 'rose'):
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
            yield db
        finally:
            db.close()

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
