"""Folio's public HTTP boundary: authentication, ownership, and revisions."""

import base64
import json
import io
from pathlib import Path
import shutil
import threading
from urllib.parse import urlencode
import zipfile

import pytest

import test_web_chat as http


PASSWORD = 'quiet-lantern-river-orbit-47'
CONTENT = {'type': 'doc', 'content': [
    {'type': 'paragraph', 'content': [
        {'type': 'text', 'text': 'Private evidence & an observation.', 'marks': [{'type': 'bold'}]}
    ]}
]}


@pytest.fixture
def folio_server(tmp_path, monkeypatch, request):
    configuration = getattr(request, 'param', {})
    if isinstance(configuration, bool):
        configuration = {'auth': configuration}
    root = Path(__file__).resolve().parents[1]
    for folder in ('folio', 'workspace'):
        shutil.copytree(root / 'assets' / folder, tmp_path / 'assets' / folder,
                        ignore=shutil.ignore_patterns('editor-build', 'node_modules'))
    web = http.web_chat
    monkeypatch.delenv('ORIGINRAG_WORKSPACE_STATE_DIR', raising=False)
    monkeypatch.setattr(web, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(web, 'load_project_env', lambda *args: None)
    monkeypatch.setattr(web, 'library_catalog', lambda: ())
    profile = configuration.get('profile', 'full')
    settings = web.WebChatSettings(backend='template', app_profile=profile)
    auth = http.AuthStore(tmp_path / 'auth.sqlite') if configuration.get('auth') else None
    if auth:
        for name in ('alice', 'bob'):
            auth.create_user(name, PASSWORD)
    registry = web.AuthenticatedWebChatRegistry(settings, tmp_path / 'sessions') if auth else None
    app = None if auth else web.WebChatApp(settings, tmp_path / 'sessions')
    handler = web.build_handler(app, auth_store=auth, app_registry=registry,
                                secure_cookies=False, profile=profile)
    server = web.create_server('127.0.0.1', 0, handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    users = {name: http.login_headers(port, name, PASSWORD)[0]
             for name in ('alice', 'bob')} if auth else {}
    try:
        yield port, users, tmp_path, profile
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request_json(port, method='GET', path='/api/folio', headers=None, payload=None, status=200):
    response = http.http_request(port, method, path, headers=headers, payload=payload)
    assert response[0] == status, response[2]
    assert response[1]['cache-control'] == 'no-store'
    return json.loads(response[2])


@pytest.mark.parametrize('folio_server', [True], indirect=True)
def test_folio_requires_login_and_same_account_csrf(folio_server):
    port, users, _, _ = folio_server
    for path in ('/folio', '/assets/folio/folio.mjs'):
        response = http.http_request(port, 'GET', path)
        assert response[0] == 303
        assert response[1]['location'].startswith('/login')
    target = '/folio?id=' + 'a' * 32
    response = http.http_request(port, 'GET', target)
    assert response[1]['location'] == '/login?next=%2Ffolio%3Fid%3D' + 'a' * 32
    request_json(port, status=401)
    payload = {'action': 'create', 'title': 'Private document'}
    request_json(port, 'POST', payload=payload, status=401)
    for headers in ({'Cookie': users['alice']['Cookie']},
                    {**users['alice'], 'X-CSRF-Token': users['bob']['X-CSRF-Token']},
                    {'Cookie': users['alice']['Cookie'], 'Origin': 'https://untrusted.example'}):
        request_json(port, 'POST', headers=headers, payload=payload, status=403)
    assert request_json(port, headers=users['alice'])['documents'] == []


def test_local_folio_rejects_cross_origin_writes(folio_server):
    port, _, _, _ = folio_server
    payload = {'action': 'create', 'title': 'Local document'}
    for headers in ({'Origin': 'https://untrusted.example'},
                    {'Host': 'untrusted.example'},
                    {'Sec-Fetch-Site': 'cross-site'}):
        request_json(port, 'POST', headers=headers, payload=payload, status=403)
    assert request_json(port)['documents'] == []


def test_folio_build_dependencies_are_not_public_assets(folio_server):
    port, _, tmp, _ = folio_server
    for folder in ('build', 'editor-build'):
        build = tmp / 'assets/folio' / folder
        build.mkdir(exist_ok=True)
        (build / 'package.json').write_text('{"private":true}', encoding='utf-8')
        for path in (f'/assets/folio/{folder}/package.json',
                     f'/assets/folio/./{folder}/package.json',
                     f'/assets/workspace/../folio/{folder}/package.json'):
            assert http.http_request(port, 'GET', path)[0] == 404, path


@pytest.mark.parametrize('folio_server', [{'profile': 'write'}, {'profile': 'rag'}], indirect=True)
def test_folio_is_only_exposed_by_the_full_workspace(folio_server):
    port, _, _, _ = folio_server
    for path in ('/folio', '/api/folio', '/assets/folio/folio.mjs',
                 '/assets/workspace/../folio/folio.mjs'):
        assert http.http_request(port, 'GET', path)[0] == 404, path
    request_json(port, 'POST', payload={'action': 'create'}, status=404)
    flags = request_json(port, path='/api/app-profile')
    assert flags['features']['folio'] is False


@pytest.mark.parametrize('folio_server', [False, True], indirect=True)
def test_document_crud_optimistic_revisions_and_exports(folio_server):
    port, users, _, _ = folio_server
    headers = users.get('alice', {})
    created = request_json(port, 'POST', headers=headers,
                           payload={'action': 'create', 'title': 'Private draft'})
    assert len(created['id']) == 32
    assert created['revision'] == 1
    save = {field: created[field] for field in ('id', 'revision', 'title', 'content', 'page', 'comments')}
    save.update(action='save', title='Edited draft', content=CONTENT)
    saved = request_json(port, 'POST', headers=headers, payload=save)
    assert saved['revision'] == 2
    assert saved['content'] == CONTENT
    assert request_json(port, path='/api/folio?id=' + saved['id'], headers=headers) == saved
    request_json(port, 'POST', headers=headers, payload=save, status=409)
    for action in ('duplicate', 'delete'):
        request_json(port, 'POST', headers=headers,
                     payload={'action': action, 'id': saved['id'], 'revision': 1}, status=409)
    for format_name in ('json', 'html', 'txt', 'docx'):
        response = http.http_request(port, 'GET',
            '/api/folio/export?id=' + saved['id'] + '&format=' + format_name, headers=headers)
        assert response[0] == 200, response[2]
        assert response[1]['cache-control'] == 'no-store'
        assert 'attachment' in response[1]['content-disposition']
        if format_name == 'docx':
            with zipfile.ZipFile(io.BytesIO(response[2])) as archive:
                assert 'Private evidence &amp; an observation.' in archive.read('word/document.xml').decode()
        elif format_name == 'json':
            assert json.loads(response[2])['content'] == CONTENT
        else:
            assert b'Private evidence' in response[2]
    duplicate = request_json(port, 'POST', headers=headers,
        payload={'action': 'duplicate', 'id': saved['id'], 'revision': saved['revision']})
    assert duplicate['id'] != saved['id']
    assert duplicate['content'] == saved['content']
    assert duplicate['revision'] == 1
    listed = request_json(port, headers=headers)['documents']
    assert {item['id'] for item in listed} == {saved['id'], duplicate['id']}
    deleted = request_json(port, 'POST', headers=headers,
        payload={'action': 'delete', 'id': saved['id'], 'revision': saved['revision']})
    assert deleted == {'id': saved['id'], 'deleted': True}
    request_json(port, path='/api/folio?id=' + saved['id'], headers=headers, status=404)
    assert request_json(port, path='/api/folio?id=' + duplicate['id'], headers=headers) == duplicate


@pytest.mark.parametrize('folio_server', [True], indirect=True)
def test_document_ids_never_grant_another_account_access(folio_server):
    port, users, _, _ = folio_server
    alice, bob = users['alice'], users['bob']
    created = request_json(port, 'POST', headers=alice,
                           payload={'action': 'create', 'title': 'Alice only'})
    key = created['id']
    assert request_json(port, headers=bob)['documents'] == []
    request_json(port, path='/api/folio?id=' + key, headers=bob, status=404)
    for format_name in ('json', 'html', 'txt', 'docx'):
        request_json(port, path='/api/folio/export?id=' + key + '&format=' + format_name,
                     headers=bob, status=404)
        request_json(port, path='/api/folio/export?id=' + key + '&format=' + format_name + '&revision=1',
                     headers=bob, status=404)
    for action in ('save', 'duplicate', 'delete'):
        payload = {'action': action, 'id': key, 'revision': created['revision']}
        if action == 'save':
            payload.update({field: created[field] for field in ('title', 'content', 'page', 'comments')})
        request_json(port, 'POST', headers=bob, payload=payload, status=404)
    request_json(port, path='/api/folio?owner=alice', headers=bob, status=400)
    assert request_json(port, path='/api/folio?id=' + key, headers=alice) == created


def test_invalid_content_and_imports_cannot_replace_a_saved_document(folio_server):
    port, _, _, _ = folio_server
    created = request_json(port, 'POST', payload={'action': 'create', 'title': 'Safe content'})
    save = {field: created[field] for field in ('id', 'revision', 'title', 'content', 'page', 'comments')}
    invalid = {'type': 'doc', 'content': [{'type': 'script', 'attrs': {'src': 'https://untrusted.example/'}}]}
    request_json(port, 'POST', payload={**save, 'action': 'save', 'content': invalid}, status=400)
    for filename, encoded in (('broken.json', 'bm90IGpzb24='),
                               ('broken.docx', 'bm90IGEgemlw'),
                               ('broken.json', '!not-base64!')):
        request_json(port, 'POST', payload={'action': 'import', 'filename': filename,
                                           'content_base64': encoded}, status=400)
    assert request_json(port, path='/api/folio?id=' + created['id']) == created
    assert len(request_json(port)['documents']) == 1


def test_malformed_json_fields_fail_cleanly_without_mutating_the_document(folio_server):
    port, _, _, _ = folio_server
    created = request_json(port, 'POST', payload={'action': 'create', 'title': 'Keep this document'})
    save = {field: created[field] for field in ('id', 'revision', 'title', 'content', 'page', 'comments')}
    save['action'] = 'save'
    malformed = [
        {'action': []}, {'action': 'create', 'template': []},
        {**save, 'title': []}, {**save, 'page': {'size': []}},
        {**save, 'content': {'type': 'doc', 'content': [{'type': []}]}},
        {**save, 'content': {'type': 'doc', 'content': [{'type': 'paragraph', 'attrs': []}]}},
        {**save, 'comments': [{'id': 'one', 'text': 'Note', 'created_at': float('nan')}]},
    ]
    for payload in malformed:
        request_json(port, 'POST', payload=payload, status=400)
    for body in (b'[]', b'null', b'{"action":"create","action":"delete"}', b'\xff'):
        response = http.http_request(port, 'POST', '/api/folio', body=body,
                                     headers={'Content-Type': 'application/json'})
        assert response[0] == 400, response[2]
    assert request_json(port, path='/api/folio?id=' + created['id']) == created
    assert len(request_json(port)['documents']) == 1


def test_document_count_quota_does_not_prevent_save_or_delete(folio_server, monkeypatch):
    from originrag import folio
    port, _, _, _ = folio_server
    monkeypatch.setattr(folio, 'MAX_DOCUMENTS', 1)
    created = request_json(port, 'POST', payload={'action': 'create', 'title': 'Only document'})
    request_json(port, 'POST', payload={'action': 'create'}, status=400)
    request_json(port, 'POST', payload={'action': 'duplicate', 'id': created['id'],
                                      'revision': created['revision']}, status=400)
    saved = request_json(port, 'POST', payload={**created, 'action': 'save', 'content': CONTENT})
    assert saved['revision'] == created['revision'] + 1
    request_json(port, 'POST', payload={'action': 'delete', 'id': saved['id'],
                                      'revision': saved['revision']})
    replacement = request_json(port, 'POST', payload={'action': 'create', 'title': 'Replacement'})
    assert request_json(port)['documents'][0]['id'] == replacement['id']


def test_docx_import_rejects_entities_in_utf8_and_utf16_xml(folio_server):
    port, _, _, _ = folio_server
    document = '''<?xml version="1.0" encoding="{encoding}"?>
<!DOCTYPE w:document [<!ENTITY sample "expanded entity">]>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>&sample;</w:t></w:r></w:p></w:body></w:document>'''
    for encoding in ('utf-8', 'utf-16', 'utf-16-le', 'utf-16-be'):
        output = io.BytesIO()
        xml = document.format(encoding='utf-8' if encoding == 'utf-8' else 'utf-16').encode(encoding)
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('[Content_Types].xml', '<Types/>')
            archive.writestr('word/document.xml', xml)
        request_json(port, 'POST', payload={'action': 'import', 'filename': 'entities.docx',
                     'content_base64': base64.b64encode(output.getvalue()).decode()}, status=400)
    assert request_json(port)['documents'] == []


def test_owner_byte_quota_rejects_growth_without_losing_saved_work(folio_server, monkeypatch):
    from originrag import folio
    port, _, _, _ = folio_server
    created = request_json(port, 'POST', payload={'action': 'create', 'title': 'Small document'})
    size = len(json.dumps({key: created[key] for key in ('content', 'page', 'comments')},
                          ensure_ascii=False, separators=(',', ':')).encode())
    monkeypatch.setattr(folio, 'MAX_OWNER_BYTES', size * 2)
    duplicate = request_json(port, 'POST', payload={'action': 'duplicate', 'id': created['id'],
                                                  'revision': created['revision']})
    request_json(port, 'POST', payload={'action': 'create'}, status=400)
    request_json(port, 'POST', payload={**created, 'action': 'save', 'content': CONTENT}, status=400)
    assert request_json(port, path='/api/folio?id=' + created['id']) == created
    request_json(port, 'POST', payload={'action': 'delete', 'id': duplicate['id'],
                                      'revision': duplicate['revision']})
    saved = request_json(port, 'POST', payload={**created, 'action': 'save', 'content': CONTENT})
    assert saved['content'] == CONTENT


def test_docx_relationship_edit_does_not_restore_a_stale_folio_snapshot(folio_server):
    port, _, _, _ = folio_server
    created = request_json(port, 'POST', payload={'action': 'create', 'title': 'A linked document'})
    content = {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [
        {'type': 'text', 'text': 'Read the updated source.',
         'marks': [{'type': 'link', 'attrs': {'href': 'https://old.example/source'}}]}
    ]}]}
    request_json(port, 'POST', payload={**created, 'action': 'save', 'content': content})
    response = http.http_request(port, 'GET', '/api/folio/export?id=' + created['id'] + '&format=docx')
    assert response[0] == 200
    updated = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(response[2])) as source:
        with zipfile.ZipFile(updated, 'w', compression=zipfile.ZIP_DEFLATED) as target:
            for entry in source.infolist():
                data = source.read(entry)
                if entry.filename == 'word/_rels/document.xml.rels':
                    assert b'https://old.example/source' in data
                    data = data.replace(b'https://old.example/source', b'https://new.example/source')
                target.writestr(entry.filename, data)
    imported = request_json(port, 'POST', payload={'action': 'import', 'filename': 'updated.docx',
        'content_base64': base64.b64encode(updated.getvalue()).decode()})
    assert 'https://new.example/source' in json.dumps(imported['content'])
    assert 'https://old.example/source' not in json.dumps(imported['content'])


@pytest.mark.parametrize('folio_server', [False, True], indirect=True)
def test_export_revision_guard_returns_conflict_without_an_attachment(folio_server):
    port, users, _, _ = folio_server
    headers = users.get('alice', {})
    original = request_json(port, 'POST', headers=headers,
                            payload={'action': 'create', 'title': 'An export snapshot'})
    saved = request_json(port, 'POST', headers=headers,
                         payload={**original, 'action': 'save', 'content': CONTENT})
    for format in ('json', 'html', 'txt', 'docx'):
        path = '/api/folio/export?' + urlencode({'id': saved['id'], 'format': format})
        response = http.http_request(port, 'GET', path + '&revision=1', headers=headers)
        assert response[0] == 409, response[2]
        assert response[1]['cache-control'] == 'no-store'
        assert 'content-disposition' not in response[1]
        error = json.loads(response[2])
        assert error['conflict'] is True and 'changed before export' in error['error']
        response = http.http_request(port, 'GET', path + '&revision=2', headers=headers)
        assert response[0] == 200, response[2]
        assert 'attachment' in response[1]['content-disposition']
        assert response[2]
    assert request_json(port, path='/api/folio?id=' + saved['id'], headers=headers) == saved


def test_export_query_revision_is_strict_and_does_not_allow_extra_fields(folio_server):
    port, _, _, _ = folio_server
    original = request_json(port, 'POST', payload={'action': 'create'})
    base = {'id': original['id'], 'format': 'json'}
    for revision in ('', '0', '-1', '1.0', '+1', ' 1', '1 ', '01', 'true', 'NaN', '1e0', '١'):
        response = http.http_request(port, 'GET', '/api/folio/export?' + urlencode({**base, 'revision': revision}))
        assert response[0] == 400, response[2]
        assert 'content-disposition' not in response[1]
        assert 'positive integer' in json.loads(response[2])['error']
    for query in (urlencode({**base, 'revision': 1}) + '&revision=1',
                  urlencode({**base, 'revision': 1, 'owner': 'someone'}),
                  urlencode({'id': original['id'], 'revision': 1}),
                  urlencode({'format': 'json', 'revision': 1})):
        request_json(port, path='/api/folio/export?' + query, status=400)
    assert request_json(port, path='/api/folio/export?' + urlencode(base)) == original


def test_export_http_keeps_the_checked_snapshot_if_another_tab_saves(folio_server, monkeypatch):
    from originrag.folio import FolioStore
    port, _, _, _ = folio_server
    original = request_json(port, 'POST', payload={'action': 'create', 'title': 'Original snapshot'})
    get = FolioStore.get
    reads = []

    def get_then_save(store, key):
        snapshot = get(store, key)
        reads.append(snapshot)
        store.change({**snapshot, 'action': 'save', 'title': 'Another tab', 'content': CONTENT})
        return snapshot

    monkeypatch.setattr(FolioStore, 'get', get_then_save)
    path = '/api/folio/export?' + urlencode({'id': original['id'], 'format': 'json', 'revision': 1})
    response = http.http_request(port, 'GET', path)
    assert response[0] == 200, response[2]
    assert json.loads(response[2]) == original
    assert response[1]['content-disposition'] == 'attachment; filename="Original snapshot.json"'
    assert len(reads) == 1
    monkeypatch.setattr(FolioStore, 'get', get)
    current = request_json(port, path='/api/folio?id=' + original['id'])
    assert current['revision'] == 2 and current['title'] == 'Another tab'
