import hashlib
import io
import json
import sys
import threading
import zipfile

import fitz
import pytest

from originrag import writing_builds as builds
from originrag.discovery import oa_import
import test_web_chat as http
from test_library_pdf import metadata
from test_reader_notes import MARK


PASSWORD = 'quiet-lantern-river-orbit-47'
PROJECT = {'id': 'test', 'revision': 1, 'main': 'main.tex', 'files': {'main.tex': 'Synthetic text'}}
CATALOGUE = ({'paper_id': 'P1', 'title': 'Synthetic privacy test', 'author': 'Example', 'year': '2026', 'local_pdf': True},)


@pytest.fixture(params=['full', 'write', 'rag'])
def secured_workspace(tmp_path, monkeypatch, request):
    web = http.web_chat
    bib = tmp_path / 'bib'
    bib.mkdir()
    with fitz.open() as document:
        document.new_page().insert_text((50, 50), 'Synthetic shared corpus paper')
        pdf = document.tobytes()
    (bib / 'paper.pdf').write_bytes(pdf)
    metadata(tmp_path / 'data/metadata/paper_registry.tsv', [('P1', 'paper.pdf')])
    monkeypatch.setattr(web, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(web, 'get_bib_dir', lambda: bib)
    monkeypatch.setattr(web, 'load_project_env', lambda *args: None)
    monkeypatch.setattr(web, 'library_catalog', lambda: CATALOGUE)
    monkeypatch.setattr(web, 'search_library_catalog', lambda *args, **kwargs: {'items': CATALOGUE})
    auth = http.AuthStore(tmp_path / 'auth.sqlite')
    for user in ('alice', 'bob'):
        auth.create_user(user, PASSWORD)
    settings = web.WebChatSettings(backend='template', app_profile=request.param)
    registry = web.AuthenticatedWebChatRegistry(settings, tmp_path / 'sessions')
    handler = web.build_handler(None, auth_store=auth, app_registry=registry, profile=request.param)
    server = web.create_server('127.0.0.1', 0, handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    users = {name: http.login_headers(port, name, PASSWORD)[0] for name in ('alice', 'bob')}
    yield port, users, hashlib.sha256(pdf).hexdigest(), request.param
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def request_json(port, method, url, headers=None, payload=None, status=200):
    result = http.http_request(port, method, url, headers=headers, payload=payload)
    assert result[0] == status, result[2]
    assert result[1]['cache-control'] == 'no-store'
    return json.loads(result[2])


def test_shared_workspace_requires_authentication_and_csrf(secured_workspace):
    port, users, fingerprint, profile = secured_workspace
    notes = '/api/reader/notes?paper_id=P1&fingerprint=' + fingerprint
    routes = ['/api/app-profile', '/api/library', '/api/library/pdf?paper_id=P1', '/api/shelf',
              '/api/reader/paper?paper_id=P1', notes, '/api/writing', '/api/writing/bibliography?id=' + 'a' * 32,
              '/api/writing?collection_refs=' + 'a' * 32, '/api/paper-imports']
    if profile != 'rag':
        routes += ['/api/writing/export?id=' + 'a' * 32, '/api/writing?bib_preview=' + 'a' * 32]
    for route in routes:
        assert http.http_request(port, 'GET', route)[0] == 401, route
    for page in ['/workspace', '/library', '/bookshelf', '/reader?paper_id=P1', '/assets/workspace/workspace.mjs']:
        response = http.http_request(port, 'GET', page)
        assert response[0] == 303 and response[1]['location'].startswith('/login')
    changes = [('/api/writing', {'action': 'create_collection', 'name': 'Private references'}),
               ('/api/shelf', {'action': 'add', 'paper_id': 'P1'}),
               ('/api/reader/notes', {'action': 'add', 'paper_id': 'P1', 'fingerprint': fingerprint, 'mark': MARK})]
    for route, payload in changes:
        assert http.http_request(port, 'POST', route, payload=payload)[0] == 401
        for invalid in [{'Cookie': users['alice']['Cookie']},
                        {**users['alice'], 'X-CSRF-Token': users['bob']['X-CSRF-Token']},
                        {'Cookie': users['alice']['Cookie'], 'Origin': 'https://untrusted.example'}]:
            assert http.http_request(port, 'POST', route, headers=invalid, payload=payload)[0] == 403
    flags = request_json(port, 'GET', '/api/app-profile', users['alice'])
    assert flags['features']['write'] == (profile != 'rag')
    assert flags['features']['rag'] == (profile != 'write')


def test_collection_shelf_and_notes_are_private_but_corpus_pdf_is_shared(secured_workspace):
    port, users, fingerprint, _ = secured_workspace
    alice, bob = users['alice'], users['bob']
    collection = request_json(port, 'POST', '/api/writing', alice, {'action': 'create_collection', 'name': 'Alice private references'})
    key = collection['id']
    collection = request_json(port, 'POST', '/api/writing', alice, {'action': 'collection_paper', 'id': key, 'revision': collection['revision'], 'paper_id': 'P1'})
    for route in [f'/api/writing?id={key}&kind=collections', f'/api/writing?collection_refs={key}', f'/api/writing/bibliography?id={key}']:
        request_json(port, 'GET', route, bob, status=404)
    request_json(port, 'POST', '/api/writing', bob, {'action': 'collection_paper', 'id': key, 'revision': collection['revision'], 'paper_id': 'P1', 'remove': True}, status=404)
    request_json(port, 'POST', '/api/shelf', alice, {'action': 'add', 'paper_id': 'P1'})
    assert request_json(port, 'GET', '/api/shelf', bob)['items'] == []
    request_json(port, 'POST', '/api/shelf', bob, {'action': 'remove', 'paper_id': 'P1', 'owner': 'alice'}, status=400)
    assert len(request_json(port, 'GET', '/api/shelf', alice)['items']) == 1
    note_payload = {'action': 'add', 'paper_id': 'P1', 'fingerprint': fingerprint, 'mark': {**MARK, 'note': 'Private Alice interpretation'}}
    mark = request_json(port, 'POST', '/api/reader/notes', alice, note_payload)['items'][0]
    notes = '/api/reader/notes?paper_id=P1&fingerprint=' + fingerprint
    assert request_json(port, 'GET', notes, bob)['items'] == []
    for action in ('update', 'delete'):
        forged = {**note_payload, 'action': action, 'id': mark['id'], 'revision': mark['revision']}
        if action == 'delete':
            forged.pop('mark')
        request_json(port, 'POST', '/api/reader/notes', bob, forged, status=409)
    request_json(port, 'GET', notes + '&owner=alice', bob, status=400)
    assert request_json(port, 'GET', notes, alice)['items'][0]['note'] == 'Private Alice interpretation'
    for headers in (alice, bob):
        status, response_headers, data, _ = http.http_request(port, 'GET', '/api/library/pdf?paper_id=P1', headers={**headers, 'Range': 'bytes=0-4'})
        assert status == 206 and data == b'%PDF-'
        assert response_headers['cache-control'] == 'no-store'
    assert http.http_request(port, 'GET', '/api/library/pdf?paper_id=P1', headers={'Range': 'bytes=0-4'})[0] == 401


def test_project_source_export_compile_and_bibliography_stay_in_owner_account(secured_workspace, monkeypatch):
    port, users, _, profile = secured_workspace
    alice, bob = users['alice'], users['bob']
    if profile == 'rag':
        for action in ('create_project', 'compile', 'save_project', 'sync_bibliography'):
            request_json(port, 'POST', '/api/writing', alice, {'action': action}, status=404)
        for path in ('/write', '/api/writing/export?id=' + 'a' * 32, '/api/writing?bib_preview=' + 'a' * 32):
            assert http.http_request(port, 'GET', path, headers=alice)[0] == 404
        return
    from originrag import web_workspace
    calls = []

    def compile_fake(project, owner, **options):
        calls.append((project['id'], str(owner)))
        return {'ok': True, 'pdf': b'%PDF-synthetic', 'revision': project['revision'], 'log': '', 'seconds': 0}

    monkeypatch.setattr(web_workspace, 'compile_saved_project', compile_fake)
    project = request_json(port, 'POST', '/api/writing', alice, {'action': 'create_project', 'name': 'Alice private manuscript'})
    key = project['id']
    for route in (f'/api/writing?id={key}', f'/api/writing/export?id={key}', f'/api/writing?bib_preview={key}'):
        request_json(port, 'GET', route, bob, status=404)
    for action in ('save_project', 'sync_bibliography', 'compile'):
        request_json(port, 'POST', '/api/writing', bob, {**project, 'action': action, 'owner': 'alice'}, status=404)
    assert not calls
    exported = http.http_request(port, 'GET', f'/api/writing/export?id={key}', headers=alice)
    assert exported[0] == 200 and exported[1]['cache-control'] == 'no-store'
    with zipfile.ZipFile(io.BytesIO(exported[2])) as archive:
        assert archive.read('main.tex').decode() == project['files']['main.tex']
    request_json(port, 'POST', '/api/writing', alice, {'action': 'compile', 'id': key, 'revision': project['revision']})
    assert len(calls) == 1
    assert request_json(port, 'GET', '/api/writing', bob)['projects'] == []
    if profile == 'write':
        for route in ('/chat', '/api/jobs', '/api/session', '/api/sessions', '/file?name=private.pdf', '/api/source-trace'):
            assert http.http_request(port, 'GET', route, headers=alice)[0] == 404
        request_json(port, 'POST', '/api/chat', alice, {'message': 'Do not call a model'}, status=404)


@pytest.fixture
def bounded_builds(monkeypatch):
    builds._CACHE.clear()
    monkeypatch.delenv('ORIGINWRITE_BUILD_CACHE_MIB', raising=False)
    monkeypatch.setattr(builds, 'compiler_fingerprint', lambda: {'engine': 'test'})
    calls = []

    def run(project):
        calls.append(project['id'])
        return {'ok': True, 'pdf': b'%PDF-' + b'x' * 1000, 'log': 'Synthetic log', 'diagnostics': [], 'seconds': 1, 'revision': project['revision']}

    monkeypatch.setattr(builds, 'compile_project', run)
    yield calls
    builds._CACHE.clear()


def test_build_cache_accounts_for_results_and_enforces_bytes_items_ttl(tmp_path, monkeypatch, bounded_builds):
    monkeypatch.setattr(builds, '_MAX_BYTES', 8500)
    monkeypatch.setattr(builds, '_MAX_ITEMS', 2)
    now = [100.0]
    monkeypatch.setattr(builds.time, 'monotonic', lambda: now[0])
    for i in range(6):
        builds.compile_saved_project({**PROJECT, 'id': str(i)}, tmp_path)
        assert len(builds._CACHE) <= 2
        assert sys.getsizeof(builds._CACHE) + sum(entry[2] for entry in builds._CACHE.values()) <= 8500
    assert builds.compile_saved_project({**PROJECT, 'id': '5'}, tmp_path)['cached']
    now[0] += builds._TTL + 1
    assert not builds.compile_saved_project({**PROJECT, 'id': '5'}, tmp_path)['cached']
    assert len(builds._CACHE) == 1
    monkeypatch.setattr(builds, '_MAX_BYTES', 500)
    assert not builds.compile_saved_project(PROJECT, tmp_path)['cached']
    assert not builds._CACHE


@pytest.mark.parametrize('setting,expected', [('0', 0), ('-5', 0), ('2', 2), ('5000', 128), ('broken', 32)])
def test_cache_configuration_is_clamped(setting, expected, monkeypatch):
    monkeypatch.setenv('ORIGINWRITE_BUILD_CACHE_MIB', setting)
    assert builds._cache_budget() == expected * 1024 * 1024


def test_cache_private_immutable_binary_and_mutable_metadata_isolation(tmp_path, bounded_builds, monkeypatch):
    result = builds.compile_saved_project(PROJECT, tmp_path / 'alice')
    cached = builds.compile_saved_project(PROJECT, tmp_path / 'alice')
    assert result['pdf'] is cached['pdf']
    cached['diagnostics'].append({'message': 'Do not mutate retained metadata'})
    assert builds.compile_saved_project(PROJECT, tmp_path / 'alice')['diagnostics'] == []
    assert not builds.compile_saved_project(PROJECT, tmp_path / 'bob')['cached']
    assert not builds.compile_saved_project({**PROJECT, 'id': 'another'}, tmp_path / 'alice')['cached']
    assert len(bounded_builds) == 3
    monkeypatch.setenv('ORIGINWRITE_BUILD_CACHE_MIB', '0')
    assert not builds.compile_saved_project(PROJECT, tmp_path / 'alice')['cached']
    assert not builds._CACHE


def test_incremental_project_digest_preserves_file_names_content_order_and_unicode():
    files = {'main.tex': 'é' * 65537, 'references.bib': 'Entry'}
    first = {**PROJECT, 'files': files}
    assert builds._project_digest(first, {}) == builds._project_digest({**first, 'files': dict(reversed(list(files.items())))}, {})
    variants = [{**first, 'files': {**files, 'main.tex': files['main.tex'] + 'a'}},
                {**first, 'files': {'main.tex': files['main.tex'], 'other.bib': 'Entry'}}]
    for variant in variants:
        assert builds._project_digest(first, {}) != builds._project_digest(variant, {})
    assert builds._project_digest(first, {}) != builds._project_digest(first, {'engine': 'updated'})


def test_import_pdf_buffer_is_closed_before_indexing():
    streams = []

    class Library:
        corpus_lock = threading.RLock()

        def store_upload(self, stream, **metadata):
            streams.append(stream)
            assert stream.read() == b'%PDF-synthetic-import'
            assert metadata['schedule_import'] is False

        def run_pending_now(self):
            assert streams[0].closed
            return {'results': [{'source_file': 'discovered-job.pdf', 'status': 'accepted'}]}

    imports = oa_import.PaperImports(Library(), downloader=lambda *args: b'%PDF-synthetic-import')
    try:
        imports.jobs['job'] = {'job_id': 'job', 'owner': 'alice', 'doi': '10.1234/test', 'state': 'queued'}
        imports.run('job')
        assert imports.jobs['job']['state'] == 'imported'
        assert imports.snapshot('bob') == []
    finally:
        imports.shutdown()
