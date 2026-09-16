import json
from pathlib import Path
import shutil
import subprocess
import zipfile
import re
import random

import pytest

from test_writing_collaboration import accounts as accounts, workspace as workspace, PASSWORD
from test_writing_editor_collaboration import node_binary
from test_writing import workspace_server as workspace_server
from originrag.writing_merge import merge_text, MergeConflict


ROOT = Path(__file__).resolve().parents[1]
SOURCE = '\\documentclass{article}\n\\begin{document}\nFirst observation.\nMiddle observation.\nFinal observation.\n\\end{document}\n'


@pytest.fixture
def collaboration_browser(workspace, tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    for folder in ('workspace', 'vendor/pdfjs', 'writing_templates'):
        shutil.copytree(ROOT / 'assets' / folder, tmp_path / 'assets' / folder, ignore=shutil.ignore_patterns('editor-build'))
    call, port, headers, _, users, _ = workspace
    base = f'http://127.0.0.1:{port}'
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        contexts = []

        def page_for(name):
            context = browser.new_context(viewport={'width': 1580, 'height': 1050})
            if name:
                context.add_cookies([{'name': part.split('=', 1)[0], 'value': part.split('=', 1)[1], 'url': base} for part in headers[name]['Cookie'].split('; ')])
            contexts.append(context)
            return context.new_page()

        yield call, base, users, page_for, tmp_path, playwright.expect
        for context in contexts:
            context.close()
        browser.close()


def manuscript(call, users, share=True):
    project = call('POST', payload={'action': 'create_project', 'name': 'Joint manuscript'})
    project = call('POST', payload={**project, 'action': 'save_project', 'files': {
        'main.tex': SOURCE,
        'references.bib': '@article{Example2026,\n title={A shared reference},\n author={Example, A.},\n year={2026}\n}\n',
        'sections/notes.tex': 'A supplementary section.'}})
    if share:
        call('POST', '/api/writing/collaboration', payload={'action': 'add', 'id': project['id'], 'user_id': users['bob'].user_id})
    return project


def open_editor(page, base, key):
    page.goto(base + '/write?id=' + key)
    page.locator('#source .cm-content').wait_for()


def test_sharing_accounts_editor_citations_download_saved_zip_and_revocation(collaboration_browser):
    call, base, users, page_for, tmp, expect = collaboration_browser
    project = manuscript(call, users, share=False)
    alice, bob = page_for('alice'), page_for('bob')
    errors = []
    for page in (alice, bob):
        page.on('pageerror', lambda error: errors.append(str(error)))
    open_editor(alice, base, project['id'])
    expect(alice.get_by_role('link', name='Download LaTeX ZIP', exact=True)).to_be_visible()
    alice.locator('#shareProject').click()
    alice.locator('#sharingSearch').fill('Bob')
    alice.get_by_role('button', name='Add Bob Writer (@bob)', exact=True).click()
    expect(alice.locator('#sharingMembers')).to_contain_text('Bob Writer')
    assert alice.locator('#sharingLink').input_value() == base + '/write?id=' + project['id']
    alice.locator('#sharingClose').click()
    expect(alice.locator('#projectShared')).to_be_visible()
    alice.set_viewport_size({'width': 1365, 'height': 950})
    alice.screenshot(path=str(tmp / 'originwrite-collaboration-toolbar.png'), full_page=True)
    alice.locator('#shareProject').click()
    expect(alice.locator('#sharingMembers')).to_contain_text('Bob Writer (@bob)')
    alice.screenshot(path=str(tmp / 'originwrite-sharing-dialog.png'), full_page=True)
    alice.locator('#sharingClose').click()
    bob.goto(base + '/write')
    expect(bob.locator('.manuscript-row .sharing-badge')).to_have_text('Shared')
    bob.locator('.manuscript-row').click()
    bob.locator('#source .cm-content').wait_for()
    expect(bob.locator('#projectLibrary')).to_be_disabled()
    bob.locator('#insertCitation').click()
    bob.get_by_role('button', name='Insert citation Example2026', exact=True).click()
    expect(bob.locator('#source .cm-content')).to_contain_text('\\cite{Example2026}')
    expect(bob.locator('#savedState')).to_have_text('All changes saved')
    draft = SOURCE.replace('Middle observation.', 'Updated by Bob before downloading.')
    bob.locator('#source .cm-content').fill(draft)
    with bob.expect_download() as download:
        bob.get_by_role('link', name='Download LaTeX ZIP', exact=True).click()
    with zipfile.ZipFile(download.value.path()) as archive:
        assert archive.read('main.tex').decode() == draft
        assert archive.read('sections/notes.tex').decode() == 'A supplementary section.'
        assert b'Example2026' in archive.read('references.bib')
    assert call('GET', '/api/writing?id=' + project['id'])['files']['main.tex'] == draft
    bob.locator('#shareProject').click()
    expect(bob.locator('#sharingSearch')).to_be_hidden()
    assert bob.get_by_role('button', name='Remove Bob Writer (@bob)').count() == 0
    bob.locator('#sharingClose').click()
    alice.locator('#shareProject').click()
    alice.get_by_role('button', name='Remove Bob Writer (@bob)', exact=True).click()
    alice.locator('#sharingClose').click()
    expect(bob.locator('#collaborationConflict')).to_be_visible(timeout=15000)
    expect(bob.locator('#conflictMessage')).to_contain_text('access is no longer available')
    expect(bob.locator('#source .cm-content')).to_contain_text('Updated by Bob before downloading.')
    alice.screenshot(path=str(tmp / 'originwrite-sharing-owner.png'), full_page=True)
    assert not errors


def test_two_browsers_merge_separate_passages_and_receive_live_updates(collaboration_browser):
    call, base, users, page_for, tmp, expect = collaboration_browser
    project = manuscript(call, users)
    alice, bob = page_for('alice'), page_for('bob')
    open_editor(alice, base, project['id'])
    open_editor(bob, base, project['id'])
    alice.locator('#source .cm-content').fill(SOURCE.replace('First observation.', 'Alice measured the first outcome.'))
    bob.locator('#source .cm-content').fill(SOURCE.replace('Final observation.', 'Bob measured the final outcome.'))
    for page in (alice, bob):
        expect(page.locator('#savedState')).to_have_text('All changes saved', timeout=15000)
        expect(page.locator('#source .cm-content')).to_contain_text('Alice measured the first outcome.', timeout=15000)
        expect(page.locator('#source .cm-content')).to_contain_text('Bob measured the final outcome.', timeout=15000)
        expect(page.locator('#collaborationConflict')).to_be_hidden()
    saved = call('GET', '/api/writing?id=' + project['id'])
    assert 'Alice measured the first outcome.' in saved['files']['main.tex']
    assert 'Bob measured the final outcome.' in saved['files']['main.tex']
    bob.screenshot(path=str(tmp / 'originwrite-live-collaboration.png'), full_page=True)


def test_typing_while_a_merged_save_response_is_delayed_is_not_lost(collaboration_browser):
    call, base, users, page_for, _, expect = collaboration_browser
    project = manuscript(call, users)
    alice = page_for('alice')
    open_editor(alice, base, project['id'])
    held = []
    pending_text = SOURCE.replace('First observation.', 'Alice first result.')
    after_typing = pending_text.replace('Final observation.', 'Alice continued typing.')
    remote_text = SOURCE.replace('Middle observation.', 'Bob middle result.')

    def intercept(route):
        payload = route.request.post_data_json if route.request.method == 'POST' else {}
        if payload.get('action') != 'save_project' or held:
            route.continue_()
            return
        current = call('GET', '/api/writing?id=' + project['id'], 'bob')
        call('POST', who='bob', payload={**current, 'action': 'save_project', 'files': {**current['files'], 'main.tex': remote_text}})
        response = route.fetch()
        held.append((route, response))

    alice.route('**/api/writing', intercept)
    alice.locator('#source .cm-content').fill(pending_text)
    expect(alice.locator('#savedState')).to_have_text('Saving…')
    alice.wait_for_function('document.querySelector("#savedState").textContent === "Saving…"')
    for _ in range(30):
        if held:
            break
        alice.wait_for_timeout(50)
    assert held
    alice.locator('#source .cm-content').fill(after_typing)
    held[0][0].fulfill(response=held[0][1])
    expect(alice.locator('#savedState')).to_have_text('All changes saved', timeout=15000)
    for fragment in ('Alice first result.', 'Bob middle result.', 'Alice continued typing.'):
        expect(alice.locator('#source .cm-content')).to_contain_text(fragment)
        assert fragment in call('GET', '/api/writing?id=' + project['id'])['files']['main.tex']
    expect(alice.locator('#collaborationConflict')).to_be_hidden()


def test_conflicting_draft_is_kept_downloadable_and_reloaded_only_explicitly(collaboration_browser):
    call, base, users, page_for, _, expect = collaboration_browser
    project = manuscript(call, users)
    bob = page_for('bob')
    open_editor(bob, base, project['id'])
    draft = SOURCE.replace('First observation.', 'Bob conflicting first conclusion.')
    bob.locator('#source .cm-content').fill(draft)
    saved = call('POST', payload={**project, 'action': 'save_project', 'files': {**project['files'], 'main.tex': SOURCE.replace('First observation.', 'Alice different first conclusion.')}})
    expect(bob.locator('#collaborationConflict')).to_be_visible(timeout=15000)
    expect(bob.locator('#source .cm-content')).to_contain_text('Bob conflicting first conclusion.')
    with bob.expect_download() as download:
        bob.locator('#conflictDraft').click()
    assert json.loads(Path(download.value.path()).read_text())['files']['main.tex'] == draft
    assert call('GET', '/api/writing?id=' + project['id'])['files'] == saved['files']
    bob.once('dialog', lambda dialog: dialog.accept())
    bob.locator('#conflictReload').click()
    expect(bob.locator('#collaborationConflict')).to_be_hidden()
    expect(bob.locator('#source .cm-content')).to_contain_text('Alice different first conclusion.')
    expect(bob.locator('#savedState')).to_have_text('All changes saved')


def test_shared_link_sign_in_returns_to_the_authorized_project(collaboration_browser):
    call, base, users, page_for, _, expect = collaboration_browser
    project = manuscript(call, users)
    page = page_for(None)
    page.goto(base + '/write?id=' + project['id'])
    expect(page).to_have_url(re.compile(r'/login\?next='))
    page.locator('#loginIdentity').fill('bob')
    page.locator('#loginPassword').fill(PASSWORD)
    page.locator('#loginSubmit').click()
    expect(page).to_have_url(base + '/write?id=' + project['id'])
    page.locator('#source .cm-content').wait_for()
    expect(page.locator('#projectShared')).to_be_visible()


def test_live_update_waits_for_composition_before_applying_remote_text(collaboration_browser):
    call, base, users, page_for, _, expect = collaboration_browser
    project = manuscript(call, users)
    bob = page_for('bob')
    open_editor(bob, base, project['id'])
    code = bob.locator('#source .cm-content')
    code.focus()
    code.dispatch_event('compositionstart')
    call('POST', payload={**project, 'action': 'save_project', 'files': {**project['files'], 'main.tex': SOURCE.replace('Middle observation.', 'Remote observation during input.')}})
    bob.wait_for_timeout(2900)
    expect(code).not_to_contain_text('Remote observation during input.')
    expect(bob.locator('#collaborationConflict')).to_be_hidden()
    code.dispatch_event('compositionend')
    expect(code).to_contain_text('Remote observation during input.', timeout=12000)
    expect(bob.locator('#collaborationConflict')).to_be_hidden()


def test_oversized_remote_change_pauses_sync_without_replacing_open_draft(collaboration_browser):
    call, base, users, page_for, _, expect = collaboration_browser
    project = manuscript(call, users)
    bob = page_for('bob')
    open_editor(bob, base, project['id'])
    call('POST', payload={**project, 'action': 'save_project', 'files': {**project['files'], 'main.tex': 'Z' * 1_100_001}})
    expect(bob.locator('#collaborationConflict')).to_be_visible(timeout=15000)
    expect(bob.locator('#conflictMessage')).to_contain_text('too large to safely apply')
    expect(bob.locator('#source .cm-content')).to_contain_text('First observation.')
    expect(bob.locator('#savedState')).to_have_text('Draft kept · saving paused')


def test_local_workspace_keeps_sharing_disabled(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        page.goto(base + '/write')
        page.get_by_role('button', name='+ New manuscript', exact=True).click()
        page.locator('#newName').fill('Local manuscript')
        page.get_by_role('button', name='Create', exact=True).click()
        playwright.expect(page.locator('#shareProject')).to_be_disabled()
        playwright.expect(page.locator('#shareProject')).to_have_attribute('title', 'Sharing is available when signed in to a shared server.')
        browser.close()


def test_client_rebase_conservative_ambiguity_unicode_and_different_files():
    script = r'''
import {rebaseDraft} from './assets/workspace/writing-collaboration.mjs';
import {diffTextChanges} from './assets/workspace/latex-editor.mjs';
const p = text => ({files: {'main.tex': text}, main: 'main.tex', collection: '', revision: 1});
for (const [base, local, remote] of [['aaa','aa','aba'], ['aaab','aab','aabba'], ['😀😀😀','😀😀','😀😺😀']]) {
  let conflicted = false;
  try { rebaseDraft(p(base),p(local),p(remote),diffTextChanges); } catch { conflicted = true; }
  if (!conflicted) throw Error('Ambiguous edit silently merged');
}
let result = rebaseDraft(p('First\nMiddle\nLast'), p('FIRST\nMiddle\nLast'), p('First\nMiddle\nLAST'), diffTextChanges);
if (result.files['main.tex'] !== 'FIRST\nMiddle\nLAST') throw Error('Separate passages lost');
result = rebaseDraft(p('Base'), {...p('Local'),files:{'main.tex':'Local'}}, {...p('Base'),files:{'main.tex':'Base','new.tex':'Remote'}}, diffTextChanges);
if (result.files['main.tex'] !== 'Local' || result.files['new.tex'] !== 'Remote') throw Error('Different files lost');
const deleted={...p('Base'),files:{}};
let conflicted=false;try {rebaseDraft(p('Base'),deleted,p('Changed'),diffTextChanges);}catch{conflicted=true;}
if(!conflicted)throw Error('Delete/edit conflict lost');
console.log('ok');
'''
    result = subprocess.run([node_binary(), '--input-type=module', '-e', script], cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'ok'


def test_client_rebase_never_accepts_ambiguous_random_reference_cases():
    rng = random.Random(947)
    cases = []
    for _ in range(2000):
        before = ''.join(rng.choice('abcα😀\n') for _ in range(rng.randrange(1, 35)))
        changed = []
        for _ in range(2):
            start = rng.randrange(len(before) + 1)
            end = rng.randrange(start, len(before) + 1)
            inserted = ''.join(rng.choice('abcα😀\n') for _ in range(rng.randrange(5)))
            changed.append(before[:start] + inserted + before[end:])
        try:
            expected = merge_text(before, *changed)
        except MergeConflict:
            expected = None
        cases.append([before, *changed, expected])
    script = r'''
import {rebaseDraft} from './assets/workspace/writing-collaboration.mjs';
import {diffTextChanges} from './assets/workspace/latex-editor.mjs';
let input='';for await(const chunk of process.stdin)input+=chunk;
const project=text=>({files:{'main.tex':text},main:'main.tex',collection:'',revision:1});
for(const [base,ours,theirs,expected] of JSON.parse(input)){
  let merged;try{merged=rebaseDraft(project(base),project(ours),project(theirs),diffTextChanges).files['main.tex'];}catch{continue;}
  if(expected===null || merged!==expected)throw Error(JSON.stringify({base,ours,theirs,expected,merged}));
}
console.log('ok');
'''
    result = subprocess.run([node_binary(), '--input-type=module', '-e', script], cwd=ROOT, input=json.dumps(cases), text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
