from urllib.parse import parse_qs, urlparse

import pytest

from test_writing import workspace_server as workspace_server


@pytest.fixture
def workspace_page(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1050})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        yield base, page, playwright.expect
        browser.close()
        assert not errors


def import_routes(page, state):
    def respond(route):
        path = urlparse(route.request.url).path
        if path == '/api/library-access':
            route.fulfill(json={'library_access': {'enabled': True, 'csrf_token': 'test-token'}})
        elif path == '/api/paper-imports':
            state['polls'] = state.get('polls', 0) + 1
            if state.pop('fail_poll', False):
                route.fulfill(status=503, content_type='text/html', body='<h1>Temporarily unavailable</h1>')
            else:
                route.fulfill(json={'jobs': state.get('jobs', [])})
        elif path == '/api/library-status':
            route.fulfill(json=state.get('library', {'state': 'idle'}))
        elif path == '/api/library-sync':
            state['syncs'] = state.get('syncs', 0) + 1
            route.fulfill(json={'state': 'queued'})
        elif path == '/api/discover-papers':
            route.fulfill(json={'items': [{'title': 'A test publication', 'doi': '10.1000/test'}]})
        elif path == '/api/paper-import':
            state['starts'] = state.get('starts', 0) + 1
            job = {'job_id': f'job-{state["starts"]}', 'doi': '10.1000/test',
                   'state': 'queued', 'message': 'Waiting to import'}
            state.setdefault('jobs', []).append(job)
            route.fulfill(json=job)
    for endpoint in ['library-access', 'paper-imports', 'library-status', 'library-sync',
                     'discover-papers', 'paper-import']:
        page.route('**/api/' + endpoint + '*', respond)


def open_import(base, page, expect):
    page.goto(base + '/library')
    page.get_by_role('button', name='Find or import papers', exact=True).first.click()
    expect(page.locator('#libraryImportFiles')).to_be_enabled()


def test_library_cancels_obsolete_search_and_explains_empty_results(workspace_page):
    base, page, expect = workspace_page
    failed = []
    page.on('requestfailed', lambda request: failed.append(request.url))
    pending = []

    def search(route):
        query = parse_qs(urlparse(route.request.url).query).get('query', [''])[0]
        if query == 'slow':
            pending.append(route)
            return
        items = [] if query == 'missing' else [{'paper_id': 'P1', 'title': 'Current result'}]
        route.fulfill(json={'items': items, 'total': len(items)})

    page.route('**/api/library?*', search)
    page.goto(base + '/library')
    expect(page.locator('#libraryCount')).to_have_text('1 paper')
    page.get_by_label('Search library').fill('slow')
    expect(page.locator('#libraryCount')).to_contain_text('Searching')
    page.get_by_label('Search library').fill('missing')
    expect(page.get_by_role('heading', name='No matching papers')).to_be_visible()
    assert pending and any('query=slow' in url for url in failed)
    assert 'query=missing' in page.url
    page.get_by_role('button', name='Clear search').click()
    expect(page.locator('#papers')).to_contain_text('Current result')
    expect(page.locator('#libraryPage')).to_have_text('1–1 of 1')
    assert 'query=' not in page.url


def test_library_search_failure_has_working_retry(workspace_page):
    base, page, expect = workspace_page
    page.route('**/api/library?*', lambda route: route.fulfill(status=502, body='Gateway error'))
    page.goto(base + '/library')
    expect(page.locator('#libraryFeedback')).to_contain_text('Please try again')
    assert 'Unexpected token' not in page.locator('#libraryFeedback').inner_text()
    page.unroute('**/api/library?*')
    page.get_by_role('button', name='Retry search').click()
    expect(page.locator('#papers')).to_contain_text('A study & its results')
    expect(page.locator('#libraryFeedback')).to_be_hidden()
    assert not page.locator('#papers').evaluate('element => element.inert')


def test_navigation_remains_usable_while_account_request_waits(workspace_page):
    base, page, expect = workspace_page
    waiting = []
    page.route('**/api/app-profile', lambda route: waiting.append(route))
    page.goto(base + '/library')
    page.locator('#globalSettings').click()
    expect(page.locator('#globalSettingsDialog')).to_be_visible()
    page.locator('#globalStartPage').select_option('/library')
    page.get_by_role('button', name='Done', exact=True).click()
    page.locator('#globalAccount').click()
    expect(page.locator('#globalAccountDetails')).to_have_text('Loading account…')
    assert len(waiting) == 1
    waiting[0].fulfill(status=503, body='Temporarily unavailable')
    expect(page.locator('#globalAccountRetry')).to_be_visible()
    expect(page.locator('#globalSignOut')).to_be_hidden()
    page.unroute('**/api/app-profile')
    page.locator('#globalAccountRetry').click()
    expect(page.locator('#globalAccountDetails')).to_contain_text('no sign-in required')
    expect(page.locator('#globalAccountRetry')).to_be_hidden()
    assert page.locator('#globalStartPage').input_value() == '/library'


@pytest.mark.parametrize('unavailable', ['timeout', 'malformed'])
def test_account_request_timeout_and_invalid_profile_are_recoverable(workspace_page, unavailable):
    base, page, expect = workspace_page
    page.clock.install()
    waiting = []

    def respond(route):
        waiting.append(route)
        if unavailable == 'malformed':
            route.fulfill(json={'features': {}})

    page.route('**/api/app-profile', respond)
    page.goto(base + '/library')
    page.locator('#globalAccount').click()
    assert len(waiting) == 1
    page.clock.fast_forward(13000)
    expect(page.locator('#globalAccountRetry')).to_be_enabled()
    expect(page.locator('#globalAccountRetry')).to_be_visible()
    expect(page.locator('#globalAccountError')).not_to_be_empty()
    page.unroute('**/api/app-profile')
    page.evaluate("window.dispatchEvent(new Event('online'))")
    expect(page.locator('#globalAccountDetails')).to_contain_text('no sign-in required')
    expect(page.locator('#globalAccountError')).to_be_empty()


def test_sign_out_preserves_unsaved_draft_and_does_not_duplicate_requests(workspace_page):
    base, page, expect = workspace_page
    page.route('**/api/app-profile', lambda route: route.fulfill(json={
        'home': '/chat', 'features': {'rag': True, 'write': True},
        'authenticated': True, 'user': {'username': 'test-user'},
    }))
    waiting = []

    def sign_out(route):
        waiting.append(route)
        route.fulfill(status=503, json={'error': 'Sign out not confirmed. Please retry.'})

    page.route('**/api/logout', sign_out)
    page.goto(base + '/library')
    page.locator('#globalAccount').click()
    page.evaluate("() => { window.originragSaveDraft = async () => { throw new Error('Draft not saved'); }; }")
    page.locator('#globalSignOut').click()
    expect(page.locator('#globalAccountError')).to_have_text('Draft not saved')
    assert not waiting
    page.evaluate('''() => { window.originragSaveDraft = () => new Promise(resolve => {
      window.finishSaving = resolve;
    }); }''')
    page.locator('#globalSignOut').click()
    expect(page.locator('#globalSignOut')).to_be_disabled()
    page.locator('#globalSignOut').evaluate('button => button.click()')
    assert not waiting
    with page.expect_response('**/api/logout'):
        page.evaluate('window.finishSaving()')
    expect(page.locator('#globalSignOut')).to_be_enabled()
    expect(page.locator('#globalAccountError')).to_contain_text('not confirmed')
    assert len(waiting) == 1 and page.url == base + '/library'


def test_workspace_initial_load_can_be_retried_without_reloading_page(workspace_page):
    base, page, expect = workspace_page
    profile_requests = []
    page.on('request', lambda request: profile_requests.append(request.url) if request.url.endswith('/api/app-profile') else None)
    page.route('**/api/writing', lambda route: route.fulfill(status=503, json={'error': 'Temporarily unavailable'}))
    page.goto(base + '/library')
    expect(page.get_by_role('heading', name='This page could not be loaded')).to_be_visible()
    page.unroute('**/api/writing')
    page.get_by_role('button', name='Retry loading').click()
    expect(page.locator('#libraryCount')).to_have_text('1 paper')
    assert len(profile_requests) == 1


def test_import_validates_entire_batch_before_uploading(workspace_page):
    base, page, expect = workspace_page
    state = {}
    import_routes(page, state)
    uploads = []
    page.route('**/api/library-upload', lambda route: (uploads.append(route.request), route.fulfill(json={'upload_id': 'one'})))
    open_import(base, page, expect)
    page.locator('#libraryImportFiles').set_input_files([
        {'name': 'valid.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF-1.4'},
        {'name': 'invalid.txt', 'mimeType': 'text/plain', 'buffer': b'text'},
    ])
    expect(page.locator('#libraryImportStatus')).to_contain_text('No files were uploaded')
    assert not uploads and not state.get('syncs')


def test_partial_upload_failure_still_processes_accepted_files(workspace_page):
    base, page, expect = workspace_page
    state = {}
    import_routes(page, state)
    uploads = []

    def upload(route):
        uploads.append(route.request.headers)
        if len(uploads) == 1:
            route.fulfill(json={'upload_id': 'accepted-one'})
        else:
            route.fulfill(status=503, json={'error': 'Storage temporarily unavailable.'})

    page.route('**/api/library-upload', upload)
    open_import(base, page, expect)
    page.locator('#libraryImportFiles').set_input_files([
        {'name': name, 'mimeType': 'application/pdf', 'buffer': b'%PDF-1.4'}
        for name in ['one.pdf', 'two.pdf']
    ])
    expect(page.locator('#libraryImportStatus')).to_contain_text('Earlier uploads are being processed')
    assert len(uploads) == 2 and state['syncs'] == 1
    assert all(item['x-originrag-local-token'] == 'test-token' for item in uploads)


def test_import_reconnects_retries_failed_job_and_restores_progress(workspace_page):
    base, page, expect = workspace_page
    state = {}
    import_routes(page, state)
    open_import(base, page, expect)
    page.get_by_label('Find papers', exact=True).fill('test')
    page.get_by_role('button', name='Search papers').click()
    page.get_by_role('button', name='Add to library', exact=True).click()
    expect(page.locator('.library-import-jobs')).to_contain_text('Waiting to import')
    state['fail_poll'] = True
    page.get_by_role('button', name='Refresh progress').click()
    expect(page.locator('#libraryImportStatus')).to_contain_text('Reconnecting')
    state['jobs'][0].update(state='failed', message='Download temporarily unavailable')
    expect(page.get_by_role('button', name='Retry import')).to_be_enabled(timeout=10000)
    page.get_by_role('button', name='Retry import').click()
    assert state['starts'] == 2
    page.locator('#libraryImportDialog').get_by_role('button', name='Close', exact=True).click()
    page.get_by_role('button', name='Find or import papers', exact=True).first.click()
    expect(page.locator('.library-import-jobs')).to_contain_text('Waiting to import')
    state['jobs'][-1].update(state='imported', message='Added to library and indexed')
    page.get_by_role('button', name='Refresh progress').click()
    expect(page.locator('.library-import-jobs')).to_contain_text('Added to library and indexed')
    assert state['starts'] == 2


def test_import_finishes_upload_and_schedules_it_after_dialog_closes(workspace_page):
    base, page, expect = workspace_page
    state = {}
    import_routes(page, state)
    pending = []
    page.route('**/api/library-upload', lambda route: pending.append(route))
    open_import(base, page, expect)
    page.locator('#libraryImportFiles').set_input_files({'name': 'one.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF-1.4'})
    expect(page.locator('#libraryImportStatus')).to_contain_text('Uploading 1 of 1')
    page.locator('#libraryImportDialog').get_by_role('button', name='Close', exact=True).click()
    assert pending
    with page.expect_response('**/api/library-sync'):
        pending[0].fulfill(json={'upload_id': 'accepted-one'})
    assert state['syncs'] == 1


@pytest.mark.parametrize('lost_confirmation', [False, True])
@pytest.mark.parametrize('edit_during_outage', [False, True])
def test_quill_retry_keeps_draft_and_handles_lost_save_confirmation(workspace_page, lost_confirmation, edit_during_outage):
    base, page, expect = workspace_page
    page.goto(base + '/write')
    page.get_by_role('button', name='+ New manuscript').click()
    page.locator('#newName').fill('Recovery test')
    page.get_by_role('button', name='Create', exact=True).click()
    expect(page.locator('#source .cm-content')).to_be_visible()
    attempts = []

    def save(route):
        if route.request.method != 'POST' or route.request.post_data_json.get('action') != 'save_project':
            route.continue_()
            return
        attempts.append(route.request.post_data_json)
        if len(attempts) == 1:
            if lost_confirmation:
                response = route.fetch()
                assert response.ok
                route.abort('connectionreset')
            else:
                route.fulfill(status=503, json={'error': 'Storage temporarily unavailable.'})
        else:
            route.continue_()

    page.route('**/api/writing', save)
    first = '\\documentclass{article}\n\\begin{document}\nFirst draft.\n\\end{document}'
    page.locator('#source .cm-content').fill(first)
    expect(page.locator('#retrySave')).to_be_visible()
    expect(page.locator('#source .cm-content')).to_contain_text('First draft.')
    updated = first.replace('First draft.', 'Draft extended while offline.') if edit_during_outage else first
    if edit_during_outage:
        page.locator('#source .cm-content').fill(updated)
    page.get_by_role('button', name='Retry saving', exact=True).click()
    expect(page.locator('#savedState')).to_have_text('All changes saved')
    expect(page.locator('#collaborationConflict')).to_be_hidden()
    key = parse_qs(urlparse(page.url).query)['id'][0]
    saved = page.request.get(base + '/api/writing?id=' + key).json()
    assert saved['files']['main.tex'] == updated
    assert len(attempts) == (1 if lost_confirmation and not edit_during_outage else 2)


def test_chat_keeps_rejected_question_and_recovers_queue_status(workspace_page):
    base, page, expect = workspace_page
    state = {'offline': True}

    def jobs(route):
        if state['offline']:
            route.fulfill(status=503, body='Temporarily unavailable')
        else:
            route.continue_()

    page.route('**/api/jobs', jobs)
    page.route('**/api/chat', lambda route: route.fulfill(status=503, json={'error': 'Please retry shortly.'}))
    page.goto(base + '/chat')
    page.locator('#guideSkip').click()
    expect(page.locator('#queueSummary')).to_have_text('Reconnecting…')
    page.locator('#message').fill('Which methods are described in these papers?')
    page.locator('#message').press('Enter')
    expect(page.locator('#message')).to_have_value('Which methods are described in these papers?')
    expect(page.locator('#chat')).to_contain_text('Please retry shortly.')
    state['offline'] = False
    page.evaluate('window.dispatchEvent(new Event("online"))')
    expect(page.locator('#queueSummary')).to_have_text('Queue idle')
    expect(page.locator('#connectionIndicator')).to_have_attribute('aria-label', 'Online')


def test_request_timeout_and_login_errors_do_not_repeat_mutations(workspace_page):
    base, page, expect = workspace_page
    page.goto(base + '/library')
    expect(page.locator('#libraryCount')).to_have_text('1 paper')
    result = page.evaluate('''async () => {
      const {requestJSON} = await import('/assets/workspace/request.mjs');
      const original = window.fetch;
      let calls = 0;
      try {
        window.fetch = (path, {signal}) => { calls++; return new Promise((resolve, reject) => {
          signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
        }); };
        let timed;
        try { await requestJSON('/synthetic', {payload: {action: 'create'}, timeout: 10}); }
        catch (error) { timed = {message: error.message, retryable: error.retryable}; }
        window.fetch = async () => new Response('<html>Sign in</html>', {status: 401});
        let login;
        try { await requestJSON('/synthetic'); } catch (error) { login = {status: error.status, message: error.message}; }
        return {calls, timed, login};
      } finally { window.fetch = original; }
    }''')
    assert result['calls'] == 1
    assert result['timed']['retryable']
    assert 'not confirmed' in result['timed']['message']
    assert result['login']['status'] == 401
    assert 'Sign in again' in result['login']['message']
