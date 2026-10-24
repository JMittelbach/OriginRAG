import os
from pathlib import Path

import pytest

from originrag.folio import validate_content


@pytest.fixture
def notes_page():
    playwright = pytest.importorskip('playwright.sync_api')
    folder = Path(__file__).resolve().parents[1] / 'assets/folio'
    modules = {name: (folder / name).read_text() for name in ('editor.mjs', 'document-tools.mjs')}
    with playwright.sync_playwright() as engine:
        browser = getattr(engine, os.environ.get('ORIGINRAG_TEST_BROWSER', 'chromium')).launch()
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('http://folio.test/**', lambda route: route.fulfill(
            status=200, content_type='application/javascript' if route.request.url.rsplit('/', 1)[-1] in modules else 'text/html',
            body=modules.get(route.request.url.rsplit('/', 1)[-1], '<main id="editor"></main>')))
        try:
            page.goto('http://folio.test/')
            page.evaluate('''async () => {
              window.api = await import('/editor.mjs');
              window.editor = api.createFolioEditor(document.querySelector('#editor'), {type:'doc',content:[{type:'paragraph'}]});
            }''')
            yield page
            assert not errors, errors
        finally:
            browser.close()


def test_note_numbering_updates_after_insert_edit_delete_and_reorder(notes_page):
    result = notes_page.evaluate('''() => {
      editor.commands.insertFolioNote({text:'First α 📝'});
      editor.commands.insertContent(' text ');
      editor.commands.insertFolioNote({text:'First endnote',kind:'endnote'});
      editor.commands.insertFolioNote({text:'Second footnote'});
      const before = api.collectFolioNotes(editor.state.doc);
      const beforeLabels = [...document.querySelectorAll('sup[data-folio-note-id]')].map(node=>node.textContent);
      editor.commands.updateFolioNote(before[2].id,{text:'Moved note',kind:'endnote'});
      const converted = api.collectFolioNotes(editor.state.doc);
      editor.commands.removeFolioNote(before[1].id);
      const removed = api.collectFolioNotes(editor.state.doc);
      const original = editor.getJSON().content[0].content;
      editor.commands.setContent({type:'doc',content:[{type:'paragraph',content:[...original].reverse()}]});
      const after = api.collectFolioNotes(editor.state.doc);
      return {before,beforeLabels,converted,removed,after,content:editor.getJSON(),
        labels:[...document.querySelectorAll('sup[data-folio-note-id]')].map(node=>({text:node.textContent,kind:node.dataset.noteKind,label:node.getAttribute('aria-label')}))};
    }''')
    assert [n['number'] for n in result['before']] == [1, 1, 2]
    assert result['beforeLabels'] == ['1', '1', '2']
    assert [n['number'] for n in result['converted']] == [1, 1, 2]
    assert [n['number'] for n in result['removed']] == [1, 1]
    assert [n['kind'] for n in result['after']] == ['endnote', 'footnote']
    assert result['labels'][0]['label'] == 'Endnote 1: Moved note'
    assert result['labels'][1]['text'] == '1'
    validate_content(result['content'])


def test_note_copy_has_new_identity_and_editing_copy_leaves_original_unchanged(notes_page):
    result = notes_page.evaluate('''() => {
      editor.commands.insertFolioNote({text:'Original note'});
      const original = api.collectFolioNotes(editor.state.doc)[0];
      const html = editor.getHTML();
      editor.commands.focus('end');
      editor.view.pasteHTML(html);
      const copied = api.collectFolioNotes(editor.state.doc);
      editor.commands.updateFolioNote(copied[1].id,{text:'Copy only'});
      editor.commands.insertContent({type:'footnote',attrs:{id:original.id,text:'Duplicate JSON',kind:'footnote'}});
      return {notes:api.collectFolioNotes(editor.state.doc),content:editor.getJSON()};
    }''')
    assert len({n['id'] for n in result['notes']}) == 3
    assert [n['text'] for n in result['notes']] == ['Original note', 'Copy only', 'Duplicate JSON']
    validate_content(result['content'])


def test_note_command_limits_and_inert_pasted_note_content(notes_page):
    result = notes_page.evaluate('''() => {
      const rejected = [editor.commands.insertFolioNote({text:'x'.repeat(4001)}),
        editor.commands.insertFolioNote({text:'bad\\u0000'}),editor.commands.insertFolioNote({text:'bad',kind:'evil'})];
      editor.commands.insertFolioNote({text:'<img src=x onerror=alert(1)>'});
      const note = api.collectFolioNotes(editor.state.doc)[0];
      rejected.push(editor.commands.updateFolioNote(note.id,{text:'x'.repeat(4001)}));
      const safe = editor.getJSON();
      const content = Array.from({length:500},(_,i)=>({type:'footnote',attrs:{id:i.toString(16).padStart(32,'0'),text:'Note '+i,kind:'footnote'}}));
      editor.commands.setContent({type:'doc',content:[{type:'paragraph',content}]});
      rejected.push(editor.commands.insertFolioNote({text:'Overflow'}));
      return {rejected,safe,count:api.collectFolioNotes(editor.state.doc).length,images:document.querySelectorAll('img[src],img[onerror]').length};
    }''')
    assert not any(result['rejected'])
    assert result['count'] == 500 and result['images'] == 0
    validate_content(result['safe'])


def test_reference_anchor_and_paragraph_spacing_engine_backend_parity(notes_page):
    result = notes_page.evaluate('''() => {
      editor.commands.setContent({type:'doc',content:[
        {type:'heading',attrs:{level:2,anchorId:'folio-anchor-'+'a'.repeat(32),spaceBefore:12.5,spaceAfter:6},content:[{type:'text',text:'Heading'}]},
        {type:'paragraph',attrs:{tocEntry:true,spaceBefore:0,spaceAfter:3},content:[{type:'text',text:'Heading',marks:[{type:'link',attrs:{href:'#folio-anchor-'+'a'.repeat(32)}}]}]},
      ]});
      const html = editor.getHTML();
      editor.commands.setContent(html);
      return {html,content:editor.getJSON(),heading:document.querySelector('h2').id,entry:document.querySelector('[data-folio-toc-entry]').textContent};
    }''')
    assert result['heading'] == 'folio-anchor-' + 'a' * 32
    assert 'margin-top: 12.5pt' in result['html']
    assert result['content']['content'][1]['attrs']['tocEntry'] is True
    assert result['entry'] == 'Heading'
    validate_content(result['content'])


@pytest.mark.parametrize('kind', ['heading', 'paragraph'])
def test_enter_does_not_copy_anchor_or_contents_marker_to_new_prose(notes_page, kind):
    result = notes_page.evaluate('''kind => {
      editor.commands.setContent({type:'doc',content:[{type:kind,attrs:{
        level:2,anchorId:'folio-anchor-'+'a'.repeat(32),tocEntry:true},content:[{type:'text',text:'Original'}]}]});
      editor.commands.setTextSelection(1 + 'Original'.length);
      editor.commands.splitBlock();
      editor.commands.insertContent('New prose');
      return editor.getJSON();
    }''', kind)
    assert result['content'][0]['attrs']['anchorId'] == 'folio-anchor-' + 'a' * 32
    assert result['content'][1]['attrs'].get('anchorId') is None
    assert result['content'][1]['attrs'].get('tocEntry') is False
    assert result['content'][1]['content'][0]['text'] == 'New prose'
    validate_content(result)


@pytest.mark.parametrize('selection', ['text', 'image', 'table', 'cells', 'all'])
def test_note_insertion_never_replaces_selected_document_content(notes_page, selection):
    result = notes_page.evaluate('''selection => {
      const paragraph = text => ({type:'paragraph',content:[{type:'text',text}]});
      const image = {type:'image',attrs:{src:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jw1sAAAAASUVORK5CYII=',alt:'Preserve picture'}};
      const table = {type:'table',content:[{type:'tableRow',content:['Left cell','Right cell'].map(text=>({type:'tableCell',content:[paragraph(text)]}))}]};
      const content = selection === 'image' ? [image,paragraph('After picture')] : ['table','cells'].includes(selection) ? [table,paragraph('After table')] : [paragraph('abcdef'),paragraph('Keep second paragraph')];
      editor.commands.setContent({type:'doc',content});
      const before = editor.state.doc.textContent;
      if (selection === 'text') editor.commands.setTextSelection({from:2,to:5});
      else if (selection === 'all') editor.commands.selectAll();
      else if (selection === 'cells') {
        const cells=[];editor.state.doc.descendants((node,pos)=>{if(node.type.name==='tableCell')cells.push(pos);});
        editor.commands.setCellSelection({anchorCell:cells[0],headCell:cells[1]});
      } else editor.commands.setNodeSelection(0);
      const selected = {from:editor.state.selection.from,to:editor.state.selection.to,type:editor.state.selection.constructor.name};
      const inserted = editor.commands.insertFolioNote({text:'Annotation, not replacement'});
      const result = {before,after:editor.state.doc.textContent,inserted,selected,content:editor.getJSON(),notes:api.collectFolioNotes(editor.state.doc),images:document.querySelectorAll('img[src]').length,cells:document.querySelectorAll('td').length};
      editor.commands.undo();
      result.undo = editor.state.doc.textContent;
      result.notesAfterUndo = api.collectFolioNotes(editor.state.doc).length;
      return result;
    }''', selection)
    assert result['selected']['to'] > result['selected']['from']
    assert result['inserted'] and result['before'] == result['after']
    assert len(result['notes']) == 1
    assert result['notes'][0]['text'] == 'Annotation, not replacement'
    if selection == 'image':
        assert result['images'] == 1
    if selection in {'table', 'cells'}:
        assert result['cells'] == 2
    assert result['undo'] == result['before'] and result['notesAfterUndo'] == 0
    validate_content(result['content'])


@pytest.mark.parametrize('selected', [False, True])
def test_note_insertion_inside_code_is_rejected_without_mutation(notes_page, selected):
    result = notes_page.evaluate('''selected => {
      editor.commands.setContent({type:'doc',content:[{type:'codeBlock',content:[{type:'text',text:'keep(code)'}]}]});
      editor.commands.setTextSelection(selected ? {from:2,to:5} : 3);
      const before=editor.getJSON(), selection=editor.state.selection.toJSON();
      const inserted=editor.commands.insertFolioNote({text:'Do not swallow this'});
      return {before,after:editor.getJSON(),selection,afterSelection:editor.state.selection.toJSON(),inserted,notes:api.collectFolioNotes(editor.state.doc)};
    }''', selected)
    assert result['inserted'] is False
    assert result['before'] == result['after'] and result['selection'] == result['afterSelection']
    assert result['notes'] == []


def test_pasted_heading_and_contents_metadata_cannot_steal_links_or_delete_prose(notes_page):
    result = notes_page.evaluate('''async () => {
      const tools=await import('/document-tools.mjs'), id='folio-anchor-'+'a'.repeat(32);
      editor.commands.setContent({type:'doc',content:[
        {type:'paragraph'},
        {type:'heading',attrs:{level:1,anchorId:id},content:[{type:'text',text:'Original target'}]},
        {type:'paragraph',content:[{type:'text',text:'Existing reference',marks:[{type:'link',attrs:{href:'#'+id}}]}]},
        {type:'paragraph'},
      ]});
      editor.commands.setTextSelection(1);
      editor.view.pasteHTML('<h1 id="'+id+'">Pasted heading</h1>');
      const before=tools.documentHeadings(editor.state.doc);
      editor.commands.focus('end');
      editor.view.pasteHTML('<p data-folio-toc-entry="true" id="'+id+'">Copied contents prose to keep</p>');
      const copiedMetadata=[];
      editor.state.doc.descendants(node=>{if(node.type.name==='paragraph' && node.textContent.includes('Copied contents'))copiedMetadata.push({...node.attrs});});
      tools.updateContents(editor);
      tools.updateContents(editor,true);
      const after=tools.documentHeadings(editor.state.doc);
      const link=[...document.querySelectorAll('a')].find(node=>node.textContent==='Existing reference');
      return {before,after,copiedMetadata,href:link.getAttribute('href'),content:editor.getJSON(),text:editor.state.doc.textContent};
    }''')
    assert result['before'][0]['id'] is None
    assert result['before'][1]['id'] == 'folio-anchor-' + 'a' * 32
    assert result['after'][1]['id'] == 'folio-anchor-' + 'a' * 32
    assert result['after'][0]['id'] != result['after'][1]['id']
    assert result['href'] == '#' + result['after'][1]['id']
    assert result['copiedMetadata'][0]['anchorId'] is None
    assert result['copiedMetadata'][0]['tocEntry'] is False
    assert 'Copied contents prose to keep' in result['text']
    validate_content(result['content'])
