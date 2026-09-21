import pytest

from test_web_reader import reader_server  # noqa: F401


@pytest.fixture
def tools_page(reader_server):  # noqa: F811
    playwright = pytest.importorskip('playwright.sync_api')
    port = reader_server[0]
    html = '''<!doctype html><html><head><style>
      * { box-sizing:border-box } body { margin:0 } main { display:flex;height:500px }
      #workspace { flex:1;min-width:320px;height:500px;overflow:auto;padding:20px }
      #notesPanel { flex:0 0 auto;width:var(--notes-width,330px) }
      #notesResize { flex:0 0 8px;cursor:col-resize }
      #workspace div { height:1600px } [hidden] { display:none!important }
    </style></head><body>
    <button id="fitPage">Fit page</button><button id="fullscreen">Focus view</button>
    <button id="shortcuts">Shortcuts</button><input id="typing"><p id="selection">A selected passage.</p>
    <main><section id="workspace"><div>Page</div></section>
    <div id="notesResize" role="separator" tabindex="0"></div><aside id="notesPanel">Notes</aside></main>
    <progress id="readingProgress" max="100"></progress><output id="readingPosition"></output>
    <dialog id="shortcutsDialog"><button id="shortcutsClose">Close</button></dialog>
    </body></html>'''
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 950})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/tools-fixture', lambda route: route.fulfill(content_type='text/html', body=html))
        page.goto(f'http://127.0.0.1:{port}/tools-fixture')
        page.evaluate('''async()=>{
          const module=await import('/assets/reader/reading-tools.mjs');
          window.toolsModule=module;
          window.state={page:1,zoom:1,jumps:[],messages:[],delay:false};
          state.pdf={numPages:4,getPage:async number=>{
            if(state.delay)await new Promise(resolve=>state.release=resolve);
            return {getViewport:()=>number===4?{width:842,height:595}:{width:595,height:842}};
          }};
          Object.defineProperty(document.documentElement,'requestFullscreen',{configurable:true,value:()=>Promise.reject(new Error('blocked'))});
          window.reading=module.readingTools({
            getPdf:()=>state.pdf,getPage:()=>state.page,getZoom:()=>state.zoom,
            renderPage:async(page,zoom,y)=>{state.jumps.push({page,zoom,y});state.page=page;state.zoom=zoom;reading.update()},
            status:(text,error)=>state.messages.push({text,error})
          });
        }''')
        yield page, playwright
        assert not errors
        browser.close()


def test_reader_tools_fit_progress_and_stale_fit(tools_page):
    page, playwright = tools_page
    assert page.evaluate('toolsModule.pageFitScale(595,842,900,600)') == pytest.approx(600/842)
    assert page.evaluate('toolsModule.pageFitScale(842,595,900,600)') == pytest.approx(600/595)
    assert page.evaluate('toolsModule.pageFitScale(595,842,9000,6000)') == 3
    assert page.evaluate('toolsModule.pageFitScale(595,842,5,5)') == .35
    assert page.evaluate('toolsModule.pageFitScale(0,842,900,600)') is None
    assert page.evaluate('toolsModule.pageFitScale(NaN,842,900,600)') is None
    assert page.evaluate('toolsModule.readingProgress(50,200,100)') == 50
    assert page.evaluate('toolsModule.readingProgress(-50,200,100)') == 0
    assert page.evaluate('toolsModule.readingProgress(900,200,100)') == 100
    assert page.evaluate('toolsModule.readingProgress(0,100,100)') == 100
    assert page.evaluate('toolsModule.readingProgress(0,100,0)') == 0
    page.locator('#fitPage').click()
    page.wait_for_function('state.jumps.length===1')
    assert page.evaluate('state.jumps[0].zoom') == pytest.approx(460/842)
    assert page.evaluate('state.jumps[0].y') == 0
    page.evaluate('state.page=4;reading.update()')
    page.locator('#fitPage').click()
    page.wait_for_function('state.jumps.length===2')
    assert page.evaluate('state.jumps[1].zoom') == pytest.approx(460/595)
    playwright.expect(page.locator('#readingPosition')).to_have_text('Page 4 of 4')
    page.locator('#workspace').evaluate('el=>el.scrollTop=el.scrollHeight')
    page.wait_for_function('document.querySelector("#readingProgress").value===100')
    page.evaluate('state.delay=true;state.page=1;reading.update()')
    page.locator('#fitPage').click()
    playwright.expect(page.locator('#fitPage')).to_be_disabled()
    page.evaluate('state.page=2;state.release()')
    playwright.expect(page.locator('#fitPage')).to_be_enabled()
    assert page.evaluate('state.jumps.length') == 2


def test_reader_tools_keyboard_help_and_fullscreen_failure(tools_page):
    page, playwright = tools_page
    page.locator('#typing').fill('f?')
    page.locator('#typing').press('?')
    page.locator('#typing').press('f')
    assert page.evaluate('state.messages.length') == 0
    playwright.expect(page.locator('#shortcutsDialog')).not_to_be_visible()
    page.locator('#typing').evaluate('el=>el.blur()')
    page.locator('#selection').evaluate('el=>{const range=document.createRange();range.selectNodeContents(el);const selected=getSelection();selected.removeAllRanges();selected.addRange(range)}')
    page.keyboard.press('?')
    playwright.expect(page.locator('#shortcutsDialog')).not_to_be_visible()
    page.evaluate('getSelection().removeAllRanges()')
    page.keyboard.press('?')
    playwright.expect(page.locator('#shortcutsDialog')).to_be_visible()
    page.keyboard.press('f')
    assert page.evaluate('state.messages.length') == 0
    page.keyboard.press('Escape')
    playwright.expect(page.locator('#shortcutsDialog')).not_to_be_visible()
    page.locator('#fullscreen').click()
    page.wait_for_function('state.messages.length===1')
    assert page.evaluate('state.messages[0].error') is True
    assert 'keep reading' in page.evaluate('state.messages[0].text')
    playwright.expect(page.locator('#fullscreen')).to_be_enabled()
    page.locator('#shortcuts').click()
    playwright.expect(page.locator('#shortcutsDialog')).to_be_visible()
    page.locator('#shortcutsClose').click()
    playwright.expect(page.locator('#shortcutsDialog')).not_to_be_visible()


def test_reader_tools_resizable_notes_and_cleanup(tools_page):
    page, playwright = tools_page
    handle = page.locator('#notesResize')
    handle.focus()
    handle.press('End')
    playwright.expect(handle).to_have_attribute('aria-valuenow', '480')
    handle.press('ArrowLeft')
    playwright.expect(handle).to_have_attribute('aria-valuenow', '480')
    handle.press('Home')
    playwright.expect(handle).to_have_attribute('aria-valuenow', '240')
    handle.press('Shift+ArrowLeft')
    playwright.expect(handle).to_have_attribute('aria-valuenow', '280')
    box = handle.bounding_box()
    page.mouse.move(box['x']+4, box['y']+30)
    page.mouse.down()
    page.mouse.move(box['x']-100, box['y']+30)
    page.mouse.up()
    assert 380 <= int(handle.get_attribute('aria-valuenow')) <= 390
    assert not page.evaluate('document.documentElement.classList.contains("notes-resizing")')
    assert page.evaluate('state.jumps.length') == 0
    page.evaluate('document.querySelector("#notesPanel").hidden=true')
    playwright.expect(handle).to_be_hidden()
    page.evaluate('document.querySelector("#notesPanel").hidden=false')
    playwright.expect(handle).to_be_visible()
    page.set_viewport_size({'width': 750, 'height': 950})
    handle.focus()
    handle.press('End')
    assert int(handle.get_attribute('aria-valuenow')) <= 422
    page.evaluate('reading.destroy()')
    page.locator('#shortcuts').click()
    playwright.expect(page.locator('#shortcutsDialog')).not_to_be_visible()


def test_reader_tools_missing_fullscreen_and_fit_failure(tools_page):
    page, playwright = tools_page
    page.evaluate('''()=>{
      reading.destroy();
      Object.defineProperty(document.documentElement,'requestFullscreen',{configurable:true,value:undefined});
      state.pdf.getPage=async()=>{throw new Error('page unavailable')};
      window.reading=toolsModule.readingTools({getPdf:()=>state.pdf,getPage:()=>state.page,getZoom:()=>state.zoom,
        renderPage:async()=>{throw new Error('should not render')},status:(text,error)=>state.messages.push({text,error})});
    }''')
    playwright.expect(page.locator('#fullscreen')).to_be_disabled()
    assert 'not available' in page.locator('#fullscreen').get_attribute('title')
    page.locator('#fitPage').click()
    page.wait_for_function('state.messages.length===1')
    assert 'Try Fit width' in page.evaluate('state.messages[0].text')
    playwright.expect(page.locator('#fitPage')).to_be_enabled()
