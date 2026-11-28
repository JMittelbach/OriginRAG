import os
from pathlib import Path
import shutil
import tempfile

import pytest

from test_writing import workspace_server as workspace_server


PAPERS = [
    {'paper_id': f'DESIGN{index}', 'title': title, 'author': author, 'year': str(year)}
    for index, (title, author, year) in enumerate([
        ('A practical guide to single-cell analysis', 'Rivera', 2026),
        ('Shared scientific origins and the interpretation of independent evidence in literature synthesis', 'Chen', 2025),
        ('Spatial methods for studying complex tissues', 'Okafor', 2024),
        ('A systematic comparison of statistical approaches', 'Ahmed', 2023),
        ('Transparent reporting in reproducible research', 'Müller', 2022),
        ('Exploring variation across biological systems', 'Kim', 2026),
    ])
]


@pytest.fixture
def design_page(workspace_server, monkeypatch):
    from test_web_chat import web_chat

    pw = pytest.importorskip('playwright.sync_api')
    base, temporary = workspace_server
    monkeypatch.setattr(web_chat, 'library_catalog', lambda: PAPERS)
    shutil.copytree(Path(__file__).resolve().parents[1] / 'assets/web', temporary / 'assets/web')
    shutil.copytree(Path(__file__).resolve().parents[1] / 'assets/vendor/markdown-it',
                    temporary / 'assets/vendor/markdown-it')
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base + '/library')
        pw.expect(page.locator('#libraryCount')).to_have_text('6 papers')
        ids = page.evaluate('''async papers => {
            async function post(path, payload) {
                const response = await fetch(path, {method: 'POST',
                    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
                if (!response.ok) throw new Error(await response.text());
                return response.json();
            }
            for (const paper of papers) await post('/api/shelf', {action: 'add', paper_id: paper.paper_id});
            const ids = [];
            for (const name of ['Methods and reproducibility', 'A working manuscript with a longer descriptive title', 'Research notes']) {
                ids.push((await post('/api/writing', {action: 'create_project', name})).id);
            }
            return ids;
        }''', PAPERS)
        yield base, page, pw.expect, ids
        browser.close()
        assert not errors


NAVIGATION_CONTRAST = '''element => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
    const context = canvas.getContext('2d', {willReadFrequently: true});
    function rgba(color) {
        context.clearRect(0, 0, 1, 1); context.fillStyle = color; context.fillRect(0, 0, 1, 1);
        return Array.from(context.getImageData(0, 0, 1, 1).data).map(value => value / 255);
    }
    const over = (front, back) => front.slice(0, 3).map((value, i) => value * front[3] + back[i] * (1 - front[3]));
    let background = [1, 1, 1];
    const ancestors = []; for (let item = element; item; item = item.parentElement) ancestors.unshift(item);
    for (const item of ancestors) background = over(rgba(getComputedStyle(item).backgroundColor), background);
    const foreground = over(rgba(getComputedStyle(element).color), background);
    function luminance(rgb) {
        const linear = rgb.map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
        return linear[0] * .2126 + linear[1] * .7152 + linear[2] * .0722;
    }
    const values = [luminance(background), luminance(foreground)].sort((a, b) => a - b);
    return (values[1] + .05) / (values[0] + .05);
}'''


def capture_design(page, name, theme, viewport):
    if capture := os.getenv('ORIGINRAG_DESIGN_CAPTURE'):
        root = Path(os.environ.get('ORIGINRAG_DESIGN_OUTPUT', tempfile.gettempdir()))
        directory = root / ('originrag-design-' + capture)
        directory.mkdir(parents=True, exist_ok=True)
        size = 'mobile' if viewport['width'] < 600 else 'desktop'
        page.screenshot(path=str(directory / f'{name}-{theme}-{size}.png'), full_page=True)


@pytest.mark.parametrize('theme', ['dark', 'light', 'black'])
@pytest.mark.parametrize('viewport', [
    {'width': 1440, 'height': 1000},
    {'width': 390, 'height': 844},
], ids=['desktop', 'mobile'])
def test_ecosystem_navigation_theme_focus_and_layout(design_page, theme, viewport):
    base, page, expect, projects = design_page
    page.evaluate('mode => window.originragAppearance.set({mode, background: "city-cat"})', theme)
    page.set_viewport_size(viewport)
    navigation_fonts = None
    for name, route, ready in [
        ('library', '/library', '#papers article'),
        ('bookshelf', '/bookshelf', '.book'),
        ('quill', '/write', '.manuscript-row'),
        ('editor', '/write?id=' + projects[0], '#source .cm-content'),
        ('chat', '/chat', '#message'),
    ]:
        page.goto(base + route)
        if name == 'chat':
            skip = page.locator('#guideSkip')
            if skip.is_visible():
                skip.click()
        expect(page.locator(ready).first).to_be_visible()
        expect(page.locator('html')).to_have_attribute('data-theme', theme)
        expect(page.locator('html')).to_have_attribute('data-background', 'city-cat')
        expect(page.locator('#globalNavigation nav a')).to_have_text(['OriginRAG', 'Bookshelf', 'Library', 'Quill', 'Folio'])
        current_fonts = page.locator('#globalNavigation nav a').evaluate_all('links => links.map(link => getComputedStyle(link).fontSize)')
        if navigation_fonts is None:
            navigation_fonts = current_fonts
        assert current_fonts == navigation_fonts, (name, current_fonts, navigation_fonts)
        expect(page.locator('#globalNavigation [aria-current="page"]')).to_have_attribute('href', route.split('?')[0])
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), name
        expect(page.locator('#globalAccount')).to_be_hidden()
        for selector in ['#appearanceButton', '#globalSettings']:
            control = page.locator(selector)
            expect(control).to_be_visible()
            box = control.bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= viewport['width'] + 1, (name, selector)
        for link in page.locator('#globalNavigation nav a').all():
            assert link.evaluate(NAVIGATION_CONTRAST) >= 4.5, (name, theme, link.inner_text())
        surface_text = {
            'library': ['#papers h3', '#papers p'],
            'quill': ['.manuscript-row h3', '.manuscript-row p'],
            'editor': ['.writer-toolbar h1', '#savedState', '.files-heading strong', '.pdf-empty h2', '#source .cm-content'],
        }
        for selector in surface_text.get(name, []):
            assert page.locator(selector).first.evaluate(NAVIGATION_CONTRAST) >= 4.5, (name, theme, selector)
        page.keyboard.press('Tab')
        page.locator('#globalSettings').focus()
        assert page.locator('#globalSettings').evaluate('''element => {
            const style = getComputedStyle(element);
            return element.matches(':focus-visible') && style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2;
        }'''), name
        if name == 'bookshelf':
            expect(page.locator('.shelf-level')).to_have_count(2)
            expect(page.locator('.book')).to_have_count(len(PAPERS))
            for book in page.locator('.book').all():
                assert book.bounding_box()['height'] <= 300
                expect(book.locator('h3')).to_have_attribute('title', book.locator('h3').inner_text())
        if name == 'quill':
            for row in page.locator('.manuscript-row').all():
                assert row.bounding_box()['height'] < 180
        capture_design(page, name, theme, viewport)
        if name == 'chat':
            assert page.evaluate('typeof window.markdownit') == 'function'
            answer = {
                'answer': '## A useful starting point\n\nThese illustrative papers support transparent reporting and careful interpretation. '
                          'Multiple reports can describe the same underlying study.\n\n'
                          '### What to check\n\n- Identify the study behind each report.\n'
                          '- Keep complementary methods and outcomes.\n- Review possible study overlap before claiming independent replication.\n\n'
                          'This is synthetic content for a user-interface test, not a scientific finding.',
                'answer_state': 'provisional', 'grounding_passed': True, 'evidence_grade': 'usable',
                'n_unique_papers': 2, 'n_chunks': 3, 'elapsed_seconds': 12,
                'assurance': {'audit_run': True, 'provisional': True, 'cited_report_count': 2,
                              'cited_publication_family_upper_bound': 2, 'lineage_graph_status': 'unreviewed'},
                'sources': [{**paper, 'cited': True, 'url': 'https://example.org/' + paper['paper_id']}
                            for paper in PAPERS[:2]],
            }
            page.evaluate('''data => {
                addMessage('user', 'How should these reports be interpreted together?');
                addMessage('assistant', data.answer, resultMeta(data), data);
            }''', answer)
            expect(page.locator('.assistant .markdown')).to_contain_text('A useful starting point')
            assert page.locator('.assistant .markdown').evaluate('element => element.scrollWidth <= element.clientWidth + 1')
            assert page.locator('.assistant .markdown p').first.evaluate(NAVIGATION_CONTRAST) >= 4.5
            for selector in ['#message', '#send']:
                box = page.locator(selector).bounding_box()
                assert box['y'] >= 0 and box['y'] + box['height'] <= viewport['height'] + 1
            capture_design(page, 'chat-answer', theme, viewport)


def test_appearance_choice_survives_switching_apps_and_modes(design_page):
    base, page, expect, _ = design_page
    page.locator('#appearanceButton').click()
    page.locator('#appearanceMode').select_option('light')
    page.locator('#appearanceBackground').select_option('lunar-horizon')
    page.keyboard.press('Escape')
    for route in ['/bookshelf', '/write', '/library']:
        page.goto(base + route)
        expect(page.locator('html')).to_have_attribute('data-theme', 'light')
        expect(page.locator('html')).to_have_attribute('data-background', 'lunar-horizon')
    page.locator('#appearanceButton').click()
    page.locator('#appearanceMode').select_option('dark')
    page.keyboard.press('Escape')
    page.reload()
    expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
    expect(page.locator('html')).to_have_attribute('data-background', 'lunar-horizon')


def test_quill_light_dialogs_preserve_readability_and_source(design_page, monkeypatch):
    from originrag import writing_language

    base, page, expect, projects = design_page
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '1')

    def forbid_model(*args, **kwargs):
        raise AssertionError('Design checks must not invoke a language model')

    monkeypatch.setattr(writing_language, 'check_language', forbid_model)
    page.evaluate('window.originragAppearance.set({mode: "light", background: "city-cat"})')
    page.goto(base + '/write?id=' + projects[0])
    source = page.locator('#source .cm-content')
    expect(source).to_be_visible()
    original = source.inner_text()
    original_style = source.evaluate('element => { const s = getComputedStyle(element); return [s.color, s.backgroundColor, s.fontSize]; }')

    def check_dialog(identifier, selectors):
        dialog = page.locator(identifier)
        expect(dialog).to_be_visible()
        assert dialog.evaluate('element => element.scrollWidth <= element.clientWidth + 1')
        for selector in selectors:
            item = dialog.locator(selector).first
            expect(item).to_be_visible()
            assert item.evaluate(NAVIGATION_CONTRAST) >= 4.5, (identifier, selector)
        capture_design(page, identifier.removeprefix('#'), 'light', {'width': 1440})

    def close_dialog(identifier):
        page.locator(identifier).locator('.dialog-close').click()
        expect(page.locator(identifier)).to_have_count(0)
        assert source.inner_text() == original
        assert source.evaluate('element => { const s = getComputedStyle(element); return [s.color, s.backgroundColor, s.fontSize]; }') == original_style

    page.locator('#referenceButton').click()
    page.locator('#referencesNewName').fill('Design review references')
    page.locator('#referencesCreate').click()
    expect(page.locator('#referencesLibrary option:checked')).to_have_text('Design review references')
    page.locator('#referencesCorpusTab').click()
    page.get_by_role('button', name='Add to personal library', exact=True).first.click()
    page.locator('#referencesLibraryTab').click()
    expect(page.locator('.references-key')).to_have_count(1)
    page.locator('#referencesPreview').click()
    check_dialog('#referencesDialog', ['h2', '.references-muted', '.references-sidebar label',
                                      '.references-key', '#referencesReview h3'])
    close_dialog('#referencesDialog')

    page.locator('#templateButton').click()
    check_dialog('#templateDialog', ['h2', '.muted', '.template-card h3', '.template-card p', '.template-layout'])
    close_dialog('#templateDialog')

    page.get_by_role('button', name='Ask Quill', exact=True).click()
    expect(page.locator('#projectChatPanel')).to_be_visible()
    expect(page.locator('#projectChatInput')).to_have_value('')
    expect(page.locator('#projectChatSend')).to_be_disabled()
    page.locator('#projectChatClose').click()
    expect(page.locator('#projectChatPanel')).to_be_hidden()
