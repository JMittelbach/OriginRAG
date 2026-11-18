import json
import threading

import pytest

from originrag import writing_chat
from test_writing import workspace_server as workspace_server
from test_writing_chat_browser import model_list as model_list, make_project, get_project, edits
from test_writing_chat_window_browser import open_popup
from test_writing_chat_reset_browser import send, delete_chat


def choose(page, name):
    page.locator('#projectChatListToggle').click()
    page.get_by_role('button', name='Open chat: ' + name, exact=True).click()


@pytest.mark.parametrize('popup', [False, True])
def test_named_chats_keep_separate_history_drafts_and_delete_only_the_selected_chat(model_list, monkeypatch, popup):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    calls = []

    def reply(prepared, emit):
        data = json.loads(prepared['prompt'].split('\n')[-1])
        calls.append(data)
        return {'answer': 'Reply to ' + data['request'], 'edits': [], 'files': prepared['files']}

    monkeypatch.setattr(writing_chat, 'chat', reply)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1400, 'height': 950})
        key, files = make_project(page, base)
        page.locator('#projectChat').click()
        chat = open_popup(page) if popup else page
        send(chat, 'First topic.')
        pw.expect(chat.locator('#projectChatStop')).to_be_hidden()
        chat.locator('#projectChatListToggle').click()
        chat.get_by_role('button', name='Rename chat: First topic.', exact=True).click()
        name = '<img src=x> My first chat'
        chat.get_by_role('textbox', name='Chat name', exact=True).fill(name)
        chat.get_by_role('textbox', name='Chat name', exact=True).press('Enter')
        assert chat.locator('#projectChatList img').count() == 0
        chat.get_by_role('button', name='Open chat: ' + name, exact=True).click()
        chat.locator('#projectChatInput').fill('My unsent follow-up.')
        chat.locator('#projectChatNew').click()
        pw.expect(chat.locator('.project-chat-message')).to_have_count(0)
        pw.expect(chat.locator('#projectChatInput')).to_have_value('')
        send(chat, 'Second topic.')
        pw.expect(chat.locator('.project-chat-message.assistant')).to_contain_text('Reply to Second topic.')
        choose(chat, name)
        pw.expect(chat.locator('#projectChatInput')).to_have_value('My unsent follow-up.')
        pw.expect(chat.locator('.project-chat-message.assistant')).to_contain_text('Reply to First topic.')
        chat.locator('#projectChatSend').click()
        pw.expect(chat.locator('.project-chat-message.assistant').last).to_contain_text('My unsent follow-up.')
        assert calls[1]['conversation'] == []
        assert calls[2]['conversation'] == [
            {'role': 'user', 'content': 'First topic.'}, {'role': 'assistant', 'content': 'Reply to First topic.'}]
        pw.expect(chat.locator('#projectChatListToggle')).to_have_text('Chats ▾')
        pw.expect(chat.get_by_role('button', name='Switch to ' + name)).to_have_attribute('aria-pressed', 'true')
        delete_chat(chat, 'Second topic.')
        pw.expect(chat.locator('.project-chat-message')).to_have_count(4)
        chat.locator('#projectChatListToggle').click()
        assert chat.get_by_role('button', name='Open chat: Second topic.', exact=True).count() == 0
        chat.get_by_role('button', name='Delete chat: ' + name, exact=True).click()
        chat.get_by_role('button', name='Keep chat', exact=True).click()
        pw.expect(chat.get_by_role('button', name='Open chat: ' + name, exact=True)).to_be_visible()
        assert get_project(page, key)['files'] == files
        browser.close()


def test_two_chat_requests_run_concurrently_and_background_previews_cannot_overwrite_active_review(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, manager = model_list
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]

    def reply(prepared, emit):
        data = json.loads(prepared['prompt'].split('\n')[-1])
        index = 0 if data['request'] == 'First topic.' else 1
        proposal = edits()[index]
        source = prepared['project']['files'][proposal['file']]
        start = source.index(proposal['original'])
        entered[index].set()
        emit({'type': 'answer_delta', 'text': f'Reply {index + 1} in progress.'})
        emit({'type': 'edit_preview', **proposal, 'from': start, 'to': start + len(proposal['original'])})
        assert release[index].wait(20)
        return {'answer': f'Reply {index + 1} complete.', 'edits': [proposal], 'files': prepared['files']}

    monkeypatch.setattr(writing_chat, 'chat', reply)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1550, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            key, files = make_project(page, base)
            page.locator('#projectChat').click()
            send(page, 'First topic.')
            pw.expect(page.locator('.cm-ai-proposed')).to_have_text('A clearer first sentence.')
            page.locator('#projectChatNew').click()
            pw.expect(page.locator('.cm-ai-review')).to_have_count(0)
            send(page, 'Second topic.')
            pw.expect(page.locator('.cm-ai-proposed')).to_have_text('A clearer second sentence.')
            assert all(event.is_set() for event in entered) and manager.active_requests == 2
            page.locator('#projectChatListToggle').click()
            assert page.locator('.project-chat-list-state').all_text_contents() == ['Running', 'Running']
            page.get_by_role('button', name='Open chat: First topic.', exact=True).click()
            pw.expect(page.locator('.project-chat-message.assistant')).to_contain_text('Reply 1 in progress.')
            pw.expect(page.locator('.cm-ai-proposed')).to_have_text('A clearer first sentence.')
            saved = get_project(page, key)
            third = page.request.post(base + '/api/writing', data={
                'action': 'project_chat', 'id': key, 'revision': saved['revision'],
                'auto_context': True, 'message': 'Third topic.', 'stream': True})
            assert third.status == 409 and 'busy' in third.json()['error'].lower()
            release[1].set()
            page.locator('#projectChatListToggle').click()
            pw.expect(page.locator('.project-chat-list-state').nth(1)).to_have_text('New reply')
            page.locator('#projectChatListToggle').click()
            pw.expect(page.locator('.cm-ai-proposed')).to_have_text('A clearer first sentence.')
            pw.expect(page.locator('#projectChatStop')).to_be_visible()
            release[0].set()
            pw.expect(page.locator('#projectChatStop')).to_be_hidden()
            choose(page, 'Second topic.')
            pw.expect(page.locator('.cm-ai-proposed')).to_have_text('A clearer second sentence.')
            pw.expect(page.locator('.cm-ai-accept')).to_be_enabled()
            page.locator('.cm-ai-accept').click()
            pw.expect(page.locator('#projectChatListToggle')).to_be_enabled()
            pw.expect(page.locator('#savedState')).to_have_text('All changes saved')
            choose(page, 'First topic.')
            pw.expect(page.locator('.cm-ai-review')).to_have_count(0)
            pw.expect(page.locator('#projectChatStatus')).to_contain_text('draft changed')
            result = get_project(page, key)['files']
            assert result['main.tex'] == files['main.tex'] and result['chapter.tex'] == 'A clearer second sentence.'
            assert not errors
        finally:
            for event in release:
                event.set()
            browser.close()
def test_tabs_and_checked_review_survive_library_round_trip(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    def reply(request, emit):
        proposal = edits()[0]
        start = request['project']['files'][proposal['file']].index(proposal['original'])
        emit({'type': 'edit_preview', **proposal, 'from': start, 'to': start + len(proposal['original'])})
        # An unusable provisional anchor must not prevent review of the verified final event.
        emit({'type': 'edit_preview', **proposal, 'from': 0, 'to': len(proposal['original'])})
        return {'answer': 'Here is a suggestion.', 'edits': [proposal], 'files': request['files']}
    monkeypatch.setattr(writing_chat, 'chat', reply)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        key, original = make_project(page, base)
        page.locator('#projectChat').click()
        send(page, 'Fix the first sentence.')
        pw.expect(page.locator('#projectChatReview')).to_be_visible()
        page.locator('#projectChatNew').click()
        page.locator('#projectChatInput').fill('Keep this draft')
        page.locator('nav').get_by_role('link', name='Library', exact=True).click()
        page.locator('nav a[href*="resume"]').click()
        pw.expect(page).to_have_url(base + '/write?id=' + key)
        pw.expect(page.locator('#projectChatPanel')).to_be_visible()
        pw.expect(page.locator('#projectChatInput')).to_have_value('Keep this draft')
        page.get_by_role('button', name='Switch to Fix the first sentence.').click()
        pw.expect(page.locator('.project-chat-message.assistant')).to_have_text('QuillHere is a suggestion.')
        page.locator('#projectChatReview').click()
        assert get_project(page, key)['files'] == original
        page.locator('.cm-ai-accept').click()
        pw.expect(page.locator('#savedState')).to_have_text('All changes saved')
        pw.expect(page.locator('#projectChatReview')).to_be_hidden()
        assert 'A clearer first sentence.' in get_project(page, key)['files']['main.tex']
        browser.close()


def test_asking_about_an_edit_keeps_it_reviewable_and_long_replies_keep_context(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    calls = []
    def reply(request, emit):
        calls.append(json.loads(request['prompt'].split('\n')[-1])['conversation'])
        return {'answer': ('A long explanation. ' * 300 if len(calls) == 1 else 'It is a wording change.'),
                'edits': edits()[:1] if len(calls) == 1 else [], 'files': request['files']}
    monkeypatch.setattr(writing_chat, 'chat', reply)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        key, original = make_project(page, base)
        page.locator('#projectChat').click()
        send(page, 'Make the first sentence clearer.')
        pw.expect(page.locator('#projectChatReview')).to_be_visible()
        send(page, 'Why this wording?')
        pw.expect(page.get_by_role('button', name='Review earlier edit')).to_be_visible()
        assert get_project(page, key)['files'] == original
        assert [m['role'] for m in calls[-1]] == ['user', 'assistant']
        assert calls[-1][-1]['content'].endswith('[shortened]') and len(calls[-1][-1]['content']) <= 4000
        page.locator('#projectChatReview').click()
        page.locator('.cm-ai-accept').click()
        pw.expect(page.locator('#projectChatReview')).to_be_hidden()
        pw.expect(page.locator('#savedState')).to_have_text('All changes saved')
        assert 'A clearer first sentence.' in get_project(page, key)['files']['main.tex']
        browser.close()


def test_one_broken_cached_chat_cannot_block_the_editor(model_list):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        key, files = make_project(page, base)
        page.goto(base + '/library')
        page.evaluate("""id => sessionStorage.setItem('quill.tab.v1:local:chat:' + id,
            JSON.stringify({open:true, active:2, threads:[null, {id:1,history:{}},
              {id:2,name:'Continue',draft:'Keep me',history:[null],selection:{file:'constructor'}}]}))""", key)
        page.goto(base + '/write?id=' + key)
        pw.expect(page.locator('#projectChatInput')).to_have_value('Keep me')
        pw.expect(page.get_by_role('button', name='Switch to Continue')).to_have_attribute('aria-pressed', 'true')
        assert get_project(page, key)['files'] == files
        browser.close()


def test_storage_pressure_prioritises_active_chat_draft(model_list):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        page.goto(base + '/library')
        saved = page.evaluate("""async () => {
          const {quillSession} = await import('/assets/workspace/quill-session.mjs');
          const session = quillSession({authenticated:true, user:{user_id:'synthetic'}});
          const original = Storage.prototype.setItem;
          Storage.prototype.setItem = function(key, value) {
            if (key.endsWith(':chat:quota') && value.length > 400) throw new DOMException('Full','QuotaExceededError');
            return original.call(this, key, value);
          };
          try {
            session.write('chat:quota', {open:true, active:2, threads:[
              {id:1, history:[], draft:'a'.repeat(600)},
              {id:2, history:[{role:'assistant',content:'x'.repeat(5000)}], draft:'My unsent question'}]});
            return session.read('chat:quota');
          } finally { Storage.prototype.setItem = original; }
        }""")
        assert saved['open'] and saved['threads'][0]['id'] == saved['active'] == 2
        assert saved['threads'][0]['draft'] == 'My unsent question'
        browser.close()
