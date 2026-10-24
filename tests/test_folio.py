import base64
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from originrag import folio
from originrag.folio import FolioConflict, FolioStore, document_html, paragraph, validate_content


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch):
    monkeypatch.delenv('ORIGINRAG_WORKSPACE_STATE_DIR', raising=False)


def doc(*nodes):
    return {'type': 'doc', 'content': list(nodes) or [paragraph('A document')]}


def test_lifecycle_owner_isolation_revisions_and_private_storage(tmp_path):
    alice, bob = FolioStore(tmp_path / 'alice'), FolioStore(tmp_path / 'bob')
    original = alice.change({'action': 'create', 'title': 'A report', 'template': 'report'})
    assert original['revision'] == 1
    assert alice.get(original['id']) == original
    assert bob.snapshot() == {'documents': []}
    with pytest.raises(KeyError):
        bob.get(original['id'])
    for action in ('save', 'duplicate', 'delete'):
        with pytest.raises(KeyError):
            bob.change({**original, 'action': action})
    saved = alice.change({**original, 'action': 'save', 'content': doc(paragraph('Updated'))})
    assert saved['revision'] == 2
    for action in ('save', 'duplicate', 'delete'):
        with pytest.raises(FolioConflict):
            alice.change({**original, 'action': action})
    copied = alice.change({'action': 'duplicate', 'id': saved['id'], 'revision': saved['revision']})
    assert copied['content'] == saved['content'] and copied['id'] != saved['id']
    assert copied['revision'] == 1 and copied['title'] == 'A report (copy)'
    assert alice.change({**saved, 'action': 'delete'}) == {'id': saved['id'], 'deleted': True}
    assert alice.snapshot()['documents'][0]['id'] == copied['id']
    assert 'content' not in alice.snapshot()['documents'][0]
    assert alice.path.stat().st_mode & 0o777 == 0o600
    assert alice.path.parent.stat().st_mode & 0o777 == 0o700


def test_concurrent_save_has_one_winner(tmp_path):
    store = FolioStore(tmp_path)
    original = store.change({'action': 'create'})

    def save(title):
        try:
            return store.change({**original, 'action': 'save', 'title': title})['revision']
        except FolioConflict:
            return 'conflict'

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save, ['First writer', 'Second writer']))
    assert sorted(map(str, outcomes)) == ['2', 'conflict']
    assert store.get(original['id'])['revision'] == 2


def test_connections_close_and_quota_counts_utf8_bytes(tmp_path, monkeypatch):
    store = FolioStore(tmp_path)
    document = store.change({'action': 'create'})
    with store.connect() as db:
        data, size = db.execute('SELECT data,byte_size FROM folio_documents').fetchone()
        assert len(data.encode()) == size
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute('SELECT 1')
    monkeypatch.setattr(folio, 'MAX_OWNER_BYTES', size + 1)
    with pytest.raises(ValueError, match='storage limit'):
        store.change({**document, 'action': 'save', 'content': doc(paragraph('é' * 1000))})
    assert store.get(document['id']) == document


@pytest.mark.parametrize('key', ['../outside', 'X' * 32, 'a' * 31, {}, [], None])
def test_identifiers_never_become_paths(tmp_path, key):
    with pytest.raises(ValueError):
        FolioStore(tmp_path).get(key)


def test_symlink_database_is_rejected(tmp_path):
    elsewhere = tmp_path / 'other.sqlite3'
    elsewhere.write_bytes(b'do not change')
    (tmp_path / '.folio.sqlite3').symlink_to(elsewhere)
    with pytest.raises(OSError):
        FolioStore(tmp_path).snapshot()
    assert elsewhere.read_bytes() == b'do not change'


@pytest.mark.parametrize('href', ['javascript:alert(1)', 'data:text/html,hello', '//example.com',
                                'file:///secret', 'https://good.com\n.evil', 'https:\\evil',
                                'https://', 'https://[bad'])
def test_unsafe_links_rejected(href):
    with pytest.raises(ValueError):
        validate_content(doc({'type': 'paragraph', 'content': [
            {'type': 'text', 'text': 'click', 'marks': [{'type': 'link', 'attrs': {'href': href}}]}]}))


@pytest.mark.parametrize('node', [
    {'type': 'script', 'text': 'alert(1)'}, {'type': []},
    {'type': 'paragraph', 'attrs': {'textAlign': {}}},
    {'type': 'paragraph', 'attrs': {'lineHeight': 'url(https://evil)'}},
    {'type': 'paragraph', 'attrs': {'indent': True}},
    {'type': 'paragraph', 'content': [{'type': 'text', 'text': '', 'marks': []}]},
    {'type': 'paragraph', 'content': [{'type': 'text', 'text': '\x00'}]},
    {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'x', 'marks': [{'type': []}]}]},
    {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'x', 'marks': [{'type': 'textStyle', 'attrs': {'fontFamily': 'a;position:fixed'}}]}]},
    {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'x', 'marks': [{'type': 'textStyle', 'attrs': {'fontSize': '999px'}}]}]},
    {'type': 'image', 'attrs': {'src': 'https://example.com/pixel.png'}},
    {'type': 'image', 'attrs': {'src': 'data:image/svg+xml;base64,PHN2Zz4='}},
    {'type': 'image', 'attrs': {'src': 'data:image/png;base64,Ym9ndXM='}},
])
def test_invalid_json_and_images_rejected(node):
    with pytest.raises(ValueError):
        validate_content(doc(node))


def test_editor_spaces_and_unknown_attributes_are_safe():
    value = doc({'type': 'paragraph', 'attrs': {'onclick': 'alert(1)'}, 'content': [
        {'type': 'text', 'text': ' ', 'marks': [{'type': 'bold', 'attrs': {'style': 'bad'}}]},
        {'type': 'text', 'text': '<script>&"', 'marks': [{'type': 'link', 'attrs': {'href': 'https://example.com', 'onclick': 'bad'}}]},
    ]})
    clean = validate_content(value)
    assert 'onclick' not in json.dumps(clean) and 'style' not in json.dumps(clean)
    rendered = document_html({'title': '<title>', 'content': clean, 'page': folio.DEFAULT_PAGE})
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered
    assert 'noopener noreferrer' in rendered


def test_bounded_depth_and_node_count():
    node = paragraph('Bottom')
    for _ in range(34):
        node = {'type': 'blockquote', 'content': [node]}
    with pytest.raises(ValueError, match='complex'):
        validate_content(doc(node))
    with pytest.raises(ValueError, match='complex'):
        validate_content(doc(*[paragraph()] * 20_001))


def test_image_canvas_is_bounded_even_when_encoded_file_is_tiny():
    raw = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + (999_999).to_bytes(4, 'big') * 2 + b'\x00' * 9
    image = {'type': 'image', 'attrs': {'src': 'data:image/png;base64,' + base64.b64encode(raw).decode()}}
    with pytest.raises(ValueError, match='megapixels'):
        validate_content(doc(image))


def test_comment_validation_and_json_round_trip(tmp_path):
    store = FolioStore(tmp_path)
    original = store.change({'action': 'create', 'title': 'Original'})
    comments = [{'id': 'c-1', 'text': '<script>literal comment</script>', 'quote': 'A quote',
                 'resolved': False, 'created_at': '2026-10-08T10:00:00Z'}]
    saved = store.change({**original, 'action': 'save', 'comments': comments})
    data, mime, filename = store.export(saved['id'], 'json')
    imported = store.change({'action': 'import', 'filename': filename,
                             'content_base64': base64.b64encode(data).decode()})
    assert imported['comments'] == comments and imported['content'] == saved['content']
    assert imported['id'] != saved['id'] and imported['revision'] == 1
    assert mime.startswith('application/json')
    with pytest.raises(ValueError):
        store.change({**saved, 'action': 'save', 'comments': comments + comments})


@pytest.mark.parametrize('template', ['blank', 'report', 'letter', 'meeting'])
def test_templates_and_exports(tmp_path, template):
    store = FolioStore(tmp_path)
    saved = store.change({'action': 'create', 'template': template, 'title': 'Safe " filename'})
    assert validate_content(saved['content']) == saved['content']
    for format in ('html', 'txt', 'json', 'docx'):
        data, mime, name = store.export(saved['id'], format)
        assert data and mime and '"' not in name and name.endswith('.' + format)


@pytest.mark.parametrize('revision', [None, 0, -1, True, False, 1.0, '1', [], {}])
def test_export_revision_must_be_a_positive_integer(tmp_path, revision):
    store = FolioStore(tmp_path)
    created = store.change({'action': 'create'})
    with pytest.raises(ValueError, match='Export revision must be a positive integer'):
        store.export(created['id'], 'json', revision=revision)


@pytest.mark.parametrize('format', ['json', 'html', 'txt', 'docx'])
def test_guarded_export_rejects_stale_revisions_and_keeps_legacy_calls(tmp_path, format):
    store = FolioStore(tmp_path)
    created = store.change({'action': 'create'})
    saved = store.change({**created, 'action': 'save', 'content': doc(paragraph('Newer text'))})
    with pytest.raises(FolioConflict, match='changed before export'):
        store.export(created['id'], format, revision=created['revision'])
    assert store.export(saved['id'], format, revision=saved['revision'])[0]
    assert store.export(saved['id'], format)[0]


def test_export_serializes_the_checked_snapshot_after_a_concurrent_save(tmp_path, monkeypatch):
    store = FolioStore(tmp_path)
    original = store.change({'action': 'create', 'title': 'Checked snapshot'})
    get = store.get
    reads = []

    def get_then_save(key):
        snapshot = get(key)
        reads.append(snapshot)
        store.change({**snapshot, 'action': 'save', 'title': 'Concurrent edit',
                      'content': doc(paragraph('Newer content'))})
        return snapshot

    monkeypatch.setattr(store, 'get', get_then_save)
    data, _, filename = store.export(original['id'], 'json', revision=original['revision'])
    assert len(reads) == 1
    assert json.loads(data) == original
    assert filename == 'Checked snapshot.json'
    assert get(original['id'])['revision'] == original['revision'] + 1


@pytest.mark.parametrize('format', ['html', 'docx'])
def test_export_passes_the_same_checked_object_to_the_serializer(tmp_path, monkeypatch, format):
    from originrag import folio_docx
    store = FolioStore(tmp_path)
    snapshot = store.change({'action': 'create'})
    reads, serialized = [], []

    def get(key):
        assert key == snapshot['id']
        reads.append(key)
        return snapshot

    def serialize(document):
        assert document is snapshot
        serialized.append(document)
        return 'snapshot' if format == 'html' else b'snapshot'

    monkeypatch.setattr(store, 'get', get)
    monkeypatch.setattr(folio if format == 'html' else folio_docx,
                        'document_html' if format == 'html' else 'export_docx', serialize)
    assert store.export(snapshot['id'], format, revision=snapshot['revision'])[0] == b'snapshot'
    assert len(reads) == len(serialized) == 1
