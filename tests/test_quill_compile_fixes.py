import os

import pytest

from originrag import web_workspace
from test_writing import workspace_server as workspace_server
from test_writing_editor_integration import create_manuscript, get_project, drag


BAD = r'\textbff{Important result.}'
GOOD = r'\textbf{Important result.}'
DIAG = {'file':'chapter.tex', 'line':2, 'message':'Undefined control sequence.', 'severity':'error'}


def prepare(page, base, broken=BAD):
    key = create_manuscript(page, base)
    doc = get_project(page, key)
    files = {'main.tex': '\\documentclass{article}\n\\begin{document}\n\\input{chapter}\n\\end{document}',
             'chapter.tex': 'Initial paragraph.\n' + broken}
    assert page.request.post(base + '/api/writing', data={**doc, 'action':'save_project', 'files':files}).ok
    page.reload()
    page.locator('#source .cm-content').wait_for()
    return key


def test_compile_problem_workflow_and_stale_review(workspace_server, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    import fitz
    base, _ = workspace_server
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '1')
    builds, requests = [], []
    with fitz.open() as pdf:
        pdf.new_page()
        valid_pdf = pdf.tobytes()
    monkeypatch.setattr(web_workspace, 'compiler_status', lambda: {'configured':True})
    def build(project, *args, **kwargs):
        builds.append(project['revision'])
        failed = BAD in project['files']['chapter.tex']
        return {'ok':not failed, 'diagnostics':[dict(DIAG, severity='warning', line=1), *([DIAG, DIAG] if failed else [])],
                'revision':project['revision'], 'pdf':b'' if failed else valid_pdf, 'seconds':0, 'log':'Synthetic compiler output.'}
    monkeypatch.setattr(web_workspace, 'compile_saved_project', build)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width':1500, 'height':950})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda r: requests.append(r.post_data_json.get('action'))
                if r.method == 'POST' and r.url.endswith('/api/writing') else None)
        key = prepare(page, base)
        page.locator('#compile').click()
        apply = page.get_by_role('button', name='Apply fix')
        pw.expect(apply).to_be_visible()
        assert len(builds) == 1
        assert BAD in get_project(page, key)['files']['chapter.tex']
        pw.expect(page.locator('.cm-compile-error')).to_have_count(0)
        page.locator('#problems button').filter(has_text='chapter.tex:2').click()
        pw.expect(page.locator('.cm-compile-error')).to_have_text(BAD)
        height = page.locator('#compileBody').bounding_box()['height']
        drag(page, '#problemsResize', 0, -80)
        assert page.locator('#compileBody').bounding_box()['height'] > height + 50
        page.locator('#problemsResize').press('Home')
        pw.expect(page.locator('#problemsResize')).to_have_attribute('aria-valuenow','72')
        apply.click()
        pw.expect(page.locator('#source .cm-content')).to_contain_text(GOOD)
        pw.expect(page.locator('#savedState')).to_have_text('All changes saved')
        assert GOOD in get_project(page, key)['files']['chapter.tex']
        pw.expect(page.locator('#previewState')).to_contain_text('PDF · revision')
        pw.expect(apply).to_have_count(0)
        page.locator('[data-edit-command="undo"]').click()
        pw.expect(page.locator('#source .cm-content')).to_contain_text(BAD)
        page.locator('#compile').click()
        pw.expect(apply).to_be_visible()
        page.locator('#source .cm-content').fill('Changed manually')
        pw.expect(apply).to_have_count(0)
        pw.expect(page.locator('.cm-compile-error')).to_have_count(0)
        page.locator('#source .cm-content').fill('Another draft.\n' + BAD)
        page.locator('#compile').click()
        pw.expect(apply).to_be_visible()
        page.route('**/api/writing?revision=*', lambda route: route.fulfill(json={'revision':99999}))
        apply.click()
        pw.expect(page.locator('#notice')).to_contain_text('saved manuscript changed')
        pw.expect(page.locator('#source .cm-content')).to_contain_text(BAD)
        assert not set(requests).intersection({'compile_fix', 'project_chat', 'language_check'})
        assert not errors
        browser.close()


@pytest.mark.skipif(os.getenv('ORIGINWRITE_TEST_COMPILER') != '1', reason='Requires isolated TeX compiler.')
@pytest.mark.parametrize('broken,fixed', [
    (GOOD + '}', GOOD),
    ('Evidence: $x^{2$ here.', 'Evidence: $x^{2}$ here.'),
    (r'\unknowncommand{Result}', None),
    (r'\textbf{unterminated', None),
])
def test_real_compiler_only_offers_clear_fixes(workspace_server, monkeypatch, broken, fixed):
    pw = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK','0')
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        key = prepare(page, base, broken)
        page.locator('#compile').click()
        pw.expect(page.locator('#previewState')).to_contain_text('Compilation failed', timeout=60000)
        fix = page.get_by_role('button', name='Apply fix')
        assert broken in get_project(page, key)['files']['chapter.tex']
        if fixed:
            pw.expect(fix).to_be_visible()
            fix.click()
            pw.expect(page.locator('#previewState')).to_contain_text('PDF · revision', timeout=60000)
            pw.expect(page.locator('#pdfPages canvas')).to_have_count(1)
            assert get_project(page, key)['files']['chapter.tex'] == 'Initial paragraph.\n' + fixed
        else:
            pw.expect(fix).to_have_count(0)
            pw.expect(page.locator('#problems .compiler-advice')).to_have_count(0)
            pw.expect(page.locator('#problems')).not_to_contain_text('Looking for a fix')
        browser.close()
