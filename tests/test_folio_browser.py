"""Real-browser document editing and recovery, backed by the actual Folio API."""

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
import zipfile

import pytest

from test_folio_http import folio_server as folio_server, request_json
from test_ecosystem_design_browser import NAVIGATION_CONTRAST


@pytest.fixture
def folio_browser(folio_server):
    playwright = pytest.importorskip('playwright.sync_api')
    port, users, tmp, _ = folio_server
    base = f'http://127.0.0.1:{port}'
    with playwright.sync_playwright() as engine:
        browser_name = os.environ.get('ORIGINRAG_TEST_BROWSER', 'chromium')
        if browser_name not in {'chromium', 'firefox'}:
            raise ValueError('Choose chromium or firefox for ORIGINRAG_TEST_BROWSER.')
        browser = getattr(engine, browser_name).launch()
        context = browser.new_context(viewport={'width': 1500, 'height': 1050})
        headers = users.get('alice', {})
        if headers:
            context.add_cookies([
                {'name': item.split('=', 1)[0], 'value': item.split('=', 1)[1], 'url': base}
                for item in headers['Cookie'].split('; ')
            ])
        page = context.new_page()
        errors, external = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda req: external.append(req.url)
                if req.url.startswith(('http:', 'https:')) and not req.url.startswith(base) else None)
        try:
            yield SimpleNamespace(page=page, expect=playwright.expect, port=port, base=base,
                                  headers=headers, tmp=tmp)
        finally:
            context.close()
            browser.close()
        assert not errors, errors
        assert not external, external


def create_document(browser, title='A useful draft', *, capture_dashboard=False):
    page = browser.page
    page.goto(browser.base + '/folio')
    if capture_dashboard:
        browser.expect(page.get_by_role('button', name='New document', exact=True)).to_be_visible()
        page.screenshot(path=str(browser.tmp / 'folio-dashboard.png'), full_page=True)
    page.get_by_role('button', name='New document', exact=True).click()
    editor = page.get_by_role('textbox', name='Document content', exact=True)
    browser.expect(editor).to_be_visible()
    page.get_by_role('textbox', name='Document title', exact=True).fill(title)
    return editor


def saved_document(browser):
    key = parse_qs(urlparse(browser.page.url).query)['id'][0]
    return request_json(browser.port, path='/api/folio?id=' + key, headers=browser.headers)


@pytest.mark.parametrize('folio_server', [False, True], indirect=True)
def test_create_format_autosave_reload_and_download_docx(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser, 'A formatted research note', capture_dashboard=True)
    editor.fill('A useful finding survives autosave.')
    editor.press('ControlOrMeta+a')
    page.get_by_role('button', name='Bold', exact=True).click()
    expect(editor.locator('strong')).to_have_text('A useful finding survives autosave.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    expect(page.get_by_role('textbox', name='Document title', exact=True)).to_have_value(
        'A formatted research note')
    expect(editor.locator('strong')).to_have_text('A useful finding survives autosave.')
    assert saved_document(browser)['title'] == 'A formatted research note'
    page.get_by_role('tab', name='File', exact=True).click()
    with page.expect_download() as downloaded:
        page.get_by_role('button', name='Download DOCX', exact=True).click()
    assert downloaded.value.suggested_filename.endswith('.docx')
    content = Path(downloaded.value.path()).read_bytes()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        document = archive.read('word/document.xml').decode()
        assert 'A useful finding survives autosave.' in document
        assert '<w:b' in document
    page.get_by_role('tab', name='Home', exact=True).click()
    expect(page.locator('#folioWordCount')).to_have_text('5 words')
    page.screenshot(path=str(browser.tmp / 'folio-desktop.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    expect(editor).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(browser.tmp / 'folio-mobile.png'), full_page=True)


def test_typing_while_save_response_is_delayed_is_persisted(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser)
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    held = []

    def delay_save(route):
        payload = route.request.post_data_json if route.request.method == 'POST' else {}
        if payload.get('action') != 'save' or held:
            route.continue_()
            return
        held.append((route, route.fetch()))

    page.route('**/api/folio', delay_save)
    editor.fill('The first saved version.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saving…')
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(20)
    assert held
    editor.fill('The first saved version. Continued typing while saving.')
    held[0][0].fulfill(response=held[0][1])
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    expect(editor).to_have_text('The first saved version. Continued typing while saving.')


def test_failed_save_preserves_draft_and_blocks_document_navigation(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser)
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    original_url = page.url

    def reject_save(route):
        payload = route.request.post_data_json if route.request.method == 'POST' else {}
        if payload.get('action') == 'save':
            route.fulfill(status=503, json={'error': 'Temporary save failure. Please retry.'})
        else:
            route.continue_()

    page.route('**/api/folio', reject_save)
    editor.fill('An unsaved draft must not disappear.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Save failed')
    page.get_by_role('button', name='Documents', exact=True).click()
    expect(editor).to_have_text('An unsaved draft must not disappear.')
    assert page.url == original_url
    page.unroute('**/api/folio', reject_save)
    page.get_by_role('button', name='Save document', exact=True).click()
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    expect(editor).to_have_text('An unsaved draft must not disappear.')


def test_revision_conflict_keeps_local_draft_and_can_save_a_separate_copy(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser, 'Two tabs editing')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    original_url = page.url
    original = saved_document(browser)
    remote_content = {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [
        {'type': 'text', 'text': 'The other tab saved this version.'}
    ]}]}
    remote = request_json(browser.port, 'POST', payload={**original, 'action': 'save',
                                                       'content': remote_content})
    with page.expect_response('**/api/folio') as conflict:
        editor.fill('Keep my conflicting local draft as a separate copy.')
    assert conflict.value.status == 409
    expect(editor).to_have_text('Keep my conflicting local draft as a separate copy.')
    page.get_by_role('tab', name='File', exact=True).click()
    page.get_by_role('button', name='Save a copy', exact=True).click()
    expect(page).not_to_have_url(original_url)
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    expect(editor).to_have_text('Keep my conflicting local draft as a separate copy.')
    assert request_json(browser.port, path='/api/folio?id=' + original['id']) == remote
    assert len(request_json(browser.port)['documents']) == 2


def test_pasted_html_drops_active_content_and_remote_images(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser)
    editor.click()
    page.evaluate('window.folioPasteExecuted = false')
    editor.evaluate('''element => {
      const transfer = new DataTransfer();
      transfer.setData('text/plain', 'Pasted safe words. Unsafe link.');
      transfer.setData('text/html', '<p>Pasted <strong>safe words.</strong> '
        + '<a href="javascript:window.folioPasteExecuted=true">Unsafe link.</a></p>'
        + '<script>window.folioPasteExecuted=true</script>'
        + '<img src="https://untrusted.example/tracker.png" onerror="window.folioPasteExecuted=true">'
        + '<iframe src="https://untrusted.example/frame"></iframe>');
      const event = new Event('paste', {bubbles:true, cancelable:true});
      Object.defineProperty(event, 'clipboardData', {value:transfer});
      element.dispatchEvent(event);
    }''')
    expect(editor).to_contain_text('Pasted safe words.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    assert editor.locator('script, iframe, [onerror], a[href^="javascript:"], img[src^="https:"]').count() == 0
    assert page.evaluate('window.folioPasteExecuted') is False
    page.reload()
    expect(editor).to_contain_text('Pasted safe words.')
    assert editor.locator('script, iframe, [onerror], a[href^="javascript:"], img[src^="https:"]').count() == 0


def test_json_and_docx_exports_can_be_imported_as_new_documents(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser, 'Round trip draft')
    editor.fill('A portable document with café and a useful observation.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    original = page.url
    page.get_by_role('tab', name='File', exact=True).click()
    exported = []
    for label, mime in (('Download JSON', 'application/json'),
                        ('Download DOCX', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')):
        with page.expect_download() as downloaded:
            page.get_by_role('button', name=label, exact=True).click()
        data = Path(downloaded.value.path()).read_bytes()
        exported.append({'name': downloaded.value.suggested_filename, 'mimeType': mime, 'buffer': data})
    assert 'café' in json.dumps(json.loads(exported[0]['buffer']), ensure_ascii=False)
    for item in exported:
        page.locator('#folioImport').set_input_files(item)
        expect(editor).to_contain_text('A portable document with café and a useful observation.')
        expect(page).not_to_have_url(original)
        expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
        original = page.url
    listed = request_json(browser.port, headers=browser.headers)
    assert len(listed['documents']) == 3


def test_invalid_import_leaves_current_saved_document_untouched(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser, 'Keep this draft')
    editor.fill('Existing work survives a malformed file.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    original_url = page.url
    with page.expect_response('**/api/folio') as imported:
        page.locator('#folioImport').set_input_files(
            {'name': 'broken.json', 'mimeType': 'application/json', 'buffer': b'{not valid json'})
    assert imported.value.status == 400
    expect(page.locator('#folioNotice')).not_to_be_empty()
    expect(editor).to_have_text('Existing work survives a malformed file.')
    assert page.url == original_url
    assert len(request_json(browser.port, headers=browser.headers)['documents']) == 1


def test_imported_checklist_and_level_six_heading_remain_editable_after_reload(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser)
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    original_url = page.url
    imported = {'title': 'Imported checklist', 'content': {'type': 'doc', 'content': [
        {'type': 'heading', 'attrs': {'level': 6}, 'content': [{'type': 'text', 'text': 'Detailed section'}]},
        {'type': 'taskList', 'content': [{'type': 'taskItem', 'attrs': {'checked': True}, 'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Verify the imported checklist.'}]}
        ]}]}
    ]}}
    page.locator('#folioImport').set_input_files(
        {'name': 'checklist.json', 'mimeType': 'application/json', 'buffer': json.dumps(imported).encode()})
    expect(page).not_to_have_url(original_url)
    expect(editor.locator('h6')).to_have_text('Detailed section')
    checkbox = editor.get_by_role('checkbox')
    expect(checkbox).to_be_checked()
    checkbox.uncheck()
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    expect(editor.locator('h6')).to_have_text('Detailed section')
    expect(checkbox).not_to_be_checked()
    expect(editor).to_contain_text('Verify the imported checklist.')


def test_comment_quotes_and_resolution_persist_after_reload(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser, 'Notes on a passage')
    editor.fill('An important passage to discuss.')
    editor.press('ControlOrMeta+a')
    page.get_by_role('button', name='Comments', exact=True).click()
    page.get_by_role('textbox', name='Comment text', exact=True).fill('Check this interpretation with the team.')
    page.locator('#folioSidebar').get_by_role('button', name='Add comment', exact=True).click()
    expect(page.locator('.folio-comment')).to_contain_text('Check this interpretation with the team.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    page.get_by_role('button', name='Comments', exact=True).click()
    expect(page.locator('.folio-comment blockquote')).to_have_text('An important passage to discuss.')
    expect(page.locator('.folio-comment p')).to_have_text('Check this interpretation with the team.')
    page.get_by_role('button', name='Resolve comment', exact=True).click()
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    page.get_by_role('button', name='Comments', exact=True).click()
    expect(page.locator('.folio-comment')).to_contain_text('Resolved')
    assert saved_document(browser)['comments'][0]['resolved'] is True


def test_insert_table_and_find_replace_survive_reload(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser, 'A short findings table')
    editor.fill('Alpha beta. Alpha result.')
    page.get_by_role('button', name='Find and replace', exact=True).click()
    page.get_by_role('searchbox', name='Find text', exact=True).fill('Alpha')
    page.get_by_role('textbox', name='Replace with', exact=True).fill('Omega')
    page.get_by_role('button', name='Find next', exact=True).click()
    expect(editor).to_be_focused()
    assert page.evaluate('getSelection().toString()') == 'Alpha'
    page.get_by_role('button', name='Replace all', exact=True).click()
    expect(editor).to_have_text('Omega beta. Omega result.')
    expect(page.locator('#folioSidebar')).to_contain_text('Replaced 2 matches')
    page.get_by_role('button', name='Close sidebar', exact=True).click()
    editor.click()
    editor.press('ControlOrMeta+a')
    editor.press('ArrowRight')
    editor.press('Enter')
    page.get_by_role('tab', name='Insert', exact=True).click()
    page.get_by_role('button', name='Insert table', exact=True).click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('spinbutton', name='Table rows', exact=True).fill('2')
    dialog.get_by_role('spinbutton', name='Table columns', exact=True).fill('2')
    dialog.get_by_role('button', name='Insert table', exact=True).click()
    cells = editor.locator('th, td')
    expect(cells).to_have_count(4)
    for index, text in enumerate(('Measure', 'Value', 'Confidence', 'High')):
        cells.nth(index).click()
        page.keyboard.insert_text(text)
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    expect(editor).to_contain_text('Omega beta. Omega result.')
    expect(editor.locator('table tr')).to_have_count(2)
    for index, text in enumerate(('Measure', 'Value', 'Confidence', 'High')):
        expect(cells.nth(index)).to_have_text(text)
    page.screenshot(path=str(browser.tmp / 'folio-table.png'), full_page=True)


def test_line_spacing_applies_to_a_mixed_heading_and_paragraph_selection(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser, 'Mixed paragraph styles')
    editor.fill('A heading')
    page.get_by_role('button', name='Heading 1', exact=True).click()
    editor.press('End')
    editor.press('Enter')
    page.get_by_role('button', name='Normal', exact=True).click()
    page.keyboard.insert_text('A normal paragraph.')
    editor.press('ControlOrMeta+a')
    page.get_by_role('combobox', name='Line spacing', exact=True).select_option('2')
    assert editor.locator('h1').evaluate('element => element.style.lineHeight') == '2'
    assert all(value == '2' for value in editor.locator('p').evaluate_all(
        'elements => elements.map(element => element.style.lineHeight)'))
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    assert editor.locator('h1').evaluate('element => element.style.lineHeight') == '2'
    assert all(value == '2' for value in editor.locator('p').evaluate_all(
        'elements => elements.map(element => element.style.lineHeight)'))


def test_appearance_changes_ui_colors_and_print_keeps_white_paper(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    page.goto(browser.base + '/folio')
    expect(page.locator('body')).to_have_css('margin', '0px')

    def choose_theme(theme):
        page.get_by_role('button', name='Appearance', exact=True).click()
        page.locator('#appearanceMode').select_option(theme)
        page.locator('#appearanceDialog').get_by_role('button', name='Done', exact=True).click()
        expect(page.locator('html')).to_have_attribute('data-theme', theme)
        page.evaluate('''async () => {
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          await Promise.all(document.getAnimations()
            .filter(animation => animation.effect.getTiming().iterations !== Infinity)
            .map(animation => animation.finished.catch(() => {})));
        }''')
        active = page.locator('#globalNavigation nav a[aria-current="page"]')
        assert active.evaluate(NAVIGATION_CONTRAST) >= 4.5, theme

    for theme in ('light', 'dark'):
        choose_theme(theme)
        expect(page.get_by_role('button', name='Project report', exact=True)).to_be_visible()
        search = page.get_by_role('searchbox', name='Search documents', exact=True)
        assert search.evaluate(NAVIGATION_CONTRAST) >= 4.5, theme
        page.screenshot(path=str(browser.tmp / f'folio-dashboard-{theme}.png'), full_page=True, animations='disabled')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.get_by_role('button', name='Project report', exact=True).click()
    editor = page.get_by_role('textbox', name='Document content', exact=True)
    expect(editor).to_contain_text('Executive summary')
    page.get_by_role('textbox', name='Document title', exact=True).fill('Evidence review · Project report')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    colors = {}
    for theme in ('light', 'dark'):
        choose_theme(theme)
        colors[theme] = page.locator('#folioRibbon').evaluate('element => getComputedStyle(element).backgroundColor')
        expect(page.locator('#folioPaper')).to_have_css('background-color', 'rgb(255, 255, 255)')
        page.screenshot(path=str(browser.tmp / f'folio-editor-{theme}.png'), full_page=True, animations='disabled')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.set_viewport_size({'width': 1500, 'height': 1050})
    assert colors['light'] != colors['dark']
    page.reload()
    expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
    page.emulate_media(media='print')
    expect(page.locator('#globalNavigation')).to_be_hidden()
    expect(page.locator('#folioRibbon')).to_be_hidden()
    expect(editor).to_be_visible()
    expect(page.locator('#folioPaper')).to_have_css('background-color', 'rgb(255, 255, 255)')
    page.screenshot(path=str(browser.tmp / 'folio-print.png'), full_page=True)
