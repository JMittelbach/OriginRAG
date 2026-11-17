import json
import time

import pytest

from originrag import model_catalog as catalog, model_hardware as hardware, model_runtime as runtime
from test_model_runtime import manager as manager, wait_job


def machine(ram=48, free=30, unified=False, vram=None, gpu_free=None):
    return {'ram_total': ram * hardware.GIB, 'ram_available': free * hardware.GIB,
            'unified_memory': unified, 'gpus': [] if vram is None else [
                {'name': 'Test GPU', 'total': vram * hardware.GIB,
                 'available': (gpu_free if gpu_free is not None else vram) * hardware.GIB, 'shared': False}]}


@pytest.mark.parametrize('host,size,status,device', [
    (machine(unified=True), 7e9, 'fits', 'Apple GPU'),
    (machine(unified=True, free=5), 7e9, 'tight', 'Apple GPU'),
    (machine(unified=True), 60e9, 'too_large', None),
    (machine(ram=128, free=90, vram=48), 22e9, 'fits', 'GPU'),
    (machine(ram=128, free=90, vram=24, gpu_free=2), 22e9, 'fits', 'RAM'),
    (machine(ram=8, free=7), 20e9, 'too_large', None),
    (machine(ram=16, free=14, vram=24), 22e9, 'hybrid', 'GPU + RAM'),
    ({}, 7e9, 'unknown', None),
])
def test_memory_fit_is_conservative_and_does_not_add_ram_to_vram(host, size, status, device):
    fit = hardware.estimate_fit({'bytes': size, 'provider': 'ollama'}, host)
    assert fit['status'] == status
    assert device is None or fit['device'].startswith(device)
    assert fit['estimated_max'] > fit['estimated_min'] > size


def test_remote_streaming_and_loaded_models_do_not_make_false_fit_claims():
    model = {'provider': 'ollama', 'bytes': 20e9}
    assert hardware.estimate_fit(model, machine(), local_runtime=False)['status'] == 'unknown'
    assert hardware.estimate_fit({**model, 'provider': 'colibri'}, machine())['estimated_max'] is None
    assert hardware.estimate_fit({**model, 'loaded': True, 'device': 'GPU'}, machine(free=0))['status'] == 'loaded'


def test_apple_probe_only_exposes_hardware_not_monitor_identifiers(monkeypatch):
    monkeypatch.setattr(hardware.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(hardware.platform, 'machine', lambda: 'arm64')
    def command(args):
        if args[0] == 'sysctl':
            return str(48 * hardware.GIB)
        if args[0] == 'vm_stat':
            return 'Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 100.\nPages inactive: 200.\nPages speculative: 30.'
        if args[0] == 'nvidia-smi':
            raise OSError()
        return json.dumps({'SPDisplaysDataType': [{'sppci_model': 'Apple Test GPU', 'display_serial': 'PRIVATE'}]})
    monkeypatch.setattr(hardware, '_command', command)
    data = hardware.detect_hardware()
    assert data['ram_total'] == 48 * hardware.GIB
    assert data['ram_available'] == 330 * 16384
    assert data['unified_memory'] and data['gpus'][0]['total'] is None
    assert 'PRIVATE' not in json.dumps(data)


def test_hardware_probe_failure_is_explicit_unknown(monkeypatch):
    monkeypatch.setattr(hardware.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(hardware, '_command', lambda args: (_ for _ in ()).throw(OSError()))
    assert hardware.detect_hardware()['ram_total'] is None


def test_hardware_cache_avoids_repeated_probes(monkeypatch):
    monkeypatch.setattr(hardware, '_HARDWARE_CACHE', None)
    calls = []
    monkeypatch.setattr(hardware, 'detect_hardware', lambda: calls.append(1) or machine())
    assert hardware.hardware_snapshot() == hardware.hardware_snapshot()
    assert calls == [1]


def manifest(size=7_000_000_000):
    return json.dumps({'layers': [{'mediaType': 'application/vnd.ollama.image.model', 'size': size}]}).encode()


def test_catalogue_search_verifies_manifests_caches_and_never_reads_weights(monkeypatch):
    monkeypatch.setattr(catalog, '_CACHE', {})
    calls = []
    def read(url):
        calls.append(url)
        if '/search?' in url:
            return b'<a href="/library/qwen3.5">Qwen</a><a href="/library/qwen3.5">duplicate</a><a href="https://evil.test/x">ignore</a>'
        return manifest()
    monkeypatch.setattr(catalog, '_read', read)
    rows = catalog.search_models('qwen model')
    assert rows[0]['model'] == 'qwen3.5:latest'
    assert rows[0]['bytes'] == 7_000_000_000
    assert rows[0]['catalog_verified']
    assert len(calls) == 2 and not any('/blobs/' in url for url in calls)
    assert catalog.search_models('qwen model') == rows and len(calls) == 2


@pytest.mark.parametrize('name', ['../secret', 'http://localhost/model', 'x:tag/../a', '/x', 'a:b:c', 'qwen:cloud-cloud', 'x\n'])
def test_invalid_or_cloud_names_are_not_network_requests(monkeypatch, name):
    monkeypatch.setattr(catalog, '_read', lambda url: pytest.fail('must not contact a URL'))
    with pytest.raises(ValueError):
        catalog.model_metadata(name)


@pytest.mark.parametrize('layers', [[], [{'mediaType': 'cloud', 'size': 100}],
                                    [{'mediaType': 'application/vnd.ollama.image.model', 'size': -1}],
                                    [{'mediaType': 'application/vnd.ollama.image.model', 'size': 'bad'}]])
def test_nonlocal_or_invalid_manifests_are_not_downloadable(monkeypatch, layers):
    monkeypatch.setattr(catalog, '_read', lambda url: json.dumps({'layers': layers}).encode())
    with pytest.raises(ValueError):
        catalog.model_metadata('test:local')


def test_public_catalogue_never_follows_redirects_or_arbitrary_urls():
    with pytest.raises(ValueError):
        catalog._read('http://127.0.0.1:8765/api/models')
    with pytest.raises(ValueError):
        catalog._NoRedirect().redirect_request(None, None, 302, '', {}, 'http://127.0.0.1/')


def test_snapshot_missing_default_is_not_selected_or_active(manager, monkeypatch):
    monkeypatch.setenv('ORIGINRAG_OLLAMA_ANSWER_MODEL', 'absent:qwen')
    result = manager.snapshot(True)
    assert result['effective_selection'] is None and result['selection_status'] == 'missing'
    assert result['configured_selection']['model'] == 'absent:qwen'
    assert not any(row['selected'] for row in result['models'])


def test_recommendations_use_free_memory_and_do_not_switch_models(manager, monkeypatch):
    monkeypatch.setattr(hardware, 'hardware_snapshot', lambda: machine(free=20, unified=True))
    monkeypatch.setattr(manager, 'inventory', lambda **kwargs: ([
        {'model': model, 'provider': 'ollama', 'name': model, 'bytes': size,
         'installed': False, 'loaded': False, 'downloadable': True}
        for model, size in runtime.OLLAMA_DOWNLOADS.items()], None))
    rows = manager.snapshot(True)['models']
    recommended = {row['model'] for row in rows if row.get('recommended')}
    assert recommended == {'qwen3.5:9b', 'qwen2.5-coder:7b'}
    assert manager.selected() is None and manager.job is None


def test_loaded_placement_uses_runtime_measurement_not_gpu_presence():
    assert runtime.ModelManager.loaded_details({'size': 100, 'size_vram': 100})['device'] == 'GPU'
    assert runtime.ModelManager.loaded_details({'size': 100, 'size_vram': 40})['device'] == 'GPU + RAM'
    assert runtime.ModelManager.loaded_details({'size': 100, 'size_vram': 0})['device'] == 'CPU / RAM'
    assert 'unknown' in runtime.ModelManager.loaded_details({'size': 100})['device']


def test_recommendations_prefer_gpu_fit_over_a_larger_cpu_only_candidate(manager, monkeypatch):
    monkeypatch.setattr(hardware, 'hardware_snapshot', lambda: machine(ram=128, free=90, vram=16))
    monkeypatch.setattr(manager, 'inventory', lambda **kwargs: ([
        {'model': model, 'provider': 'ollama', 'name': model, 'bytes': size,
         'installed': False, 'loaded': False, 'downloadable': True}
        for model, size in runtime.OLLAMA_DOWNLOADS.items()], None))
    recommended = {row['model'] for row in manager.snapshot(True)['models'] if row.get('recommended')}
    assert recommended == {'qwen3.5:9b', 'qwen2.5-coder:7b'}


def test_running_status_outage_does_not_erase_installed_models(tmp_path, monkeypatch):
    manager = runtime.ModelManager(tmp_path)
    def get(url):
        if url.endswith('/api/ps'):
            raise OSError('status unavailable')
        return {'models': [{'name': 'test:tiny', 'size': 20}]}
    monkeypatch.setattr(runtime, 'http_json', get)
    rows, error = manager.inventory()
    row = next(row for row in rows if row['model'] == 'test:tiny')
    assert error and row['installed'] and not row['loaded_verified']
    manager.close()


def test_offline_runtime_does_not_claim_custom_default_is_absent(tmp_path, monkeypatch):
    manager = runtime.ModelManager(tmp_path)
    monkeypatch.setenv('ORIGINRAG_OLLAMA_ANSWER_MODEL', 'custom-default:local')
    monkeypatch.setattr(runtime, 'http_json', lambda *args: (_ for _ in ()).throw(OSError()))
    view = manager.snapshot(True)
    assert view['selection_status'] == 'unavailable'
    row = next(row for row in view['models'] if row['model'] == 'custom-default:local')
    assert row['runtime_reachable'] is False and not row['selected']
    manager.close()


def test_discovered_download_requires_verified_name_digest_and_confirmation(manager, monkeypatch):
    row = {'model': 'new-model:small', 'provider': 'ollama', 'name': 'new-model:small',
           'bytes': 7_000_000_000, 'catalog_digest': 'verified', 'installed': False,
           'loaded': False, 'downloadable': True}
    monkeypatch.setattr(catalog, 'search_models', lambda query: [row])
    monkeypatch.setattr(catalog, 'model_metadata', lambda name: row)
    manager.search('new-model:small')
    calls = []
    monkeypatch.setattr(manager, 'download_ollama', lambda model: calls.append(model))
    payload = {'action': 'download', 'provider': 'ollama', 'model': row['model'], 'catalog_digest': 'verified'}
    with pytest.raises(ValueError, match='Confirm'):
        manager.change(payload)
    with pytest.raises(ValueError, match='Search'):
        manager.change({**payload, 'catalog_digest': 'wrong', 'confirmed': True})
    manager.change({**payload, 'confirmed': True})
    assert wait_job(manager)['status'] == 'done' and calls == [row['model']]
    assert manager.selected() is None
    manager.discovered[row['model']] = (time.monotonic() - 1801, row)
    with pytest.raises(ValueError, match='Search'):
        manager.change({**payload, 'confirmed': True})


def test_catalogue_size_changes_require_new_confirmation(manager, monkeypatch):
    manager.discovered['new:small'] = (time.monotonic(), {'catalog_digest': 'old'})
    monkeypatch.setattr(catalog, '_CACHE', {'new:small': (time.monotonic(), [{'model': 'new:small'}])})
    monkeypatch.setattr(catalog, 'model_metadata', lambda name: {'catalog_digest': 'new'})
    with pytest.raises(ValueError, match='changed'):
        manager.change({'action': 'download', 'provider': 'ollama', 'model': 'new:small',
                        'catalog_digest': 'old', 'confirmed': True})
    assert manager.job is None
    assert not catalog._CACHE and not manager.discovered
