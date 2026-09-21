import io
import json
import threading
import uuid
import zipfile
from urllib.parse import urlencode

import pytest

from originrag import web_auth, web_workspace
from originrag.writing import WritingStore
from originrag.writing_collaboration import WritingCollaboration
import test_web_chat as http


PASSWORD = 'quiet-lantern-river-orbit-47'
CATALOGUE = [{'paper_id': 'P1', 'title': 'Shared source, private library', 'author': 'Example', 'year': '2026'}]


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(web_auth, 'SCRYPT_N', 2**10)
    auth = web_auth.AuthStore(tmp_path / 'auth.sqlite')
    users = {name: auth.create_user(name, PASSWORD, display_name=name.title() + ' Writer') for name in ('alice', 'bob', 'carol')}
    return auth, users


@pytest.fixture(params=['write', 'full'])
def workspace(tmp_path, monkeypatch, accounts, request):
    auth, users = accounts
    web = http.web_chat
    monkeypatch.setattr(web, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(web, 'library_catalog', lambda: CATALOGUE)
    monkeypatch.setattr(web, 'load_project_env', lambda *args: None)
    root = tmp_path / 'sessions'
    registry = web.AuthenticatedWebChatRegistry(web.WebChatSettings(backend='template', app_profile=request.param), root)
    handler = web.build_handler(None, auth_store=auth, app_registry=registry)
    server = web.create_server('127.0.0.1', 0, handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    headers = {name: http.login_headers(port, name, PASSWORD)[0] for name in users}

    def call(method, route='/api/writing', who='alice', payload=None, status=200):
        response = http.http_request(port, method, route, headers=headers.get(who), payload=payload)
        assert response[0] == status, response[2]
        assert response[1]['cache-control'] == 'no-store'
        return json.loads(response[2])

    yield call, port, headers, auth, users, root
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def project(call, **fields):
    return call('POST', payload={'action': 'create_project', 'name': 'Private manuscript', **fields})


def test_project_management_never_loads_corpus(workspace, monkeypatch):
    call, _, _, _, _, _ = workspace
    def forbidden():
        raise AssertionError('Manuscript management must not load the corpus')
    monkeypatch.setattr(http.web_chat, 'library_catalog', forbidden)
    def forbidden_chat(*args, **kwargs):
        raise AssertionError('Quill must not create a RAG chat session')
    monkeypatch.setattr(http.web_chat, 'WebChatApp', forbidden_chat)
    assert 'library_access' in call('GET', '/api/library-access')
    p = project(call)
    assert call('GET')['projects'][0]['id'] == p['id']
    call('POST', payload={'action': 'delete_project', 'id': p['id'], 'revision': p['revision'],
                         'confirm_name': p['name'], 'confirm_delete': True})


def share(call, p, target, action='add', who='alice', status=200):
    return call('POST', '/api/writing/collaboration', who, {'action': action, 'id': p['id'], 'user_id': target.user_id}, status)


def test_delete_requires_owner_and_confirmation_and_preserves_collection(workspace):
    call, _, _, _, users, _ = workspace
    collection = call('POST', payload={'action': 'create_collection', 'name': 'Keep papers'})
    p = project(call, collection=collection['id'])
    share(call, p, users['bob'])
    payload = {'action': 'delete_project', 'id': p['id'], 'revision': p['revision'],
               'confirm_name': p['name'], 'confirm_delete': True}
    call('POST', who='bob', payload=payload, status=403)
    call('POST', who='carol', payload=payload, status=404)
    call('POST', payload={**payload, 'confirm_delete': False}, status=400)
    call('POST', payload={**payload, 'confirm_name': 'Wrong name'}, status=400)
    assert call('POST', payload=payload)['deleted'] is True
    call('GET', '/api/writing?id=' + p['id'], status=404)
    call('GET', '/api/writing?id=' + p['id'], who='bob', status=404)
    assert call('GET')['collections'][0]['id'] == collection['id']


def test_shared_project_requires_explicit_grant_for_every_operation(workspace, monkeypatch):
    call, port, headers, _, users, root = workspace
    p = project(call)
    assert p['collaboration']['role'] == 'owner' and not p['collaboration']['shared']
    assert p['collaboration']['owner'] == {'user_id': users['alice'].user_id, 'username': 'alice', 'display_name': 'Alice Writer'}
    key = p['id']
    routes = [f'/api/writing?id={key}', f'/api/writing?revision={key}', f'/api/writing/export?id={key}',
              f'/api/writing?bib_preview={key}', f'/api/writing/collaboration?id={key}']
    for route in routes:
        call('GET', route, 'bob', status=404)
    call('POST', who='bob', payload={**p, 'action': 'save_project'}, status=404)
    call('POST', who='bob', payload={'id': key, 'action': 'compile', 'revision': p['revision']}, status=404)
    share(call, p, users['carol'], who='bob', status=404)
    assert http.http_request(port, 'GET', '/write?id=' + key, headers=headers['bob'])[0] == 200
    assert call('GET', who='bob')['projects'] == []

    permission = share(call, p, users['bob'])
    assert permission['members'] == [{**{k: getattr(users['bob'], k) for k in ('user_id', 'username', 'display_name')}, 'role': 'editor'}]
    assert permission['collaboration']['shared']
    shared = call('GET', '/api/writing?id=' + key, 'bob')
    assert shared['files'] == p['files']
    assert shared['collaboration']['role'] == 'editor'
    assert not shared['collaboration']['can_manage']
    assert call('GET', who='bob')['projects'][0]['id'] == key
    share(call, p, users['carol'], who='bob', status=403)
    share(call, p, users['bob'], action='remove', who='bob', status=403)
    share(call, p, users['carol'], who='carol', status=404)
    poll = call('GET', '/api/writing?revision=' + key, 'bob')
    assert set(poll) == {'id', 'revision', 'collaboration'}
    assert poll['revision'] == 1

    calls = []
    def compile_fake(source, directory, **options):
        calls.append((source['id'], directory))
        return {'ok': True, 'pdf': b'%PDF-shared', 'revision': source['revision']}
    monkeypatch.setattr(web_workspace, 'compile_saved_project', compile_fake)
    assert call('POST', who='bob', payload={'action': 'compile', 'id': key, 'revision': 1})['ok']
    assert calls == [(key, root / 'users' / users['alice'].user_id)]
    result = http.http_request(port, 'GET', '/api/writing/export?id=' + key, headers=headers['bob'])
    assert result[0] == 200
    with zipfile.ZipFile(io.BytesIO(result[2])) as archive:
        assert archive.read('main.tex').decode() == p['files']['main.tex']
        assert '.writing.sqlite3' not in archive.namelist()

    share(call, p, users['bob'], action='remove')
    for route in routes:
        call('GET', route, 'bob', status=404)
    call('POST', who='bob', payload={**shared, 'action': 'save_project'}, status=404)
    assert call('GET', who='bob')['projects'] == []


def test_editor_cannot_expose_or_change_private_bibliography(workspace):
    call, _, _, _, users, root = workspace
    collection = call('POST', payload={'action': 'create_collection', 'name': 'Secret owner collection'})
    collection = call('POST', payload={**collection, 'action': 'collection_paper', 'paper_id': 'P1'})
    p = project(call, collection=collection['id'])
    preview = call('GET', '/api/writing?bib_preview=' + p['id'])
    p = call('POST', payload={'action': 'sync_bibliography', 'id': p['id'], 'revision': p['revision'],
            'collection_revision': collection['revision'], 'preview_token': preview['preview_token']})
    assert p['bibliography_sync']
    share(call, p, users['bob'])
    shared = call('GET', '/api/writing?id=' + p['id'], 'bob')
    assert shared['collection'] == '' and 'bibliography_sync' not in shared
    assert '@article' in shared['files']['references.bib']
    assert call('GET', who='bob')['collections'] == []
    assert call('GET', who='bob')['projects'][0]['collection'] == ''
    for route in [f"/api/writing?id={collection['id']}&kind=collections", f"/api/writing?collection_refs={collection['id']}",
                  f"/api/writing/bibliography?id={collection['id']}"]:
        call('GET', route, 'bob', status=404)
    call('GET', '/api/writing?bib_preview=' + p['id'], 'bob', status=403)
    call('POST', who='bob', payload={'action': 'sync_bibliography', 'id': p['id']}, status=403)
    call('POST', who='bob', payload={**shared, 'action': 'save_project', 'collection': collection['id']}, status=403)
    call('POST', who='bob', payload={**shared, 'action': 'save_project', 'collection': 'f' * 32}, status=403)

    saved = call('POST', who='bob', payload={**shared, 'action': 'save_project',
            'files': {**shared['files'], 'sections/new.tex': 'A shared contribution'}})
    assert saved['collection'] == '' and 'bibliography_sync' not in saved
    owner = call('GET', '/api/writing?id=' + p['id'])
    assert owner['collection'] == collection['id'] and owner['bibliography_sync'] == p['bibliography_sync']
    assert owner['files']['sections/new.tex'] == 'A shared contribution'
    own_store = WritingStore(root / 'users' / users['bob'].user_id)
    assert own_store.snapshot()['projects'] == []
    call('POST', who='bob', payload={**shared, 'action': 'save_project'}, status=409)


def test_revision_poll_does_not_load_project_sources(workspace, monkeypatch):
    call, _, _, _, users, _ = workspace
    p = project(call)
    share(call, p, users['bob'])
    def forbidden(*args, **kwargs):
        raise AssertionError('Polling must not deserialize project files')
    monkeypatch.setattr(WritingStore, 'get', forbidden)
    assert call('GET', '/api/writing?revision=' + p['id'], 'bob')['revision'] == 1


def test_editor_stale_merge_preserves_owner_collection_and_conflicts_keep_source(workspace):
    call, _, _, _, users, _ = workspace
    collection = call('POST', payload={'action': 'create_collection', 'name': 'Private owner library'})
    p = project(call, collection=collection['id'])
    p = call('POST', payload={**p, 'action': 'save_project', 'files': {'main.tex': 'Alpha beta gamma\n', 'references.bib': ''}})
    share(call, p, users['bob'])
    base = call('GET', '/api/writing?id=' + p['id'], 'bob')
    owner_edit = call('POST', payload={**p, 'action': 'save_project', 'files': {**p['files'], 'main.tex': 'ALPHA beta gamma\n'}})
    editor_save = {**base, 'action': 'save_project', 'base': {k: base[k] for k in ('files', 'main', 'collection', 'revision')},
                   'files': {**base['files'], 'main.tex': 'Alpha beta GAMMA\n'}}
    merged = call('POST', who='bob', payload=editor_save)
    assert merged['files']['main.tex'] == 'ALPHA beta GAMMA\n'
    assert merged['revision'] == owner_edit['revision'] + 1
    assert merged['collection'] == ''
    assert call('GET', '/api/writing?id=' + p['id'])['collection'] == collection['id']
    conflicting = {**editor_save, 'files': {**base['files'], 'main.tex': 'Changed beta gamma\n'}}
    response = call('POST', who='bob', payload=conflicting, status=409)
    assert 'files' not in response
    assert call('GET', '/api/writing?id=' + p['id'])['files'] == merged['files']


def test_shared_dashboard_uses_metadata_and_still_checks_access(workspace, monkeypatch):
    from test_writing_summaries import reject_source_reads
    call, _, _, _, users, _ = workspace
    collection = call('POST', payload={'action': 'create_collection', 'name': 'Private library'})
    p = project(call, collection=collection['id'])
    share(call, p, users['bob'])
    connect = WritingStore.connect
    monkeypatch.setattr(WritingStore, 'connect', lambda self: reject_source_reads(connect(self)))
    result = call('GET', who='bob')['projects']
    assert len(result) == 1 and result[0]['id'] == p['id']
    assert result[0]['collection'] == '' and result[0]['collaboration']['role'] == 'editor'
    assert call('GET', who='carol')['projects'] == []
    share(call, p, users['bob'], action='remove')
    assert call('GET', who='bob')['projects'] == []


def test_revoked_while_compiling_does_not_return_project_pdf(workspace, monkeypatch):
    call, _, _, auth, users, root = workspace
    p = project(call)
    share(call, p, users['bob'])
    acl = WritingCollaboration(root, auth)
    def compile_then_revoke(source, directory, **options):
        acl.change(users['alice'], {'action': 'remove', 'id': p['id'], 'user_id': users['bob'].user_id})
        return {'ok': True, 'pdf': b'%PDF-private', 'revision': source['revision']}
    monkeypatch.setattr(web_workspace, 'compile_saved_project', compile_then_revoke)
    result = call('POST', who='bob', payload={'action': 'compile', 'id': p['id'], 'revision': 1}, status=404)
    assert 'pdf' not in result


def test_auth_csrf_disabled_accounts_search_and_validation(workspace):
    call, port, headers, auth, users, _ = workspace
    p = project(call)
    key = p['id']
    routes = ['/api/writing/collaborators?query=bo', '/api/writing/collaboration?id=' + key,
              '/api/writing?revision=' + key]
    for route in routes:
        call('GET', route, None, status=401)
    payload = {'action': 'add', 'id': key, 'user_id': users['bob'].user_id}
    route = '/api/writing/collaboration'
    call('POST', route, None, payload, status=401)
    for invalid in [{'Cookie': headers['alice']['Cookie']}, {**headers['alice'], 'X-CSRF-Token': headers['bob']['X-CSRF-Token']}]:
        assert http.http_request(port, 'POST', route, headers=invalid, payload=payload)[0] == 403
    found = call('GET', '/api/writing/collaborators?query=Bob%20Writer')['users']
    assert found == [{k: getattr(users['bob'], k) for k in ('user_id', 'username', 'display_name')}]
    for query in ['b', 'a' * 81]:
        call('GET', '/api/writing/collaborators?' + urlencode({'query': query}), status=400)
    assert call('GET', '/api/writing/collaborators?query=%25%25')['users'] == []
    call('POST', route, payload={**payload, 'owner': users['alice'].user_id}, status=400)
    call('POST', route, payload={**payload, 'user_id': '../../alice'}, status=400)
    call('POST', route, payload={**payload, 'id': '../../private'}, status=400)
    share(call, p, users['alice'], status=400)
    share(call, p, users['bob'])
    auth.set_disabled('carol', disabled=True)
    assert call('GET', '/api/writing/collaborators?query=car')['users'] == []
    share(call, p, users['carol'], status=404)
    auth.set_disabled('alice', disabled=True)
    call('GET', '/api/writing?id=' + key, 'bob', status=404)
    assert call('GET', who='bob')['projects'] == []
    call('GET', '/api/writing?id=' + key, 'alice', status=401)


def test_login_deep_link_is_preserved_but_never_grants_access(workspace):
    call, port, headers, _, _, _ = workspace
    key = project(call)['id']
    target = '/write?id=' + key
    response = http.http_request(port, 'GET', target)
    assert response[0] == 303
    assert response[1]['location'] == '/login?' + urlencode({'next': target})
    login = http.http_request(port, 'GET', response[1]['location'])
    assert login[0] == 200 and b'window.location.replace(loginTarget)' in login[2]
    logged_in = http.http_request(port, 'GET', response[1]['location'], headers=headers['bob'])
    assert logged_in[0] == 303 and logged_in[1]['location'] == target
    call('GET', '/api/writing?id=' + key, 'bob', status=404)
    for invalid in ['https://evil.example', '//evil.example', '/write?id=' + key + '&owner=alice', '/write?id=../secret',
                    '/write?id=' + key + '\n', '/chat', '/write?id=' + key + '#fragment']:
        result = http.http_request(port, 'GET', '/login?' + urlencode({'next': invalid}), headers=headers['bob'])
        assert result[0] == 303 and result[1]['location'] == '/'
    result = http.http_request(port, 'GET', '/login?next=' + target + '&next=' + target, headers=headers['bob'])
    assert result[1]['location'] == '/'


def test_sharing_limit_persistence_and_atomic_revocation(tmp_path, accounts):
    auth, users = accounts
    acl = WritingCollaboration(tmp_path / 'sessions', auth)
    owner_store = acl.store(users['alice'].user_id)
    p = owner_store.change({'action': 'create_project', 'name': 'Existing private manuscript'}, [])
    for index in range(20):
        target = auth.create_user('editor' + str(index), PASSWORD)
        acl.change(users['alice'], {'action': 'add', 'id': p['id'], 'user_id': target.user_id})
    assert len(acl.members(users['alice'], p['id'])['members']) == 20
    acl.change(users['alice'], {'action': 'add', 'id': p['id'], 'user_id': target.user_id})
    with pytest.raises(ValueError, match='20 editors'):
        acl.change(users['alice'], {'action': 'add', 'id': p['id'], 'user_id': users['bob'].user_id})
    assert acl.path.stat().st_mode & 0o777 == 0o600
    recreated = WritingCollaboration(acl.directory, auth)
    assert len(recreated.members(users['alice'], p['id'])['members']) == 20
    entered, revoke_started, revoked = threading.Event(), threading.Event(), threading.Event()
    def revoke():
        revoke_started.set()
        recreated.change(users['alice'], {'action': 'remove', 'id': p['id'], 'user_id': target.user_id})
        revoked.set()
    with acl.access(target, p['id']) as access:
        entered.set()
        thread = threading.Thread(target=revoke)
        thread.start()
        assert revoke_started.wait(2)
        assert not revoked.wait(.1)
        access.store.change({**p, 'action': 'save_project', 'files': {**p['files'], 'new.tex': 'Saved before revocation'}}, [])
    thread.join(timeout=5)
    assert revoked.is_set()
    with pytest.raises(KeyError):
        with recreated.access(target, p['id']):
            pass
    assert owner_store.get(p['id'])['files']['new.tex'] == 'Saved before revocation'


def test_owner_path_and_collection_ids_cannot_be_used_as_project_acl(tmp_path, accounts):
    auth, users = accounts
    acl = WritingCollaboration(tmp_path / 'sessions', auth)
    store = acl.store(users['alice'].user_id)
    collection = store.change({'action': 'create_collection', 'name': 'Private collection'}, [])
    with pytest.raises(KeyError):
        acl.change(users['alice'], {'action': 'add', 'id': collection['id'], 'user_id': users['bob'].user_id})
    for invalid in ['../alice', '/tmp', 'x' * 32]:
        with pytest.raises(ValueError):
            acl.store(invalid)
    symlink_user = str(uuid.uuid4())
    (acl.directory / 'users' / symlink_user).symlink_to(store.directory)
    with pytest.raises(OSError):
        acl.store(symlink_user)


@pytest.mark.parametrize('mode', ['local', 'rag'])
def test_collaboration_is_disabled_without_accounts_or_write_profile(tmp_path, accounts, monkeypatch, mode):
    web = http.web_chat
    monkeypatch.setattr(web, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(web, 'library_catalog', lambda: [])
    auth, _ = accounts
    if mode == 'local':
        app = web.WorkspaceApp(web.WebChatSettings(app_profile='write'), tmp_path / 'local')
        handler = web.build_handler(app, profile='write')
    else:
        registry = web.AuthenticatedWebChatRegistry(web.WebChatSettings(backend='template', app_profile='rag'), tmp_path / 'sessions')
        handler = web.build_handler(None, auth_store=auth, app_registry=registry)
    server = web.create_server('127.0.0.1', 0, handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        headers = http.login_headers(port, 'alice', PASSWORD)[0] if mode == 'rag' else {}
        for method, route in [('GET', '/api/writing/collaborators?query=bo'), ('GET', '/api/writing/collaboration?id=' + 'a' * 32),
                              ('POST', '/api/writing/collaboration')]:
            assert http.http_request(port, method, route, headers=headers, payload={} if method == 'POST' else None)[0] == 404
        if mode == 'local':
            response = http.http_request(port, 'POST', '/api/writing', headers=headers,
                                         payload={'action': 'create_project', 'name': 'Local manuscript'})
            assert response[0] == 200
            p = json.loads(response[2])
            assert p['collaboration']['role'] == 'local' and not p['collaboration']['enabled']
        else:
            assert http.http_request(port, 'GET', '/api/writing?revision=' + 'a' * 32, headers=headers)[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
