import json
from pathlib import Path
import shutil

import pytest

from test_app_profiles import profile_server as profile_server
from test_writing import workspace_server as workspace_server


def test_profile_navigation_and_shared_login(profile_server):
    playwright = pytest.importorskip('playwright.sync_api')
    profile, port, headers, _, _, tmp = profile_server
    source = Path(__file__).resolve().parents[1]
    shutil.copytree(source / 'assets/workspace', tmp / 'assets/workspace', ignore=shutil.ignore_patterns('editor-build'))
    base = f'http://127.0.0.1:{port}'
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        cookies = [dict(zip(('name', 'value'), item.split('=', 1)), url=base) for item in headers['Cookie'].split('; ')]
        context.add_cookies(cookies)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base + '/workspace')
        page.wait_for_url(base + ('/write' if profile == 'write' else '/chat'))
        if profile != 'write':
            page.locator('#guideSkip').click()
        header = page.locator('#globalNavigation' if profile == 'full' else '#applicationHeader')
        assert page.locator('#globalNavigation').count() == (1 if profile == 'full' else 0)
        assert header.locator('nav a').count() == (4 if profile == 'full' else 2)
        if profile == 'write':
            playwright.expect(header.locator('.origin-brand')).to_have_text('Quill')
            assert header.get_by_role('link', name='OriginRAG', exact=True).count() == 0
        elif profile == 'rag':
            assert header.get_by_role('link', name='Quill', exact=True).count() == 0
        playwright.expect(page.locator('#globalAccount')).to_contain_text('alice')
        header.get_by_role('link', name='Library', exact=True).click()
        page.get_by_role('button', name='+ Collection').click()
        page.locator('#newName').fill('Private sources')
        page.get_by_role('button', name='Create', exact=True).click()
        page.get_by_role('button', name='Private sources', exact=True).click()
        if profile == 'rag':
            assert page.get_by_role('button', name='Write with this library').count() == 0
        else:
            page.get_by_role('button', name='Write with this library').wait_for()
        page.locator('#globalAccount').click()
        page.locator('#globalSignOut').click()
        page.wait_for_url(base + '/login')
        assert context.request.get(base + '/api/shelf').status == 401
        assert context.request.get(base + '/api/writing').status == 401
        assert not errors
        browser.close()


def test_library_import_works_without_visiting_chat(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        seen = []

        def mocked(route):
            url = route.request.url
            path = url.split('/api/', 1)[1].split('?', 1)[0]
            seen.append((path, route.request.method, route.request.headers, route.request.post_data_buffer))
            payloads = {
                'status': {'library_access': {'enabled': True, 'csrf_token': 'local-test-token'}},
                'library-upload': {'upload_id': 'upload-1'},
                'library-sync': {'state': 'complete'},
                'library-status': {'state': 'complete', 'batch_id': 'batch-1'},
                'paper-imports': {'jobs': [{'job_id': 'job-1', 'doi': '10.1000/test', 'state': 'imported', 'message': 'Added to library'}]},
                'paper-import': {'job_id': 'job-1'},
                'discover-journals': {'items': [{'title': 'Test Journal', 'issns': ['1234-5678']}]},
                'discover-papers': {'items': [{'title': '<b>A study</b>', 'author': 'Example', 'year': '2026', 'journal': 'Test Journal', 'doi': '10.1000/test'}]},
            }
            route.fulfill(json=payloads[path])

        for endpoint in ['status', 'library-upload', 'library-sync', 'library-status', 'paper-imports', 'paper-import', 'discover-journals', 'discover-papers']:
            page.route(base + '/api/' + endpoint + '*', mocked)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base + '/library')
        page.get_by_role('button', name='Find or import papers').click()
        dialog = page.locator('#libraryImportDialog')
        playwright.expect(page.locator('#libraryImportFiles')).to_be_enabled()
        page.locator('#libraryImportFiles').set_input_files({'name': 'example.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF-1.4\nsynthetic upload'})
        playwright.expect(page.locator('.library-import-jobs')).to_contain_text('Library updated')
        uploaded = next(item for item in seen if item[0] == 'library-upload')
        assert uploaded[2]['x-originrag-local-token'] == 'local-test-token'
        assert uploaded[3].startswith(b'%PDF-')
        dialog.get_by_label('Find papers', exact=True).fill('A study')
        dialog.get_by_label('Find journals', exact=True).fill('Test')
        dialog.get_by_role('checkbox').check()
        dialog.get_by_role('button', name='Search papers').click()
        dialog.get_by_role('button', name='Add to library', exact=True).click()
        playwright.expect(page.locator('.library-import-jobs')).to_contain_text('Added to library')
        imported = next(item for item in seen if item[0] == 'paper-import')
        assert json.loads(imported[3]) == {'doi': '10.1000/test'}
        assert page.locator('.library-find-results h3').inner_text() == '<b>A study</b>'
        assert page.locator('.library-find-results h3 b').count() == 0
        dialog.get_by_role('button', name='Close', exact=True).click()
        playwright.expect(dialog).not_to_be_attached()
        assert page.url.endswith('/library')
        assert not errors
        browser.close()
