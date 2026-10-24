"""Independent literal-OOXML fixtures for DOCX edge cases (no Folio sidecar)."""
import base64
import io
import struct
import zipfile
import xml.etree.ElementTree as ET
import zlib

import pytest

from originrag.folio import DEFAULT_PAGE, paragraph, plain_text, validate_content
from originrag.folio_docx import NS, export_docx, import_docx


def package(body, *, numbering=None, styles=None, extra=None):
    namespaces = ' '.join(f'xmlns:{prefix}="{uri}"' for prefix, uri in NS.items())
    main = f'<w:document {namespaces}><w:body>{body}</w:body></w:document>'
    files = {'[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
             'word/document.xml': main, **(extra or {})}
    for name, value in (('numbering', numbering), ('styles', styles)):
        if value is not None:
            files[f'word/{name}.xml'] = f'<w:{name} xmlns:w="{NS["w"]}">{value}</w:{name}>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return out.getvalue()


def exported(*nodes):
    return export_docx({'title': 'Edge cases', 'page': DEFAULT_PAGE, 'comments': [],
                        'content': validate_content({'type': 'doc', 'content': list(nodes)})})


def main_xml(raw, name='word/document.xml'):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return ET.fromstring(archive.read(name))


def cell(text, **attrs):
    return {'type': 'tableCell', 'attrs': attrs, 'content': [paragraph(text)]}


def test_export_text_tabs_and_newlines_use_word_controls():
    root = main_xml(exported(paragraph('Before\tmiddle\nafter\r\nlast')))
    assert len(root.findall('.//w:tab', NS)) == 1
    assert len(root.findall('.//w:br', NS)) == 2
    assert ''.join(n.text or '' for n in root.findall('.//w:t', NS)) == 'Beforemiddleafterlast'


def test_export_table_column_widths_are_not_replaced_with_equal_widths():
    raw = exported({'type': 'table', 'content': [
        {'type': 'tableRow', 'content': [cell('Small', colwidth=[120]), cell('Wide', colwidth=[240])]},
        {'type': 'tableRow', 'content': [cell('Spanning', colspan=2, colwidth=[120, 240])]},
    ]})
    root = main_xml(raw)
    assert [int(n.get('{' + NS['w'] + '}w')) for n in root.findall('.//w:gridCol', NS)] == [1800, 3600]
    assert [int(n.get('{' + NS['w'] + '}w')) for n in root.findall('.//w:tcW', NS)] == [1800, 3600, 5400]


def test_import_table_grid_widths_and_omitted_leading_cell_keep_positions():
    raw = package('''<w:tbl><w:tblGrid><w:gridCol w:w="1800"/><w:gridCol w:w="3600"/></w:tblGrid>
      <w:tr><w:trPr><w:gridBefore w:val="1"/></w:trPr>
      <w:tc><w:p><w:r><w:t>Right column</w:t></w:r></w:p></w:tc></w:tr></w:tbl>''')
    imported, warnings = import_docx(raw)
    cells = imported['content']['content'][0]['content'][0]['content']
    assert len(cells) == 2
    assert cells[0]['attrs']['colwidth'] == [120]
    assert cells[1]['attrs']['colwidth'] == [240]
    assert plain_text({'type': 'doc', 'content': cells[0]['content']}).strip() == ''
    assert 'Right column' in plain_text({'type': 'doc', 'content': cells[1]['content']})
    assert any('omitted' in warning.lower() for warning in warnings)


def test_nested_numbering_levels_import_as_nested_lists_not_siblings():
    numbering = '''<w:abstractNum w:abstractNumId="0">
      <w:lvl w:ilvl="0"><w:start w:val="3"/><w:numFmt w:val="decimal"/></w:lvl>
      <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/></w:lvl>
      </w:abstractNum><w:num w:numId="7"><w:abstractNumId w:val="0"/></w:num>'''
    body = ''.join(f'''<w:p><w:pPr><w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="7"/></w:numPr></w:pPr>
      <w:r><w:t>{text}</w:t></w:r></w:p>''' for level, text in [(0, 'Parent'), (1, 'Child'), (1, 'Child two'), (0, 'Parent two')])
    imported, _ = import_docx(package(body, numbering=numbering))
    root = imported['content']['content'][0]
    assert root['type'] == 'orderedList' and root['attrs']['start'] == 3
    assert len(root['content']) == 2
    nested = root['content'][0]['content'][1]
    assert nested['type'] == 'bulletList' and len(nested['content']) == 2


def test_page_breaks_keep_their_position_and_multiplicity_within_a_paragraph():
    raw = package('''<w:p><w:r><w:t>Before</w:t><w:br w:type="page"/><w:t>Middle</w:t>
      <w:br w:type="page"/><w:t>After</w:t></w:r></w:p>''')
    imported, _ = import_docx(raw)
    content = imported['content']['content']
    assert [node['type'] for node in content] == ['paragraph', 'pageBreak', 'paragraph', 'pageBreak', 'paragraph']
    assert [node['content'][0]['text'] for node in content if node['type'] == 'paragraph'] == ['Before', 'Middle', 'After']


def png(width=80, height=40):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\xaa\xbb\xcc' * width) * height)) + chunk(b'IEND', b''))


def without_snapshot(raw):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(out, 'w') as dest:
        for name in source.namelist():
            if name != 'customXml/folio.json':
                dest.writestr(name, source.read(name))
    return out.getvalue()


@pytest.mark.parametrize('attrs,expected', [({}, (80, 40)), ({'width': 300}, (300, 150)),
                                         ({'height': 200}, (400, 200)), ({'width': 300, 'height': 200}, (300, 200))])
def test_picture_native_aspect_and_explicit_dimensions_and_accessibility(attrs, expected):
    raw = exported({'type': 'image', 'attrs': {
        'src': 'data:image/png;base64,' + base64.b64encode(png()).decode(),
        'alt': 'Accessible figure description', 'title': 'Figure title', **attrs}})
    root = main_xml(raw)
    extent = root.find('.//wp:extent', NS)
    assert (int(extent.get('cx')), int(extent.get('cy'))) == tuple(n * 9525 for n in expected)
    imported, _ = import_docx(without_snapshot(raw))
    image = next(n for n in imported['content']['content'] if n['type'] == 'image')
    assert (image['attrs']['width'], image['attrs']['height']) == expected
    assert image['attrs']['alt'] == 'Accessible figure description'
    assert image['attrs']['title'] == 'Figure title'


def test_text_and_multiple_inline_images_preserve_reading_order():
    drawing = '''<w:drawing><wp:inline><wp:extent cx="762000" cy="381000"/><wp:docPr id="1" name="figure" descr="Diagram"/>
      <a:graphic><a:graphicData><a:blip r:embed="img"/></a:graphicData></a:graphic></wp:inline></w:drawing>'''
    body = '<w:p><w:r><w:t>Before</w:t>' + drawing + '<w:t>Between</w:t>' + drawing + '<w:t>After</w:t></w:r></w:p>'
    rels = f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="img" Type="{NS["r"]}/image" Target="media/figure.png"/></Relationships>'
    imported, warnings = import_docx(package(body, extra={'word/media/figure.png': png(), 'word/_rels/document.xml.rels': rels}))
    blocks = imported['content']['content']
    assert [n['type'] for n in blocks] == ['paragraph', 'image', 'paragraph', 'image', 'paragraph']
    assert [n['content'][0]['text'] for n in blocks if n['type'] == 'paragraph'] == ['Before', 'Between', 'After']
    assert all(n['attrs']['alt'] == 'Diagram' for n in blocks if n['type'] == 'image')
    assert any('block images' in warning for warning in warnings)


@pytest.mark.parametrize('list_type,word_format', [('1', 'decimal'), ('a', 'lowerLetter'), ('A', 'upperLetter'),
                                                 ('i', 'lowerRoman'), ('I', 'upperRoman')])
def test_ordered_list_format_and_start_are_preserved_in_actual_word_parts(list_type, word_format):
    node = {'type': 'orderedList', 'attrs': {'start': 4, 'type': list_type}, 'content': [
        {'type': 'listItem', 'content': [paragraph('Fourth'), paragraph('Continuation')]},
        {'type': 'listItem', 'content': [paragraph('Fifth')]},
    ]}
    raw = exported(node)
    root, nums = main_xml(raw), main_xml(raw, 'word/numbering.xml')
    num_id = root.find('.//w:numId', NS).get('{' + NS['w'] + '}val')
    instance = next(n for n in nums.findall('w:num', NS) if n.get('{' + NS['w'] + '}numId') == num_id)
    abstract_id = instance.find('w:abstractNumId', NS).get('{' + NS['w'] + '}val')
    abstract = next(n for n in nums.findall('w:abstractNum', NS) if n.get('{' + NS['w'] + '}abstractNumId') == abstract_id)
    assert abstract.find('w:lvl/w:numFmt', NS).get('{' + NS['w'] + '}val') == word_format
    imported, _ = import_docx(without_snapshot(raw))
    result = imported['content']['content'][0]
    assert result['attrs']['start'] == 4 and result['attrs'].get('type', '1') == list_type
    assert len(result['content']) == 2 and len(result['content'][0]['content']) == 2


def test_numbered_list_continues_across_an_intervening_plain_paragraph():
    numbering = '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="5"/><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
    numbered = '<w:p><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Item</w:t></w:r></w:p>'
    imported, _ = import_docx(package(numbered + '<w:p><w:r><w:t>Aside</w:t></w:r></w:p>' + numbered, numbering=numbering))
    assert [n['attrs']['start'] for n in imported['content']['content'] if n['type'] == 'orderedList'] == [5, 6]


def test_inherited_paragraph_character_styles_and_direct_off_override():
    styles = '''<w:style w:styleId="Normal" w:type="paragraph" w:default="1"><w:rPr><w:rFonts w:ascii="Arial"/><w:sz w:val="22"/></w:rPr></w:style>
      <w:style w:styleId="Base" w:type="paragraph"><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:color w:val="123456"/></w:rPr></w:style>
      <w:style w:styleId="CustomHeading" w:type="paragraph"><w:basedOn w:val="Base"/><w:pPr><w:outlineLvl w:val="1"/><w:jc w:val="center"/></w:pPr></w:style>
      <w:style w:styleId="Emphasis" w:type="character"><w:rPr><w:i/></w:rPr></w:style>'''
    body = '''<w:p><w:pPr><w:pStyle w:val="CustomHeading"/></w:pPr><w:r><w:rPr><w:rStyle w:val="Emphasis"/><w:b w:val="0"/></w:rPr><w:t>Styled heading</w:t></w:r></w:p>'''
    imported, _ = import_docx(package(body, styles=styles))
    node = imported['content']['content'][0]
    assert node['type'] == 'heading' and node['attrs'] == {'level': 2, 'textAlign': 'center'}
    marks = node['content'][0]['marks']
    assert {'type': 'italic'} in marks and {'type': 'bold'} not in marks
    assert next(m['attrs'] for m in marks if m['type'] == 'textStyle') == {'fontFamily': 'Arial', 'fontSize': '11pt', 'color': '#123456'}


def test_cyclic_styles_are_bounded_and_reported():
    styles = '<w:style w:styleId="A"><w:basedOn w:val="B"/></w:style><w:style w:styleId="B"><w:basedOn w:val="A"/></w:style>'
    imported, warnings = import_docx(package('<w:p><w:pPr><w:pStyle w:val="A"/></w:pPr><w:r><w:t>Still readable</w:t></w:r></w:p>', styles=styles))
    assert 'Still readable' in plain_text(imported['content'])
    assert any('cyclic' in w.lower() for w in warnings)


def test_code_blocks_import_as_plain_text_without_disallowed_marked_breaks():
    raw = exported({'type': 'codeBlock', 'content': [{'type': 'text', 'text': 'line one\n\tline two'}]})
    imported, _ = import_docx(without_snapshot(raw))
    code = imported['content']['content'][0]
    assert code['type'] == 'codeBlock'
    assert code['content'] == [{'type': 'text', 'text': 'line one\n\tline two'}]


def test_vertical_merge_continuation_text_is_not_silently_discarded():
    raw = package('''<w:tbl><w:tr><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>Top</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p><w:r><w:t>Continuation text</w:t></w:r></w:p></w:tc></w:tr></w:tbl>''')
    imported, warnings = import_docx(raw)
    merged = imported['content']['content'][0]['content'][0]['content'][0]
    assert merged['attrs']['rowspan'] == 2
    assert 'Continuation text' in plain_text({'type': 'doc', 'content': merged['content']})
    assert any('continuation cells' in w for w in warnings)


def test_inconsistent_vertical_merge_keeps_both_cell_contents_and_reports_repair():
    raw = package('''<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>Wide top</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p><w:r><w:t>Narrow bottom</w:t></w:r></w:p></w:tc></w:tr></w:tbl>''')
    imported, warnings = import_docx(raw)
    assert 'Wide top' in plain_text(imported['content']) and 'Narrow bottom' in plain_text(imported['content'])
    assert any('inconsistent vertical' in w for w in warnings)
    validate_content(imported['content'])


def test_nested_table_and_horizontal_vertical_merge_survive_native_round_trip():
    nested = {'type': 'table', 'content': [{'type': 'tableRow', 'content': [cell('Nested content')]}]}
    table = {'type': 'table', 'content': [
        {'type': 'tableRow', 'content': [cell('Merged', colspan=2, rowspan=2), cell('Top right')]},
        {'type': 'tableRow', 'content': [{'type': 'tableCell', 'content': [paragraph('Container'), nested]}]},
    ]}
    imported, _ = import_docx(without_snapshot(exported(table)))
    result = imported['content']['content'][0]
    assert result['content'][0]['content'][0]['attrs']['colspan'] == 2
    assert result['content'][0]['content'][0]['attrs']['rowspan'] == 2
    nested_result = result['content'][1]['content'][0]['content'][1]
    assert nested_result['type'] == 'table' and 'Nested content' in plain_text(nested_result)


def test_field_cached_text_and_explicit_unsupported_feature_warnings():
    body = '''<w:p><w:r><w:t>Figure </w:t></w:r><w:fldSimple w:instr="SEQ Figure"><w:r><w:t>3</w:t></w:r></w:fldSimple>
      <w:r><w:footnoteReference w:id="1"/></w:r></w:p><w:altChunk r:id="external"/>'''
    imported, warnings = import_docx(package(body))
    assert 'Figure 3' in plain_text(imported['content'])
    assert any('fields' in warning for warning in warnings)
    assert any('Footnotes' in warning for warning in warnings)
    assert any('Embedded objects' in warning for warning in warnings)


def test_export_properties_are_unique_and_in_word_schema_order():
    raw = exported({'type': 'paragraph', 'attrs': {'textAlign': 'center', 'indent': 1, 'lineHeight': '1.5'}, 'content': [
        {'type': 'text', 'text': 'Styled link', 'marks': [
            {'type': 'textStyle', 'attrs': {'fontFamily': 'Arial', 'fontSize': '14pt', 'color': '#123456'}},
            {'type': 'underline'}, {'type': 'subscript'}, {'type': 'bold'},
            {'type': 'link', 'attrs': {'href': 'https://example.com'}},
        ]},
    ]})
    root = main_xml(raw)
    def local(element):
        return element.tag.rsplit('}', 1)[-1]
    paragraph_properties = root.find('.//w:pPr', NS)
    assert list(map(local, paragraph_properties)) == ['spacing', 'ind', 'jc']
    run_properties = root.find('.//w:rPr', NS)
    assert list(map(local, run_properties)) == ['rFonts', 'b', 'color', 'sz', 'u', 'vertAlign']
    assert run_properties.find('w:color', NS).get('{' + NS['w'] + '}val') == '123456'
