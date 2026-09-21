import pytest

from test_writing import workspace_server as workspace_server


def test_corpus_drag_navigation_and_double_delete(workspace_server):
    pw = pytest.importorskip('playwright.sync_api')
    base, tmp = workspace_server
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base + '/library')
        page.locator('#query').fill('no such synthetic paper')
        pw.expect(page.locator('#libraryCount')).to_have_text('0 papers')
        page.locator('#query').fill('study')
        pw.expect(page.locator('#librarySuggestions option')).to_have_count(1)
        page.locator('#query').fill('')
        pw.expect(page.locator('#libraryCount')).to_have_text('1 paper')
        page.get_by_role('button', name='+ Collection').click()
        page.locator('#newName').fill('Working papers')
        page.get_by_role('button', name='Create', exact=True).click()
        target = page.get_by_role('button', name='Working papers', exact=True)
        target.wait_for()
        page.locator('article[data-paper-id="P1"]').drag_to(target)
        pw.expect(page.locator('#notice')).to_contain_text('Paper linked')
        target.click()
        page.get_by_role('button', name='Browse corpus', exact=True).click()
        pw.expect(page.get_by_role('button', name='In collection', exact=True)).to_be_disabled()
        page.get_by_role('button', name='Save to shelf', exact=True).click()
        pw.expect(page.locator('#notice')).to_contain_text('Saved to your bookshelf')
        page.locator('#globalNavigation a[href="/bookshelf"]').click()
        pw.expect(page.locator('.shelf-level')).to_have_count(2)
        pw.expect(page.locator('.book')).to_have_count(1)
        assert page.locator('.book').bounding_box()['height'] < 260
        assert 'serif' not in page.locator('h1').evaluate('el => getComputedStyle(el).fontFamily').replace('sans-serif', '')
        page.screenshot(path=str(tmp / 'bookshelf-new.png'), full_page=True)
        page.locator('#globalNavigation a[href="/write"]').click()
        page.get_by_role('button', name='+ New manuscript').click()
        page.locator('#newName').fill('Delete only this test')
        page.get_by_role('button', name='Create', exact=True).click()
        page.locator('#source').wait_for()
        page.locator('#globalNavigation a[href="/write"]').click()
        pw.expect(page.locator('.manuscript-row')).to_have_count(1)
        pw.expect(page.locator('.manuscript-row')).to_contain_text('Edited')
        page.screenshot(path=str(tmp / 'writing-desk-new.png'), full_page=True)
        dialogs = []
        def cancel_second(dialog):
            dialogs.append(dialog.message)
            dialog.accept() if len(dialogs) == 1 else dialog.dismiss()
        page.on('dialog', cancel_second)
        page.get_by_role('button', name='Delete', exact=True).click()
        assert len(dialogs) == 2
        page.remove_listener('dialog', cancel_second)
        pw.expect(page.locator('.manuscript-row')).to_have_count(1)
        page.on('dialog', lambda dialog: dialog.accept())
        page.get_by_role('button', name='Delete', exact=True).click()
        pw.expect(page.locator('.manuscript-row')).to_have_count(0)
        page.locator('#globalSettings').click()
        page.locator('#globalStartPage').select_option('/library')
        page.get_by_role('button', name='Done', exact=True).click()
        page.goto(base + '/workspace')
        page.wait_for_url('**/library')
        pw.expect(page.get_by_role('button', name='Working papers', exact=True)).to_be_visible()
        assert page.locator('#appearanceButton').count() == 1
        page.locator('#appearanceButton').click()
        pw.expect(page.locator('#appearanceDialog')).to_be_visible()
        page.keyboard.press('Escape')
        page.goto(base + '/chat')
        page.locator('#guideSkip').click()
        assert page.locator('#globalNavigation nav a').count() == 4
        assert page.locator('a[href="/workspace"]').count() == 0
        assert page.locator('#appearanceButton').count() == 1
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.locator('#globalNavigation').bounding_box()['width'] <= 390
        pw.expect(page.locator('#globalSettings')).to_be_visible()
        app = page.locator('body > .app').bounding_box()
        assert app['y'] + app['height'] <= 845
        assert not errors
        browser.close()
