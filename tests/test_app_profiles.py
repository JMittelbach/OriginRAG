import csv
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from originrag.app_profiles import app_profile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import web_chat as web  # noqa: E402


def request(port, method, path, payload=None, headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {'Content-Type': 'application/json', **(headers or {})}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        data = response.read()
        return response.status, dict(response.getheaders()), json.loads(data) if data and response.getheader('Content-Type', '').startswith('application/json') else data
    finally:
        connection.close()


@pytest.mark.parametrize('profile,rag,write', [('full', True, True), ('rag', True, False), ('write', False, True)])
def test_profile_flags_and_parser(monkeypatch, profile, rag, write):
    monkeypatch.setenv('ORIGINRAG_APP_PROFILE', profile)
    parsed = app_profile()
    assert (parsed.rag, parsed.write) == (rag, write)
    assert web.parse_args([]).profile == profile
    assert web.parse_args(['--profile', 'full']).profile == 'full'
    assert parsed.public()['features']['reader']


def test_invalid_profile_rejected():
    with pytest.raises(ValueError):
        app_profile('other')


def test_write_cold_start_without_optional_dependencies_or_chat_state(tmp_path):
    code = r'''
import importlib.abc, json, pathlib, sys, threading
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        prefixes = ('ask_corpus', 'pandas', 'numpy', 'pydantic', 'typer', 'rich', 'prompt_toolkit', 'rapidfuzz', 'yaml', 'fitz', 'pymupdf', 'originrag.retrieval', 'originrag.analysis.ollama_client')
        if any(name == prefix or name.startswith(prefix + '.') for prefix in prefixes):
            raise AssertionError('Write startup imported ' + name)
sys.meta_path.insert(0, Block())
sys.path.insert(0, str(pathlib.Path.cwd() / 'scripts'))
import web_chat as web
root = pathlib.Path(sys.argv[1])
settings = web.WebChatSettings(app_profile='write')
app = web.WorkspaceApp(settings, root / 'users')
handler = web.build_handler(app, profile='write')
assert handler.turn_job_manager is None
assert not app.out_dir.exists()
assert not hasattr(app, 'session_dir')
assert [thread.name for thread in threading.enumerate()] == ['MainThread']
assert web.parse_args(['--profile','write']).profile == 'write'
handler.paper_import_manager.shutdown()
print(json.dumps({'profile':'write','chat_workers':0,'chat_sessions':0,'optional_dependencies':0}))
'''
    result = subprocess.run([sys.executable, '-S', '-c', code, str(tmp_path)], cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['optional_dependencies'] == 0


@pytest.fixture(params=['write', 'rag', 'full'])
def profile_server(tmp_path, monkeypatch, request):
    profile = request.param
    monkeypatch.setattr(web, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(web, 'library_catalog', lambda: [])
    settings = web.WebChatSettings(app_profile=profile, backend='template')
    auth = web.AuthStore(tmp_path / 'accounts.sqlite')
    auth.create_user('alice', 'quiet-lantern-river-orbit-47')
    auth.create_user('bob', 'quiet-lantern-river-orbit-47')
    token, session = auth.authenticate('alice', 'quiet-lantern-river-orbit-47')
    headers = {'Cookie': f'pfempire_session={token}; pfempire_csrf={session.csrf_token}', 'X-CSRF-Token': session.csrf_token}
    registry = web.AuthenticatedWebChatRegistry(settings, tmp_path / 'sessions')
    handler = web.build_handler(None, auth_store=auth, app_registry=registry, profile=profile)
    server = web.create_server('127.0.0.1', 0, handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield profile, server.server_address[1], headers, registry, handler, tmp_path
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    if handler.turn_job_manager:
        handler.turn_job_manager.shutdown()
    handler.paper_import_manager.shutdown()


def test_profile_api_protected_and_common_libraries_available(profile_server):
    profile, port, headers, registry, handler, root = profile_server
    assert request(port, 'GET', '/api/app-profile')[0] == 401
    status, _, data = request(port, 'GET', '/api/app-profile', headers=headers)
    assert status == 200 and data['profile'] == profile and data['authenticated']
    assert data['user']['username'] == 'alice'
    for path in ['/library', '/bookshelf', '/reader', '/api/library', '/api/shelf']:
        assert request(port, 'GET', path, headers=headers)[0] == 200
    payload = {'action': 'create_collection', 'name': 'My references'}
    status, _, collection = request(port, 'POST', '/api/writing', payload, headers)
    assert status == 200
    assert request(port, 'GET', '/api/writing?collection_refs=' + collection['id'], headers=headers)[0] == 200
    assert request(port, 'POST', '/api/writing', payload, {'Cookie': headers['Cookie']})[0] == 403
    if profile == 'write':
        assert handler.turn_job_manager is None
        assert not registry.apps
        assert not list(root.rglob('session_config.json'))
        assert not list(root.rglob('session.md'))


def test_disabled_features_are_not_accessible_by_direct_url(profile_server):
    profile, port, headers, registry, handler, root = profile_server
    status, _, data = request(port, 'POST', '/api/writing', {'action': 'create_project', 'name': 'My manuscript'}, headers)
    if profile == 'rag':
        assert status == 404
        for path in ['/write', '/api/writing/export?id=' + 'a' * 32,
                     '/api/writing?id=' + 'a' * 32, '/api/writing?bib_preview=' + 'a' * 32]:
            assert request(port, 'GET', path, headers=headers)[0] == 404
        for action in ['save_project', 'compile', 'sync_bibliography', 'apply_template', 'create_project']:
            assert request(port, 'POST', '/api/writing', {'action': action, 'id': 'a' * 32}, headers)[0] == 404
        snapshot = request(port, 'GET', '/api/writing', headers=headers)[2]
        assert snapshot['projects'] == snapshot['templates'] == []
    else:
        assert status == 200
        assert request(port, 'GET', '/api/writing?id=' + data['id'], headers=headers)[0] == 200
    if profile == 'write':
        for path in ['/chat', '/api/chat', '/api/reset', '/api/sessions', '/api/session', '/api/jobs',
                     '/api/job/cancel', '/api/progress', '/api/source-trace', '/api/source-request', '/file']:
            assert request(port, 'GET', path, headers=headers)[0] == 404
            assert request(port, 'POST', path, {}, headers)[0] == 404
        status, response_headers, _ = request(port, 'GET', '/', headers=headers)
        assert status in {302, 303} and response_headers['Location'] == '/write'
        status_data = request(port, 'GET', '/api/status', headers=headers)[2]
        assert 'library_access' in status_data and 'queue' not in status_data
    else:
        assert request(port, 'GET', '/chat', headers=headers)[0] == 200


def test_metadata_only_pdf_import_does_not_build_retrieval(tmp_path, monkeypatch):
    from test_metadata_fulltext_upgrade import setup_library
    import import_paper_inbox as inbox
    source, metadata = setup_library(tmp_path, monkeypatch)
    def prohibited(*args, **kwargs):
        raise AssertionError('A write-only import must not extract chunks or touch the retrieval index.')
    for name in ['extract_pdf_text', 'chunk_pages', 'corpus_fingerprint', 'build_fts_index', 'update_fts_index']:
        monkeypatch.setattr(inbox, name, prohibited)
    library = web.LibrarySyncManager(source.parent, metadata_only=True, auto_worker=False, refresh_callback=lambda: None)
    summary = library.run_pending_now()
    assert summary['imported'] == 1
    assert not (tmp_path / 'data/chunks').exists()
    assert not (tmp_path / 'data/extracted_text').exists()
    assert not (tmp_path / 'data/indexes').exists()
    with (metadata / 'papers.tsv').open() as stream:
        paper = next(csv.DictReader(stream, delimiter='\t'))
    assert paper['fulltext_status'] == 'pdf_available'
    assert (tmp_path / 'bib' / paper['local_pdf_relpath']).is_file()


def test_metadata_import_can_start_with_empty_local_catalogue(tmp_path, monkeypatch):
    import import_paper_inbox as inbox
    from test_oa_import import pdf_bytes
    monkeypatch.setattr(inbox, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(inbox, 'fetch_crossref', lambda *args, **kwargs: {'title': 'A study of graph algorithms', 'author': 'Example', 'year': '2024'})
    source = tmp_path / 'bib/inbox/test.pdf'
    source.parent.mkdir(parents=True)
    source.write_bytes(pdf_bytes())
    result = inbox.import_inbox(source.parent, build_retrieval=False)
    assert result['imported'] == 1 and not result['retrieval_prepared']
    assert (tmp_path / 'data/metadata/paper_registry.tsv').is_file()
    assert not (tmp_path / 'data/indexes').exists()


def test_write_only_import_module_stays_free_of_retrieval_imports():
    code = r'''
import importlib.abc, pathlib, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.startswith(('originrag.retrieval', 'pandas', 'numpy', 'ask_corpus')):
            raise AssertionError(name)
sys.meta_path.insert(0, Block())
sys.path.insert(0, str(pathlib.Path.cwd() / 'scripts'))
import import_paper_inbox
print('metadata-only importer ready')
'''
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src')}
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
