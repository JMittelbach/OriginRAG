import json
import os

import pytest

from originrag import web_workspace, writing_chat
from test_writing import workspace_server as workspace_server
from test_writing_chat_browser import model_list as model_list
from test_writing_editor_integration import create_manuscript, get_project, drag


BAD = r'\textbff{Important result.}'
GOOD = r'\textbf{Important result.}'
DIAG = {'file':'chapter.tex', 'line':2, 'message':'Undefined control sequence.', 'severity':'error'}


def prepare(page, base):
    key = create_manuscript(page, base)
    doc = get_project(page, key)
    files = {'main.tex': '\\documentclass{article}\n\\begin{document}\n\\input{chapter}\n\\end{document}',
             'chapter.tex': 'Initial paragraph.\n' + BAD}
    assert page.request.post(base + '/api/writing', data={**doc, 'action':'save_project', 'files':files}).ok
    page.reload()
    page.locator('#source .cm-content').wait_for()
    return key


def test_compile_help_limits_context_and_rejects_edits_outside_excerpt():
    source = '\n'.join(['Private intro', 'duplicate', 'line', 'line', 'line', 'duplicate', BAD, 'line'])
    project = {'files':{'chapter.tex':source, 'private.tex':'Never send this file.'}, 'revision':2}
    request = writing_chat.prepare_compile_fix(project, {**DIAG, 'line':7, 'revision':2})
    assert 'Private intro' not in request['prompt'] and 'private.tex' not in request['prompt']
    assert writing_chat.edit_location(request, 'chapter.tex', 'duplicate')[0] == source.rfind('duplicate')
    assert writing_chat.edit_location(request, 'chapter.tex', 'Private intro') is None
    with pytest.raises(ValueError):
        writing_chat.prepare_compile_fix(project, {**DIAG, 'revision':1})
    with pytest.raises(ValueError):
        writing_chat.prepare_compile_fix(project, {**DIAG, 'line':99, 'revision':2})
    generic = writing_chat.prepare_compile_fix(project, {**DIAG, 'file':'', 'line':0, 'revision':2})
    assert generic['mode'] == 'discuss' and not generic['files']


def test_compile_problem_workflow_and_stale_review(model_list, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    import fitz
    base, _, _ = model_list
    calls, builds = [], []
    with fitz.open() as pdf:
        pdf.new_page()
        valid_pdf = pdf.tobytes()
    monkeypatch.setattr(web_workspace, 'compiler_status', lambda: {'configured':True})
    def build(project, *args, **kwargs):
        builds.append(project['revision'])
        failed = BAD in project['files']['chapter.tex']
        return {'ok':not failed, 'diagnostics':[dict(DIAG, severity='warning', line=1), *([DIAG, DIAG] if failed else [])],
                'revision':project['revision'], 'pdf':b'' if failed else valid_pdf, 'seconds':0, 'log':'Synthetic compiler output.'}
    def generate(prompt, update, **kwargs):
        calls.append(prompt)
        edits = [{'file':'chapter.tex', 'original':BAD, 'replacement':GOOD, 'reason':'Fix the command spelling.'}] if BAD in prompt.replace('\\\\','\\') else []
        return json.dumps({'answer':'The bold command is misspelled.', 'edits':edits}), 'test:qwen'
    monkeypatch.setattr(web_workspace, 'compile_saved_project', build)
    monkeypatch.setattr(writing_chat, 'generate_json_stream', generate)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width':1500, 'height':950})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        key = prepare(page, base)
        page.locator('#compile').click()
        apply = page.get_by_role('button', name='Apply fix')
        pw.expect(apply).to_be_visible()
        assert len(calls) == 1 and len(builds) == 1
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
        assert len(calls) == 1
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
        assert not errors
        browser.close()


@pytest.mark.skipif(not (os.getenv('ORIGINWRITE_TEST_COMPILER') and os.getenv('ORIGINRAG_TEST_LOCAL_MODEL')),
                    reason='Opt in with an isolated TeX compiler and an installed local model.')
def test_real_qwen_compile_fix_to_pdf(workspace_server, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK','1')
    monkeypatch.setenv('ORIGINRAG_OLLAMA_ANSWER_MODEL',os.environ['ORIGINRAG_TEST_LOCAL_MODEL'])
    monkeypatch.setenv('ORIGINRAG_OLLAMA_URL','http://127.0.0.1:11434')
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        key = prepare(page, base)
        page.locator('#compile').click()
        pw.expect(page.locator('#previewState')).to_contain_text('Compilation failed', timeout=60000)
        fix = page.get_by_role('button', name='Apply fix')
        pw.expect(fix).to_be_visible(timeout=135000)
        assert BAD in get_project(page, key)['files']['chapter.tex']
        fix.click()
        pw.expect(page.locator('#previewState')).to_contain_text('PDF · revision', timeout=60000)
        pw.expect(page.locator('#pdfPages canvas')).to_have_count(1)
        assert GOOD in get_project(page, key)['files']['chapter.tex']
        browser.close()
