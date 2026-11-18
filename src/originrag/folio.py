"""Private, revisioned rich-text documents and a deliberately bounded editor schema."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
from urllib.parse import urlsplit
import uuid

MAX_DOCUMENT_BYTES = 8_000_000
MAX_IMPORT_BYTES = 8_000_000
MAX_IMAGE_BYTES = 2_000_000
MAX_DOCUMENTS = 500
MAX_OWNER_BYTES = 100_000_000
MAX_NOTES = 500
MAX_NOTE_TEXT = 4000
DEFAULT_PAGE = {'size': 'a4', 'orientation': 'portrait', 'margin': 'normal'}
BLOCKS = {'paragraph', 'heading', 'blockquote', 'bulletList', 'orderedList', 'taskList',
          'codeBlock', 'horizontalRule', 'image', 'shape', 'table', 'pageBreak'}
SHAPES = {'rect': 'Rectangle', 'roundRect': 'Rounded rectangle', 'ellipse': 'Ellipse'}
INLINE = {'text', 'hardBreak', 'footnote'}
MARKS = {'bold', 'italic', 'underline', 'strike', 'code', 'link', 'textStyle',
         'highlight', 'subscript', 'superscript'}
MARK_ORDER = {name: index for index, name in enumerate(
    ('link', 'textStyle', 'bold', 'code', 'italic', 'strike', 'underline', 'highlight', 'subscript', 'superscript'))}
_NO_EXPORT_REVISION = object()


class FolioConflict(ValueError):
    """The requested revision is no longer current; never silently overwrite."""


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('Invalid document identifier.')
    return value


def clean_text(value, limit, label, *, empty=True):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'Invalid {label}; maximum {limit:,} characters.')
    if any(ord(c) < 32 and c not in '\t\n\r' or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError(f'Invalid control character in {label}.')
    if not empty and not value.strip():
        raise ValueError(f'{label.capitalize()} cannot be empty.')
    return value


def color(value):
    # CSS serialization uses rgba for alpha colours; retain alpha in a form
    # shared by the editor, HTML and DOCX renderers.
    if isinstance(value, str):
        rgba = re.fullmatch(r'rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(0(?:\.\d{1,8})?|1(?:\.0{1,8})?|\.\d{1,8})\s*\)', value)
        if rgba and all(int(c) <= 255 for c in rgba.groups()[:3]):
            return '#' + ''.join(f'{int(c):02x}' for c in rgba.groups()[:3]) + f'{int(float(rgba[4]) * 255 + 0.5):02x}'
    if not isinstance(value, str) or not re.fullmatch(
        r'#[0-9a-fA-F]{3,8}|(?:black|white|red|blue|green|yellow|orange|purple|gray|grey|transparent|navy|teal|maroon)|rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\)', value):
        raise ValueError('Use a valid text or highlight colour.')
    if value.startswith('#') and len(value) not in {4, 5, 7, 9}:
        raise ValueError('Invalid colour.')
    if value.startswith('rgb') and any(int(n) > 255 for n in re.findall(r'\d+', value)):
        raise ValueError('Invalid colour.')
    return value


def safe_link(value):
    clean_text(value, 2000, 'link', empty=False)
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or '\\' in value:
        raise ValueError('Links cannot contain whitespace or control characters.')
    if value.startswith('#'):
        return value
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValueError('Invalid link.') from exc
    if parsed.scheme.lower() not in {'https', 'http', 'mailto', 'tel'}:
        raise ValueError('Only http, https, email and telephone links are supported.')
    if parsed.scheme.lower() in {'http', 'https'} and not parsed.hostname:
        raise ValueError('A web link needs a hostname.')
    return value


def image_data(value):
    if not isinstance(value, str) or len(value) > MAX_IMAGE_BYTES * 4 // 3 + 100:
        raise ValueError('An image must be no larger than 2 MB.')
    match = re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)', value)
    if not match:
        raise ValueError('Insert an embedded PNG, JPEG or WebP image; remote images are not loaded.')
    try:
        data = base64.b64decode(match[2], validate=True)
    except ValueError as exc:
        raise ValueError('Invalid image encoding.') from exc
    if len(data) > MAX_IMAGE_BYTES or not (
        match[1] == 'png' and data.startswith(b'\x89PNG\r\n\x1a\n')
        or match[1] == 'jpeg' and data.startswith(b'\xff\xd8\xff')
        or match[1] == 'webp' and data.startswith(b'RIFF') and data[8:12] == b'WEBP'
    ):
        raise ValueError('The image contents do not match its supported image type.')
    width, height = _image_dimensions(match[1], data)
    if not 1 <= width <= 8192 or not 1 <= height <= 8192 or width * height > 32_000_000:
        raise ValueError('An image must fit within 8,192 pixels per side and 32 megapixels.')
    return match[1], data


def _image_dimensions(kind, data):
    """Read only bounded raster headers; prevent tiny compressed huge-canvas images."""
    if kind == 'png' and len(data) >= 33 and data[8:16] == b'\x00\x00\x00\rIHDR':
        return int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
    if kind == 'webp':
        if len(data) >= 30 and data[12:16] == b'VP8X':
            return int.from_bytes(data[24:27], 'little') + 1, int.from_bytes(data[27:30], 'little') + 1
        if len(data) >= 30 and data[12:16] == b'VP8 ' and data[23:26] == b'\x9d\x01\x2a':
            return int.from_bytes(data[26:28], 'little') & 0x3fff, int.from_bytes(data[28:30], 'little') & 0x3fff
        if len(data) >= 25 and data[12:16] == b'VP8L' and data[20] == 0x2f:
            bits = int.from_bytes(data[21:25], 'little')
            return (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
    if kind == 'jpeg':
        offset, segments = 2, 0
        frames = {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}
        while offset + 3 < len(data) and segments < 2048:
            if data[offset] != 0xff:
                break
            while offset < len(data) and data[offset] == 0xff:
                offset += 1
            if offset + 3 >= len(data):
                break
            marker, offset = data[offset], offset + 1
            size = int.from_bytes(data[offset:offset + 2], 'big')
            if size < 2 or offset + size > len(data):
                break
            if marker in frames and size >= 8:
                return int.from_bytes(data[offset + 5:offset + 7], 'big'), int.from_bytes(data[offset + 3:offset + 5], 'big')
            if marker in {0xda, 0xd9}:
                break
            offset += size
            segments += 1
    raise ValueError('The image has an invalid or unsupported raster header.')


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'Invalid {label}.')
    return value


def _attrs(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError('Invalid formatting attributes.')
    return value


def _mark(mark):
    if not isinstance(mark, dict) or not isinstance(mark.get('type'), str) or mark['type'] not in MARKS:
        raise ValueError('Unsupported text formatting.')
    kind, attrs, out = mark['type'], _attrs(mark.get('attrs')), {}
    if kind == 'link':
        out = {'href': safe_link(attrs.get('href')), 'target': '_blank', 'rel': 'noopener noreferrer'}
    elif kind == 'textStyle':
        if attrs.get('fontFamily'):
            font = attrs['fontFamily']
            if not isinstance(font, str) or not 1 <= len(font) <= 100 or any(
                not (c.isalnum() or unicodedata.category(c).startswith('M') or c in ' ,-.\'"_') for c in font
            ):
                raise ValueError('Invalid font family.')
            out['fontFamily'] = font
        if attrs.get('fontSize'):
            size = str(attrs['fontSize'])
            match = re.fullmatch(r'(\d{1,2}(?:\.\d{1,2})?)(px|pt)?', size)
            if not match or not 6 <= float(match[1]) <= 96:
                raise ValueError('Font size must be 6–96 px or pt.')
            out['fontSize'] = size if match[2] else size + 'pt'
        if attrs.get('color'):
            out['color'] = color(attrs['color'])
    elif kind == 'highlight' and attrs.get('color'):
        out['color'] = color(attrs['color'])
    return {'type': kind, **({'attrs': out} if out else {})}


def _validate_table_grid(table):
    """Bound logical cells; never let the editor repair away imported content."""
    rows = table['content']
    grid = [[False] * 100 for _ in rows]
    widths = []
    for row_index, row in enumerate(rows):
        column = 0
        for cell in row.get('content', []):
            while column < 100 and grid[row_index][column]:
                column += 1
            colspan, rowspan = cell['attrs']['colspan'], cell['attrs']['rowspan']
            if column + colspan > 100 or row_index + rowspan > len(rows):
                raise ValueError('Table spans must fit within 100 columns and the table rows.')
            for occupied_row in range(row_index, row_index + rowspan):
                for occupied_col in range(column, column + colspan):
                    if grid[occupied_row][occupied_col]:
                        raise ValueError('Table cells cannot overlap.')
                    grid[occupied_row][occupied_col] = True
            column += colspan
        occupied = grid[row_index]
        width = max((index + 1 for index, value in enumerate(occupied) if value), default=0)
        if not width or not all(occupied[:width]):
            raise ValueError('Table rows must form a complete rectangular grid.')
        widths.append(width)
    if len(set(widths)) != 1:
        raise ValueError('Table rows must have the same logical column count.')


def validate_content(content):
    """Rebuild allowlisted JSON; unknown HTML/CSS attributes never reach a renderer."""
    if not isinstance(content, dict) or content.get('type') != 'doc':
        raise ValueError('Content must be an editor document.')
    try:
        if len(json.dumps(content, ensure_ascii=True).encode()) > MAX_DOCUMENT_BYTES:
            raise ValueError('A document must be no larger than 8 MB.')
    except (RecursionError, TypeError) as exc:
        raise ValueError('Invalid document JSON.') from exc
    count = 0
    note_ids = set()

    def node(value, depth=0):
        nonlocal count
        count += 1
        if count > 20_000 or depth > 32 or not isinstance(value, dict):
            raise ValueError('The document is too complex (20,000 nodes / 32 levels).')
        kind = value.get('type')
        if not isinstance(kind, str) or kind not in BLOCKS | INLINE | {'doc', 'listItem', 'taskItem', 'tableRow', 'tableCell', 'tableHeader'}:
            raise ValueError('Unsupported document element.')
        attrs, out = _attrs(value.get('attrs')), {'type': kind}
        clean = {}
        if kind == 'text':
            out['text'] = clean_text(value.get('text'), 1_000_000, 'text')
            if not out['text']:
                raise ValueError('Text nodes cannot be empty.')
        if kind == 'footnote':
            if len(note_ids) >= MAX_NOTES:
                raise ValueError('A document supports up to 500 footnotes and endnotes.')
            note_kind = attrs.get('kind', 'footnote')
            if not isinstance(note_kind, str) or note_kind not in {'footnote', 'endnote'}:
                raise ValueError('Choose a footnote or endnote.')
            key = attrs.get('id')
            if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{32}', key) or key in note_ids:
                key = uuid.uuid4().hex
            note_ids.add(key)
            clean = {'id': key, 'kind': note_kind,
                     'text': clean_text(attrs.get('text', ''), MAX_NOTE_TEXT, 'note')}
        if kind in {'paragraph', 'heading'}:
            if attrs.get('anchorId') is not None:
                if not isinstance(attrs['anchorId'], str) or not re.fullmatch(r'folio-anchor-[0-9a-f]{32}', attrs['anchorId']):
                    raise ValueError('Invalid paragraph anchor.')
                clean['anchorId'] = attrs['anchorId']
            for key in ('spaceBefore', 'spaceAfter'):
                if attrs.get(key) is not None:
                    spacing_value = attrs[key]
                    if type(spacing_value) not in {int, float} or not 0 <= spacing_value <= 144:
                        raise ValueError('Paragraph spacing must be between 0 and 144 pt.')
                    clean[key] = round(spacing_value, 2)
            if attrs.get('textAlign') is not None:
                if not isinstance(attrs['textAlign'], str) or attrs['textAlign'] not in {'left', 'center', 'right', 'justify'}:
                    raise ValueError('Invalid text alignment.')
                clean['textAlign'] = attrs['textAlign']
            if attrs.get('indent') is not None:
                clean['indent'] = _integer(attrs['indent'], 0, 8, 'paragraph indent')
            if attrs.get('lineHeight') is not None:
                spacing = str(attrs['lineHeight'])
                if not re.fullmatch(r'[123](?:\.\d{1,2})?', spacing) or not 1 <= float(spacing) <= 3:
                    raise ValueError('Line spacing must be between 1 and 3.')
                clean['lineHeight'] = spacing
        if kind == 'paragraph' and attrs.get('tocEntry') is not None:
            if type(attrs['tocEntry']) is not bool:
                raise ValueError('Invalid table of contents entry.')
            clean['tocEntry'] = attrs['tocEntry']
        if kind == 'heading':
            clean['level'] = _integer(attrs.get('level', 1), 1, 6, 'heading level')
        if kind == 'orderedList':
            clean['start'] = _integer(attrs.get('start', 1), 1, 100_000, 'list start')
            if attrs.get('type') is not None:
                if not isinstance(attrs['type'], str) or attrs['type'] not in {'1', 'a', 'A', 'i', 'I'}:
                    raise ValueError('Invalid ordered list numbering style.')
                clean['type'] = attrs['type']
        if kind == 'taskItem':
            if type(attrs.get('checked', False)) is not bool:
                raise ValueError('Invalid checklist item.')
            clean['checked'] = attrs.get('checked', False)
        if kind == 'codeBlock' and attrs.get('language'):
            clean['language'] = clean_text(attrs['language'], 80, 'code language')
        if kind in {'tableCell', 'tableHeader'}:
            for key in ('colspan', 'rowspan'):
                clean[key] = _integer(attrs.get(key, 1), 1, 100, key)
            if attrs.get('colwidth') is not None:
                widths = attrs['colwidth']
                if not isinstance(widths, list) or len(widths) != clean['colspan']:
                    raise ValueError('Invalid table column widths.')
                clean['colwidth'] = [_integer(w, 0, 2400, 'column width') for w in widths]
            if attrs.get('backgroundColor'):
                clean['backgroundColor'] = color(attrs['backgroundColor'])
        if kind == 'shape':
            geometry = attrs.get('geometry', 'rect')
            if not isinstance(geometry, str) or geometry not in SHAPES:
                raise ValueError('Unsupported shape.')
            clean['geometry'] = geometry
            for key, default in [('fill', '#729ad0'), ('stroke', '#315887')]:
                shade = attrs.get(key, default)
                if not isinstance(shade, str) or not re.fullmatch(r'none|#[0-9a-fA-F]{6}', shade):
                    raise ValueError('Invalid shape colour.')
                clean[key] = shade.lower()
            clean['width'] = _integer(attrs.get('width', 160), 1, 2400, 'shape width')
            clean['height'] = _integer(attrs.get('height', 90), 1, 2400, 'shape height')
            clean['weight'] = _integer(attrs.get('weight', 2), 1, 16, 'shape line weight')
        if kind == 'image':
            image_data(attrs.get('src'))
            clean['src'] = attrs['src']
            for key in ('alt', 'title'):
                if attrs.get(key) is not None:
                    clean[key] = clean_text(attrs[key], 1000, 'image description')
            for key in ('width', 'height'):
                if attrs.get(key) is not None:
                    clean[key] = _integer(attrs[key], 1, 2400, 'image dimension')
        if clean:
            out['attrs'] = clean
        marks = value.get('marks', [])
        if not isinstance(marks, list) or len(marks) > 12 or (marks and kind not in {'text', 'hardBreak'}):
            raise ValueError('Invalid text formatting.')
        if marks:
            out['marks'] = [_mark(m) for m in marks]
            kinds = [mark['type'] for mark in out['marks']]
            if len(set(kinds)) != len(kinds) or 'code' in kinds and len(kinds) > 1:
                raise ValueError('Duplicate or conflicting text formatting.')
            out['marks'].sort(key=lambda mark: MARK_ORDER[mark['type']])
        children = value.get('content', [])
        if not isinstance(children, list):
            raise ValueError('Invalid document children.')
        allowed = (BLOCKS if kind in {'doc', 'blockquote', 'listItem', 'taskItem', 'tableCell', 'tableHeader'}
                   else INLINE if kind in {'paragraph', 'heading'}
                   else {'text'} if kind == 'codeBlock'
                   else {'listItem'} if kind in {'bulletList', 'orderedList'}
                   else {'taskItem'} if kind == 'taskList'
                   else {'tableRow'} if kind == 'table'
                   else {'tableCell', 'tableHeader'} if kind == 'tableRow' else set())
        if any(not isinstance(c, dict) or not isinstance(c.get('type'), str) or c['type'] not in allowed for c in children):
            raise ValueError('Invalid document structure.')
        if kind == 'table' and not 1 <= len(children) <= 100 or kind == 'tableRow' and len(children) > 100:
            raise ValueError('Tables support up to 100 rows and columns.')
        if kind in {'bulletList', 'orderedList', 'taskList'} and not children:
            raise ValueError('A list must contain at least one item.')
        if kind in {'blockquote', 'listItem', 'taskItem', 'tableCell', 'tableHeader'} and not children:
            children = [{'type': 'paragraph'}]
        if kind in {'listItem', 'taskItem'} and children[0].get('type') != 'paragraph':
            raise ValueError('A list item must begin with a paragraph.')
        if children or kind == 'doc':
            out['content'] = [node(child, depth + 1) for child in children]
        if kind == 'codeBlock' and any(child.get('marks') for child in out.get('content', [])):
            raise ValueError('Code blocks contain only unformatted text.')
        if kind == 'table':
            _validate_table_grid(out)
        return out

    cleaned = node(content)
    if not cleaned['content']:
        cleaned['content'] = [{'type': 'paragraph'}]
    return cleaned


def validate_page(page):
    if not isinstance(page, dict):
        raise ValueError('Invalid page layout.')
    result = {}
    for key, choices in {'size': {'a4', 'letter'}, 'orientation': {'portrait', 'landscape'},
                         'margin': {'normal', 'narrow', 'wide'}}.items():
        if not isinstance(page.get(key, DEFAULT_PAGE[key]), str) or page.get(key, DEFAULT_PAGE[key]) not in choices:
            raise ValueError('Invalid page ' + key + '.')
        result[key] = page.get(key, DEFAULT_PAGE[key])
    return result


def validate_comments(comments):
    if not isinstance(comments, list) or len(comments) > 500:
        raise ValueError('A document supports up to 500 comments.')
    result, seen = [], set()
    for item in comments:
        if not isinstance(item, dict):
            raise ValueError('Invalid comment.')
        key = item.get('id')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key) or key in seen:
            raise ValueError('Invalid or duplicate comment identifier.')
        seen.add(key)
        resolved = item.get('resolved', False)
        if type(resolved) is not bool:
            raise ValueError('Invalid comment state.')
        created = item.get('created_at', '')
        if isinstance(created, str):
            created = clean_text(created, 80, 'comment date')
        elif type(created) not in {int, float} or not 0 <= created < 10**14:
            raise ValueError('Invalid comment date.')
        result.append({'id': key, 'text': clean_text(item.get('text'), 4000, 'comment', empty=False),
                       'quote': clean_text(item.get('quote', ''), 2000, 'comment quote'),
                       'created_at': created, 'resolved': resolved})
    return result


def paragraph(text='', *, heading=None):
    return {'type': 'heading' if heading else 'paragraph',
            **({'attrs': {'level': heading}} if heading else {}),
            **({'content': [{'type': 'text', 'text': text}]} if text else {})}


def template_content(template):
    templates = {
        'blank': [paragraph()],
        'report': [paragraph('Report title', heading=1), paragraph('Subtitle · Author · Date'),
                   paragraph('Executive summary', heading=2), paragraph('Summarise the purpose and key findings.'),
                   paragraph('Background', heading=2), paragraph('Set out the context for this report.'),
                   paragraph('Findings', heading=2), paragraph('Present your evidence and analysis.'),
                   paragraph('Next steps', heading=2), paragraph('List recommendations and actions.')],
        'letter': [paragraph('Your name'), paragraph('Your address'), paragraph('Date'), paragraph(),
                   paragraph('Recipient name'), paragraph('Recipient address'), paragraph(),
                   paragraph('Dear recipient,'), paragraph('Write your letter here.'), paragraph(),
                   paragraph('Kind regards,'), paragraph('Your name')],
        'meeting': [paragraph('Meeting notes', heading=1), paragraph('Date · Attendees'),
                    paragraph('Agenda', heading=2), paragraph('Topics for discussion.'),
                    paragraph('Discussion', heading=2), paragraph('Capture the important points.'),
                    paragraph('Decisions', heading=2), paragraph('Record what was agreed.'),
                    paragraph('Action items', heading=2),
                    {'type': 'bulletList', 'content': [{'type': 'listItem', 'content': [paragraph('Owner · Action · Due date')]}]}],
    }
    if not isinstance(template, str) or template not in templates:
        raise ValueError('Choose a blank, report, letter or meeting template.')
    return {'type': 'doc', 'content': templates[template]}


def plain_text(content):
    notes = []
    counters = {'footnote': 0, 'endnote': 0}

    def visit(node):
        kind = node['type']
        if kind == 'text':
            return node['text']
        if kind in {'hardBreak', 'pageBreak', 'horizontalRule'}:
            return '\n'
        if kind == 'image':
            return '[' + node.get('attrs', {}).get('alt', 'Image') + ']\n'
        if kind == 'shape':
            return '[' + SHAPES[node['attrs']['geometry']] + ']\n'
        if kind == 'footnote':
            attrs = node['attrs']
            counters[attrs['kind']] += 1
            number = counters[attrs['kind']]
            notes.append((attrs['kind'], number, attrs['text']))
            return '[' + ('E' if attrs['kind'] == 'endnote' else '') + str(number) + ']'
        value = ''.join(visit(child) for child in node.get('content', []))
        return value + ('\n' if kind in {'paragraph', 'heading', 'codeBlock', 'tableRow'} else '')
    value = visit(content).rstrip()
    for kind, title in [('footnote', 'Footnotes'), ('endnote', 'Endnotes')]:
        selected = [(number, text) for note_kind, number, text in notes if note_kind == kind]
        if selected:
            value += '\n\n' + title + '\n' + '\n'.join(f'{number}. {text}' for number, text in selected)
    return value + '\n'


def document_html(document):
    """Standalone inert HTML exported only from validated nodes, never raw HTML."""
    def escape(value):
        return html.escape(str(value), quote=True)

    notes = []
    counters = {'footnote': 0, 'endnote': 0}

    def visit(node):
        kind, attrs = node['type'], node.get('attrs', {})
        if kind == 'footnote':
            counters[attrs['kind']] += 1
            number, key = counters[attrs['kind']], escape(attrs['id'])
            notes.append((attrs['kind'], number, key, attrs['text']))
            label = ('Endnote' if attrs['kind'] == 'endnote' else 'Footnote') + ' ' + str(number)
            return f'<sup id="note-ref-{key}"><a href="#note-{key}" role="doc-noteref" aria-label="{label}">{number}</a></sup>'
        if kind == 'text':
            value = escape(node['text'])
            for mark in node.get('marks', []):
                typ, ma = mark['type'], mark.get('attrs', {})
                tag = {'bold': 'strong', 'italic': 'em', 'underline': 'u', 'strike': 's',
                       'code': 'code', 'subscript': 'sub', 'superscript': 'sup'}.get(typ)
                if tag:
                    value = f'<{tag}>{value}</{tag}>'
                elif typ == 'link':
                    value = f'<a href="{escape(ma["href"])}" rel="noopener noreferrer">{value}</a>'
                elif typ in {'textStyle', 'highlight'}:
                    styles = ([f'background-color:{ma.get("color", "#fff59d")}'] if typ == 'highlight' else
                              [f'{css}:{ma[key]}' for key, css in {'color': 'color', 'fontSize': 'font-size', 'fontFamily': 'font-family'}.items() if key in ma])
                    value = '<span style="' + escape(';'.join(styles)) + '">' + value + '</span>'
            return value
        inner = ''.join(visit(c) for c in node.get('content', []))
        if kind == 'doc':
            return inner
        if kind == 'hardBreak':
            return '<br>'
        if kind == 'horizontalRule':
            return '<hr>'
        if kind == 'pageBreak':
            return '<div class="page-break"></div>'
        if kind == 'image':
            extra = ''.join(f' {key}="{escape(attrs[key])}"' for key in ('alt', 'title', 'width', 'height') if key in attrs)
            if attrs.get('width') and attrs.get('height'):
                extra += f' style="aspect-ratio:{attrs["width"]}/{attrs["height"]}"'
            return f'<img src="{escape(attrs["src"])}"{extra}>'
        if kind == 'shape':
            radius = {'rect': '0', 'roundRect': '12px', 'ellipse': '50%'}[attrs['geometry']]
            fill = 'transparent' if attrs['fill'] == 'none' else attrs['fill']
            stroke = 'transparent' if attrs['stroke'] == 'none' else attrs['stroke']
            style = (f'width:{attrs["width"]}px;height:{attrs["height"]}px;max-width:100%;box-sizing:border-box;'
                     f'border-radius:{radius};background-color:{fill};border:{attrs["weight"]}px solid {stroke};print-color-adjust:exact')
            return f'<div role="img" aria-label="{SHAPES[attrs["geometry"]]}" style="{style}"></div>'
        tag = {'paragraph': 'p', 'heading': 'h' + str(attrs.get('level', 1)), 'blockquote': 'blockquote',
               'bulletList': 'ul', 'orderedList': 'ol', 'taskList': 'ul', 'listItem': 'li', 'taskItem': 'li',
               'codeBlock': 'pre', 'table': 'table', 'tableRow': 'tr', 'tableCell': 'td', 'tableHeader': 'th'}[kind]
        extra, styles = '', []
        if kind in {'paragraph', 'heading'}:
            if attrs.get('anchorId'):
                extra += ' id="' + escape(attrs['anchorId']) + '"'
            if kind == 'paragraph' and attrs.get('tocEntry'):
                extra += ' data-folio-toc-entry="true"'
            for key, css in [('spaceBefore', 'margin-top'), ('spaceAfter', 'margin-bottom')]:
                if attrs.get(key) is not None:
                    styles.append(f'{css}:{attrs[key]}pt')
            if 'textAlign' in attrs:
                styles.append('text-align:' + attrs['textAlign'])
            if attrs.get('indent'):
                styles.append(f'margin-left:{attrs["indent"] * 0.5}in')
            if 'lineHeight' in attrs:
                styles.append('line-height:' + attrs['lineHeight'])
        if kind in {'tableCell', 'tableHeader'}:
            extra += f' colspan="{attrs.get("colspan", 1)}" rowspan="{attrs.get("rowspan", 1)}"'
            if attrs.get('backgroundColor'):
                styles.append('background-color:' + attrs['backgroundColor'])
        if kind == 'orderedList':
            extra += f' start="{attrs.get("start", 1)}"'
            if attrs.get('type'):
                extra += f' type="{escape(attrs["type"])}"'
        if kind == 'taskItem':
            inner = ('☑ ' if attrs.get('checked') else '☐ ') + inner
        if styles:
            extra += ' style="' + escape(';'.join(styles)) + '"'
        return f'<{tag}{extra}>{inner}</{tag}>'

    page = document['page']
    margin = {'normal': '25.4mm', 'narrow': '12.7mm', 'wide': '38.1mm'}[page['margin']]
    body = visit(document['content'])
    for kind, title in [('footnote', 'Footnotes'), ('endnote', 'Endnotes')]:
        selected = [(number, key, text) for note_kind, number, key, text in notes if note_kind == kind]
        if selected:
            items = ''.join(f'<li id="note-{key}"><a href="#note-ref-{key}" aria-label="Back to reference {number}">↩</a> '
                            + escape(text).replace('\n', '<br>') + '</li>' for number, key, text in selected)
            body += f'<section class="folio-notes" aria-label="{title}"><h2>{title}</h2><ol>{items}</ol></section>'
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'">'
            '<title>' + escape(document['title']) + '</title><style>'
            f'@page{{size:{page["size"]} {page["orientation"]};margin:{margin}}}'
            'body{max-width:850px;margin:40px auto;padding:0 24px;font:11pt/1.5 Georgia,serif;color:#182028}'
            'table{border-collapse:collapse;width:100%}td,th{border:1px solid #aab2bb;padding:8px}'
            'img{max-width:100%;height:auto}pre{white-space:pre-wrap}blockquote{border-left:3px solid #abb;padding-left:1em}'
            '.folio-notes{margin-top:2em;border-top:1px solid #abb;padding-top:.5em}.folio-notes li{white-space:pre-wrap}'
            '.page-break{break-after:page}@media print{body{margin:0;padding:0;max-width:none}}'
            '</style></head><body>' + body + '</body></html>')


class FolioStore:
    def __init__(self, directory):
        from .workspace_storage import workspace_database
        self.directory = Path(directory)
        self.path = workspace_database(self.directory, '.folio.sqlite3')

    @contextmanager
    def connect(self):
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise OSError('Unsafe document storage path.')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # The database lives in the already user-scoped owner directory. Do not
        # tighten a pre-existing library directory's permissions unexpectedly.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS folio_documents '
                       '(id TEXT PRIMARY KEY, title TEXT NOT NULL, data TEXT NOT NULL, '
                       'revision INTEGER NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, '
                       'byte_size INTEGER NOT NULL DEFAULT 0)')
            if 'byte_size' not in {row[1] for row in db.execute('PRAGMA table_info(folio_documents)')}:
                db.execute('ALTER TABLE folio_documents ADD COLUMN byte_size INTEGER NOT NULL DEFAULT 0')
                db.execute('UPDATE folio_documents SET byte_size=LENGTH(CAST(data AS BLOB))')
            db.execute('CREATE INDEX IF NOT EXISTS folio_updated ON folio_documents(updated DESC, id)')
            db.commit()
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _decode(row):
        if row is None:
            raise KeyError('Document not found.')
        key, title, data, revision, created, updated = row[:6]
        return {'id': key, 'title': title, **json.loads(data), 'revision': revision,
                'created_at': created, 'updated_at': updated}

    def get(self, key):
        key = identifier(key)
        with self.connect() as db:
            return self._decode(db.execute('SELECT * FROM folio_documents WHERE id=?', (key,)).fetchone())

    def snapshot(self):
        with self.connect() as db:
            rows = db.execute('SELECT id,title,revision,created,updated FROM folio_documents ORDER BY updated DESC,id').fetchall()
        return {'documents': [{'id': r[0], 'title': r[1], 'revision': r[2], 'created_at': r[3], 'updated_at': r[4]} for r in rows]}

    @staticmethod
    def _fields(payload):
        title = clean_text(payload.get('title', 'Untitled document'), 180, 'document title', empty=False).strip()
        data = {'content': validate_content(payload.get('content')),
                'page': validate_page(payload.get('page', DEFAULT_PAGE)),
                'comments': validate_comments(payload.get('comments', []))}
        encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        if len(encoded.encode('utf-8')) > MAX_DOCUMENT_BYTES:
            raise ValueError('A document must be no larger than 8 MB.')
        return title, encoded

    def change(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('Expected a document request.')
        action = payload.get('action')
        if not isinstance(action, str) or action not in {'create', 'save', 'duplicate', 'delete', 'import'}:
            raise ValueError('Unknown document action.')
        warnings = []
        if action == 'import':
            payload, warnings = self.import_payload(payload)
        if action == 'create':
            payload = {**payload, 'content': template_content(payload.get('template', 'blank')),
                       'page': dict(DEFAULT_PAGE), 'comments': []}
        if action in {'create', 'save', 'import'}:
            title, encoded = self._fields(payload)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old_size = 0
            if action in {'save', 'duplicate', 'delete'}:
                key = identifier(payload.get('id'))
                row = db.execute('SELECT * FROM folio_documents WHERE id=?', (key,)).fetchone()
                current = self._decode(row)
                revision = payload.get('revision')
                if type(revision) is not int or revision != current['revision']:
                    raise FolioConflict('This document changed in another tab. Reload it or save your work as a copy.')
                old_size = len(row[2].encode('utf-8'))
                if action == 'delete':
                    db.execute('DELETE FROM folio_documents WHERE id=? AND revision=?', (key, revision))
                    return {'id': key, 'deleted': True}
                if action == 'duplicate':
                    title, encoded = self._fields({**current, 'title': current['title'][:173] + ' (copy)'})
            creating = action in {'create', 'duplicate', 'import'}
            count, size = db.execute('SELECT COUNT(*),COALESCE(SUM(byte_size),0) FROM folio_documents').fetchone()
            if creating and count >= MAX_DOCUMENTS:
                raise ValueError('Your Folio library supports up to 500 documents.')
            if size - (0 if creating else old_size) + len(encoded.encode('utf-8')) > MAX_OWNER_BYTES:
                raise ValueError('Your Folio library exceeds its 100 MB storage limit.')
            now = time.time()
            if creating:
                key, revision, created = uuid.uuid4().hex, 1, now
                db.execute('INSERT INTO folio_documents VALUES (?,?,?,?,?,?,?)',
                           (key, title, encoded, revision, created, now, len(encoded.encode('utf-8'))))
            else:
                revision, created = current['revision'] + 1, current['created_at']
                db.execute('UPDATE folio_documents SET title=?,data=?,revision=?,updated=?,byte_size=? WHERE id=?',
                           (title, encoded, revision, now, len(encoded.encode('utf-8')), key))
            result = {'id': key, 'title': title, **json.loads(encoded), 'revision': revision,
                      'created_at': created, 'updated_at': now}
        if warnings:
            result['warnings'] = warnings
        return result

    @staticmethod
    def import_payload(payload):
        filename = payload.get('filename')
        if not isinstance(filename, str) or not 1 <= len(filename) <= 240:
            raise ValueError('Choose a DOCX or Folio JSON file.')
        encoded = payload.get('content_base64')
        if not isinstance(encoded, str) or len(encoded) > MAX_IMPORT_BYTES * 4 // 3 + 4:
            raise ValueError('Import files must be no larger than 8 MB.')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ValueError('Invalid file encoding.') from exc
        if len(raw) > MAX_IMPORT_BYTES:
            raise ValueError('Import files must be no larger than 8 MB.')
        suffix = Path(filename).suffix.lower()
        if suffix == '.json':
            try:
                data = json.loads(raw)
            except (ValueError, RecursionError, UnicodeError) as exc:
                raise ValueError('Choose a valid Folio JSON export.') from exc
            if not isinstance(data, dict):
                raise ValueError('Choose a valid Folio JSON export.')
            if data.get('type') == 'doc':
                data = {'content': data}
            return {'title': Path(filename).stem[:180] or 'Imported document', **data}, []
        if suffix == '.docx':
            from .folio_docx import import_docx
            data, warnings = import_docx(raw)
            data.setdefault('title', Path(filename).stem[:180] or 'Imported document')
            return data, warnings
        raise ValueError('Only DOCX and Folio JSON imports are supported.')

    def export(self, key, format, *, revision=_NO_EXPORT_REVISION):
        if revision is not _NO_EXPORT_REVISION and (type(revision) is not int or revision < 1):
            raise ValueError('Export revision must be a positive integer.')
        # Check the exact snapshot used by every serializer, without a second
        # read that could switch to another tab's newer revision during export.
        document = self.get(key)
        if revision is not _NO_EXPORT_REVISION and document['revision'] != revision:
            raise FolioConflict('This document changed before export. Reload it or save your edits as a copy, then try again.')
        name = re.sub(r'[^A-Za-z0-9._ -]', '', document['title']).strip(' .')[:80] or 'document'
        if format == 'json':
            data = json.dumps(document, ensure_ascii=False, indent=2).encode('utf-8')
            kind = 'application/json; charset=utf-8'
        elif format == 'txt':
            data, kind = plain_text(document['content']).encode('utf-8'), 'text/plain; charset=utf-8'
        elif format == 'html':
            data, kind = document_html(document).encode('utf-8'), 'text/html; charset=utf-8'
        elif format == 'docx':
            from .folio_docx import export_docx
            data, kind = export_docx(document), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        else:
            raise ValueError('Choose DOCX, HTML, plain text or JSON export.')
        return data, kind, name + '.' + format
