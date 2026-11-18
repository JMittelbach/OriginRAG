import base64
import io
import xml.etree.ElementTree as ET
import zipfile

import pytest

from originrag.folio import SHAPES, document_html, plain_text, validate_content
from originrag.folio_docx import NS, import_docx
from test_folio_browser import folio_browser as folio_browser
from test_folio_http import folio_server as folio_server
from test_folio_document_tools_browser import paragraph, seed
from test_folio_docx_edges import exported, png, without_snapshot


def picture():
    return {'type': 'image', 'attrs': {'src': 'data:image/png;base64,' + base64.b64encode(png(4, 2)).decode(),
                                       'width': 100, 'height': 50, 'alt': 'Example'}}


@pytest.mark.parametrize('geometry', ['rect', 'ellipse', 'roundRect'])
def test_native_shape_without_folio_snapshot(geometry):
    node = {'type': 'shape', 'attrs': {'geometry': geometry, 'fill': 'none', 'stroke': '#aabbcc',
                                      'weight': 3, 'width': 254, 'height': 127}}
    raw = exported(picture(), node, paragraph('After the drawing.'))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
        assert [e.get('id') for e in root.findall('.//wp:docPr', NS)] == ['1', '2']
        props = root.find('.//wps:spPr', NS)
        assert props.find('a:prstGeom', NS).get('prst') == geometry
        assert props.find('a:noFill', NS) is not None
        assert props.find('a:ln', NS).get('w') == str(3 * 9525)
    doc, _ = import_docx(without_snapshot(raw))
    assert doc['content']['content'][1] == node
    html = document_html({**doc, 'title': 'Shapes'})
    assert 'background-color:transparent' in html and 'border:3px solid #aabbcc' in html
    assert '[' + SHAPES[geometry] + ']' in plain_text(doc['content'])


@pytest.mark.parametrize('attrs', [{'width': 0}, {'geometry': 'unknown'}, {'weight': 64}, {'fill': 'invalid'}])
def test_invalid_shape_values(attrs):
    with pytest.raises(ValueError):
        validate_content({'type': 'doc', 'content': [{'type': 'shape', 'attrs': attrs}]})


def test_shape_pane_context_switch_undo_and_reopen(folio_browser):
    page, expect = folio_browser.page, folio_browser.expect
    page.set_default_timeout(5000)
    editor = seed(folio_browser, [paragraph('Intro'), picture(), paragraph('End')])
    editor.locator('p').first.click()
    page.get_by_role('tab', name='Insert', exact=True).click()
    page.get_by_role('button', name='Shapes', exact=True).click()
    page.get_by_role('button', name='Ellipse', exact=True).click()
    pane = page.locator('#folioSidebar')
    shape = editor.locator('[data-folio-shape=ellipse]')
    expect(shape).to_be_visible()
    expect(pane.get_by_role('heading')).to_have_text('Format Shape')
    assert pane.bounding_box()['x'] > editor.bounding_box()['x']
    assert not page.locator('dialog[open]').count()
    pane.get_by_label('No fill', exact=True).check()
    expect(shape).to_have_attribute('data-fill', 'none')
    page.get_by_role('button', name='Quick undo').click()
    expect(shape).to_have_attribute('data-fill', '#729ad0')
    width = pane.get_by_label('Width (px)', exact=True)
    width.fill('225')
    width.press('Enter')
    expect(shape).to_have_attribute('data-width', '225')
    photo = editor.locator('img')
    photo.click(button='right')
    page.get_by_role('button', name='Format Picture', exact=True).click()
    expect(pane.get_by_role('heading')).to_have_text('Format Picture')
    width.fill('120')
    width.press('Enter')
    expect(photo).to_have_attribute('height', '60')
    expect(shape).to_have_attribute('data-width', '225')
    alt = pane.get_by_label('Alt text')
    alt.fill('Updated description')
    alt.press('Tab')
    expect(photo).to_have_attribute('alt', 'Updated description')
    editor.locator('p').last.click()
    expect(pane).to_contain_text('Select a shape or picture')
    shape.click(button='right')
    page.get_by_role('button', name='Format Shape', exact=True).click()
    expect(width).to_have_value('225')
    pane.get_by_role('button', name='Close sidebar').click()
    expect(pane).to_be_hidden()
    shape.dblclick()
    expect(pane.get_by_role('heading')).to_have_text('Format Shape')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    expect(shape).to_have_attribute('data-width', '225')
    expect(photo).to_have_attribute('alt', 'Updated description')


def test_format_pane_two_shapes_keyboard_switch_and_delete(folio_browser):
    page, expect = folio_browser.page, folio_browser.expect
    editor = seed(folio_browser, [{'type': 'shape', 'attrs': {'geometry': 'rect', 'width': 190}},
                                  {'type': 'shape', 'attrs': {'geometry': 'roundRect', 'width': 110}},
                                  paragraph('Body')])
    first, second = editor.locator('[data-folio-shape]').all()
    first.click()
    editor.press('Shift+F10')
    page.get_by_role('button', name='Format Shape', exact=True).click()
    pane = page.locator('#folioSidebar')
    width = pane.get_by_label('Width (px)')
    expect(width).to_have_value('190')
    second.click()
    expect(width).to_have_value('110')
    pane.get_by_label('No line', exact=True).check()
    expect(first).to_have_attribute('data-stroke', '#315887')
    expect(second).to_have_attribute('data-stroke', 'none')
    second.click()
    editor.press('Delete')
    expect(editor.locator('[data-folio-shape]')).to_have_count(1)
    first.dblclick()
    expect(width).to_have_value('190')
    pane.get_by_label('Line weight').fill('4')
    pane.get_by_label('Line weight').press('Tab')
    expect(first).to_have_attribute('data-weight', '4')
    page.set_viewport_size({'width': 390, 'height': 844})
    expect(pane.get_by_role('heading')).to_be_visible()
    box = pane.bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= 391
