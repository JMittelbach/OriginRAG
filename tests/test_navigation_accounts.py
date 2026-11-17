from html.parser import HTMLParser

import pytest

from originrag.app_profiles import app_profile
from originrag.web_navigation import navigation_for_profile, with_navigation
from test_app_profiles import profile_server as profile_server, request
from test_writing import workspace_server as workspace_server


class NavigationControls(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.controls = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'button' and attrs.get('id') in {'globalAccount', 'globalSettings', 'appearanceButton'}:
            self.controls[attrs['id']] = attrs


@pytest.mark.parametrize('profile', ['full', 'rag', 'write'])
@pytest.mark.parametrize('accounts_enabled', [False, True])
def test_account_visibility_is_rendered_from_auth_mode_not_install_profile(profile, accounts_enabled):
    html = with_navigation('<html><head></head><body></body></html>')
    rendered = navigation_for_profile(html, app_profile(profile), accounts_enabled=accounts_enabled)
    controls = NavigationControls(rendered).controls
    assert ('hidden' not in controls['globalAccount']) is accounts_enabled
    assert 'hidden' not in controls['globalSettings']
    assert 'hidden' not in controls['appearanceButton']
    header_id = 'globalNavigation' if profile == 'full' else 'applicationHeader'
    assert f'id="{header_id}"' in rendered


def test_local_http_pages_hide_account_before_javascript_runs(workspace_server):
    base, _ = workspace_server
    port = int(base.rsplit(':', 1)[1])
    for path in ('/chat', '/bookshelf', '/library', '/write', '/folio', '/reader?paper_id=P1'):
        status, _, html = request(port, 'GET', path)
        assert status == 200
        controls = NavigationControls(html.decode()).controls
        assert 'hidden' in controls['globalAccount'], path


def test_authenticated_http_pages_keep_account_even_on_loopback(profile_server):
    profile, port, headers, _, _, _ = profile_server
    paths = ['/bookshelf', '/library', '/reader?paper_id=P1']
    if profile != 'write':
        paths.append('/chat')
    if profile != 'rag':
        paths.append('/write')
    if profile == 'full':
        paths.append('/folio')
    for path in paths:
        status, _, html = request(port, 'GET', path, headers=headers)
        assert status == 200
        controls = NavigationControls(html.decode()).controls
        assert 'hidden' not in controls['globalAccount'], path


def test_local_browser_keeps_account_hidden_during_profile_failure_and_recovery(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        errors, waiting = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/app-profile', lambda route: waiting.append(route))
        page.goto(base + '/library')
        playwright.expect(page.locator('#globalAccount')).to_be_hidden()
        page.locator('#globalSettings').click()
        playwright.expect(page.locator('#globalSettingsDialog')).to_be_visible()
        assert len(waiting) == 1
        waiting[0].fulfill(status=503, body='Temporarily unavailable')
        playwright.expect(page.locator('#globalAccountError')).not_to_be_empty()
        playwright.expect(page.locator('#globalAccount')).to_be_hidden()
        page.unroute('**/api/app-profile')
        page.evaluate("window.dispatchEvent(new Event('online'))")
        playwright.expect(page.locator('#globalAccountDetails')).to_contain_text('no sign-in required')
        playwright.expect(page.locator('#globalAccount')).to_be_hidden()
        playwright.expect(page.locator('#globalSignOut')).to_be_hidden()
        page.keyboard.press('Escape')
        for path in ('/chat', '/bookshelf', '/write', '/folio'):
            page.goto(base + path)
            playwright.expect(page.locator('#globalAccountDetails')).to_contain_text('no sign-in required')
            playwright.expect(page.locator('#globalAccount')).to_be_hidden()
            playwright.expect(page.locator('#globalAccountDialog')).not_to_be_visible()
        assert not errors
        browser.close()
