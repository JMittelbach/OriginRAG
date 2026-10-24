"""Pointer-driven drawing, bounded PNG insertion, and failure recovery."""

import base64
import os
from pathlib import Path
import struct

import pytest

from test_folio_http import folio_server as folio_server
from test_folio_browser import folio_browser as folio_browser, create_document, saved_document


def open_drawing(browser, fail_once=False):
    page = browser.page
    page.goto(browser.base + '/folio')
    page.evaluate("""async (failOnce) => {
      const {openDrawing} = await import('/assets/folio/drawing.mjs');
      window.insertedPictures = [];
      window.drawingClosed = 0;
      window.openTestDrawing = () => openDrawing({
        onInsert: async (picture) => {
          window.insertedPictures.push(picture);
          if (failOnce) {
            if (window.insertedPictures.length === 1) throw new Error('Storage is temporarily busy.');
            await new Promise(resolve => { window.finishDrawingInsert = resolve; });
          }
        }, onClose: () => window.drawingClosed++,
      });
      window.openTestDrawing();
    }""", fail_once)
    dialog = page.get_by_role('dialog', name='Draw', exact=True)
    browser.expect(dialog).to_be_visible()
    return dialog, dialog.get_by_label('Drawing canvas', exact=True)


def stroke(page, canvas, start=(.2, .4), end=(.8, .4)):
    box = canvas.bounding_box()
    page.mouse.move(box['x'] + box['width'] * start[0], box['y'] + box['height'] * start[1])
    page.mouse.down()
    page.mouse.move(box['x'] + box['width'] * end[0], box['y'] + box['height'] * end[1], steps=15)
    page.mouse.up()
    page.evaluate('new Promise(requestAnimationFrame)')


def alpha(canvas, x=480, y=216):
    return canvas.evaluate('(canvas, point) => canvas.getContext("2d").getImageData(...point, 1, 1).data[3]', [x, y])


def screenshot(browser, name):
    directory = os.environ.get('ORIGINRAG_UI_REVIEW_DIR')
    if directory:
        browser.page.screenshot(path=str(Path(directory) / name))


def test_drawing_pointer_ink_undo_redo_and_confirmed_clear(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    dialog, canvas = open_drawing(browser)
    expect(dialog.get_by_role('button', name='Insert drawing', exact=True)).to_be_disabled()
    stroke(page, canvas)
    assert alpha(canvas) == 255
    page.evaluate("document.documentElement.dataset.theme = 'light'")
    screenshot(browser, 'folio-drawing-light.png')
    page.evaluate("document.documentElement.dataset.theme = 'dark'")
    screenshot(browser, 'folio-drawing-dark.png')
    expect(dialog.get_by_text('1 stroke', exact=True)).to_be_visible()
    dialog.get_by_role('button', name='Undo stroke', exact=True).click()
    page.evaluate('new Promise(requestAnimationFrame)')
    assert alpha(canvas) == 0
    dialog.get_by_role('button', name='Redo stroke', exact=True).click()
    page.evaluate('new Promise(requestAnimationFrame)')
    assert alpha(canvas) == 255
    dialog.get_by_role('button', name='Clear canvas', exact=True).click()
    dialog.get_by_role('button', name='Keep drawing', exact=True).click()
    assert alpha(canvas) == 255
    dialog.get_by_role('button', name='Clear canvas', exact=True).click()
    dialog.get_by_role('button', name='Clear drawing', exact=True).click()
    page.evaluate('new Promise(requestAnimationFrame)')
    assert alpha(canvas) == 0
    dialog.get_by_role('button', name='Insert drawing', exact=True).click()
    expect(dialog.get_by_role('alert')).to_contain_text('The canvas is empty')
    canvas.focus()
    canvas.press('ControlOrMeta+z')
    page.evaluate('new Promise(requestAnimationFrame)')
    assert alpha(canvas) == 255
    dialog.get_by_role('button', name='Cancel drawing', exact=True).click()
    expect(dialog).to_have_count(0)
    assert page.evaluate('window.insertedPictures.length') == 0
    assert page.evaluate('window.drawingClosed') == 1


def test_highlighter_eraser_and_transparent_png(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    dialog, canvas = open_drawing(browser)
    dialog.get_by_role('button', name='Highlighter', exact=True).click()
    stroke(page, canvas)
    assert 60 < alpha(canvas) < 100
    dialog.get_by_role('button', name='Eraser', exact=True).click()
    stroke(page, canvas, start=(.5, .3), end=(.5, .5))
    assert alpha(canvas) == 0
    assert alpha(canvas, 300, 216) > 0
    dialog.get_by_label('Drawing description', exact=True).fill('A highlighted observation')
    dialog.get_by_role('button', name='Insert drawing', exact=True).click()
    expect(dialog).to_have_count(0)
    result = page.evaluate('window.insertedPictures[0]')
    assert result['alt'] == 'A highlighted observation'
    assert result['width'] == 960 and result['height'] == 540
    assert result['src'].startswith('data:image/png;base64,')
    image = base64.b64decode(result['src'].split(',', 1)[1])
    assert image.startswith(b'\x89PNG\r\n\x1a\n')
    assert struct.unpack('!II', image[16:24]) == (960, 540)
    assert len(image) <= 2_000_000
    assert page.evaluate("""async () => {
      const bitmap = new Image(); bitmap.src = window.insertedPictures[0].src; await bitmap.decode();
      const canvas = document.createElement('canvas'); canvas.width = 960; canvas.height = 540;
      const ctx = canvas.getContext('2d'); ctx.drawImage(bitmap, 0, 0);
      return ctx.getImageData(0, 0, 1, 1).data[3];
    }""") == 0


def test_insert_failure_retains_drawing_and_awaits_success(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    dialog, canvas = open_drawing(browser, fail_once=True)
    stroke(page, canvas)
    dialog.get_by_label('Drawing background', exact=True).select_option('white')
    dialog.get_by_role('button', name='Insert drawing', exact=True).click()
    expect(dialog.get_by_role('alert')).to_contain_text('Storage is temporarily busy')
    expect(dialog.get_by_role('alert')).to_contain_text('Your drawing is still here')
    assert alpha(canvas) == 255
    dialog.get_by_role('button', name='Insert drawing', exact=True).click()
    expect(dialog).to_have_attribute('aria-busy', 'true')
    expect(dialog.get_by_role('button', name='Cancel drawing', exact=True)).to_be_disabled()
    page.keyboard.press('Escape')
    expect(dialog).to_be_visible()
    assert page.evaluate('window.insertedPictures.length') == 2
    page.evaluate('window.finishDrawingInsert()')
    expect(dialog).to_have_count(0)
    assert page.evaluate('window.drawingClosed') == 1
    assert page.evaluate("""async () => {
      const bitmap = new Image(); bitmap.src = window.insertedPictures[1].src; await bitmap.decode();
      const canvas = document.createElement('canvas'); canvas.width = 960; canvas.height = 540;
      const ctx = canvas.getContext('2d'); ctx.drawImage(bitmap, 0, 0);
      return [...ctx.getImageData(0, 0, 1, 1).data];
    }""") == [255, 255, 255, 255]


def test_drawing_mobile_layout_escape_and_no_duplicate_dialog(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    page.set_viewport_size({'width': 390, 'height': 844})
    dialog, canvas = open_drawing(browser)
    page.evaluate('window.openTestDrawing()')
    expect(dialog).to_have_count(1)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert dialog.evaluate('(node) => node.scrollWidth <= node.clientWidth')
    stroke(page, canvas)
    screenshot(browser, 'folio-drawing-mobile.png')
    expect(dialog.get_by_role('button', name='Insert drawing', exact=True)).to_be_enabled()
    page.keyboard.press('Escape')
    expect(dialog).to_have_count(0)
    assert page.evaluate('window.drawingClosed') == 1
    assert page.evaluate('window.insertedPictures.length') == 0


def test_drawing_single_stroke_point_budget_stays_responsive(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    dialog, canvas = open_drawing(browser)
    box = canvas.bounding_box()
    canvas.evaluate("canvas => canvas.addEventListener('pointerdown', event => { canvas.dataset.testPointer = event.pointerId; }, {once:true})")
    page.mouse.move(box['x'] + 10, box['y'] + 10)
    page.mouse.down()
    canvas.evaluate("""(canvas) => {
      const rect = canvas.getBoundingClientRect();
      for (let index = 0; index < 4500; index++) canvas.dispatchEvent(new PointerEvent('pointermove', {
        pointerId: Number(canvas.dataset.testPointer), clientX: rect.left + (index % 2 ? 20 : 40), clientY: rect.top + 40,
        bubbles: true, isPrimary: true, buttons: 1,
      }));
    }""")
    page.mouse.up()
    expect(dialog.get_by_role('alert')).to_contain_text('This stroke reached the drawing limit')
    dialog.get_by_role('button', name='Insert drawing', exact=True).click()
    expect(dialog).to_have_count(0)
    assert page.evaluate('window.insertedPictures.length') == 1


def test_drawing_encoded_size_guard_preserves_ink(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    dialog, canvas = open_drawing(browser)
    stroke(page, canvas)
    page.evaluate("""() => {
      window.originalDrawingEncoder = HTMLCanvasElement.prototype.toDataURL;
      HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,' + 'A'.repeat(Math.ceil(2000001 / 3) * 4);
    }""")
    dialog.get_by_role('button', name='Insert drawing', exact=True).click()
    expect(dialog.get_by_role('alert')).to_contain_text('too large to insert')
    assert alpha(canvas) == 255
    assert page.evaluate('window.insertedPictures.length') == 0
    page.evaluate('() => { HTMLCanvasElement.prototype.toDataURL = window.originalDrawingEncoder; }')
    dialog.get_by_role('button', name='Insert drawing', exact=True).click()
    expect(dialog).to_have_count(0)
    assert page.evaluate('window.insertedPictures.length') == 1


@pytest.mark.skipif(os.environ.get('ORIGINRAG_TEST_BROWSER') == 'firefox', reason='Trusted touch injection uses Chromium CDP.')
def test_real_touch_stroke_uses_the_same_ink_canvas(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    page.set_viewport_size({'width': 390, 'height': 844})
    dialog, canvas = open_drawing(browser)
    box = canvas.bounding_box()
    session = page.context.new_cdp_session(page)
    session.send('Emulation.setTouchEmulationEnabled', {'enabled': True, 'maxTouchPoints': 1})
    x, y = box['x'] + box['width'] * .2, box['y'] + box['height'] * .4
    session.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}]})
    for step in range(1, 16):
        session.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [
            {'x': x + box['width'] * .6 * step / 15, 'y': y}]})
    session.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    page.evaluate('new Promise(requestAnimationFrame)')
    assert alpha(canvas) == 255
    dialog.get_by_role('button', name='Insert drawing', exact=True).click()
    expect(dialog).to_have_count(0)
    assert page.evaluate('window.insertedPictures.length') == 1
    session.detach()


def test_draw_ribbon_insertion_is_saved_as_picture(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser, 'A drawing in a document')
    editor.fill('A labelled sketch follows.')
    editor.press('End')
    editor.press('Enter')
    page.get_by_role('tab', name='Draw', exact=True).click()
    page.get_by_role('button', name='Insert drawing', exact=True).click()
    dialog = page.get_by_role('dialog', name='Draw', exact=True)
    stroke(page, dialog.get_by_label('Drawing canvas', exact=True))
    dialog.get_by_label('Drawing description', exact=True).fill('A hand-drawn connection')
    dialog.get_by_role('button', name='Insert drawing', exact=True).click()
    expect(dialog).to_have_count(0)
    expect(editor.locator('img')).to_have_count(1)
    expect(editor.locator('img')).to_have_attribute('alt', 'A hand-drawn connection')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    image = next(node for node in saved_document(browser)['content']['content'] if node['type'] == 'image')
    assert image['attrs']['src'].startswith('data:image/png;base64,')
    page.reload()
    expect(editor.locator('img')).to_have_attribute('alt', 'A hand-drawn connection')


def test_draw_ribbon_presets_open_with_actual_selected_ink(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    create_document(browser, 'Drawing preset regression')
    page.get_by_role('tab', name='Draw', exact=True).click()
    for name, tool, colour, thickness in [
        ('Black pen', 'Pen', '#202020', '3'),
        ('Red pen', 'Pen', '#d62d36', '3'),
        ('Fine pen', 'Pen', '#718c98', '1'),
        ('Highlighter', 'Highlighter', '#f4db20', '20'),
    ]:
        page.locator('#folioRibbon').get_by_role('button', name=name, exact=True).click()
        dialog = page.get_by_role('dialog', name='Draw', exact=True)
        expect(dialog.get_by_role('button', name=tool, exact=True)).to_have_attribute('aria-pressed', 'true')
        expect(dialog.get_by_label('Ink colour', exact=True)).to_have_value(colour)
        expect(dialog.get_by_label('Ink thickness', exact=True)).to_have_value(thickness)
        dialog.get_by_role('button', name='Cancel drawing', exact=True).click()
        expect(dialog).to_have_count(0)
