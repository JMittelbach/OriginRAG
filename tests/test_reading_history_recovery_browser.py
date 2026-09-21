from urllib.parse import parse_qs, urlparse

import pytest

from test_reading_history_browser import journey as journey
from test_web_reader import reader_server as reader_server


def test_finished_search_aborts_stale_request_and_keeps_latest_results(journey):
    page, expect, base, api = journey
    for paper_id in ['P1', 'P2']:
        api('POST', '/api/reading', {'action': 'mark_read', 'paper_id': paper_id})
    page.goto(base + '/bookshelf?view=read')
    rows = page.locator('#readingHistoryList article[data-paper-id]')
    expect(rows).to_have_count(2)
    pending = []

    def search_query(request):
        return parse_qs(urlparse(request.url).query).get('query', [''])[0]

    def delayed_search(route):
        if search_query(route.request) == 'slow search':
            pending.append(route)
        else:
            route.continue_()

    page.route('**/api/reading?*', delayed_search)
    with page.expect_request(lambda request: search_query(request) == 'slow search'):
        page.locator('#readingHistoryQuery').fill('slow search')
    with page.expect_event('requestfailed', predicate=lambda request: search_query(request) == 'slow search'):
        page.locator('#readingHistoryQuery').fill('second')
    expect(rows).to_have_count(1)
    expect(rows).to_have_attribute('data-paper-id', 'P2')
    expect(page.locator('#readingHistoryCount')).to_have_text('2 papers finished')
    expect(page.locator('#readingHistoryFeedback')).to_be_hidden()
    assert pending
    assert parse_qs(urlparse(page.url).query)['query'] == ['second']


@pytest.mark.parametrize('action', ['save_to_shelf', 'mark_unread'])
def test_finished_lost_write_acknowledgement_is_checked_without_reposting(journey, action):
    page, expect, base, api = journey
    api('POST', '/api/reading', {'action': 'mark_read', 'paper_id': 'P1'})
    if action == 'mark_unread':
        api('POST', '/api/shelf', {'action': 'add', 'paper_id': 'P1'})
    page.goto(base + '/bookshelf?view=read')
    row = page.locator('#readingHistoryList article[data-paper-id="P1"]')
    expect(row).to_be_visible()
    writes, checked = [], []
    path = '/api/shelf' if action == 'save_to_shelf' else '/api/reading'

    def lose_acknowledgement(route):
        writes.append(route.request.post_data_json)
        response = route.fetch()
        assert response.status == 200
        route.abort('failed')

    page.route('**' + path, lose_acknowledgement)
    page.on('request', lambda request: checked.append(request.url)
            if request.method == 'GET' and '/api/reading?paper_id=P1' in request.url else None)
    if action == 'mark_unread':
        page.once('dialog', lambda dialog: dialog.accept())
    row.get_by_role('button', name='Save to shelf' if action == 'save_to_shelf' else 'Mark as unread', exact=True).click()
    if action == 'save_to_shelf':
        expect(row).to_contain_text('On your shelf')
        expect(page.locator('#notice')).to_have_text('Saved to your bookshelf.')
    else:
        expect(row).to_have_count(0)
        expect(page.locator('#readingHistoryCount')).to_have_text('0 papers finished')
    assert len(writes) == 1
    assert len(checked) == 1
    state = api('GET', '/api/reading?paper_id=P1')
    assert state['on_shelf']
    assert state['is_read'] is (action == 'save_to_shelf')
    expect(page.locator('#readingHistoryActionStatus')).to_be_hidden()


def test_finished_unconfirmed_write_keeps_read_only_status_retry(journey):
    page, expect, base, api = journey
    api('POST', '/api/reading', {'action': 'mark_read', 'paper_id': 'P1'})
    page.goto(base + '/bookshelf?view=read')
    row = page.locator('#readingHistoryList article[data-paper-id="P1"]')
    expect(row).to_be_visible()
    writes = []

    def lose_acknowledgement(route):
        writes.append(route.request.post_data_json)
        assert route.fetch().status == 200
        route.abort('failed')

    page.route('**/api/shelf', lose_acknowledgement)
    page.route('**/api/reading?paper_id=P1', lambda route: route.fulfill(status=503, content_type='text/html', body='Temporarily unavailable'))
    row.get_by_role('button', name='Save to shelf', exact=True).click()
    check = page.get_by_role('button', name='Check status', exact=True)
    expect(check).to_be_visible()
    expect(row).to_be_visible()
    expect(row.get_by_role('button', name='Mark as unread', exact=True)).to_be_disabled()
    expect(page.locator('#readingHistoryCount')).to_have_text('1 paper finished')
    page.unroute('**/api/reading?paper_id=P1')
    check.click()
    expect(page.locator('#readingHistoryActionStatus')).to_be_hidden()
    expect(row.get_by_role('button', name='Mark as unread', exact=True)).to_be_enabled()
    expect(row).to_contain_text('On your shelf')
    assert len(writes) == 1
    state = api('GET', '/api/reading?paper_id=P1')
    assert state['is_read'] and state['on_shelf']
