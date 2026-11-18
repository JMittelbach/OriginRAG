import base64
import io
import os
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import pytest

from test_folio_browser import folio_browser as folio_browser, saved_document
from test_folio_http import folio_server as folio_server
from test_folio_document_tools_browser import seed, paragraph, download, WORD
from test_folio_docx_edges import png


def select_contents(locator):
    locator.evaluate('''node => {
      node.closest('[contenteditable]').focus();
      const range = document.createRange(); range.selectNodeContents(node);
      const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
    }''')


def test_custom_font_sizes_growth_validation_and_docx(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = seed(browser, [paragraph('Precise type size.')])
    editor.press('ControlOrMeta+a')
    size = page.get_by_role('spinbutton', name='Font size', exact=True)
    size.fill('17.5')
    size.press('Enter')
    expect(editor.locator('[style*="font-size: 17.5pt"]')).to_have_text('Precise type size.')
    page.get_by_role('button', name='Increase font size', exact=True).click()
    expect(size).to_have_value('18.5')
    page.get_by_role('button', name='Decrease font size', exact=True).click()
    expect(size).to_have_value('17.5')
    for value in ['12', '24', '9.5', '17.5']:
        size.fill(value)
        size.press('Enter')
        expect(editor).to_have_text('Precise type size.')
    size.fill('999')
    size.press('Enter')
    expect(page.locator('#folioNotice')).to_contain_text('6 to 96')
    expect(editor.locator('[style*="font-size: 17.5pt"]')).to_have_count(1)
    size.press('Escape')
    expect(size).to_have_value('17.5')
    with zipfile.ZipFile(io.BytesIO(download(browser, 'DOCX'))) as archive:
        document = ET.fromstring(archive.read('word/document.xml'))
        assert any(node.attrib.get('{' + WORD['w'] + '}val') == '35' for node in document.findall('.//w:sz', WORD))


def test_selected_word_statistics_and_find_keep_document_untouched(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = seed(browser, [paragraph('Mosaik 🪶'), paragraph('Noch ein Mosaik 🪶')])
    revision = saved_document(browser)['revision']
    stats = page.locator('#folioWordCount')
    expect(stats).to_have_text('6 words')
    select_contents(editor.locator('p').first)
    expect(stats).to_have_text('2 of 6 words')
    stats.click()
    details = page.get_by_role('dialog', name='Document statistics')
    expect(details.get_by_role('heading', name='Selection')).to_be_visible()
    expect(details.get_by_text('8 characters (including spaces)', exact=True)).to_be_visible()
    details.get_by_role('button', name='Done').click()
    editor.press('ControlOrMeta+f')
    find = page.get_by_role('searchbox', name='Find text')
    expect(find).to_have_value('Mosaik 🪶')
    expect(editor.locator('.folio-search-hit')).to_have_count(2)
    find.fill('Mosaik')
    page.get_by_label('Whole words', exact=True).check()
    replace = page.get_by_role('textbox', name='Replace with')
    replace.fill('Entwurf')
    replace.press('ControlOrMeta+f')
    expect(find).to_be_focused()
    expect(find).to_have_value('Mosaik')
    expect(replace).to_have_value('Entwurf')
    expect(page.get_by_label('Whole words', exact=True)).to_be_checked()
    replace.press('Escape')
    expect(editor).to_be_focused()
    expect(editor.locator('.folio-search-hit')).to_have_count(0)
    editor.press('ArrowRight')
    expect(stats).to_have_text('6 words')
    assert saved_document(browser)['revision'] == revision


def test_replace_selected_occurrence_not_first_and_undo(folio_browser):
    page, expect = folio_browser.page, folio_browser.expect
    editor = seed(folio_browser, [paragraph('Echo'), paragraph('Echo'), paragraph('Echo')])
    select_contents(editor.locator('p').last)
    expect(page.locator('#folioWordCount')).to_have_text('1 of 3 words')
    editor.press('ControlOrMeta+f')
    expect(page.locator('#folioSidebar [role=status]')).to_have_text('3 of 3 matches')
    page.get_by_role('textbox', name='Replace with').fill('Changed')
    page.get_by_role('button', name='Replace current').click()
    expect(editor.locator('p')).to_have_text(['Echo', 'Echo', 'Changed'])
    page.get_by_role('button', name='Quick undo').click()
    expect(editor.locator('p')).to_have_text(['Echo', 'Echo', 'Echo'])


def test_multiline_selection_counts_words_without_seeding_find(folio_browser):
    page, expect = folio_browser.page, folio_browser.expect
    editor = seed(folio_browser, [
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'First'},
                                          {'type': 'hardBreak'},
                                          {'type': 'text', 'text': 'second'}]},
        paragraph('Third')])
    stats = page.locator('#folioWordCount')
    expect(stats).to_have_text('3 words')
    select_contents(editor.locator('p').first)
    expect(stats).to_have_text('2 of 3 words')
    editor.press('ControlOrMeta+f')
    expect(page.get_by_role('searchbox', name='Find text')).to_have_value('')
    expect(editor.locator('.folio-search-hit')).to_have_count(0)


def test_format_painter_preserves_links_and_heading_structure_and_undo(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = seed(browser, [
        {'type': 'paragraph', 'attrs': {'lineHeight': '2', 'textAlign': 'center'}, 'content': [
            {'type': 'text', 'text': 'Source style', 'marks': [{'type': 'bold'},
                {'type': 'textStyle', 'attrs': {'fontSize': '18pt', 'color': '#123abc'}}]}]},
        {'type': 'heading', 'attrs': {'level': 2}, 'content': [
            {'type': 'text', 'text': 'Destination link', 'marks': [{'type': 'italic'},
                {'type': 'link', 'attrs': {'href': 'https://example.org/kept'}}]}]},
    ])
    select_contents(editor.locator('p').first)
    expect(page.get_by_role('button', name='Bold', exact=True)).to_have_attribute('aria-pressed', 'true')
    painter = page.get_by_role('button', name='Format painter', exact=True)
    painter.click()
    expect(painter).to_have_attribute('aria-pressed', 'true')
    select_contents(editor.locator('h2'))
    expect(page.get_by_role('button', name='Italic', exact=True)).to_have_attribute('aria-pressed', 'true')
    editor.dispatch_event('pointerup')
    expect(editor.locator('h2 strong')).to_have_text('Destination link')
    expect(editor.locator('h2 a')).to_have_attribute('href', 'https://example.org/kept')
    expect(editor.locator('h2 em')).to_have_count(0)
    expect(editor.locator('h2')).to_have_css('text-align', 'center')
    expect(painter).to_have_attribute('aria-pressed', 'false')
    page.get_by_role('button', name='Quick undo', exact=True).click()
    expect(editor.locator('h2 em')).to_have_text('Destination link')
    expect(editor.locator('h2 strong')).to_have_count(0)
    painter.click()
    page.keyboard.press('Escape')
    expect(painter).to_have_attribute('aria-pressed', 'false')


def test_paragraph_marks_are_view_only_and_do_not_dirty_or_export(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = seed(browser, [paragraph('First paragraph.'), paragraph('Second paragraph.')])
    before = saved_document(browser)
    toggle = page.get_by_role('button', name='Show paragraph marks', exact=True)
    toggle.click()
    expect(toggle).to_have_attribute('aria-pressed', 'true')
    assert editor.locator('p').first.evaluate("node => getComputedStyle(node, '::after').content") == '"¶"'
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    assert saved_document(browser)['revision'] == before['revision']
    assert '¶' not in download(browser, 'HTML').decode()
    page.emulate_media(media='print')
    assert editor.locator('p').first.evaluate("node => getComputedStyle(node, '::after').content") in ('none', 'normal')


def test_find_case_words_single_replace_and_atomic_undo(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = seed(browser, [paragraph('Cat cat scatter cat_2 🪶 cat.')])
    page.get_by_role('button', name='Find and replace', exact=True).click()
    find = page.get_by_role('searchbox', name='Find text')
    find.fill('cat')
    expect(editor.locator('.folio-search-hit')).to_have_count(5)
    page.get_by_label('Whole words', exact=True).check()
    expect(editor.locator('.folio-search-hit')).to_have_count(3)
    page.get_by_label('Match case', exact=True).check()
    expect(editor.locator('.folio-search-hit')).to_have_count(2)
    page.get_by_label('Replace with', exact=True).fill('dog')
    page.get_by_role('button', name='Replace current', exact=True).click()
    expect(editor).to_have_text('Cat dog scatter cat_2 🪶 cat.')
    expect(editor.locator('.folio-search-current')).to_have_text('cat')
    page.get_by_role('button', name='Quick undo', exact=True).click()
    expect(editor).to_have_text('Cat cat scatter cat_2 🪶 cat.')
    page.get_by_role('button', name='Replace all', exact=True).click()
    expect(editor).to_have_text('Cat dog scatter cat_2 🪶 dog.')
    page.get_by_role('button', name='Quick undo', exact=True).click()
    expect(editor).to_have_text('Cat cat scatter cat_2 🪶 cat.')
    find.focus()
    find.press('Shift+Enter')
    expect(find).to_be_focused()
    expect(page.locator('#folioSidebar')).to_contain_text('2 of 2 matches')
    find.press('Escape')
    expect(editor.locator('.folio-search-hit')).to_have_count(0)


def test_search_across_marks_notes_emoji_and_live_edits(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = seed(browser, [{'type': 'paragraph', 'content': [
        {'type': 'text', 'text': '🪶 co'}, {'type': 'text', 'text': 'operate', 'marks': [{'type': 'bold'}]},
        {'type': 'footnote', 'attrs': {'id': 'a' * 32, 'text': 'Footnote text', 'kind': 'footnote'}},
        {'type': 'text', 'text': 'operate 🪶'},
    ]}])
    before = saved_document(browser)
    page.get_by_role('button', name='Find and replace', exact=True).click()
    find = page.get_by_role('searchbox', name='Find text')
    find.fill('cooperate')
    expect(page.locator('#folioSidebar')).to_contain_text('1 matches')
    expect(editor.locator('.folio-search-hit')).to_have_count(2)
    find.press('Enter')
    expect(find).to_be_focused()
    assert ''.join(editor.locator('.folio-search-current').all_text_contents()) == 'cooperate'
    assert saved_document(browser)['revision'] == before['revision']
    page.get_by_label('Replace with', exact=True).fill('work')
    page.get_by_role('button', name='Replace current', exact=True).click()
    expect(editor.locator('[data-folio-note-id]')).to_have_count(1)
    find.fill('🪶')
    expect(editor.locator('.folio-search-hit')).to_have_count(2)
    editor.press('ControlOrMeta+End')
    editor.press('End')
    editor.press('Space')
    editor.press('x')
    expect(page.locator('#folioSidebar')).to_contain_text('2 matches')
    assert 'folio-search' not in download(browser, 'HTML').decode()


@pytest.mark.parametrize('keyboard', [False, True])
def test_table_grid_insert_header_choice_and_undo(folio_browser, keyboard):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = seed(browser, [paragraph('A table follows.')])
    editor.press('ControlOrMeta+End')
    page.get_by_role('tab', name='Insert', exact=True).click()
    page.get_by_role('button', name='Insert table', exact=True).click()
    dialog = page.get_by_role('dialog', name='Insert table', exact=True)
    dialog.get_by_label('Header row', exact=True).uncheck()
    if keyboard:
        first = dialog.get_by_role('gridcell', name='1 columns, 1 rows', exact=True)
        first.focus()
        for key in ['ArrowRight', 'ArrowRight', 'ArrowDown', 'Enter']:
            page.keyboard.press(key)
    else:
        dialog.get_by_role('gridcell', name='3 columns, 2 rows', exact=True).click()
    expect(dialog).to_have_count(0)
    expect(editor.locator('table tr')).to_have_count(2)
    expect(editor.locator('table td')).to_have_count(6)
    expect(editor.locator('table th')).to_have_count(0)
    page.get_by_role('button', name='Quick undo', exact=True).click()
    expect(editor.locator('table')).to_have_count(0)


def test_picture_presets_keep_ratio_and_can_be_undone(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    image = 'data:image/png;base64,' + base64.b64encode(png(2, 2)).decode()
    editor = seed(browser, [paragraph('Picture sizes'), {'type': 'image', 'attrs': {
        'src': image, 'width': 120, 'height': 80, 'alt': 'Kept description'}}, paragraph('After')])
    picture = editor.locator('img')
    picture.click()
    page.get_by_role('tab', name='Picture', exact=True).click()
    page.get_by_role('button', name='Fit picture to page', exact=True).click()
    width = round(editor.evaluate('node => node.clientWidth'))
    expect(picture).to_have_attribute('width', str(width))
    expect(picture).to_have_attribute('height', str(round(width * 2 / 3)))
    page.get_by_role('button', name='Half-page picture', exact=True).click()
    expect(picture).to_have_attribute('width', str(round(width / 2)))
    expect(picture).to_have_attribute('alt', 'Kept description')
    page.get_by_role('button', name='Quick undo', exact=True).click()
    expect(picture).to_have_attribute('width', str(width))


def test_picture_with_only_width_uses_intrinsic_ratio(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    image = 'data:image/png;base64,' + base64.b64encode(png(2, 2)).decode()
    editor = seed(browser, [{'type': 'image', 'attrs': {'src': image, 'width': 120}}, paragraph('After')])
    picture = editor.locator('img')
    picture.click()
    page.get_by_role('tab', name='Picture', exact=True).click()
    page.get_by_role('button', name='Half-page picture', exact=True).click()
    expect(picture).to_have_attribute('height', picture.get_attribute('width'))


def test_format_painter_waits_until_keyboard_selection_is_complete(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = seed(browser, [
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Source', 'marks': [{'type': 'bold'}]}]},
        paragraph('Keyboard destination'),
    ])
    select_contents(editor.locator('p').first)
    expect(page.get_by_role('button', name='Bold', exact=True)).to_have_attribute('aria-pressed', 'true')
    painter = page.get_by_role('button', name='Format painter', exact=True)
    painter.click()
    editor.evaluate('node => { node.editor.commands.setTextSelection(9); node.editor.view.focus(); }')
    page.keyboard.down('Shift')
    page.keyboard.press('ArrowRight')
    expect(painter).to_have_attribute('aria-pressed', 'true')
    for _ in range(len('Keyboard destination') - 1):
        page.keyboard.press('ArrowRight')
    page.wait_for_function("getSelection().toString() === 'Keyboard destination'")
    page.keyboard.up('Shift')
    expect(editor.locator('p strong').last).to_have_text('Keyboard destination')
    expect(painter).to_have_attribute('aria-pressed', 'false')


def test_search_navigation_reuses_matches_and_caps_highlight_nodes(folio_browser):
    browser = folio_browser
    page = browser.page
    seed(browser, [paragraph('One background paragraph.')])
    result = page.evaluate('''async () => {
      const {createFolioEditor, folioSearchState} = await import('/assets/folio/editor.mjs');
      const host = document.createElement('div'); document.body.append(host);
      const editor = createFolioEditor(host, {type:'doc',content:[{type:'paragraph',content:[{type:'text',text:'hit '.repeat(2500)}]}]});
      editor.commands.setFolioSearch('hit');
      const first = folioSearchState(editor).matches;
      editor.commands.setFolioSearch('hit', {}, 2499);
      const result = {count:first.length, cached:first === folioSearchState(editor).matches,
        highlights:host.querySelectorAll('.folio-search-hit').length,
        active:host.querySelectorAll('.folio-search-current').length,
        clean:!editor.getHTML().includes('folio-search')};
      editor.destroy(); host.remove(); return result;
    }''')
    assert result == {'count': 2500, 'cached': True, 'highlights': 2001, 'active': 1, 'clean': True}


def test_search_literals_unicode_boundaries_and_atom_offsets(folio_browser):
    browser = folio_browser
    seed(browser, [paragraph('Search edge cases')])
    result = browser.page.evaluate('''async () => {
      const {createFolioEditor} = await import('/assets/folio/editor.mjs');
      const {findTextMatches} = await import('/assets/folio/text-search.mjs');
      const host = document.createElement('div'); document.body.append(host);
      const editor = createFolioEditor(host, {type:'doc',content:[{type:'paragraph',content:[
        {type:'text',text:'a.b aXb C++ c++ café CAFÉ cafe\u0301 x_café 🪶 '},
        {type:'footnote',attrs:{id:'b'.repeat(32),text:'Hidden needle',kind:'footnote'}},
        {type:'text',text:'needle'}]}, {type:'paragraph',content:[{type:'text',text:'new paragraph'}]}]});
      const search = (query, options) => findTextMatches(editor.state.doc, query, options);
      const result = {literal:search('a.b').length, plus:search('C++').length,
        case:search('C++',{matchCase:true}).length, words:search('café',{wholeWord:true}).length,
        combining:search('cafe',{wholeWord:true}).length, empty:search('').length,
        across:search('needle new').length, hidden:search('Hidden').length,
        resolved:search('needle').map(item => editor.state.doc.textBetween(item.from,item.to))};
      editor.destroy(); host.remove(); return result;
    }''')
    assert result == {'literal': 1, 'plus': 2, 'case': 1, 'words': 2, 'combining': 0,
                      'empty': 0, 'across': 0, 'hidden': 0, 'resolved': ['needle']}


@pytest.mark.parametrize('theme,width', [('light', 1500), ('dark', 1500), ('black', 390)])
def test_new_controls_and_table_picker_fit_small_screens(folio_browser, theme, width):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    page.set_viewport_size({'width': width, 'height': 950})
    seed(browser, [paragraph('A familiar writing workspace.')])
    page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
    assert page.locator('#folioRibbon').bounding_box()['height'] <= 150
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    directory = Path(os.environ.get('ORIGINRAG_UI_REVIEW_DIR', str(browser.tmp)))
    page.screenshot(path=str(directory / f'folio-home-polish-{theme}.png'))
    page.get_by_role('tab', name='Insert', exact=True).click()
    page.get_by_role('button', name='Insert table', exact=True).click()
    dialog = page.get_by_role('dialog', name='Insert table', exact=True)
    expect(dialog.get_by_role('gridcell')).to_have_count(80)
    dialog.get_by_role('gridcell', name='4 columns, 3 rows', exact=True).hover()
    expect(dialog.locator('.chosen')).to_have_count(12)
    assert dialog.evaluate('node => node.scrollWidth <= node.clientWidth + 1')
    assert dialog.bounding_box()['width'] <= width
    page.screenshot(path=str(directory / f'folio-table-picker-{theme}.png'))
    page.keyboard.press('Escape')
    expect(dialog).to_have_count(0)
