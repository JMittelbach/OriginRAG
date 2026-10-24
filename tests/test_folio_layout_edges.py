"""Print-layout regressions exercise real browser PDF output, not just CSS flags."""

import base64
import time

import pytest

from test_folio_browser import folio_browser as folio_browser
from test_folio_http import folio_server as folio_server, request_json
from test_folio_ui_edges import image_file


def paragraph(value):
    return {'type': 'paragraph', 'content': [{'type': 'text', 'text': value}]}


def open_fixture(browser, content, *, layout=None):
    document = request_json(browser.port, 'POST', headers=browser.headers,
                            payload={'action': 'create', 'title': 'Print-layout fixture'})
    payload = {key: document[key] for key in ('id', 'revision', 'title', 'page', 'comments')}
    payload.update(action='save', content={'type': 'doc', 'content': content})
    if layout:
        payload['page'] = layout
    saved = request_json(browser.port, 'POST', headers=browser.headers, payload=payload)
    started = time.monotonic()
    browser.page.goto(browser.base + '/folio?id=' + saved['id'])
    browser.expect(browser.page.get_by_role('textbox', name='Document content', exact=True)).to_be_visible()
    return saved, time.monotonic() - started


def print_pdf(browser):
    fitz = pytest.importorskip('fitz')
    page = browser.page
    page.evaluate('''() => {
      window.folioPrintInvoked = false;
      document.documentElement.removeAttribute('data-folio-printed');
      window.print = () => {
        window.folioPrintInvoked = true;
        window.folioPrintedNotes = document.getElementById('folioNotes')?.innerText;
        document.documentElement.dataset.folioPrinted = 'true';
      };
    }''')
    page.get_by_role('tab', name='File', exact=True).click()
    page.get_by_role('button', name='Print or save PDF', exact=True).click()
    browser.expect(page.locator('html')).to_have_attribute('data-folio-printed', 'true')
    pdf = page.pdf(prefer_css_page_size=True, print_background=True)
    return fitz.open(stream=pdf, filetype='pdf')


def test_print_preserves_long_table_and_text_without_workspace_chrome(folio_browser):
    browser = folio_browser
    rows = []
    for index in range(81):
        rows.append({'type': 'tableRow', 'content': [
            {'type': 'tableHeader' if index == 0 else 'tableCell',
             'content': [paragraph(f'Cell {index:03d}-{column} evidence retained.')],
             'attrs': {'colspan': 1, 'rowspan': 1}}
            for column in range(3)
        ]})
    document, _ = open_fixture(browser, [paragraph('Start of printed document'),
        {'type': 'table', 'content': rows}, paragraph('End of printed document')])
    with print_pdf(browser) as pdf:
        assert len(pdf) >= 3
        text = '\n'.join(page.get_text() for page in pdf)
        assert 'Start of printed document' in text
        assert 'End of printed document' in text
        for index in range(81):
            for column in range(3):
                assert f'Cell {index:03d}-{column}' in text
        assert 'OriginRAG' not in text and 'Spelling: browser' not in text
        assert pdf[0].rect.width == pytest.approx(595.3, abs=1)
        assert pdf[0].rect.height == pytest.approx(841.9, abs=1)
    saved = request_json(browser.port, path='/api/folio?id=' + document['id'], headers=browser.headers)
    assert saved['revision'] == document['revision'], 'Printing must not modify document content.'


def test_explicit_page_breaks_and_landscape_letter_dimensions(folio_browser):
    browser = folio_browser
    open_fixture(browser, [paragraph('FIRST PAGE UNIQUE'), {'type': 'pageBreak'},
                          paragraph('SECOND PAGE UNIQUE'), {'type': 'pageBreak'},
                          paragraph('THIRD PAGE UNIQUE')],
                 layout={'size': 'letter', 'orientation': 'landscape', 'margin': 'narrow'})
    with print_pdf(browser) as pdf:
        assert len(pdf) == 3
        for index, label in enumerate(('FIRST', 'SECOND', 'THIRD')):
            assert label + ' PAGE UNIQUE' in pdf[index].get_text()
            assert pdf[index].rect.width == pytest.approx(792, abs=1)
            assert pdf[index].rect.height == pytest.approx(612, abs=1)
            assert 'Page break' not in pdf[index].get_text()


def test_native_browser_print_uses_current_page_settings_without_ribbon_click(folio_browser):
    fitz = pytest.importorskip('fitz')
    browser = folio_browser
    open_fixture(browser, [paragraph('Native browser print retains page settings.')],
                 layout={'size': 'letter', 'orientation': 'landscape', 'margin': 'narrow'})
    raw = browser.page.pdf(prefer_css_page_size=True)
    with fitz.open(stream=raw, filetype='pdf') as pdf:
        assert pdf[0].rect.width == pytest.approx(792, abs=1)
        assert pdf[0].rect.height == pytest.approx(612, abs=1)
        assert 'Native browser print retains page settings.' in pdf[0].get_text()


def test_print_includes_real_note_text_without_edit_controls(folio_browser):
    browser = folio_browser
    open_fixture(browser, [{'type': 'paragraph', 'content': [
        {'type': 'text', 'text': 'Body with two references'},
        {'type': 'footnote', 'attrs': {'id': 'a' * 32, 'kind': 'footnote', 'text': 'FOOTNOTE PRINTED exactly once.'}},
        {'type': 'footnote', 'attrs': {'id': 'b' * 32, 'kind': 'endnote', 'text': 'ENDNOTE PRINTED & retained.'}},
    ]}])
    browser.expect(browser.page.locator('#folioNotes')).to_contain_text('FOOTNOTE PRINTED')
    with print_pdf(browser) as pdf:
        text = '\n'.join(page.get_text() for page in pdf)
        assert text.count('FOOTNOTE PRINTED exactly once.') == 1
        assert text.count('ENDNOTE PRINTED & retained.') == 1
        assert 'Body with two references' in text
        assert 'Edit footnote' not in text and 'OriginRAG' not in text


@pytest.mark.parametrize('attempt', range(5))
def test_print_refreshes_notes_after_undo_without_waiting_for_debounce(folio_browser, attempt):
    browser = folio_browser
    browser.page.add_init_script('''
      const originalSetTimeout = window.setTimeout.bind(window);
      window.setTimeout = (callback, delay, ...args) => originalSetTimeout(callback, delay === 180 ? 30000 : delay, ...args);
    ''')
    open_fixture(browser, [{'type': 'paragraph', 'content': [
        {'type': 'text', 'text': 'A retained reference'},
        {'type': 'footnote', 'attrs': {'id': 'a' * 32, 'kind': 'footnote', 'text': 'RESTORED NOTE MUST PRINT'}},
    ]}])
    browser.page.get_by_role('button', name='Edit footnote 1', exact=True).click()
    browser.page.get_by_role('button', name='Remove note', exact=True).click()
    browser.expect(browser.page.locator('#folioNotes')).to_have_count(0)
    browser.page.get_by_role('button', name='Quick undo', exact=True).click()
    with print_pdf(browser) as pdf:
        text = '\n'.join(page.get_text() for page in pdf)
        assert text.count('RESTORED NOTE MUST PRINT') == 1, browser.page.evaluate('''() => ({
          notes: document.getElementById('folioNotes')?.outerHTML,
          paper: document.getElementById('folioPaper')?.innerText,
          atPrint: window.folioPrintedNotes,
        })''')


def test_large_picture_inside_table_stays_visible_in_printed_pdf(folio_browser):
    browser = folio_browser
    source = 'data:image/png;base64,' + base64.b64encode(image_file()['buffer']).decode()
    open_fixture(browser, [{'type': 'table', 'content': [{'type': 'tableRow', 'content': [
        {'type': 'tableCell', 'attrs': {'colwidth': [160]}, 'content': [
            paragraph('Image in table'),
            {'type': 'image', 'attrs': {'src': source, 'width': 1200, 'height': 400,
                                      'alt': 'A blue rectangular diagram'}},
        ]},
        {'type': 'tableCell', 'attrs': {'colwidth': [340]},
         'content': [paragraph('The adjacent cell must remain readable.')]},
    ]}]}])
    picture = browser.page.locator('.tiptap img')
    browser.expect(picture).to_be_visible()
    geometry = picture.evaluate('''image => ({
        width: image.getBoundingClientRect().width,
        cellWidth: image.closest('td').getBoundingClientRect().width
    })''')
    assert 0 < geometry['width'] <= geometry['cellWidth']
    with print_pdf(browser) as pdf:
        text = '\n'.join(page.get_text() for page in pdf)
        assert 'Image in table' in text and 'The adjacent cell must remain readable.' in text
        images = [(page, image) for page in pdf for image in page.get_images()]
        assert len(images) == 1
        page, embedded = images[0]
        rectangles = page.get_image_rects(embedded[0])
        assert len(rectangles) == 1
        assert rectangles[0].width > 0 and rectangles[0].height > 0
        assert page.rect.contains(rectangles[0])


def test_long_document_keeps_last_paragraph_on_edit_save_and_reload(folio_browser, record_property):
    browser = folio_browser
    paragraphs = [paragraph(f'Paragraph {index:04d}. A longer document must retain its content.')
                  for index in range(1200)]
    document, open_seconds = open_fixture(browser, paragraphs)
    record_property('editor_open_seconds', round(open_seconds, 3))
    page = browser.page
    editor = page.get_by_role('textbox', name='Document content', exact=True)
    browser.expect(editor.locator('p')).to_have_count(1200)
    editor.locator('p').last.evaluate('''element => {
      element.closest('[contenteditable]').focus();
      const range = document.createRange();
      range.selectNodeContents(element);
      range.collapse(false);
      const selection = getSelection();
      selection.removeAllRanges(); selection.addRange(range);
    }''')
    page.keyboard.type(' Final change survived.')
    browser.expect(editor.locator('p').last).to_contain_text('Final change survived.')
    browser.expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    browser.expect(editor.locator('p')).to_have_count(1200)
    browser.expect(editor.locator('p').last).to_contain_text('Final change survived.')
    saved = request_json(browser.port, path='/api/folio?id=' + document['id'], headers=browser.headers)
    assert len(saved['content']['content']) == 1200
    assert saved['content']['content'][0]['content'] == document['content']['content'][0]['content']
