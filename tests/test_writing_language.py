import json
import threading
import time
from types import SimpleNamespace

import pytest

from originrag import writing_language as language
from test_writing_collaboration import accounts as accounts, workspace as workspace, project, share


def suggestion(original='recieve', replacement='receive'):
    return {'original': original, 'replacement': replacement, 'reason': 'Spelling'}


def payload(p, text='We recieve samples.'):
    return {'action': 'language_check', 'id': p['id'], 'revision': p['revision'],
            'file': 'main.tex', 'from': 0, 'to': len(text.encode('utf-16-le')) // 2, 'text': text}


def test_suggestions_are_grounded_and_do_not_edit_latex_numbers_or_ambiguous_words():
    text = r'We recieve samples. \cite{citationKey} $mathVariable$' + '\nRepeated repeated repeated. 42 subjects.'
    items = [suggestion(), suggestion('hallucination'), suggestion('citationKey'),
             suggestion('mathVariable'), suggestion('repeated'), suggestion('42 subjects', '50 subjects'),
             suggestion('samples', r'\input{secrets}'), {'wrong': 'shape'}]
    result = language.parse_suggestions(json.dumps({'suggestions': items}), text, 10)
    assert result['suggestions'] == [{**suggestion(), 'line': 10}]
    assert result['filtered'] == 7


def test_selection_utf16_and_partial_command_argument_protection():
    source = '😀\n' + r'\cite{recieve}' + '\nWe recieve samples.'
    p = {'revision': 1, 'files': {'main.tex': source}}
    start = len(source[:source.index('recieve')].encode('utf-16-le')) // 2
    selected = language.selection(p, {'revision': 1, 'file': 'main.tex', 'from': start, 'to': start + 7, 'text': 'recieve'})
    result = language.parse_suggestions(json.dumps({'suggestions': [suggestion()]}), *selected)
    assert result['suggestions'] == []
    with pytest.raises(ValueError):
        language.selection(p, {'revision': 1, 'file': 'main.tex', 'from': 1, 'to': 2, 'text': 'x'})


def test_spaced_latex_command_arguments_comments_math_are_not_language_edits():
    text = '\\cite [p. 5] {mispeled}\n% commment\n$mathemtics$\n\\textbf{strrong}'
    raw = json.dumps({'suggestions': [suggestion(word, 'corrected') for word in ['mispeled', 'commment', 'mathemtics', 'strrong']]})
    assert language.parse_suggestions(raw, text, 1)['suggestions'] == []


@pytest.mark.parametrize('raw', ['wrong', '[]', '{}', '{"suggestions":null}'])
def test_bad_model_output_is_not_claimed_to_be_correct(raw):
    with pytest.raises(language.LanguageUnavailable):
        language.parse_suggestions(raw, 'text', 1)


def test_bounded_call_and_unavailable_error_do_not_expose_backend_details(monkeypatch):
    seen = {}
    def call(prompt, **kw):
        seen.update(kw)
        return SimpleNamespace(text='{"suggestions":[]}', error=None, model='test')
    monkeypatch.setattr(language, 'call_ollama_generate', call)
    monkeypatch.setattr(language, 'answer_model', lambda: 'existing-model')
    assert language.check_language('A sentence.', 1)['suggestions'] == []
    assert seen['num_ctx'] == 4096 and seen['num_predict'] == 900 and seen['timeout'] == 60
    monkeypatch.setattr(language, 'call_ollama_generate', lambda *a, **kw: SimpleNamespace(text=None, error='private backend credential'))
    with pytest.raises(language.LanguageUnavailable, match='unavailable') as exc:
        language.check_language('Text', 1)
    assert 'credential' not in str(exc.value)


def test_elapsed_deadline_discards_a_late_response(monkeypatch):
    monkeypatch.setattr(language, 'CHECK_SECONDS', 0.01)
    monkeypatch.setattr(language, 'answer_model', lambda: 'test')
    def slow(*args, **kwargs):
        time.sleep(0.03)
        return SimpleNamespace(text='{"suggestions":[]}', error=None, model='test')
    monkeypatch.setattr(language, 'call_ollama_generate', slow)
    with pytest.raises(language.LanguageUnavailable, match='60 seconds'):
        language.check_language('A sentence.', 1)


def test_optional_language_model_does_not_change_rag_configuration(monkeypatch):
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_MODEL', 'installed-proofreader')
    monkeypatch.setenv('ORIGINRAG_OLLAMA_ANSWER_MODEL', 'rag-model')
    seen = {}
    def call(*args, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(text='{"suggestions":[]}', error=None, model=kwargs['model'])
    monkeypatch.setattr(language, 'call_ollama_generate', call)
    assert language.check_language('Text.', 1)['model'] == 'installed-proofreader'
    assert seen['keep_alive'] == '60s'
    import os
    assert os.environ['ORIGINRAG_OLLAMA_ANSWER_MODEL'] == 'rag-model'


def test_authenticated_language_check_owner_editor_revision_and_revocation(workspace, monkeypatch):
    call, _, _, _, users, _ = workspace
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '1')
    p = project(call)
    p = call('POST', payload={**p, 'action': 'save_project', 'files': {'main.tex': 'We recieve samples.'}})
    requests = []
    def check(*args):
        requests.append(args)
        return {'suggestions': [{**suggestion(), 'line': 1}], 'filtered': 0, 'model': 'fake'}
    monkeypatch.setattr(language, 'check_language', check)
    call('POST', who='bob', payload=payload(p), status=404)
    assert requests == []
    share(call, p, users['bob'])
    result = call('POST', who='bob', payload=payload(p))
    assert result['suggestions'][0]['replacement'] == 'receive'
    assert call('GET', '/api/writing?id=' + p['id'])['files'] == p['files']
    call('POST', payload={**payload(p), 'revision': 0}, status=400)
    call('POST', payload={**payload(p), 'text': 'other'}, status=400)
    call('POST', payload={**payload(p), 'to': 5000}, status=400)
    assert len(requests) == 1
    def revoke(*args):
        share(call, p, users['bob'], action='remove')
        return result
    monkeypatch.setattr(language, 'check_language', revoke)
    call('POST', who='bob', payload=payload(p), status=404)
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '0')
    call('POST', payload=payload(p), status=503)


def test_check_calls_are_serialized_without_waiting(workspace, monkeypatch):
    call, _, _, _, _, _ = workspace
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '1')
    p = project(call)
    p = call('POST', payload={**p, 'action': 'save_project', 'files': {'main.tex': 'We recieve samples.'}})
    entered, release = threading.Event(), threading.Event()
    def check(*args):
        entered.set()
        assert release.wait(5)
        return {'suggestions': [], 'filtered': 0, 'model': 'fake'}
    monkeypatch.setattr(language, 'check_language', check)
    outcome = []
    worker = threading.Thread(target=lambda: outcome.append(call('POST', payload=payload(p))))
    worker.start()
    try:
        assert entered.wait(3)
        call('POST', payload=payload(p), status=409)
    finally:
        release.set()
        worker.join(5)
    assert len(outcome) == 1


def test_changed_project_during_check_discards_stale_suggestions(workspace, monkeypatch):
    call, _, _, _, _, _ = workspace
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '1')
    p = project(call)
    p = call('POST', payload={**p, 'action': 'save_project', 'files': {'main.tex': 'We recieve samples.'}})
    def check(*args):
        call('POST', payload={**p, 'action': 'save_project', 'files': {'main.tex': 'New version.'}})
        return {'suggestions': [suggestion()]}
    monkeypatch.setattr(language, 'check_language', check)
    result = call('POST', payload=payload(p), status=409)
    assert 'suggestions' not in result
    assert call('GET', '/api/writing?id=' + p['id'])['files']['main.tex'] == 'New version.'
