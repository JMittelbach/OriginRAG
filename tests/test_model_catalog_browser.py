import json
import time

import pytest

import test_web_chat as http
from test_web_models import models_server as models_server, get, post


def test_search_endpoint_is_authenticated_and_downloads_keep_the_same_permissions(models_server, monkeypatch):
    port, accounts, manager = models_server
    calls = []
    monkeypatch.setattr(manager, 'search', lambda query: calls.append(query) or [])
    headers = accounts.get('reader', {})
    response = http.http_request(port, 'GET', '/api/models?search=qwen3.5%3A9b', headers=headers)
    assert response[0] == 200 and calls == ['qwen3.5:9b']
    assert json.loads(response[2])['search_models'] == []
    if accounts:
        assert http.http_request(port, 'GET', '/api/models?search=qwen')[0] == 401
        assert calls == ['qwen3.5:9b']
        assert post(port, headers, {'action': 'download', 'provider': 'ollama',
                                  'model': 'qwen3.5:9b', 'confirmed': True}) == 403
    else:
        assert http.http_request(port, 'GET', '/api/models?search=qwen',
                                 headers={'Origin': 'https://evil.test'})[0] == 403


def test_simple_model_cards_search_installed_filter_and_no_automatic_mutations(models_server, monkeypatch):
    playwright = pytest.importorskip('playwright.sync_api')
    port, accounts, manager = models_server
    monkeypatch.setenv('ORIGINRAG_OLLAMA_ANSWER_MODEL', 'missing:qwen')
    calls, writes = [], []
    def search(query):
        calls.append(query)
        if query == 'offline':
            raise OSError('catalogue offline')
        row = {'provider': 'ollama', 'model': 'new:tiny', 'name': 'new:tiny',
               'bytes': 4e9, 'installed': False, 'loaded': False, 'downloadable': True,
               'catalog_digest': 'verified'}
        manager.discovered[row['model']] = (time.monotonic(), row)
        return [row]
    monkeypatch.setattr(manager, 'search', search)
    monkeypatch.setattr(manager, 'change', lambda payload: writes.append(payload))
    base = f'http://127.0.0.1:{port}'
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        context = browser.new_context(viewport={'width': 1200, 'height': 900})
        if accounts:
            context.add_cookies([{'name': part.split('=', 1)[0], 'value': part.split('=', 1)[1], 'url': base}
                                for part in accounts['admin']['Cookie'].split('; ')])
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base + '/library')
        page.locator('#globalSettings').click()
        page.locator('#globalModels').click()
        playwright.expect(page.locator('#modelSelection')).to_contain_text('No model ready')
        assert 'Active:' not in page.locator('#modelSelection').inner_text()
        assert page.locator('.model-badge-selected', has_text='Selected').count() == 0
        playwright.expect(page.locator('#modelHardware')).to_contain_text('This host')
        page.locator('#modelFilter').select_option('installed')
        page.locator('#modelSearch').fill('test')
        playwright.expect(page.locator('#modelList')).to_contain_text('test:tiny')
        page.wait_for_timeout(650)
        assert calls == []
        assert page.locator('.model-details').get_attribute('open') is None
        page.locator('#modelFilter').select_option('search')
        page.locator('#modelSearch').fill('new:tiny')
        playwright.expect(page.locator('#modelList')).to_contain_text('new:tiny')
        playwright.expect(page.locator('#modelList')).to_contain_text('Not installed')
        assert calls == ['new:tiny'] and writes == []
        page.once('dialog', lambda dialog: dialog.dismiss())
        page.get_by_role('button', name='Download', exact=True).click()
        assert writes == []
        page.locator('#modelSearch').fill('offline')
        playwright.expect(page.locator('#modelError')).to_contain_text('catalogue offline')
        page.locator('#modelFilter').select_option('installed')
        page.locator('#modelSearch').fill('')
        playwright.expect(page.locator('#modelList')).to_contain_text('test:tiny')
        page.locator('#modelClose').click()
        page.locator('#globalSettings').click()
        page.locator('#globalModels').click()
        playwright.expect(page.locator('#modelList')).to_contain_text('test:tiny')
        assert writes == [] and not errors
        browser.close()


def test_stale_catalogue_responses_cannot_replace_newer_search(models_server):
    playwright = pytest.importorskip('playwright.sync_api')
    port, accounts, manager = models_server
    base = f'http://127.0.0.1:{port}'
    data = get(port, accounts.get('admin'))[1]
    def result(name):
        return {**data, 'search_models': [name], 'models': [
            {'provider': 'ollama', 'model': name, 'name': name, 'bytes': 1e9,
             'installed': False, 'loaded': False, 'downloadable': True}]}
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        context = browser.new_context()
        if accounts:
            context.add_cookies([{'name': part.split('=', 1)[0], 'value': part.split('=', 1)[1], 'url': base}
                                for part in accounts['admin']['Cookie'].split('; ')])
        page = context.new_page()
        page.goto(base + '/library')
        page.locator('#globalSettings').click()
        page.locator('#globalModels').click()
        playwright.expect(page.locator('#modelList')).to_contain_text('test:tiny')
        delayed = []
        page.route('**/api/models?search=old', lambda route: delayed.append(route))
        page.route('**/api/models?search=new', lambda route: route.fulfill(json=result('new:tiny')))
        page.locator('#modelSearch').fill('old')
        page.wait_for_timeout(750)
        assert delayed
        page.locator('#modelSearch').fill('new')
        playwright.expect(page.locator('#modelList')).to_contain_text('new:tiny')
        delayed[0].fulfill(json=result('old:tiny'))
        page.wait_for_timeout(100)
        assert 'old:tiny' not in page.locator('#modelList').inner_text()
        browser.close()
