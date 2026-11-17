from pathlib import Path

import pytest


ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'workspace'
SHELL = '''<!doctype html><html><head>
<link rel="stylesheet" href="/writer.css"><link rel="stylesheet" href="/writer-layout.css">
<style>body{margin:0}.writing #source{font-size:var(--editor-font-size)}.files{color:white}</style>
</head><body class="writing"><header>
<button id="toggleFiles">Files</button><button id="swapPanes">Swap</button>
<select id="layoutMode"><option value="split">Both</option><option value="code">Code</option><option value="pdf">PDF</option></select>
<select id="paneDirection"><option value="side">Side</option><option value="stack">Stack</option></select>
<button id="editorTheme">Theme</button><button id="resetLayout">Reset</button>
<input id="codeZoom" type="number" step="any" value="11">
<button id="codeZoomOut">Smaller</button><button id="codeZoomIn">Larger</button>
</header><main><div class="editor-grid" id="writerGrid">
<aside class="files" id="filesPane"><button id="hideFiles">Hide</button>Files</aside>
<div class="splitter files-resize" id="filesResize" role="separator" tabindex="0" aria-valuemin="160"></div>
<section class="source-pane" id="sourcePane"><div id="source">Source code</div></section>
<div class="splitter panes-resize" id="panesResize" role="separator" tabindex="0" aria-valuemin="20" aria-valuemax="80"></div>
<section class="preview-pane" id="previewPane">PDF preview</section>
</div></main><script type="module">
import {setupWriterLayout} from '/writer-layout.mjs';
window.layout = setupWriterLayout(state => {window.lastResize = state;});
</script></body></html>'''


@pytest.fixture
def layout_page():
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def respond(route):
            name = route.request.url.rsplit('/', 1)[-1]
            if not name:
                route.fulfill(body=SHELL, content_type='text/html')
            elif name in {'writer.css', 'writer-layout.css', 'writer-layout.mjs'}:
                route.fulfill(body=(ASSETS / name).read_text(), content_type='text/javascript' if name.endswith('mjs') else 'text/css')
            else:
                route.fulfill(status=404)

        page.route('http://originwrite.test/**', respond)
        page.goto('http://originwrite.test/')
        page.wait_for_function('window.layout && window.lastResize')
        yield page
        assert not errors
        browser.close()


def drag(page, selector, dx=0, dy=0):
    bounds = page.locator(selector).bounding_box()
    x, y = bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + dy, steps=8)
    page.mouse.up()


def box(page, selector):
    return page.locator(selector).bounding_box()


def test_stacked_height_drag_and_separate_width_persistence(layout_page):
    page = layout_page
    before = box(page, '#sourcePane')['width']
    drag(page, '#panesResize', dx=110)
    side_width = box(page, '#sourcePane')['width']
    assert side_width > before + 100
    page.locator('#paneDirection').select_option('stack')
    assert page.locator('#panesResize').get_attribute('aria-orientation') == 'horizontal'
    assert box(page, '#sourcePane')['y'] < box(page, '#previewPane')['y']
    assert box(page, '#filesPane')['height'] > box(page, '#sourcePane')['height']
    initial_height = box(page, '#sourcePane')['height']
    drag(page, '#panesResize', dy=100)
    stacked_height = box(page, '#sourcePane')['height']
    assert stacked_height > initial_height + 90
    page.locator('#paneDirection').select_option('side')
    assert box(page, '#sourcePane')['width'] == pytest.approx(side_width, abs=2)
    page.locator('#paneDirection').select_option('stack')
    assert box(page, '#sourcePane')['height'] == pytest.approx(stacked_height, abs=2)
    page.reload()
    page.wait_for_function('window.layout && window.lastResize')
    assert page.locator('#paneDirection').input_value() == 'stack'
    assert box(page, '#sourcePane')['height'] == pytest.approx(stacked_height, abs=2)
    page.locator('#swapPanes').click()
    assert box(page, '#sourcePane')['y'] > box(page, '#previewPane')['y']
    drag(page, '#panesResize', dy=-50)
    assert box(page, '#sourcePane')['height'] > stacked_height + 40
    page.locator('#hideFiles').click()
    assert not page.locator('#filesPane').is_visible()
    assert box(page, '#sourcePane')['x'] == pytest.approx(0, abs=1)
    page.locator('#resetLayout').click()
    assert page.locator('#paneDirection').input_value() == 'side'
    assert box(page, '#sourcePane')['width'] == pytest.approx(box(page, '#previewPane')['width'], abs=2)


def test_keyboard_resize_bounds_zoom_and_single_panes(layout_page):
    page = layout_page
    split = page.locator('#panesResize')
    page.locator('#paneDirection').select_option('stack')
    split.press('Home')
    assert split.get_attribute('aria-valuenow') == '20'
    split.press('ArrowUp')
    assert split.get_attribute('aria-valuenow') == '20'
    split.press('End')
    assert split.get_attribute('aria-valuenow') == '80'
    split.press('ArrowDown')
    assert split.get_attribute('aria-valuenow') == '80'
    split.dblclick()
    assert split.get_attribute('aria-valuenow') == '50'
    page.locator('#codeZoom').fill('20')
    page.locator('#codeZoom').press('Enter')
    assert page.locator('#source').evaluate('e => getComputedStyle(e).fontSize') == '20px'
    page.locator('#codeZoomIn').click()
    assert page.locator('#codeZoom').input_value() == '21'
    assert not page.locator('#codeZoomIn').is_disabled()
    page.locator('#codeZoomOut').click()
    page.reload()
    page.wait_for_function('window.layout && window.lastResize')
    assert page.locator('#codeZoom').input_value() == '20'
    assert page.evaluate('window.lastResize.fontSize') == 20
    page.locator('#layoutMode').select_option('pdf')
    assert not page.locator('#sourcePane').is_visible()
    assert page.locator('#paneDirection').is_disabled()
    assert page.locator('#panesResize').get_attribute('tabindex') == '-1'
    page.evaluate('window.layout.showCode()')
    assert page.locator('#sourcePane').is_visible()
    assert not page.locator('#previewPane').is_visible()
    page.locator('#layoutMode').select_option('split')
    assert page.locator('#panesResize').get_attribute('tabindex') == '0'
    assert page.locator('#panesResize').get_attribute('aria-orientation') == 'horizontal'


def test_mobile_automatic_stacking_keeps_desktop_preference(layout_page):
    page = layout_page
    page.locator('#hideFiles').click()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.wait_for_function("document.getElementById('writerGrid').dataset.narrow === 'true'")
    assert page.locator('#paneDirection').is_disabled()
    assert page.locator('#panesResize').get_attribute('aria-orientation') == 'horizontal'
    assert page.locator('#filesResize').get_attribute('tabindex') == '-1'
    before = box(page, '#sourcePane')['height']
    drag(page, '#panesResize', dy=50)
    assert box(page, '#sourcePane')['height'] > before + 40
    page.set_viewport_size({'width': 1500, 'height': 900})
    page.wait_for_function("document.getElementById('writerGrid').dataset.narrow === 'false'")
    assert page.locator('#paneDirection').input_value() == 'side'
    assert page.locator('#panesResize').get_attribute('aria-orientation') == 'vertical'
    assert box(page, '#sourcePane')['width'] == pytest.approx(box(page, '#previewPane')['width'], abs=2)


def test_legacy_settings_bounds_and_pointer_cancel(layout_page):
    page = layout_page
    page.evaluate("localStorage.setItem('originwrite.layout.v1', JSON.stringify({width:999,ratio:.65,fontSize:-999,direction:'invalid'}))")
    page.reload()
    page.wait_for_function('window.layout && window.lastResize')
    assert page.locator('#filesResize').get_attribute('aria-valuenow') == '400'
    assert page.locator('#panesResize').get_attribute('aria-valuenow') == '65'
    assert page.locator('#codeZoom').input_value() == '11'
    assert page.locator('#paneDirection').input_value() == 'side'
    page.locator('#filesResize').press('Home')
    assert box(page, '#filesPane')['width'] == pytest.approx(160, abs=1)
    page.locator('#filesResize').press('ArrowLeft')
    assert box(page, '#filesPane')['width'] == pytest.approx(160, abs=1)
    bounds = box(page, '#panesResize')
    x, y = bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 40, y)
    assert page.locator('body').evaluate("e => e.classList.contains('resizing')")
    page.locator('#panesResize').dispatch_event('pointercancel', {'pointerId': 1})
    assert not page.locator('body').evaluate("e => e.classList.contains('resizing')")
    page.mouse.up()
    page.evaluate('window.layout.destroy()')
    assert not page.locator('body').evaluate("e => e.classList.contains('resizing')")


def test_free_font_sizes_below_eleven_above_twenty_four_and_decimals(layout_page):
    page = layout_page
    zoom = page.locator('#codeZoom')
    for value in ['9', '6.5', '32', '72', '999', '0.5']:
        zoom.fill(value)
        zoom.press('Enter')
        assert zoom.input_value() == value
        assert float(page.locator('#source').evaluate('e => parseFloat(getComputedStyle(e).fontSize)')) == pytest.approx(float(value))
    page.locator('#codeZoomOut').click()
    assert zoom.input_value() == '0.25'
    page.locator('#codeZoomIn').click()
    assert zoom.input_value() == '0.5'
    page.reload()
    page.wait_for_function('window.layout && window.lastResize')
    assert zoom.input_value() == '0.5'
    assert page.evaluate('window.lastResize.fontSize') == .5
    for invalid in ['0', '-2', '']:
        zoom.fill(invalid)
        zoom.press('Enter')
        assert zoom.input_value() == '0.5'
        assert zoom.get_attribute('aria-invalid') == 'true'
    zoom.fill('15.25')
    zoom.press('Escape')
    assert zoom.input_value() == '0.5'
    page.locator('#resetLayout').click()
    assert zoom.input_value() == '11'


def test_default_code_font_is_eleven_and_saved_sizes_are_respected(layout_page):
    page = layout_page
    zoom = page.locator('#codeZoom')
    assert zoom.input_value() == '11'
    assert page.locator('#source').evaluate('e => getComputedStyle(e).fontSize') == '11px'
    assert page.evaluate('window.lastResize.fontSize') == 11
    zoom.fill('18')
    zoom.press('Enter')
    page.reload()
    page.wait_for_function('window.layout && window.lastResize')
    assert zoom.input_value() == '18'
    page.locator('#resetLayout').click()
    assert zoom.input_value() == '11'
    page.reload()
    page.wait_for_function('window.layout && window.lastResize')
    assert zoom.input_value() == '11'
