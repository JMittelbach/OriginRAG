import threading

import pytest

from originrag import writing_chat, model_runtime
from test_writing import workspace_server as workspace_server
from test_writing_editor_integration import create_manuscript, get_project


@pytest.fixture
def model_list(workspace_server, monkeypatch):
    base, root = workspace_server
    monkeypatch.setenv('ORIGINRAG_OLLAMA_ANSWER_MODEL', 'test:qwen')
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '1')
    manager = model_runtime.manager_for(root)
    rows = [{'provider': 'ollama', 'model': name, 'name': name, 'installed': True,
             'loaded': False, 'bytes': 100, 'downloadable': False}
            for name in ['test:qwen', 'qwen3-coder:30b']]
    monkeypatch.setattr(manager, 'inventory', lambda **kwargs: (rows, None))
    return base, root, manager


def make_project(page, base):
    key = create_manuscript(page, base)
    value = get_project(page, key)
    files = {'main.tex': '\\documentclass{article}\n\\begin{document}\nA first sentence.\n\\input{chapter}\n\\end{document}',
             'chapter.tex': 'A second sentence.', 'references.bib': ''}
    response = page.request.post(base + '/api/writing', data={**value, 'action': 'save_project', 'files': files})
    assert response.ok
    page.reload()
    page.locator('#source .cm-content').wait_for()
    return key, files


def edits():
    return [
        {'file': 'main.tex', 'original': 'A first sentence.', 'replacement': 'A clearer first sentence.', 'reason': 'Requested rewrite', 'review_notes': []},
        {'file': 'chapter.tex', 'original': 'A second sentence.', 'replacement': 'A clearer second sentence.', 'reason': 'Requested rewrite', 'review_notes': []},
    ]


def test_project_chat_reads_project_previews_multiple_files_confirms_and_preserves_undo(model_list, monkeypatch):
    playwright = pytest.importorskip('playwright.sync_api')
    base, root, manager = model_list
    release = threading.Event()
    calls = []
    def chat(prepared, emit):
        calls.append((prepared, model_runtime.CURRENT_RUNTIME.get()['model']))
        emit({'type': 'answer_delta', 'text': 'Here are two suggested changes.', 'model': 'test:qwen'})
        assert release.wait(15)
        return {'answer': 'Here are two suggested changes.', 'edits': edits(), 'filtered': 0,
                'files': prepared['files'], 'model': 'test:qwen'}
    monkeypatch.setattr(writing_chat, 'chat', chat)
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 1050})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            key, files = make_project(page, base)
            page.locator('#projectChat').click()
            playwright.expect(page.locator('#projectChatPanel')).to_be_visible()
            playwright.expect(page.locator('#projectChatName')).to_have_text('Integrated manuscript')
            assert page.locator('#projectChatPanel select, #projectChatOptions, #projectChatFiles').count() == 0
            page.locator('#projectChatInput').fill('Rewrite the first and second sentence to be clearer.')
            page.locator('#projectChatSend').click()
            playwright.expect(page.locator('.project-chat-message.assistant')).to_contain_text('Here are two suggested changes.')
            assert page.locator('.project-chat-apply').count() == 0
            assert get_project(page, key)['files'] == files
            assert 'A second sentence.' in calls[0][0]['prompt']
            assert calls[0][1] == 'test:qwen' and calls[0][0]['automatic_context'] is True
            assert manager.selected() is None
            release.set()
            accept = page.locator('.cm-ai-accept')
            playwright.expect(accept).to_be_enabled()
            playwright.expect(page.locator('.cm-ai-proposed')).to_have_text('A clearer first sentence.')
            page.screenshot(path=str(root / 'quill-project-chat.png'))
            confirmations = []
            def confirm(dialog):
                confirmations.append(dialog.message)
                dialog.dismiss()
            page.on('dialog', confirm)
            accept.click()
            playwright.expect(page.locator('.cm-ai-proposed')).to_have_text('A clearer second sentence.')
            playwright.expect(accept).to_be_enabled()
            accept.click()
            playwright.expect(page.locator('.cm-ai-review')).to_have_count(0)
            playwright.expect(page.locator('#savedState')).to_have_text('All changes saved')
            assert confirmations == []
            updated = get_project(page, key)['files']
            assert updated['main.tex'] == files['main.tex'].replace('A first sentence.', 'A clearer first sentence.')
            assert updated['chapter.tex'] == 'A clearer second sentence.'
            assert updated['references.bib'] == ''
            page.locator('#projectChatClose').click()
            page.locator('[data-file="main.tex"]').click()
            code = page.locator('#source .cm-content')
            code.click()
            page.keyboard.press('ControlOrMeta+z')
            playwright.expect(code.locator('.cm-line')).to_have_text(files['main.tex'].split('\n'))
            page.locator('[data-file="chapter.tex"]').click()
            code.click()
            page.keyboard.press('ControlOrMeta+z')
            playwright.expect(code).to_have_text(files['chapter.tex'])
            assert not errors
        finally:
            release.set()
            browser.close()


def test_project_chat_rejects_proposal_when_remote_manuscript_changes(model_list, monkeypatch):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    def chat(prepared, emit):
        return {'answer': 'Review before applying.', 'edits': edits(), 'filtered': 0,
                'files': prepared['files'], 'model': 'test:qwen'}
    monkeypatch.setattr(writing_chat, 'chat', chat)
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        key, files = make_project(page, base)
        page.locator('#projectChat').click()
        page.locator('#projectChatInput').fill('Rewrite these two sentences.')
        page.locator('#projectChatSend').click()
        playwright.expect(page.locator('.cm-ai-accept')).to_be_enabled()
        current = get_project(page, key)
        remote_files = {**files, 'chapter.tex': 'An external update.'}
        assert page.request.post(base + '/api/writing', data={**current, 'action': 'save_project', 'files': remote_files}).ok
        page.locator('.cm-ai-accept').click()
        playwright.expect(page.locator('#projectChatStatus')).to_contain_text('manuscript changed')
        assert get_project(page, key)['files'] == remote_files
        browser.close()


def test_quill_models_visible_on_dashboard_and_settings_work_if_models_module_fails(model_list):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        page.goto(base + '/write')
        playwright.expect(page.locator('#quillHomeModels')).to_be_visible()
        page.route('**/assets/workspace/models.mjs', lambda route: route.fulfill(status=503, content_type='text/javascript', body=''))
        page.locator('#globalSettings').click()
        playwright.expect(page.locator('#globalSettingsDialog')).to_be_visible()
        page.locator('#globalModels').click()
        playwright.expect(page.locator('#modelError')).to_contain_text('could not be loaded')
        page.locator('#modelClose').click()
        page.locator('#globalSettings').click()
        playwright.expect(page.locator('#globalSettingsDialog')).to_be_visible()
        page.keyboard.press('Escape')
        page.locator('#quillHomeModels').click()
        playwright.expect(page.locator('#modelError')).to_contain_text('could not be loaded')
        browser.close()


def test_missing_local_model_explains_download_and_does_not_send(model_list, monkeypatch):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _, manager = model_list
    monkeypatch.setattr(manager, 'inventory', lambda **kwargs: ([], None))
    def forbidden(*args):
        raise AssertionError('An absent model must not be invoked')
    monkeypatch.setattr(writing_chat, 'chat', forbidden)
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        make_project(page, base)
        page.locator('#projectChat').click()
        page.locator('#projectChatInput').fill('Explain the LaTeX.')
        playwright.expect(page.locator('#projectChatSend')).to_be_enabled()
        page.locator('#projectChatSend').click()
        playwright.expect(page.locator('.project-chat-message.assistant')).to_contain_text('Choose an installed model')
        page.locator('#projectChatPanel').get_by_role('button', name='Models', exact=True).click()
        playwright.expect(page.locator('#modelDialog')).to_be_visible()
        browser.close()


def test_project_chat_stop_discards_incomplete_edits(model_list, monkeypatch):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    release = threading.Event()
    def chat(prepared, emit):
        emit({'type': 'answer_delta', 'text': 'A provisional reply.', 'model': 'test:qwen'})
        assert release.wait(15)
        return {'answer': 'Reply', 'edits': edits(), 'filtered': 0, 'files': prepared['files'], 'model': 'test:qwen'}
    monkeypatch.setattr(writing_chat, 'chat', chat)
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        try:
            key, files = make_project(page, base)
            page.locator('#projectChat').click()
            page.locator('#projectChatInput').fill('Rewrite the paragraphs.')
            page.locator('#projectChatSend').click()
            playwright.expect(page.locator('.project-chat-message.assistant')).to_contain_text('A provisional reply.')
            page.locator('#projectChatStop').click()
            playwright.expect(page.locator('.project-chat-message.assistant')).to_contain_text('Request stopped')
            release.set()
            assert page.locator('.project-chat-apply').count() == 0
            assert get_project(page, key)['files'] == files
        finally:
            release.set()
            browser.close()


@pytest.mark.parametrize('width', [390, 1440])
def test_automatic_chat_is_minimal_and_settings_remain_usable(model_list, width):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _, _ = model_list
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        make_project(page, base)
        page.locator('#projectChat').click()
        assert page.locator('#projectChatPanel select, #projectChatOptions, #projectChatAll, #projectChatNone').count() == 0
        assert len(page.locator('#projectChatPanel').inner_text().split()) <= 12
        playwright.expect(page.locator('#projectChatList')).to_be_hidden()
        page.locator('#projectChatInput').fill('Explain this project.')
        playwright.expect(page.locator('#projectChatSend')).to_be_enabled()
        page.locator('#globalSettings').click()
        playwright.expect(page.locator('#globalSettingsDialog')).to_be_visible()
        page.keyboard.press('Escape')
        playwright.expect(page.locator('#projectChatPanel')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        browser.close()


def test_chat_works_immediately_in_popup_without_model_or_context_get_requests(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _, manager = model_list
    monkeypatch.setenv('ORIGINRAG_OLLAMA_ANSWER_MODEL', 'missing:qwen')
    monkeypatch.setattr(manager, 'inventory', lambda **kwargs: ([{
        'provider': 'ollama', 'model': 'test:qwen', 'name': 'test:qwen', 'installed': True,
        'loaded': False, 'bytes': 100, 'downloadable': False}], None))
    calls = []
    def reply(prepared, emit):
        calls.append((prepared, model_runtime.CURRENT_RUNTIME.get()['model']))
        return {'answer': 'The main preamble declares article.', 'edits': [], 'filtered': 0,
                'files': prepared['files'], 'model': 'test:qwen'}
    monkeypatch.setattr(writing_chat, 'chat', reply)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        key, files = make_project(page, base)
        page.route('**/api/models', lambda route: route.abort())
        page.route('**/api/writing?chat_context=*', lambda route: route.abort())
        page.locator('#projectChat').click()
        with page.expect_popup() as popup:
            page.locator('#projectChatWindow').click()
        child = popup.value
        pw.expect(child.locator('#projectChatName')).to_have_text('Integrated manuscript')
        child.locator('#projectChatInput').fill('What is in the preamble of main.tex?')
        pw.expect(child.locator('#projectChatSend')).to_be_enabled()
        child.locator('#projectChatSend').click()
        pw.expect(child.locator('.project-chat-message.assistant')).to_contain_text('declares article')
        assert calls[0][1] == 'test:qwen' and 'documentclass' in calls[0][0]['prompt']
        assert manager.selected() is None
        assert get_project(page, key)['files'] == files
        browser.close()
