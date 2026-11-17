import json

import pytest

from originrag import model_runtime as runtime
from test_model_runtime import manager as manager, wait_job
from test_web_models import models_server as models_server, get, post


def test_snapshot_names_configured_default_active_and_protected_models(manager, monkeypatch):
    monkeypatch.setenv('ORIGINRAG_OLLAMA_ANSWER_MODEL', 'test:tiny')
    result = manager.snapshot(True)
    assert result['effective_selection']['model'] == result['default']['model'] == 'test:tiny'
    row = next(item for item in result['models'] if item['model'] == 'test:tiny')
    assert row['selected'] and row['default'] and not row['deletable']
    with pytest.raises(ValueError, match='cannot be deleted'):
        manager.change({'action': 'delete', 'provider': 'ollama', 'model': 'test:tiny', 'confirmed': True})
    assert manager.job is None


def test_delete_needs_confirmation_and_does_not_change_selection(manager, monkeypatch):
    monkeypatch.setenv('ORIGINRAG_OLLAMA_ANSWER_MODEL', 'configured:qwen')
    payload = {'action': 'delete', 'provider': 'ollama', 'model': 'test:tiny'}
    with pytest.raises(ValueError, match='Confirm'):
        manager.change(payload)
    calls = []
    monkeypatch.setattr(manager, 'delete_model', lambda *args: calls.append(args))
    manager.change({**payload, 'confirmed': True})
    assert wait_job(manager)['status'] == 'done'
    assert calls == [('ollama', 'test:tiny')]
    assert manager.selected() is None
    runtime.atomic_json(manager.state / 'selection.json', {'provider': 'ollama', 'model': 'test:tiny', 'mode': 'fast'})
    with pytest.raises(ValueError, match='cannot be deleted'):
        manager.change({**payload, 'confirmed': True})
    assert calls == [('ollama', 'test:tiny')]


def test_ollama_delete_uses_exact_model_and_mapped_endpoint(manager, monkeypatch):
    seen = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return b''
    def call(request, timeout):
        seen.append((request.full_url, request.get_method(), json.loads(request.data), timeout))
        return Response()
    monkeypatch.setattr(runtime.urllib.request, 'urlopen', call)
    monkeypatch.setattr(manager, 'progress', lambda *args: None)
    manager.ollama_model_urls = {'test:tiny': 'http://127.0.0.1:11435'}
    manager.delete_model('ollama', 'test:tiny')
    assert seen == [('http://127.0.0.1:11435/api/delete', 'DELETE', {'model': 'test:tiny'}, 30)]


def test_colibri_deletion_cannot_follow_external_symlinks(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(manager, 'progress', lambda *args: None)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'keep').write_text('important', encoding='utf-8')
    manager.state.mkdir(parents=True)
    (manager.state / runtime.MODEL_ID).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='app-managed'):
        manager.delete_model('colibri', runtime.MODEL_ID)
    assert (outside / 'keep').read_text() == 'important'
    with pytest.raises(ValueError):
        manager.delete_model('colibri', '../outside')


def test_deletion_permissions_are_separate_from_download_permissions(models_server, monkeypatch):
    port, accounts, manager = models_server
    calls = []
    monkeypatch.setattr(manager, 'change', lambda payload: calls.append(payload))
    payload = {'action': 'delete', 'provider': 'ollama', 'model': 'test:tiny', 'confirmed': True}
    if accounts:
        monkeypatch.setenv('ORIGINRAG_MODEL_DOWNLOAD_USERS', 'reader')
        assert post(port, accounts['reader'], payload) == 403
        assert post(port, {'Cookie': accounts['admin']['Cookie']}, payload) == 403
        assert post(port, accounts['admin'], payload) == 202
    else:
        assert post(port, {}, payload) == 403
        token = get(port)[1]['local_token']
        assert post(port, {'X-OriginRAG-Local-Token': token}, payload) == 202
    assert calls == [payload]


def test_chat_exposes_models_directly_and_delete_requires_confirmation(models_server, monkeypatch):
    playwright = pytest.importorskip('playwright.sync_api')
    port, accounts, manager = models_server
    base, calls = f'http://127.0.0.1:{port}', []
    monkeypatch.setattr(manager, 'change', lambda payload: calls.append(payload))
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        context = browser.new_context()
        if accounts:
            context.add_cookies([
                {'name': item.split('=', 1)[0], 'value': item.split('=', 1)[1], 'url': base}
                for item in accounts['admin']['Cookie'].split('; ')
            ])
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base + '/chat')
        page.locator('#guideSkip').click()
        page.locator('#chatModels').click()
        playwright.expect(page.locator('#modelDialog')).to_be_visible()
        playwright.expect(page.locator('#modelSelection')).to_have_text('No model ready')
        playwright.expect(page.locator('#modelConfigured')).to_contain_text('Configured default:')
        page.locator('#modelFilter').select_option('installed')
        page.locator('#modelSearch').fill('test:tiny')
        page.locator('.model-details > summary').click()
        button = page.get_by_role('button', name='Delete download', exact=True)
        playwright.expect(button).to_be_enabled()
        page.once('dialog', lambda dialog: dialog.dismiss())
        button.click()
        assert not calls
        page.once('dialog', lambda dialog: dialog.accept())
        button.click()
        playwright.expect(button).to_be_enabled()
        assert calls == [{'action': 'delete', 'provider': 'ollama', 'model': 'test:tiny', 'mode': 'fast', 'confirmed': True}]
        assert not errors
        browser.close()
