import json
import threading

import pytest

from originrag import writing_chat
from test_writing_chat_browser import model_list as model_list, make_project, get_project
from test_writing import workspace_server as workspace_server


def select_line(page, text):
    page.locator('#source .cm-line').filter(has_text=text).last.click()
    page.keyboard.press('Home')
    page.keyboard.press('Shift+End')


def ask_selection(page):
    page.locator('#source .cm-content').dispatch_event('contextmenu', {'clientX': 400, 'clientY': 400})
    page.get_by_role('menuitem', name='Ask Quill…', exact=True).click()


def test_right_click_opens_free_prompt_without_running_a_check(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    calls = []
    monkeypatch.setattr(writing_chat, 'chat', lambda *args: calls.append(args))
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        key, _ = make_project(page, base)
        code = page.locator('#source .cm-content')
        code.fill('Prefix stays.\nWe recieve samples.\nSuffix stays.')
        select_line(page, 'We recieve samples.')
        ask_selection(page)
        pw.expect(page.locator('#projectChatPanel')).to_be_visible()
        pw.expect(page.locator('#projectChatSelectionSummary')).to_have_text('Selection · main.tex')
        pw.expect(page.locator('#projectChatInput')).to_have_value('')
        pw.expect(page.locator('#projectChatInput')).to_be_focused()
        pw.expect(page.locator('#projectChatSend')).to_be_disabled()
        assert page.locator('#projectChatMode, #languageDialog').count() == 0
        assert not calls
        page.locator('#projectChatClose').click()
        pw.expect(page.locator('#projectChatPanel')).to_be_hidden()
        pw.expect(page.locator('#savedState')).to_have_text('All changes saved')
        assert get_project(page, key)['files']['main.tex'].endswith('Suffix stays.')
        browser.close()


def test_free_prompt_live_preview_selection_apply_confirmation_and_undo(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    release = threading.Event()
    calls = []
    def chat(prepared, emit):
        calls.append(prepared)
        emit({'type': 'answer_delta', 'text': 'Here is your requested rewrite.'})
        selected = prepared['selection']
        emit({'type': 'edit_preview', **selected, 'original': selected['text'], 'replacement': 'We receive'})
        assert release.wait(15)
        raw = {'answer': 'Here is your requested rewrite.', 'edits': [{
            'file': 'main.tex', 'original': 'We recieve samples.',
            'replacement': 'We receive samples.', 'reason': '<img src=x onerror=alert(1)>'}]}
        return {**writing_chat.parse_reply(json.dumps(raw), prepared), 'model': 'test:qwen'}
    monkeypatch.setattr(writing_chat, 'chat', chat)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            key, _ = make_project(page, base)
            code = page.locator('#source .cm-content')
            original = '🪶 Prefix stays.\nWe recieve samples.\nWe recieve samples.\nSuffix stays.'
            code.fill(original)
            select_line(page, 'We recieve samples.')
            ask_selection(page)
            prompt = 'Please correct only the spelling here. Keep the wording and all other text.'
            page.locator('#projectChatInput').fill(prompt)
            pw.expect(page.locator('#projectChatSend')).to_be_enabled()
            page.locator('#projectChatInput').press('Enter')
            pw.expect(page.locator('.project-chat-message.assistant')).to_contain_text('Here is your requested rewrite.')
            pw.expect(page.locator('.cm-ai-proposed')).to_contain_text('We receive')
            pw.expect(page.locator('.cm-ai-accept')).to_be_disabled()
            assert get_project(page, key)['files']['main.tex'] == original
            assert calls[0]['selection']['text'] == 'We recieve samples.'
            assert prompt in calls[0]['prompt']
            release.set()
            pw.expect(page.locator('.cm-ai-accept')).to_be_enabled()
            pw.expect(page.locator('.project-chat-live')).to_have_count(0)
            assert page.locator('#projectChatPanel img').count() == 0
            confirmations = []
            def confirm(dialog):
                confirmations.append(dialog.message)
                dialog.dismiss()
            page.on('dialog', confirm)
            page.locator('.cm-ai-accept').click()
            pw.expect(page.locator('.cm-ai-review')).to_have_count(0)
            pw.expect(page.locator('#savedState')).to_have_text('All changes saved')
            expected = '🪶 Prefix stays.\nWe recieve samples.\nWe receive samples.\nSuffix stays.'
            assert get_project(page, key)['files']['main.tex'] == expected
            page.locator('#projectChatClose').click()
            code.click()
            page.keyboard.press('ControlOrMeta+z')
            pw.expect(code.locator('.cm-line')).to_have_text(original.split('\n'))
            assert confirmations == [] and not errors
        finally:
            release.set()
            browser.close()


def test_ask_toolbar_attach_remove_and_question_preserves_user_prompt(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    calls = []
    def chat(prepared, emit):
        calls.append(prepared)
        return {**writing_chat.parse_reply('{"answer":"This is a LaTeX sentence.","edits":[]}', prepared), 'model': 'test:qwen'}
    monkeypatch.setattr(writing_chat, 'chat', chat)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        key, files = make_project(page, base)
        code = page.locator('#source .cm-content')
        code.click()
        page.keyboard.press('ControlOrMeta+a')
        page.get_by_role('button', name='Ask Quill', exact=True).click()
        pw.expect(page.locator('#projectChatSelectionContext')).to_be_visible()
        page.locator('#projectChatInput').fill('Explain this without changing it.')
        page.locator('#projectChatRemoveSelection').click()
        pw.expect(page.locator('#projectChatSelectionContext')).to_be_hidden()
        pw.expect(page.locator('#projectChatInput')).to_have_value('Explain this without changing it.')
        page.locator('#projectChatSend').click()
        pw.expect(page.locator('.project-chat-message.assistant')).to_contain_text('LaTeX sentence')
        assert calls[0]['selection'] is None and page.locator('.project-chat-apply').count() == 0
        assert get_project(page, key)['files'] == files
        browser.close()


@pytest.mark.parametrize('interrupt', ['stop', 'incomplete'])
def test_interrupted_chat_cannot_apply_partial_selection_edits(model_list, interrupt):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        key, files = make_project(page, base)
        page.locator('#projectChat').click()
        def respond(route):
            if route.request.post_data_json.get('action') != 'project_chat' or interrupt == 'stop':
                if route.request.post_data_json.get('action') != 'project_chat':
                    route.continue_()
            else:
                route.fulfill(content_type='application/x-ndjson', body='{"type":"started"}\n{"type":"edit_preview","file":"main.tex","replacement":"Provisional"}\n')
        page.route('**/api/writing', respond)
        page.locator('#projectChatInput').fill('Correct this sentence.')
        page.locator('#projectChatSend').click()
        if interrupt == 'stop':
            pw.expect(page.locator('#projectChatStop')).to_be_visible()
            page.locator('#projectChatStop').click()
            pw.expect(page.locator('.project-chat-message.assistant')).to_contain_text('Request stopped')
        else:
            pw.expect(page.locator('.project-chat-message.assistant')).to_contain_text('interrupted before completion')
        assert page.locator('.project-chat-apply, .project-chat-live').count() == 0
        assert get_project(page, key)['files'] == files
        browser.close()


def test_assistant_disabled_does_not_intercept_native_context_menu(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '0')
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        make_project(page, base)
        pw.expect(page.locator('#languageCheck')).to_be_hidden()
        page.locator('#source .cm-content').click()
        page.keyboard.press('ControlOrMeta+a')
        page.locator('#source').dispatch_event('contextmenu', {'clientX': 400, 'clientY': 400})
        assert page.locator('.language-context-menu').count() == 0
        browser.close()
