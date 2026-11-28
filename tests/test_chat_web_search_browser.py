import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect
from test_web_onboarding_browser import browser_page  # noqa: F401


RESULT = {"task": "web_discovery", "answer": "Web search · Crossref · 1 result for “graph algorithms”.",
          "external_papers": [{"title": "A comparison of graph algorithms", "doi": "10.1234/test",
                               "author": "Example", "year": "2025", "journal": "Computing Research",
                               "abstract": "We compared two algorithms using the same dataset."}]}


def show(page, result=RESULT):
    page.evaluate("r => addMessage('assistant', r.answer, resultMeta(r), r)", result)


def test_compact_external_results_and_background_import(browser_page, tmp_path):
    page, state, requests, _ = browser_page
    page.locator('#guideSkip').click()
    show(page)
    row = page.locator('.chat-web-results li')
    expect(row).to_have_count(1)
    expect(row.locator('a')).to_have_attribute('href', 'https://doi.org/10.1234/test')
    expect(row.locator('.web-paper-meta')).to_have_text('Example · Computing Research · 2025')
    assert not row.locator('details').evaluate('el => el.open')
    assert not page.locator('.assistant table, .assistant .answer-warnings, .assistant .result-summary').count()
    assert '/api/paper-import' not in requests
    row.locator('summary').click()
    expect(row.locator('details p')).to_contain_text('two algorithms')
    add = row.get_by_role('button', name='Add to library')
    expect(add).to_be_enabled()
    add.click()
    expect(row.get_by_role('button')).to_have_text('Waiting…')
    assert not page.locator('#discoveryDialog').evaluate('el => el.open')
    state['imports'][0].update(state='indexing', message='Indexing the verified PDF')
    expect(row.get_by_role('button')).to_have_text('Indexing…', timeout=6000)
    state['imports'][0].update(state='imported', message='Added and indexed')
    expect(row.get_by_role('button')).to_have_text('In library', timeout=6000)
    expect(row.get_by_role('button')).to_be_disabled()
    assert requests.count('/api/paper-import') == 1
    page.screenshot(path=str(tmp_path / 'chat-web-search.png'))


def test_external_metadata_cannot_inject_html_and_mobile_layout_fits(browser_page):
    page, _, _, _ = browser_page
    page.locator('#guideSkip').click()
    page.set_viewport_size({'width': 390, 'height': 844})
    show(page, {**RESULT, 'external_papers': [{**RESULT['external_papers'][0],
        'title': '<img src=x onerror=alert(1)>', 'abstract': '<script>alert(1)</script>'},
        {'doi': 'javascript:alert(1)', 'title': 'Unsafe link'}]})
    row = page.locator('.chat-web-results li')
    expect(row).to_have_count(1)
    row.locator('summary').click()
    expect(row.locator('details p')).to_have_text('<script>alert(1)</script>')
    assert not row.locator('img, script').count()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')


def test_same_doi_controls_stay_in_sync_and_failure_is_retryable(browser_page):
    page, state, requests, _ = browser_page
    page.locator('#guideSkip').click()
    show(page)
    show(page)
    buttons = page.locator('.chat-web-results button')
    expect(buttons.first).to_be_enabled()
    buttons.first.click()
    expect(buttons.nth(1)).to_have_text('Waiting…')
    expect(buttons.nth(1)).to_be_disabled()
    state['imports'][0].update(state='manual_upload', message='No verified PDF found')
    expect(buttons.first).to_have_text('Retry import', timeout=6000)
    expect(buttons.nth(1)).to_be_enabled()
    assert 'Not added' in page.locator('.web-import-status').first.inner_text()
    assert requests.count('/api/paper-import') == 1


def test_saved_external_cards_reconnect_to_import_job(browser_page):
    page, state, _, _ = browser_page
    page.locator('#guideSkip').click()
    state['imports'] = [{'job_id': 'existing', 'doi': '10.1234/test', 'state': 'indexing'}]
    show(page)
    expect(page.locator('.chat-web-results button')).to_have_text('Indexing…', timeout=6000)


def test_external_cards_respect_local_access_without_account(browser_page):
    page, state, requests, _ = browser_page
    state.update(authenticated=False, local_imports=True)
    page.reload()
    page.locator('#guideSkip').click()
    show(page)
    add = page.locator('.chat-web-results button')
    expect(add).to_be_enabled()
    add.click()
    expect(add).to_have_text('Waiting…')
    assert requests.count('/api/paper-import') == 1


def test_external_cards_without_import_permission_remain_readable(browser_page):
    page, state, requests, _ = browser_page
    state.update(authenticated=False, local_imports=False)
    page.reload()
    page.locator('#guideSkip').click()
    show(page)
    button = page.locator('.chat-web-results button')
    expect(button).to_have_text('Import unavailable')
    expect(button).to_be_disabled()
    page.locator('.chat-web-results summary').click()
    expect(page.locator('.chat-web-results details p')).to_be_visible()
    assert '/api/paper-import' not in requests


def test_already_indexed_paper_never_reimports_and_missing_abstract_is_honest(browser_page):
    page, _, requests, _ = browser_page
    page.locator('#guideSkip').click()
    show(page, {**RESULT, 'external_papers': [{**RESULT['external_papers'][0], 'fulltext_ready': True, 'abstract': ''}]})
    button = page.locator('.chat-web-results button')
    expect(button).to_have_text('In library')
    expect(button).to_be_disabled()
    page.locator('.chat-web-results summary').click()
    expect(page.locator('.chat-web-results details p')).to_contain_text('No abstract supplied')
    assert '/api/paper-import' not in requests


def test_followup_renders_summary_without_repeating_cards_or_exposing_state(browser_page):
    page, _, requests, _ = browser_page
    page.locator('#guideSkip').click()
    show(page)
    show(page, {**RESULT, 'answer': 'They compared **two algorithms**.\n\n*Based on the abstract.*',
                'selected_external_dois': ['10.1234/test'], 'source_scope': 'abstract',
                'show_external_cards': False})
    answer = page.locator('.assistant').last
    expect(answer).to_contain_text('They compared two algorithms.')
    expect(answer.locator('strong')).to_have_text('two algorithms')
    assert not answer.locator('.chat-web-results, .answer-warnings').count()
    assert 'selected_external_dois' not in answer.inner_text()
    assert '/api/paper-import' not in requests


@pytest.mark.parametrize('theme,width', [('dark', 1280), ('light', 1280), ('dark', 390)])
def test_loading_status_is_on_wallpaper_with_no_card(browser_page, tmp_path, theme, width):
    page, _, _, _ = browser_page
    page.locator('#guideSkip').click()
    page.set_viewport_size({'width': width, 'height': 850})
    page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
    page.evaluate("""() => { currentSessionDir = 'minimal-chat';
        addJobMessage({job_id: 'minimal-progress', session_dir: 'minimal-chat', state: 'running',
        message: 'Search the web for papers about solar cells',
        stage: 'Searching the web for papers', elapsed_seconds: 10, can_cancel: true}); }""")
    job = page.locator('[data-job-id="minimal-progress"]')
    expect(job.locator('.working-progress')).to_be_visible()
    expect(job.get_by_role('button', name='Stop this request')).to_be_visible()
    expect(job.locator('.role')).to_be_hidden()
    expect(job.locator('.working-dot')).to_be_hidden()
    style = job.locator('.bubble').evaluate('el => { const s = getComputedStyle(el); return [s.backgroundColor, s.borderTopWidth, s.boxShadow]; }')
    assert style == ['rgba(0, 0, 0, 0)', '0px', 'none']
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.screenshot(path=str(tmp_path / 'minimal-progress.png'))
    page.evaluate("""() => updateJobMessage(document.querySelector('[data-job-id="minimal-progress"]'),
        {job_id: 'minimal-progress', session_dir: 'minimal-chat', state: 'failed',
        message: 'Search the web for papers about solar cells', error: 'Search service unavailable'})""")
    expect(job.locator('.job-edit')).to_be_visible()
    expect(job.locator('.working-progress')).to_be_hidden()
    assert job.locator('.bubble').evaluate('el => getComputedStyle(el).borderTopWidth') != '0px'
