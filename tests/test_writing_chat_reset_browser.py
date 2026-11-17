import json

import pytest

from originrag import writing_chat
from test_writing import workspace_server as workspace_server
from test_writing_chat_browser import model_list as model_list, make_project, get_project, edits
from test_writing_chat_window_browser import open_popup
from test_writing_language_browser import select_line, ask_selection


def send(page, question):
    page.locator('#projectChatInput').fill(question)
    page.locator('#projectChatSend').click()


def delete_chat(page, name):
    page.locator('#projectChatListToggle').click()
    page.get_by_role('button', name='Delete chat: ' + name, exact=True).click()
    page.get_by_role('button', name='Delete chat permanently', exact=True).click()
    page.locator('#projectChatListToggle').click()


@pytest.mark.parametrize('detached', [False, True])
def test_new_chat_clears_history_and_selection_but_keeps_project_context(model_list, monkeypatch, detached):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    calls = []

    def reply(prepared, emit):
        calls.append(prepared)
        return {'answer': 'Previous mistaken refusal.' if len(calls) == 1 else 'Fresh project reply.',
                'edits': [], 'files': prepared['files']}

    monkeypatch.setattr(writing_chat, 'chat', reply)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        key, files = make_project(page, base)
        select_line(page, 'A first sentence.')
        ask_selection(page)
        chat = open_popup(page) if detached else page
        dialogs, errors = [], []
        chat.on('dialog', lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        chat.on('pageerror', lambda error: errors.append(str(error)))
        send(chat, 'Explain the selection.')
        pw.expect(chat.locator('.project-chat-message.assistant')).to_contain_text('mistaken refusal')
        pw.expect(chat.locator('#projectChatStop')).to_be_hidden()
        assert calls[0]['selection'] is not None
        before = chat.locator('#projectChatPanel').bounding_box()
        chat.get_by_role('button', name='New chat', exact=True).click()
        pw.expect(chat.locator('.project-chat-message')).to_have_count(0)
        pw.expect(chat.locator('#projectChatSelectionContext')).to_be_hidden()
        pw.expect(chat.locator('#projectChatInput')).to_be_focused()
        pw.expect(chat.locator('#projectChatSend')).to_be_disabled()
        assert chat.locator('#projectChatPanel').bounding_box() == before
        assert get_project(page, key)['files'] == files
        send(chat, 'Explain main.tex in this project.')
        pw.expect(chat.locator('.project-chat-message.assistant')).to_contain_text('Fresh project reply')
        assert len(calls) == 2 and calls[1]['selection'] is None
        data = json.loads(calls[1]['prompt'].split('\n')[-1])
        assert data['conversation'] == []
        assert any(row['file'] == 'main.tex' and row.get('content') == files['main.tex'] for row in data['files'])
        assert get_project(page, key)['files'] == files
        if detached:
            chat.locator('#projectChatWindow').click()
            pw.expect(page.locator('.project-chat-message')).to_have_count(2)
            pw.expect(page.locator('.project-chat-message.assistant')).to_contain_text('Fresh project reply')
        assert not dialogs and not errors
        browser.close()


def test_new_chat_discards_unaccepted_proposals_and_preserves_accepted_edits_and_undo(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    monkeypatch.setattr(writing_chat, 'chat', lambda prepared, emit: {
        'answer': 'Two proposals.', 'edits': edits(), 'files': prepared['files']})
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        key, files = make_project(page, base)
        page.locator('#projectChat').click()
        send(page, 'Rewrite both sentences.')
        pw.expect(page.locator('.cm-ai-accept')).to_be_enabled()
        page.locator('.cm-ai-accept').click()
        pw.expect(page.locator('.cm-ai-proposed')).to_have_text('A clearer second sentence.')
        pw.expect(page.locator('#projectChatNew')).to_be_enabled()
        pw.expect(page.locator('#savedState')).to_have_text('All changes saved')
        saved = get_project(page, key)['files']
        assert saved['main.tex'] == files['main.tex'].replace('A first sentence.', 'A clearer first sentence.')
        assert saved['chapter.tex'] == files['chapter.tex']
        page.locator('#projectChatNew').click()
        pw.expect(page.locator('.cm-ai-review')).to_have_count(0)
        pw.expect(page.locator('.project-chat-message')).to_have_count(0)
        assert get_project(page, key)['files'] == saved
        page.locator('[data-file="main.tex"]').click()
        page.get_by_role('button', name='Undo', exact=True).click()
        pw.expect(page.locator('#source .cm-line')).to_have_text(files['main.tex'].split('\n'))
        browser.close()


def test_deleting_chat_during_autosave_never_sends_the_old_question(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    calls = []

    def reply(prepared, emit):
        calls.append(prepared)
        return {'answer': 'Fresh draft received.', 'edits': [], 'files': prepared['files']}

    monkeypatch.setattr(writing_chat, 'chat', reply)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        key, _ = make_project(page, base)
        page.evaluate('''() => {
          const fetch = window.fetch.bind(window);
          window.fetch = (url, options) => {
            const payload = typeof options?.body === 'string' ? JSON.parse(options.body) : {};
            if (payload.action !== 'save_project' || window.releaseSave) return fetch(url, options);
            return new Promise((resolve, reject) => {
              window.releaseSave = () => fetch(url, options).then(resolve, reject);
            });
          };
        }''')
        draft = 'This unsaved text must survive a chat reset.'
        page.locator('#source .cm-content').fill(draft)
        page.locator('#projectChat').click()
        send(page, 'Old question while saving.')
        page.wait_for_function('typeof window.releaseSave === "function"')
        delete_chat(page, 'Old question while saving.')
        send(page, 'New question about my draft.')
        page.evaluate('window.releaseSave()')
        pw.expect(page.locator('.project-chat-message.assistant')).to_contain_text('Fresh draft received.')
        assert len(calls) == 1
        data = json.loads(calls[0]['prompt'].split('\n')[-1])
        assert data['request'] == 'New question about my draft.' and data['conversation'] == []
        assert draft in calls[0]['prompt']
        assert get_project(page, key)['files']['main.tex'] == draft
        assert page.locator('.project-chat-message').count() == 2
        browser.close()


def test_chat_keeps_visible_messages_but_sends_only_recent_history(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    calls = []

    def reply(prepared, emit):
        calls.append(json.loads(prepared['prompt'].split('\n')[-1]))
        return {'answer': f'Answer {len(calls) - 1}.', 'edits': [], 'files': prepared['files']}

    monkeypatch.setattr(writing_chat, 'chat', reply)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        make_project(page, base)
        page.locator('#projectChat').click()
        for number in range(6):
            send(page, f'Question {number}.')
            pw.expect(page.locator('.project-chat-message.assistant').last).to_contain_text(f'Answer {number}.')
            pw.expect(page.locator('#projectChatStop')).to_be_hidden()
        assert page.locator('.project-chat-message').count() == 12
        assert len(calls[-1]['conversation']) == 8
        assert calls[-1]['conversation'][0] == {'role': 'user', 'content': 'Question 1.'}
        assert calls[-1]['conversation'][-1] == {'role': 'assistant', 'content': 'Answer 4.'}
        assert any(row['file'] == 'main.tex' for row in calls[-1]['files'])
        browser.close()


def test_delete_during_generation_ignores_late_events_and_cannot_cancel_the_new_reply(model_list):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        key, files = make_project(page, base)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.evaluate('''() => {
          const fetch = window.fetch.bind(window), schedule = window.setTimeout.bind(window);
          window.chatRequests = []; window.chatTimeouts = [];
          window.setTimeout = (fn, ms, ...args) => {
            if (ms === 135000) window.chatTimeouts.push(fn);
            return schedule(fn, ms, ...args);
          };
          window.fetch = (url, options) => {
            const payload = typeof options?.body === 'string' ? JSON.parse(options.body) : {};
            if (payload.action !== 'project_chat') return fetch(url, options);
            let stream;
            const body = new ReadableStream({start(controller) { stream = controller; }});
            const emit = data => stream.enqueue(new TextEncoder().encode(JSON.stringify(data) + '\\n'));
            window.chatRequests.push({payload, signal: options.signal, emit, finish(answer) {
              emit({type:'done', answer, edits:[], files:['main.tex'], revision:payload.revision}); stream.close();
            }});
            emit({type:'started'});
            return Promise.resolve(new Response(body, {headers:{'Content-Type':'application/x-ndjson'}}));
          };
        }''')
        page.locator('#projectChat').click()
        send(page, 'Old request.')
        page.wait_for_function('window.chatRequests.length === 1')
        pw.expect(page.locator('.project-chat-text').last).to_have_text('Thinking…')
        start = files['main.tex'].index('A first sentence.')
        page.evaluate('event => window.chatRequests[0].emit(event)', {
            'type': 'edit_preview', 'file': 'main.tex', 'original': 'A first sentence.',
            'replacement': 'Unaccepted old draft.', 'from': start, 'to': start + len('A first sentence.')})
        pw.expect(page.locator('.cm-ai-accept')).to_be_disabled()
        delete_chat(page, 'Old request.')
        pw.expect(page.locator('.cm-ai-review')).to_have_count(0)
        pw.expect(page.locator('.project-chat-message')).to_have_count(0)
        assert page.evaluate('window.chatRequests[0].signal.aborted')
        send(page, 'Fresh request.')
        page.wait_for_function('window.chatRequests.length === 2')
        assert page.evaluate('window.chatRequests[1].payload.history') == []
        page.evaluate('''() => {
          window.chatTimeouts[0]();
          window.chatRequests[0].emit({type:'answer_delta', text:'Obsolete answer.'});
          window.chatRequests[0].finish('Obsolete final answer.');
        }''')
        page.wait_for_timeout(80)
        assert not page.evaluate('window.chatRequests[1].signal.aborted')
        pw.expect(page.locator('#projectChatStop')).to_be_visible()
        pw.expect(page.locator('.project-chat-text').last).to_have_text('Thinking…')
        pw.expect(page.locator('.cm-ai-review')).to_have_count(0)
        page.evaluate("window.chatRequests[1].emit({type:'answer_delta', text:'Fresh response.'})")
        pw.expect(page.locator('.project-chat-text').last).to_have_text('Fresh response.')
        page.evaluate("window.chatRequests[1].finish('Fresh response.')")
        pw.expect(page.locator('#projectChatStop')).to_be_hidden()
        assert page.locator('.project-chat-message').count() == 2
        assert get_project(page, key)['files'] == files and not errors
        send(page, 'Follow-up.')
        page.wait_for_function('window.chatRequests.length === 3')
        assert page.evaluate('window.chatRequests[2].payload.history') == [
            {'role': 'user', 'content': 'Fresh request.'}, {'role': 'assistant', 'content': 'Fresh response.'}]
        page.evaluate("window.chatRequests[2].finish('Follow-up response.')")
        pw.expect(page.locator('#projectChatStop')).to_be_hidden()
        browser.close()
