"""Optional independent DOCX checks: run with python-docx installed."""
import base64
import io

import pytest

docx = pytest.importorskip('docx')

from originrag.folio import DEFAULT_PAGE, plain_text, validate_content  # noqa: E402
from originrag.folio_docx import export_docx, import_docx  # noqa: E402


def paragraph(text, **extra):
    return {'type': 'paragraph', 'content': [{'type': 'text', 'text': text, **extra}]}


def test_export_can_be_opened_by_an_independent_docx_library():
    image = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jw1sAAAAASUVORK5CYII='
    content = validate_content({'type': 'doc', 'content': [
        {'type': 'heading', 'attrs': {'level': 1}, 'content': [{'type': 'text', 'text': 'Independent check'}]},
        paragraph('Formatted evidence', marks=[{'type': 'bold'}, {'type': 'italic'}]),
        {'type': 'table', 'content': [
            {'type': 'tableRow', 'content': [
                {'type': 'tableHeader', 'content': [paragraph('Feature')]},
                {'type': 'tableHeader', 'content': [paragraph('Value')]},
            ]},
            {'type': 'tableRow', 'content': [
                {'type': 'tableCell', 'content': [paragraph('Count')]},
                {'type': 'tableCell', 'content': [paragraph('42')]},
            ]},
        ]},
        {'type': 'image', 'attrs': {'src': 'data:image/png;base64,' + image, 'width': 100, 'height': 100}},
    ]})
    raw = export_docx({'title': 'Independent check', 'content': content, 'page': DEFAULT_PAGE, 'comments': []})
    opened = docx.Document(io.BytesIO(raw))
    assert opened.paragraphs[0].text == 'Independent check'
    assert any(run.bold and run.italic for p in opened.paragraphs for run in p.runs
               if 'Formatted evidence' in run.text)
    assert opened.tables[0].cell(1, 1).text == '42'
    assert len(opened.inline_shapes) == 1
    assert opened.sections[0].page_width == pytest.approx(21 * 360_000, abs=2_000)
    assert opened.sections[0].page_height == pytest.approx(29.7 * 360_000, abs=2_000)
    pictures = [part.blob for part in opened.part.package.parts if part.content_type == 'image/png']
    assert pictures == [base64.b64decode(image)]


def test_import_accepts_a_document_from_an_independent_docx_writer():
    original = docx.Document()
    original.add_heading('Imported independently', level=2)
    p = original.add_paragraph()
    p.add_run('Before ')
    p.add_run('emphasis').bold = True
    p.add_run(' after.')
    table = original.add_table(rows=2, cols=2)
    table.cell(0, 0).text = 'Category'
    table.cell(1, 1).text = 'Observed'
    stream = io.BytesIO()
    original.save(stream)
    imported, warnings = import_docx(stream.getvalue())
    content = validate_content(imported['content'])
    text = plain_text(content)
    assert 'Imported independently' in text
    assert 'Before emphasis after.' in text
    assert 'Observed' in text
    assert content['content'][0]['type'] == 'heading'
    assert content['content'][0]['attrs']['level'] == 2
    assert any({'type': 'bold'} in item.get('marks', [])
               for block in content['content'] for item in block.get('content', [])
               if item.get('text') == 'emphasis')
    assert isinstance(warnings, list)


def test_export_preserves_vertically_merged_table_cells():
    content = validate_content({'type': 'doc', 'content': [
        {'type': 'table', 'content': [
            {'type': 'tableRow', 'content': [
                {'type': 'tableCell', 'attrs': {'rowspan': 2}, 'content': [paragraph('Merged')]},
                {'type': 'tableCell', 'content': [paragraph('Upper right')]},
            ]},
            {'type': 'tableRow', 'content': [
                {'type': 'tableCell', 'content': [paragraph('Lower right')]},
            ]},
        ]},
    ]})
    raw = export_docx({'title': 'Merged table', 'content': content, 'page': DEFAULT_PAGE, 'comments': []})
    table = docx.Document(io.BytesIO(raw)).tables[0]
    assert table.cell(0, 0).text == table.cell(1, 0).text == 'Merged'
    assert table.cell(1, 1).text == 'Lower right'


def test_real_jpeg_aspect_and_dimensions_open_correctly_in_independent_reader():
    fitz = pytest.importorskip('fitz')
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 80, 40), False)
    pixmap.clear_with(255)
    raw_image = pixmap.tobytes('jpeg')
    document = {'title': 'JPEG size', 'page': DEFAULT_PAGE, 'comments': [], 'content': validate_content({
        'type': 'doc', 'content': [{'type': 'image', 'attrs': {'src': 'data:image/jpeg;base64,' + base64.b64encode(raw_image).decode(), 'height': 120}}]})}
    opened = docx.Document(io.BytesIO(export_docx(document)))
    assert opened.inline_shapes[0].width == 240 * 9525
    assert opened.inline_shapes[0].height == 120 * 9525


def test_resized_columns_and_nested_merged_table_open_in_independent_reader():
    content = validate_content({'type': 'doc', 'content': [{'type': 'table', 'content': [
        {'type': 'tableRow', 'content': [
            {'type': 'tableCell', 'attrs': {'rowspan': 2, 'colspan': 2, 'colwidth': [80, 120]}, 'content': [paragraph('Merged 2×2')]},
            {'type': 'tableCell', 'attrs': {'colwidth': [240]}, 'content': [paragraph('Right')]},
        ]},
        {'type': 'tableRow', 'content': [{'type': 'tableCell', 'attrs': {'colwidth': [240]}, 'content': [paragraph('Nested below'),
            {'type': 'table', 'content': [{'type': 'tableRow', 'content': [{'type': 'tableCell', 'content': [paragraph('Inner')]}]}]},
        ]}]},
    ]}]})
    opened = docx.Document(io.BytesIO(export_docx({'title': 'Grid', 'content': content, 'page': DEFAULT_PAGE, 'comments': []})))
    table = opened.tables[0]
    assert [column.width for column in table.columns] == [80 * 9525, 120 * 9525, 240 * 9525]
    assert table.cell(0, 0).text == table.cell(1, 1).text == 'Merged 2×2'
    assert table.cell(1, 2).tables[0].cell(0, 0).text == 'Inner'
