import json
import os

import pytest

from originrag import writing_chat
from test_writing import workspace_server as workspace_server
from test_writing_chat_browser import model_list as model_list, make_project, get_project, edits
import test_web_chat as http


def fake_chat(prepared, emit):
    reply = {'answer': 'The project contains main.tex and chapter.tex.', 'edits': edits() if prepared['mode'] == 'edit' else []}
    return {**writing_chat.parse_reply(json.dumps(reply), prepared), 'model': 'test:qwen'}


def open_popup(page):
    with page.expect_popup() as opened:
        page.locator('#projectChatWindow').click()
    child = opened.value
    child.locator('#projectChatInput').wait_for()
    return child


def test_project_chat_popout_docking_and_native_close_preserve_one_conversation(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, manager = model_list
    calls = []
    def reply(prepared, emit):
        calls.append(prepared)
        return fake_chat(prepared, emit)
    monkeypatch.setattr(writing_chat, 'chat', reply)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        key, files = make_project(page, base)
        page.locator('#projectChat').click()
        pw.expect(page.locator('#projectChatName')).to_have_text('Integrated manuscript')
        before = page.locator('#projectChatPanel').bounding_box()['x']
        page.locator('#projectChatHandle').press('ArrowLeft')
        assert page.locator('#projectChatPanel').bounding_box()['x'] < before
        child = open_popup(page)
        child.on('pageerror', lambda error: errors.append(str(error)))
        pw.expect(page.locator('#projectChatPanel')).to_have_count(0)
        pw.expect(child.locator('#projectChatName')).to_contain_text('Integrated manuscript')
        child.locator('#projectChatInput').fill('Explain this project.')
        child.locator('#projectChatInput').press('Enter')
        pw.expect(child.locator('.project-chat-message.assistant')).to_contain_text('contains main.tex')
        assert not child.locator('#projectChatPanel').evaluate('el => el.scrollWidth > el.clientWidth')
        if capture := os.environ.get('QUILL_ASSISTANT_CAPTURE'):
            child.locator('#projectChatPanel').screenshot(path=capture + '-window.png')
        child.locator('#projectChatWindow').click()
        pw.expect(page.locator('.project-chat-message')).to_have_count(2)
        pw.expect(page.locator('#projectChatPanel')).to_be_visible()
        if capture:
            page.locator('#projectChatPanel').screenshot(path=capture + '-docked.png')
        child = open_popup(page)
        pw.expect(child.locator('.project-chat-message')).to_have_count(2)
        child.close()
        pw.expect(page.locator('#projectChatPanel')).to_be_hidden()
        pw.expect(page.locator('#projectChat')).to_have_attribute('aria-expanded', 'false')
        page.locator('#projectChat').click()
        pw.expect(page.locator('.project-chat-message')).to_have_count(2)
        assert len(calls) == 1 and manager.selected() is None
        assert get_project(page, key)['files'] == files and not errors
        browser.close()


def test_popout_edits_use_the_connected_editor_confirmation_and_undo(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    monkeypatch.setattr(writing_chat, 'chat', fake_chat)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        key, files = make_project(page, base)
        page.locator('#projectChat').click()
        child = open_popup(page)
        child.locator('#projectChatInput').fill('Rewrite both sentences.')
        child.locator('#projectChatSend').click()
        pw.expect(page.locator('.cm-ai-accept')).to_be_enabled()
        assert child.locator('.cm-ai-review, .project-chat-apply').count() == 0
        assert get_project(page, key)['files'] == files
        page.locator('.cm-ai-accept').click()
        pw.expect(page.locator('.cm-ai-proposed')).to_have_text('A clearer second sentence.')
        pw.expect(page.locator('.cm-ai-accept')).to_be_enabled()
        page.locator('.cm-ai-accept').click()
        pw.expect(page.locator('.cm-ai-review')).to_have_count(0)
        pw.expect(page.locator('#savedState')).to_have_text('All changes saved')
        assert get_project(page, key)['files']['chapter.tex'] == 'A clearer second sentence.'
        child.locator('#projectChatClose').click()
        page.locator('[data-file="main.tex"]').click()
        code = page.locator('#source .cm-content')
        code.click()
        page.keyboard.press('ControlOrMeta+z')
        pw.expect(code.locator('.cm-line')).to_have_text(files['main.tex'].split('\n'))
        browser.close()


def test_automatic_chat_uses_project_context_without_corpus_text_or_manual_options(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, root, _ = model_list
    papers = [{'paper_id': 'P1', 'title': 'Stellar spectroscopy', 'author': 'Rivera', 'year': '2026', 'local_pdf': True},
              {'paper_id': 'P2', 'title': 'Unselected battery study', 'author': 'Doe', 'year': '2025'}]
    monkeypatch.setattr(http.web_chat, 'library_catalog', lambda: papers)
    chunks = root / 'data/chunks'
    chunks.mkdir(parents=True)
    (chunks / 'P1.jsonl').write_text(json.dumps({'paper_id': 'P1', 'chunk_id': 'C1', 'section': 'Methods', 'page_start': 2, 'text': 'The telescope uses a calibrated spectrometer.'}) + '\n', encoding='utf-8')
    calls = []
    def reply(prepared, emit):
        calls.append(prepared)
        return {**writing_chat.parse_reply(json.dumps({'answer': 'main.tex contains A first sentence.', 'edits': []}), prepared), 'model': 'test:qwen'}
    monkeypatch.setattr(writing_chat, 'chat', reply)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        key, files = make_project(page, base)
        page.locator('#projectChat').click()
        assert page.locator('#projectChatOptions, #projectChatLibrary, #projectChatModel').count() == 0
        page.locator('#projectChatInput').fill('What is in main.tex?')
        page.locator('#projectChatSend').click()
        pw.expect(page.locator('.project-chat-message.assistant')).to_contain_text('A first sentence.')
        assert page.locator('.project-chat-apply').count() == 0
        assert len(calls) == 1 and calls[0]['sources'] == []
        assert 'A first sentence.' in calls[0]['prompt']
        assert 'calibrated spectrometer' not in calls[0]['prompt']
        assert 'Unselected battery' not in calls[0]['prompt']
        assert get_project(page, key)['files'] == files
        browser.close()


def test_drag_entire_chat_header_and_keep_panel_on_screen_after_resize(model_list):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        make_project(page, base)
        page.locator('#projectChat').click()
        panel = page.locator('#projectChatPanel')
        before = panel.bounding_box()
        handle = page.locator('#projectChatName').bounding_box()
        x, y = handle['x'] + 30, handle['y'] + handle['height'] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x - 170, y + 90, steps=8)
        page.mouse.up()
        after = panel.bounding_box()
        assert after['x'] < before['x'] - 150 and after['y'] > before['y'] + 70
        page.set_viewport_size({'width': 390, 'height': 640})
        page.wait_for_timeout(80)
        bounds = panel.bounding_box()
        assert 0 <= bounds['x'] and bounds['x'] + bounds['width'] <= 391
        assert 0 <= bounds['y'] and bounds['y'] + bounds['height'] <= 641
        page.locator('#projectChatInput').fill('Still usable after moving.')
        pw.expect(page.locator('#projectChatSend')).to_be_enabled()
        page.locator('#projectChatClose').click()
        pw.expect(panel).to_be_hidden()
        browser.close()


def test_blocked_popups_keep_the_docked_chat_usable(model_list):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        make_project(page, base)
        page.locator('#projectChat').click()
        page.evaluate('window.open = () => null')
        page.locator('#projectChatWindow').click()
        pw.expect(page.locator('#projectChatStatus')).to_contain_text('blocked')
        page.locator('#projectChatInput').fill('Still available?')
        pw.expect(page.locator('#projectChatSend')).to_be_enabled()
        browser.close()


def test_leaving_editor_clears_popout_project_context_and_links_back_to_original(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    monkeypatch.setattr(writing_chat, 'chat', fake_chat)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        key, _ = make_project(page, base)
        page.locator('#projectChat').click()
        child = open_popup(page)
        child.locator('#projectChatInput').fill('PRIVATE SESSION TEXT')
        child.locator('#projectChatSend').click()
        pw.expect(child.locator('.project-chat-message.assistant')).to_contain_text('contains main.tex')
        page.goto(base + '/library')
        pw.expect(child.locator('.project-chat-disconnected')).to_contain_text('cleared')
        assert 'PRIVATE SESSION TEXT' not in child.locator('body').inner_text()
        pw.expect(child.locator('#projectChatPanel')).to_have_count(0)
        pw.expect(child.locator('.project-chat-disconnected a')).to_have_attribute('href', base + '/write?id=' + key)
        assert child.title() == 'Quill assistant'
        browser.close()
