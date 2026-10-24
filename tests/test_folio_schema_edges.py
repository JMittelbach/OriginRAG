"""Regression contract between the real, offline editor and persisted Folio JSON."""
from pathlib import Path

import pytest

from originrag.folio import DEFAULT_PAGE, color, document_html, paragraph, validate_content


PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jw1sAAAAASUVORK5CYII='


@pytest.fixture(scope='module')
def editor_page():
    playwright = pytest.importorskip('playwright.sync_api')
    bundle = (Path(__file__).resolve().parents[1] / 'assets/folio/editor.mjs').read_text()
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('http://folio.test/**', lambda route: route.fulfill(
            status=200,
            content_type='application/javascript' if route.request.url.endswith('/editor.mjs') else 'text/html',
            body=bundle if route.request.url.endswith('/editor.mjs') else '<main id="editor"></main>'))
        page.goto('http://folio.test/')
        page.evaluate('''async () => {
          const {createFolioEditor} = await import('/editor.mjs');
          window.editor = createFolioEditor(document.querySelector('#editor'), {type:'doc',content:[{type:'paragraph'}]});
        }''')
        yield page
        browser.close()
        assert not errors, errors


def walk(node):
    yield node
    for child in node.get('content', []):
        yield from walk(child)


def paste_and_round_trip(page, html):
    raw = page.evaluate('''html => {
      editor.commands.setContent('<p></p>'); editor.commands.setTextSelection(1);
      editor.view.pasteHTML(html); editor.state.doc.check(); return editor.getJSON();
    }''', html)
    clean = validate_content(raw)
    reloaded = page.evaluate('''content => {
      editor.commands.setContent(content); editor.state.doc.check(); return editor.getJSON();
    }''', clean)
    assert validate_content(reloaded) == clean
    return clean


@pytest.mark.parametrize(('css', 'expected'), [
    ('color:rgba(20,40,60,0.5);font-family:"游ゴシック";font-size:12.5pt',
     {'color': '#14283c80', 'fontFamily': '游ゴシック', 'fontSize': '12.5pt'}),
    ('color:hsl(120, 100%, 25%);font-size:1.5em', {'color': 'rgb(0, 128, 0)', 'fontSize': '24px'}),
    ('color:rebeccapurple;font-size:120%', {'color': '#663399', 'fontSize': '19.2px'}),
    ('color:rgb(255 0 0 / 25%);font-size:200px', {'color': '#ff000040', 'fontSize': '96px'}),
    ('font-family:"Noto Sans Devanagari", sans-serif;font-size:0.1em',
     {'fontFamily': '"Noto Sans Devanagari", sans-serif', 'fontSize': '6px'}),
])
def test_pasted_css_normalizes_to_savable_formatting(editor_page, css, expected):
    from html import escape
    clean = paste_and_round_trip(editor_page, f'<p><span style="{escape(css, quote=True)}">A😀é हिंदी العربية</span></p>')
    text = next(node for node in walk(clean) if node['type'] == 'text')
    assert text['text'] == 'A😀é हिंदी العربية'
    assert next(mark['attrs'] for mark in text['marks'] if mark['type'] == 'textStyle') == expected


def test_pasted_highlights_line_break_marks_and_line_spacing(editor_page):
    clean = paste_and_round_trip(editor_page, '<p>Before</p><p style="line-height:1.333333"><strong><span style="background-color:rgba(255,0,0,.5)">A<br>B</span></strong></p><p>After</p>')
    paragraph_node = clean['content'][1]
    assert paragraph_node['attrs']['lineHeight'] == '1.33'
    assert [node['type'] for node in paragraph_node['content']] == ['text', 'hardBreak', 'text']
    assert all([mark for mark in node['marks'] if mark != {'type': 'textStyle'}] ==
               [{'type': 'bold'}, {'type': 'highlight', 'attrs': {'color': '#ff000080'}}]
               for node in paragraph_node['content']), paragraph_node


def test_code_unicode_whitespace_and_nested_numbering(editor_page):
    code = 'A\n😀 α\tZ\n  indented'
    clean = paste_and_round_trip(editor_page, f'<pre><code>{code}</code></pre><ol start="3" type="A"><li><p>Outer</p><ol start="4" type="i"><li>Inner</li></ol></li></ol>')
    assert next(node for node in walk(clean) if node['type'] == 'codeBlock')['content'] == [{'type': 'text', 'text': code}]
    lists = [node['attrs'] for node in walk(clean) if node['type'] == 'orderedList']
    assert lists == [{'start': 3, 'type': 'A'}, {'start': 4, 'type': 'i'}]
    html = document_html({'title': 'Lists', 'content': clean, 'page': DEFAULT_PAGE})
    assert '<ol start="3" type="A">' in html and '<ol start="4" type="i">' in html


@pytest.mark.parametrize('html', [
    '<table><tr><td rowspan="2">A</td><td>B</td></tr><tr><td>C</td></tr></table>',
    '<table><tr><td rowspan="2" colspan="2">A</td></tr><tr></tr></table>',
    '<table><tr><td><p>Outer</p><table><tr><td>Inner</td></tr></table></td></tr></table>',
    '<table><tr><td>A</td><td>B</td></tr><tr><td>C</td></tr></table>',
])
def test_pasted_merged_nested_and_ragged_tables_stay_valid(editor_page, html):
    clean = paste_and_round_trip(editor_page, html)
    assert next(node for node in walk(clean) if node['type'] == 'table')


def test_pasted_table_column_widths_include_first_and_merged_columns(editor_page):
    clean = paste_and_round_trip(editor_page, '<table><colgroup><col width="120"><col style="width:135pt"></colgroup><tr><td>A</td><td>B</td></tr><tr><td colspan="2">AB</td></tr></table>')
    cells = [node for node in walk(clean) if node['type'] == 'tableCell']
    assert [node['attrs']['colwidth'] for node in cells] == [[120], [180], [120, 180]]
    clean = paste_and_round_trip(editor_page, '<table><tr><td colwidth="3200">wide</td><td colwidth="NaN">bad width</td></tr></table>')
    cells = [node for node in walk(clean) if node['type'] == 'tableCell']
    assert cells[0]['attrs']['colwidth'] == [2400]
    assert 'colwidth' not in cells[1]['attrs']


def test_explicit_image_proportions_and_constrained_table_images(editor_page):
    editor_page.add_style_tag(content='''#editor{width:400px}.tiptap img{max-width:100%;height:auto;display:block}
      .tiptap table{table-layout:fixed;width:100%;border-collapse:collapse}.tiptap td{padding:0}''')
    clean = paste_and_round_trip(editor_page, f'<p>Image</p><img src="{PNG}" width="120" height="40"><table><tr><td><img src="{PNG}" width="1200" height="300"></td><td>Other</td></tr></table>')
    editor_page.locator('img').first.evaluate('(image) => image.decode()')
    dimensions = editor_page.evaluate('''() => [...document.querySelectorAll('.tiptap img')].map(image => {
      const rect=image.getBoundingClientRect(); return {width:rect.width,height:rect.height,cell:image.closest('td')?.getBoundingClientRect().width};
    })''')
    assert dimensions[0]['width'] == pytest.approx(120)
    assert dimensions[0]['height'] == pytest.approx(40)
    assert dimensions[1]['width'] <= dimensions[1]['cell'] + 1
    assert dimensions[1]['width'] / dimensions[1]['height'] == pytest.approx(4, rel=.01)
    html = document_html({'title': 'Image', 'content': clean, 'page': DEFAULT_PAGE})
    assert 'aspect-ratio:120/40' in html and 'aspect-ratio:1200/300' in html


def test_image_drop_uses_pointer_and_file_rejections_are_explicit(editor_page):
    result = editor_page.evaluate('''async () => {
      const {createFolioEditor} = await import('/editor.mjs');
      const host = document.createElement('section'); document.body.append(host);
      const images = [], errors = [];
      const probe = createFolioEditor(host, '<p>First paragraph</p><p>Drop target paragraph</p>', {
        onImage: file => images.push({name:file.name,position:probe.state.selection.from}), onError: message => errors.push(message)
      });
      const image = new File(['image'], 'one.png', {type:'image/png'});
      const pdf = new File(['pdf'], 'file.pdf', {type:'application/pdf'});
      const target = probe.view.coordsAtPos(23), coordinates = {left:target.left,top:(target.top+target.bottom)/2};
      const expected = probe.view.posAtCoords(coordinates).pos;
      probe.commands.setTextSelection(1);
      const handlers = probe.options.editorProps;
      const accepted = handlers.handleDrop(probe.view,{dataTransfer:{files:[image]},clientX:coordinates.left,clientY:coordinates.top});
      const multiDrop = handlers.handleDrop(probe.view,{dataTransfer:{files:[image,image]}});
      const badDrop = handlers.handleDrop(probe.view,{dataTransfer:{files:[pdf]}});
      const multiPaste = handlers.handlePaste(probe.view,{clipboardData:{files:[image,image]}});
      const badPaste = handlers.handlePaste(probe.view,{clipboardData:{files:[pdf]}});
      const plainPaste = handlers.handlePaste(probe.view,{clipboardData:{files:[]}});
      probe.destroy(); host.remove();
      return {expected,images,errors,accepted,multiDrop,badDrop,multiPaste,badPaste,plainPaste};
    }''')
    assert result['expected'] > 16
    assert result['images'] == [{'name': 'one.png', 'position': result['expected']}]
    assert all(result[key] for key in ('accepted', 'multiDrop', 'badDrop', 'multiPaste', 'badPaste'))
    assert not result['plainPaste']
    assert result['errors'] == ['Insert one image at a time.', 'Insert a PNG, JPEG or WebP image.'] * 2


def cell(*, colspan=1, rowspan=1, children=None):
    return {'type': 'tableCell', 'attrs': {'colspan': colspan, 'rowspan': rowspan},
            'content': children if children is not None else [paragraph('Cell')]}


def table(*rows):
    return {'type': 'table', 'content': [{'type': 'tableRow', 'content': row} for row in rows]}


@pytest.mark.parametrize('invalid', [
    table([]), table([cell()], []), table([cell(), cell()], [cell()]),
    table([cell(rowspan=2)]), table([cell(colspan=100), cell()]),
    table([cell(), cell(rowspan=2)], [cell(colspan=2)]),
    table([cell(children=[table([])])]),
    {'type': 'codeBlock', 'content': [{'type': 'hardBreak'}]},
    {'type': 'codeBlock', 'content': [{'type': 'text', 'text': 'bad', 'marks': [{'type': 'bold'}]}]},
    {'type': 'bulletList'}, {'type': 'orderedList'}, {'type': 'taskList'},
    {'type': 'orderedList', 'attrs': {'type': 'bad'}, 'content': [{'type': 'listItem', 'content': [paragraph()]}]},
    {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'bad', 'marks': [{'type': 'bold'}, {'type': 'bold'}]}]},
    {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'bad', 'marks': [{'type': 'code'}, {'type': 'bold'}]}]},
    {'type': 'image', 'attrs': {'src': PNG}, 'marks': [{'type': 'bold'}]},
])
def test_json_structures_that_editor_would_repair_are_rejected(invalid):
    with pytest.raises(ValueError):
        validate_content({'type': 'doc', 'content': [invalid]})


def test_full_table_span_bound_and_empty_covered_rows():
    valid = table([cell(colspan=100, rowspan=100)], *([[]] * 99))
    result = validate_content({'type': 'doc', 'content': [valid]})
    assert len(result['content'][0]['content']) == 100
    assert result['content'][0]['content'][0]['content'][0]['attrs'] == {'colspan': 100, 'rowspan': 100}


@pytest.mark.parametrize(('value', 'expected'), [('rgba(20,40,60,.5)', '#14283c80'), ('rgba(255,0,0,0)', '#ff000000'), ('rgba(1,2,3,1)', '#010203ff')])
def test_alpha_colors_are_canonical_and_safe(value, expected):
    assert color(value) == expected


@pytest.mark.parametrize('value', ['rgba(300,0,0,.5)', 'rgba(0,0,0,1.1)', 'red;background:url(https://evil)', 'var(--color)'])
def test_unsafe_or_out_of_bounds_colors_are_rejected(value):
    with pytest.raises(ValueError):
        color(value)
