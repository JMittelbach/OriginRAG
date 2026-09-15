import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
import threading
import time

import pytest

from originrag.cancellation import Cancellation, TurnCancelled, cancellation_scope, checkpoint, run_process
from originrag.analysis import ollama_client, llm_backends


@pytest.fixture
def waiting_model():
    started, disconnected = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            started.set()
            self.connection.settimeout(5)
            try:
                if self.connection.recv(1) == b'':
                    disconnected.set()
            except OSError:
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', started, disconnected
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.parametrize('client', ['structured', 'legacy'])
def test_cancel_disconnects_model_without_fallback(waiting_model, monkeypatch, client):
    url, started, disconnected = waiting_model
    token = Cancellation()
    outcome = []
    monkeypatch.setattr(llm_backends, '_call_ollama_cli', lambda **kwargs: pytest.fail('Cancellation must not retry through CLI'))
    def run():
        try:
            with cancellation_scope(token):
                if client == 'structured':
                    ollama_client.generate_text('Synthetic test', base_url=url, model='test', timeout=30)
                else:
                    llm_backends._call_ollama_generate('Synthetic test', 'ollama', url, 'test')
            outcome.append('completed')
        except TurnCancelled:
            outcome.append('cancelled')
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert started.wait(3)
    before = time.monotonic()
    assert token.cancel()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert disconnected.wait(1)
    assert outcome == ['cancelled']
    assert time.monotonic() - before < 2


def test_cancellation_is_not_swallowed_and_commit_wins():
    token = Cancellation()
    assert token.cancel()
    with pytest.raises(TurnCancelled):
        with cancellation_scope(token):
            pytest.fail('A cancelled request started')
    finished = Cancellation()
    finished.commit()
    assert not finished.cancel()
    checkpoint()


def test_cli_cancel_kills_only_its_child(tmp_path):
    token = Cancellation()
    marker = tmp_path / 'started'
    outcome = []
    def run():
        try:
            with cancellation_scope(token):
                run_process([sys.executable, '-c', 'import pathlib,time,sys; pathlib.Path(sys.argv[1]).touch(); time.sleep(30)', str(marker)],
                            input='', text=True, capture_output=True, timeout=60)
        except TurnCancelled:
            outcome.append('cancelled')
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 3
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert marker.exists()
    token.cancel()
    thread.join(timeout=2)
    assert not thread.is_alive() and outcome == ['cancelled']


def test_queue_owner_fifo_persistence_and_running_cancel(tmp_path, monkeypatch, waiting_model):
    import test_web_chat as helpers
    web = helpers.web_chat
    url, started, disconnected = waiting_model
    app = web.WebChatApp(web.WebChatSettings(backend='template'), tmp_path / 'sessions')
    ran = []
    def run(session_dir, message, settings_update, progress_callback=None):
        ran.append(message)
        if message == 'first':
            ollama_client.generate_text('Synthetic test', base_url=url, model='test', timeout=30)
        return {'answer': message}
    monkeypatch.setattr(app, 'run_turn_for_session', run)
    manager = web.TurnJobManager(capacity=1)
    def enqueue(owner, message):
        return manager.enqueue(owner_key=owner, app=app, session_dir=app.session_dir, message=message, settings_update={})['job_id']
    first = enqueue('alice', 'first')
    assert started.wait(3)
    second = enqueue('alice', 'second')
    third = enqueue('bob', 'third')
    try:
        assert manager.cancel('bob', first) is None
        assert manager.cancel('alice', second)['state'] == 'cancelled'
        assert manager.cancel('alice', second)['state'] == 'cancelled'
        assert manager.global_status() == {'running': 1, 'queued': 1, 'capacity': 1}
        assert manager.cancel('alice', first)['state'] == 'cancelling'
        assert manager.wait_for_terminal(first, timeout=3)['state'] == 'cancelled'
        assert disconnected.wait(1)
        assert manager.wait_for_terminal(third, timeout=3)['state'] == 'complete'
        assert ran == ['first', 'third']
        assert manager.job('alice', first).get('result') is None
        assert json.loads((app.session_dir / 'turn_jobs' / f'{first}.json').read_text())['state'] == 'cancelled'
        recovered = web.TurnJobManager(auto_worker=False)
        recovered.recover_persisted('alice', app)
        assert recovered.job('alice', first)['state'] == 'cancelled'
    finally:
        manager.shutdown()


def test_cancel_during_corpus_lock_does_not_wait_for_import(tmp_path):
    import test_web_chat as helpers
    web = helpers.web_chat
    app = web.WebChatApp(web.WebChatSettings(backend='template'), tmp_path / 'sessions')
    lock = threading.RLock()
    lock.acquire()
    manager = web.TurnJobManager(corpus_lock=lock)
    try:
        job_id = manager.enqueue(owner_key='alice', app=app, session_dir=app.session_dir, message='Synthetic test', settings_update={})['job_id']
        deadline = time.monotonic() + 2
        while manager.job('alice', job_id)['state'] == 'queued' and time.monotonic() < deadline:
            time.sleep(.01)
        assert manager.cancel('alice', job_id)['state'] == 'cancelling'
        assert manager.wait_for_terminal(job_id, timeout=2)['state'] == 'cancelled'
    finally:
        lock.release()
        manager.shutdown()


def test_cancel_http_requires_owner_and_csrf(tmp_path):
    import test_web_chat as helpers
    web = helpers.web_chat
    auth = helpers.AuthStore(tmp_path / 'auth.sqlite')
    auth.create_user('alice', 'quiet-lantern-river-orbit-47')
    auth.create_user('bob', 'violet-planet-canvas-forest-83')
    registry = web.AuthenticatedWebChatRegistry(web.WebChatSettings(backend='template'), tmp_path / 'sessions')
    handler = web.build_handler(None, auth_store=auth, app_registry=registry, secure_cookies=False)
    handler.turn_job_manager.auto_worker = False
    server = web.create_server('127.0.0.1', 0, handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        alice, _ = helpers.login_headers(port, 'alice', 'quiet-lantern-river-orbit-47')
        bob, _ = helpers.login_headers(port, 'bob', 'violet-planet-canvas-forest-83')
        status, _, raw, _ = helpers.http_request(port, 'POST', '/api/chat', payload={'message': 'Synthetic test'}, headers=alice)
        assert status == 202
        job_id = json.loads(raw)['job_id']
        payload = {'job_id': job_id}
        assert helpers.http_request(port, 'POST', '/api/job/cancel', payload=payload, headers=bob)[0] == 404
        assert helpers.http_request(port, 'POST', '/api/job/cancel', payload=payload, headers={'Cookie': alice['Cookie']})[0] == 403
        assert helpers.http_request(port, 'POST', '/api/job/cancel', payload={'job_id': []}, headers=alice)[0] == 400
        status, _, raw, _ = helpers.http_request(port, 'POST', '/api/job/cancel', payload=payload, headers=alice)
        assert status == 200 and json.loads(raw)['state'] == 'cancelled'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_external_queue_cancel_acknowledged_by_worker(tmp_path, monkeypatch, waiting_model):
    import test_web_chat  # noqa: F401
    import ollama_worker
    url, started, disconnected = waiting_model
    monkeypatch.setenv('PFEMPIRE_LLM_QUEUE_DIR', str(tmp_path))
    token = Cancellation()
    outcome = []
    def call():
        try:
            with cancellation_scope(token):
                llm_backends._call_queue('Synthetic cancellation check')
        except TurnCancelled:
            outcome.append('cancelled')
    caller = threading.Thread(target=call, daemon=True)
    caller.start()
    deadline = time.monotonic() + 3
    while not list((tmp_path / 'jobs').glob('*.json')) and time.monotonic() < deadline:
        time.sleep(.01)
    worker = threading.Thread(target=ollama_worker.process_once, kwargs={
        'jobs_dir': tmp_path / 'jobs', 'results_dir': tmp_path / 'results',
        'completed_dir': tmp_path / 'completed', 'failed_dir': tmp_path / 'failed',
        'base_url': url, 'timeout_seconds': 10}, daemon=True)
    worker.start()
    assert started.wait(3)
    token.cancel()
    caller.join(timeout=3)
    worker.join(timeout=3)
    assert not caller.is_alive() and not worker.is_alive()
    assert disconnected.wait(1)
    assert outcome == ['cancelled']
    result = json.loads(next((tmp_path / 'results').glob('*.json')).read_text())
    assert result['status'] == 'cancelled' and result['answer'] is None


def test_cancelled_result_is_not_added_to_chat_history(tmp_path, monkeypatch):
    import test_web_chat as helpers
    web = helpers.web_chat
    token = Cancellation()
    app = web.WebChatApp(web.WebChatSettings(backend='template'), tmp_path / 'sessions')
    def run(*args, **kwargs):
        token.cancel()
        return {}
    monkeypatch.setattr(web.ask_corpus, 'run_question', run)
    with pytest.raises(TurnCancelled):
        with cancellation_scope(token):
            app.run_turn_for_session(app.session_dir, 'Synthetic question', {})
    assert web.read_session_turns(app.session_dir) == []
    assert not list(app.session_dir.glob('*/public_result.json'))


def test_stop_does_not_undo_an_answer_already_being_saved(tmp_path, monkeypatch):
    import test_web_chat as helpers
    from originrag.cancellation import commit_answer
    web = helpers.web_chat
    app = web.WebChatApp(web.WebChatSettings(backend='template'), tmp_path / 'sessions')
    saving, release = threading.Event(), threading.Event()
    def run(*args, **kwargs):
        commit_answer()
        saving.set()
        assert release.wait(3)
        return {'answer': 'Completed answer'}
    monkeypatch.setattr(app, 'run_turn_for_session', run)
    manager = web.TurnJobManager()
    job_id = manager.enqueue(owner_key='alice', app=app, session_dir=app.session_dir, message='Synthetic question', settings_update={})['job_id']
    try:
        assert saving.wait(2)
        response = manager.cancel('alice', job_id)
        assert response['state'] == 'running' and not response['can_cancel']
        release.set()
        assert manager.wait_for_terminal(job_id, timeout=2)['state'] == 'complete'
        assert manager.job('alice', job_id)['result']['answer'] == 'Completed answer'
    finally:
        release.set()
        manager.shutdown()
