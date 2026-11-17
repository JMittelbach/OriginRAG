import pytest

from test_web_onboarding_browser import browser_page as browser_page
from test_writing import workspace_server as workspace_server


def check_close(page, panel):
    close = panel.locator('.dialog-close')
    assert close.count() == 1
    assert close.inner_text() == '×'
    assert close.get_attribute('aria-label').startswith('Close ')
    box, button = panel.bounding_box(), close.bounding_box()
    assert box and button
    assert 0 <= button['x'] - box['x'] <= 24
    assert 0 <= button['y'] - box['y'] <= 24
    assert 24 <= button['width'] <= 36 and 24 <= button['height'] <= 36
    assert not panel.evaluate('el => el.scrollWidth > el.clientWidth + 1')
    background = close.evaluate('el => getComputedStyle(el).backgroundColor')
    values = [float(value.strip()) for value in background.removeprefix('rgba(').removesuffix(')').split(',')]
    assert len(values) == 4 and values[0] > values[1] and 0 < values[3] < .5
    close.focus()
    assert close.evaluate('el => document.activeElement === el')
    return close


@pytest.mark.parametrize('theme,width', [('dark', 1280), ('light', 1280), ('dark', 390), ('light', 390)])
def test_static_popups_have_small_red_top_left_close_buttons(browser_page, theme, width):
    page, _, _, _ = browser_page
    page.set_viewport_size({'width': width, 'height': 900})
    check_close(page, page.locator('#welcomeGuide')).click()
    page.locator('#appearanceButton').click()
    page.locator('#appearanceMode').select_option(theme)
    check_close(page, page.locator('#appearanceDialog')).click()
    for trigger, panel in [('#globalSettings', '#globalSettingsDialog'),
                           ('#discoveryButton', '#discoveryDialog'),
                           ('#aboutButton', '.about-panel'),
                           ('#queueSummary', '.queue-panel')]:
        page.locator(trigger).click()
        container = page.locator(panel)
        check_close(page, container).click()
        assert not container.is_visible()
    page.locator('#discoveryButton').click()
    page.keyboard.press('Escape')
    assert not page.locator('#discoveryDialog').is_visible()
    assert page.locator('#discoveryButton').evaluate('el => document.activeElement === el')


def test_dynamic_quill_and_library_dialogs_share_the_same_close_control(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base + '/library')
        for module, run, identifier in [
            ('writing-templates', 'm.openTemplateGallery([], async () => {})', '#templateDialog'),
            ('writing-collaboration', "m.openProjectCitations({files:{}}, () => {})", '#projectCitations'),
            ('writing-backups', "m.setupProjectBackup({getProject:()=>({files:{}}), save:async()=>{}, notify:()=>{}}).open()", '#backupDialog'),
            ('library-import', "m.openLibraryImport({notice:()=>{},changed:()=>{}})", '#libraryImportDialog'),
        ]:
            page.evaluate(f"async () => {{ const m = await import('/assets/workspace/{module}.mjs'); await {run}; }}")
            dialog = page.locator(identifier)
            check_close(page, dialog).click()
            playwright.expect(dialog).not_to_be_visible()
        assert not errors
        browser.close()
