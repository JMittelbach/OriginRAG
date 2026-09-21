import json
import sqlite3
import threading

import pytest

import test_web_chat as helpers


ENDPOINT = '/api/reader/notebook'
EMPTY = {'paper_id': 'P1', 'text': '', 'revision': 0, 'updated_at': None}
WRITE = {'paper_id': 'P1', 'text': 'My personal paper notes.', 'revision': 0}


@pytest.fixture
def notebook_server(tmp_path, monkeypatch, request):
    web = helpers.web_chat
    config = getattr(request, 'param', {})
    monkeypatch.setattr(web, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(web, 'library_catalog', lambda: ({'paper_id': 'P1'}, {'paper_id': 'P2'}))
    auth = helpers.AuthStore(tmp_path / 'auth.sqlite') if config.get('auth') else None
    if auth:
        for name in ('alice', 'bob'):
            auth.create_user(name, 'quiet-lantern-river-orbit-47')
    settings = web.WebChatSettings(backend='template', app_profile=config.get('profile', 'full'))
    registry = web.AuthenticatedWebChatRegistry(settings, tmp_path / 'sessions') if auth else None
    app = None if auth else web.WorkspaceApp(settings, tmp_path / 'sessions')
    handler = web.build_handler(app, auth_store=auth, app_registry=registry, secure_cookies=False)
    server = web.create_server('127.0.0.1', 0, handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], tmp_path, auth
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request(port, method='GET', path=ENDPOINT + '?paper_id=P1', **kwargs):
    status, headers, raw, _ = helpers.http_request(port, method, path, **kwargs)
    assert headers['content-type'].startswith('application/json')
    return status, json.loads(raw)


@pytest.mark.parametrize('notebook_server', [{'profile': profile} for profile in ('full', 'rag', 'write')], indirect=True)
def test_notebook_api_round_trip_and_stale_clear_in_all_profiles(notebook_server):
    port, _, _ = notebook_server
    assert request(port) == (200, EMPTY)
    status, saved = request(port, 'POST', ENDPOINT, payload=WRITE)
    assert status == 200
    assert saved == {**WRITE, 'revision': 1, 'updated_at': saved['updated_at']}
    assert type(saved['updated_at']) is int and saved['updated_at'] > 1_000_000_000_000
    assert request(port) == (200, saved)
    assert request(port, path=ENDPOINT + '?paper_id=P2') == (200, {**EMPTY, 'paper_id': 'P2'})
    assert request(port, 'POST', ENDPOINT, payload=WRITE)[0] == 409
    status, cleared = request(port, 'POST', ENDPOINT, payload={**WRITE, 'text': '', 'revision': 1})
    assert status == 200 and cleared['revision'] == 2 and cleared['text'] == ''
    assert request(port, 'POST', ENDPOINT, payload=WRITE)[0] == 409
    assert request(port) == (200, cleared)


@pytest.mark.parametrize('notebook_server', [{'auth': True}], indirect=True)
def test_notebook_auth_csrf_and_per_user_isolation(notebook_server):
    port, _, _ = notebook_server
    alice, _ = helpers.login_headers(port, 'alice', 'quiet-lantern-river-orbit-47')
    bob, _ = helpers.login_headers(port, 'bob', 'quiet-lantern-river-orbit-47')
    assert request(port)[0] == 401
    assert request(port, 'POST', ENDPOINT, payload=WRITE)[0] == 401
    assert request(port, 'POST', ENDPOINT, payload=WRITE, headers={'Cookie': alice['Cookie']})[0] == 403
    assert request(port, 'POST', ENDPOINT, payload=WRITE, headers={**alice, 'X-CSRF-Token': 'wrong'})[0] == 403
    assert request(port, 'POST', ENDPOINT, payload=WRITE, headers=alice)[0] == 200
    assert request(port, headers=bob) == (200, EMPTY)
    assert request(port, 'POST', ENDPOINT, payload={**WRITE, 'text': 'Bob private notes'}, headers=bob)[0] == 200
    assert request(port, headers=alice)[1]['text'] == WRITE['text']
    assert request(port, headers=bob)[1]['text'] == 'Bob private notes'
    alice_another_session, _ = helpers.login_headers(port, 'alice', 'quiet-lantern-river-orbit-47')
    assert request(port, headers=alice_another_session)[1]['text'] == WRITE['text']
    assert request(port, 'POST', ENDPOINT, payload={**WRITE, 'owner': 'bob'}, headers=alice)[0] == 400
    assert request(port, path=ENDPOINT + '?paper_id=P1&owner=bob', headers=alice)[0] == 400


def test_notebook_local_browser_restrictions(notebook_server):
    port, _, _ = notebook_server
    for headers in ({'Origin': 'https://evil.example'}, {'Sec-Fetch-Site': 'cross-site'}, {'Host': 'evil.example'}):
        assert request(port, headers=headers)[0] == 403
        assert request(port, 'POST', ENDPOINT, payload=WRITE, headers=headers)[0] == 403
    assert request(port) == (200, EMPTY)


def test_notebook_rejects_missing_unknown_duplicate_or_blank_queries(notebook_server):
    port, _, _ = notebook_server
    for query in ('', '?paper_id=', '?paper_id', '?paper_id=P1&paper_id=P1',
                  '?paper_id=P1&paper_id=', '?paper_id=&paper_id=P1',
                  '?paper_id=P1&owner=', '?paper_id=P1&fingerprint=' + 'a' * 64,
                  '?paper_id=P1&unexpected', '?paper_id=%00', '?paper_id=%FF',
                  '?paper_id=' + 'P' * 161):
        assert request(port, path=ENDPOINT + query)[0] == 400, query
    assert request(port, path=ENDPOINT + '?paper_id=missing')[0] == 404
    assert request(port, 'POST', ENDPOINT, payload={**WRITE, 'paper_id': 'missing'})[0] == 404
    for query in ('?paper_id=P1', '?owner=', '?unknown'):
        assert request(port, 'POST', ENDPOINT + query, payload=WRITE)[0] == 400
    assert request(port) == (200, EMPTY)


def test_notebook_rejects_bad_json_fields_and_types_without_writes(notebook_server):
    port, _, _ = notebook_server
    invalid = [None, [], True, 'not an object', {},
               {'paper_id': 'P1', 'text': ''}, {**WRITE, 'fingerprint': 'a' * 64},
               *({**WRITE, 'paper_id': value} for value in (None, [], 1, True, '', ' ')),
               *({**WRITE, 'text': value} for value in (None, [], 1, True, {}, 'x' * 50_001, '\ud800')),
               *({**WRITE, 'revision': value} for value in (None, [], 0.0, True, -1, '0', 2 ** 80))]
    for payload in invalid:
        assert request(port, 'POST', ENDPOINT, body=json.dumps(payload).encode())[0] == 400, repr(payload)[:100]
    for raw in (b'{', b'\xff', b'{"paper_id":"P1","text":"a","text":"b","revision":0}',
                b'{"paper_id":"P1","paper_id":"P2","text":"a","revision":0}'):
        assert request(port, 'POST', ENDPOINT, body=raw)[0] == 400
    assert request(port) == (200, EMPTY)


def test_notebook_stores_markup_only_as_json_text_and_accepts_unicode_limit(notebook_server):
    port, _, _ = notebook_server
    text = '\n😀 Résumé 日本語\x00 </textarea><img src=x onerror=alert(1)><script>alert(2)</script> '
    status, saved = request(port, 'POST', ENDPOINT, payload={**WRITE, 'text': text})
    assert status == 200 and saved['text'] == text
    assert request(port)[1]['text'] == text
    status, saved = request(port, 'POST', ENDPOINT, payload={**WRITE, 'text': '😀' * 50_000, 'revision': 1})
    assert status == 200 and saved['text'] == '😀' * 50_000
    assert request(port, 'POST', ENDPOINT, payload={**WRITE, 'text': '😀' * 50_001, 'revision': 2})[0] == 400
    assert request(port) == (200, saved)


@pytest.mark.parametrize('failure', [OSError, sqlite3.OperationalError])
def test_notebook_storage_failures_are_503_and_preserve_saved_draft(notebook_server, monkeypatch, failure):
    port, _, _ = notebook_server
    assert request(port, 'POST', ENDPOINT, payload=WRITE)[0] == 200

    def unavailable(*args):
        raise failure('Private filesystem path must not leak')

    for method, request_method, kwargs in (
        ('notebook_snapshot', 'GET', {}),
        ('change_notebook', 'POST', {'payload': {**WRITE, 'text': 'New draft', 'revision': 1}}),
    ):
        with monkeypatch.context() as context:
            context.setattr(helpers.web_chat.ReaderNotes, method, unavailable)
            path = ENDPOINT + '?paper_id=P1' if request_method == 'GET' else ENDPOINT
            status, result = request(port, request_method, path, **kwargs)
            assert status == 503 and 'Private filesystem path' not in result['error']
    assert request(port)[1]['text'] == WRITE['text']
    assert request(port)[1]['revision'] == 1
