import io
import zipfile
import xml.etree.ElementTree as ET

import pytest

from originrag.folio import DEFAULT_PAGE, paragraph, validate_content
from originrag.folio_docx import NS, _parse_xml, export_docx, import_docx


def document():
    return {'title': 'Interchange', 'content': validate_content({'type': 'doc', 'content': [
        paragraph('Heading', heading=2),
        {'type': 'paragraph', 'attrs': {'textAlign': 'center', 'indent': 2, 'lineHeight': '1.5'},
         'content': [{'type': 'text', 'text': 'Link and bold', 'marks': [{'type': 'bold'},
                      {'type': 'link', 'attrs': {'href': 'https://before.example'}}]}]},
        {'type': 'pageBreak'},
    ]}), 'page': {**DEFAULT_PAGE, 'size': 'letter', 'orientation': 'landscape'},
        'comments': [{'id': 'comment1', 'text': 'Review this', 'quote': 'Heading', 'resolved': False, 'created_at': ''}]}


def rebuild(raw, replacements=None, remove=()):
    stream = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as original, zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as output:
        for name in original.namelist():
            if name not in remove:
                output.writestr(name, (replacements or {}).get(name, original.read(name)))
        for name, data in (replacements or {}).items():
            if name not in original.namelist():
                output.writestr(name, data)
    return stream.getvalue()


def test_unchanged_docx_lossless_round_trip_and_actual_word_parts():
    original = document()
    raw = export_docx(original)
    imported, warnings = import_docx(raw)
    assert imported == original and not warnings
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        body = ET.fromstring(archive.read('word/document.xml'))
        assert body.find('.//w:b', NS) is not None
        assert body.find('.//w:hyperlink', NS) is not None
        assert body.find('.//w:commentReference', NS) is not None
        assert archive.read('word/comments.xml')


@pytest.mark.parametrize('part', ['word/styles.xml', 'word/_rels/document.xml.rels', 'word/comments.xml'])
def test_any_changed_semantic_part_invalidates_lossless_snapshot(part):
    raw = export_docx(document())
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        data = archive.read(part).replace(b'https://before.example', b'https://after.example')
    changed = rebuild(raw, {part: data + b'\n'})
    imported, warnings = import_docx(changed)
    assert warnings
    if part.endswith('.rels'):
        marks = imported['content']['content'][1]['content'][0]['marks']
        assert next(m['attrs']['href'] for m in marks if m['type'] == 'link') == 'https://after.example'


def test_external_common_word_formatting_is_read_without_sidecar():
    raw = rebuild(export_docx(document()), remove=('customXml/folio.json',))
    imported, warnings = import_docx(raw)
    assert warnings
    heading, para = imported['content']['content'][:2]
    assert heading['type'] == 'heading' and heading['attrs']['level'] == 2
    assert para['attrs'] == {'textAlign': 'center', 'indent': 2, 'lineHeight': '1.5'}
    assert imported['page']['size'] == 'letter' and imported['page']['orientation'] == 'landscape'
    assert imported['comments'][0]['text'].endswith('Review this')


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16', 'utf-16-le', 'utf-32'])
def test_xml_entities_rejected_for_every_encoding(encoding):
    raw = ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY evil "expanded">]><x>&evil;</x>').encode(encoding)
    with pytest.raises(ValueError):
        _parse_xml(raw)


@pytest.mark.parametrize('filename', ['../escape.xml', '/absolute.xml', 'word/../escape.xml', 'word\\escape.xml', 'word/vbaProject.bin'])
def test_unsafe_archives_and_macros_rejected(filename):
    raw = rebuild(export_docx(document()), {filename: b'unsafe'})
    with pytest.raises(ValueError):
        import_docx(raw)


def test_archive_member_count_expanded_bytes_ratio_and_duplicate_paths():
    original = export_docx(document())
    many = rebuild(original, {f'junk/{i}.txt': b'x' for i in range(513)})
    with pytest.raises(ValueError, match='file count'):
        import_docx(many)
    huge = rebuild(original, {'junk.txt': b'x' * 2_000_000})
    with pytest.raises(ValueError, match='compression ratio'):
        import_docx(huge)
    expanded = rebuild(original, {f'junk/{i}.txt': b'x' * 1_000_000 for i in range(25)})
    with pytest.raises(ValueError, match='expanded size'):
        import_docx(expanded)
    output = io.BytesIO(original)
    with pytest.warns(UserWarning), zipfile.ZipFile(output, 'a') as archive:
        archive.writestr('word/document.xml', b'<duplicate/>')
    with pytest.raises(ValueError, match='duplicate'):
        import_docx(output.getvalue())


def test_remote_images_not_fetched_or_kept():
    main = f'''<w:document xmlns:w="{NS['w']}" xmlns:a="{NS['a']}" xmlns:r="{NS['r']}">
      <w:body><w:p><w:r><w:drawing><a:blip r:link="rIdRemote"/></w:drawing></w:r></w:p></w:body></w:document>'''
    rels = '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdRemote" TargetMode="External" Target="http://127.0.0.1/private" Type="image"/></Relationships>'''
    raw = rebuild(export_docx(document()), {'word/document.xml': main.encode(), 'word/_rels/document.xml.rels': rels.encode()}, remove=('customXml/folio.json',))
    imported, _ = import_docx(raw)
    assert all(n['type'] != 'image' for n in imported['content']['content'])


def test_vertical_merge_export_and_external_import():
    original = document()
    original['content'] = validate_content({'type': 'doc', 'content': [{'type': 'table', 'content': [
        {'type': 'tableRow', 'content': [
            {'type': 'tableCell', 'attrs': {'rowspan': 2}, 'content': [paragraph('Merged')]},
            {'type': 'tableCell', 'content': [paragraph('Top right')]},
        ]},
        {'type': 'tableRow', 'content': [{'type': 'tableCell', 'content': [paragraph('Lower right')]}]},
    ]}]})
    raw = export_docx(original)
    imported, _ = import_docx(rebuild(raw, remove=('customXml/folio.json',)))
    table = imported['content']['content'][0]
    assert table['content'][0]['content'][0]['attrs']['rowspan'] == 2
    assert len(table['content'][1]['content']) == 1
    assert table['content'][1]['content'][0]['content'][0]['content'][0]['text'] == 'Lower right'


def test_separate_numbered_lists_restart_and_preserve_start_and_continuations():
    original = document()

    def ordered(start, text):
        return {'type': 'orderedList', 'attrs': {'start': start}, 'content': [
            {'type': 'listItem', 'content': [paragraph(text), paragraph('Unnumbered continuation')]},
            {'type': 'listItem', 'content': [paragraph('Second item')]},
        ]}

    original['content'] = validate_content({'type': 'doc', 'content': [ordered(7, 'First list'), paragraph('Between lists'), ordered(1, 'Restarted list')]})
    raw = export_docx(original)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        main = ET.fromstring(archive.read('word/document.xml'))
        nums = [n.get('{' + NS['w'] + '}val') for n in main.findall('.//w:numId', NS)]
        assert nums == ['2', '2', '3', '3']
        numbering = ET.fromstring(archive.read('word/numbering.xml'))
        starts = [n.get('{' + NS['w'] + '}val') for n in numbering.findall('.//w:startOverride', NS)]
        assert starts == ['7', '1']
    imported, _ = import_docx(rebuild(raw, remove=('customXml/folio.json',)))
    lists = [n for n in imported['content']['content'] if n['type'] == 'orderedList']
    assert lists[0]['attrs']['start'] == 7
    assert lists[-1]['attrs']['start'] == 1
