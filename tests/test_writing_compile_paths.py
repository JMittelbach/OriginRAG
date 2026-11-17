import os
import time

import pytest

from originrag.writing_compile import compile_project, diagnostics
from originrag.writing_builds import compile_saved_project
from test_writing import workspace_server as workspace_server


@pytest.mark.parametrize('path', [
    'chapters/my intro.tex', './chapters/my intro.tex', 'chapters/my intro',
    '/source/chapters/my intro.tex', '[build]/source/chapters/my intro.tex',
])
@pytest.mark.parametrize('prefix', ['', 'error: ', 'warning: '])
def test_compiler_paths_resolve_only_to_actual_project_files(path, prefix):
    items = diagnostics(prefix + path + ':12: Undefined command', {'chapters/my intro.tex': ''})
    assert items == [{'file': 'chapters/my intro.tex', 'line': 12, 'message': 'Undefined command'}]


def test_external_and_ambiguous_files_are_not_mapped_to_wrong_project_file():
    files = {'chapters/my intro.tex': '', 'main.tex': ''}
    for name in ['my intro.tex', '/private/main.tex', '../main.tex', 'other.tex']:
        message = 'error: ' + name + ':2: Undefined command'
        assert diagnostics(message, files) == [{'file': '', 'line': 0, 'message': message}]
    assert diagnostics('error: main.tex:' + '9' * 5000 + ': Error', files)[0]['line'] == 0


@pytest.mark.skipif(os.environ.get('ORIGINWRITE_TEST_COMPILER') != '1', reason='Requires isolated TeX compiler')
def test_real_compile_nested_space_path_repair_and_cache(tmp_path):
    files = {
        'main.tex': '\\documentclass{article}\n\\begin{document}\n\\input{chapters/my intro}\n\\end{document}',
        'chapters/my intro.tex': 'A paragraph.\n\\undefinedQuillCommand\n',
    }
    project = {'id': 'path-regression', 'revision': 1, 'files': files, 'main': 'main.tex'}
    broken = compile_project(project)
    assert not broken['ok'] and not broken['pdf']
    assert any(item['file'] == 'chapters/my intro.tex' and item['line'] == 2 for item in broken['diagnostics']), broken['log']
    project['files']['chapters/my intro.tex'] = 'A repaired paragraph with umlauts: Größe.\n'
    project['revision'] = 2
    start = time.monotonic()
    fixed = compile_saved_project(project, tmp_path)
    elapsed = time.monotonic() - start
    assert fixed['ok'] and fixed['pdf'].startswith(b'%PDF'), fixed['log']
    assert not fixed['cached']
    start = time.monotonic()
    reused = compile_saved_project(project, tmp_path)
    cached_elapsed = time.monotonic() - start
    assert reused['cached'] and reused['pdf'] == fixed['pdf']
    print(f'REAL_TEX rebuild_seconds={elapsed:.4f} cached_seconds={cached_elapsed:.4f}', flush=True)


@pytest.mark.skipif(os.environ.get('ORIGINWRITE_TEST_COMPILER') != '1', reason='Requires isolated TeX compiler')
def test_nested_file_diagnostic_opens_correct_editor_and_recovers(workspace_server):
    from playwright.sync_api import sync_playwright, expect
    from test_writing_editor_integration import create_manuscript, get_project
    base, _ = workspace_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        key = create_manuscript(page, base)
        project = get_project(page, key)
        files = {
            'main.tex': '\\documentclass{article}\n\\begin{document}\n\\input{chapters/my intro}\n\\end{document}',
            'chapters/my intro.tex': 'A paragraph.\n\\undefinedQuillCommand\n',
        }
        response = page.request.post(base + '/api/writing', data={**project, 'action': 'save_project', 'files': files})
        assert response.ok
        page.reload()
        page.locator('#compile').click()
        expect(page.locator('#previewState')).to_contain_text('Compilation failed', timeout=30000)
        page.locator('#layoutMode').select_option('pdf')
        page.locator('#problems button').filter(has_text='chapters/my intro.tex:2').click()
        expect(page.locator('#sourcePane')).to_be_visible()
        expect(page.locator('#source .cm-content')).to_contain_text('undefinedQuillCommand')
        page.locator('#source .cm-content').fill('A repaired paragraph.')
        page.locator('#compileCode').click()
        page.locator('#layoutMode').select_option('split')
        expect(page.locator('#previewState')).to_contain_text('PDF · revision', timeout=30000)
        expect(page.locator('#pdfPages canvas')).to_have_count(1, timeout=10000)
        assert get_project(page, key)['files']['chapters/my intro.tex'] == 'A repaired paragraph.'
        browser.close()
