import base64
import io
import json
from pathlib import Path
import random
import shutil
import subprocess
import zipfile

import pytest

from originrag.writing import WritingStore, decode_file, unpack_project
from test_writing import workspace_server as workspace_server


ROOT = Path(__file__).resolve().parents[1]


def node_binary():
    node = shutil.which('node') or shutil.which('nodejs')
    if node:
        return node
    playwright = pytest.importorskip('playwright')
    node = Path(playwright.__file__).parent / 'driver' / 'node'
    if not node.is_file():
        pytest.skip('Node.js required for editor difference tests')
    return str(node)


def test_exact_bounded_editor_diff_unicode_random_and_separate_hunks():
    rng = random.Random(621)
    examples = [('', ''), ('abc', 'def'), ('a😀b', 'a😺b'),
                ('First\nMiddle local\nLast', 'FIRST\nMiddle local\nLAST'),
                ('a' * 1000, 'b' * 1000), ('a' * 1_000_001, 'a' * 1_000_000 + 'b')]
    for _ in range(1200):
        examples.append(tuple(''.join(rng.choice('abcα\n😀😺') for _ in range(rng.randrange(60))) for _ in range(2)))
    script = r'''
import {diffTextChanges} from './assets/workspace/latex-editor.mjs';
let raw = ''; for await (const chunk of process.stdin) raw += chunk;
const examples = JSON.parse(raw);
const results = examples.map(([before, after]) => {
  const changes = diffTextChanges(before, after);
  if (changes === null) return null;
  let reconstructed = before, end = -1;
  for (const change of changes) {
    if (change.from < end || change.from > change.to || change.to > before.length) throw Error('Invalid range');
    for (const position of [change.from, change.to]) {
      if (position && /[\uD800-\uDBFF]/.test(before[position - 1]) && /[\uDC00-\uDFFF]/.test(before[position])) throw Error('Split surrogate');
    }
    end = change.to;
  }
  for (const change of [...changes].reverse()) reconstructed = reconstructed.slice(0, change.from) + change.insert + reconstructed.slice(change.to);
  if (reconstructed !== after) throw Error('Text mismatch');
  return changes;
});
console.log(JSON.stringify(results));
'''
    result = subprocess.run([node_binary(), '--input-type=module', '-e', script], cwd=ROOT,
                            input=json.dumps(examples), text=True, capture_output=True, check=True, timeout=30)
    changes = json.loads(result.stdout)
    assert changes[0] == []
    assert len(changes[3]) == 2
    assert changes[4:6] == [None, None]
    assert all(value is not None for value in changes[6:])


def open_test_editor(page, base):
    page.route('**/editor-test', lambda route: route.fulfill(content_type='text/html', body='''
<!doctype html><html><head><style>#test-editor {height: 240px; width: 700px;}</style></head>
<body><input id="outside"><div id="test-editor"></div><script type="module">
import {createLatexEditor} from '/assets/workspace/latex-editor.mjs';
window.changes = []; window.positions = [];
window.editor = createLatexEditor(document.querySelector('#test-editor'),
  (name, text) => changes.push({name, text}), (line, column) => positions.push({line, column}));
</script></body></html>'''))
    page.goto(base + '/editor-test')
    page.wait_for_function('() => !!window.editor')


def test_remote_editor_changes_preserve_cursor_focus_and_local_undo(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        open_test_editor(page, base)
        page.evaluate(r"() => {editor.open('main.tex', 'First\nMiddle\nLast'); editor.jump(2);}")
        page.keyboard.press('ArrowRight')
        page.keyboard.insert_text(' local')
        position = page.evaluate('positions.at(-1)')
        assert page.evaluate('changes.length') == 1
        assert page.evaluate(r"editor.applyRemote('main.tex', 'FIRST\nMiddle local\nLAST')")
        assert page.evaluate('changes.length') == 1
        assert page.evaluate('positions.at(-1)') == position
        assert page.locator('.cm-content').evaluate('element => element === document.activeElement')
        page.evaluate("editor.command('undo')")
        assert page.evaluate('changes.at(-1).text') == 'FIRST\nMiddle\nLAST'
        page.evaluate("editor.command('redo')")
        assert page.evaluate('changes.at(-1).text') == 'FIRST\nMiddle local\nLAST'
        page.locator('#outside').focus()
        assert page.evaluate(r"editor.applyRemote('main.tex', 'FIRST\nMiddle local\nLAST!')")
        assert page.locator('#outside').evaluate('element => element === document.activeElement')
        assert page.evaluate('changes.length') == 3
        assert not errors
        browser.close()


def test_remote_cached_documents_permissions_and_budget_rejection(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        open_test_editor(page, base)
        page.evaluate(r"() => {editor.open('one.tex', 'First\nMiddle\nLast'); editor.jump(2);}")
        page.keyboard.press('ArrowRight')
        page.keyboard.insert_text(' local')
        page.evaluate("() => {editor.open('two.tex', 'Other'); editor.jump(1);}")
        page.keyboard.press('ArrowRight')
        page.keyboard.insert_text(' own')
        count = page.evaluate('changes.length')
        assert page.evaluate(r"editor.applyRemote('one.tex', 'FIRST\nMiddle local\nLAST')")
        assert page.evaluate('changes.length') == count
        assert page.locator('.cm-content').inner_text() == 'Other own'
        page.evaluate(r"editor.open('one.tex', 'FIRST\nMiddle local\nLAST')")
        page.evaluate("editor.command('undo')")
        assert page.evaluate('changes.at(-1).text') == 'FIRST\nMiddle\nLAST'
        page.evaluate("editor.open('two.tex', 'Other own', false)")
        assert page.locator('.cm-content').get_attribute('contenteditable') == 'false'
        page.evaluate("editor.open('two.tex', 'Other own', true)")
        page.evaluate("editor.command('undo')")
        assert page.evaluate('changes.at(-1).text') == 'Other'
        assert not page.evaluate("editor.applyRemote('two.tex', 'x'.repeat(2_000_001))")
        assert page.locator('.cm-content').inner_text() == 'Other'
        assert page.evaluate("editor.applyRemote('new.tex', 'New remote file')")
        assert page.locator('.cm-content').inner_text() == 'Other'
        page.evaluate("editor.open('new.tex', 'New remote file')")
        count = page.evaluate('changes.length')
        page.evaluate("editor.command('undo')")
        assert page.locator('.cm-content').inner_text() == 'New remote file'
        assert page.evaluate('changes.length') == count
        browser.close()


def test_remote_update_preserves_scroll_and_defers_ime_composition(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.add_init_script('window.EditContext = undefined')
        open_test_editor(page, base)
        original = '\n'.join(f'Line {i}: unchanged source text' for i in range(100))
        page.evaluate("text => {editor.open('main.tex', text); editor.jump(40);}", original)
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        scroller = page.locator('.cm-scroller')
        before = scroller.evaluate('element => element.scrollTop')
        updated = original.replace('Line 0:', 'First line:').replace('Line 99:', 'Final line:')
        assert page.evaluate("text => editor.applyRemote('main.tex', text)", updated)
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        assert abs(scroller.evaluate('element => element.scrollTop') - before) < 2
        assert page.evaluate('changes.length') == 0
        page.locator('.cm-content').dispatch_event('compositionstart', {'data': ''})
        assert not page.evaluate("text => editor.applyRemote('main.tex', text)", updated + '\nRemote append')
        page.locator('.cm-content').dispatch_event('compositionend', {'data': ''})
        assert page.evaluate("text => editor.applyRemote('main.tex', text)", updated + '\nRemote append')
        assert page.evaluate('changes.length') == 0
        browser.close()


def test_destroy_and_recreate_editor_discards_only_confirmed_old_draft_state(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        open_test_editor(page, base)
        page.evaluate("() => {editor.open('main.tex', 'Initial text'); editor.jump(1);}")
        page.keyboard.press('ArrowRight')
        page.keyboard.insert_text(' unsaved')
        assert page.evaluate('changes.length') == 1
        page.evaluate("() => {editor.open('other.tex', 'Cached draft'); editor.destroy();}")
        assert page.locator('.cm-editor').count() == 0
        page.evaluate('''async () => {
          const {createLatexEditor} = await import('/assets/workspace/latex-editor.mjs');
          window.newChanges = [];
          window.editor = createLatexEditor(document.querySelector('#test-editor'),
            (name, text) => newChanges.push({name, text}), () => {});
          editor.open('main.tex', 'Explicitly reloaded saved version');
          editor.command('undo');
        }''')
        assert page.locator('.cm-editor').count() == 1
        assert page.locator('.cm-content').inner_text() == 'Explicitly reloaded saved version'
        assert page.evaluate('newChanges.length') == 0
        page.evaluate("editor.jump(1)")
        page.keyboard.press('ArrowRight')
        page.keyboard.insert_text(' new edit')
        assert page.evaluate('newChanges.length') == 1
        assert page.evaluate('changes.length') == 1
        assert not errors
        browser.close()


def test_zip_export_reimport_preserves_utf8_nested_sources_and_binary_figures(tmp_path):
    store = WritingStore(tmp_path / 'owner')
    project = store.change({'action': 'create_project', 'name': 'Export original'}, [])
    original = {
        'main.tex': '\\documentclass{article}\n\\begin{document}\n\\input{sections/results}\n\\bibliography{references}\n\\end{document}',
        'sections/results.tex': 'Résultats α 😀: \\includegraphics{figures/chart.png} \\cite{Result2026}',
        'references.bib': '@article{Result2026, title={A result}, author={Müller, A.}, year={2026}}\n',
        'figures/chart.png': base64.b64encode(b'\x89PNG\r\n\x1a\n\x00\xff\x80binary').decode(),
        'figures/supplement.pdf': base64.b64encode(b'%PDF-1.7\n\x00\xff\x80\n%%EOF').decode(),
        'custom.sty': '\\ProvidesPackage{custom}\n',
    }
    store.change({**project, 'action': 'save_project', 'files': original}, [])
    archive = store.export(project['id'])
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        assert set(zipped.namelist()) == set(original)
        for filename, content in original.items():
            assert zipped.read(filename) == decode_file(filename, content)
    files, main = unpack_project(base64.b64encode(archive))
    assert main == 'main.tex' and files == original
    imported = store.change({'action': 'create_project', 'name': 'Imported copy',
                             'zip': base64.b64encode(archive).decode()}, [])
    assert imported['id'] != project['id']
    assert imported['files'] == original
    with zipfile.ZipFile(io.BytesIO(store.export(imported['id']))) as zipped:
        assert {name: zipped.read(name) for name in zipped.namelist()} == {
            name: decode_file(name, content) for name, content in original.items()}
