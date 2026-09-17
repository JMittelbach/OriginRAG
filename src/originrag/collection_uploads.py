import sqlite3
from contextlib import closing
from pathlib import Path

from .writing import WritingStore, WritingConflict


class CollectionUploads:
    def __init__(self, path):
        self.path = Path(path)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        self.path.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS links (upload TEXT PRIMARY KEY, directory TEXT, collection TEXT)')
        return db

    def add(self, upload, directory, collection):
        WritingStore(directory).get(collection, 'collections')
        with closing(self.connect()) as db, db:
            db.execute('INSERT INTO links VALUES(?,?,?)', (upload, str(Path(directory).resolve()), collection))

    def discard(self, upload):
        if not self.path.exists():
            return
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM links WHERE upload=?', (upload,))

    def finish(self, results, catalogue):
        if not self.path.exists():
            return
        db = self.connect()
        try:
            for result in results:
                row = db.execute('SELECT directory,collection FROM links WHERE upload=?', (result.get('upload_id', ''),)).fetchone()
                if not row:
                    continue
                result['collection_link_state'] = 'not_added'
                if result.get('status') == 'accepted' and result.get('paper_id'):
                    store = WritingStore(row[0])
                    for attempt in range(3):
                        try:
                            collection = store.get(row[1], 'collections')
                            store.change({'action': 'collection_paper', 'id': row[1], 'revision': collection['revision'], 'paper_id': result['paper_id']}, catalogue)
                            result['collection_link_state'] = 'added'
                            break
                        except WritingConflict:
                            if attempt == 2:
                                result['collection_link_state'] = 'retry_needed'
                        except (KeyError, ValueError, OSError, sqlite3.Error):
                            result['collection_link_state'] = 'retry_needed'
                            break
                if result['collection_link_state'] != 'retry_needed':
                    db.execute('DELETE FROM links WHERE upload=?', (result['upload_id'],))
            db.commit()
        finally:
            db.close()
