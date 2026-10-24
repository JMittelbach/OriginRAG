"""Exercise the checked-in, offline editor bundle without the surrounding UI."""
from pathlib import Path

import pytest

from originrag.folio import validate_content


def test_bundled_engine_preserves_supported_nodes_and_mixed_selection_formatting():
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
        try:
            page.goto('http://folio.test/')
            result = page.evaluate('''async () => {
              const {createFolioEditor, safeLink} = await import('/editor.mjs');
              const text = value => [{type:'text',text:value}];
              const editor = createFolioEditor(document.querySelector('#editor'), {type:'doc',content:[
                {type:'heading',attrs:{level:6},content:text('Heading six')},
                {type:'paragraph',content:text('A paragraph')},
                {type:'taskList',content:[{type:'taskItem',attrs:{checked:true},content:[{type:'paragraph',content:text('Checklist item')}]}]},
                {type:'table',content:[{type:'tableRow',content:[
                  {type:'tableHeader',attrs:{backgroundColor:'#aabbcc'},content:[{type:'paragraph',content:text('Shaded header')}]},
                  {type:'tableCell',attrs:{backgroundColor:'rgb(20, 40, 60)'},content:[{type:'paragraph',content:text('Shaded cell')}]}
                ]}]}
              ]});
              editor.commands.setTextSelection({from:1,to:editor.state.doc.content.size-1});
              const spaced = editor.commands.setParagraphSpacing('2');
              const font = editor.commands.setFontSize('16pt');
              const bold = editor.commands.toggleBold();
              const result = {spaced,font,bold,content:editor.getJSON(),h6:!!document.querySelector('h6'),
                checkbox:!!document.querySelector('input[type=checkbox]:checked'),safe:safeLink('https://example.com'),
                unsafe:['https:evil.com','javascript:alert(1)','https://good.com\\\\evil','https://'].map(safeLink)};
              const html = editor.getHTML();
              editor.commands.setContent(html);
              result.htmlRoundTrip = editor.getJSON();
              editor.commands.setContent({type:'doc',content:[{type:'paragraph'}]});
              editor.commands.setTextSelection(1);
              const image = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jw1sAAAAASUVORK5CYII=';
              editor.view.pasteHTML(`<img src="${image}" width="120" height="90">`);
              result.pastedImage = editor.getJSON().content.find(node => node.type === 'image');
              editor.destroy();
              return result;
            }''')
            assert result['spaced'] and result['font'] and result['bold']
            assert result['h6'] and result['checkbox']
            assert result['content']['content'][0]['attrs']['lineHeight'] == '2'
            assert result['content']['content'][1]['attrs']['lineHeight'] == '2'
            assert result['content']['content'][2]['type'] == 'taskList'
            table = result['content']['content'][3]
            assert table['content'][0]['content'][0]['attrs']['backgroundColor'] == '#aabbcc'
            assert table['content'][0]['content'][1]['attrs']['backgroundColor'] == 'rgb(20, 40, 60)'
            pasted_table = next(n for n in result['htmlRoundTrip']['content'] if n['type'] == 'table')
            assert pasted_table['content'][0]['content'][0]['attrs']['backgroundColor'] == 'rgb(170, 187, 204)'
            assert pasted_table['content'][0]['content'][1]['attrs']['backgroundColor'] == 'rgb(20, 40, 60)'
            assert result['pastedImage']['attrs']['width'] == 120
            assert result['pastedImage']['attrs']['height'] == 90
            validate_content(result['content'])
            validate_content(result['htmlRoundTrip'])
            validate_content({'type': 'doc', 'content': [result['pastedImage']]})
            assert result['safe'] and not any(result['unsafe'])
            assert not errors, errors
        finally:
            browser.close()
