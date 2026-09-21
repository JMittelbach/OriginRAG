import json

import pytest

from test_web_reader import reader_server as reader_server


def reading_page(browser, port, state, handler=None, viewport=None):
    page = browser.new_page(viewport=viewport or {'width': 1280, 'height': 900})
    events = {'posts': [], 'gets': 0}

    def respond(route):
        if route.request.method == 'POST':
            payload = route.request.post_data_json
            events['posts'].append(payload)
            if handler and handler(route, payload, state, events):
                return
            state['is_read'] = payload['action'] == 'mark_read'
            state['read_at'] = 1789956000000 if state['is_read'] else None
            state['read_count'] = int(state['is_read'])
            if payload.get('remove_from_shelf'):
                state['on_shelf'] = False
        else:
            events['gets'] += 1
        route.fulfill(status=200, content_type='application/json', body=json.dumps(state))

    page.route('**/api/reading**', respond)
    page.route('**/assets/reader/reader.mjs', lambda route: route.fulfill(
        content_type='text/javascript', body='''
import {readingStatus} from '/assets/reader/reading-status.mjs';
window.readingMessages=[];
window.readingStatusTest=readingStatus({paperId:'P1',status:message=>readingMessages.push(message)});
void readingStatusTest.init();
'''))
    page.goto(f'http://127.0.0.1:{port}/reader?paper_id=P1')
    page.wait_for_function('document.querySelector("#readStatus").getAttribute("aria-busy")==="false"')
    return page, events


def test_manual_read_confirmation_preserves_shelf_and_unread_never_adds(reader_server):
    playwright = pytest.importorskip('playwright.sync_api')
    state = {'paper_id': 'P1', 'is_read': False, 'read_at': None, 'on_shelf': True, 'read_count': 0}
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page, events = reading_page(browser, reader_server[0], state)
        assert events['posts'] == []
        assert page.locator('#readHistoryLink').get_attribute('href') == '/bookshelf?view=read'
        page.locator('#readStatus').click()
        playwright.expect(page.locator('#readStatusSubmit')).to_be_enabled()
        playwright.expect(page.locator('#readStatusRemove')).not_to_be_checked()
        page.locator('#readStatusSubmit').click()
        playwright.expect(page.locator('#readStatusDialog')).not_to_be_visible()
        assert events['posts'] == [{'action': 'mark_read', 'paper_id': 'P1', 'remove_from_shelf': False}]
        assert state['on_shelf'] is True
        assert page.evaluate('readingMessages.at(-1)') == 'Marked as read. 1 paper finished.'
        playwright.expect(page.locator('#readStatus')).to_contain_text('Read ·')
        state['on_shelf'] = False
        page.locator('#readStatus').click()
        playwright.expect(page.locator('#readStatusSubmit')).to_be_enabled()
        playwright.expect(page.locator('#readStatusRemoveRow')).not_to_be_visible()
        playwright.expect(page.locator('#readStatusSubmit')).to_have_text('Mark as unread')
        page.locator('#readStatusSubmit').click()
        playwright.expect(page.locator('#readStatusDialog')).not_to_be_visible()
        assert state['is_read'] is False and state['on_shelf'] is False
        assert events['posts'][-1] == {'action': 'mark_unread', 'paper_id': 'P1', 'remove_from_shelf': False}
        browser.close()


def test_lost_acknowledgement_is_reconciled_without_repeating_write(reader_server):
    playwright = pytest.importorskip('playwright.sync_api')
    state = {'paper_id': 'P1', 'is_read': False, 'read_at': None, 'on_shelf': True, 'read_count': 0}

    def lost_ack(route, payload, current, events):
        current.update(is_read=True, read_at=1789956000000, on_shelf=False, read_count=1)
        route.fulfill(status=503, content_type='application/json', body='{"error":"Response lost after saving."}')
        return True

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page, events = reading_page(browser, reader_server[0], state, lost_ack)
        page.locator('#readStatus').click()
        playwright.expect(page.locator('#readStatusSubmit')).to_be_enabled()
        page.locator('#readStatusRemove').check()
        page.locator('#readStatusSubmit').click()
        playwright.expect(page.locator('#readStatusDialog')).not_to_be_visible()
        assert len(events['posts']) == 1 and events['posts'][0]['remove_from_shelf'] is True
        assert events['gets'] >= 3
        assert page.evaluate('readingMessages.at(-1)') == 'Marked as read. 1 paper finished.'
        browser.close()


def test_shelf_removal_is_part_of_confirmation_and_retry_rechecks_state(reader_server):
    playwright = pytest.importorskip('playwright.sync_api')
    state = {'paper_id': 'P1', 'is_read': False, 'read_at': None, 'on_shelf': True, 'read_count': 0}

    def partial(route, payload, current, events):
        if len(events['posts']) != 1:
            return False
        current.update(is_read=True, read_at=1789956000000, read_count=1)
        route.fulfill(status=200, content_type='application/json', body=json.dumps(current))
        return True

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page, events = reading_page(browser, reader_server[0], state, partial)
        page.locator('#readStatus').click()
        playwright.expect(page.locator('#readStatusSubmit')).to_be_enabled()
        page.locator('#readStatusRemove').check()
        page.locator('#readStatusSubmit').click()
        playwright.expect(page.locator('#readStatusMessage')).to_contain_text('still on your bookshelf')
        playwright.expect(page.locator('#readStatusDialog')).to_be_visible()
        assert page.evaluate('readingMessages.length') == 0
        playwright.expect(page.locator('#readStatusSubmit')).to_be_disabled()
        page.locator('#readStatusRetry').click()
        playwright.expect(page.locator('#readStatusSubmit')).to_be_enabled()
        before = events['gets']
        page.locator('#readStatusSubmit').click()
        playwright.expect(page.locator('#readStatusDialog')).not_to_be_visible()
        assert events['gets'] > before
        assert events['posts'][1]['remove_from_shelf'] is True
        assert state['on_shelf'] is False
        browser.close()
