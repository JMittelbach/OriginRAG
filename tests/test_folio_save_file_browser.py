"""Save-as writes real export bytes using a stub picker, never an OS dialog in CI."""
import io
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse
import zipfile

import pytest

from test_folio_http import folio_server as folio_server, request_json
from test_folio_browser import folio_browser as folio_browser, create_document, saved_document


PICKER_STUB = r'''(() => {
  const state = window.saveFileTest = {mode:'success', calls:[], writes:[], creates:0, closes:0, aborts:0, trace:[]};
  const realFetch = window.fetch;
  window.fetch = (...args) => { state.trace.push('fetch:' + String(args[0])); return realFetch(...args); };
  Object.defineProperty(window, 'showSaveFilePicker', {configurable:true, writable:true, value:async options => {
    state.trace.push('picker'); state.calls.push(options);
    if (state.mode === 'cancel') throw new DOMException('The user canceled the picker.', 'AbortError');
    if (state.mode === 'defer') await new Promise(resolve => { state.resolvePicker = resolve; });
    return {name:options.suggestedName, createWritable:async () => {
      state.creates++;
      if (state.mode === 'permission') throw new DOMException('File permission denied.', 'NotAllowedError');
      if (state.mode === 'create_wait') await new Promise(resolve => { state.resolveCreate = resolve; });
      return {
        write:async data => {
          if (state.mode === 'write') throw new Error('The destination disk is full.');
          const bytes = data instanceof Blob ? new Uint8Array(await data.arrayBuffer()) :
            typeof data === 'string' ? new TextEncoder().encode(data) : new Uint8Array(data);
          state.writes.push(Array.from(bytes));
          if (state.mode === 'write_wait') await new Promise(resolve => { state.resolveWrite = resolve; });
        },
        close:async () => {
          if (state.mode === 'close') throw new Error('The file could not be closed.');
          if (state.mode === 'close_wait') await new Promise(resolve => { state.resolveClose = resolve; });
          state.closes++;
        },
        abort:async () => { state.aborts++; },
      };
    }};
  }});
})();'''


def use_picker(browser, mode='success'):
    browser.page.add_init_script(PICKER_STUB)
    editor = create_document(browser, 'Original Origin title')
    browser.expect(browser.page.locator('#folioSaveStatus')).to_have_text('Saved')
    browser.page.evaluate('mode => {saveFileTest.mode=mode;saveFileTest.trace=[]}', mode)
    return editor


def save_dialog(browser, filename='A chosen filename', format_name='docx'):
    page = browser.page
    page.get_by_role('tab', name='File', exact=True).click()
    page.get_by_role('button', name='Save as…', exact=True).click()
    dialog = page.get_by_role('dialog', name='Save as…', exact=True)
    browser.expect(dialog).to_be_visible()
    dialog.get_by_role('textbox', name='Filename', exact=True).fill(filename)
    dialog.get_by_role('combobox', name='Format', exact=True).select_option(format_name)
    return dialog


def assert_export(data, format_name, text):
    if format_name == 'docx':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert text in archive.read('word/document.xml').decode()
            assert '[Content_Types].xml' in archive.namelist()
    elif format_name == 'json':
        exported = json.loads(data)
        assert text in json.dumps(exported, ensure_ascii=False)
        assert exported['title'] == 'Original Origin title'
    else:
        assert text in data.decode('utf-8')
        if format_name == 'html':
            assert '<!doctype html>' in data.decode().lower()


@pytest.mark.parametrize('format_name', ['docx', 'html', 'txt', 'json'])
def test_native_save_as_writes_real_bytes_without_renaming_original(folio_browser, format_name):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = use_picker(browser)
    editor.fill('Portable café observations.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    original_url, original = page.url, saved_document(browser)
    downloads = []
    page.on('download', lambda download: downloads.append(download))
    dialog = save_dialog(browser, 'Chosen destination', format_name)
    if format_name == 'docx' and (review_dir := os.environ.get('ORIGINRAG_UI_REVIEW_DIR')):
        page.screenshot(path=str(Path(review_dir) / 'folio-save-as-dialog.png'), full_page=True, animations='disabled')
    dialog.get_by_role('button', name='Save file', exact=True).click()
    page.wait_for_function('() => saveFileTest.closes === 1')
    expect(dialog).not_to_be_visible()
    result = page.evaluate('saveFileTest')
    assert len(result['calls']) == 1 and result['creates'] == 1
    assert result['calls'][0]['suggestedName'] == 'Chosen destination.' + format_name
    assert result['calls'][0]['types'][0]['accept']
    assert_export(bytes(result['writes'][0]), format_name, 'Portable café observations.')
    assert not downloads
    assert page.url == original_url and saved_document(browser) == original
    expect(page.get_by_role('textbox', name='Document title', exact=True)).to_have_value('Original Origin title')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    assert len(request_json(browser.port, headers=browser.headers)['documents']) == 1


def test_picker_opens_before_waiting_for_unsaved_edits_and_exports_them(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = use_picker(browser)
    original_url = page.url
    held = []

    def hold_save(route):
        payload = route.request.post_data_json if route.request.method == 'POST' else {}
        if payload.get('action') == 'save' and not held:
            held.append((route, route.fetch()))
        else:
            route.continue_()

    page.route('**/api/folio', hold_save)
    editor.fill('The latest unsaved draft belongs in the chosen file.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saving…')
    dialog = save_dialog(browser, 'Latest draft', 'json')
    page.evaluate('saveFileTest.trace=[]')
    dialog.get_by_role('button', name='Save file', exact=True).click()
    page.wait_for_function('() => saveFileTest.calls.length === 1', timeout=3000)
    assert page.evaluate('saveFileTest.trace[0]') == 'picker'
    assert page.evaluate('saveFileTest.writes.length') == 0
    assert held
    held[0][0].fulfill(response=held[0][1])
    page.wait_for_function('() => saveFileTest.closes === 1')
    assert_export(bytes(page.evaluate('saveFileTest.writes[0]')), 'json',
                  'The latest unsaved draft belongs in the chosen file.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    assert page.url == original_url


def test_native_picker_cancel_keeps_dialog_and_document_without_write(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    use_picker(browser, 'cancel')
    original_url, original = page.url, saved_document(browser)
    downloads, exports = [], []
    page.on('download', lambda download: downloads.append(download))
    page.on('request', lambda request: exports.append(request.url) if '/api/folio/export?' in request.url else None)
    dialog = save_dialog(browser)
    before_notice = page.locator('#folioNotice').inner_text()
    dialog.get_by_role('button', name='Save file', exact=True).click()
    page.wait_for_function('() => saveFileTest.calls.length === 1')
    expect(dialog.get_by_role('button', name='Save file', exact=True)).to_be_enabled()
    expect(dialog).to_be_visible()
    assert page.evaluate('saveFileTest.creates') == 0
    assert not exports and not downloads
    assert page.locator('#folioNotice').inner_text() == before_notice
    assert page.url == original_url and saved_document(browser) == original
    dialog.get_by_role('button', name='Cancel', exact=True).click()
    expect(dialog).not_to_be_visible()


@pytest.mark.parametrize(('mode', 'error'), [
    ('permission', 'permission denied'), ('write', 'disk is full'), ('close', 'could not be closed'),
])
def test_native_file_failure_has_no_fake_success_and_allows_retry(folio_browser, mode, error):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = use_picker(browser, mode)
    editor.fill('Preserve this document when the computer write fails.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    original_url, original = page.url, saved_document(browser)
    dialog = save_dialog(browser, 'Retry destination', 'txt')
    dialog.get_by_role('button', name='Save file', exact=True).click()
    expect(dialog.get_by_role('alert')).to_contain_text(re.compile(error, re.I))
    expect(dialog).to_be_visible()
    assert page.evaluate('saveFileTest.closes') == 0
    assert page.url == original_url and saved_document(browser) == original
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.evaluate('saveFileTest.mode="success"')
    dialog.get_by_role('button', name='Save file', exact=True).click()
    page.wait_for_function('() => saveFileTest.closes === 1')
    expect(dialog).not_to_be_visible()
    assert b'Preserve this document' in bytes(page.evaluate('saveFileTest.writes.at(-1)'))


@pytest.mark.parametrize('format_name', ['docx', 'html', 'txt', 'json'])
def test_unsupported_picker_downloads_actual_file_bytes(folio_browser, format_name):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    page.add_init_script("Object.defineProperty(window,'showSaveFilePicker',{configurable:true,value:undefined})")
    editor = create_document(browser, 'Original Origin title')
    editor.fill('Download fallback contains these café observations.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    original_url, original = page.url, saved_document(browser)
    dialog = save_dialog(browser, 'Portable fallback', format_name)
    with page.expect_download() as downloaded:
        dialog.get_by_role('button', name='Save file', exact=True).click()
    assert downloaded.value.suggested_filename == 'Portable fallback.' + format_name
    assert_export(Path(downloaded.value.path()).read_bytes(), format_name,
                  'Download fallback contains these café observations.')
    expect(dialog).not_to_be_visible()
    assert page.url == original_url and saved_document(browser) == original


def test_delayed_picker_does_not_export_a_different_document(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = use_picker(browser, 'defer')
    editor.fill('The source document is the export target.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    original = saved_document(browser)
    other = request_json(browser.port, 'POST', headers=browser.headers,
                         payload={'action': 'create', 'title': 'A different document'})
    exported = []
    page.on('request', lambda request: exported.append(request.url) if '/api/folio/export?' in request.url else None)
    dialog = save_dialog(browser, 'Captured source', 'json')
    dialog.get_by_role('button', name='Save file', exact=True).click()
    page.wait_for_function('() => typeof saveFileTest.resolvePicker === "function"')
    # A modal normally prevents this pointer action; a queued application action
    # must still never make the ongoing export silently target another document.
    page.get_by_role('button', name='Documents', exact=True, include_hidden=True).evaluate('(button) => button.click()')
    expect(page).to_have_url(browser.base + '/folio')
    page.get_by_role('button', name=other['title'], exact=True, include_hidden=True).evaluate('(button) => button.click()')
    expect(page).to_have_url(browser.base + '/folio?id=' + other['id'])
    page.evaluate('saveFileTest.resolvePicker()')
    expect(dialog.get_by_role('alert')).to_contain_text('The document changed')
    assert all(parse_qs(urlparse(url).query)['id'] == [original['id']] for url in exported)
    assert page.evaluate('saveFileTest.writes.length') == 0
    assert request_json(browser.port, path='/api/folio?id=' + other['id'], headers=browser.headers) == other


def test_changed_server_revision_during_picker_is_not_written(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    use_picker(browser, 'defer')
    original = saved_document(browser)
    dialog = save_dialog(browser, 'Stable revision', 'json')
    dialog.get_by_role('button', name='Save file', exact=True).click()
    page.wait_for_function('() => typeof saveFileTest.resolvePicker === "function"')
    remote = request_json(browser.port, 'POST', headers=browser.headers,
                          payload={**original, 'action': 'save', 'title': 'Another tab changed the document'})
    page.evaluate('saveFileTest.resolvePicker()')
    expect(dialog.get_by_role('alert')).not_to_be_empty()
    assert page.evaluate('saveFileTest.writes.length') == 0
    assert page.evaluate('saveFileTest.closes') == 0
    assert request_json(browser.port, path='/api/folio?id=' + original['id'], headers=browser.headers) == remote


def test_save_keyboard_shortcuts_keep_origin_save_and_save_as_separate(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = use_picker(browser)
    editor.fill('Keyboard save retains the normal Origin autosave workflow.')
    editor.press('ControlOrMeta+s')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    assert 'Keyboard save' in json.dumps(saved_document(browser)['content'])
    assert page.evaluate('saveFileTest.calls.length') == 0
    expect(page.get_by_role('dialog', name='Save as…', exact=True)).not_to_be_visible()
    editor.press('ControlOrMeta+Shift+s')
    dialog = page.get_by_role('dialog', name='Save as…', exact=True)
    expect(dialog).to_be_visible()
    dialog.get_by_role('button', name='Cancel', exact=True).click()
    expect(dialog).not_to_be_visible()
    assert page.evaluate('saveFileTest.calls.length') == 0


@pytest.mark.parametrize(('mode', 'resolver', 'cancel'), [
    ('create_wait', 'resolveCreate', 'button'), ('write_wait', 'resolveWrite', 'button'),
    ('write_wait', 'resolveWrite', 'escape'),
])
def test_dialog_cancel_during_pending_file_write_aborts_without_committing(folio_browser, mode, resolver, cancel):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    use_picker(browser, mode)
    original = saved_document(browser)
    dialog = save_dialog(browser)
    dialog.get_by_role('button', name='Save file', exact=True).click()
    page.wait_for_function('(name) => typeof saveFileTest[name] === "function"', arg=resolver)
    if cancel == 'escape':
        page.keyboard.press('Escape')
    else:
        dialog.get_by_role('button', name='Cancel', exact=True).click()
    expect(dialog).not_to_be_visible()
    page.evaluate('(name) => saveFileTest[name]()', resolver)
    page.wait_for_function('() => saveFileTest.aborts === 1 || saveFileTest.closes === 1')
    assert page.evaluate('saveFileTest.aborts') == 1
    assert page.evaluate('saveFileTest.closes') == 0
    assert not page.locator('#folioNotice').inner_text().startswith('File saved:')
    assert saved_document(browser) == original


def test_native_stream_close_is_non_cancelable_until_commit_finishes(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    use_picker(browser, 'close_wait')
    dialog = save_dialog(browser, 'Committed destination')
    dialog.get_by_role('button', name='Save file', exact=True).click()
    page.wait_for_function('() => typeof saveFileTest.resolveClose === "function"')
    expect(dialog.get_by_role('button', name='Cancel', exact=True)).to_be_disabled()
    page.keyboard.press('Escape')
    expect(dialog).to_be_visible()
    assert page.evaluate('saveFileTest.closes') == 0
    assert not page.locator('#folioNotice').inner_text().startswith('File saved:')
    page.evaluate('saveFileTest.resolveClose()')
    page.wait_for_function('() => saveFileTest.closes === 1')
    expect(dialog).not_to_be_visible()
    expect(page.locator('#folioNotice')).to_contain_text('File saved: Committed destination.docx')
    assert page.evaluate('saveFileTest.aborts') == 0


def test_html_login_response_cannot_be_written_as_a_docx(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    use_picker(browser)
    page.route('**/api/folio/export?**', lambda route: route.fulfill(
        status=200, content_type='text/html', body='<!doctype html><html><body>Please log in</body></html>'))
    dialog = save_dialog(browser)
    dialog.get_by_role('button', name='Save file', exact=True).click()
    expect(dialog.get_by_role('alert')).to_contain_text('No file was saved')
    expect(dialog).to_be_visible()
    assert page.evaluate('saveFileTest.calls.length') == 1
    assert page.evaluate('saveFileTest.creates') == 0
    assert page.evaluate('saveFileTest.writes.length') == 0
