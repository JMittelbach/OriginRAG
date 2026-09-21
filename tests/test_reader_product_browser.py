import hashlib
import json
from pathlib import Path

import pytest

import test_web_chat as helpers
from test_reader_notes import MARK
from test_web_reader import reader_server as reader_server


@pytest.fixture
def reader_page(reader_server):
    playwright = pytest.importorskip('playwright.sync_api')
    port, fingerprint, _, _ = reader_server
    base = f'http://127.0.0.1:{port}'
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        page = browser.new_page(
            viewport={'width': 1440, 'height': 1000},
            permissions=['clipboard-read', 'clipboard-write'],
        )
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        yield page, playwright.expect, base, fingerprint
        browser.close()
        assert not errors


def seed_notes(reader_server):
    port, fingerprint, _, _ = reader_server
    samples = [
        {**MARK, 'quote': 'A useful passage.', 'note': 'Compare the methods.', 'color': 'yellow'},
        {**MARK, 'page': 2, 'quote': 'Further useful details.\nA second line.', 'note': 'Check the sample size.', 'color': 'mint'},
    ]
    result = None
    for mark in samples:
        status, _, raw, _ = helpers.http_request(
            port, 'POST', '/api/reader/notes',
            payload={'paper_id': 'P1', 'fingerprint': fingerprint, 'action': 'add', 'mark': mark},
        )
        assert status == 200
        result = json.loads(raw)
    return result['items']


def open_reader(page, base):
    page.goto(base + '/reader?paper_id=P1')
    page.wait_for_selector('#fit:not([disabled])', timeout=20000)


@pytest.mark.parametrize('theme,width,height', [('light', 1440, 1000), ('dark', 1440, 1000), ('light', 390, 844), ('dark', 390, 844)])
def test_reader_product_themes_responsive_layout_and_download(reader_server, reader_page, theme, width, height):
    seed_notes(reader_server)
    page, expect, base, _ = reader_page
    page.set_viewport_size({'width': width, 'height': height})
    page.add_init_script(f"localStorage.setItem('originrag-reader-theme', {json.dumps(theme)})")
    open_reader(page, base)
    expect(page.locator('html')).to_have_attribute('data-theme', theme)
    expect(page.locator('.note')).to_have_count(2)
    expect(page.locator('#downloadPDF')).to_have_attribute('href', '/api/library/pdf?paper_id=P1')
    assert page.locator('#downloadPDF').get_attribute('download') is not None
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    expect(page.locator('#readingPosition')).to_contain_text('1')
    expect(page.locator('#notesFilter')).to_be_visible()
    page.locator('#fitPage').click()
    page.wait_for_selector('#fit:not([disabled])')
    page_box = page.locator('#page').bounding_box()
    workspace_box = page.locator('#workspace').bounding_box()
    assert page_box['width'] <= workspace_box['width'] + 1
    assert page_box['height'] <= workspace_box['height'] + 1
    assert abs(page_box['width'] / page_box['height'] - 595 / 842) < .003
    screenshot_dir = Path('/private/tmp/originrag-reader-product') if Path('/private/tmp').exists() else reader_server[2] / 'screenshots'
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshot_dir / f'reader-{theme}-{width}.png'), full_page=True)


def test_reader_filters_keep_unsaved_note_and_colour_drafts(reader_server, reader_page):
    samples = seed_notes(reader_server)
    page, expect, base, _ = reader_page
    open_reader(page, base)
    first = page.locator(f'.note[data-note-id="{samples[0]["id"]}"]')
    first.locator('textarea').fill('An unsaved comparison survives filtering.')
    first.locator('.note-color').select_option('red')
    expect(page.locator('#notesState')).to_have_text('Unsaved changes')
    page.locator('#notesFilter').fill('sample size')
    expect(page.locator('.note:visible')).to_have_count(1)
    expect(page.locator('.note:visible textarea')).to_have_value('Check the sample size.')
    page.locator('#notesFilter').fill('a query with no matching passage')
    expect(page.locator('.note:visible')).to_have_count(0)
    expect(page.locator('#notesEmpty')).to_be_visible()
    page.locator('#notesFilter').fill('')
    expect(page.locator('.note:visible')).to_have_count(2)
    expect(first.locator('textarea')).to_have_value('An unsaved comparison survives filtering.')
    expect(first.locator('.note-color')).to_have_value('red')
    page.locator('#notesColor').select_option('mint')
    expect(page.locator('.note:visible')).to_have_count(1)
    page.locator('#notesColor').select_option('all')
    expect(first.locator('textarea')).to_have_value('An unsaved comparison survives filtering.')
    expect(first.get_by_role('button', name='Save note', exact=True)).to_be_enabled()
    first.get_by_role('button', name='Save note', exact=True).click()
    expect(page.locator('#notesState')).to_have_text('Saved')
    page.reload()
    page.wait_for_selector('#fit:not([disabled])')
    expect(first.locator('textarea')).to_have_value('An unsaved comparison survives filtering.')
    expect(first.locator('.note-color')).to_have_value('red')


def test_reader_recolour_requires_explicit_save_and_quote_copy_is_read_only(reader_server, reader_page):
    samples = seed_notes(reader_server)
    page, expect, base, _ = reader_page
    open_reader(page, base)
    writes = []
    page.on('request', lambda request: writes.append(request.post_data_json) if request.method == 'POST' and request.url.endswith('/api/reader/notes') else None)
    first = page.locator(f'.note[data-note-id="{samples[0]["id"]}"]')
    first.locator('.note-copy').click()
    page.wait_for_function("navigator.clipboard.readText().then(text => text === 'A useful passage.')")
    assert not writes
    first.locator('.note-color').select_option('rose')
    expect(first.get_by_role('button', name='Save note', exact=True)).to_be_enabled()
    assert not writes
    first.get_by_role('button', name='Save note', exact=True).click()
    expect(page.locator('#notesState')).to_have_text('Saved')
    assert len(writes) == 1
    assert writes[0]['mark']['color'] == 'rose'
    assert writes[0]['mark']['quote'] == samples[0]['quote']
    expect(page.locator('[data-page="1"] .mark.rose')).to_have_count(1)


def test_reader_markdown_export_retains_saved_page_quote_and_note(reader_server, reader_page):
    seed_notes(reader_server)
    page, expect, base, fingerprint = reader_page
    open_reader(page, base)
    page.locator('.note textarea').first.fill('THIS DRAFT HAS NOT BEEN SAVED')
    page.locator('#notesFilter').fill('sample size')
    expect(page.locator('.note:visible')).to_have_count(1)
    page.locator('#exportFormat').select_option('markdown')
    with page.expect_download() as pending:
        page.locator('#export').click()
    download = pending.value
    assert download.suggested_filename.endswith('.md')
    text = Path(download.path()).read_text()
    for required in ['Synthetic reader paper', 'P1', fingerprint, 'Page 1', 'Page 2', 'A useful passage.', 'Compare the methods.', 'Further useful details.', 'A second line.', 'Check the sample size.']:
        assert required in text
    assert 'THIS DRAFT HAS NOT BEEN SAVED' not in text
    expect(page.locator('#status')).to_contain_text('saved notes only')
    page.locator('#exportFormat').select_option('json')
    with page.expect_download() as pending:
        page.locator('#export').click()
    exported = json.loads(Path(pending.value.path()).read_text())
    assert len(exported['annotations']) == 2
    assert exported['fingerprint'] == fingerprint


def test_reader_download_is_the_exact_library_pdf(reader_page):
    page, _, base, fingerprint = reader_page
    open_reader(page, base)
    with page.expect_download() as pending:
        page.locator('#downloadPDF').click()
    download = pending.value
    assert download.suggested_filename.endswith('.pdf')
    content = Path(download.path()).read_bytes()
    assert content.startswith(b'%PDF-')
    assert hashlib.sha256(content).hexdigest() == fingerprint


def test_reader_html_error_preserves_note_and_colour_until_successful_retry(reader_server, reader_page):
    samples = seed_notes(reader_server)
    page, expect, base, _ = reader_page
    open_reader(page, base)
    first = page.locator(f'.note[data-note-id="{samples[0]["id"]}"]')
    first.locator('textarea').fill('An idea that must survive a gateway failure.')
    first.locator('.note-color').select_option('red')
    page.route('**/api/reader/notes', lambda route: route.fulfill(status=503, content_type='text/html', body='<h1>Gateway unavailable</h1>'))
    first.get_by_role('button', name='Save note', exact=True).click()
    expect(page.locator('#notesState')).to_have_text('Save not confirmed')
    expect(page.locator('#status')).to_have_text('The server could not complete this request. Please try again.')
    expect(first.locator('textarea')).to_have_value('An idea that must survive a gateway failure.')
    expect(first.locator('.note-color')).to_have_value('red')
    expect(first.get_by_role('button', name='Save note', exact=True)).to_be_enabled()
    page.unroute('**/api/reader/notes')
    first.get_by_role('button', name='Save note', exact=True).click()
    expect(page.locator('#notesState')).to_have_text('Saved')
    page.reload()
    page.wait_for_selector('#fit:not([disabled])')
    expect(first.locator('textarea')).to_have_value('An idea that must survive a gateway failure.')
    expect(first.locator('.note-color')).to_have_value('red')


def test_reader_concurrently_deleted_note_keeps_downloadable_unsaved_draft(reader_server, reader_page):
    samples = seed_notes(reader_server)
    page, expect, base, fingerprint = reader_page
    port = reader_server[0]
    open_reader(page, base)
    first = page.locator(f'.note[data-note-id="{samples[0]["id"]}"]')
    first.locator('textarea').fill('Keep this thought even if the highlight is deleted elsewhere.')
    first.locator('.note-color').select_option('red')
    status, _, raw, _ = helpers.http_request(
        port, 'POST', '/api/reader/notes',
        payload={
            'paper_id': 'P1', 'fingerprint': fingerprint, 'action': 'delete',
            'id': samples[0]['id'], 'revision': samples[0]['revision'],
        },
    )
    assert status == 200
    assert [item['id'] for item in json.loads(raw)['items']] == [samples[1]['id']]
    writes = []
    page.on('request', lambda request: writes.append(request.post_data_json) if request.method == 'POST' and request.url.endswith('/api/reader/notes') else None)
    page.locator('#reload').click()
    recovery = page.locator('.draft-recovery')
    expect(recovery).to_be_visible()
    expect(recovery.locator('blockquote')).to_have_text(samples[0]['quote'])
    expect(recovery.locator('textarea')).to_have_value('Keep this thought even if the highlight is deleted elsewhere.')
    assert recovery.locator('textarea').evaluate('element => element.readOnly')
    expect(page.locator('#notesState')).to_have_text('Unsaved changes')
    page.locator('#notesFilter').fill('no saved note matches this phrase')
    expect(recovery).to_be_visible()
    with page.expect_download() as pending:
        recovery.get_by_role('button', name='Save draft copy', exact=True).click()
    exported = json.loads(Path(pending.value.path()).read_text())
    assert exported['format'] == 'originrag-reader-draft-v1'
    assert exported['paper_id'] == 'P1' and exported['fingerprint'] == fingerprint
    assert exported['annotation'] == {
        **samples[0], 'note': 'Keep this thought even if the highlight is deleted elsewhere.', 'color': 'red',
    }
    page.once('dialog', lambda dialog: dialog.accept())
    recovery.get_by_role('button', name='Dismiss draft', exact=True).click()
    expect(recovery).to_have_count(0)
    expect(page.locator('#notesState')).to_have_text('Saved')
    page.locator('#notesFilter').fill('')
    expect(page.locator('.note')).to_have_count(1)
    expect(page.locator('.note textarea')).to_have_value(samples[1]['note'])
    assert not writes
    url = '/api/reader/notes?paper_id=P1&fingerprint=' + fingerprint
    remaining = json.loads(helpers.http_request(port, 'GET', url)[2])['items']
    assert remaining == [samples[1]]


@pytest.mark.parametrize('supported', [True, False])
def test_reader_focus_view_unavailable_keeps_reader_usable(reader_page, supported):
    page, expect, base, _ = reader_page
    replacement = "() => Promise.reject(new Error('Browser denied full screen'))" if supported else 'undefined'
    page.add_init_script(f"Object.defineProperty(Element.prototype, 'requestFullscreen', {{configurable: true, value: {replacement}}})")
    open_reader(page, base)
    if supported:
        page.locator('#fullscreen').click()
        expect(page.locator('#status')).to_have_text('Full-screen reading was not available. You can keep reading in this window.')
        expect(page.locator('#fullscreen')).to_be_enabled()
    else:
        expect(page.locator('#fullscreen')).to_be_disabled()
    page.locator('#next').click()
    expect(page.locator('#pageNumber')).to_have_value('2')


def test_reader_shortcuts_dialog_does_not_trigger_background_navigation(reader_page):
    page, expect, base, _ = reader_page
    open_reader(page, base)
    page.locator('#shortcuts').click()
    expect(page.locator('#shortcutsDialog')).to_be_visible()
    page.keyboard.press('ArrowRight')
    expect(page.locator('#pageNumber')).to_have_value('1')
    page.keyboard.press('Control+f')
    expect(page.locator('#searchBar')).to_be_hidden()
    page.keyboard.press('Escape')
    expect(page.locator('#shortcutsDialog')).to_be_hidden()
    page.locator('#pageNumber').focus()
    page.keyboard.press('ArrowRight')
    expect(page.locator('#pageNumber')).to_have_value('1')
    page.evaluate('document.activeElement.blur()')
    page.keyboard.press('ArrowRight')
    expect(page.locator('#pageNumber')).to_have_value('2')


@pytest.mark.parametrize('reader_server', [{'pages': 8}], indirect=True)
def test_reader_fit_page_handles_landscape_and_sidebar_resize_bounds(reader_page):
    page, expect, base, _ = reader_page
    open_reader(page, base)
    page.locator('#pageNumber').fill('4')
    page.locator('#pageNumber').press('Enter')
    page.locator('#fitPage').click()
    page.wait_for_selector('#fit:not([disabled])')
    expect(page.locator('#pageNumber')).to_have_value('4')
    box = page.locator('#page').bounding_box()
    area = page.locator('#workspace').bounding_box()
    assert box['width'] <= area['width'] + 1 and box['height'] <= area['height'] + 1
    assert abs(box['width'] / box['height'] - 842 / 595) < .003
    assert box['x'] >= area['x'] - 1 and box['y'] >= area['y'] - 1
    assert box['x'] + box['width'] <= area['x'] + area['width'] + 1
    assert box['y'] + box['height'] <= area['y'] + area['height'] + 1
    handle = page.locator('#notesResize')
    expect(handle).to_be_visible()
    start = page.locator('#notesPanel').bounding_box()['width']
    handle.focus()
    page.keyboard.press('ArrowLeft')
    assert page.locator('#notesPanel').bounding_box()['width'] > start
    rectangle = handle.bounding_box()
    page.mouse.move(rectangle['x'] + rectangle['width'] / 2, rectangle['y'] + 40)
    page.mouse.down()
    page.mouse.move(30, rectangle['y'] + 40, steps=10)
    page.mouse.up()
    assert 240 <= page.locator('#notesPanel').bounding_box()['width'] <= 600
    assert page.locator('#workspace').bounding_box()['width'] >= 440
    page.set_viewport_size({'width': 390, 'height': 844})
    expect(handle).to_be_hidden()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
