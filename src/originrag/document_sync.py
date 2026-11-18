"""Exchange individual saved documents through a user's existing synced folder."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import uuid

from .folio import FolioStore
from .writing import WritingStore


KEY = re.compile(r'[0-9a-f]{32}')
FILE = re.compile(r'([0-9a-f]{32})\.([0-9a-f]{32})\.origin1')
MAX_BYTES = 32_000_000
SUBFOLDER = 'Origin Documents'
FIELDS = {'write': ('name', 'main', 'files'), 'folio': ('title', 'page', 'comments', 'content')}


class SyncConflict(ValueError):
    pass


def suggestions():
    home = Path.home()
    cloud = home / 'Library/CloudStorage'
    paths = [home / 'Library/Mobile Documents/com~apple~CloudDocs', home / 'Dropbox',
             home / 'Nextcloud', home / 'Sync']
    if cloud.is_dir():
        paths.extend(sorted(cloud.iterdir())[:50])
    return list(dict.fromkeys(str(p) for p in paths if p.is_dir()))


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def covers(a, b):
    return all(a.get(k, 0) >= n for k, n in b.items())


def portable(kind, doc):
    return {k: doc[k] for k in FIELDS[kind]}


def snapshot(path, with_body=False):
    match = FILE.fullmatch(path.name)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES + 8192:
            raise ValueError('Invalid sync file.')
        header = handle.readline(8193)
        if len(header) > 8192 or not header.endswith(b'\n'):
            raise ValueError('Incomplete sync file. Wait for the folder to finish syncing.')
        meta = json.loads(header)
        if not isinstance(meta, dict) or set(meta) != {'id', 'device', 'kind', 'title', 'hash', 'clock'}:
            raise ValueError('Invalid sync record.')
        clock = meta['clock']
        if (not match or (meta['id'], meta['device']) != match.groups()
                or meta['kind'] not in FIELDS or not isinstance(meta['title'], str) or len(meta['title']) > 180
                or not re.fullmatch(r'[0-9a-f]{64}', str(meta['hash']))
                or not isinstance(clock, dict) or not 1 <= len(clock) <= 64 or meta['device'] not in clock
                or not all(KEY.fullmatch(k) and type(v) is int and 0 < v < 2**53 for k, v in clock.items())):
            raise ValueError('Invalid sync record.')
        if not with_body:
            return meta
        body = handle.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES or digest(body) != meta['hash']:
            raise ValueError('Incomplete sync file. Wait for the folder to finish syncing.')
        doc = json.loads(body)
        if not isinstance(doc, dict) or set(doc) != set(FIELDS[meta['kind']]):
            raise ValueError('Invalid synced document.')
        return meta, doc


def newest(records):
    if len(records) > 64:
        raise ValueError('Too many device versions for one document.')
    return [r for r in records if not any(o['clock'] != r['clock'] and covers(o['clock'], r['clock'])
                                          for o in records)]


def publish(folder, key, device, kind, title, clock, body):
    if len(body) > MAX_BYTES or len(clock) > 64:
        raise ValueError('Document or device limit exceeded.')
    meta = dict(id=key, device=device, kind=kind, title=title, clock=clock, hash=digest(body))
    with tempfile.NamedTemporaryFile(dir=folder, prefix='.origin-', delete=False) as handle:
        tmp = Path(handle.name)
        try:
            handle.write(encode(meta) + b'\n')
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, folder / f'{key}.{device}.origin1')
    finally:
        tmp.unlink(missing_ok=True)
    return meta


class DocumentSync:
    def __init__(self, owner, profile, catalogue):
        self.owner = Path(owner)
        self.profile = profile
        self.catalogue = catalogue

    def run(self, request):
        action = request.get('action')
        if action not in ('status', 'connect', 'disconnect', 'sync', 'auto', 'open', 'copy'):
            raise ValueError('Unknown sync action.')
        dbpath = self.owner / '.document-sync.sqlite3'
        if action == 'status' and not dbpath.exists():
            return {'folder': '', 'suggestions': suggestions(), 'documents': []}
        self.owner.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(dbpath, timeout=6)) as db, db:
            dbpath.chmod(0o600)
            db.row_factory = sqlite3.Row
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, device TEXT, folder TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS links (folder TEXT, remote TEXT, kind TEXT, local TEXT, hash TEXT, clock TEXT, PRIMARY KEY(folder,remote), UNIQUE(folder,kind,local))')
            db.execute("INSERT OR IGNORE INTO settings VALUES(1,?,'')", (uuid.uuid4().hex,))
            device, selected = db.execute('SELECT device,folder FROM settings WHERE id=1').fetchone()
            if action == 'disconnect':
                db.execute("UPDATE settings SET folder='' WHERE id=1")
                selected = ''
            if action == 'connect':
                raw = request.get('folder')
                if not isinstance(raw, str) or not raw.strip() or len(raw) > 4096:
                    raise ValueError('Choose an existing synced folder on this computer.')
                root = Path(raw.strip()).expanduser()
                if not root.is_absolute() or not root.is_dir() or root.resolve() in (Path('/'), Path.home().resolve()):
                    raise ValueError('Choose a specific existing folder, not your home or filesystem root.')
                dest = root if root.name == SUBFOLDER else root.resolve() / SUBFOLDER
                if dest.is_symlink():
                    raise ValueError('Choose a folder without a linked Origin Documents directory.')
                dest.mkdir(mode=0o700, exist_ok=True)
                selected = str(dest.resolve())
                db.execute('UPDATE settings SET folder=? WHERE id=1', (selected,))
            folder = Path(selected) if selected else None
            if folder and (folder.resolve() != folder or not folder.is_dir()) and action != 'status':
                raise ValueError('Sync folder unavailable. Reconnect the drive or disconnect this folder.')
            if action in ('status', 'connect', 'disconnect'):
                records = {}
                available = bool(folder and folder.resolve() == folder and folder.is_dir())
                unreadable = 0
                if available:
                    for path in folder.glob('*.origin1'):
                        if not FILE.fullmatch(path.name):
                            continue
                        try:
                            record = snapshot(path)
                        except (ValueError, TypeError, RecursionError, OSError):
                            unreadable += 1
                            continue
                        if record['kind'] == 'folio' and not self.profile.folio:
                            continue
                        records.setdefault(record['id'], []).append(record)
                docs = []
                for rows in records.values():
                    if len(rows) > 64:
                        unreadable += len(rows)
                        continue
                    heads = newest(rows)
                    docs.extend({'id': r['id'], 'kind': r['kind'], 'device': r['device'], 'title': r['title'],
                                 'conflict': len(heads) > 1} for r in heads)
                return {'folder': str(folder.parent) if folder else '', 'available': available, 'unreadable': unreadable,
                        'suggestions': [] if folder else suggestions(),
                        'documents': sorted(docs, key=lambda r: r['title'].casefold())}
            if not folder:
                if action == 'auto':
                    return {'skipped': True}
                raise ValueError('Connect a sync folder first.')
            return self.exchange(db, folder, device, request)

    def exchange(self, db, folder, device, req):
        kind, key, action = req.get('kind'), req.get('id'), req['action']
        if kind not in FIELDS or (kind == 'folio' and not self.profile.folio) or not isinstance(key, str) or not KEY.fullmatch(key):
            raise ValueError('Choose a document from this workspace.')
        store = (WritingStore if kind == 'write' else FolioStore)(self.owner)
        field = 'local' if action in ('sync', 'auto') else 'remote'
        link = db.execute(f'SELECT * FROM links WHERE folder=? AND kind=? AND {field}=?',
                          (str(folder), kind, key)).fetchone()
        if action == 'auto' and not link:
            return {'skipped': True}
        remote = link['remote'] if link else key
        local = link['local'] if link else key if action == 'sync' else None
        rows = [snapshot(p) for p in folder.glob(f'{remote}.*.origin1') if FILE.fullmatch(p.name)]
        if any(r['kind'] != kind for r in rows):
            raise ValueError('Document type mismatch.')
        heads = newest(rows)
        if action == 'copy':
            heads = [r for r in heads if r['device'] == req.get('device')]
            local = None
        if len(heads) > 1:
            raise SyncConflict('Both computers edited this document. Both versions are safe. Open a separate copy to compare them.')
        head = heads[0] if heads else None
        if not head and (link or action != 'sync'):
            raise SyncConflict('The shared version is not available yet. Wait for your folder to sync.')
        current = store.get(local) if local else None
        body = encode(portable(kind, current)) if current else None
        curr_hash = digest(body) if current else None
        base = link['hash'] if link else None
        if head and link and action != 'copy':
            prior = json.loads(link['clock'])
            if not covers(head['clock'], prior) or (head['clock'] == prior and head['hash'] != base):
                raise SyncConflict('The folder is behind or inconsistent. Wait for it to finish syncing.')
        message, received = 'Up to date.', False
        if head and curr_hash != head['hash'] and (not current or head['hash'] != base):
            if current and curr_hash != base:
                raise SyncConflict('Both versions changed. Your local edits are safe. Open a separate copy of the shared version to compare.')
            if action == 'auto':
                raise SyncConflict('A newer version is available. Choose Sync this document to receive it.')
            latest, incoming = snapshot(folder / f"{remote}.{head['device']}.origin1", with_body=True)
            if latest != head:
                raise SyncConflict('The shared version changed. Try again.')
            if action == 'copy':
                name = 'name' if kind == 'write' else 'title'
                incoming[name] = (incoming[name][:113 if kind == 'write' else 173] + ' (copy)')
            args = {**incoming, 'action': ('save_project' if current else 'create_project') if kind == 'write'
                    else ('save' if current else 'create')}
            if current:
                args.update(id=local, revision=current['revision'])
            if kind == 'write':
                args['collection'] = current.get('collection', '') if current else ''
                saved = store.change(args, self.catalogue() if args['collection'] else (), from_document_sync=True)
            else:
                saved = store.change(args, from_document_sync=True)
            local = saved['id']
            received = bool(current)
            message = 'Shared version opened.' if not current else 'Received the updated document.'
        elif not head or curr_hash != head['hash']:
            clock = dict(head['clock']) if head else {}
            clock[device] = clock.get(device, 0) + 1
            head = publish(folder, remote, device, kind, current.get('name') or current.get('title'), clock, body)
            message = 'Saved to the sync folder.'
        if action != 'copy':
            db.execute('INSERT OR REPLACE INTO links VALUES(?,?,?,?,?,?)',
                       (str(folder), remote, kind, local, head['hash'], json.dumps(head['clock'])))
        return {'id': local, 'url': f'/{kind}?id={local}', 'message': message, 'received': received}
