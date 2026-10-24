import json
from pathlib import Path

import pytest

import test_web_chat as helpers
from test_reader_notes import MARK
from test_reader_product_browser import open_reader, reader_page as reader_page
from test_web_reader import reader_server as reader_server


@pytest.mark.parametrize(
    'reader_server,profile',
    [({'profile': profile}, profile) for profile in ('full', 'rag', 'write')],
    indirect=['reader_server'],
)
def test_reader_shared_navigation_matches_profile_and_links_work(reader_page, profile):
    page, expect, base, _ = reader_page
    open_reader(page, base)
    header_id = '#globalNavigation' if profile == 'full' else '#applicationHeader'
    header = page.locator(header_id)
    expect(header).to_be_visible()
    expect(page.locator('body > header')).to_have_count(1)
    expect(page.locator('#globalNavigation')).to_have_count(int(profile == 'full'))
    expect(page.locator('#applicationHeader')).to_have_count(int(profile != 'full'))
    expected_links = [('Bookshelf', '/bookshelf'), ('Library', '/library')]
    if profile == 'full':
        expected_links = [('OriginRAG', '/chat'), *expected_links, ('Quill', '/write'), ('Folio', '/folio')]
    assert header.locator('nav a').evaluate_all(
        'links => links.map(link => [link.textContent, link.getAttribute("href")])'
    ) == [list(item) for item in expected_links]
    brand = header.locator('.origin-brand')
    expect(brand).to_have_attribute('href', '/write' if profile == 'write' else '/chat')
    if profile != 'full':
        expect(brand).to_have_text('Quill' if profile == 'write' else 'OriginRAG')
        disabled_path = '/chat' if profile == 'write' else '/write'
        expect(header.locator(f'a[href="{disabled_path}"]')).to_have_count(0)
        expect(page.locator(f'#globalStartPage option[value="{disabled_path}"]')).to_have_count(0)
    for _, path in expected_links:
        assert page.request.get(base + path).status == 200
    assert page.request.get(base + brand.get_attribute('href')).status == 200
    for selector in ('#appearanceButton', '#globalSettings', '#globalAccount'):
        expect(header.locator(selector)).to_have_count(1)
        expect(header.locator(selector)).to_be_visible()
    for name, path in [('Library', '/library'), ('Bookshelf', '/bookshelf')]:
        header.get_by_role('link', name=name, exact=True).click()
        page.wait_for_url(base + path)
        expect(page.locator(header_id)).to_be_visible()
        open_reader(page, base)


@pytest.mark.parametrize('width,height', [(1440, 1000), (390, 844)])
def test_reader_focus_preserves_global_header_without_a_paper_title_row(reader_page, width, height):
    page, expect, base, _ = reader_page
    page.set_viewport_size({'width': width, 'height': height})
    open_reader(page, base)
    header = page.locator('#globalNavigation')
    controls = page.get_by_role('navigation', name='Reading controls')
    expect(page.locator('#title')).to_have_text('Synthetic reader paper')
    assert page.locator('#title').evaluate('''element => {
        const style = getComputedStyle(element);
        return style.position === "absolute" && element.offsetWidth <= 1 && element.offsetHeight <= 1;
    }''')
    expect(page.locator('body > header')).to_have_count(1)
    for selector in ('#readStatus', '#downloadPDF', '#original'):
        expect(controls.locator(selector)).to_be_visible()
    header_box = header.bounding_box()
    assert header_box['y'] == pytest.approx(0, abs=1)
    assert controls.bounding_box()['y'] == pytest.approx(
        header_box['y'] + header_box['height'], abs=1,
    )
    if width == 1440:
        assert controls.bounding_box()['height'] <= 56
    page.locator('#fullscreen').click()
    expect(page.locator('#fullscreen')).to_have_attribute('aria-pressed', 'true')
    expect(header).to_be_visible()
    expect(page.locator('#readingFooter')).to_be_hidden()
    expect(controls).to_be_visible()
    for name in ('OriginRAG', 'Bookshelf', 'Library', 'Quill'):
        expect(header.get_by_role('link', name=name, exact=True)).to_be_visible()
    for selector in ('#appearanceButton', '#globalSettings', '#globalAccount'):
        expect(header.locator(selector)).to_be_visible()
    assert header.bounding_box() == pytest.approx(header_box, abs=1)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#globalSettings').click()
    expect(page.locator('#globalSettingsDialog')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.locator('#globalSettingsDialog')).to_be_hidden()
    expect(page.locator('#fullscreen')).to_have_attribute('aria-pressed', 'true')
    page.locator('#fullscreen').click()
    expect(page.locator('#readingFooter')).to_be_visible()
    expect(header).to_be_visible()


@pytest.mark.parametrize('width,height', [(1440, 1000), (390, 844)])
def test_reader_shared_dialogs_do_not_trigger_pdf_shortcuts(reader_page, width, height):
    page, expect, base, _ = reader_page
    page.set_viewport_size({'width': width, 'height': height})
    open_reader(page, base)
    page.locator('#fullscreen').click()
    for button, dialog_id in [
        ('#appearanceButton', '#appearanceDialog'),
        ('#globalSettings', '#globalSettingsDialog'),
        ('#globalAccount', '#globalAccountDialog'),
    ]:
        page.locator(button).click()
        dialog = page.locator(dialog_id)
        expect(dialog).to_be_visible()
        if dialog_id == '#appearanceDialog':
            for mode in ('light', 'dark', 'black'):
                page.locator('#appearanceMode').select_option(mode)
                expect(page.locator('html')).to_have_attribute('data-theme', mode)
                assert page.locator('#page').evaluate(
                    'element => getComputedStyle(element).backgroundColor'
                ) == 'rgb(255, 255, 255)'
                assert page.locator('#page canvas').evaluate(
                    'canvas => [...canvas.getContext("2d").getImageData(0, 0, 1, 1).data]'
                ) == [255, 255, 255, 255]
        elif dialog_id == '#globalSettingsDialog':
            page.locator('#globalStartPage').select_option('/library')
            assert json.loads(page.evaluate(
                'localStorage.getItem("origin.workspace.settings.v1")'
            ))['startPage'] == '/library'
        else:
            expect(page.locator('#globalAccountDetails')).to_contain_text('Local workspace')
        dialog.get_by_role('button', name='Close' if dialog_id == '#globalAccountDialog' else 'Done', exact=True).focus()
        for key in ('ArrowRight', 'Control+f', 'f', '?'):
            page.keyboard.press(key)
        expect(page.locator('#pageNumber')).to_have_value('1')
        expect(page.locator('#searchBar')).to_be_hidden()
        expect(page.locator('#shortcutsDialog')).to_be_hidden()
        expect(page.locator('#fullscreen')).to_have_attribute('aria-pressed', 'true')
        page.keyboard.press('Escape')
        expect(dialog).to_be_hidden()
        expect(page.locator('#fullscreen')).to_have_attribute('aria-pressed', 'true')
    page.evaluate('document.activeElement.blur()')
    page.keyboard.press('ArrowRight')
    expect(page.locator('#pageNumber')).to_have_value('2')
    page.keyboard.press('Escape')
    expect(page.locator('#fullscreen')).to_have_attribute('aria-pressed', 'false')
    page.reload()
    page.wait_for_selector('#fit:not([disabled])')
    expect(page.locator('html')).to_have_attribute('data-theme', 'black')


@pytest.mark.parametrize('reader_server', [{'auth': True}], indirect=True)
def test_reader_can_sign_out_when_paper_notebook_cannot_load(reader_server, reader_page):
    page, expect, base, _ = reader_page
    headers, _ = helpers.login_headers(reader_server[0], 'alice', 'quiet-lantern-river-orbit-47')
    page.context.add_cookies([
        {'name': item.split('=', 1)[0], 'value': item.split('=', 1)[1], 'url': base}
        for item in headers['Cookie'].split('; ')
    ])
    writes = []
    page.on('request', lambda request: writes.append(request.post_data_json)
            if request.method == 'POST' and request.url.endswith('/api/reader/notebook') else None)
    page.route('**/api/reader/notebook?*', lambda route: route.fulfill(
        status=503, content_type='application/json',
        body=json.dumps({'error': 'Paper notes are temporarily unavailable.'}),
    ))
    open_reader(page, base)
    page.locator('#paperNotesTab').click()
    expect(page.locator('#paperNotesState')).to_have_attribute('data-state', 'error')
    expect(page.locator('#paperNotes')).to_be_disabled()
    expect(page.locator('#globalAccount')).to_have_text('alice')
    page.locator('#globalAccount').click()
    expect(page.locator('#globalAccountDialog')).to_be_visible()
    page.locator('#globalSignOut').click()
    page.wait_for_url(base + '/login')
    assert not writes
    assert page.request.get(base + '/api/app-profile').status == 401


@pytest.mark.parametrize('reader_server', [{'auth': True}], indirect=True)
@pytest.mark.parametrize('save_fails', [False, True], ids=['save-before-navigation', 'failed-save-stays-in-reader'])
def test_reader_library_navigation_waits_for_notebook_save(reader_server, reader_page, save_fails):
    page, expect, base, _ = reader_page
    headers, _ = helpers.login_headers(reader_server[0], 'alice', 'quiet-lantern-river-orbit-47')
    page.context.add_cookies([
        {'name': item.split('=', 1)[0], 'value': item.split('=', 1)[1], 'url': base}
        for item in headers['Cookie'].split('; ')
    ])
    open_reader(page, base)
    page.locator('#paperNotesTab').click()
    notebook = page.locator('#paperNotes')
    expect(notebook).to_be_enabled()
    expect(notebook).to_have_value('')
    events, failed_requests = [], []
    page.on('response', lambda response: events.append('saved')
            if response.request.method == 'POST' and response.url.endswith('/api/reader/notebook')
            and response.status == 200 else None)
    page.on('framenavigated', lambda frame: events.append('library')
            if frame == page.main_frame and frame.url == base + '/library' else None)
    if save_fails:
        def unavailable(route):
            failed_requests.append(route.request.method)
            route.fulfill(status=503, content_type='application/json',
                          body=json.dumps({'error': 'Paper notes are temporarily unavailable.'}))
        page.route('**/api/reader/notebook*', unavailable)
    draft = 'Keep this conclusion before returning to my library.'
    notebook.fill(draft)
    page.locator('#globalNavigation').get_by_role('link', name='Library', exact=True).click()
    if save_fails:
        expect(page.locator('#paperNotesState')).to_have_attribute('data-state', 'error')
        assert page.url == base + '/reader?paper_id=P1'
        expect(notebook).to_be_visible()
        expect(notebook).to_have_value(draft)
        expect(page.locator('#paperNotesSave')).to_be_enabled()
        expect(page.locator('#paperNotesExport')).to_be_enabled()
        assert failed_requests == ['POST', 'GET']
        assert events == []
    else:
        page.wait_for_url(base + '/library')
        assert events == ['saved', 'library']
    saved = page.request.get(base + '/api/reader/notebook?paper_id=P1')
    assert saved.status == 200
    assert saved.json()['text'] == ('' if save_fails else draft)
    assert saved.json()['revision'] == (0 if save_fails else 1)
    if not save_fails:
        open_reader(page, base)
        page.locator('#paperNotesTab').click()
        expect(notebook).to_have_value(draft)


@pytest.mark.parametrize('reader_server', [{'auth': True}], indirect=True)
def test_reader_paper_notebook_saves_reopens_and_stays_private(reader_server):
    playwright = pytest.importorskip('playwright.sync_api')
    port, fingerprint, _, _ = reader_server
    base = f'http://127.0.0.1:{port}'
    users = {}
    errors = []
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        for username in ('alice', 'bob'):
            headers, _ = helpers.login_headers(port, username, 'quiet-lantern-river-orbit-47')
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            context.add_cookies([
                {'name': item.split('=', 1)[0], 'value': item.split('=', 1)[1], 'url': base}
                for item in headers['Cookie'].split('; ')
            ])
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            users[username] = (page, headers)
        alice, alice_headers = users['alice']
        bob, _ = users['bob']
        mark = {**MARK, 'note': 'This comment belongs to a highlighted passage.'}
        status, _, _, _ = helpers.http_request(
            port, 'POST', '/api/reader/notes', headers=alice_headers,
            payload={'paper_id': 'P1', 'fingerprint': fingerprint, 'action': 'add', 'mark': mark},
        )
        assert status == 200
        for username, text in [('alice', 'Alice: compare the conclusions with the other study.'),
                               ('bob', 'Bob: review the limitations before citing this paper.')]:
            page, _ = users[username]
            open_reader(page, base)
            playwright.expect(page.locator('#globalAccount')).to_have_text(username)
            playwright.expect(page.locator('.note')).to_have_count(int(username == 'alice'))
            page.locator('#paperNotesTab').click()
            notebook = page.locator('#paperNotes')
            playwright.expect(notebook).to_be_enabled()
            playwright.expect(notebook).to_have_value('')
            notebook.fill(text)
            with page.expect_response(lambda response: response.request.method == 'POST'
                                      and response.url.endswith('/api/reader/notebook')) as saved:
                page.locator('#paperNotesSave').click()
            assert saved.value.status == 200
            assert saved.value.json()['text'] == text
            playwright.expect(page.locator('#paperNotesState')).to_have_text('Saved')
            page.reload()
            page.wait_for_selector('#fit:not([disabled])')
            page.locator('#paperNotesTab').click()
            playwright.expect(notebook).to_have_value(text)
            playwright.expect(page.locator('#paperNotesState')).to_have_attribute('data-state', 'saved')
        alice.reload()
        alice.wait_for_selector('#fit:not([disabled])')
        playwright.expect(alice.locator('.note textarea')).to_have_value(mark['note'])
        alice.locator('#paperNotesTab').click()
        playwright.expect(alice.locator('#paperNotes')).to_have_value(
            'Alice: compare the conclusions with the other study.'
        )
        playwright.expect(bob.locator('#paperNotes')).to_have_value(
            'Bob: review the limitations before citing this paper.'
        )
        screenshot_dir = Path('/private/tmp/originrag-reader-product') if Path('/private/tmp').exists() else reader_server[2] / 'screenshots'
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        alice.screenshot(path=str(screenshot_dir / 'reader-notebook.png'), full_page=True)
        assert not errors
        browser.close()
