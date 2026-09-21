import json
from pathlib import Path
import shutil

import pytest

import test_web_chat as helpers
from test_web_reader import reader_server as reader_server


PAPERS = [
    {'paper_id': 'P1', 'title': 'Synthetic reader paper', 'author': 'Example', 'year': '2026', 'local_pdf': True},
    {'paper_id': 'P2', 'title': 'A second research paper', 'author': 'Rivera', 'year': '2025'},
]


@pytest.fixture
def journey(reader_server, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    port, _, temporary, auth = reader_server
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / 'assets/workspace', temporary / 'assets/workspace', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('editor-build'))
    monkeypatch.setattr(helpers.web_chat, 'library_catalog', lambda: PAPERS)
    headers = helpers.login_headers(port, 'alice', 'quiet-lantern-river-orbit-47')[0] if auth else {}
    base = f'http://127.0.0.1:{port}'

    def api(method, path, payload=None, *, user_headers=None):
        response = helpers.http_request(port, method, path, payload=payload,
                                        headers=headers if user_headers is None else user_headers)
        assert response[0] == 200, response[2]
        return json.loads(response[2])

    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        if auth:
            page.context.add_cookies([{'name': item.split('=', 1)[0], 'value': item.split('=', 1)[1], 'url': base}
                                     for item in headers['Cookie'].split('; ')])
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        yield page, pw.expect, base, api
        browser.close()
        assert not errors


def open_reader(page, base):
    page.goto(base + '/reader?paper_id=P1')
    page.wait_for_selector('#fit:not([disabled])', timeout=20000)
    page.wait_for_selector('#readStatus[aria-busy="false"]')


@pytest.mark.parametrize('reader_server', [False, True], indirect=True)
def test_reader_marks_read_explicitly_and_finished_list_can_undo(reader_server, journey):
    page, expect, base, api = journey
    api('POST', '/api/shelf', {'action': 'add', 'paper_id': 'P1'})
    open_reader(page, base)
    writes = []
    page.on('request', lambda request: writes.append(request.post_data_json)
            if request.method == 'POST' and request.url.endswith('/api/reading') else None)
    page.locator('#workspace').evaluate('el => el.scrollTop = el.scrollHeight')
    expect(page.locator('#pageNumber')).to_have_value('2')
    assert not writes
    assert not api('GET', '/api/reading?paper_id=P1')['is_read']
    page.locator('#readStatus').click()
    expect(page.locator('#readStatusSubmit')).to_be_enabled()
    expect(page.locator('#readStatusRemove')).not_to_be_checked()
    page.locator('#readStatusSubmit').click()
    expect(page.locator('#readStatusDialog')).not_to_be_visible()
    expect(page.locator('#readStatus')).to_have_attribute('data-state', 'read')
    current = api('GET', '/api/reading?paper_id=P1')
    assert current['is_read'] and current['on_shelf'] and current['read_count'] == 1
    assert len(writes) == 1
    if reader_server[3]:
        bob = helpers.login_headers(reader_server[0], 'bob', 'quiet-lantern-river-orbit-47')[0]
        assert api('GET', '/api/reading', user_headers=bob)['items'] == []
        assert not api('GET', '/api/reading?paper_id=P1', user_headers=bob)['is_read']
    page.reload()
    expect(page.locator('#readStatus')).to_have_attribute('data-state', 'read')
    assert api('GET', '/api/reading?paper_id=P1')['read_at'] == current['read_at']
    page.locator('#readHistoryLink').click()
    page.wait_for_url('**/bookshelf?view=read')
    row = page.locator('#readingHistoryList article[data-paper-id="P1"]')
    expect(row).to_contain_text('Synthetic reader paper')
    expect(page.locator('#readingHistoryCount')).to_contain_text('1')
    page.once('dialog', lambda dialog: dialog.accept())
    row.get_by_role('button', name='Mark as unread', exact=True).click()
    expect(row).to_have_count(0)
    assert api('GET', '/api/reading?paper_id=P1') == {
        'paper_id': 'P1', 'is_read': False, 'read_at': None, 'on_shelf': True, 'read_count': 0,
    }


def test_reader_can_remove_finished_paper_from_shelf_without_deleting_pdf_or_notes(journey):
    page, expect, base, api = journey
    api('POST', '/api/shelf', {'action': 'add', 'paper_id': 'P1'})
    open_reader(page, base)
    page.locator('#textLayer span').first.evaluate('el => {const range=document.createRange();range.selectNodeContents(el);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);}')
    page.locator('#highlight').click()
    expect(page.locator('.note')).to_have_count(1)
    page.locator('#readStatus').click()
    expect(page.locator('#readStatusSubmit')).to_be_enabled()
    page.locator('#readStatusRemove').check()
    page.locator('#readStatusSubmit').click()
    expect(page.locator('#readStatus')).to_have_attribute('data-state', 'read')
    expect(page.locator('#readStatusDialog')).not_to_be_visible()
    assert api('GET', '/api/shelf')['items'] == []
    assert api('GET', '/api/reading')['total'] == 1
    page.reload()
    expect(page.locator('.note')).to_have_count(1)
    expect(page.locator('#textLayer')).to_contain_text('Synthetic paper')
    page.locator('#readHistoryLink').click()
    row = page.locator('#readingHistoryList article[data-paper-id="P1"]')
    row.get_by_role('button', name='Save to shelf', exact=True).click()
    expect(row).to_contain_text('On your shelf')
    assert api('GET', '/api/reading?paper_id=P1')['is_read']
    assert len(api('GET', '/api/shelf')['items']) == 1


@pytest.mark.parametrize('width,theme', [(1440, 'dark'), (390, 'light')])
def test_finished_search_pagination_and_empty_states(journey, monkeypatch, reader_server, width, theme):
    page, expect, base, api = journey
    catalogue = [*PAPERS, *({'paper_id': f'P{i}', 'title': f'Finished methods paper {i}', 'author': 'Rivera'} for i in range(3, 30))]
    monkeypatch.setattr(helpers.web_chat, 'library_catalog', lambda: catalogue)
    for paper in catalogue:
        api('POST', '/api/reading', {'action': 'mark_read', 'paper_id': paper['paper_id']})
    page.set_viewport_size({'width': width, 'height': 950})
    page.goto(base + '/bookshelf?view=read')
    page.evaluate('theme => window.originragAppearance.set({mode: theme, background: "plain"})', theme)
    rows = page.locator('#readingHistoryList article[data-paper-id]')
    expect(rows).to_have_count(24)
    expect(page.locator('#readingHistoryCount')).to_contain_text('29')
    page.locator('#readingHistoryNext').click()
    expect(rows).to_have_count(5)
    page.locator('#readingHistoryQuery').fill('Synthetic')
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text('Synthetic reader paper')
    expect(page.locator('#readingHistoryCount')).to_contain_text('29')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(reader_server[2] / f'finished-{theme}-{width}.png'), full_page=True)
    page.locator('#readingHistoryQuery').fill('no such finished paper')
    expect(rows).to_have_count(0)
    page.locator('#readingHistoryQuery').fill('')
    expect(rows).to_have_count(24)


def test_finished_loading_failure_has_retry_and_no_false_empty_history(journey):
    page, expect, base, api = journey
    api('POST', '/api/reading', {'action': 'mark_read', 'paper_id': 'P1'})
    page.route('**/api/reading?*', lambda route: route.fulfill(status=503, body='Unavailable'))
    page.goto(base + '/bookshelf?view=read')
    retry = page.get_by_role('button', name='Retry', exact=True)
    expect(retry).to_be_visible()
    page.unroute('**/api/reading?*')
    retry.click()
    expect(page.locator('#readingHistoryList article[data-paper-id]')).to_have_count(1)
