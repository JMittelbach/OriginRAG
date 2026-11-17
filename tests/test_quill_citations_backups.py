import base64
import zipfile

import pytest

from test_writing import workspace_server as workspace_server
from test_writing_editor_integration import create_manuscript, get_project


def test_citation_parser_handles_comments_nested_titles_and_multiple_keys(workspace_server):
    from playwright.sync_api import sync_playwright
    base, _ = workspace_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(base + '/write')
        result = page.evaluate(r'''async () => {
          const m = await import('/assets/workspace/citation-completion.mjs');
          const files = {'refs.bib': '@comment{ @article{fake, title={Ignore}} }\n% @article{hidden, title={Hidden}}\n@article{Doe2026, title={A {nested} title}, author={Doe, Jane}, year={2026}}\n@book(Other2025, title="Second", author="Other, A.")'};
          return {options: m.bibliographyOptions(files),
            multi: m.citationContext('Result \\citep[see][p. 2]{Doe2026, Ot'),
            comment: m.citationContext('% ignore \\cite{Do'),
            escaped: m.citationContext('50\\% result \\cite{Do'),
            text: m.citationContext('Do'),
            closed: m.citationContext('\\cite{Doe2026} more'),
            style: m.citationContext('\\citestyle{Do'),
            escapedCommand: m.citationContext('\\\\cite{Do')};
        }''')
        assert [item['label'] for item in result['options']] == ['Doe2026', 'Other2025']
        assert 'A nested title' in result['options'][0]['info']
        assert result['multi']['used'] == ['Doe2026']
        assert result['comment'] is None and result['text'] is None and result['closed'] is None
        assert result['style'] is None and result['escapedCommand'] is None
        assert result['escaped'] is not None
        browser.close()


def test_inline_completion_uploads_and_project_backup_roundtrip(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, tmp = workspace_server
    image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==')
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1550, 'height': 1000}, accept_downloads=True)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        project_id = create_manuscript(page, base, 'Autocomplete and backup')
        bib = b'@article{Doe2026, author={Doe, Jane}, title={A useful study}, year={2026}}\n@article{Other2025, title={Other study}, year={2025}}'
        page.locator('#fileUpload').set_input_files([
            {'name': 'myrefs.bib', 'mimeType': 'text/plain', 'buffer': bib},
            {'name': 'figure.png', 'mimeType': 'image/png', 'buffer': image},
        ])
        playwright.expect(page.locator('#fileList [data-file="myrefs.bib"]')).to_be_visible()
        playwright.expect(page.locator('#savedState')).to_have_text('All changes saved')
        code = page.locator('#source .cm-content')
        code.fill('\\cite{')
        page.keyboard.insert_text('Do')
        playwright.expect(page.locator('.cm-tooltip-autocomplete')).to_contain_text('Doe2026')
        page.keyboard.press('ArrowDown')
        page.keyboard.press('Enter')
        playwright.expect(code).to_contain_text('Doe2026')
        page.keyboard.insert_text(', Ot')
        playwright.expect(page.locator('.cm-tooltip-autocomplete')).to_contain_text('Other2025')
        page.keyboard.press('Escape')
        page.locator('.writer-menu > summary').click()
        page.locator('#backupProject').click()
        with page.expect_download() as event:
            page.get_by_role('button', name='Download backup ZIP', exact=True).click()
        download = event.value
        assert project_id[:8] in download.suggested_filename
        with zipfile.ZipFile(download.path()) as archive:
            assert archive.read('myrefs.bib') == bib
            assert archive.read('figure.png') == image
            assert b'Doe2026' in archive.read('main.tex')
        saved = get_project(page, project_id)
        assert saved['files']['figure.png'] == base64.b64encode(image).decode()
        page.screenshot(path=str(tmp / 'quill-backups.png'))
        assert not errors
        browser.close()


def test_upload_batches_are_atomic_and_reject_collisions_and_invalid_text(workspace_server):
    from playwright.sync_api import sync_playwright
    base, _ = workspace_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(base + '/write')
        result = page.evaluate(r'''async () => {
          const {prepareProjectUploads} = await import('/assets/workspace/project-uploads.mjs');
          const existing = {'main.tex': 'Original'};
          const batches = [
            [new File(['OK'], 'ok.tex'), new File(['bad'], 'bad.exe')],
            [new File(['replace'], 'MAIN.tex')],
            [new File(['one'], 'new.bib'), new File(['two'], 'NEW.bib')],
            [new File([new Uint8Array([255])], 'bad.tex')],
            [new File(['null\0byte'], 'bad.tex')],
            [new File([new Uint8Array(8000001)], 'large.png')],
          ];
          const rejected = [];
          for (const files of batches) {
            try { await prepareProjectUploads(files, existing); rejected.push(false); }
            catch { rejected.push(true); }
          }
          return {rejected, existing};
        }''')
        assert all(result['rejected'])
        assert result['existing'] == {'main.tex': 'Original'}
        browser.close()


def test_folder_backups_permission_and_error_handling(workspace_server):
    from playwright.sync_api import sync_playwright
    base, _ = workspace_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        project_id = create_manuscript(page, base)
        page.evaluate(r'''async id => {
          const {setupProjectBackup} = await import('/assets/workspace/writing-backups.mjs');
          window.backupWrites = []; window.backupPermissions = 'granted';
          window.showDirectoryPicker = async () => ({name: 'Dropbox',
            queryPermission: async () => window.backupPermissions,
            getFileHandle: async name => ({createWritable: async () => ({
              write: async blob => window.backupWrites.push({name, size: blob.size}),
              close: async () => {}, abort: async () => {}})})});
          const project = await (await fetch('/api/writing?id=' + id)).json();
          setupProjectBackup({getProject: () => project, save: async () => {}, notify: () => {}}).open();
        }''', project_id)
        page.locator('#chooseBackupFolder').click()
        page.locator('#saveProjectBackup').click()
        page.wait_for_function('() => backupWrites.length === 1')
        assert page.evaluate('backupWrites[0].size') > 0
        page.evaluate("backupPermissions = 'denied'")
        page.locator('#saveProjectBackup').click()
        page.wait_for_function("() => document.getElementById('backupStatus').textContent.includes('Folder access expired')")
        assert page.evaluate('backupWrites.length') == 1
        browser.close()
