import pytest

from originrag import writing_language as language
from test_writing import workspace_server as workspace_server
from test_writing_editor_integration import create_manuscript, get_project


def test_language_button_selected_prose_suggestions_jump_and_no_rewrite(workspace_server, monkeypatch):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '1')
    calls = []
    def check(*args):
        calls.append(args)
        return {'suggestions': [{'original': 'recieve', 'replacement': 'receive', 'reason': '<img src=x onerror=alert(1)>', 'line': 1}], 'filtered': 0, 'model': 'test'}
    monkeypatch.setattr(language, 'check_language', check)
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1365, 'height': 950})
        key = create_manuscript(page, base)
        page.locator('#source .cm-content').click()
        page.keyboard.press('ControlOrMeta+a')
        page.keyboard.insert_text('We recieve samples.')
        page.keyboard.press('ControlOrMeta+a')
        page.get_by_role('button', name='Language check', exact=True).click()
        assert calls == []
        page.get_by_role('button', name='Check language', exact=True).click()
        page.get_by_role('button', name='Line 1', exact=True).wait_for()
        assert calls[0][0] == 'We recieve samples.'
        assert 'recieve → receive' in page.locator('#languageDialog').inner_text()
        assert page.locator('#languageDialog img').count() == 0
        assert get_project(page, key)['files']['main.tex'] == 'We recieve samples.'
        page.screenshot(path=str(_ / 'originwrite-language-check.png'))
        page.get_by_role('button', name='Line 1', exact=True).click()
        page.locator('#languageDialog').wait_for(state='detached')
        assert 'recieve' in page.locator('#source .cm-content').inner_text()
        browser.close()


def test_language_check_disabled_and_empty_selection_never_calls_model(workspace_server, monkeypatch):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    monkeypatch.delenv('ORIGINWRITE_LANGUAGE_CHECK', raising=False)
    def forbidden(*args):
        raise AssertionError('No model invocation expected')
    monkeypatch.setattr(language, 'check_language', forbidden)
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        create_manuscript(page, base)
        assert page.locator('#languageCheck').is_hidden()
        monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '1')
        page.reload()
        page.locator('#source .cm-content').wait_for()
        page.get_by_role('button', name='Language check', exact=True).click()
        assert page.get_by_role('button', name='Check language', exact=True).is_disabled()
        assert 'Select up to' in page.locator('#languageDialog').inner_text()
        browser.close()
