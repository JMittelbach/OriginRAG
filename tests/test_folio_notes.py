import copy
import io
import re
import xml.etree.ElementTree as ET
import zipfile

import pytest

from originrag.folio import DEFAULT_PAGE, document_html, plain_text, validate_content
from originrag.folio_docx import NS, export_docx, import_docx, tag


def note(text='A note', kind='footnote', key='a' * 32):
    return {'type': 'footnote', 'attrs': {'id': key, 'text': text, 'kind': kind}}


def document(nodes=None):
    return {'title': 'Notes', 'page': DEFAULT_PAGE, 'comments': [], 'content': validate_content({
        'type': 'doc', 'content': [{'type': 'paragraph', 'content': nodes or [
            {'type': 'text', 'text': 'Before'}, note('First\nsecond\tthird α'),
            {'type': 'text', 'text': ' between '}, note('End <script>not active</script>', 'endnote', 'b' * 32),
            {'type': 'text', 'text': ' after'}, note('Second footnote', key='c' * 32),
        ]}]})}


def package(raw, changes=None, *, sidecar=False):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = {name: archive.read(name) for name in archive.namelist() if sidecar or name != 'customXml/folio.json'}
    files.update(changes or {})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return stream.getvalue()


def notes(content):
    found = []
    def visit(node):
        if node['type'] == 'footnote':
            found.append(node['attrs'])
        for child in node.get('content', []):
            visit(child)
    visit(content)
    return found


def test_duplicate_note_ids_create_independent_notes_and_number_by_document_order():
    doc = document([note('one'), note('two'), note('three', 'endnote'), note('four', key='bad')])
    found = notes(doc['content'])
    assert len({n['id'] for n in found}) == 4
    assert all(re.fullmatch(r'[0-9a-f]{32}', n['id']) for n in found)
    assert [n['text'] for n in found] == ['one', 'two', 'three', 'four']
    assert plain_text(doc['content']).startswith('[1][2][E1][3]')
    assert 'Footnotes\n1. one\n2. two\n3. four' in plain_text(doc['content'])
    assert 'Endnotes\n1. three' in plain_text(doc['content'])


@pytest.mark.parametrize('attrs', [
    {'kind': 'malicious'}, {'kind': []}, {'text': 'x' * 4001}, {'text': '\x00'}, {'text': '\ud800'}, {'text': 2},
])
def test_invalid_note_fields_rejected(attrs):
    entry = note()
    entry['attrs'].update(attrs)
    with pytest.raises(ValueError):
        document([entry])


def test_notes_bounded_and_cannot_contain_nested_objects_or_marks():
    assert len(notes(document([note() for _ in range(500)])['content'])) == 500
    with pytest.raises(ValueError, match='500'):
        document([note() for _ in range(501)])
    for extension in [{'content': [{'type': 'text', 'text': 'nested'}]}, {'marks': [{'type': 'bold'}]}]:
        with pytest.raises(ValueError):
            document([{**note(), **extension}])


def test_html_notes_safe_numbered_linked_and_printable():
    doc = document()
    rendered = document_html(doc)
    assert 'End &lt;script&gt;not active&lt;/script&gt;' in rendered
    assert '<script>' not in rendered
    for entry in notes(doc['content']):
        assert f'id="note-{entry["id"]}"' in rendered
        assert f'href="#note-{entry["id"]}"' in rendered
        assert f'href="#note-ref-{entry["id"]}"' in rendered
    assert 'aria-label="Footnote 2"' in rendered
    assert 'aria-label="Endnote 1"' in rendered


def test_docx_contains_real_note_parts_relationships_and_reference_order():
    doc = document()
    raw = export_docx(doc)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        main = ET.fromstring(archive.read('word/document.xml'))
        foot = ET.fromstring(archive.read('word/footnotes.xml'))
        end = ET.fromstring(archive.read('word/endnotes.xml'))
        assert [n.get(tag('w:id')) for n in main.findall('.//w:footnoteReference', NS)] == ['1', '2']
        assert [n.get(tag('w:id')) for n in main.findall('.//w:endnoteReference', NS)] == ['1']
        assert len(foot.findall('w:footnote', NS)) == 4
        assert len(end.findall('w:endnote', NS)) == 3
        assert foot.find('.//w:footnoteRef', NS) is not None
        assert end.find('.//w:endnoteRef', NS) is not None
        rels = archive.read('word/_rels/document.xml.rels')
        types = archive.read('[Content_Types].xml')
        assert b'Target="footnotes.xml"' in rels and b'Target="endnotes.xml"' in rels
        assert b'wordprocessingml.footnotes+xml' in types and b'wordprocessingml.endnotes+xml' in types
    imported, warnings = import_docx(raw)
    assert imported == doc and warnings == []
    native, warnings = import_docx(package(raw))
    assert [(n['kind'], n['text']) for n in notes(native['content'])] == [(n['kind'], n['text']) for n in notes(doc['content'])]
    assert [n['type'] for n in native['content']['content'][0]['content']] == [n['type'] for n in doc['content']['content'][0]['content']]
    assert not any('not imported' in warning for warning in warnings)


def test_changed_note_part_invalidates_snapshot():
    raw = export_docx(document([note('Original note')]))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        changed = archive.read('word/footnotes.xml').replace(b'Original note', b'Edited in Word')
    imported, warnings = import_docx(package(raw, {'word/footnotes.xml': changed}, sidecar=True))
    assert notes(imported['content'])[0]['text'] == 'Edited in Word'
    assert warnings


def test_native_notes_survive_independent_docx_library_roundtrip():
    docx = pytest.importorskip('docx')
    original = document()
    opened = docx.Document(io.BytesIO(export_docx(original)))
    note_parts = {relationship.reltype.rsplit('/', 1)[-1]: relationship.target_part
                  for relationship in opened.part.rels.values()
                  if relationship.reltype.rsplit('/', 1)[-1] in {'footnotes', 'endnotes'}}
    assert set(note_parts) == {'footnotes', 'endnotes'}
    assert b'First' in note_parts['footnotes'].blob
    assert b'End &lt;script&gt;' in note_parts['endnotes'].blob
    opened.paragraphs[0].add_run(' Edited independently')
    stream = io.BytesIO()
    opened.save(stream)
    imported, _ = import_docx(stream.getvalue())
    assert [(n['kind'], n['text']) for n in notes(imported['content'])] == [(n['kind'], n['text']) for n in notes(original['content'])]
    assert 'Edited independently' in plain_text(imported['content'])


def test_complex_native_note_preserves_text_and_warns_about_flattened_features():
    raw = export_docx(document([note()]))
    part = f'''<w:footnotes xmlns:w="{NS['w']}"><w:footnote w:id="1">
      <w:p><w:r><w:footnoteRef/></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>Bold note</w:t></w:r></w:p>
      <w:p><w:hyperlink><w:r><w:t>Linked text</w:t></w:r></w:hyperlink><w:del><w:r><w:delText>Deleted</w:delText></w:r></w:del></w:p>
      </w:footnote></w:footnotes>'''
    imported, warnings = import_docx(package(raw, {'word/footnotes.xml': part}))
    assert notes(imported['content'])[0]['text'] == 'Bold note\nLinked text'
    assert any('plain text' in warning for warning in warnings)


@pytest.mark.parametrize('part', [
    '<w:footnote w:id="1"><w:p><w:r><w:t>one</w:t></w:r></w:p></w:footnote>' * 2,
    '<w:footnote w:id="no"><w:p/></w:footnote>',
    '<w:footnote w:id="1"><w:p><w:r><w:t>' + 'x' * 4001 + '</w:t></w:r></w:p></w:footnote>',
])
def test_invalid_native_notes_rejected(part):
    raw = export_docx(document([note()]))
    with pytest.raises(ValueError):
        import_docx(package(raw, {'word/footnotes.xml': f'<w:footnotes xmlns:w="{NS["w"]}">{part}</w:footnotes>'}))


def test_external_note_relationship_not_fetched_and_no_fake_empty_note_created():
    raw = export_docx(document([note()]))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        rels = archive.read('word/_rels/document.xml.rels').replace(b'Target="footnotes.xml"', b'Target="https://example.invalid/notes.xml" TargetMode="External"')
    imported, warnings = import_docx(package(raw, {'word/_rels/document.xml.rels': rels}))
    assert not notes(imported['content'])
    assert any('external or missing' in warning for warning in warnings)


def test_notes_in_table_and_heading_remain_in_document_order():
    doc = document()
    doc['content'] = validate_content({'type': 'doc', 'content': [
        {'type': 'heading', 'attrs': {'level': 2}, 'content': [note('Heading note')]},
        {'type': 'table', 'content': [{'type': 'tableRow', 'content': [{'type': 'tableCell', 'content': [
            {'type': 'paragraph', 'content': [note('Cell note')]}]}]}]},
    ]})
    imported, _ = import_docx(package(export_docx(doc)))
    assert [n['text'] for n in notes(imported['content'])] == ['Heading note', 'Cell note']


def test_note_only_content_in_merged_continuation_cell_is_not_discarded():
    raw = export_docx(document([note('Keep this merged-cell note')]))
    body = f'''<w:document xmlns:w="{NS['w']}"><w:body><w:tbl>
      <w:tr><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>Top</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p><w:r><w:footnoteReference w:id="1"/></w:r></w:p></w:tc></w:tr>
      </w:tbl></w:body></w:document>'''
    imported, warnings = import_docx(package(raw, {'word/document.xml': body}))
    assert [n['text'] for n in notes(imported['content'])] == ['Keep this merged-cell note']
    assert any('continuation cells' in warning for warning in warnings)


def test_spacing_and_folio_anchors_roundtrip_native_word_bookmarks():
    anchor = 'folio-anchor-' + 'a' * 32
    doc = document()
    doc['content'] = validate_content({'type': 'doc', 'content': [
        {'type': 'heading', 'attrs': {'level': 1, 'anchorId': anchor, 'spaceBefore': 12.5, 'spaceAfter': 6, 'lineHeight': '1.5'},
         'content': [{'type': 'text', 'text': 'Destination'}]},
        {'type': 'paragraph', 'attrs': {'tocEntry': True}, 'content': [
            {'type': 'text', 'text': 'Destination', 'marks': [{'type': 'link', 'attrs': {'href': '#' + anchor}}]}]},
    ]})
    rendered = document_html(doc)
    assert f'id="{anchor}"' in rendered and 'margin-top:12.5pt' in rendered
    raw = export_docx(doc)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
        bookmark = root.find('.//w:bookmarkStart', NS)
        assert len(bookmark.get(tag('w:name'))) <= 40
        assert root.find('.//w:hyperlink', NS).get(tag('w:anchor')) == bookmark.get(tag('w:name'))
        assert len(root.findall('.//w:p[1]/w:pPr/w:spacing', NS)) == 1
    imported, _ = import_docx(package(raw))
    heading = imported['content']['content'][0]
    assert heading['attrs']['anchorId'] == anchor
    assert heading['attrs']['spaceBefore'] == 12.5 and heading['attrs']['spaceAfter'] == 6
    assert heading['attrs']['lineHeight'] == '1.5'
    assert imported['content']['content'][1]['attrs']['tocEntry'] is True
    assert imported['content']['content'][1]['content'][0]['marks'][0]['attrs']['href'] == '#' + anchor


@pytest.mark.parametrize('attrs', [{'anchorId': 'javascript:bad'}, {'spaceBefore': -1}, {'spaceAfter': 145}, {'spaceAfter': float('nan')}, {'spaceAfter': True}, {'tocEntry': 'true'}])
def test_invalid_paragraph_attributes_rejected(attrs):
    doc = copy.deepcopy(document()['content'])
    doc['content'][0]['attrs'] = attrs
    with pytest.raises(ValueError):
        validate_content(doc)
