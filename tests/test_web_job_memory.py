import gc
import json
from pathlib import Path
import sys
import threading
import weakref

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import web_chat as web  # noqa: E402


class Result(dict):
    pass


class FakeApp:
    def __init__(self, root, references):
        self.out_dir = root
        self.session_dir = root / 'chat'
        self.session_dir.mkdir(parents=True)
        self.settings = web.WebChatSettings(backend='template')
        self.references = references
        self.buffer = bytearray(1_000_000)

    def run_turn_for_session(self, session, message, settings, progress_callback=None):
        result = Result(answer=message, output_dir=str(session), details='x' * 100_000)
        self.references.append(weakref.ref(result))
        return result


def enqueue(manager, app, callback=None):
    return manager.enqueue(owner_key='alice', app=app, session_dir=app.session_dir, message='A saved answer',
                           settings_update={'profile_instruction': 'x' * 10_000}, completion_callback=callback)['job_id']


def wait_released(manager, job_id):
    with manager.condition:
        assert manager.condition.wait_for(lambda: manager.jobs[job_id].state in manager.terminal_states
                                         and manager.jobs[job_id].app is None, timeout=5)


def test_completed_job_releases_app_callback_result_and_idle_worker_locals(tmp_path):
    references, callback_seen = [], threading.Event()
    app = FakeApp(tmp_path, references)
    class Callback:
        def __init__(self, owner_app):
            self.app = owner_app
        def __call__(self, result, error):
            assert result['answer'] == 'A saved answer' and not error
            callback_seen.set()
    callback = Callback(app)
    app_ref, callback_ref = weakref.ref(app), weakref.ref(callback)
    manager = web.TurnJobManager()
    try:
        job_id = enqueue(manager, app, callback)
        del app, callback
        wait_released(manager, job_id)
        gc.collect()
        job = manager.jobs[job_id]
        assert callback_seen.is_set()
        assert job.result is None and job.app is None and job.completion_callback is None
        assert job.settings_update == {}
        assert app_ref() is None and callback_ref() is None and references[0]() is None
        assert job.result_path.name == f'{job_id}.result.json'
        assert job.result_path.stat().st_mode & 0o777 == 0o600
        fetched = manager.job('alice', job_id)
        assert fetched['result']['answer'] == 'A saved answer'
        assert len(fetched['result']['details']) == 100_000
        assert manager.job('bob', job_id) is None
        assert job.result is None
    finally:
        manager.shutdown()


@pytest.mark.parametrize('tamper', ['owner', 'job_id', 'symlink', 'missing', 'corrupt'])
def test_persisted_result_never_crosses_owner_or_path_boundary(tmp_path, tamper):
    app = FakeApp(tmp_path, [])
    manager = web.TurnJobManager()
    try:
        job_id = enqueue(manager, app)
        wait_released(manager, job_id)
        path = manager.jobs[job_id].result_path
        data = json.loads(path.read_text())
        if tamper in {'owner', 'job_id'}:
            data['owner_scope_hash' if tamper == 'owner' else 'job_id'] = 'not-alice'
            path.write_text(json.dumps(data))
        elif tamper == 'symlink':
            outside = tmp_path / 'foreign-result.json'
            outside.write_text(json.dumps(data))
            path.unlink()
            path.symlink_to(outside)
        elif tamper == 'missing':
            path.unlink()
        else:
            path.write_text('not json')
        fetched = manager.job('alice', job_id)
        assert fetched['state'] == 'complete'
        assert fetched['result_unavailable'] is True
        assert 'result' not in fetched
        assert manager.job('bob', job_id) is None
    finally:
        manager.shutdown()


def test_result_persistence_failure_keeps_answer_in_memory(tmp_path, monkeypatch):
    app = FakeApp(tmp_path, [])
    manager = web.TurnJobManager()
    original = web.write_json_atomically
    def fail_result(path, payload, **kwargs):
        if path.name.endswith('.result.json'):
            raise OSError('Synthetic disk failure')
        return original(path, payload, **kwargs)
    monkeypatch.setattr(web, 'write_json_atomically', fail_result)
    try:
        job_id = enqueue(manager, app)
        wait_released(manager, job_id)
        job = manager.jobs[job_id]
        assert job.result is not None and job.result_path is None
        assert job.app is None and job.completion_callback is None
        assert manager.job('alice', job_id)['result']['answer'] == 'A saved answer'
    finally:
        manager.shutdown()


def test_pending_jobs_keep_live_app_and_cancelled_jobs_release_it(tmp_path):
    app = FakeApp(tmp_path, [])
    manager = web.TurnJobManager(auto_worker=False)
    job_id = enqueue(manager, app, lambda result, error: None)
    with manager.condition:
        manager._release_terminal_locked(manager.jobs[job_id])
    assert manager.jobs[job_id].app is app
    assert manager.jobs[job_id].settings_update
    assert manager.jobs[job_id].completion_callback is not None
    manager.cancel('alice', job_id)
    assert manager.jobs[job_id].app is None
    assert manager.jobs[job_id].settings_update == {}
    assert manager.jobs[job_id].completion_callback is None


def test_recovery_ignores_result_sidecars_and_does_not_retain_app(tmp_path):
    app = FakeApp(tmp_path, [])
    first = web.TurnJobManager(auto_worker=False)
    job_id = enqueue(first, app)
    first.cancel('alice', job_id)
    sidecar = app.session_dir / 'turn_jobs' / f'{job_id}.result.json'
    sidecar.write_text('not valid JSON and must not be interpreted as job metadata')
    recovered = web.TurnJobManager(auto_worker=False)
    recovered.recover_persisted('alice', app)
    assert recovered.job('alice', job_id)['state'] == 'cancelled'
    assert recovered.jobs[job_id].app is None
    assert len(recovered.jobs) == 1


def test_elapsed_time_uses_execution_start_and_stops_on_completion(tmp_path, monkeypatch):
    app = FakeApp(tmp_path, [])
    monkeypatch.setattr(web.time, 'time', lambda: 100.0)
    manager = web.TurnJobManager(auto_worker=False)
    job_id = enqueue(manager, app)
    monkeypatch.setattr(web.time, 'time', lambda: 111.9)
    assert manager.job('alice', job_id)['elapsed_seconds'] == 11
    job = manager.jobs[job_id]
    job.state, job.started_at = 'running', 105.0
    assert manager.job('alice', job_id)['elapsed_seconds'] == 6
    job.state, job.completed_at = 'complete', 110.0
    monkeypatch.setattr(web.time, 'time', lambda: 250.0)
    assert manager.job('alice', job_id)['elapsed_seconds'] == 5
    assert manager.job('bob', job_id) is None
    job.completed_at = 101.0
    assert manager.job('alice', job_id)['elapsed_seconds'] == 0


@pytest.mark.parametrize('value', [float('nan'), float('inf'), '-inf'])
def test_invalid_persisted_timestamps_cannot_break_job_polling(tmp_path, value):
    app = FakeApp(tmp_path, [])
    manager = web.TurnJobManager(auto_worker=False)
    job_id = enqueue(manager, app)
    path = app.session_dir / 'turn_jobs' / f'{job_id}.json'
    payload = json.loads(path.read_text())
    payload['started_at'] = value
    path.write_text(json.dumps(payload))
    recovered = web.TurnJobManager(auto_worker=False)
    recovered.recover_persisted('alice', app)
    assert recovered.owner_jobs('alice') == []


def test_paper_discussion_has_distinct_reading_and_answer_progress():
    assert web.progress_percent('Reading the selected paper') < web.progress_percent('Discussing your paper')
    assert web.progress_percent('Understanding the question and preparing search terms') > 6
