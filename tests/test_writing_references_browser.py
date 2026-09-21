import pytest

from test_writing import workspace_server as workspace_server


HARNESS = r'''async () => {
  const api = async (path = '/api/writing', payload) => {
    const response = await fetch(path, {method: payload ? 'POST' : 'GET',
      headers: payload ? {'Content-Type': 'application/json'} : {},
      body: payload ? JSON.stringify(payload) : undefined});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    return result;
  };
  window.referencesProject = await api('/api/writing', {action:'create_project', name:'Reference test'});
  window.referenceMessages = []; window.referenceCitations = [];
  window.referenceContext = {
    api, getProject:()=>window.referencesProject, save:async()=>{},
    linkCollection:async id => {
      window.referencesProject = await api('/api/writing', {...window.referencesProject, action:'save_project', collection:id});
    },
    acceptProject:project => {window.referencesProject=project;},
    insertCitation:key => {window.referenceCitations.push(key);return true;},
    notice:message => window.referenceMessages.push(message),
  };
  const module = await import('/assets/workspace/writing-references.mjs');
  window.openReferenceTest=()=>module.openReferences(window.referenceContext);
  await window.openReferenceTest();
}'''


def start(page, base):
    page.goto(base + '/library')
    page.locator('#libraryCount').filter(has_text='1 paper').wait_for()
    page.evaluate(HARNESS)
    page.locator('#referencesDialog').wait_for()


def collect_one(page):
    page.locator('#referencesNewName').fill('My manuscript sources')
    page.locator('#referencesCreate').click()
    page.wait_for_function("() => document.querySelector('#referencesLibrary').value !== '' && document.querySelector('#referencesDialog').getAttribute('aria-busy') !== 'true'")
    page.locator('#referencesCorpusTab').click()
    page.locator('#referencesSearch').fill('Example')
    page.locator('#referencesResults .references-paper').first.wait_for()
    page.get_by_role('button', name='Add to personal library', exact=True).click()
    page.get_by_role('button', name='In your library', exact=True).wait_for()
    page.locator('#referencesLibraryTab').click()
    page.get_by_role('button', name='Insert citation', exact=True).wait_for()


def test_reference_dialog_create_collect_preview_sync_and_insert(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1450, 'height': 980})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        start(page, base)
        assert page.locator('#referencesPreview').is_disabled()
        collect_one(page)
        key = page.locator('.references-key').inner_text()
        assert page.locator('.references-warning').count() >= 1
        page.get_by_role('button', name='Insert citation', exact=True).click()
        page.locator('#referencesApply').wait_for()
        assert page.evaluate('referenceCitations') == []
        assert page.evaluate("referencesProject.files['references.bib']") == ''
        assert '1 added' in page.locator('#referencesReview').inner_text()
        page.locator('#referencesApply').click()
        page.locator('#referencesDialog').wait_for(state='detached')
        assert page.evaluate('referenceCitations') == [key]
        assert key in page.evaluate("referencesProject.files['references.bib']")
        page.evaluate('openReferenceTest()')
        page.get_by_role('button', name='Insert citation', exact=True).click()
        page.locator('#referencesDialog').wait_for(state='detached')
        assert page.evaluate('referenceCitations') == [key, key]
        assert not errors
        browser.close()


def test_reference_dialog_manual_conflict_blocks_sync_and_keeps_source(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1450, 'height': 980})
        start(page, base)
        collect_one(page)
        page.locator('#referencesPreview').click()
        page.locator('#referencesApply').click()
        page.wait_for_function("() => referenceMessages.some(message=>message.startsWith('Bibliography synced'))")
        page.locator('#referencesClose').click()
        page.evaluate("""async () => {
          referencesProject.files['references.bib'] = referencesProject.files['references.bib'].replace('A study', 'My manual title');
          referencesProject = await referenceContext.api('/api/writing', {...referencesProject, action:'save_project'});
          await openReferenceTest();
        }""")
        before = page.evaluate("referencesProject.files['references.bib']")
        page.locator('#referencesPreview').click()
        page.locator('.references-conflicts').wait_for()
        assert page.locator('#referencesApply').is_disabled()
        assert 'Nothing will be overwritten' in page.locator('.references-conflicts').inner_text()
        assert page.evaluate("referencesProject.files['references.bib']") == before
        page.locator('#referencesClose').click()
        page.locator('#referencesDialog').wait_for(state='detached')
        browser.close()


def test_reference_dialog_mobile_escape_and_text_safety(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        start(page, base)
        page.locator('#referencesNewName').fill('<img src=x onerror=alert(1)>')
        page.locator('#referencesCreate').click()
        page.wait_for_function("() => document.querySelector('#referencesLibrary').value !== ''")
        assert '<img src=x' in page.locator('#referencesLibrary option:checked').inner_text()
        assert page.locator('#referencesDialog img').count() == 0
        bounds = page.locator('#referencesDialog').bounding_box()
        assert bounds['x'] >= 0 and bounds['width'] <= 390
        page.keyboard.press('Escape')
        page.locator('#referencesDialog').wait_for(state='detached')
        browser.close()


@pytest.mark.parametrize('placement', ['unused_file', 'disconnected_target'])
def test_reference_citation_requires_selected_bibliography_and_document_connection(workspace_server, placement):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1450, 'height': 980})
        start(page, base)
        collect_one(page)
        key = page.locator('.references-key').inner_text()
        page.locator('#referencesClose').click()
        page.evaluate(r'''async placement => {
          const refs = await referenceContext.api('/api/writing?collection_refs=' + referencesProject.collection);
          referencesProject.files['main.tex'] = '\\documentclass{article}\n\\begin{document}\nText\n\\end{document}\n';
          referencesProject.files[placement === 'unused_file' ? 'unused.bib' : 'references.bib'] = refs.bibtex;
          referencesProject = await referenceContext.api('/api/writing', {...referencesProject, action:'save_project'});
          await openReferenceTest();
        }''', placement)
        page.get_by_role('button', name='Insert citation', exact=True).click()
        page.locator('#referencesApply').wait_for()
        assert page.evaluate('referenceCitations') == []
        assert page.locator('#referencesDialog').is_visible()
        review = page.locator('#referencesReview').inner_text()
        assert 'connected in main.tex' in review
        assert ('1 added' if placement == 'unused_file' else '0 added') in review
        page.locator('#referencesApply').click()
        page.locator('#referencesDialog').wait_for(state='detached')
        assert page.evaluate('referenceCitations') == [key]
        assert key in page.evaluate("referencesProject.files['references.bib']")
        assert r'\bibliography{references}' in page.evaluate("referencesProject.files['main.tex']")
        browser.close()


@pytest.mark.parametrize('custom', [r'\bibliography{other}', r'\begin{thebibliography}{9}\end{thebibliography}'])
def test_reference_citation_does_not_override_custom_bibliography(workspace_server, custom):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1450, 'height': 980})
        start(page, base)
        collect_one(page)
        page.locator('#referencesClose').click()
        page.evaluate(r'''async custom => {
          const refs = await referenceContext.api('/api/writing?collection_refs=' + referencesProject.collection);
          referencesProject.files['main.tex'] = '\\documentclass{article}\n\\begin{document}\n' + custom + '\n\\end{document}\n';
          referencesProject.files['references.bib'] = refs.bibtex;
          referencesProject = await referenceContext.api('/api/writing', {...referencesProject, action:'save_project'});
          await openReferenceTest();
        }''', custom)
        before = page.evaluate('referencesProject')
        page.get_by_role('button', name='Insert citation', exact=True).click()
        page.locator('.references-connection-warning').wait_for()
        assert page.locator('#referencesApply').is_disabled()
        assert page.evaluate('referenceCitations') == []
        assert page.evaluate('referencesProject') == before
        page.locator('#referencesClose').click()
        browser.close()
