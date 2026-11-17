import os

import pytest

from test_web_models import models_server as models_server, get


@pytest.mark.parametrize(('width', 'theme'), [(1280, 'dark'), (390, 'dark'), (390, 'light')])
def test_quiet_model_overview_keeps_details_optional_and_never_mutates(models_server, monkeypatch, width, theme):
    pw = pytest.importorskip('playwright.sync_api')
    port, accounts, manager = models_server
    base = f'http://127.0.0.1:{port}'
    data = get(port, accounts.get('admin'))[1]
    selection = {'provider': 'ollama', 'model': 'qwen3.5:9b', 'mode': 'fast'}
    fit = {'status': 'fits', 'label': 'Likely fits now', 'device': 'Apple GPU · shared memory',
           'estimated_min': 9e9, 'estimated_max': 14e9}
    data.update(effective_selection=selection, configured_selection=selection,
                default={**selection, 'model': 'qwen3:30b'},
                hardware={'ram_total': 48 * 1024 ** 3, 'ram_available': 25 * 1024 ** 3,
                          'unified_memory': True, 'gpus': [{'name': 'Apple M5 Pro'}]},
                job={'status': 'done', 'action': 'select', 'message': 'Done'},
                models=[
                    {**selection, 'name': 'qwen3.5:9b', 'installed': True, 'selected': True,
                     'recommended': True, 'bytes': 6.6e9, 'fit': fit,
                     'measurement': {'tokens_per_second': 12.4, 'metric': 'decode', 'measured_at': 1}},
                    {'provider': 'ollama', 'model': 'qwen2.5-coder:7b', 'name': 'qwen2.5-coder:7b',
                     'installed': False, 'recommended': True, 'downloadable': True, 'bytes': 4.7e9, 'fit': fit},
                    {'provider': 'ollama', 'model': 'qwen3:30b', 'name': 'qwen3:30b', 'default': True,
                     'installed': False, 'downloadable': True, 'bytes': 20e9,
                     'fit': {**fit, 'status': 'tight', 'label': 'Fits capacity · check free memory'}},
                ])
    writes = []
    monkeypatch.setattr(manager, 'change', lambda payload: writes.append(payload))
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        context = browser.new_context(viewport={'width': width, 'height': 900})
        if accounts:
            context.add_cookies([{'name': part.split('=', 1)[0], 'value': part.split('=', 1)[1], 'url': base}
                                for part in accounts['admin']['Cookie'].split('; ')])
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/models', lambda route: route.fulfill(json=data) if route.request.method == 'GET' else route.continue_())
        page.goto(base + '/library')
        page.evaluate('(theme) => document.documentElement.dataset.theme = theme', theme)
        page.locator('#globalSettings').click()
        page.locator('#globalModels').click()
        panel = page.locator('#modelDialog')
        pw.expect(page.locator('#modelSelection')).to_have_text('Using qwen3.5:9b')
        pw.expect(page.locator('.model-row')).to_have_count(2)
        pw.expect(page.locator('#modelJob')).to_be_hidden()
        visible = panel.inner_text()
        assert 'Configured default' not in visible and 'This host' not in visible
        assert 'done: Done' not in visible and 'Runtime' not in visible
        assert 'qwen3:30b' not in visible
        assert len(visible.split()) < 55
        assert panel.bounding_box()['height'] < 520
        assert not panel.evaluate('element => element.scrollWidth > element.clientWidth')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if capture := os.environ.get('ORIGINRAG_MODELS_CAPTURE'):
            panel.screenshot(path=f'{capture}-{width}-{theme}-{bool(accounts)}.png')
        page.once('dialog', lambda dialog: dialog.dismiss())
        page.get_by_role('button', name='Download', exact=True).click()
        assert not writes
        page.locator('.model-details > summary').first.click()
        pw.expect(page.locator('.model-details').first).to_contain_text('12.4 tokens/s')
        page.locator('.model-advanced > summary').click()
        pw.expect(page.locator('#modelHardware')).to_be_visible()
        pw.expect(page.locator('#modelConfigured')).to_have_text('Configured default: qwen3:30b')
        page.locator('#modelClose').click()
        pw.expect(panel).to_be_hidden()
        assert not writes and not errors
        browser.close()
