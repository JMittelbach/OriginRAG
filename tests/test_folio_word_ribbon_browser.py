"""The detailed ribbon keeps real commands, accessible names and bounded layout."""

import os
from pathlib import Path

import pytest

from test_ecosystem_design_browser import NAVIGATION_CONTRAST
from test_folio_browser import create_document, folio_browser as folio_browser
from test_folio_http import folio_server as folio_server


def capture(browser, name):
    directory = Path(os.environ.get('ORIGINRAG_UI_REVIEW_DIR', str(browser.tmp)))
    browser.page.screenshot(path=str(directory / name), full_page=True, animations='disabled')


@pytest.mark.parametrize('theme', ['light', 'dark', 'black'])
def test_ribbon_icons_names_and_styles_survive_rerender(folio_browser, theme):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser, 'A clearly organised ribbon')
    page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
    editor.fill('A real formatting action.')
    editor.press('ControlOrMeta+a')
    bold = page.get_by_role('button', name='Bold', exact=True)
    expect(bold.locator('svg')).to_have_count(1)
    expect(bold.locator('.folio-tool-label')).to_have_count(1)
    bold.click()
    expect(editor.locator('strong')).to_have_text('A real formatting action.')
    expect(bold).to_have_attribute('aria-pressed', 'true')
    for tab in ['File', 'Home', 'Insert', 'Draw', 'Design', 'Layout', 'References', 'Review', 'View']:
        page.get_by_role('tab', name=tab, exact=True).click()
        ribbon = page.locator('#folioRibbon')
        expect(ribbon).to_have_class('folio-ribbon folio-ribbon-detailed')
        expect(ribbon).to_have_css('background-color', 'rgb(250, 250, 250)' if theme == 'light' else 'rgb(41, 41, 41)')
        for control in ribbon.locator('button.folio-tool').all():
            expect(control.locator(':scope > svg.folio-icon')).to_have_count(1)
            expect(control.locator(':scope > .folio-tool-label')).to_have_count(1)
            expect(control.locator('svg')).to_have_attribute('aria-hidden', 'true')
            assert control.get_attribute('aria-label')
            if control.is_enabled():
                assert control.evaluate(NAVIGATION_CONTRAST) >= 4.5, (theme, tab, control.get_attribute('aria-label'))
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        if tab in {'Draw', 'Design', 'References'}:
            capture(browser, f'folio-ribbon-{tab.lower()}-{theme}.png')
    page.get_by_role('tab', name='Home', exact=True).click()
    for style in page.locator('.folio-style').all():
        expect(style.locator('.folio-style-sample')).to_have_text('AaBbCc')
        expect(style.locator('.folio-style-label')).to_have_count(1)
        assert style.locator('.folio-style-label').evaluate(NAVIGATION_CONTRAST) >= 4.5
    capture(browser, f'folio-ribbon-home-{theme}.png')
    page.get_by_role('tab', name='Insert', exact=True).click()
    capture(browser, f'folio-ribbon-insert-{theme}.png')


def test_ribbon_decorator_is_idempotent_and_preserves_handlers(folio_browser):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    editor = create_document(browser)
    editor.fill('The command is still attached.')
    editor.press('ControlOrMeta+a')
    assert page.evaluate('''async () => {
        const {decorateRibbon} = await import('/assets/folio/icons.mjs');
        const ribbon = document.getElementById('folioRibbon');
        const button = ribbon.querySelector('[aria-label="Bold"]');
        const command = button.dataset.command;
        decorateRibbon(ribbon); decorateRibbon(ribbon);
        return button === ribbon.querySelector('[aria-label="Bold"]') &&
            button.dataset.command === command && button.querySelectorAll('svg').length === 1 &&
            button.querySelectorAll('.folio-tool-label').length === 1;
    }''')
    page.get_by_role('button', name='Bold', exact=True).click()
    expect(editor.locator('strong')).to_have_text('The command is still attached.')
    font = page.get_by_label('Font family', exact=True)
    font.select_option('Georgia')
    expect(editor.locator('[style*="font-family: Georgia"]')).to_have_count(1)
    page.get_by_role('button', name='Heading 1', exact=True).click()
    expect(editor.locator('h1')).to_have_text('The command is still attached.')


@pytest.mark.parametrize('width', [390, 768, 1500])
def test_ribbon_overflow_stays_inside_editor_and_tabs_are_keyboard_accessible(folio_browser, width):
    browser = folio_browser
    page, expect = browser.page, browser.expect
    create_document(browser)
    page.set_viewport_size({'width': width, 'height': 900})
    for tab in ['Home', 'Insert', 'Draw', 'Design', 'Layout', 'References', 'Review', 'View']:
        page.get_by_role('tab', name=tab, exact=True).click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (
            tab, page.evaluate('''() => [...document.querySelectorAll('body *')].filter(node => {
                const rect = node.getBoundingClientRect();
                return rect.width > innerWidth && getComputedStyle(node).position !== 'absolute';
            }).slice(0, 12).map(node => [node.id, node.className, node.getBoundingClientRect().width])'''))
        box = page.locator('#folioRibbon').bounding_box()
        assert box['width'] <= width + 1
        assert box['height'] <= 150
        assert page.locator('#folioRibbon').evaluate('node => getComputedStyle(node).overflowX') == 'auto'
        assert page.locator('#folioCanvas').bounding_box()['height'] > 360
    page.get_by_role('tab', name='Home', exact=True).focus()
    page.keyboard.press('ArrowRight')
    expect(page.get_by_role('tab', name='Insert', exact=True)).to_be_focused()
    expect(page.get_by_role('tab', name='Insert', exact=True)).to_have_attribute('aria-selected', 'true')
    capture(browser, f'folio-ribbon-{width}.png')
