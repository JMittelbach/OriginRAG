from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .writing import WritingStore, identifier
from .workspace_storage import workspace_database


LOCAL_COLLABORATION = {'enabled': False, 'role': 'local', 'owner': None,
                       'can_manage': False, 'can_sync_bibliography': True, 'shared': False}
MAX_EDITORS = 20


def user_identifier(value):
    if not isinstance(value, str):
        raise ValueError('Invalid account identifier.')
    try:
        valid = str(uuid.UUID(value)) == value
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Invalid account identifier.')
    return value


@dataclass
class ProjectAccess:
    store: WritingStore
    info: dict

    def public(self, project):
        result = {**project, 'collaboration': self.info}
        if self.info['role'] == 'editor':
            result['collection'] = ''
            result.pop('bibliography_sync', None)
        return result


class WritingCollaboration:
    def __init__(self, directory, auth_store):
        self.directory = Path(directory).resolve()
        self.path = workspace_database(self.directory, '.writing-collaboration.sqlite3')
        self.auth_store = auth_store

    @contextmanager
    def connect(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise OSError('Unsafe collaboration database path.')
        db = sqlite3.connect(self.path, timeout=10)
        try:
            os.chmod(self.path, 0o600)
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, owner TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS editors (project TEXT NOT NULL REFERENCES projects(id), '
                       'user TEXT NOT NULL, PRIMARY KEY(project,user))')
            db.execute('CREATE INDEX IF NOT EXISTS editors_by_user ON editors(user)')
            db.commit()
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def user(self, user_id):
        with self.auth_store.connect() as db:
            row = db.execute('SELECT user_id,username,display_name FROM web_users '
                             'WHERE user_id=? AND disabled_at IS NULL', (user_identifier(user_id),)).fetchone()
        if row is None:
            raise KeyError('Account not available.')
        return dict(row)

    def search(self, user, query):
        self.user(user.user_id)
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 80:
            raise ValueError('Enter 2–80 characters to find an account.')
        term = '%' + query.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        with self.auth_store.connect() as db:
            rows = db.execute("SELECT user_id,username,display_name FROM web_users WHERE disabled_at IS NULL "
                              "AND user_id != ? AND (username LIKE ? ESCAPE '\\' OR display_name LIKE ? ESCAPE '\\') "
                              'ORDER BY username LIMIT 20', (user.user_id, term, term)).fetchall()
        return {'users': [dict(row) for row in rows]}

    def store(self, owner_id):
        owner_id = user_identifier(owner_id)
        users = self.directory / 'users'
        directory = users / owner_id
        if users.is_symlink() or directory.is_symlink() or (directory / '.writing.sqlite3').is_symlink():
            raise OSError('Unsafe account writing path.')
        if directory.resolve().parent != users.resolve():
            raise OSError('Unsafe account writing path.')
        return WritingStore(directory)

    @staticmethod
    def _revision(store, key):
        return store.revision(key)

    def _access(self, db, user, key, *, owner_only=False):
        key = identifier(key)
        self.user(user.user_id)
        own = self.store(user.user_id)
        try:
            self._revision(own, key)
        except KeyError:
            pass
        else:
            db.execute('INSERT OR IGNORE INTO projects(id,owner) VALUES (?,?)', (key, user.user_id))
        row = db.execute('SELECT owner FROM projects WHERE id=?', (key,)).fetchone()
        if row is None:
            raise KeyError('Project not found.')
        owner_id = row[0]
        is_owner = owner_id == user.user_id
        if not is_owner and not db.execute('SELECT 1 FROM editors WHERE project=? AND user=?', (key, user.user_id)).fetchone():
            raise KeyError('Project not found.')
        if owner_only and not is_owner:
            raise PermissionError('Only the project owner can change sharing or synchronise their personal library.')
        owner = self.user(owner_id)
        store = own if is_owner else self.store(owner_id)
        self._revision(store, key)
        info = {'enabled': True, 'role': 'owner' if is_owner else 'editor', 'owner': owner,
                'can_manage': is_owner, 'can_sync_bibliography': is_owner,
                'shared': bool(db.execute('SELECT 1 FROM editors WHERE project=? LIMIT 1', (key,)).fetchone())}
        return ProjectAccess(store, info)

    @contextmanager
    def access(self, user, key, *, owner_only=False):
        with self.connect() as db:
            yield self._access(db, user, key, owner_only=owner_only)

    def snapshot(self, user, own_store):
        snapshot = own_store.snapshot()
        with self.connect() as db:
            owner = self.user(user.user_id)
            shared_ids = {row[0] for row in db.execute('SELECT DISTINCT project FROM editors')}
            for item in snapshot['projects']:
                db.execute('INSERT OR IGNORE INTO projects(id,owner) VALUES (?,?)', (item['id'], user.user_id))
                recorded = db.execute('SELECT owner FROM projects WHERE id=?', (item['id'],)).fetchone()
                if recorded[0] != user.user_id:
                    raise KeyError('Project ownership mismatch.')
                item['collaboration'] = {'enabled': True, 'role': 'owner', 'owner': owner,
                    'can_manage': True, 'can_sync_bibliography': True, 'shared': item['id'] in shared_ids}
            shared = db.execute('SELECT p.id,p.owner FROM projects p JOIN editors e ON e.project=p.id '
                                'WHERE e.user=? AND p.owner != ?', (user.user_id, user.user_id)).fetchall()
            for key, _ in shared:
                try:
                    access = self._access(db, user, key)
                    source = access.store.connect()
                    try:
                        row = source.execute("SELECT data,revision,updated FROM documents WHERE id=? AND kind='projects'", (key,)).fetchone()
                    finally:
                        source.close()
                    data = json.loads(row[0])
                    snapshot['projects'].append({'id': key, 'name': data['name'], 'revision': row[1], 'updated': row[2],
                        'count': len(data.get('files', {})), 'collection': '', 'template': data.get('template', ''),
                        'collaboration': access.info})
                except KeyError:
                    continue
        snapshot['projects'].sort(key=lambda item: item.get('updated', 0), reverse=True)
        return snapshot

    def _members(self, db, key, access):
        members = []
        for row in db.execute('SELECT user FROM editors WHERE project=? ORDER BY user', (key,)):
            try:
                members.append({**self.user(row[0]), 'role': 'editor'})
            except KeyError:
                continue
        members.sort(key=lambda item: item['username'])
        return {'id': key, 'collaboration': access.info, 'members': members, 'limit': MAX_EDITORS}

    def members(self, user, key):
        with self.connect() as db:
            access = self._access(db, user, key)
            return self._members(db, key, access)

    def change(self, user, payload):
        if set(payload) != {'action', 'id', 'user_id'} or payload.get('action') not in {'add', 'remove'}:
            raise ValueError('Choose an account to add or remove.')
        key, target = identifier(payload['id']), user_identifier(payload['user_id'])
        with self.connect() as db:
            access = self._access(db, user, key, owner_only=True)
            if target == user.user_id:
                raise ValueError('The project owner already has access.')
            if payload['action'] == 'add':
                self.user(target)
                present = db.execute('SELECT 1 FROM editors WHERE project=? AND user=?', (key, target)).fetchone()
                count = db.execute('SELECT COUNT(*) FROM editors WHERE project=?', (key,)).fetchone()[0]
                if not present and count >= MAX_EDITORS:
                    raise ValueError('A project can have up to 20 editors.')
                db.execute('INSERT OR IGNORE INTO editors(project,user) VALUES (?,?)', (key, target))
            else:
                db.execute('DELETE FROM editors WHERE project=? AND user=?', (key, target))
            access.info['shared'] = bool(db.execute('SELECT 1 FROM editors WHERE project=? LIMIT 1', (key,)).fetchone())
            return self._members(db, key, access)
