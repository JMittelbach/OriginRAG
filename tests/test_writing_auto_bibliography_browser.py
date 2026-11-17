import json
from urllib.parse import urlparse

import pytest

from test_writing import workspace_server as workspace_server
from test_writing_editor_integration import create_manuscript, get_project
import test_web_chat as http


def test_default_references_flow_syncs_and_inserts_without_manual_apply(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        project_id = create_manuscript(page, base)
        code = page.locator('#source .cm-content')
        code.fill('\\documentclass{article}\n\\begin{document}\nMy paragraph: \n\\end{document}')
        page.locator('#source .cm-line').filter(has_text='My paragraph:').click()
        page.keyboard.press('End')
        page.locator('#referenceButton').click()
        playwright.expect(page.locator('#referencesAutomatic')).to_be_checked()
        page.locator('#referencesNewName').fill('Automatically linked papers')
        page.locator('#referencesCreate').click()
        page.wait_for_function("() => document.querySelector('#referencesLibrary').value && document.querySelector('#referencesDialog').getAttribute('aria-busy') !== 'true'")
        page.locator('#referencesCorpusTab').click()
        page.locator('#referencesSearch').fill('Example')
        page.get_by_role('button', name='Add to personal library', exact=True).click()
        page.get_by_role('button', name='In your library', exact=True).wait_for()
        stored = get_project(page, project_id)
        assert 'Example2026' in stored['files']['references.bib']
        assert stored['bibliography_status']['state'] == 'synced'
        page.locator('#referencesLibraryTab').click()
        page.locator('#referencesResults').get_by_role('button', name='Insert citation', exact=True).click()
        page.locator('#referencesDialog').wait_for(state='detached')
        playwright.expect(code).to_contain_text('My paragraph: \\cite{Example2026}')
        playwright.expect(page.locator('#savedState')).to_have_text('All changes saved')
        assert not errors
        browser.close()


def test_other_tab_library_change_keeps_dirty_draft_and_updates_completion(workspace_server, monkeypatch):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    catalogue = [
        {'paper_id': 'P1', 'title': 'First study', 'author': 'Doe', 'year': '2026'},
        {'paper_id': 'P2', 'title': 'Second study', 'author': 'Other', 'year': '2026'},
    ]
    monkeypatch.setattr(http.web_chat, 'library_catalog', lambda: catalogue)
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 1000})
        project_id = create_manuscript(page, base)
        collection = page.evaluate("""async id => {
          const post = async payload => (await fetch('/api/writing', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)})).json();
          let library = await post({action:'create_collection', name:'Shared across tabs'});
          library = await post({...library, action:'collection_paper', paper_id:'P1'});
          const project = await (await fetch('/api/writing?id=' + id)).json();
          await post({...project, action:'save_project', collection:library.id});
          return library;
        }""", project_id)
        page.reload()
        code = page.locator('#source .cm-content')
        code.wait_for()
        code.fill('\\documentclass{article}\n\\begin{document}\nAn important new paragraph.\n\\end{document}')
        # A second tab changes the library before this tab's autosave runs.
        response = page.request.post(base + '/api/writing', data={**collection, 'action': 'collection_paper', 'paper_id': 'P2'})
        assert response.ok, response.text()
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        playwright.expect(page.locator('#savedState')).to_have_text('All changes saved')
        stored = get_project(page, project_id)
        assert 'An important new paragraph.' in stored['files']['main.tex']
        assert 'Other2026' in stored['files']['references.bib']
        code.fill('\\cite{')
        page.keyboard.insert_text('Other')
        playwright.expect(page.locator('.cm-tooltip-autocomplete')).to_contain_text('Other2026')
        browser.close()


def test_compile_requires_resolved_bibliography_but_recovery_zip_is_available(workspace_server, monkeypatch):
    base, _ = workspace_server
    port = urlparse(base).port
    def call(payload):
        result = http.http_request(port, 'POST', '/api/writing', payload=payload)
        assert result[0] == 200, result[2]
        return json.loads(result[2])
    library = call({'action': 'create_collection', 'name': 'Sources'})
    library = call({**library, 'action': 'collection_paper', 'paper_id': 'P1'})
    project = call({'action': 'create_project', 'name': 'Checked before compile', 'collection': library['id']})
    project = call({**project, 'action': 'save_project', 'files': {**project['files'], 'references.bib': project['files']['references.bib'].replace('A study', 'My manual title')}})
    def forbidden(*args, **kwargs):
        raise AssertionError('A conflicted bibliography must not reach the compiler')
    from originrag import web_workspace
    monkeypatch.setattr(web_workspace, 'compile_saved_project', forbidden)
    result = http.http_request(port, 'POST', '/api/writing', payload={'action': 'compile', 'id': project['id'], 'revision': project['revision']})
    assert result[0] == 409
    assert 'manual changes' in json.loads(result[2])['error']
    assert http.http_request(port, 'GET', '/api/writing/export?id=' + project['id'])[0] == 200
