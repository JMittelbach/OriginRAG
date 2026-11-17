"""Rich object controls exercise the actual editor and persistence API."""
import struct
import zlib
import base64
import os
from pathlib import Path

from test_folio_http import folio_server as folio_server
from test_folio_browser import folio_browser as folio_browser, create_document, saved_document


def screenshot_path(browser, filename):
    return Path(os.environ.get('ORIGINRAG_UI_REVIEW_DIR', str(browser.tmp))) / filename


def image_file():
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    width, height = 120, 60
    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('!IIBBBBB', width, height, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress((b'\x00' + b'\x70\x9a\xca' * width) * height))
    png += chunk(b'IEND', b'')
    return {'name': 'diagram.png', 'mimeType': 'image/png', 'buffer': png}


def add_image(browser):
    page, expect = browser.page, browser.expect
    editor = create_document(browser, 'A document with a diagram')
    editor.fill('A paragraph before the picture.')
    editor.press('ControlOrMeta+End')
    editor.press('Enter')
    page.get_by_role('tab', name='Insert', exact=True).click()
    page.get_by_role('button', name='Insert image', exact=True).click()
    dialog = page.get_by_role('dialog', name='Insert an image', exact=True)
    dialog.get_by_label('Image file', exact=True).set_input_files(image_file())
    dialog.get_by_label('Image description', exact=True).fill('A blue diagram')
    dialog.get_by_role('button', name='Insert image', exact=True).click()
    expect(editor.locator('img')).to_have_count(1)
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    return editor


def test_picture_dimensions_alt_caption_and_undo_survive_reload(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = add_image(browser)
    picture = editor.locator('img')
    picture.click()
    page.get_by_role('tab', name='Picture', exact=True).click()
    page.get_by_role('button', name='Picture size and description', exact=True).click()
    dialog = page.get_by_role('dialog', name='Picture size and description', exact=True)
    dialog.get_by_label('Picture width', exact=True).fill('240')
    expect(dialog.get_by_label('Picture height', exact=True)).to_have_value('120')
    dialog.get_by_label('Lock aspect ratio', exact=True).uncheck()
    dialog.get_by_label('Picture height', exact=True).fill('90')
    dialog.get_by_label('Picture description', exact=True).fill('The amended blue diagram')
    dialog.get_by_role('button', name='Apply picture changes', exact=True).click()
    expect(picture).to_have_attribute('width', '240')
    expect(picture).to_have_attribute('height', '90')
    expect(picture).to_have_attribute('alt', 'The amended blue diagram')
    page.get_by_role('button', name='Insert picture caption', exact=True).click()
    dialog = page.get_by_role('dialog', name='Insert caption', exact=True)
    dialog.get_by_label('Caption text', exact=True).fill('Figure 1. A useful diagram.')
    dialog.get_by_role('button', name='Insert caption', exact=True).click()
    expect(editor.locator('em')).to_have_text('Figure 1. A useful diagram.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    expect(picture).to_have_attribute('width', '240')
    expect(picture).to_have_attribute('height', '90')
    expect(picture).to_have_attribute('alt', 'The amended blue diagram')
    expect(editor.locator('em')).to_have_text('Figure 1. A useful diagram.')
    picture.click()
    page.get_by_role('tab', name='Picture', exact=True).click()
    page.get_by_role('button', name='Remove picture', exact=True).click()
    expect(picture).to_have_count(0)
    page.get_by_role('button', name='Undo', exact=True).click()
    expect(picture).to_have_count(1)
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')


def test_table_contextual_headers_selection_shading_and_structure(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser, 'A styled table')
    editor.click()
    page.get_by_role('tab', name='Insert', exact=True).click()
    page.get_by_role('button', name='Insert table', exact=True).click()
    dialog = page.get_by_role('dialog', name='Insert table', exact=True)
    dialog.get_by_label('Table rows', exact=True).fill('3')
    dialog.get_by_label('Table columns', exact=True).fill('3')
    dialog.get_by_role('button', name='Insert table', exact=True).click()
    cells = editor.locator('td, th')
    expect(cells).to_have_count(9)
    for index, text in enumerate(('Topic', 'Owner', 'Status', 'Draft review', 'Alex', 'Ready', 'Final proof', 'Sam', 'Next')):
        cells.nth(index).click()
        page.keyboard.insert_text(text)
    cells.nth(4).click()
    page.get_by_role('tab', name='Table', exact=True).click()
    page.get_by_role('button', name='Select row', exact=True).click()
    expect(editor.locator('.selectedCell')).to_have_count(3)
    page.get_by_label('Cell fill color', exact=True).fill('#ffeeaa')
    expect(editor.locator('[style*="background-color: rgb(255, 238, 170)"]')).to_have_count(3)
    page.get_by_role('button', name='Select column', exact=True).click()
    expect(editor.locator('.selectedCell')).to_have_count(3)
    page.get_by_role('button', name='Select table', exact=True).click()
    expect(editor.locator('.selectedCell')).to_have_count(9)
    cells.nth(4).click()
    page.get_by_role('button', name='Toggle header column', exact=True).click()
    expect(editor.locator('th')).to_have_count(5)
    page.get_by_role('button', name='Add row above', exact=True).click()
    expect(editor.locator('tr')).to_have_count(4)
    page.get_by_role('button', name='Add column before', exact=True).click()
    expect(editor.locator('tr').first.locator('td, th')).to_have_count(4)
    page.get_by_role('button', name='Insert table caption', exact=True).click()
    dialog = page.get_by_role('dialog', name='Insert caption', exact=True)
    dialog.get_by_label('Caption text', exact=True).fill('Table 1. Summary.')
    dialog.get_by_role('button', name='Insert caption', exact=True).click()
    expect(editor.locator('em')).to_have_text('Table 1. Summary.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    page.reload()
    expect(editor.locator('tr')).to_have_count(4)
    expect(editor.locator('em')).to_have_text('Table 1. Summary.')
    assert 'ffeeaa' in str(saved_document(browser)['content'])
    editor.locator('td').first.click()
    page.get_by_role('tab', name='Table', exact=True).click()
    assert page.locator('#folioRibbon').evaluate('element => element.scrollWidth <= element.clientWidth'), 'The table ribbon should fit the 1500px desktop viewport.'
    expect(page.locator('#folioWordCount')).not_to_have_text('0 words')
    path = screenshot_path(browser, 'folio-table-contextual.png')
    page.screenshot(path=str(path), full_page=True, animations='disabled')
    print(path)


def test_link_dialog_preserves_selection_and_cancel_restores_it(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser, 'Linked selection')
    editor.fill('Selected words stay selected.')
    editor.press('ControlOrMeta+a')
    page.get_by_role('tab', name='Insert', exact=True).click()
    page.get_by_role('button', name='Insert link', exact=True).click()
    dialog = page.get_by_role('dialog', name='Insert a link', exact=True)
    dialog.get_by_label('Link URL', exact=True).fill('#section')
    dialog.get_by_role('button', name='Cancel', exact=True).click()
    page.get_by_role('tab', name='Home', exact=True).click()
    page.get_by_role('button', name='Bold', exact=True).click()
    expect(editor.locator('strong')).to_have_text('Selected words stay selected.')
    page.get_by_role('tab', name='Insert', exact=True).click()
    page.get_by_role('button', name='Insert link', exact=True).click()
    dialog = page.get_by_role('dialog', name='Insert a link', exact=True)
    dialog.get_by_label('Link URL', exact=True).fill('#section')
    dialog.get_by_role('button', name='Apply link', exact=True).click()
    expect(editor.locator('a[href="#section"]')).to_have_text('Selected words stay selected.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')


def test_delayed_image_paste_keeps_original_location_while_typing(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser, 'Asynchronous paste')
    editor.fill('First paragraph.')
    editor.press('Enter')
    page.keyboard.insert_text('Second paragraph.')
    # Use the editor's public selection command to isolate asynchronous paste
    # mapping from browser-specific Cmd+Home/AllSelection arrow semantics.
    editor.evaluate('element => element.editor.commands.setTextSelection(1)')
    page.evaluate('''() => {
      const original = FileReader.prototype.readAsDataURL;
      FileReader.prototype.readAsDataURL = function(file) {
        window.releaseFolioImage = () => original.call(this, file);
      };
    }''')
    editor.evaluate('''(element, content) => {
      const bytes = Uint8Array.from(atob(content), value => value.charCodeAt(0));
      const transfer = new DataTransfer();
      transfer.items.add(new File([bytes], 'diagram.png', {type: 'image/png'}));
      const event = new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: transfer});
      // Firefox does not retain constructor-supplied clipboardData on an
      // untrusted event. Supply the real DataTransfer object explicitly.
      if (event.clipboardData?.files.length !== 1) Object.defineProperty(event, 'clipboardData', {value: transfer});
      if (event.clipboardData.files.length !== 1) throw new Error('Synthetic clipboard did not contain the test image.');
      element.dispatchEvent(event);
    }''', base64.b64encode(image_file()['buffer']).decode())
    editor.evaluate('element => element.editor.commands.setTextSelection(element.editor.state.doc.content.size - 1)')
    page.keyboard.insert_text(' Continued typing.')
    page.evaluate('window.releaseFolioImage()')
    picture = editor.locator('img')
    expect(picture).to_have_count(1)
    assert picture.evaluate('image => image.nextElementSibling.textContent') == 'First paragraph.', editor.inner_html()
    expect(editor).to_contain_text('Second paragraph. Continued typing.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')


def test_cancel_image_dialog_while_reading_does_not_insert_later(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser, 'Cancelled image')
    editor.fill('Keep this text.')
    page.evaluate('''() => {
      const original = FileReader.prototype.readAsDataURL;
      FileReader.prototype.readAsDataURL = function(file) {
        window.releaseFolioImage = () => original.call(this, file);
      };
    }''')
    page.get_by_role('tab', name='Insert', exact=True).click()
    page.get_by_role('button', name='Insert image', exact=True).click()
    dialog = page.get_by_role('dialog', name='Insert an image', exact=True)
    dialog.get_by_label('Image file', exact=True).set_input_files(image_file())
    dialog.get_by_role('button', name='Insert image', exact=True).click()
    expect(dialog.get_by_role('button', name='Insert image', exact=True)).to_be_disabled()
    dialog.get_by_role('button', name='Cancel', exact=True).click()
    expect(dialog).to_have_count(0)
    page.evaluate('window.releaseFolioImage()')
    expect(editor).to_have_text('Keep this text.')
    expect(page.locator('#folioSaveStatus')).to_have_text('Saved')
    assert editor.locator('img').count() == 0
    page.reload()
    expect(editor).to_have_text('Keep this text.')


def test_contextual_picture_controls_are_readable_in_both_themes_and_narrow_dialog(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = add_image(browser)
    for theme in ('light', 'dark'):
        page.set_viewport_size({'width': 1280, 'height': 900})
        page.get_by_role('button', name='Appearance', exact=True).click()
        page.locator('#appearanceMode').select_option(theme)
        page.locator('#appearanceDialog').get_by_role('button', name='Close appearance', exact=True).click()
        expect(page.locator('html')).to_have_attribute('data-theme', theme)
        editor.locator('img').click()
        page.get_by_role('tab', name='Picture', exact=True).click()
        path = screenshot_path(browser, f'folio-picture-{theme}.png')
        page.screenshot(path=str(path), full_page=True, animations='disabled')
        print(path)
        page.get_by_role('button', name='Picture size and description', exact=True).click()
        page.set_viewport_size({'width': 390, 'height': 844})
        dialog = page.get_by_role('dialog', name='Picture size and description', exact=True)
        expect(dialog.get_by_label('Picture width', exact=True)).to_be_visible()
        expect(dialog.get_by_label('Picture height', exact=True)).to_be_visible()
        expect(dialog.get_by_role('button', name='Apply picture changes', exact=True)).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert dialog.evaluate('element => { const r=element.getBoundingClientRect(); return r.left>=8 && r.right<=innerWidth-8 && r.top>=8 && r.bottom<=innerHeight-8; }'), 'The picture dialog should stay within the narrow viewport.'
        path = screenshot_path(browser, f'folio-picture-dialog-narrow-{theme}.png')
        page.screenshot(path=str(path), full_page=True, animations='disabled')
        print(path)
        dialog.get_by_role('button', name='Cancel', exact=True).click()
