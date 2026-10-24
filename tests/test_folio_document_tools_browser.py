"""Document tools exercise real editing, persistence, and independent DOCX parsing."""

import io
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from test_folio_http import folio_server as folio_server, request_json
from test_folio_browser import folio_browser as folio_browser, saved_document


WORD = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
WORD_ID = '{' + WORD['w'] + '}id'
DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def paragraph(text):
    return {'type': 'paragraph', 'content': [{'type': 'text', 'text': text}]}


def heading(text, level=1):
    return {'type': 'heading', 'attrs': {'level': level}, 'content': [{'type': 'text', 'text': text}]}


def seed(browser, content):
    document = request_json(browser.port, 'POST', headers=browser.headers,
                            payload={'action': 'create', 'title': 'Document tools regression'})
    payload = {key: document[key] for key in ('id', 'revision', 'title', 'page', 'comments')}
    payload.update(action='save', content={'type': 'doc', 'content': content})
    request_json(browser.port, 'POST', headers=browser.headers, payload=payload)
    browser.page.goto(browser.base + '/folio?id=' + document['id'])
    editor = browser.page.get_by_role('textbox', name='Document content', exact=True)
    browser.expect(editor).to_be_visible()
    return editor


def caret_at_end(locator):
    locator.evaluate("""node => {
      node.closest('[contenteditable]').focus();
      const range = document.createRange(); range.selectNodeContents(node); range.collapse(false);
      const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
    }""")


def insert_note(browser, kind, text):
    page = browser.page
    page.get_by_role('tab', name='References', exact=True).click()
    page.get_by_role('button', name=f'Insert {kind}', exact=True).click()
    dialog = page.get_by_role('dialog', name=f'Insert {kind}', exact=True)
    dialog.get_by_label('Note text', exact=True).fill(text)
    dialog.get_by_role('button', name='Insert note', exact=True).click()
    browser.expect(dialog).to_have_count(0)


def download(browser, format_name):
    page = browser.page
    page.get_by_role('tab', name='File', exact=True).click()
    with page.expect_download() as pending:
        page.get_by_role('button', name='Download ' + format_name, exact=True).click()
    return Path(pending.value.path()).read_bytes()


def without_private_snapshot(content):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as original, zipfile.ZipFile(output, 'w') as cleaned:
        for name in original.namelist():
            if name != 'customXml/folio.json':
                cleaned.writestr(name, original.read(name))
    return output.getvalue()


def import_docx(browser, content):
    previous = browser.page.url
    browser.page.locator('#folioImport').set_input_files({
        'name': 'external-roundtrip.docx', 'mimeType': DOCX_MIME, 'buffer': content})
    browser.expect(browser.page).not_to_have_url(previous)
    browser.expect(browser.page.locator('#folioSaveStatus')).to_have_text('Saved')


def test_notes_insert_separate_numbering_save_and_reload(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = seed(browser, [paragraph('A body paragraph with notes.')])
    for kind, text in [('footnote', 'First footnote'), ('endnote', 'First endnote'), ('footnote', 'Second footnote')]:
        caret_at_end(editor.locator('p').first)
        insert_note(browser, kind, text)
    references = editor.locator('[data-folio-note-id]')
    expect(references).to_have_text(['1', '1', '2'])
    expect(references.nth(0)).to_have_attribute('data-note-kind', 'footnote')
    expect(references.nth(1)).to_have_attribute('data-note-kind', 'endnote')
    expect(page.locator('#folioNotes')).to_contain_text('First footnote')
    expect(page.locator('#folioNotes')).to_contain_text('First endnote')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    nodes = saved_document(browser)['content']['content'][0]['content']
    notes = [node for node in nodes if node['type'] == 'footnote']
    assert [note['attrs']['kind'] for note in notes] == ['footnote', 'endnote', 'footnote']
    assert len({note['attrs']['id'] for note in notes}) == 3
    page.reload()
    expect(references).to_have_text(['1', '1', '2'])
    expect(page.get_by_role('button', name='Edit footnote 2', exact=True)).to_have_text('Second footnote')


def test_footnote_annotates_selected_text_without_replacing_it(folio_browser):
    browser = folio_browser
    editor = seed(browser, [paragraph('Every selected word must remain.')])
    editor.select_text()
    insert_note(browser, 'footnote', 'Annotation, not replacement')
    browser.expect(editor).to_contain_text('Every selected word must remain.')
    browser.expect(editor.locator('[data-folio-note-id]')).to_have_count(1)
    browser.expect(browser.page.locator('#folioSaveStatus')).to_have_text('Saved')
    browser.page.reload()
    browser.expect(editor).to_contain_text('Every selected word must remain.')
    browser.expect(browser.page.get_by_role('button', name='Edit footnote 1', exact=True)).to_have_text('Annotation, not replacement')


def test_note_edit_type_change_delete_and_undo(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = seed(browser, [{'type': 'paragraph', 'content': [
        {'type': 'text', 'text': 'Keep body text '},
        {'type': 'footnote', 'attrs': {'id': '1' * 32, 'kind': 'footnote', 'text': 'First note'}},
        {'type': 'footnote', 'attrs': {'id': '2' * 32, 'kind': 'footnote', 'text': 'Change this note'}},
        {'type': 'footnote', 'attrs': {'id': '3' * 32, 'kind': 'endnote', 'text': 'Existing endnote'}},
    ]}])
    page.get_by_role('button', name='Edit footnote 2', exact=True).click()
    dialog = page.get_by_role('dialog', name='Edit note', exact=True)
    dialog.get_by_label('Note text', exact=True).fill('Revised endnote')
    dialog.get_by_label('Note type', exact=True).select_option('endnote')
    dialog.get_by_role('button', name='Save note', exact=True).click()
    expect(editor.locator('[data-folio-note-id]')).to_have_text(['1', '1', '2'])
    expect(page.get_by_role('button', name='Edit endnote 1', exact=True)).to_have_text('Revised endnote')
    editor.locator('[data-folio-note-id]').first.click()
    page.get_by_role('dialog', name='Edit note', exact=True).get_by_role('button', name='Remove note', exact=True).click()
    expect(editor.locator('[data-folio-note-id]')).to_have_text(['1', '2'])
    expect(page.locator('#folioNotes h2')).to_have_text(['Endnotes'])
    expect(editor).to_contain_text('Keep body text')
    page.get_by_role('button', name='Quick undo', exact=True).click()
    expect(editor.locator('[data-folio-note-id]')).to_have_count(3)
    expect(page.get_by_role('button', name='Edit footnote 1', exact=True)).to_have_text('First note')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    expect(editor.locator('[data-folio-note-id]')).to_have_count(3)


def test_notes_export_real_word_parts_and_safe_html(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    first = 'Quoted <img src=x onerror=alert(1)> & a café footnote.'
    second = 'An endnote with α and a second line.\nDetails retained.'
    editor = seed(browser, [{'type': 'paragraph', 'content': [
        {'type': 'text', 'text': 'Supported text '},
        {'type': 'footnote', 'attrs': {'id': '4' * 32, 'kind': 'footnote', 'text': first}},
        {'type': 'footnote', 'attrs': {'id': '5' * 32, 'kind': 'endnote', 'text': second}},
    ]}])
    html = download(browser, 'HTML').decode()
    assert '&lt;img src=x onerror=alert(1)&gt;' in html
    assert '<img src=x' not in html
    assert 'doc-noteref' in html and 'Footnotes' in html and 'Endnotes' in html
    content = download(browser, 'DOCX')
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        document = ET.fromstring(archive.read('word/document.xml'))
        assert len(document.findall('.//w:footnoteReference', WORD)) == 1
        assert len(document.findall('.//w:endnoteReference', WORD)) == 1
        footnotes = ET.fromstring(archive.read('word/footnotes.xml'))
        endnotes = ET.fromstring(archive.read('word/endnotes.xml'))
        assert first in ''.join(footnotes.itertext())
        assert 'An endnote with α' in ''.join(endnotes.itertext())
        assert 'Details retained.' in ''.join(endnotes.itertext())
    import_docx(browser, without_private_snapshot(content))
    expect(editor.locator('[data-folio-note-id]')).to_have_text(['1', '1'])
    expect(page.get_by_role('button', name='Edit footnote 1', exact=True)).to_have_text(first)
    expect(page.get_by_role('button', name='Edit endnote 1', exact=True)).to_contain_text('Details retained.')
    assert page.locator('#folioNotes img, #folioNotes script').count() == 0


def test_design_set_preserves_marks_code_and_undo(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = seed(browser, [heading('A manuscript title'), {'type': 'paragraph', 'content': [
        {'type': 'text', 'text': 'Important', 'marks': [{'type': 'bold'}, {'type': 'italic'}]},
        {'type': 'text', 'text': ' ordinary text.'},
    ]}, {'type': 'codeBlock', 'content': [{'type': 'text', 'text': 'print("unchanged")'}]},
        paragraph('After the code block.')])
    initial = editor.inner_html()
    page.get_by_role('tab', name='Design', exact=True).click()
    page.get_by_role('button', name='Apply Manuscript style set', exact=True).click()
    expect(editor.locator('strong em, em strong')).to_have_text('Important')
    expect(editor.locator('h1 span')).to_have_css('font-family', re.compile(r'^"?Times New Roman"?$'))
    expect(editor.locator('p span').first).to_have_css('font-size', '16px')
    assert editor.locator('pre span[style]').count() == 0
    expect(editor.locator('pre')).to_have_text('print("unchanged")')
    page.get_by_role('button', name='Quick undo', exact=True).click()
    assert editor.inner_html() == initial
    page.get_by_role('button', name='Quick redo', exact=True).click()
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    expect(editor.locator('h1 span')).to_have_css('font-family', re.compile(r'^"?Times New Roman"?$'))
    expect(editor.locator('strong em, em strong')).to_have_text('Important')


def test_paragraph_dialog_selected_range_spacing_persists(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = seed(browser, [paragraph('First selected paragraph.'), paragraph('Second selected paragraph.'),
                            paragraph('Outside the selection.')])
    editor.evaluate("""node => {
      node.focus(); const paragraphs = node.querySelectorAll('p');
      const range = document.createRange(); range.setStart(paragraphs[0].firstChild, 0);
      range.setEnd(paragraphs[1].lastChild, paragraphs[1].lastChild.textContent.length);
      const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
    }""")
    page.get_by_role('tab', name='Layout', exact=True).click()
    page.get_by_role('button', name='Paragraph spacing', exact=True).click()
    dialog = page.get_by_role('dialog', name='Paragraph spacing', exact=True)
    for label, value in [('Before (pt)', '12'), ('After (pt)', '18'), ('Line spacing', '1.75'), ('Indent (½-inch steps)', '2')]:
        dialog.get_by_label(label, exact=True).fill(value)
    dialog.get_by_role('button', name='Apply spacing', exact=True).click()
    expect(dialog).to_have_count(0)
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    nodes = saved_document(browser)['content']['content']
    for node in nodes[:2]:
        for key, value in {'spaceBefore': 12, 'spaceAfter': 18, 'lineHeight': 1.75, 'indent': 2}.items():
            assert float(node['attrs'][key]) == value
    assert nodes[2].get('attrs', {}).get('indent', 0) == 0
    assert nodes[2].get('attrs', {}).get('spaceBefore', 0) == 0
    page.reload()
    expect(editor.locator('p').first).to_have_css('margin-top', '16px')
    expect(editor.locator('p').nth(1)).to_have_css('margin-bottom', '24px')


def test_imported_fractional_paragraph_spacing_can_be_applied_unchanged(folio_browser):
    browser = folio_browser
    content = paragraph('Imported fractional spacing must stay usable.')
    content['attrs'] = {'spaceBefore': 12.5, 'spaceAfter': 7.92, 'lineHeight': '1.12'}
    editor = seed(browser, [content])
    caret_at_end(editor.locator('p').first)
    browser.page.get_by_role('tab', name='Layout', exact=True).click()
    browser.page.get_by_role('button', name='Paragraph spacing', exact=True).click()
    dialog = browser.page.get_by_role('dialog', name='Paragraph spacing', exact=True)
    browser.expect(dialog.get_by_label('Before (pt)', exact=True)).to_have_value('12.5')
    browser.expect(dialog.get_by_label('After (pt)', exact=True)).to_have_value('7.92')
    dialog.get_by_role('button', name='Apply spacing', exact=True).click()
    browser.expect(dialog).to_have_count(0)
    browser.expect(browser.page.locator('#folioSaveStatus')).to_have_text('Saved')
    attrs = saved_document(browser)['content']['content'][0]['attrs']
    assert attrs['spaceBefore'] == 12.5 and attrs['spaceAfter'] == 7.92
    assert attrs['lineHeight'] == '1.12'


def test_table_of_contents_updates_links_and_preserves_body(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = seed(browser, [heading('Background'), paragraph('Keep this explanation.'), heading('Results', 2),
                            paragraph('Keep these results.')])
    page.get_by_role('tab', name='References', exact=True).click()
    page.get_by_role('button', name='Table of contents', exact=True).click()
    entries = editor.locator('p[data-folio-toc-entry]')
    expect(entries).to_have_text(['Contents', 'Background', 'Results'])
    ids = editor.locator('h1,h2').evaluate_all('nodes => nodes.map(node => node.id)')
    assert len(set(ids)) == 2 and all(item.startswith('folio-anchor-') for item in ids)
    assert entries.locator('a').evaluate_all('nodes => nodes.map(node => node.getAttribute("href"))') == ['#' + item for item in ids]
    editor.locator('h2').fill('Updated results')
    page.get_by_role('tab', name='References', exact=True).click()
    page.get_by_role('button', name='Update table of contents', exact=True).click()
    expect(entries).to_have_text(['Contents', 'Background', 'Updated results'])
    assert editor.locator('h1,h2').evaluate_all('nodes => nodes.map(node => node.id)') == ids
    expect(editor).to_contain_text('Keep this explanation.')
    expect(editor).to_contain_text('Keep these results.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    expect(entries).to_have_text(['Contents', 'Background', 'Updated results'])
    page.get_by_role('tab', name='References', exact=True).click()
    page.get_by_role('button', name='Table of contents', exact=True).click()
    expect(entries).to_have_count(3)
    import_docx(browser, without_private_snapshot(download(browser, 'DOCX')))
    expect(entries).to_have_text(['Contents', 'Background', 'Updated results'])
    assert editor.locator('h1,h2').evaluate_all('nodes => nodes.map(node => node.id)') == ids
    page.get_by_role('tab', name='References', exact=True).click()
    page.get_by_role('button', name='Update table of contents', exact=True).click()
    expect(entries).to_have_count(3)


def test_cross_reference_navigation_and_docx_roundtrip(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = seed(browser, [heading('Target section'), *[paragraph(f'Intervening paragraph {index}.') for index in range(30)],
                            paragraph('See this section: ')])
    caret_at_end(editor.locator('p').last)
    page.get_by_role('tab', name='References', exact=True).click()
    page.get_by_role('button', name='Insert cross-reference', exact=True).click()
    dialog = page.get_by_role('dialog', name='Insert cross-reference', exact=True)
    dialog.get_by_role('button', name='Insert reference', exact=True).click()
    link = editor.locator('a[href^="#folio-anchor-"]')
    expect(link).to_have_text('Target section')
    anchor = editor.locator('h1').get_attribute('id')
    assert link.get_attribute('href') == '#' + anchor
    link.click()
    expect(editor.locator('h1')).to_be_in_viewport()
    assert '/folio?id=' in page.url
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    content = download(browser, 'DOCX')
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        document = ET.fromstring(archive.read('word/document.xml'))
        anchors = [item.attrib.get('{' + WORD['w'] + '}anchor') for item in document.findall('.//w:hyperlink', WORD)]
        assert 'folio_' + anchor.removeprefix('folio-anchor-') in anchors
    import_docx(browser, without_private_snapshot(content))
    expect(link).to_have_text('Target section')
    assert link.get_attribute('href') == '#' + editor.locator('h1').get_attribute('id')
    page.reload()
    expect(link).to_have_text('Target section')


def test_symbol_gallery_inserts_literal_text_not_html(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = seed(browser, [paragraph('Symbols: ')])
    caret_at_end(editor.locator('p'))
    page.get_by_role('tab', name='Insert', exact=True).click()
    page.get_by_role('button', name='Insert symbol', exact=True).click()
    dialog = page.get_by_role('dialog', name='Insert symbol', exact=True)
    dialog.get_by_role('button', name='Symbol α', exact=True).click()
    dialog.get_by_role('button', name='Symbol ≤', exact=True).click()
    expect(dialog.get_by_label('Symbol text', exact=True)).to_have_value('α≤')
    text = 'α ≤ β <img src=x onerror=alert(1)> & Ω'
    dialog.get_by_label('Symbol text', exact=True).fill(text)
    dialog.get_by_role('button', name='Insert symbols', exact=True).click()
    expect(editor).to_contain_text(text)
    assert editor.locator('img, script').count() == 0
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    expect(editor).to_contain_text(text)
    html = download(browser, 'HTML').decode()
    assert '&lt;img src=x onerror=alert(1)&gt;' in html
