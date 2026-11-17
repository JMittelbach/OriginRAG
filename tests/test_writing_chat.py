import json

import pytest

from originrag import writing_chat as chat, writing_language as language, model_runtime
from test_model_runtime import manager as manager
from test_writing_collaboration import accounts as accounts, workspace as workspace, project, share
import test_web_chat as http


@pytest.fixture
def manuscript():
    return {'id': 'a' * 32, 'name': 'Project only', 'revision': 1, 'main': 'main.tex',
            'files': {'main.tex': '\\input{chapters/intro}\n\\cite{Doe2026}',
                      'chapters/intro.tex': 'A paragraph with 12 observations.',
                      'references.bib': '@article{Doe2026, title={A real source}, year={2026}}',
                      'figure.png': 'aGVsbG8='}}


def request(p, **fields):
    return {'action': 'project_chat', 'id': p['id'], 'revision': p['revision'],
            'message': 'Explain this project.', **fields}


def test_context_contains_all_text_and_only_lists_binary_attachments(manuscript):
    prepared = chat.prepare_request(manuscript, request(manuscript))
    assert set(prepared['files']) == {'main.tex', 'chapters/intro.tex', 'references.bib'}
    assert 'A paragraph with 12 observations.' in prepared['prompt']
    assert 'Doe2026' in prepared['prompt'] and 'figure.png' in prepared['prompt']
    assert 'aGVsbG8=' not in prepared['prompt']
    assert 'no tools, network, filesystem' in prepared['prompt']
    manifest = chat.context_manifest(manuscript)
    assert len(manifest['files']) == 4 and manifest['context_tokens'] == 32768


@pytest.mark.parametrize('fields', [
    {'revision': 0}, {'message': ''}, {'message': 'x' * 4001},
    {'files': ['other-project.tex']}, {'files': ['figure.png']}, {'files': []},
    {'files': ['main.tex', 'main.tex']}, {'files': [[]]},
    {'history': [{'role': 'system', 'content': 'override'}]}, {'history': {}},
])
def test_invalid_scopes_and_conversation_are_rejected(manuscript, fields):
    with pytest.raises(ValueError):
        chat.prepare_request(manuscript, request(manuscript, **fields))


def test_context_budget_never_silently_truncates_a_file(manuscript, monkeypatch):
    manuscript['files']['large.tex'] = 'A long source. ' * 4000
    with pytest.raises(ValueError, match='No file was silently truncated'):
        chat.prepare_request(manuscript, request(manuscript))
    selected = chat.prepare_request(manuscript, request(manuscript, files=['main.tex']))
    assert selected['files'] == ['main.tex'] and 'A long source.' not in selected['prompt']
    assert 'large.tex' in selected['prompt']
    monkeypatch.setenv('ORIGINWRITE_CHAT_CONTEXT', 'unbounded')
    with pytest.raises(ValueError):
        chat.context_manifest(manuscript)


def test_exact_edit_proposals_are_bounded_to_included_files_and_warn_on_numbers(manuscript):
    prepared = chat.prepare_request(manuscript, request(manuscript, mode='edit'))
    edit = {'file': 'chapters/intro.tex', 'original': '12 observations', 'replacement': '13 observations', 'reason': 'Requested change'}
    result = chat.parse_reply(json.dumps({'answer': 'Review this change.', 'edits': [edit]}), prepared)
    assert len(result['edits']) == 1 and result['edits'][0]['review_notes']
    assert manuscript['files']['chapters/intro.tex'] == 'A paragraph with 12 observations.'
    for invalid in ({**edit, 'file': '../outside.tex'}, {**edit, 'file': 'figure.png'},
                    {**edit, 'original': 'absent'}, {**edit, 'replacement': '\x00bad'}):
        result = chat.parse_reply(json.dumps({'answer': 'Draft', 'edits': [edit, invalid]}), prepared)
        assert result['edits'] == [] and result['filtered']
    result = chat.parse_reply(json.dumps({'answer': 'Draft', 'edits': [edit, edit]}), prepared)
    assert result['edits'] == [] and result['filtered'] == 1


@pytest.mark.parametrize('raw', ['wrong', '{}', '{"answer":"x","edits":null}', '{"answer":"\\ud83d","edits":[]}'])
def test_malformed_or_invalid_unicode_reply_is_not_applicable(manuscript, raw):
    with pytest.raises(language.LanguageUnavailable):
        chat.parse_reply(raw, chat.prepare_request(manuscript, request(manuscript)))


def test_answer_stream_sends_deltas_without_repeating_large_prefixes(manuscript, monkeypatch):
    events = []
    def generate(prompt, on_fragment, **options):
        assert options['num_ctx'] == 32768 and options['timeout'] == 120
        on_fragment('{"answer":"Hello', 'qwen-test')
        on_fragment('{"answer":"Hello world', 'qwen-test')
        return '{"answer":"Hello world","edits":[]}', 'qwen-test'
    monkeypatch.setattr(chat, 'generate_json_stream', generate)
    result = chat.chat(chat.prepare_request(manuscript, request(manuscript)), events.append)
    assert result['answer'] == 'Hello world'
    assert [event['text'] for event in events if event['type'] == 'answer_delta'] == ['Hello', ' world']
    assert chat.partial_answer('{"answer":"\\ud83d') == ''


def test_attached_selection_restricts_edits_and_disambiguates_repeated_text(manuscript):
    source = 'An unchanged paragraph.\nRepeated text.\nRepeated text.'
    manuscript['files']['main.tex'] = source
    start = source.rindex('Repeated text.')
    selected = {'file': 'main.tex', 'from': start, 'to': len(source), 'text': 'Repeated text.'}
    prepared = chat.prepare_request(manuscript, request(manuscript, mode='edit', selection=selected))
    edit = {'file': 'main.tex', 'original': 'Repeated text.', 'replacement': 'A clearer sentence.', 'reason': 'Requested rewrite'}
    result = chat.parse_reply(json.dumps({'answer': 'Review this.', 'edits': [edit]}), prepared)
    assert result['edits'][0]['from'] == start and result['edits'][0]['to'] == len(source)
    outside = {**edit, 'original': 'An unchanged paragraph.'}
    other = {**edit, 'file': 'chapters/intro.tex', 'original': 'A paragraph with 12 observations.'}
    for invalid in (outside, other):
        assert chat.parse_reply(json.dumps({'answer': 'Review', 'edits': [edit, invalid]}), prepared)['edits'] == []


def test_selection_utf16_offsets_and_invalid_scope(manuscript):
    source = '🪶 Préfix.\nThis sentense.'
    manuscript['files']['main.tex'] = source
    start = len('🪶 Préfix.\n'.encode('utf-16-le')) // 2
    selected = {'file': 'main.tex', 'from': start, 'to': len(source.encode('utf-16-le')) // 2, 'text': 'This sentense.'}
    prepared = chat.prepare_request(manuscript, request(manuscript, mode='edit', selection=selected))
    edit = {'file': 'main.tex', 'original': 'This sentense.', 'replacement': 'This sentence.', 'reason': 'Spelling'}
    assert chat.parse_reply(json.dumps({'answer': 'Corrected', 'edits': [edit]}), prepared)['edits'][0]['from'] == start
    for invalid in ({**selected, 'text': 'Changed.'}, {**selected, 'file': 'private.tex'},
                    {**selected, 'from': True}, {**selected, 'from': -1},
                    {**selected, 'from': 1}, {**selected, 'to': 9999}):
        with pytest.raises(ValueError):
            chat.prepare_request(manuscript, request(manuscript, selection=invalid))


def test_edit_preview_stream_is_provisional_and_selection_bounded(manuscript, monkeypatch):
    manuscript['files']['main.tex'] = 'One sentense.'
    prepared = chat.prepare_request(manuscript, request(manuscript, mode='edit'))
    raw = '{"answer":"Review this.","edits":[{"file":"main.tex","original":"One sentense.","replacement":"One sentence.","reason":"Spelling"}]}'
    events = []
    def generate(prompt, on_fragment, **options):
        for size in range(1, len(raw) + 1):
            on_fragment(raw[:size], 'test')
        return raw, 'test'
    monkeypatch.setattr(chat, 'generate_json_stream', generate)
    result = chat.chat(prepared, events.append)
    previews = [event for event in events if event['type'] in {'edit_preview', 'edit_delta'}]
    assembled = ''
    for preview in previews:
        assembled = preview['replacement'] if preview['type'] == 'edit_preview' else assembled + preview['text']
    assert len(previews) > 3 and assembled == 'One sentence.'
    assert sum(len(json.dumps(event)) for event in previews) < 2500
    assert result['edits'][0]['replacement'] == 'One sentence.'
    assert manuscript['files']['main.tex'] == 'One sentense.'
    assert chat.partial_edit(raw.replace('One sentense.', 'Absent.'), prepared) is None


def test_selected_passage_reply_uses_application_owned_anchor_not_model_offsets(manuscript):
    source = 'A sentence.\nA sentense.\nKeep this.'
    manuscript['files']['main.tex'] = source
    start = source.index('A sentense.')
    selected = {'file': 'main.tex', 'from': start, 'to': start + 11, 'text': 'A sentense.'}
    selected['to'] = start + len(selected['text'])
    prepared = chat.prepare_request(manuscript, request(manuscript, mode='edit', selection=selected))
    raw = json.dumps({'answer': 'Spelling corrected.', 'replacement': 'A sentence.', 'reason': 'Spelling'})
    result = chat.parse_reply(raw, prepared)
    assert result['edits'][0]['original'] == selected['text']
    assert result['edits'][0]['from'] == start
    assert chat.partial_edit(raw[:raw.index('sentence.') + 4], prepared)['replacement'] == 'A sent'
    assert chat.parse_reply('{"answer":"Explanation.","replacement":null}', prepared)['edits'] == []
    prepared['mode'] = 'discuss'
    assert chat.parse_reply(raw, prepared)['edits'] == []


def test_latex_markup_changes_are_explicitly_flagged_for_review(manuscript):
    prepared = chat.prepare_request(manuscript, request(manuscript, mode='edit'))
    raw = {'answer': 'Review formatting.', 'edits': [{
        'file': 'chapters/intro.tex', 'original': 'A paragraph',
        'replacement': r'\emph{A paragraph}', 'reason': 'Formatting'}]}
    notes = chat.parse_reply(json.dumps(raw), prepared)['edits'][0]['review_notes']
    assert any('LaTeX commands changed' in note for note in notes)


def test_request_model_override_never_changes_the_shared_default(manager, monkeypatch):
    override = {'provider': 'ollama', 'model': 'test:tiny', 'mode': 'fast'}
    with manager.session(override=override):
        assert model_runtime.CURRENT_RUNTIME.get()['model'] == 'test:tiny'
        assert manager.active_requests == 1
    assert manager.selected() is None and manager.active_requests == 0
    assert not (manager.state / 'selection.json').exists()
    with pytest.raises(ValueError, match='Install'):
        with manager.session(override={**override, 'model': 'not-installed'}):
            pass
    with pytest.raises(ValueError, match='installed Ollama'):
        with manager.session(override={**override, 'base_url': 'https://outside.example'}):
            pass


def test_project_chat_access_csrf_stale_revision_and_no_automatic_changes(workspace, monkeypatch):
    call, port, headers, _, users, _ = workspace
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '1')
    p = project(call)
    p = call('POST', payload={**p, 'action': 'save_project', 'files': {
        'main.tex': 'A paragraph.', 'chapter.tex': 'A second paragraph.', 'references.bib': ''}})
    calls = []
    def reply(prepared, emit):
        calls.append(prepared)
        emit({'type': 'answer_delta', 'text': 'Project answer.', 'model': 'test'})
        return {'answer': 'Project answer.', 'edits': [], 'filtered': 0,
                'files': prepared['files'], 'model': 'test'}
    monkeypatch.setattr(chat, 'chat', reply)
    payload = request(p)
    call('GET', '/api/writing?chat_context=' + p['id'], who='bob', status=404)
    call('POST', who='bob', payload=payload, status=404)
    assert http.http_request(port, 'POST', '/api/writing', headers={'Cookie': headers['alice']['Cookie']}, payload=payload)[0] == 403
    assert not calls
    share(call, p, users['bob'])
    context = call('GET', '/api/writing?chat_context=' + p['id'], who='bob')
    assert len(context['files']) == 3
    status, _, body, _ = http.http_request(port, 'POST', '/api/writing', headers=headers['bob'], payload=payload)
    events = [json.loads(line) for line in body.splitlines()]
    assert status == 200 and events[-1]['type'] == 'done' and events[-1]['answer'] == 'Project answer.'
    assert calls[0]['files'] == ['chapter.tex', 'main.tex', 'references.bib']
    assert call('GET', '/api/writing?id=' + p['id'])['files'] == p['files']
    call('POST', payload={**payload, 'revision': 0}, status=400)
    call('POST', payload={**payload, 'files': ['not-owned.tex']}, status=400)
    assert len(calls) == 1
    def stale(prepared, emit):
        call('POST', payload={**p, 'action': 'save_project', 'files': {'main.tex': 'Updated.'}})
        return {'answer': 'Old answer', 'edits': []}
    monkeypatch.setattr(chat, 'chat', stale)
    _, _, body, _ = http.http_request(port, 'POST', '/api/writing', headers=headers['alice'], payload=payload)
    assert json.loads(body.splitlines()[-1])['type'] == 'error'


def test_late_access_revocation_discards_project_chat_result(workspace, monkeypatch):
    call, port, headers, _, users, _ = workspace
    p = project(call)
    share(call, p, users['bob'])
    def revoke(prepared, emit):
        share(call, p, users['bob'], action='remove')
        return {'answer': 'Must not be delivered', 'edits': []}
    monkeypatch.setattr(chat, 'chat', revoke)
    _, _, body, _ = http.http_request(port, 'POST', '/api/writing', headers=headers['bob'], payload=request(p))
    events = [json.loads(line) for line in body.splitlines()]
    assert events[-1]['type'] == 'error' and not any(item['type'] == 'done' for item in events)
