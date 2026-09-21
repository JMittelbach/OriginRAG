import json
from pathlib import Path
from urllib.parse import urlparse

import pytest


ROOT = Path(__file__).resolve().parents[1]
HTML = '''<!doctype html><html><body>
<section id="paperNotebook">
<textarea id="paperNotes" maxlength="50000" disabled></textarea>
<output id="paperNotesState" aria-live="polite"></output>
<button id="paperNotesSave">Save notes</button>
<button id="paperNotesReload">Reload saved</button>
<button id="paperNotesExport">Export draft</button><output id="paperNotesCount"></output>
<section id="paperNotesConflict" hidden>
<textarea id="paperNotesSavedVersion" readonly></textarea>
<button id="paperNotesUseSaved">Use saved</button>
<button id="paperNotesKeepDraft">Keep draft</button></section></section>
<script type="module">
import {paperNotebook} from '/assets/reader/paper-notebook.mjs';
window.messages=[];
window.notebook=paperNotebook({paperId:'P1',status:(text,error)=>messages.push({text,error})});
window.notebookReady=notebook.init();
</script></body></html>'''


@pytest.fixture
def notebook_page():
    playwright = pytest.importorskip('playwright.sync_api')
    profiles = {
        'alice': {'paper_id': 'P1', 'text': '', 'revision': 0, 'updated_at': None},
        'bob': {'paper_id': 'P1', 'text': 'Bob private notes', 'revision': 1,
                'updated_at': 1789956000000},
    }
    state = {'profiles': profiles, 'posts': [], 'gets': 0, 'mode': 'ok',
             'held': [], 'fail_get': False, 'headers': []}
    assets = {
        '/' + path: (ROOT / path).read_text()
        for path in ['assets/reader/paper-notebook.mjs', 'assets/workspace/request.mjs']
    }

    def respond(route, record, payload):
        if payload['revision'] != record['revision']:
            route.fulfill(status=409, content_type='application/json',
                          body=json.dumps({'error': 'Saved notes changed in another tab.'}))
            return
        record.update(text=payload['text'], revision=record['revision'] + 1,
                      updated_at=1789956000000 + record['revision'])
        if state['mode'].startswith('lost_ack'):
            if state['mode'] == 'lost_ack_unavailable':
                state['fail_get'] = True
            route.fulfill(status=503, content_type='application/json',
                          body='{"error":"Response lost after saving."}')
        else:
            route.fulfill(content_type='application/json', body=json.dumps(record))

    def handle(route):
        path = urlparse(route.request.url).path
        if path in assets:
            route.fulfill(content_type='text/javascript', body=assets[path])
        elif path == '/api/reader/notebook':
            cookie = route.request.header_value('cookie') or ''
            record = profiles['bob' if 'pfempire_csrf=bob' in cookie else 'alice']
            if route.request.method == 'GET':
                state['gets'] += 1
                if state['fail_get']:
                    route.fulfill(status=503, content_type='application/json',
                                  body='{"error":"Notebook temporarily unavailable."}')
                else:
                    route.fulfill(content_type='application/json', body=json.dumps(record))
            else:
                payload = route.request.post_data_json
                state['posts'].append(payload)
                state['headers'].append(route.request.all_headers())
                if state['mode'] == 'hold':
                    state['held'].append((route, record, payload))
                elif state['mode'] == 'reject':
                    route.fulfill(status=503, content_type='application/json',
                                  body='{"error":"Notebook temporarily unavailable."}')
                else:
                    respond(route, record, payload)
        else:
            route.fulfill(content_type='text/html', body=HTML)

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(accept_downloads=True)
        context.add_cookies([{'name': 'pfempire_csrf', 'value': 'alice',
                             'url': 'http://notebook.test'}])
        page = context.new_page()
        page.expose_function('heldNotebookWrites', lambda: len(state['held']))
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', handle)
        page.clock.install()
        page.goto('http://notebook.test/reader?paper_id=P1')
        page.wait_for_function('window.notebook && !document.querySelector("#paperNotes").disabled')
        state['release'] = lambda: respond(*state['held'].pop(0))
        yield page, state, playwright
        assert not errors
        context.close()
        browser.close()


def saved(page):
    page.wait_for_function('document.querySelector("#paperNotesState").dataset.state === "saved"')
    assert page.evaluate('notebook.dirty() || notebook.saving()') is False


def test_notebook_debounces_plain_text_autosave_and_reload(notebook_page):
    page, state, playwright = notebook_page
    notes = page.locator('#paperNotes')
    notes.fill('first')
    page.clock.fast_forward(1100)
    assert state['posts'] == []
    text = '<img src=x onerror="window.injected=true">\n**Plain text** 😀'
    notes.fill(text)
    page.clock.fast_forward(1100)
    assert state['posts'] == []
    page.clock.fast_forward(100)
    saved(page)
    assert state['posts'] == [{'paper_id': 'P1', 'text': text, 'revision': 0}]
    assert state['headers'][0]['x-csrf-token'] == 'alice'
    assert 'pfempire_csrf=alice' in state['headers'][0]['cookie']
    assert page.locator('#paperNotebook img').count() == 0
    assert page.evaluate('window.injected') is None
    playwright.expect(page.locator('#paperNotesCount')).to_have_text(f'{len(text)} / 50,000 characters')
    page.reload()
    playwright.expect(notes).to_have_value(text)
    notes.fill('')
    page.locator('#paperNotesSave').click()
    saved(page)
    assert state['profiles']['alice']['text'] == ''
    assert state['profiles']['alice']['revision'] == 2
    assert page.evaluate('localStorage.length + sessionStorage.length') == 0


def test_notebook_save_drains_edits_during_single_flight_for_logout(notebook_page):
    page, state, playwright = notebook_page
    state['mode'] = 'hold'
    page.locator('#paperNotes').fill('first version')
    with page.expect_request(lambda request: request.method == 'POST'):
        page.locator('#paperNotesSave').click()
    page.wait_for_function('async () => notebook.saving() && await heldNotebookWrites() === 1')
    assert len(state['held']) == 1
    page.locator('#paperNotes').fill('latest version')
    page.evaluate('''()=>{
      window.logoutResult='pending';
      notebook.save().then(()=>window.logoutResult='saved',()=>window.logoutResult='blocked');
    }''')
    assert len(state['posts']) == 1
    with page.expect_request(lambda request: request.method == 'POST'):
        state['release']()
    page.wait_for_function('async () => await heldNotebookWrites() === 1')
    assert len(state['held']) == 1
    assert state['posts'][1] == {'paper_id': 'P1', 'text': 'latest version', 'revision': 1}
    assert page.evaluate('logoutResult') == 'pending'
    state['release']()
    page.wait_for_function('logoutResult === "saved"')
    saved(page)
    playwright.expect(page.locator('#paperNotes')).to_have_value('latest version')
    assert state['profiles']['alice']['text'] == 'latest version'


@pytest.mark.parametrize('unavailable', [False, True])
def test_notebook_reconciles_lost_ack_without_duplicate_write(notebook_page, unavailable):
    page, state, playwright = notebook_page
    state['mode'] = 'lost_ack_unavailable' if unavailable else 'lost_ack'
    page.locator('#paperNotes').fill('saved despite response failure')
    page.locator('#paperNotesSave').click()
    if unavailable:
        playwright.expect(page.locator('#paperNotesState')).to_have_attribute('data-state', 'error')
        assert page.evaluate('notebook.dirty()')
        page.clock.fast_forward(6000)
        assert len(state['posts']) == 1
        state['fail_get'] = False
        page.locator('#paperNotesSave').click()
    saved(page)
    assert len(state['posts']) == 1
    assert state['gets'] >= 2
    playwright.expect(page.locator('#paperNotes')).to_have_value('saved despite response failure')


def test_notebook_preserves_failed_draft_without_automatic_retries(notebook_page):
    page, state, playwright = notebook_page
    state['mode'] = 'reject'
    page.locator('#paperNotes').fill('important draft')
    page.evaluate('''()=>{
      window.logoutResult='pending';
      notebook.save().then(()=>window.logoutResult='saved',()=>window.logoutResult='blocked');
    }''')
    page.wait_for_function('logoutResult === "blocked"')
    assert page.evaluate('notebook.dirty()')
    playwright.expect(page.locator('#paperNotes')).to_have_value('important draft')
    page.locator('#paperNotes').fill('important draft continued')
    page.clock.fast_forward(6000)
    assert len(state['posts']) == 1
    playwright.expect(page.locator('#paperNotesSave')).to_have_text('Retry save')
    before = state['gets']
    state['mode'] = 'ok'
    page.locator('#paperNotesSave').click()
    saved(page)
    assert state['gets'] > before
    assert state['posts'][1]['text'] == 'important draft continued'
    assert state['posts'][1]['revision'] == 0


def test_notebook_conflict_requires_review_and_explicit_resolution(notebook_page):
    page, state, playwright = notebook_page
    page.locator('#paperNotes').fill('my draft')
    state['profiles']['alice'].update(text='other tab', revision=1, updated_at=1789956000000)
    page.locator('#paperNotesSave').click()
    playwright.expect(page.locator('#paperNotesConflict')).to_be_visible()
    playwright.expect(page.locator('#paperNotes')).to_have_value('my draft')
    playwright.expect(page.locator('#paperNotesSavedVersion')).to_have_value('other tab')
    page.locator('#paperNotes').fill('my continued draft')
    page.clock.fast_forward(6000)
    assert len(state['posts']) == 1
    state['profiles']['alice'].update(text='other tab changed again', revision=2)
    page.locator('#paperNotesKeepDraft').click()
    playwright.expect(page.locator('#paperNotesSavedVersion')).to_have_value('other tab changed again')
    assert len(state['posts']) == 1
    page.locator('#paperNotesKeepDraft').click()
    saved(page)
    assert state['posts'][1] == {'paper_id': 'P1', 'text': 'my continued draft', 'revision': 2}
    page.locator('#paperNotes').fill('unsaved replacement')
    state['profiles']['alice'].update(text='saved replacement', revision=4)
    page.locator('#paperNotesSave').click()
    playwright.expect(page.locator('#paperNotesConflict')).to_be_visible()
    page.once('dialog', lambda dialog: dialog.dismiss())
    page.locator('#paperNotesUseSaved').click()
    playwright.expect(page.locator('#paperNotes')).to_have_value('unsaved replacement')
    page.once('dialog', lambda dialog: dialog.accept())
    page.locator('#paperNotesUseSaved').click()
    saved(page)
    playwright.expect(page.locator('#paperNotes')).to_have_value('saved replacement')


def test_notebook_account_change_blocks_old_draft_and_reload_is_private(notebook_page, tmp_path):
    page, state, playwright = notebook_page
    page.locator('#paperNotes').fill('Alice unsaved secret')
    page.context.add_cookies([{'name': 'pfempire_csrf', 'value': 'bob',
                              'url': 'http://notebook.test'}])
    page.clock.fast_forward(1200)
    playwright.expect(page.locator('#paperNotesState')).to_contain_text('Your sign-in changed')
    assert state['posts'] == []
    assert page.evaluate('notebook.dirty()')
    with page.expect_download() as download:
        page.locator('#paperNotesExport').click()
    path = tmp_path / 'draft.txt'
    download.value.save_as(path)
    assert path.read_text() == 'Alice unsaved secret'
    page.reload()
    playwright.expect(page.locator('#paperNotes')).to_have_value('Bob private notes')
    assert page.evaluate('localStorage.length + sessionStorage.length') == 0
    assert state['profiles']['alice']['text'] == ''
    assert state['profiles']['bob']['text'] == 'Bob private notes'


def test_notebook_composition_and_destroy_do_not_autosave_partial_input(notebook_page):
    page, state, playwright = notebook_page
    notes = page.locator('#paperNotes')
    notes.dispatch_event('compositionstart')
    notes.fill('composing')
    page.clock.fast_forward(5000)
    assert state['posts'] == []
    notes.dispatch_event('compositionend')
    page.clock.fast_forward(1200)
    saved(page)
    assert len(state['posts']) == 1
    notes.fill('draft after save')
    page.evaluate('notebook.destroy()')
    page.clock.fast_forward(5000)
    assert len(state['posts']) == 1
