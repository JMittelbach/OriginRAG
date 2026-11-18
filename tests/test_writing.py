import base64
import io
import json
import os
from pathlib import Path
import shutil
import threading
import zipfile

import pytest

from originrag.writing import WritingStore, WritingConflict, STARTER, unpack_project, validate_files
from originrag.writing_compile import diagnostics, compile_project, COMPILE_SLOT, CompilerBusy
import originrag.writing_compile as compiler

CATALOGUE = [{'paper_id': 'P1', 'title': 'A study & its results', 'author': 'Example', 'year': '2026'}]


def create(store, action='create_project', **kwargs):
    return store.change({'action': action, 'name': 'My project', **kwargs}, CATALOGUE)


def test_project_revision_export_and_isolation(tmp_path):
    alice, bob = WritingStore(tmp_path / 'alice'), WritingStore(tmp_path / 'bob')
    p = create(alice)
    assert p['files']['main.tex'] == STARTER
    assert alice.get(p['id']) == p
    with pytest.raises(KeyError):
        bob.get(p['id'])
    payload = {'action': 'save_project', **p, 'files': {**p['files'], 'sections/methods.tex': 'Methods'}}
    saved = alice.change(payload, CATALOGUE)
    assert saved['revision'] == 2
    with pytest.raises(WritingConflict):
        alice.change(payload, CATALOGUE)
    with zipfile.ZipFile(io.BytesIO(alice.export(p['id']))) as archive:
        assert archive.read('sections/methods.tex') == b'Methods'
    assert len(alice.snapshot()['projects']) == 1 and bob.snapshot()['projects'] == []
    assert alice.path.stat().st_mode & 0o777 == 0o600


def test_collection_membership_and_bib(tmp_path):
    s = WritingStore(tmp_path)
    c = create(s, 'create_collection')
    c = s.change({'action': 'collection_paper', 'id': c['id'], 'revision': c['revision'], 'paper_id': 'P1'}, CATALOGUE)
    assert c['papers'] == ['P1']
    assert r'A study \& its results' in s.collection_bib(c['id'], CATALOGUE)
    p = create(s)
    p = s.change({**p, 'action': 'save_project', 'collection': c['id']}, CATALOGUE)
    assert p['collection'] == c['id']
    with pytest.raises(ValueError):
        s.change({**p, 'action': 'save_project', 'collection': 'f' * 32}, CATALOGUE)


@pytest.mark.parametrize('name', ['../secret.tex', '/tmp/file.tex', 'a/../../x.tex', '.hidden.tex', 'a//b.tex', 'a\\b.tex', 'x.sh', '-x.tex'])
def test_paths_rejected(name):
    with pytest.raises(ValueError):
        validate_files({name: 'x'}, name)


def test_limits_and_file_folder_collisions():
    with pytest.raises(ValueError):
        validate_files({'main.tex': 'x' * 12_000_001}, 'main.tex')
    with pytest.raises(ValueError):
        validate_files({'main.tex': '', 'main.tex/child.tex': ''}, 'main.tex')
    with pytest.raises(ValueError):
        validate_files({'main.tex': '', 'MAIN.tex': ''}, 'main.tex')
    with pytest.raises(ValueError):
        validate_files({'main.tex': ''}, [])


def test_zip_validation_and_roundtrip(tmp_path):
    store = WritingStore(tmp_path)
    p = create(store)
    files, main = unpack_project(base64.b64encode(store.export(p['id'])))
    assert main == 'main.tex' and files == p['files']
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('../evil.tex', 'test')
    with pytest.raises(ValueError):
        unpack_project(base64.b64encode(stream.getvalue()))


def test_compile_busy_does_not_start_engine(monkeypatch):
    monkeypatch.setattr(compiler, 'QUEUE_WAIT_SECONDS', 0)
    for _ in range(COMPILE_SLOT.capacity):
        COMPILE_SLOT.acquire()
    try:
        with pytest.raises(CompilerBusy):
            compile_project({'files': {'main.tex': ''}, 'main': 'main.tex', 'revision': 1})
    finally:
        for _ in range(COMPILE_SLOT.capacity):
            COMPILE_SLOT.release()


def test_diagnostics():
    result = diagnostics('main.tex:14: Undefined control sequence.\nwarning: a warning')
    assert result[0] == {'file': 'main.tex', 'line': 14, 'message': 'Undefined control sequence.', 'severity': 'error'}
    assert result[1]['line'] == 0


def test_rootless_compile_command_is_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(compiler, 'rootless_tools', lambda: ('/tools/bwrap', '/tools/tectonic', '/tools/tex-cache'))
    command = compiler.rootless_command(tmp_path, 'main.tex')
    assert '--unshare-all' in command and '--clearenv' in command
    assert '--new-session' in command and '--die-with-parent' in command
    assert '--share-net' not in command
    assert command[command.index('--remount-ro') + 1] == '/'
    assert '--only-cached' in command and '--untrusted' in command
    assert 'RLIMIT_AS' in command[3]
    assert str(Path.home()) not in command


def test_writing_api_accounts_and_csrf(tmp_path, monkeypatch):
    import test_web_chat as h
    web = h.web_chat
    monkeypatch.setattr(web, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(web, 'library_catalog', lambda: CATALOGUE)
    auth = h.AuthStore(tmp_path / 'auth.sqlite')
    for name in ['alice', 'bob']:
        auth.create_user(name, 'quiet-lantern-river-orbit-47')
    registry = web.AuthenticatedWebChatRegistry(web.WebChatSettings(backend='template'), tmp_path / 'sessions')
    server = web.create_server('127.0.0.1', 0, web.build_handler(None, auth_store=auth, app_registry=registry))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    port = server.server_address[1]
    try:
        alice = h.login_headers(port, 'alice', 'quiet-lantern-river-orbit-47')[0]
        bob = h.login_headers(port, 'bob', 'quiet-lantern-river-orbit-47')[0]
        payload = {'action': 'create_project', 'name': 'Private manuscript'}
        r = h.http_request(port, 'POST', '/api/writing', headers=alice, payload=payload)
        assert r[0] == 200
        key = json.loads(r[2])['id']
        assert h.http_request(port, 'GET', '/api/writing?id=' + key, headers=bob)[0] == 404
        assert h.http_request(port, 'GET', '/api/writing/export?id=' + key, headers=bob)[0] == 404
        assert h.http_request(port, 'GET', '/api/writing')[0] == 401
        assert h.http_request(port, 'POST', '/api/writing', headers={k: v for k, v in alice.items() if k.lower() != 'x-csrf-token'}, payload=payload)[0] == 403
        for page in ['/workspace', '/write', '/library', '/bookshelf', '/chat']:
            assert h.http_request(port, 'GET', page, headers=alice)[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=3)


@pytest.fixture
def workspace_server(tmp_path, monkeypatch):
    import test_web_chat as h
    web = h.web_chat
    root = Path(__file__).resolve().parents[1]
    for folder in ['workspace', 'vendor/pdfjs', 'writing_templates']:
        shutil.copytree(root / 'assets' / folder, tmp_path / 'assets' / folder, ignore=shutil.ignore_patterns('editor-build'))
    monkeypatch.setattr(web, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(web, 'library_catalog', lambda: CATALOGUE)
    app = web.WebChatApp(web.WebChatSettings(backend='template'), tmp_path / 'sessions')
    server = web.create_server('127.0.0.1', 0, web.build_handler(app))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f'http://127.0.0.1:{server.server_address[1]}', tmp_path
    server.shutdown()
    server.server_close()
    t.join(timeout=3)


def test_browser_project_library_edit_and_reopen(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, tmp = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(base + '/workspace')
        page.wait_for_url('**/chat')
        page.locator('#guideSkip').click()
        page.locator('#globalNavigation a[href="/library"]').click()
        page.get_by_role('button', name='+ Collection').click()
        page.locator('#newName').fill('My study')
        page.get_by_role('button', name='Create', exact=True).click()
        page.get_by_role('button', name='My study', exact=True).wait_for()
        page.locator('[aria-label="Destination collection"]').select_option(label='My study')
        playwright.expect(page.locator('#notice')).to_contain_text('Added')
        page.get_by_role('button', name='My study', exact=True).click()
        playwright.expect(page.locator('#libraryCount')).to_have_text('1 paper')
        page.get_by_role('link', name='Quill', exact=True).click()
        page.get_by_role('button', name='+ New manuscript').click()
        page.locator('#newName').fill('Demo manuscript')
        page.get_by_role('button', name='Create', exact=True).click()
        page.locator('#source').wait_for()
        page.locator('#source .cm-content').fill('\\documentclass{article}\n\\begin{document}\nHello Quill.\n\\end{document}')
        playwright.expect(page.locator('#savedState')).to_have_text('All changes saved')
        page.locator('.project-settings > summary').click()
        page.locator('#projectLibrary').select_option(label='My study')
        page.get_by_role('button', name='Manage references').click()
        page.locator('#referencesPreview').click()
        page.locator('#referencesApply').click()
        playwright.expect(page.locator('#notice')).to_contain_text('Bibliography synced')
        page.locator('#referencesClose').click()
        page.locator('[data-file="references.bib"]').click()
        playwright.expect(page.locator('#source .cm-content')).to_contain_text('A study')
        playwright.expect(page.locator('#savedState')).to_have_text('All changes saved')
        page.reload()
        playwright.expect(page.locator('#source .cm-content')).to_contain_text('Hello Quill')
        assert page.locator('#projectLibrary option:checked').text_content() == 'My study'
        page.screenshot(path=str(tmp / 'originwrite-editor.png'), full_page=True)
        assert not errors
        browser.close()


def test_writer_resizing_syntax_history_outline_and_mobile(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, tmp = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 1000})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(base + '/write')
        page.get_by_role('button', name='+ New manuscript').click()
        page.locator('#newName').fill('A manuscript in progress')
        page.get_by_role('button', name='Create', exact=True).click()
        code = page.locator('#source .cm-content')
        code.wait_for()
        text = '\\documentclass{article}\n% A comment, not a command\n\\begin{document}\n\\section{Introduction}\nA clear research question.\n\\section{Methods}\nThe study design.\n\\end{document}'
        code.fill(text)
        playwright.expect(page.locator('#savedState')).to_have_text('All changes saved')
        playwright.expect(page.locator('.tex-command').first).to_have_text('\\documentclass')
        playwright.expect(page.locator('.tex-comment').first).to_contain_text('A comment')
        page.locator('#outline').get_by_role('button', name='Methods', exact=True).click()
        playwright.expect(page.locator('#cursorPosition')).to_contain_text('Ln 6')
        page.get_by_role('button', name='Find and replace', exact=True).click()
        page.locator('.cm-search input[name=search]').fill('research')
        page.locator('.cm-search button[name=close]').click()

        def drag(handle, dx=0, dy=0):
            box = page.locator(handle).bounding_box()
            x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + dx, y + dy, steps=8)
            page.mouse.up()

        width = page.locator('#filesPane').bounding_box()['width']
        drag('#filesResize', dx=70)
        assert page.locator('#filesPane').bounding_box()['width'] > width + 60
        page.locator('#filesResize').focus()
        page.keyboard.press('ArrowLeft')
        assert page.locator('#filesPane').bounding_box()['width'] < width + 65
        before = page.locator('#sourcePane').bounding_box()['width']
        drag('#panesResize', dx=110)
        assert page.locator('#sourcePane').bounding_box()['width'] > before + 90
        page.locator('#toggleFiles').click()
        playwright.expect(page.locator('#filesPane')).not_to_be_visible()
        playwright.expect(page.locator('#toggleFiles')).to_have_attribute('aria-expanded', 'false')
        page.locator('#swapPanes').click()
        assert page.locator('#sourcePane').bounding_box()['x'] > page.locator('#previewPane').bounding_box()['x']
        page.locator('#editorTheme').click()
        playwright.expect(page.locator('body')).to_have_attribute('data-editor-theme', 'dark')
        page.reload()
        code.wait_for()
        playwright.expect(page.locator('#filesPane')).not_to_be_visible()
        playwright.expect(page.locator('body')).to_have_attribute('data-editor-theme', 'dark')
        assert page.locator('#sourcePane').bounding_box()['x'] > page.locator('#previewPane').bounding_box()['x']
        page.locator('#layoutMode').select_option('code')
        playwright.expect(page.locator('#previewPane')).not_to_be_visible()
        page.locator('#layoutMode').select_option('pdf')
        playwright.expect(page.locator('#sourcePane')).not_to_be_visible()
        page.locator('#layoutMode').select_option('split')
        page.locator('#toggleFiles').click()

        code.click()
        page.keyboard.press('ControlOrMeta+End')
        page.keyboard.insert_text('\nFirst-file-only edit')
        page.locator('#addFile').click()
        page.locator('#newName').fill('sections/results.tex')
        page.get_by_role('button', name='Create', exact=True).click()
        playwright.expect(page.locator('#fileLabel')).to_have_text('sections/results.tex')
        code.click()
        page.keyboard.insert_text('Second-file-only edit')
        page.get_by_role('button', name='Undo', exact=True).click()
        playwright.expect(code).not_to_contain_text('Second-file-only edit')
        page.get_by_role('button', name='Redo', exact=True).click()
        playwright.expect(code).to_contain_text('Second-file-only edit')
        page.locator('[data-file="main.tex"]').click()
        playwright.expect(code).to_contain_text('First-file-only edit')
        page.get_by_role('button', name='Undo', exact=True).click()
        playwright.expect(code).not_to_contain_text('First-file-only edit')
        playwright.expect(code).to_contain_text('A clear research question')
        page.locator('.writer-menu > summary').click()
        page.locator('#resetLayout').click()
        page.locator('.writer-menu > summary').click()
        page.locator('#editorTheme').click()
        page.screenshot(path=str(tmp / 'writer-desktop.png'), full_page=True)

        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('#hideFiles').click()
        playwright.expect(page.locator('#panesResize')).to_have_attribute('aria-orientation', 'horizontal')
        before = page.locator('#sourcePane').bounding_box()['height']
        drag('#panesResize', dy=35)
        assert page.locator('#sourcePane').bounding_box()['height'] > before + 20
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('#layoutMode').select_option('pdf')
        playwright.expect(page.locator('#previewPane')).to_be_visible()
        page.locator('#layoutMode').select_option('code')
        playwright.expect(page.locator('#sourcePane')).to_be_visible()
        page.screenshot(path=str(tmp / 'writer-mobile.png'), full_page=True)
        playwright.expect(page.locator('#savedState')).to_have_text('All changes saved')
        assert not errors
        browser.close()


@pytest.mark.skipif(os.environ.get('ORIGINWRITE_TEST_COMPILER') != '1', reason='Explicit opt-in for an installed isolated compiler')
def test_real_compile_errors_and_private_file_isolation(tmp_path):
    import fitz
    source = r'\documentclass{article}\begin{document}Hello Quill.\end{document}'
    project = {'files': {'main.tex': source}, 'main': 'main.tex', 'revision': 1}
    result = compile_project(project)
    assert result['ok'], result['log']
    with fitz.open(stream=result['pdf'], filetype='pdf') as pdf:
        assert 'Hello Quill.' in pdf[0].get_text()
    bad = compile_project({**project, 'files': {'main.tex': source.replace('Hello Quill.', r'\undefinedQuillCommand')}})
    assert not bad['ok'] and bad['diagnostics'] and not bad['pdf']
    secret = tmp_path / 'private.tex'
    secret.write_text('PRIVATE-CONTENT-MUST-NOT-LEAK')
    attempt = source.replace('Hello Quill.', r'\input{' + str(secret) + '}')
    blocked = compile_project({**project, 'files': {'main.tex': attempt}})
    assert not blocked['ok'] and not blocked['pdf']
    outside = tmp_path / 'must-not-exist.txt'
    attempt = source.replace('Hello Quill.', r'\immediate\write18{touch ' + str(outside) + '}No shell escape.')
    compile_project({**project, 'files': {'main.tex': attempt}})
    assert not outside.exists()


@pytest.mark.skipif(os.environ.get('ORIGINWRITE_TEST_COMPILER') != '1', reason='Explicit opt-in for a real compiler')
def test_browser_auto_compile_and_errors(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, tmp = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1550, 'height': 1050})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base + '/write')
        page.get_by_role('button', name='+ New manuscript').click()
        page.locator('#newName').fill('A manuscript in progress')
        page.get_by_role('button', name='Create', exact=True).click()
        source = '\\documentclass{article}\n\\begin{document}\n\\section{A first result}\nHello Quill.\n\\end{document}'
        page.locator('#source .cm-content').fill(source)
        page.locator('#autoCompile').check()
        playwright.expect(page.locator('#pdfPages canvas')).to_have_count(1, timeout=30000)
        playwright.expect(page.locator('#previewState')).to_contain_text('PDF · revision')
        page.screenshot(path=str(tmp / 'originwrite-compiled.png'), full_page=True)
        page.locator('#source .cm-content').fill(source.replace('Hello Quill.', '\\undefinedQuillCommand'))
        playwright.expect(page.locator('#previewState')).to_contain_text('Compilation failed', timeout=30000)
        playwright.expect(page.locator('#compileLog')).to_contain_text('Undefined control sequence')
        assert page.locator('#pdfPages canvas').count() == 1
        diagnostic = page.locator('#problems button').filter(has_text='main.tex:').first
        if diagnostic.count():
            page.locator('#layoutMode').select_option('pdf')
            diagnostic.click()
            playwright.expect(page.locator('#sourcePane')).to_be_visible()
        page.locator('#layoutMode').select_option('split')
        page.locator('#source .cm-content').fill(source.replace('Hello Quill.', 'A corrected result.'))
        try:
            playwright.expect(page.locator('#previewState')).to_contain_text('PDF · revision', timeout=10000)
        except AssertionError:
            pytest.fail(page.locator('#notice').inner_text() + ' | ' + str(errors))
        assert not errors
        browser.close()
