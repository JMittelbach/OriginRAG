import os
from urllib.parse import parse_qs, urlparse

import pytest

from test_writing import workspace_server as workspace_server


def create_manuscript(page, base, name='Integrated manuscript'):
    page.goto(base + '/write')
    page.get_by_role('button', name='+ New manuscript').click()
    page.locator('#newName').fill(name)
    page.get_by_role('button', name='Create', exact=True).click()
    page.locator('#source .cm-content').wait_for()
    return parse_qs(urlparse(page.url).query)['id'][0]


def get_project(page, project_id):
    return page.evaluate("async id => (await fetch('/api/writing?id=' + id)).json()", project_id)


def test_real_code_editor_free_font_size_does_not_change_manuscript(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        project_id = create_manuscript(page, base, 'Readable at any size')
        original = get_project(page, project_id)
        zoom = page.locator('#codeZoom')
        for size in ['8', '9.5', '32', '72', '120']:
            zoom.fill(size)
            zoom.press('Enter')
            assert page.locator('#source .cm-editor').evaluate('e => parseFloat(getComputedStyle(e).fontSize)') == float(size)
        page.locator('#codeZoomIn').click()
        assert zoom.input_value() == '121'
        page.locator('#codeZoomOut').click()
        assert zoom.input_value() == '120'
        page.reload()
        page.locator('#source .cm-content').wait_for()
        assert zoom.input_value() == '120'
        assert get_project(page, project_id)['files'] == original['files']
        assert get_project(page, project_id)['revision'] == original['revision']
        assert not errors
        browser.close()


def drag(page, selector, dx=0, dy=0):
    bounds = page.locator(selector).bounding_box()
    x, y = bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + dy, steps=8)
    page.mouse.up()


def test_real_editor_dirty_source_reference_sync_and_cursor(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, tmp = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1650, 'height': 1050})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        project_id = create_manuscript(page, base)
        page.evaluate("async id => { const project = await (await fetch('/api/writing?id=' + id)).json(); await fetch('/api/writing', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:'configure_bibliography', id, revision:project.revision, enabled:false, filename:'references.bib'})}); }", project_id)
        page.reload()
        page.locator('#source .cm-content').wait_for()
        code = page.locator('#source .cm-content')
        code.fill('\\documentclass{article}\n\\begin{document}\nA result needs evidence: \n\\end{document}')
        page.locator('#source .cm-line').filter(has_text='A result needs evidence:').click()
        page.keyboard.press('End')
        page.keyboard.insert_text('Live unsaved edit ')
        playwright.expect(page.locator('#savedState')).to_have_text('Unsaved changes')
        page.locator('#referenceButton').click()
        page.locator('#referencesNewName').fill('Sources for this manuscript')
        page.locator('#referencesCreate').click()
        page.wait_for_function("() => document.querySelector('#referencesLibrary').value && document.querySelector('#referencesDialog').getAttribute('aria-busy') !== 'true'")
        page.locator('#referencesCorpusTab').click()
        page.locator('#referencesSearch').fill('Example')
        page.get_by_role('button', name='Add to personal library', exact=True).click()
        page.get_by_role('button', name='In your library', exact=True).wait_for()
        page.locator('#referencesLibraryTab').click()
        key = page.locator('.references-key').inner_text()
        page.locator('#referencesResults').get_by_role('button', name='Insert citation', exact=True).click()
        page.locator('#referencesApply').wait_for()
        pending = get_project(page, project_id)
        assert 'Live unsaved edit' in pending['files']['main.tex']
        assert key not in pending['files']['references.bib']
        page.locator('#referencesApply').click()
        page.locator('#referencesDialog').wait_for(state='detached')
        playwright.expect(page.locator('#savedState')).to_have_text('All changes saved')
        stored = get_project(page, project_id)
        source = stored['files']['main.tex']
        citation = '\\cite{' + key + '}'
        assert 'Live unsaved edit' in source
        assert source.index('Live unsaved edit') < source.index(citation) < source.index('\\end{document}'), source
        assert '\\bibliography{references}' in source
        assert key in stored['files']['references.bib']
        page.reload()
        playwright.expect(page.locator('#source .cm-content')).to_contain_text(citation)
        if os.environ.get('ORIGINWRITE_TEST_COMPILER') == '1':
            page.locator('#compile').click()
            playwright.expect(page.locator('#pdfPages canvas')).to_have_count(1, timeout=30000)
            playwright.expect(page.locator('#previewState')).to_contain_text('PDF · revision')
        page.screenshot(path=str(tmp / 'originwrite-references-integrated.png'), full_page=True)
        print('Quill reference screenshot:', tmp / 'originwrite-references-integrated.png')
        assert not errors
        browser.close()


def test_template_gallery_creates_new_project_and_preserves_unsaved_original(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, tmp = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1650, 'height': 1050})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        first_id = create_manuscript(page, base, 'Original draft')
        text = '\\documentclass{article}\n\\begin{document}Keep this draft unchanged.\\end{document}'
        page.locator('#source .cm-content').fill(text)
        page.locator('#templateButton').click()
        page.locator('#templateDialog').wait_for()
        playwright.expect(page.locator('#templateDialog .template-card')).to_have_count(7)
        page.wait_for_function("() => [...document.querySelectorAll('#templateDialog img')].every(image => image.complete && image.naturalWidth > 0)")
        page.screenshot(path=str(tmp / 'originwrite-template-gallery.png'), full_page=True)
        page.locator('#templateDialog [data-template="bioinformatics"]').click()
        page.locator('#newName').fill('New Bioinformatics draft')
        page.get_by_role('button', name='Create', exact=True).click()
        playwright.expect(page.locator('#projectTitle')).to_have_text('New Bioinformatics draft')
        second_id = parse_qs(urlparse(page.url).query)['id'][0]
        assert second_id != first_id
        old = get_project(page, first_id)
        new = get_project(page, second_id)
        assert old['files']['main.tex'] == text
        assert new['template'] == 'bioinformatics'
        assert 'oup-authoring-template.cls' in new['files']
        assert 'oup-abbrvnat.bst' in new['files'] and 'LPPL-1.3c.txt' in new['files']
        page.goto(base + '/write')
        playwright.expect(page.locator('.manuscript-row')).to_have_count(2)
        page.screenshot(path=str(tmp / 'originwrite-dashboard.png'), full_page=True)
        page.get_by_role('tab', name='Templates', exact=True).click()
        playwright.expect(page.locator('#deskPanel .template-card')).to_have_count(7)
        page.locator('[aria-label="Search templates"]').fill('G3')
        playwright.expect(page.locator('#deskPanel .template-card')).to_have_count(1)
        page.get_by_role('button', name='Clear', exact=True).click()
        page.locator('[aria-label="Filter templates by publisher"]').select_option('PLOS')
        playwright.expect(page.locator('#deskPanel .template-card')).to_have_count(3)
        page.locator('[aria-label="Search templates"]').fill('biology PLOS')
        playwright.expect(page.locator('#deskPanel .template-card')).to_have_count(1)
        playwright.expect(page.locator('#deskPanel .template-card h3')).to_have_text('PLOS Computational Biology')
        page.locator('[aria-label="Search templates"]').fill('not-a-journal')
        playwright.expect(page.locator('#deskPanel [role="status"]')).to_contain_text('0 of 7')
        page.get_by_role('button', name='Clear', exact=True).click()
        playwright.expect(page.locator('#deskPanel .template-card')).to_have_count(7)
        page.screenshot(path=str(tmp / 'originwrite-expanded-templates.png'), full_page=True)
        print('Quill dashboard screenshot:', tmp / 'originwrite-dashboard.png')
        print('Quill template screenshot:', tmp / 'originwrite-template-gallery.png')
        assert not errors
        browser.close()


@pytest.mark.skipif(os.environ.get('ORIGINWRITE_TEST_COMPILER') != '1', reason='Requires isolated local compiler')
def test_real_compile_stacked_height_and_independent_code_pdf_zoom(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, tmp = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1700, 'height': 1150})
        errors, compile_payloads = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def record(request):
            if request.method == 'POST' and request.url.endswith('/api/writing'):
                payload = request.post_data_json
                if payload.get('action') == 'compile':
                    compile_payloads.append(payload)

        page.on('request', record)
        project_id = create_manuscript(page, base, 'Layout and PDF')
        page.locator('#compile').click()
        playwright.expect(page.locator('#pdfPages canvas')).to_have_count(1, timeout=30000)
        playwright.expect(page.locator('#previewState')).to_contain_text('PDF · revision')
        assert compile_payloads[-1]['force'] is False
        original = get_project(page, project_id)['files']
        page.locator('#codeZoom').fill('20')
        page.locator('#codeZoom').press('Enter')
        assert page.locator('#source .cm-editor').evaluate('element => getComputedStyle(element).fontSize') == '20px'
        page.locator('#pdfZoom').select_option('0.5')
        before = page.locator('#pdfPages canvas').bounding_box()['width']
        page.locator('#pdfZoomIn').click()
        assert page.locator('#pdfPages canvas').bounding_box()['width'] > before + 100
        assert page.locator('#codeZoom').input_value() == '20'
        page.locator('#paneDirection').select_option('stack')
        assert page.locator('#panesResize').get_attribute('aria-orientation') == 'horizontal'
        before = page.locator('#sourcePane').bounding_box()['height']
        drag(page, '#panesResize', dy=85)
        assert page.locator('#sourcePane').bounding_box()['height'] > before + 70
        page.locator('#codeZoomOut').click()
        assert page.locator('#pdfZoom').input_value() == '0.75'
        assert page.locator('#source .cm-editor').evaluate('element => getComputedStyle(element).fontSize') == '19px'
        assert get_project(page, project_id)['files'] == original
        assert len(compile_payloads) == 1
        page.screenshot(path=str(tmp / 'originwrite-stacked-editor.png'), full_page=True)
        page.locator('#paneDirection').select_option('side')
        page.screenshot(path=str(tmp / 'originwrite-editor-pdf.png'), full_page=True)
        page.locator('#autoCompile').check()
        playwright.expect(page.locator('#previewState')).to_contain_text('saved build', timeout=10000)
        assert compile_payloads[-1]['force'] is False
        print('Quill editor screenshot:', tmp / 'originwrite-editor-pdf.png')
        print('Quill stacked screenshot:', tmp / 'originwrite-stacked-editor.png')
        assert not errors
        browser.close()
