from contextlib import contextmanager
import io
import json

import pytest

from originrag import writing_language as language, model_runtime
from test_writing_collaboration import accounts as accounts, workspace as workspace, project, share
from test_writing_language import payload, suggestion
import test_web_chat as http


def install_stream(monkeypatch, chunks):
    seen = []
    @contextmanager
    def open_response(request, timeout):
        seen.append((request.full_url, json.loads(request.data), timeout))
        yield io.BytesIO(b''.join(json.dumps(item).encode() + b'\n' for item in chunks))
    monkeypatch.setattr(language, 'urlopen', open_response)
    monkeypatch.setattr(language, 'answer_model', lambda: 'qwen3:test')
    return seen


def test_live_stream_emits_only_complete_validated_suggestions(monkeypatch):
    raw = json.dumps({'suggestions': [suggestion()]})
    split = raw.index('}') + 1
    seen = install_stream(monkeypatch, [
        {'response': raw[:split], 'done': False}, {'response': raw[split:], 'done': True, 'done_reason': 'stop'},
    ])
    events = []
    result = language.check_language_stream('We recieve samples.', 1, (), events.append)
    assert result['suggestions'] == [{**suggestion(), 'line': 1}]
    assert any(event['type'] == 'progress' for event in events)
    assert next(event for event in events if event['type'] == 'preview')['suggestions'] == result['suggestions']
    assert seen[0][1]['stream'] is True and seen[0][1]['think'] is False
    assert seen[0][1]['format'] == 'json' and seen[0][1]['keep_alive'] == '60s'


def test_fast_project_stream_batches_previews_without_losing_the_tail(monkeypatch):
    raw = json.dumps({'answer': 'abc' * 1000, 'edits': []})
    install_stream(monkeypatch, [{'response': char} for char in raw] + [{'done': True}])
    frames = []
    text, _ = language.generate_json_stream('test', lambda prefix, model: frames.append(prefix), preview_interval=60)
    assert text == raw == frames[-1] and frames[0] == raw[:1]
    assert 2 <= len(frames) < 12
    assert all(b.startswith(a) for a, b in zip(frames, frames[1:]))


@pytest.mark.parametrize('chunks', [
    [{'response': '{"suggestions":[]}', 'done': False}],
    [{'response': '{"suggestions":[]}', 'done': True, 'done_reason': 'length'}],
    [{'response': 'invalid JSON', 'done': True}],
    [{'response': 5, 'done': True}],
    [{'error': 'private backend details'}],
])
def test_incomplete_or_invalid_stream_never_returns_an_applicable_result(monkeypatch, chunks):
    install_stream(monkeypatch, chunks)
    with pytest.raises(language.LanguageUnavailable) as caught:
        language.check_language_stream('We recieve samples.', 1, (), lambda event: None)
    assert 'private backend details' not in str(caught.value)


def test_stream_uses_shared_selected_model_endpoint_and_blocks_colibri(monkeypatch):
    seen = install_stream(monkeypatch, [{'response': '{"suggestions":[]}', 'done': True}])
    runtime = {'provider': 'ollama', 'model': 'selected:qwen', 'base_url': 'http://127.0.0.1:11435'}
    token = model_runtime.CURRENT_RUNTIME.set(runtime)
    try:
        assert language.check_language_stream('Text.', 1, (), lambda event: None)['model'] == 'selected:qwen'
        assert seen[0][0] == 'http://127.0.0.1:11435/api/generate'
    finally:
        model_runtime.CURRENT_RUNTIME.reset(token)
    token = model_runtime.CURRENT_RUNTIME.set({'provider': 'colibri'})
    try:
        with pytest.raises(language.LanguageUnavailable, match='Ollama'):
            language.check_language_stream('Text.', 1, (), lambda event: None)
    finally:
        model_runtime.CURRENT_RUNTIME.reset(token)
    assert len(seen) == 1


def test_overlapping_corrections_are_filtered_before_preview():
    raw = json.dumps({'suggestions': [suggestion('recieve samples', 'receive samples'), suggestion()]})
    result = language.parse_suggestions(raw, 'We recieve samples.', 1)
    assert len(result['suggestions']) == 1 and result['filtered'] == 1


def test_default_writing_model_uses_its_configured_private_runtime(monkeypatch):
    seen = install_stream(monkeypatch, [{'response': '{"suggestions":[]}', 'done': True}])
    monkeypatch.setenv('ORIGINRAG_OLLAMA_MODEL_URLS', '{"qwen3:test":"http://127.0.0.1:11435"}')
    result = language.check_language_stream('A sentence.', 1, (), lambda event: None)
    assert result['model'] == 'qwen3:test'
    assert seen[0][0] == 'http://127.0.0.1:11435/api/generate'


def test_stream_requires_project_access_csrf_and_rechecks_revision(workspace, monkeypatch):
    call, port, headers, _, users, root = workspace
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '1')
    p = project(call)
    p = call('POST', payload={**p, 'action': 'save_project', 'files': {'main.tex': 'We recieve samples.'}})
    sent = []
    def check(text, line, blocked, emit):
        sent.append(text)
        result = {'suggestions': [{**suggestion(), 'line': 1}], 'filtered': 0, 'model': 'test'}
        emit({'type': 'preview', **result})
        return result
    monkeypatch.setattr(language, 'check_language_stream', check)
    body = {**payload(p), 'stream': True}
    assert http.http_request(port, 'POST', '/api/writing', headers={'Cookie': headers['alice']['Cookie']}, payload=body)[0] == 403
    call('POST', who='carol', payload=body, status=404)
    assert not sent
    share(call, p, users['bob'])
    status, response_headers, data, _ = http.http_request(port, 'POST', '/api/writing', headers=headers['bob'], payload=body)
    events = [json.loads(line) for line in data.splitlines()]
    assert status == 200 and response_headers['content-type'].startswith('application/x-ndjson')
    assert events[-1]['type'] == 'done' and events[-1]['revision'] == p['revision']
    assert call('GET', '/api/writing?id=' + p['id'])['files'] == p['files']
    def stale(text, line, blocked, emit):
        call('POST', payload={**p, 'action': 'save_project', 'files': {'main.tex': 'Updated.'}})
        return {'suggestions': [], 'model': 'test'}
    monkeypatch.setattr(language, 'check_language_stream', stale)
    _, _, data, _ = http.http_request(port, 'POST', '/api/writing', headers=headers['alice'], payload=body)
    events = [json.loads(line) for line in data.splitlines()]
    assert events[-1]['type'] == 'error' and not any(event['type'] == 'done' for event in events)


def test_stream_late_revocation_discards_result(workspace, monkeypatch):
    call, port, headers, _, users, _ = workspace
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '1')
    p = project(call)
    p = call('POST', payload={**p, 'action': 'save_project', 'files': {'main.tex': 'We recieve samples.'}})
    share(call, p, users['bob'])
    def revoke(text, line, blocked, emit):
        share(call, p, users['bob'], action='remove')
        return {'suggestions': [suggestion()], 'model': 'test'}
    monkeypatch.setattr(language, 'check_language_stream', revoke)
    _, _, data, _ = http.http_request(port, 'POST', '/api/writing', headers=headers['bob'], payload={**payload(p), 'stream': True})
    events = [json.loads(line) for line in data.splitlines()]
    assert events[-1]['type'] == 'error' and not any(event['type'] == 'done' for event in events)
