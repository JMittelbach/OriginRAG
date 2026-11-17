import pytest

from test_folio_http import folio_server as folio_server
from test_folio_browser import folio_browser as folio_browser, create_document
from test_web_onboarding_browser import browser_page as browser_page
from test_writing import workspace_server as workspace_server
from test_writing_chat_browser import model_list as model_list, make_project, get_project


@pytest.mark.parametrize('failed', [False, True])
def test_chat_submission_does_not_steal_library_search_focus(browser_page, failed):
    from playwright.sync_api import expect
    page, _, _, _ = browser_page
    page.locator('#guideSkip').click()
    pending = []
    page.route('**/api/chat', lambda route: pending.append(route))
    page.locator('#message').fill('Explain the example paper.')
    page.locator('#send').click()
    page.locator('#libraryButton').click()
    search = page.locator('#librarySearch')
    search.fill('A different paper')
    assert pending
    if failed:
        pending[0].fulfill(status=503, json={'error': 'Please retry later.'})
    else:
        pending[0].fulfill(status=202, json={'job_id': 'example', 'state': 'queued', 'session_dir': ''})
    expect(page.locator('#send')).to_be_enabled()
    expect(search).to_be_focused()
    expect(search).to_have_value('A different paper')


def test_quill_rename_composition_escape_and_focus(model_list):
    from playwright.sync_api import sync_playwright, expect
    base, _, _ = model_list
    with sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        key, files = make_project(page, base)
        page.locator('#projectChat').click()
        page.locator('#projectChatInput').dispatch_event('keydown', {'key': 'Escape', 'isComposing': True})
        expect(page.locator('#projectChatPanel')).to_be_visible()
        page.locator('#projectChatListToggle').click()
        page.get_by_role('button', name='Rename chat: Chat 1', exact=True).click()
        name = page.get_by_role('textbox', name='Chat name', exact=True)
        name.fill('A new topic')
        for keypress in ('Enter', 'Escape'):
            name.dispatch_event('keydown', {'key': keypress, 'isComposing': True})
            expect(name).to_be_visible()
        name.press('Enter')
        expect(page.locator('#projectChatListToggle')).to_contain_text('A new topic')
        page.keyboard.press('Escape')
        expect(page.locator('#projectChatList')).to_be_hidden()
        expect(page.locator('#projectChatPanel')).to_be_visible()
        page.keyboard.press('Escape')
        expect(page.locator('#projectChatPanel')).to_be_hidden()
        expect(page.locator('#projectChat')).to_be_focused()
        assert get_project(page, key)['files'] == files
        browser.close()


def test_folio_search_keyboard_navigation_keeps_query_focus(folio_browser):
    page, expect = folio_browser.page, folio_browser.expect
    editor = create_document(folio_browser)
    original = 'Alpha first. Alpha second. Alpha third.'
    editor.fill(original)
    page.get_by_role('button', name='Find and replace', exact=True).click()
    query = page.get_by_role('searchbox', name='Find text', exact=True)
    query.fill('Alpha')
    query.dispatch_event('keydown', {'key': 'Enter', 'isComposing': True})
    expect(query).to_be_focused()
    query.press('Enter')
    expect(query).to_be_focused()
    expect(page.locator('#folioSidebar')).to_contain_text('1 of 3 matches')
    query.press('Enter')
    expect(page.locator('#folioSidebar')).to_contain_text('2 of 3 matches')
    query.press('Shift+Enter')
    expect(page.locator('#folioSidebar')).to_contain_text('1 of 3 matches')
    query.press('Shift+Enter')
    expect(page.locator('#folioSidebar')).to_contain_text('3 of 3 matches')
    query.fill('')
    expect(page.locator('#folioSidebar')).to_contain_text('Enter text to find.')
    query.press('Escape')
    expect(page.locator('#folioSidebar')).to_be_hidden()
    expect(editor).to_be_focused()
    expect(editor).to_have_text(original)
