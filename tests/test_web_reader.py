import hashlib
import json
from pathlib import Path
import shutil
import threading

import fitz
import pytest

import test_web_chat as helpers
from test_library_pdf import metadata
from test_reader_notes import MARK


@pytest.fixture
def reader_server(tmp_path, monkeypatch, request):
    web = helpers.web_chat
    root = Path(__file__).resolve().parents[1]
    for folder in ('reader', 'vendor/pdfjs'):
        shutil.copytree(root / 'assets' / folder, tmp_path / 'assets' / folder)
    bib = tmp_path / 'bib'
    bib.mkdir()
    configuration = getattr(request, 'param', False)
    page_count = configuration.get('pages', 2) if isinstance(configuration, dict) else 2
    with fitz.open() as document:
        texts = ['Synthetic paper: a useful finding.', 'Another page with further evidence.\nA useful finding. Further useful details.']
        texts.extend(f'Page {number}: further observations.' for number in range(3, page_count + 1))
        for number, text in enumerate(texts, 1):
            width, height = (842, 595) if page_count > 2 and number % 4 == 0 else (595, 842)
            document.new_page(width=width, height=height).insert_text((72, 72), text, fontsize=16)
        document.set_toc([[1, 'Overview', 1], [1, 'Further evidence', 2]])
        content = document.tobytes()
    (bib / 'paper.pdf').write_bytes(content)
    metadata(tmp_path / 'data/metadata/paper_registry.tsv', [('P1', 'paper.pdf')])
    monkeypatch.setattr(web, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(web, 'get_bib_dir', lambda: bib)
    monkeypatch.setattr(web, 'load_project_env', lambda *args: None)
    monkeypatch.setattr(web, 'library_catalog', lambda: ({'paper_id': 'P1', 'title': 'Synthetic reader paper'},))
    auth_enabled = configuration.get('auth', False) if isinstance(configuration, dict) else configuration
    auth = helpers.AuthStore(tmp_path / 'auth.sqlite') if auth_enabled else None
    if auth:
        for user in ('alice', 'bob'):
            auth.create_user(user, 'quiet-lantern-river-orbit-47')
    settings = web.WebChatSettings(backend='template')
    registry = web.AuthenticatedWebChatRegistry(settings, tmp_path / 'sessions') if auth else None
    app = None if auth else web.WebChatApp(settings, tmp_path / 'sessions')
    handler = web.build_handler(app, auth_store=auth, app_registry=registry, secure_cookies=False)
    server = web.create_server('127.0.0.1', 0, handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], hashlib.sha256(content).hexdigest(), tmp_path, auth
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


@pytest.mark.parametrize('reader_server', [False, True], indirect=True)
def test_reader_http_authorization_and_note_isolation(reader_server):
    port, fingerprint, tmp, auth = reader_server
    alice = helpers.login_headers(port, 'alice', 'quiet-lantern-river-orbit-47')[0] if auth else {}
    url = '/api/reader/notes?paper_id=P1&fingerprint=' + fingerprint
    post = {'paper_id': 'P1', 'fingerprint': fingerprint, 'action': 'add', 'mark': MARK}
    assert helpers.http_request(port, 'GET', '/reader?paper_id=P1', headers=alice)[0] == 200
    assert helpers.http_request(port, 'GET', '/api/reader/paper?paper_id=P1', headers=alice)[0] == 200
    assert helpers.http_request(port, 'GET', url, headers=alice)[0] == 200
    status, _, raw, _ = helpers.http_request(port, 'POST', '/api/reader/notes', headers=alice, payload=post)
    result = json.loads(raw)
    assert status == 200 and len(result['items']) == 1
    assert json.loads(helpers.http_request(port, 'GET', url, headers=alice)[2])['items'] == result['items']
    assert helpers.http_request(port, 'GET', url + '&owner=bob', headers=alice)[0] == 400
    assert helpers.http_request(port, 'POST', '/api/reader/notes', headers=alice, payload={**post, 'paper_id': 'missing'})[0] == 404
    if auth:
        bob = helpers.login_headers(port, 'bob', 'quiet-lantern-river-orbit-47')[0]
        assert json.loads(helpers.http_request(port, 'GET', url, headers=bob)[2])['items'] == []
        assert helpers.http_request(port, 'GET', url)[0] == 401
        assert helpers.http_request(port, 'POST', '/api/reader/notes', headers={k:v for k,v in alice.items() if k.lower() != 'x-csrf-token'}, payload=post)[0] == 403
    else:
        assert helpers.http_request(port, 'POST', '/api/reader/notes', headers={'Origin': 'https://evil.example'}, payload=post)[0] == 403


@pytest.mark.parametrize('reader_server', [False, True], indirect=True)
def test_real_browser_can_read_highlight_save_and_reopen(reader_server):
    playwright = pytest.importorskip('playwright.sync_api')
    port, fingerprint, tmp, auth = reader_server
    base = f'http://127.0.0.1:{port}'
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        if auth:
            headers, _ = helpers.login_headers(port, 'alice', 'quiet-lantern-river-orbit-47')
            page.context.add_cookies([{'name': part.split('=', 1)[0], 'value': part.split('=', 1)[1], 'url': base} for part in headers['Cookie'].split('; ')])
        errors, external, console = [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: console.append(message.text))
        page.on('request', lambda req: external.append(req.url) if req.url.startswith(('http:', 'https:')) and not req.url.startswith(base) else None)
        page.goto(base + '/reader?paper_id=P1')
        try:
            page.wait_for_selector('#fit:not([disabled])', timeout=20000)
        except Exception:
            pytest.fail(f"Reader did not load: {page.locator('#status').inner_text()}; errors={errors}; console={console}")
        assert page.locator('#textLayer').inner_text().startswith('Synthetic paper')
        assert page.locator('#pageCount').inner_text() == '/ 2'
        page.locator('#textLayer span').first.evaluate('el => {const range=document.createRange();range.selectNodeContents(el);const s=window.getSelection();s.removeAllRanges();s.addRange(range);} ')
        page.locator('#highlight').click()
        page.wait_for_selector('.note')
        page.locator('textarea').fill('Compare this finding with the next paper.')
        page.get_by_role('button', name='Save note', exact=True).click()
        playwright.expect(page.locator('#status')).to_have_text('Saved to your personal notes.')
        assert page.locator('.mark').count() >= 1
        before = page.locator('.mark').first.evaluate('el=>el.style.left')
        page.locator('#zoomIn').click()
        page.wait_for_selector('#fit:not([disabled])')
        assert page.locator('.mark').first.evaluate('el=>el.style.left') == before
        page.locator('#next').click()
        page.wait_for_selector('#fit:not([disabled])')
        playwright.expect(page.locator('#pageNumber')).to_have_value('2')
        assert page.locator('#highlights .mark').count() == 0
        assert page.locator('[data-page="1"] .mark').count() >= 1
        page.get_by_role('button', name='Page 1', exact=True).click()
        page.wait_for_selector('.mark')
        page.reload()
        page.wait_for_selector('#fit:not([disabled])')
        assert page.locator('textarea').input_value() == 'Compare this finding with the next paper.'
        page.route('**/api/reader/notes', lambda route: route.fulfill(status=503, content_type='application/json', body=json.dumps({'error': 'Temporary failure. Please retry.'})))
        page.locator('textarea').fill('An unsaved thought survives a failed request.')
        page.get_by_role('button', name='Save note', exact=True).click()
        playwright.expect(page.locator('#status')).to_have_text('Temporary failure. Please retry.')
        assert page.locator('textarea').input_value() == 'An unsaved thought survives a failed request.'
        page.unroute('**/api/reader/notes')
        page.get_by_role('button', name='Save note', exact=True).click()
        playwright.expect(page.locator('#status')).to_have_text('Saved to your personal notes.')
        page.screenshot(path=str(tmp / 'reader-desktop.png'), full_page=True)
        with page.expect_download() as downloaded:
            page.locator('#export').click()
        data = json.loads(Path(downloaded.value.path()).read_text())
        assert data['fingerprint'] == fingerprint and len(data['annotations']) == 1
        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('#fit').click()
        page.wait_for_selector('#fit:not([disabled])')
        page.screenshot(path=str(tmp / 'reader-mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.once('dialog', lambda dialog: dialog.accept())
        page.get_by_role('button', name='Delete', exact=True).click()
        page.wait_for_selector('.note', state='detached')
        assert not errors and not external
        browser.close()


def test_reader_search_contents_selection_toolbar_and_shortcuts(reader_server):
    playwright = pytest.importorskip('playwright.sync_api')
    port, _, tmp, _ = reader_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 950})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{port}/reader?paper_id=P1')
        page.wait_for_selector('#fit:not([disabled])')
        page.keyboard.press('Control+f')
        playwright.expect(page.locator('#searchQuery')).to_be_focused()
        page.locator('#searchQuery').fill('UsEfUl')
        page.locator('#searchQuery').press('Enter')
        playwright.expect(page.locator('#searchStatus')).to_have_text('1 / 3')
        page.wait_for_selector('.search-hit.active')
        assert page.locator('#pageNumber').input_value() == '1'
        page.locator('#searchNext').click()
        playwright.expect(page.locator('#searchStatus')).to_have_text('2 / 3')
        playwright.expect(page.locator('#pageNumber')).to_have_value('2')
        page.wait_for_selector('#fit:not([disabled])')
        assert page.locator('#searchHighlights .search-hit').count() == 2
        page.locator('#searchPrevious').click()
        playwright.expect(page.locator('#pageNumber')).to_have_value('1')
        page.wait_for_selector('#fit:not([disabled])')
        page.locator('#searchQuery').fill('absent phrase')
        page.locator('#searchQuery').press('Enter')
        playwright.expect(page.locator('#searchStatus')).to_have_text('No matches in selectable text.')
        assert page.locator('.search-hit').count() == 0
        page.locator('#searchQuery').press('Escape')
        playwright.expect(page.locator('#searchBar')).to_be_hidden()
        page.locator('#contents').click()
        page.get_by_role('button', name='Further evidence', exact=True).click()
        playwright.expect(page.locator('#pageNumber')).to_have_value('2')
        page.wait_for_selector('#fit:not([disabled])')
        page.locator('#textLayer span').first.evaluate('el=>{const r=document.createRange();r.selectNodeContents(el);const s=getSelection();s.removeAllRanges();s.addRange(r);}')
        page.get_by_role('button', name='Highlight in green', exact=True).click()
        page.wait_for_selector('.mark.mint')
        page.locator('#textLayer span').last.evaluate('el=>{const r=document.createRange();r.selectNodeContents(el);const s=getSelection();s.removeAllRanges();s.addRange(r);}')
        page.locator('#selectionNote').click()
        playwright.expect(page.locator('textarea').last).to_be_focused()
        page.locator('textarea').last.fill('A quick note from the selection toolbar.')
        page.locator('textarea').last.press('Control+s')
        playwright.expect(page.locator('#status')).to_have_text('Saved to your personal notes.')
        page.locator('#workspace').evaluate('el=>el.scrollTop+=document.querySelector("#page").getBoundingClientRect().top-el.getBoundingClientRect().top+250')
        old_fraction = page.locator('#workspace').evaluate('el=>(el.getBoundingClientRect().top-document.querySelector("#page").getBoundingClientRect().top+50)/document.querySelector("#page").clientHeight')
        page.locator('#zoomIn').click()
        page.wait_for_selector('#fit:not([disabled])')
        new_fraction = page.locator('#workspace').evaluate('el=>(el.getBoundingClientRect().top-document.querySelector("#page").getBoundingClientRect().top+50)/document.querySelector("#page").clientHeight')
        assert abs(new_fraction-old_fraction) < .005
        page.locator('#workspace').evaluate('el=>el.scrollTop=0')
        page.screenshot(path=str(tmp/'reader-improved.png'), full_page=True)
        assert not errors
        browser.close()


@pytest.mark.parametrize('reader_server', [False, True], indirect=True)
def test_copy_does_not_highlight_and_click_opens_comment(reader_server):
    playwright = pytest.importorskip('playwright.sync_api')
    port, _, tmp, auth = reader_server
    base = f'http://127.0.0.1:{port}'
    with playwright.sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width': 1440, 'height': 950}, permissions=['clipboard-read', 'clipboard-write'])
        if auth:
            headers, _ = helpers.login_headers(port, 'alice', 'quiet-lantern-river-orbit-47')
            page.context.add_cookies([{'name': item.split('=', 1)[0], 'value': item.split('=', 1)[1], 'url': base} for item in headers['Cookie'].split('; ')])
        posts, errors = [], []
        page.on('request', lambda req: posts.append(req.post_data_json) if req.url.endswith('/api/reader/notes') and req.method == 'POST' else None)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base+'/reader?paper_id=P1')
        page.wait_for_selector('#fit:not([disabled])')
        span=page.locator('#textLayer span').first
        box=span.bounding_box()
        start=(box['x']+1,box['y']+box['height']/2)
        end=(box['x']+box['width']-1,start[1])
        page.mouse.move(*start)
        page.mouse.down()
        page.mouse.move(*end, steps=15)
        page.mouse.up()
        page.wait_for_selector('#selectionTools:not([hidden])')
        assert not posts
        page.locator('#selectionCopy').click()
        playwright.expect(page.locator('#status')).to_have_text('Copied. No highlight was saved.')
        assert 'Synthetic paper' in page.evaluate('()=>navigator.clipboard.readText()')
        assert not posts and page.locator('.mark').count() == 0
        page.get_by_role('button',name='Highlight in red',exact=True).click()
        page.wait_for_selector('.mark.red')
        assert len(posts) == 1 and posts[0]['mark']['color'] == 'red'
        page.locator('#notesToggle').click()
        box=span.bounding_box()
        start=(box['x']+1,box['y']+box['height']/2)
        end=(box['x']+box['width']-1,start[1])
        point=(start[0]+20,start[1])
        page.mouse.click(*point)
        playwright.expect(page.locator('textarea')).to_be_focused()
        page.locator('textarea').fill('My comment on the red passage.')
        page.get_by_role('button',name='Save note',exact=True).click()
        playwright.expect(page.locator('#status')).to_have_text('Saved to your personal notes.')
        count=len(posts)
        page.locator('#notesToggle').click()
        page.mouse.dblclick(*point, delay=60)
        page.wait_for_selector('#selectionTools:not([hidden])')
        page.keyboard.press('Control+c')
        page.wait_for_timeout(500)
        playwright.expect(page.locator('#notesPanel')).to_be_hidden()
        assert page.evaluate('()=>getSelection().toString().length') > 0
        assert len(posts) == count
        page.evaluate('()=>getSelection().removeAllRanges()')
        page.mouse.move(*start)
        page.mouse.down()
        page.mouse.move(*end, steps=15)
        page.mouse.up()
        page.wait_for_timeout(500)
        playwright.expect(page.locator('#notesPanel')).to_be_hidden()
        assert len(posts) == count
        page.evaluate('()=>getSelection().removeAllRanges()')
        page.mouse.click(*point,button='right')
        page.keyboard.press('Escape')
        page.wait_for_timeout(500)
        playwright.expect(page.locator('#notesPanel')).to_be_hidden()
        page.evaluate('()=>getSelection().removeAllRanges()')
        page.mouse.click(*point)
        playwright.expect(page.locator('textarea')).to_be_focused()
        assert page.locator('textarea').input_value() == 'My comment on the red passage.'
        page.reload()
        page.wait_for_selector('.mark.red')
        assert page.locator('textarea').input_value() == 'My comment on the red passage.'
        assert not errors
        page.screenshot(path=str(tmp/'reader-click-comment.png'),full_page=True)
        browser.close()


def test_reader_search_normalization_limits_and_cancellation(reader_server):
    playwright = pytest.importorskip('playwright.sync_api')
    port, _, _, _ = reader_server
    with playwright.sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page()
        page.goto(f'http://127.0.0.1:{port}/reader?paper_id=P1')
        page.wait_for_selector('#fit:not([disabled])')
        result=page.evaluate('''async()=>{
          const {searchable,matches,navigation}=await import('/assets/reader/navigation.mjs');
          const normalized=searchable(['Mixed  CASE ', 'with\\nspaces', 'soft\\u00adhyphen']);
          window.readerTest={jumps:[],mode:'many'};
          navigation({getPage:()=>1,getZoom:()=>1,renderPage:async p=>readerTest.jumps.push(p),getPdf:()=>({numPages:1,getPage:async()=>({getTextContent:()=>readerTest.mode==='many'?Promise.resolve({items:[{str:'needle '.repeat(1001)}]}):new Promise(resolve=>readerTest.release=resolve)})})});
          return {text:normalized.text,mapping:normalized.locations.length,literal:matches('a.b aXb','a.b')};
        }''')
        expected = 'mixed case with spaces softhyphen'
        assert result == {'text': expected, 'mapping': len(expected), 'literal': [0]}
        page.locator('#find').click()
        page.locator('#searchQuery').fill('needle')
        page.locator('#searchQuery').press('Enter')
        playwright.expect(page.locator('#searchStatus')).to_have_text('1 / 1000 (partial results)')
        page.evaluate("()=>{readerTest.mode='slow';readerTest.jumps=[];}")
        page.locator('#searchQuery').fill('new phrase')
        # Rebind to use an empty text cache for the pending search.
        page.evaluate('''async()=>{const {navigation}=await import('/assets/reader/navigation.mjs');navigation({getPage:()=>1,getZoom:()=>1,renderPage:async p=>readerTest.jumps.push(p),getPdf:()=>({numPages:1,getPage:async()=>({getTextContent:()=>new Promise(resolve=>readerTest.release=resolve)})})});}''')
        page.locator('#searchQuery').press('Enter')
        playwright.expect(page.locator('#searchStatus')).to_have_text('Searching page 1 / 1…')
        page.locator('#searchClose').click()
        page.evaluate("async()=>{readerTest.release({items:[{str:'new phrase'}]});await new Promise(resolve=>setTimeout(resolve,20));}")
        assert page.evaluate('()=>readerTest.jumps') == []
        assert page.locator('#searchStatus').inner_text() == ''
        playwright.expect(page.locator('#searchBar')).to_be_hidden()
        browser.close()


def test_continuous_scrolling_keeps_page_specific_highlights(reader_server):
    playwright = pytest.importorskip('playwright.sync_api')
    port, _, tmp, _ = reader_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 950})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(f'http://127.0.0.1:{port}/reader?paper_id=P1')
        page.wait_for_selector('#fit:not([disabled])')
        assert page.locator('.reader-page').count() == 2
        page.locator('#textLayer span').first.evaluate('el=>{const r=document.createRange();r.selectNodeContents(el);const s=getSelection();s.removeAllRanges();s.addRange(r);}')
        page.get_by_role('button', name='Highlight in red', exact=True).click()
        page.wait_for_selector('.mark.red')
        view = page.locator('#workspace').bounding_box()
        page.mouse.move(view['x'] + view['width'] / 2, view['y'] + 150)
        page.mouse.wheel(0, 1700)
        playwright.expect(page.locator('#pageNumber')).to_have_value('2')
        page.wait_for_selector('#fit:not([disabled])')
        assert 'Another page' in page.locator('#textLayer').inner_text()
        page.locator('#textLayer span').first.evaluate('el=>{const r=document.createRange();r.selectNodeContents(el);const s=getSelection();s.removeAllRanges();s.addRange(r);}')
        page.get_by_role('button', name='Highlight in green', exact=True).click()
        page.wait_for_selector('[data-page="2"] .mark.mint')
        assert page.locator('[data-page="1"] .mark.red').count() > 0
        page.mouse.move(view['x'] + view['width'] / 2, view['y'] + 150)
        page.mouse.wheel(0, -2500)
        playwright.expect(page.locator('#pageNumber')).to_have_value('1')
        assert page.locator('#highlights .mark.red').count() > 0
        page.locator('#next').click()
        playwright.expect(page.locator('#pageNumber')).to_have_value('2')
        assert page.locator('#workspace').evaluate('el=>el.scrollTop') > 700
        with page.expect_download() as downloaded:
            page.locator('#export').click()
        notes = json.loads(Path(downloaded.value.path()).read_text())['annotations']
        assert {(m['page'], m['color']) for m in notes} == {(1, 'red'), (2, 'mint')}
        page.screenshot(path=str(tmp / 'reader-continuous.png'), full_page=True)
        assert not errors
        browser.close()


@pytest.mark.parametrize('reader_server', [{'pages': 28}], indirect=True)
def test_scrolling_long_mixed_size_pdf_releases_distant_canvases(reader_server):
    playwright = pytest.importorskip('playwright.sync_api')
    port, _, _, _ = reader_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 950})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(f'http://127.0.0.1:{port}/reader?paper_id=P1')
        page.wait_for_selector('#fit:not([disabled])')
        for number in [12, 25, 4, 27, 1]:
            page.locator('#workspace').evaluate('(el,n)=>{const page=el.querySelector(`[data-page="${n}"]`);el.scrollTop+=page.getBoundingClientRect().top-el.getBoundingClientRect().top-20;}', number)
            playwright.expect(page.locator('#pageNumber')).to_have_value(str(number))
            page.wait_for_selector('#fit:not([disabled])')
        page.wait_for_timeout(300)
        count = page.locator('.page-canvas').evaluate_all('els=>els.filter(el=>el.width>0).length')
        assert count <= 5
        assert 'Synthetic paper' in page.locator('#textLayer').inner_text()
        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('#fit').click()
        page.wait_for_selector('#fit:not([disabled])')
        page.locator('#workspace').evaluate('(el,n)=>{const target=el.querySelector(`[data-page="${n}"]`);el.scrollTop+=target.getBoundingClientRect().top-el.getBoundingClientRect().top-20;}', 2)
        playwright.expect(page.locator('#pageNumber')).to_have_value('2')
        assert not errors
        browser.close()
