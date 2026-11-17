import json
import os
import threading

import pytest

from originrag import writing_chat
from test_writing import workspace_server as workspace_server
from test_writing_chat_browser import model_list as model_list, make_project, get_project, edits


def model_reply(proposals):
    def reply(prepared, emit):
        return {**writing_chat.parse_reply(json.dumps({'answer': 'Suggested changes are in the editor.', 'edits': proposals}), prepared), 'model': 'test:qwen'}
    return reply


def ask(page, question='Rewrite these paragraphs.'):
    page.locator('#projectChat').click()
    page.locator('#projectChatInput').fill(question)
    page.locator('#projectChatSend').click()


def test_inline_reject_one_accept_other_without_browser_confirm_and_toolbar_undo(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    monkeypatch.setattr(writing_chat, 'chat', model_reply(edits()))
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 1050})
        errors, dialogs = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('dialog', lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        key, files = make_project(page, base)
        ask(page)
        pw.expect(page.locator('.cm-ai-accept')).to_be_enabled()
        pw.expect(page.locator('.cm-ai-original')).to_have_text('A first sentence.')
        pw.expect(page.locator('.cm-ai-proposed')).to_have_text('A clearer first sentence.')
        assert page.locator('#projectChatPanel .cm-ai-review, .project-chat-apply, .project-chat-proposal').count() == 0
        assert get_project(page, key)['files'] == files
        page.locator('.cm-ai-reject').click()
        pw.expect(page.locator('.cm-ai-proposed')).to_have_text('A clearer second sentence.')
        assert get_project(page, key)['files'] == files
        page.locator('#projectChatClose').click()
        page.locator('.cm-ai-accept').click()
        pw.expect(page.locator('.cm-ai-review')).to_have_count(0)
        pw.expect(page.locator('#savedState')).to_have_text('All changes saved')
        updated = get_project(page, key)['files']
        assert updated['main.tex'] == files['main.tex'] and updated['chapter.tex'] == 'A clearer second sentence.'
        page.get_by_role('button', name='Undo', exact=True).click()
        pw.expect(page.locator('#source .cm-line')).to_have_text(['A second sentence.'])
        page.get_by_role('button', name='Redo', exact=True).click()
        pw.expect(page.locator('#source .cm-line')).to_have_text(['A clearer second sentence.'])
        assert not errors and not dialogs
        browser.close()


def test_accepting_one_inline_change_reanchors_later_changes_and_keeps_each_undo(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    proposals = [{'file': 'main.tex', 'original': 'First sentence.', 'replacement': 'A considerably longer first sentence.', 'reason': 'Style'},
                 {'file': 'main.tex', 'original': 'Second sentence.', 'replacement': 'A clearer second sentence.', 'reason': 'Style'}]
    monkeypatch.setattr(writing_chat, 'chat', model_reply(proposals))
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 1050})
        key, _ = make_project(page, base)
        original = '🪶 Header.\nFirst sentence.\nSecond sentence.\nSuffix unchanged.'
        page.locator('#source .cm-content').fill(original)
        ask(page)
        pw.expect(page.locator('.cm-ai-review')).to_have_count(2)
        page.locator('.cm-ai-accept').first.click()
        pw.expect(page.locator('.cm-ai-review')).to_have_count(1)
        pw.expect(page.locator('.cm-ai-original')).to_have_text('Second sentence.')
        pw.expect(page.locator('.cm-ai-accept')).to_be_enabled()
        page.locator('.cm-ai-accept').click()
        pw.expect(page.locator('.cm-ai-review')).to_have_count(0)
        pw.expect(page.locator('#savedState')).to_have_text('All changes saved')
        expected = original.replace('First sentence.', proposals[0]['replacement']).replace('Second sentence.', proposals[1]['replacement'])
        assert get_project(page, key)['files']['main.tex'] == expected
        page.get_by_role('button', name='Undo', exact=True).click()
        pw.expect(page.locator('#source .cm-line')).to_have_text(original.replace('First sentence.', proposals[0]['replacement']).split('\n'))
        page.keyboard.press('ControlOrMeta+z')
        pw.expect(page.locator('#source .cm-line')).to_have_text(original.split('\n'))
        browser.close()


def test_inline_live_preview_is_not_saved_and_reject_stops_generation(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    release = threading.Event()
    def reply(prepared, emit):
        text = prepared['project']['files']['main.tex']
        start = text.index('A first sentence.')
        emit({'type': 'answer_delta', 'text': 'Revising.'})
        emit({'type': 'edit_preview', 'file': 'main.tex', 'original': 'A first sentence.', 'replacement': 'A clearer', 'from': start, 'to': start + len('A first sentence.')})
        emit({'type': 'edit_delta', 'file': 'main.tex', 'text': ' first sentence.', 'from': start, 'to': start + len('A first sentence.')})
        assert release.wait(15)
        return {'answer': 'Done.', 'edits': [], 'files': prepared['files']}
    monkeypatch.setattr(writing_chat, 'chat', reply)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 1050})
        try:
            key, files = make_project(page, base)
            ask(page)
            pw.expect(page.locator('.cm-ai-proposed')).to_have_text('A clearer first sentence.')
            pw.expect(page.locator('.cm-ai-accept')).to_be_disabled()
            assert get_project(page, key)['files'] == files
            assert page.locator('.project-chat-live').count() == 0
            page.locator('.cm-ai-reject').click()
            pw.expect(page.locator('.cm-ai-review')).to_have_count(0)
            pw.expect(page.locator('.project-chat-message.assistant')).to_contain_text('Request stopped')
            release.set()
            assert get_project(page, key)['files'] == files
        finally:
            release.set()
            browser.close()


def test_typing_invalidates_inline_suggestions_without_overwriting_user_text(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    monkeypatch.setattr(writing_chat, 'chat', model_reply(edits()[:1]))
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 1050})
        key, files = make_project(page, base)
        ask(page)
        pw.expect(page.locator('.cm-ai-accept')).to_be_enabled()
        page.locator('#source .cm-line').first.click()
        page.keyboard.press('Home')
        page.keyboard.insert_text('User edit ')
        pw.expect(page.locator('.cm-ai-review')).to_have_count(0)
        pw.expect(page.locator('#savedState')).to_have_text('All changes saved')
        assert get_project(page, key)['files']['main.tex'] == 'User edit ' + files['main.tex']
        browser.close()


def test_inline_accept_rechecks_remote_revision_and_cannot_replace_a_newer_draft(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    monkeypatch.setattr(writing_chat, 'chat', model_reply(edits()[:1]))
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 1050})
        key, files = make_project(page, base)
        ask(page)
        pw.expect(page.locator('.cm-ai-accept')).to_be_enabled()
        current = get_project(page, key)
        remote = {**files, 'main.tex': 'Newer remote content.'}
        assert page.request.post(base + '/api/writing', data={**current, 'action': 'save_project', 'files': remote}).ok
        page.locator('.cm-ai-accept').click()
        pw.expect(page.locator('.cm-ai-review')).to_have_count(0)
        pw.expect(page.locator('#projectChatStatus')).to_contain_text('manuscript changed')
        assert get_project(page, key)['files'] == remote
        browser.close()


def test_chat_resize_corners_and_keyboard_can_grow_and_shrink_without_losing_input(model_list):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1100})
        make_project(page, base)
        page.locator('#projectChat').click()
        panel = page.locator('#projectChatPanel')
        page.locator('#projectChatInput').fill('Keep this unsent question.')
        before = panel.bounding_box()
        grip = page.locator('#projectChatResizeLeft').bounding_box()
        x, y = grip['x'] + grip['width'] / 2, grip['y'] + grip['height'] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x - 110, y + 85, steps=8)
        page.mouse.up()
        grown = panel.bounding_box()
        assert grown['width'] > before['width'] + 90 and grown['height'] > before['height'] + 65
        page.locator('#projectChatResize').press('ArrowLeft')
        page.locator('#projectChatResize').press('ArrowUp')
        shrunk = panel.bounding_box()
        assert shrunk['width'] < grown['width'] - 15 and shrunk['height'] < grown['height'] - 15
        pw.expect(page.locator('#projectChatInput')).to_have_value('Keep this unsent question.')
        pw.expect(page.locator('#projectChatSend')).to_be_enabled()
        if capture := os.environ.get('QUILL_INLINE_CAPTURE'):
            panel.screenshot(path=capture + '-resized-chat.png')
        browser.close()


def test_review_survives_switching_files_and_next_navigates_without_applying(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    monkeypatch.setattr(writing_chat, 'chat', model_reply(edits()))
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 1050})
        key, files = make_project(page, base)
        ask(page)
        pw.expect(page.locator('.cm-ai-accept')).to_be_enabled()
        page.locator('[data-file="chapter.tex"]').click()
        pw.expect(page.locator('.cm-ai-proposed')).to_have_text('A clearer second sentence.')
        page.wait_for_timeout(80)
        pw.expect(page.locator('.cm-ai-accept')).to_be_enabled()
        page.locator('.cm-ai-next').click()
        pw.expect(page.locator('.cm-ai-proposed')).to_have_text('A clearer first sentence.')
        assert get_project(page, key)['files'] == files
        if capture := os.environ.get('QUILL_INLINE_CAPTURE'):
            page.locator('#source').screenshot(path=capture + '-editor.png')
        browser.close()


def test_multiline_suggestion_is_visible_in_editor_and_accept_preserves_surrounding_text(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    selected = '\n'.join(f'Paragraph line {number}.' for number in range(90))
    replacement = 'A concise revised paragraph.\nA second revised line.'
    monkeypatch.setattr(writing_chat, 'chat', model_reply([{
        'file': 'main.tex', 'original': selected, 'replacement': replacement, 'reason': 'Requested rewrite'}]))
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 1050})
        key, _ = make_project(page, base)
        original = 'Prefix unchanged.\n' + selected + '\nSuffix unchanged.'
        page.locator('#source .cm-content').fill(original)
        ask(page)
        pw.expect(page.locator('.cm-ai-accept')).to_be_in_viewport()
        assert get_project(page, key)['files']['main.tex'] == original
        page.locator('.cm-ai-accept').click()
        pw.expect(page.locator('.cm-ai-review')).to_have_count(0)
        pw.expect(page.locator('#savedState')).to_have_text('All changes saved')
        assert get_project(page, key)['files']['main.tex'] == 'Prefix unchanged.\n' + replacement + '\nSuffix unchanged.'
        page.get_by_role('button', name='Undo', exact=True).click()
        pw.expect(page.locator('#savedState')).to_have_text('All changes saved')
        assert get_project(page, key)['files']['main.tex'] == original
        browser.close()
