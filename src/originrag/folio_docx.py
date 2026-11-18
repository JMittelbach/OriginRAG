"""Small, dependency-free DOCX interchange for Folio's common document features.

This is not a Word layout engine. Unsupported Word objects are not executed or
fetched. A checksummed custom part preserves Folio-only data on an unedited
Folio round-trip; after a Word edit, the actual Word body is imported instead.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
from pathlib import PurePosixPath
import posixpath
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile

from .folio import DEFAULT_PAGE, MAX_IMPORT_BYTES, MAX_NOTES, MAX_NOTE_TEXT, SHAPES, _image_dimensions, clean_text, image_data, safe_link, validate_content, validate_page, validate_comments

NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
    'pic': 'http://schemas.openxmlformats.org/drawingml/2006/picture',
    'wps': 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape',
    'mc': 'http://schemas.openxmlformats.org/markup-compatibility/2006',
}
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
LIST_FORMATS = {'1': 'decimal', 'a': 'lowerLetter', 'A': 'upperLetter', 'i': 'lowerRoman', 'I': 'upperRoman'}
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)


def tag(name):
    prefix, local = name.split(':', 1)
    return '{' + NS[prefix] + '}' + local


def sub(parent, name, attrs=None, text=None):
    child = ET.SubElement(parent, tag(name), {tag(k) if ':' in k else k: str(v) for k, v in (attrs or {}).items()})
    child.text = text
    return child


def xml(element):
    return ET.tostring(element, encoding='utf-8', xml_declaration=True)


def _ordered_properties(root):
    """Emit unique properties in schema order rather than editor mark order."""
    orders = {
        'rPr': 'rStyle rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint noProof snapToGrid vanish webHidden color spacing w kern position sz szCs highlight u effect bdr shd fitText vertAlign rtl cs em lang eastAsianLayout specVanish oMath rPrChange',
        'pPr': 'pStyle keepNext keepLines pageBreakBefore framePr widowControl numPr suppressLineNumbers pBdr shd tabs suppressAutoHyphens kinsoku wordWrap overflowPunct topLinePunct autoSpaceDE autoSpaceDN bidi adjustRightInd snapToGrid spacing ind contextualSpacing mirrorIndents suppressOverlap jc textDirection textAlignment textboxTightWrap outlineLvl divId cnfStyle rPr sectPr pPrChange',
        'tblPr': 'tblStyle tblpPr tblOverlap bidiVisual tblStyleRowBandSize tblStyleColBandSize tblW jc tblCellSpacing tblInd tblBorders shd tblLayout tblCellMar tblLook tblCaption tblDescription tblPrChange',
        'tcPr': 'cnfStyle tcW gridSpan hMerge vMerge tcBorders shd noWrap tcMar textDirection tcFitText vAlign hideMark headers cellIns cellDel cellMerge tcPrChange',
    }
    for name, order in orders.items():
        positions = {tag('w:' + value): index for index, value in enumerate(order.split())}
        for props in root.iter(tag('w:' + name)):
            unique = {child.tag: child for child in props}
            props[:] = sorted(unique.values(), key=lambda child: positions.get(child.tag, len(positions)))


def _hex_color(value):
    value = value or '000000'
    if value.startswith('#'):
        result = value[1:]
        if len(result) in {3, 4}:
            result = ''.join(c * 2 for c in result[:3])
        return result[:6].upper()
    if value.startswith('rgb('):
        return ''.join(f'{int(n):02X}' for n in re.findall(r'\d+', value))
    return {'black': '000000', 'white': 'FFFFFF', 'red': 'FF0000', 'blue': '0000FF', 'green': '008000',
            'yellow': 'FFFF00', 'orange': 'FFA500', 'purple': '800080', 'gray': '808080', 'grey': '808080',
            'navy': '000080', 'teal': '008080', 'maroon': '800000'}.get(value, '000000')


def _package_digest(files):
    digest = hashlib.sha256()
    for name in sorted(n for n in files if n != 'customXml/folio.json'):
        digest.update(name.encode('utf-8'))
        digest.update(b'\0')
        digest.update(hashlib.sha256(files[name]).digest())
    return digest.hexdigest()


def export_docx(document):
    root = ET.Element(tag('w:document'))
    body = sub(root, 'w:body')
    relationships = ET.Element('Relationships', xmlns=REL)
    media = {}
    numbering_instances = [(1, 1, 1, 0)]
    notes = {'footnote': [], 'endnote': []}
    bookmark_count = 0
    drawing_id = 0

    def relationship(kind, target, external=False):
        key = 'rId' + str(len(relationships) + 1)
        attrs = {'Id': key, 'Type': NS['r'] + '/' + kind, 'Target': target}
        if external:
            attrs['TargetMode'] = 'External'
        ET.SubElement(relationships, 'Relationship', attrs)
        return key

    relationship('styles', 'styles.xml')
    relationship('numbering', 'numbering.xml')

    def run(parent, node):
        if node['type'] == 'footnote':
            attrs = node['attrs']
            kind = attrs['kind']
            notes[kind].append(attrs['text'])
            element = sub(parent, 'w:r')
            sub(sub(element, 'w:rPr'), 'w:rStyle', {'w:val': kind.capitalize() + 'Reference'})
            sub(element, 'w:' + kind + 'Reference', {'w:id': len(notes[kind])})
            return
        if node['type'] == 'hardBreak':
            node = {'type': 'text', 'text': '\n', 'marks': node.get('marks', [])}
        marks = node.get('marks', [])
        link = next((m['attrs']['href'] for m in marks if m['type'] == 'link'), None)
        if link:
            if link.startswith('#'):
                anchor = link[1:]
                if re.fullmatch(r'folio-anchor-[0-9a-f]{32}', anchor):
                    anchor = 'folio_' + anchor.removeprefix('folio-anchor-')
                parent = sub(parent, 'w:hyperlink', {'w:anchor': anchor})
            else:
                parent = sub(parent, 'w:hyperlink', {'r:id': relationship('hyperlink', link, True)})
        element = sub(parent, 'w:r')
        props = sub(element, 'w:rPr')
        for mark in marks:
            kind, attrs = mark['type'], mark.get('attrs', {})
            simple = {'bold': 'w:b', 'italic': 'w:i', 'strike': 'w:strike'}
            if kind in simple:
                sub(props, simple[kind])
            elif kind == 'underline':
                sub(props, 'w:u', {'w:val': 'single'})
            elif kind in {'subscript', 'superscript'}:
                sub(props, 'w:vertAlign', {'w:val': kind})
            elif kind == 'code':
                sub(props, 'w:rFonts', {'w:ascii': 'Courier New', 'w:hAnsi': 'Courier New'})
            elif kind == 'textStyle':
                if attrs.get('fontFamily'):
                    font = attrs['fontFamily'].split(',')[0].strip(' \"\'')
                    sub(props, 'w:rFonts', {'w:ascii': font, 'w:hAnsi': font})
                if attrs.get('fontSize'):
                    size = attrs['fontSize']
                    points = float(re.search(r'[\d.]+', size)[0]) * (0.75 if size.endswith('px') else 1)
                    sub(props, 'w:sz', {'w:val': round(points * 2)})
                if attrs.get('color'):
                    sub(props, 'w:color', {'w:val': _hex_color(attrs['color'])})
            elif kind == 'highlight':
                sub(props, 'w:shd', {'w:val': 'clear', 'w:fill': _hex_color(attrs.get('color', '#ffff00'))})
            elif kind == 'link':
                if not any(m['type'] == 'textStyle' and m.get('attrs', {}).get('color') for m in marks):
                    sub(props, 'w:color', {'w:val': '175CD3'})
                sub(props, 'w:u', {'w:val': 'single'})
        # Literal newlines/tabs inside w:t are not Word's line/tab controls.
        for part in re.split(r'(\r\n|\r|\n|\t)', node['text']):
            if part == '\t':
                sub(element, 'w:tab')
            elif part in {'\r\n', '\r', '\n'}:
                sub(element, 'w:br')
            elif part:
                text = sub(element, 'w:t', text=part)
                text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')

    def graphic_data(parent, cx, cy, name, uri, **extra):
        nonlocal drawing_id
        drawing_id += 1
        inline = sub(sub(sub(parent, 'w:r'), 'w:drawing'), 'wp:inline', {'distT': 0, 'distB': 0, 'distL': 0, 'distR': 0})
        sub(inline, 'wp:extent', {'cx': cx, 'cy': cy})
        sub(inline, 'wp:docPr', {'id': drawing_id, 'name': name, **extra})
        return sub(sub(inline, 'a:graphic'), 'a:graphicData', {'uri': NS[uri]})

    def draw_shape(parent, attrs):
        cx, cy = attrs['width'] * 9525, attrs['height'] * 9525
        shape = sub(graphic_data(parent, cx, cy, SHAPES[attrs['geometry']], 'wps'), 'wps:wsp')
        sub(shape, 'wps:cNvSpPr')
        props = sub(shape, 'wps:spPr')
        transform = sub(props, 'a:xfrm')
        sub(transform, 'a:off', {'x': 0, 'y': 0})
        sub(transform, 'a:ext', {'cx': cx, 'cy': cy})
        sub(sub(props, 'a:prstGeom', {'prst': attrs['geometry']}), 'a:avLst')
        for key in ('fill', 'stroke'):
            target = props if key == 'fill' else sub(props, 'a:ln', {'w': attrs['weight'] * 9525})
            if attrs[key] == 'none':
                sub(target, 'a:noFill')
            else:
                sub(sub(target, 'a:solidFill'), 'a:srgbClr', {'val': attrs[key][1:].upper()})
        sub(shape, 'wps:bodyPr')

    def picture(parent, node):
        attrs = node['attrs']
        kind, raw = image_data(attrs['src'])
        name = f'image{len(media) + 1}.{kind}'
        media['word/media/' + name] = raw
        key = relationship('image', 'media/' + name)
        native_w, native_h = _image_dimensions(kind, raw)
        width, height = attrs.get('width'), attrs.get('height')
        if width and not height:
            height = max(1, round(width * native_h / native_w))
        elif height and not width:
            width = max(1, round(height * native_w / native_h))
        elif not width and not height:
            page = document['page']
            page_w, page_h = (11906, 16838) if page['size'] == 'a4' else (12240, 15840)
            if page['orientation'] == 'landscape':
                page_w, page_h = page_h, page_w
            margin = {'normal': 1440, 'narrow': 720, 'wide': 2160}[page['margin']]
            scale = min(1, (page_w - 2 * margin) / (15 * native_w), (page_h - 2 * margin) / (15 * native_h))
            width, height = max(1, round(native_w * scale)), max(1, round(native_h * scale))
        scale = min(1, 2400 / width, 2400 / height)
        width, height = max(1, round(width * scale)), max(1, round(height * scale))
        cx, cy = width * 9525, height * 9525
        graphic = graphic_data(parent, cx, cy, name, 'pic', descr=attrs.get('alt', ''), title=attrs.get('title', ''))
        pic = sub(graphic, 'pic:pic')
        nonvisual = sub(pic, 'pic:nvPicPr')
        sub(nonvisual, 'pic:cNvPr', {'id': len(media), 'name': name})
        sub(nonvisual, 'pic:cNvPicPr')
        fill = sub(pic, 'pic:blipFill')
        sub(fill, 'a:blip', {'r:embed': key})
        sub(sub(fill, 'a:stretch'), 'a:fillRect')
        shape = sub(pic, 'pic:spPr')
        transform = sub(shape, 'a:xfrm')
        sub(transform, 'a:off', {'x': 0, 'y': 0})
        sub(transform, 'a:ext', {'cx': cx, 'cy': cy})
        sub(sub(shape, 'a:prstGeom', {'prst': 'rect'}), 'a:avLst')

    def blocks(parent, nodes, level=0, numbering=None, quote=False):
        nonlocal bookmark_count
        for node_index, node in enumerate(nodes):
            kind, attrs = node['type'], node.get('attrs', {})
            children = node.get('content', [])
            if kind in {'paragraph', 'heading', 'codeBlock'}:
                p = sub(parent, 'w:p')
                props = sub(p, 'w:pPr')
                if kind == 'heading':
                    sub(props, 'w:pStyle', {'w:val': 'Heading' + str(attrs.get('level', 1))})
                elif kind == 'codeBlock':
                    sub(props, 'w:pStyle', {'w:val': 'Code'})
                elif attrs.get('tocEntry'):
                    sub(props, 'w:pStyle', {'w:val': 'FolioContents'})
                elif numbering is not None and node_index > 0:
                    sub(props, 'w:pStyle', {'w:val': 'FolioListContinuation'})
                if attrs.get('textAlign'):
                    sub(props, 'w:jc', {'w:val': 'both' if attrs['textAlign'] == 'justify' else attrs['textAlign']})
                if attrs.get('indent') or quote:
                    sub(props, 'w:ind', {'w:left': attrs.get('indent', 0) * 720 + (720 if quote else 0)})
                elif numbering is not None and node_index > 0:
                    sub(props, 'w:ind', {'w:left': (level + 1) * 720})
                spacing = {}
                if attrs.get('lineHeight'):
                    spacing.update({'w:line': round(float(attrs['lineHeight']) * 240), 'w:lineRule': 'auto'})
                for key, word_key in [('spaceBefore', 'w:before'), ('spaceAfter', 'w:after')]:
                    if attrs.get(key) is not None:
                        spacing[word_key] = round(attrs[key] * 20)
                if spacing:
                    sub(props, 'w:spacing', spacing)
                if numbering is not None and node_index == 0:
                    nums = sub(props, 'w:numPr')
                    sub(nums, 'w:ilvl', {'w:val': min(level, 8)})
                    sub(nums, 'w:numId', {'w:val': numbering})
                if attrs.get('anchorId'):
                    bookmark_count += 1
                    sub(p, 'w:bookmarkStart', {'w:id': bookmark_count, 'w:name': 'folio_' + attrs['anchorId'].removeprefix('folio-anchor-')})
                    sub(p, 'w:bookmarkEnd', {'w:id': bookmark_count})
                for child in children:
                    run(p, child)
            elif kind in {'bulletList', 'orderedList', 'taskList'}:
                list_level = min(8, level + (1 if numbering else 0))
                number_id = 1
                if kind == 'orderedList':
                    number_id = len(numbering_instances) + 1
                    abstract_id = 2 + list(LIST_FORMATS).index(attrs.get('type', '1'))
                    numbering_instances.append((number_id, abstract_id, attrs.get('start', 1), list_level))
                for item in children:
                    item_children = item.get('content', [])
                    if kind == 'taskList' and item_children:
                        prefix = {'type': 'text', 'text': '☑ ' if item.get('attrs', {}).get('checked') else '☐ '}
                        item_children = [{**item_children[0], 'content': [prefix] + item_children[0].get('content', [])}] + item_children[1:]
                    blocks(parent, item_children, level=list_level, numbering=number_id)
            elif kind == 'blockquote':
                blocks(parent, children, level=level, quote=True)
            elif kind == 'pageBreak':
                sub(sub(sub(parent, 'w:p'), 'w:r'), 'w:br', {'w:type': 'page'})
            elif kind == 'horizontalRule':
                border = sub(sub(sub(parent, 'w:p'), 'w:pPr'), 'w:pBdr')
                sub(border, 'w:bottom', {'w:val': 'single', 'w:sz': 6, 'w:color': 'A0AAB5'})
            elif kind == 'image':
                picture(sub(parent, 'w:p'), node)
            elif kind == 'shape':
                draw_shape(sub(parent, 'w:p'), attrs)
            elif kind == 'table':
                table = sub(parent, 'w:tbl')
                props = sub(table, 'w:tblPr')
                table_width = sub(props, 'w:tblW', {'w:w': 0, 'w:type': 'auto'})
                borders = sub(props, 'w:tblBorders')
                for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
                    sub(borders, 'w:' + side, {'w:val': 'single', 'w:sz': 4, 'w:color': 'BCC4CC'})
                grid = sub(table, 'w:tblGrid')
                cols, active, width_hints, cell_widths = 1, {}, {}, []
                for row in children:
                    tr = sub(table, 'w:tr')
                    if row.get('content') and all(c['type'] == 'tableHeader' for c in row['content']):
                        sub(sub(tr, 'w:trPr'), 'w:tblHeader')
                    column, next_active = 0, {}

                    def continuation():
                        nonlocal column
                        remaining, span = active.pop(column)
                        tc = sub(tr, 'w:tc')
                        cp = sub(tc, 'w:tcPr')
                        cell_widths.append((cp, column, span))
                        if span > 1:
                            sub(cp, 'w:gridSpan', {'w:val': span})
                        sub(cp, 'w:vMerge')
                        sub(tc, 'w:p')
                        if remaining > 1:
                            next_active[column] = (remaining - 1, span)
                        column += span

                    for cell in row.get('content', []):
                        while column in active:
                            continuation()
                        tc = sub(tr, 'w:tc')
                        cp = sub(tc, 'w:tcPr')
                        ca = cell.get('attrs', {})
                        span = ca.get('colspan', 1)
                        cell_widths.append((cp, column, span))
                        for offset, width in enumerate(ca.get('colwidth') or []):
                            if width:
                                width_hints.setdefault(column + offset, width * 15)
                        if span > 1:
                            sub(cp, 'w:gridSpan', {'w:val': span})
                        if ca.get('rowspan', 1) > 1:
                            sub(cp, 'w:vMerge', {'w:val': 'restart'})
                            next_active[column] = (ca['rowspan'] - 1, span)
                        if ca.get('backgroundColor') or cell['type'] == 'tableHeader':
                            sub(cp, 'w:shd', {'w:fill': _hex_color(ca.get('backgroundColor', '#edf0f4'))})
                        blocks(tc, cell.get('content', []))
                        if not len(tc) or tc[-1].tag != tag('w:p'):
                            sub(tc, 'w:p')
                        column += span
                    while active:
                        if column not in active:
                            raise ValueError('A merged table has an inconsistent column layout.')
                        continuation()
                    cols, active = max(cols, column), next_active
                if active:
                    raise ValueError('A merged table cell extends past the last row.')
                widths = [width_hints.get(i, max(240, 9000 // cols)) for i in range(cols)]
                for width in widths:
                    sub(grid, 'w:gridCol', {'w:w': width})
                for cell_props, column, span in cell_widths:
                    cell_props.insert(0, ET.Element(tag('w:tcW'), {
                        tag('w:w'): str(sum(widths[column:column + span])), tag('w:type'): 'dxa'}))
                if width_hints:
                    table_width.set(tag('w:w'), str(sum(widths)))
                    table_width.set(tag('w:type'), 'dxa')
                    sub(props, 'w:tblLayout', {'w:type': 'fixed'})

    blocks(body, document['content']['content'])
    comments_xml = None
    active_comments = [c for c in document.get('comments', []) if not c.get('resolved')]
    if active_comments:
        relationship('comments', 'comments.xml')
        comments_xml = ET.Element(tag('w:comments'))
        first = body.find('w:p', NS)
        if first is None:
            first = sub(body, 'w:p')
        for i, comment in enumerate(active_comments):
            sub(first, 'w:commentRangeStart', {'w:id': i})
            sub(first, 'w:commentRangeEnd', {'w:id': i})
            sub(sub(first, 'w:r'), 'w:commentReference', {'w:id': i})
            c = sub(comments_xml, 'w:comment', {'w:id': i, 'w:author': 'Folio'})
            text = (('Regarding: ' + comment['quote'] + '\n\n') if comment.get('quote') else '') + comment['text']
            sub(sub(sub(c, 'w:p'), 'w:r'), 'w:t', text=text)
    page = document['page']
    width, height = (11906, 16838) if page['size'] == 'a4' else (12240, 15840)
    if page['orientation'] == 'landscape':
        width, height = height, width
    section = sub(body, 'w:sectPr')
    for kind in ('footnote', 'endnote'):
        if notes[kind]:
            props = sub(section, 'w:' + kind + 'Pr')
            sub(props, 'w:pos', {'w:val': 'pageBottom' if kind == 'footnote' else 'docEnd'})
            sub(props, 'w:numFmt', {'w:val': 'decimal'})
    sub(section, 'w:pgSz', {'w:w': width, 'w:h': height, 'w:orient': page['orientation']})
    margin = {'normal': 1440, 'narrow': 720, 'wide': 2160}[page['margin']]
    sub(section, 'w:pgMar', {**{'w:' + side: margin for side in ('top', 'right', 'bottom', 'left')}, 'w:header': 720, 'w:footer': 720, 'w:gutter': 0})
    _ordered_properties(root)
    main_xml = xml(root)

    styles = ET.Element(tag('w:styles'))
    normal = sub(styles, 'w:style', {'w:type': 'paragraph', 'w:default': 1, 'w:styleId': 'Normal'})
    sub(normal, 'w:name', {'w:val': 'Normal'})
    rp = sub(normal, 'w:rPr')
    sub(rp, 'w:rFonts', {'w:ascii': 'Calibri', 'w:hAnsi': 'Calibri'})
    sub(rp, 'w:sz', {'w:val': 22})
    for level in range(1, 7):
        style = sub(styles, 'w:style', {'w:type': 'paragraph', 'w:styleId': 'Heading' + str(level)})
        sub(style, 'w:name', {'w:val': 'heading ' + str(level)})
        sub(style, 'w:basedOn', {'w:val': 'Normal'})
        props = sub(style, 'w:pPr')
        sub(props, 'w:keepNext')
        sub(props, 'w:outlineLvl', {'w:val': level - 1})
        sub(props, 'w:spacing', {'w:before': 240, 'w:after': 120})
        props = sub(style, 'w:rPr')
        sub(props, 'w:b')
        sub(props, 'w:sz', {'w:val': {1: 40, 2: 32, 3: 28, 4: 26, 5: 24, 6: 22}[level]})
    code = sub(styles, 'w:style', {'w:type': 'paragraph', 'w:styleId': 'Code'})
    sub(code, 'w:name', {'w:val': 'Code'})
    sub(sub(code, 'w:rPr'), 'w:rFonts', {'w:ascii': 'Courier New', 'w:hAnsi': 'Courier New'})
    continuation = sub(styles, 'w:style', {'w:type': 'paragraph', 'w:styleId': 'FolioListContinuation'})
    sub(continuation, 'w:name', {'w:val': 'Folio list continuation'})
    sub(continuation, 'w:basedOn', {'w:val': 'Normal'})
    contents_style = sub(styles, 'w:style', {'w:type': 'paragraph', 'w:styleId': 'FolioContents'})
    sub(contents_style, 'w:name', {'w:val': 'Folio contents entry'})
    sub(contents_style, 'w:basedOn', {'w:val': 'Normal'})
    note_parts = {}
    for kind, texts in notes.items():
        if not texts:
            continue
        relationship(kind + 's', kind + 's.xml')
        part = ET.Element(tag('w:' + kind + 's'))
        for key, separator in [(-1, 'separator'), (0, 'continuationSeparator')]:
            item = sub(part, 'w:' + kind, {'w:type': separator, 'w:id': key})
            sub(sub(sub(item, 'w:p'), 'w:r'), 'w:' + separator)
        for number, text in enumerate(texts, 1):
            item = sub(part, 'w:' + kind, {'w:id': number})
            p = sub(item, 'w:p')
            sub(sub(p, 'w:pPr'), 'w:pStyle', {'w:val': kind.capitalize() + 'Text'})
            ref = sub(p, 'w:r')
            sub(sub(ref, 'w:rPr'), 'w:rStyle', {'w:val': kind.capitalize() + 'Reference'})
            sub(ref, 'w:' + kind + 'Ref')
            run(p, {'type': 'text', 'text': ' ' + text})
        note_parts[kind + 's'] = xml(part)
        style = sub(styles, 'w:style', {'w:type': 'paragraph', 'w:styleId': kind.capitalize() + 'Text'})
        sub(style, 'w:name', {'w:val': kind.capitalize() + ' Text'})
        sub(style, 'w:basedOn', {'w:val': 'Normal'})
        sub(sub(style, 'w:rPr'), 'w:sz', {'w:val': 20})
        style = sub(styles, 'w:style', {'w:type': 'character', 'w:styleId': kind.capitalize() + 'Reference'})
        sub(style, 'w:name', {'w:val': kind.capitalize() + ' Reference'})
        sub(sub(style, 'w:rPr'), 'w:vertAlign', {'w:val': 'superscript'})

    numbering_xml = ET.Element(tag('w:numbering'))
    for number, kind in enumerate(['bullet', *LIST_FORMATS.values()], 1):
        abstract = sub(numbering_xml, 'w:abstractNum', {'w:abstractNumId': number})
        sub(abstract, 'w:multiLevelType', {'w:val': 'multilevel'})
        for level in range(9):
            item = sub(abstract, 'w:lvl', {'w:ilvl': level})
            sub(item, 'w:start', {'w:val': 1})
            sub(item, 'w:numFmt', {'w:val': kind})
            sub(item, 'w:lvlText', {'w:val': '•' if kind == 'bullet' else f'%{level + 1}.'})
            sub(sub(item, 'w:pPr'), 'w:ind', {'w:left': (level + 1) * 720, 'w:hanging': 360})
    for number_id, abstract_id, start, level in numbering_instances:
        instance = sub(numbering_xml, 'w:num', {'w:numId': number_id})
        sub(instance, 'w:abstractNumId', {'w:val': abstract_id})
        if abstract_id != 1:
            override = sub(instance, 'w:lvlOverride', {'w:ilvl': level})
            sub(override, 'w:startOverride', {'w:val': start})

    _ordered_properties(styles)
    _ordered_properties(numbering_xml)
    types = ET.Element('Types', xmlns=CT)
    for ext, content_type in {'rels': 'application/vnd.openxmlformats-package.relationships+xml', 'xml': 'application/xml',
                              'json': 'application/json', 'png': 'image/png', 'jpeg': 'image/jpeg', 'webp': 'image/webp'}.items():
        ET.SubElement(types, 'Default', Extension=ext, ContentType=content_type)
    overrides = {'document': 'document.main', 'styles': 'styles', 'numbering': 'numbering'}
    if comments_xml is not None:
        overrides['comments'] = 'comments'
    overrides.update({name: name for name in note_parts})
    for name, suffix in overrides.items():
        ET.SubElement(types, 'Override', PartName=f'/word/{name}.xml', ContentType=f'application/vnd.openxmlformats-officedocument.wordprocessingml.{suffix}+xml')
    package_rels = ET.Element('Relationships', xmlns=REL)
    ET.SubElement(package_rels, 'Relationship', Id='rId1', Type=NS['r'] + '/officeDocument', Target='word/document.xml')
    files = {'[Content_Types].xml': xml(types), '_rels/.rels': xml(package_rels),
             'word/document.xml': main_xml, 'word/styles.xml': xml(styles),
             'word/numbering.xml': xml(numbering_xml), 'word/_rels/document.xml.rels': xml(relationships), **media}
    if comments_xml is not None:
        files['word/comments.xml'] = xml(comments_xml)
    files.update({'word/' + name + '.xml': data for name, data in note_parts.items()})
    snapshot = {'version': 2, 'package_sha256': _package_digest(files),
                'document': {k: document[k] for k in ('title', 'content', 'page', 'comments')}}
    files['customXml/folio.json'] = json.dumps(snapshot, ensure_ascii=False).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


def _read_archive(raw):
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValueError('DOCX exceeds the 8 MB import limit.')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) > 512 or sum(m.file_size for m in members) > 24_000_000:
                raise ValueError('DOCX archive exceeds its expanded size or file count limit.')
            result, seen = {}, set()
            for member in members:
                name = member.filename
                path = PurePosixPath(name)
                if (name in seen or path.is_absolute() or '\\' in name or '\x00' in name
                        or any(p in {'.', '..', ''} for p in name.rstrip('/').split('/'))
                        or (member.external_attr >> 16) & 0o170000 == 0o120000
                        or member.flag_bits & 1 or member.file_size > 12_000_000):
                    raise ValueError('DOCX contains unsafe, duplicate or oversized archive entries.')
                seen.add(name)
                if member.file_size > 1_000_000 and member.file_size > max(1, member.compress_size) * 1000:
                    raise ValueError('DOCX archive compression ratio is too high.')
                if 'vbaproject' in name.lower() or name.lower().endswith(('.exe', '.dll', '.vbs', '.js')):
                    raise ValueError('Macro-enabled or executable document content is not supported.')
                if member.is_dir():
                    continue
                result[name] = archive.read(member)
            if '[Content_Types].xml' not in result or 'word/document.xml' not in result:
                raise ValueError('Choose a valid DOCX document.')
            if b'macroEnabled' in result['[Content_Types].xml']:
                raise ValueError('Macro-enabled documents are not supported.')
            return result
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError) as exc:
        raise ValueError('Choose a valid, unencrypted DOCX document.') from exc


def _parse_xml(raw):
    try:
        decoded = raw.decode('utf-8-sig')
    except UnicodeError as exc:
        raise ValueError('DOCX XML must use UTF-8 encoding.') from exc
    if '\x00' in decoded:
        raise ValueError('DOCX XML must use UTF-8 encoding.')
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', decoded, re.I):
        raise ValueError('Document XML entities and document types are not supported.')
    try:
        count, depth = 0, 0
        parser = ET.iterparse(io.StringIO(decoded), events=('start', 'end'))
        for event, _ in parser:
            if event == 'start':
                count, depth = count + 1, depth + 1
                if count > 100_000 or depth > 64:
                    raise ValueError('Document XML is too complex.')
            else:
                depth -= 1
    except (ET.ParseError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc) == 'Document XML is too complex.':
            raise
        raise ValueError('The DOCX contains invalid XML.') from exc
    return parser.root


def _shape_from_word(drawing, shape):
    geom = shape.find('wps:spPr/a:prstGeom', NS)
    extent = drawing.find('wp:inline/wp:extent', NS)
    if geom is None or geom.get('prst') not in SHAPES or extent is None or shape.find('wps:txbx', NS) is not None:
        return None
    def pixels(text, maximum, fallback):
        return min(maximum, max(1, round(int(text) / 9525))) if re.fullmatch(r'[0-9]{1,14}', text) else fallback
    attrs = {'geometry': geom.get('prst'), 'width': pixels(extent.get('cx', ''), 2400, 160),
             'height': pixels(extent.get('cy', ''), 2400, 90)}
    for key, path in [('fill', 'wps:spPr'), ('stroke', 'wps:spPr/a:ln')]:
        target = shape.find(path, NS)
        if target is None:
            continue
        shade = target.find('a:solidFill/a:srgbClr', NS)
        if target.find('a:noFill', NS) is not None:
            attrs[key] = 'none'
        elif shade is not None and re.fullmatch('[0-9a-fA-F]{6}', shade.get('val', '')):
            attrs[key] = '#' + shade.get('val').lower()
        if key == 'stroke':
            attrs['weight'] = pixels(target.get('w', ''), 16, 2)
    return attrs


def import_docx(raw):
    files = _read_archive(raw)
    root = _parse_xml(files['word/document.xml'])
    # Validate every XML part, including unused ones, before accepting the file.
    for name, data in files.items():
        if name.endswith(('.xml', '.rels')) and name != 'word/document.xml':
            _parse_xml(data)
    if 'customXml/folio.json' in files:
        try:
            snapshot = json.loads(files['customXml/folio.json'])
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError('Invalid Folio metadata in DOCX.') from exc
        if isinstance(snapshot, dict) and snapshot.get('version') == 2 and snapshot.get('package_sha256') == _package_digest(files):
            document = snapshot.get('document')
            if not isinstance(document, dict):
                raise ValueError('Invalid Folio document metadata.')
            return {**document, 'content': validate_content(document.get('content')),
                    'page': validate_page(document.get('page', DEFAULT_PAGE)),
                    'comments': validate_comments(document.get('comments', []))}, []
    body = root.find('w:body', NS)
    if body is None:
        raise ValueError('DOCX has no document body.')
    warnings = ['DOCX import preserves common text formatting, lists, tables and embedded images. '
                'Advanced Word layout, floating objects, headers, footers, fields and tracked changes may not be preserved.']

    def warn(message):
        if message not in warnings:
            warnings.append(message)

    for names, message in [
        (('ins', 'del', 'moveFrom', 'moveTo'), 'Tracked insertions are accepted and deletions omitted; revision history is not retained.'),
        (('fldSimple', 'fldChar', 'instrText'), 'Word fields retain their displayed text only; field calculations are not available.'),
        (('object', 'altChunk'), 'Embedded objects or externally linked document content are not imported.'),
    ]:
        if any(body.find('.//w:' + name, NS) is not None for name in names):
            warn(message)
    if any(not re.fullmatch(r'folio_[0-9a-f]{32}', bookmark.get(tag('w:name'), ''))
           for bookmark in body.findall('.//w:bookmarkStart', NS)):
        warn('Other Word bookmark destinations are not retained; internal bookmark links may need relinking.')
    if any(re.fullmatch(r'word/(?:header|footer)\d+\.xml', name) for name in files):
        warn('Word headers and footers are not imported.')
    if len(body.findall('.//w:sectPr', NS)) > 1:
        warn('Multiple Word sections are simplified to one page layout.')
    if body.find('.//{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath') is not None:
        warn('Word equations are not imported; keep the original DOCX to retain their mathematical layout.')
    relationships = {}
    if 'word/_rels/document.xml.rels' in files:
        relroot = _parse_xml(files['word/_rels/document.xml.rels'])
        relationships = {r.get('Id'): r.attrib for r in relroot}
    note_texts = {'footnote': {}, 'endnote': {}}
    imported_notes = 0

    def note_plain_text(element):
        if element.tag in {tag('w:del'), tag('w:moveFrom'), tag('w:instrText'), tag('w:rPr'), tag('w:pPr')}:
            return ''
        if element.tag == tag('w:t'):
            return element.text or ''
        if element.tag in {tag('w:br'), tag('w:cr')}:
            return '\n'
        if element.tag == tag('w:tab'):
            return '\t'
        text = ''.join(note_plain_text(child) for child in element)
        return text + ('\n' if element.tag == tag('w:p') else '')

    for kind in note_texts:
        relation = next((r for r in relationships.values() if r.get('Type') == NS['r'] + '/' + kind + 's'), None)
        if relation is None:
            continue
        target = relation.get('Target', '')
        path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join('word', target))
        if relation.get('TargetMode') == 'External' or '\\' in target or not path.startswith('word/') or path not in files:
            warn('Footnotes/endnotes with external or missing package relationships were not loaded.')
            continue
        note_root = _parse_xml(files[path])
        if note_root.tag != tag('w:' + kind + 's'):
            raise ValueError('Invalid DOCX note part.')
        for element in note_root.findall('w:' + kind, NS):
            key = element.get(tag('w:id'), '')
            if element.get(tag('w:type'), 'normal') != 'normal':
                continue
            if not re.fullmatch(r'\d{1,9}', key) or key in note_texts[kind]:
                raise ValueError('Invalid or duplicate DOCX note identifier.')
            imported_notes += 1
            if imported_notes > MAX_NOTES:
                raise ValueError('A document supports up to 500 footnotes and endnotes.')
            text = note_plain_text(element).rstrip('\n')
            # Word's usual reference run is followed by a single separator space.
            if element.find('.//w:' + kind + 'Ref', NS) is not None and text.startswith(' '):
                text = text[1:]
            note_texts[kind][key] = clean_text(text, MAX_NOTE_TEXT, 'note')
            if any(element.find('.//w:' + name, NS) is not None for name in
                   ('hyperlink', 'tbl', 'drawing', 'pict', 'fldChar', 'fldSimple', 'ins', 'del', 'object')) or any(
                child.tag != tag('w:rStyle') for props in element.findall('.//w:rPr', NS) for child in props
            ):
                warn('Footnotes/endnotes were imported as plain text; rich formatting, fields and embedded objects inside notes are not retained.')
    note_reference_count = 0
    numbering, abstracts = {}, {}
    if 'word/numbering.xml' in files:
        nr = _parse_xml(files['word/numbering.xml'])
        for abstract in nr.findall('w:abstractNum', NS):
            levels = {}
            for level in abstract.findall('w:lvl', NS):
                fmt = level.find('w:numFmt', NS)
                start = level.find('w:start', NS)
                levels[level.get(tag('w:ilvl'), '0')] = (fmt.get(tag('w:val'), 'bullet') if fmt is not None else 'bullet',
                                                       start.get(tag('w:val'), '1') if start is not None else '1')
            abstracts[abstract.get(tag('w:abstractNumId'))] = levels
        for number in nr.findall('w:num', NS):
            reference = number.find('w:abstractNumId', NS)
            if reference is not None:
                levels = dict(abstracts.get(reference.get(tag('w:val')), {}))
                for override in number.findall('w:lvlOverride', NS):
                    start = override.find('w:startOverride', NS)
                    level = override.get(tag('w:ilvl'), '0')
                    if start is not None:
                        levels[level] = (levels.get(level, ('decimal', '1'))[0], start.get(tag('w:val'), '1'))
                numbering[number.get(tag('w:numId'))] = levels

    def val(parent, name, default=None):
        element = parent.find(name, NS) if parent is not None else None
        return element.get(tag('w:val'), default) if element is not None else default

    styles_root = _parse_xml(files['word/styles.xml']) if 'word/styles.xml' in files else ET.Element(tag('w:styles'))
    styles = {style.get(tag('w:styleId')): style for style in styles_root.findall('w:style', NS)}
    default_style = next((key for key, style in styles.items() if style.get(tag('w:type')) == 'paragraph'
                          and style.get(tag('w:default')) in {'1', 'true', 'on'}), 'Normal')

    def style_chain(key):
        chain, seen = [], set()
        while key in styles and key not in seen and len(chain) < 32:
            seen.add(key)
            style = styles[key]
            chain.append(style)
            key = val(style, 'w:basedOn')
        if key in seen or len(chain) >= 32:
            warn('A cyclic or overly deep Word style inheritance chain was simplified.')
        return list(reversed(chain))

    def merge_props(elements, kind):
        result = ET.Element(tag('w:' + kind))

        def overlay(target, source):
            target.attrib.update(source.attrib)
            for child in source:
                existing = target.find(child.tag)
                if existing is None:
                    target.append(copy.deepcopy(child))
                else:
                    overlay(existing, child)

        for element in elements:
            if element is not None:
                overlay(result, element)
        return result

    def paragraph_properties(p):
        direct = p.find('w:pPr', NS)
        chain = style_chain(val(direct, 'w:pStyle', default_style))
        props = merge_props([styles_root.find('w:docDefaults/w:pPrDefault/w:pPr', NS),
                             *(s.find('w:pPr', NS) for s in chain), direct], 'pPr')
        run_props = merge_props([styles_root.find('w:docDefaults/w:rPrDefault/w:rPr', NS),
                                 *(s.find('w:rPr', NS) for s in chain)], 'rPr')
        return props, run_props

    def marks_for(run, base_props=None):
        direct = run.find('w:rPr', NS)
        props = merge_props([base_props, *(s.find('w:rPr', NS) for s in style_chain(val(direct, 'w:rStyle'))), direct], 'rPr')
        marks = []
        for name, kind in (('b', 'bold'), ('i', 'italic'), ('u', 'underline'), ('strike', 'strike')):
            element = props.find('w:' + name, NS)
            if element is not None and element.get(tag('w:val'), '1') not in {'0', 'false', 'off', 'none'}:
                marks.append({'type': kind})
        if val(props, 'w:vertAlign') in {'subscript', 'superscript'}:
            marks.append({'type': val(props, 'w:vertAlign')})
        styles = {}
        font = props.find('w:rFonts', NS)
        if font is not None:
            name = font.get(tag('w:ascii')) or font.get(tag('w:hAnsi'))
            if name and re.fullmatch(r'[A-Za-z0-9 ,\-\'"]{1,100}', name):
                styles['fontFamily'] = name
        size = val(props, 'w:sz')
        if size and size.isdigit() and 12 <= int(size) <= 192:
            styles['fontSize'] = str(int(size) / 2).rstrip('0').rstrip('.') + 'pt' if int(size) % 2 else str(int(size) // 2) + 'pt'
        shade = val(props, 'w:color')
        if shade and re.fullmatch(r'[0-9A-Fa-f]{6}', shade):
            styles['color'] = '#' + shade
        if styles:
            marks.append({'type': 'textStyle', 'attrs': styles})
        highlight = val(props, 'w:highlight')
        shading = props.find('w:shd', NS)
        if shading is not None and re.fullmatch(r'[0-9A-Fa-f]{6}', shading.get(tag('w:fill'), '')):
            marks.append({'type': 'highlight', 'attrs': {'color': '#' + shading.get(tag('w:fill'))}})
        elif highlight in {'yellow', 'green', 'blue', 'red', 'black', 'white'}:
            marks.append({'type': 'highlight', 'attrs': {'color': highlight}})
        return marks

    def inline_elements(parent, inherited=None, base_props=None):
        nonlocal note_reference_count
        # Keep a single ordered stream: separating images/breaks from text used
        # to move them to the paragraph end and collapse consecutive page breaks.
        result = []
        for child in parent:
            if child.tag == tag('w:hyperlink'):
                href = relationships.get(child.get(tag('r:id')), {}).get('Target')
                if child.get(tag('w:anchor')):
                    anchor = child.get(tag('w:anchor'))
                    if re.fullmatch(r'folio_[0-9a-f]{32}', anchor):
                        anchor = 'folio-anchor-' + anchor.removeprefix('folio_')
                    href = '#' + anchor
                try:
                    safe_link(href)
                except (TypeError, ValueError):
                    href = None
                result.extend(inline_elements(child, {'type': 'link', 'attrs': {'href': href}} if href else None, base_props))
            elif child.tag == tag('w:r'):
                marks = marks_for(child, base_props) + ([inherited] if inherited else [])
                for element in child:
                    if element.tag == tag('mc:AlternateContent'):
                        drawing = element.find('mc:Choice/w:drawing', NS)
                        if drawing is None:
                            warn('An unsupported alternate drawing was not imported.')
                            continue
                        element = drawing
                    if element.tag in {tag('w:footnoteReference'), tag('w:endnoteReference')}:
                        kind = 'footnote' if element.tag == tag('w:footnoteReference') else 'endnote'
                        key = element.get(tag('w:id'), '')
                        if key not in note_texts[kind]:
                            warn('Footnotes/endnotes with missing note bodies were not imported; keep the original DOCX.')
                            continue
                        note_reference_count += 1
                        if note_reference_count > MAX_NOTES:
                            raise ValueError('A document supports up to 500 footnotes and endnotes.')
                        result.append({'type': 'footnote', 'attrs': {'id': uuid.uuid4().hex, 'kind': kind, 'text': note_texts[kind][key]}})
                        if element.get(tag('w:customMarkFollows')) in {'1', 'true', 'on'}:
                            warn('Custom note reference marks were replaced by automatic numeric references.')
                    elif element.tag == tag('w:t') and element.text:
                        result.append({'type': 'text', 'text': element.text, **({'marks': marks} if marks else {})})
                    elif element.tag == tag('w:tab'):
                        result.append({'type': 'text', 'text': '\t', **({'marks': marks} if marks else {})})
                    elif element.tag == tag('w:br'):
                        if element.get(tag('w:type')) == 'page':
                            result.append({'type': 'pageBreak'})
                        else:
                            result.append({'type': 'hardBreak', **({'marks': marks} if marks else {})})
                            if element.get(tag('w:type')) == 'column':
                                warn('Word column breaks were converted to line breaks; multi-column layout is not retained.')
                    elif element.tag == tag('w:cr'):
                        result.append({'type': 'hardBreak', **({'marks': marks} if marks else {})})
                    elif element.tag == tag('w:drawing'):
                        shape = element.find('wp:inline/a:graphic/a:graphicData/wps:wsp', NS)
                        if shape is not None:
                            attrs = _shape_from_word(element, shape)
                            if attrs:
                                result.append({'type': 'shape', 'attrs': attrs})
                                warn('Basic shape geometry, size and solid colours were imported; other Word shape styling is not retained.')
                                continue
                        if not element.findall('.//a:blip', NS):
                            warn('A chart, shape or drawing without a supported embedded raster image was not imported.')
                        if element.find('.//a:srcRect', NS) is not None or any(
                            any(key in transform.attrib for key in ('rot', 'flipH', 'flipV'))
                            for transform in element.findall('.//a:xfrm', NS)
                        ):
                            warn('Figure cropping, rotation and flipping are not retained; the original embedded image is used.')
                        for blip in element.findall('.//a:blip', NS):
                            rel = relationships.get(blip.get(tag('r:embed')), {})
                            target = rel.get('Target', '')
                            if not target or rel.get('TargetMode') == 'External' or '\\' in target:
                                warn('External or missing image relationships were not loaded.')
                                continue
                            path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join('word', target))
                            if not path.startswith('word/media/') or path not in files:
                                warn('An embedded figure with a missing or unsupported package location was not imported.')
                                continue
                            raw_image = files[path]
                            suffix = PurePosixPath(path).suffix.lower()
                            mime = {'.png': 'png', '.jpg': 'jpeg', '.jpeg': 'jpeg', '.webp': 'webp'}.get(suffix)
                            if not mime:
                                warn('An unsupported embedded image format was not imported; use PNG, JPEG or WebP.')
                                continue
                            source = 'data:image/' + mime + ';base64,' + base64.b64encode(raw_image).decode()
                            image_data(source)
                            attrs = {'src': source, 'alt': ''}
                            extent = element.find('.//wp:extent', NS)
                            if extent is not None:
                                for key, dim in (('width', 'cx'), ('height', 'cy')):
                                    value = extent.get(dim, '')
                                    if value.isdigit():
                                        attrs[key] = max(1, min(2400, round(int(value) / 9525)))
                            desc = element.find('.//wp:docPr', NS)
                            if desc is not None:
                                attrs['alt'] = desc.get('descr', '')[:1000]
                                if desc.get('title'):
                                    attrs['title'] = desc.get('title')[:1000]
                            result.append({'type': 'image', 'attrs': attrs})
                            warn('Word figures were imported as block images in reading order; inline wrapping and floating positions are not retained.')
                    elif element.tag == tag('w:pict'):
                        warn('Legacy VML drawings are not imported; convert them to PNG or JPEG in Word first.')
            elif child.tag in {tag('w:ins'), tag('w:moveTo'), tag('w:smartTag'), tag('w:sdt'), tag('w:sdtContent'), tag('w:fldSimple')}:
                result.extend(inline_elements(child, inherited, base_props))
                if child.tag == tag('w:fldSimple'):
                    warn('Word fields retain their displayed text only; field calculations are not available.')
        return result

    def paragraph(p):
        props, run_props = paragraph_properties(p)
        style = val(props, 'w:pStyle', '')
        heading = re.fullmatch(r'Heading([1-6])', style, re.I)
        outline = val(props, 'w:outlineLvl')
        heading_level = int(heading[1]) if heading else int(outline) + 1 if outline in {'0', '1', '2', '3', '4', '5'} else None
        attrs = {}
        kind = 'heading' if heading_level else 'codeBlock' if style.lower() == 'code' else 'paragraph'
        if kind == 'paragraph' and style == 'FolioContents':
            attrs['tocEntry'] = True
        if heading_level:
            attrs['level'] = heading_level
        if kind != 'codeBlock':
            bookmark = next((b.get(tag('w:name')) for b in p.findall('w:bookmarkStart', NS)
                             if re.fullmatch(r'folio_[0-9a-f]{32}', b.get(tag('w:name'), ''))), None)
            if bookmark:
                attrs['anchorId'] = 'folio-anchor-' + bookmark.removeprefix('folio_')
        alignment = val(props, 'w:jc')
        if alignment in {'left', 'right', 'center', 'both', 'justify'} and kind != 'codeBlock':
            attrs['textAlign'] = 'justify' if alignment == 'both' else alignment
        if props is not None and kind != 'codeBlock':
            indent = props.find('w:ind', NS)
            spacing = props.find('w:spacing', NS)
            if indent is not None and indent.get(tag('w:left'), '').isdigit():
                attrs['indent'] = min(8, int(indent.get(tag('w:left'))) // 720)
            if spacing is not None and spacing.get(tag('w:lineRule'), 'auto') == 'auto' and spacing.get(tag('w:line'), '').isdigit():
                attrs['lineHeight'] = str(round(max(1, min(3, int(spacing.get(tag('w:line'))) / 240)), 2))
            if spacing is not None:
                for key, word_key in [('spaceBefore', 'before'), ('spaceAfter', 'after')]:
                    value = spacing.get(tag('w:' + word_key), '')
                    if value.isdigit():
                        attrs[key] = min(144, int(value) / 20)
        stream = inline_elements(p, base_props=run_props)
        nodes, inline = [], []

        def flush():
            if inline:
                content = list(inline)
                if kind == 'codeBlock':
                    if any(n['type'] == 'footnote' for n in content):
                        warn('Notes in a Word code-style paragraph were retained as inline plain text.')
                        content = [{'type': 'text', 'text': '[' + n['attrs']['text'] + ']'} if n['type'] == 'footnote' else n for n in content]
                    text = ''.join(n.get('text', '\n' if n['type'] == 'hardBreak' else '') for n in content)
                    content = [{'type': 'text', 'text': text}] if text else []
                nodes.append({'type': kind, **({'attrs': dict(attrs)} if attrs else {}), 'content': content})
                inline.clear()

        if props is not None and props.find('w:pageBreakBefore', NS) is not None and val(props, 'w:pageBreakBefore', '1') not in {'0', 'false', 'off'}:
            nodes.append({'type': 'pageBreak'})
        for item in stream:
            if item['type'] in {'image', 'shape', 'pageBreak'}:
                flush()
                nodes.append(item)
            else:
                inline.append(item)
        flush()
        if not stream:
            nodes.append({'type': kind, **({'attrs': attrs} if attrs else {})})
        list_info = None
        num = props.find('w:numPr', NS) if props is not None else None
        if num is not None and val(num, 'w:numId') not in {None, '0'}:
            numid, level = val(num, 'w:numId'), val(num, 'w:ilvl', '0')
            fmt, start = numbering.get(numid, {}).get(level, ('bullet', '1'))
            list_type = next((key for key, value in LIST_FORMATS.items() if value == fmt), '1')
            list_info = ('orderedList' if fmt != 'bullet' else 'bulletList', numid, level, start, list_type)
            if fmt not in {'bullet', *LIST_FORMATS.values()}:
                warn('An unsupported custom list numbering format was converted to decimal numbering.')
        elif style in {'ListBullet', 'ListBullet2', 'ListBullet3', 'ListNumber', 'ListNumber2', 'ListNumber3'}:
            list_info = ('bulletList' if 'Bullet' in style else 'orderedList', style, str(int(style[-1]) - 1) if style[-1:].isdigit() else '0', '1', '1')
        if props is not None and props.find('w:pBdr', NS) is not None and not stream:
            nodes = [{'type': 'horizontalRule'}]
        return nodes, list_info

    def read_table(table, depth):
        source_rows = table.findall('w:tr', NS)
        if not 1 <= len(source_rows) <= 100:
            raise ValueError('DOCX tables support 1–100 rows.')

        def bounded(value, low, high, label):
            if not isinstance(value, str) or not value.isdecimal() or not low <= int(value) <= high:
                raise ValueError('Unsupported or invalid DOCX table ' + label + '.')
            return int(value)

        grid = [max(0, min(2400, round(int(c.get(tag('w:w'), '0')) / 15)))
                if c.get(tag('w:w'), '0').isdecimal() else 0 for c in table.findall('w:tblGrid/w:gridCol', NS)]
        row_data, width = [], len(grid)
        for row in source_rows:
            props = row.find('w:trPr', NS)
            before = bounded(val(props, 'w:gridBefore', '0'), 0, 100, 'leading grid omission')
            after = bounded(val(props, 'w:gridAfter', '0'), 0, 100, 'trailing grid omission')
            cells = [(cell, bounded(val(cell.find('w:tcPr', NS), 'w:gridSpan', '1'), 1, 100, 'column span'))
                     for cell in row.findall('w:tc', NS)]
            width = max(width, before + sum(span for _, span in cells) + after)
            row_data.append((row, before, after, cells))
        if not 1 <= width <= 100:
            raise ValueError('DOCX tables support up to 100 grid columns.')
        grid += [0] * (width - len(grid))

        def blank(column):
            attrs = {'colspan': 1, 'rowspan': 1}
            if grid[column]:
                attrs['colwidth'] = [grid[column]]
            return {'type': 'tableCell', 'attrs': attrs, 'content': [{'type': 'paragraph'}]}

        def visible(nodes):
            return any(n['type'] in {'image', 'shape', 'table', 'pageBreak', 'horizontalRule', 'footnote'} or
                       n.get('text', '').strip() or visible(n.get('content', [])) for n in nodes)

        rows, active_merges = [], {}
        for row, before, after, source_cells in row_data:
            cells = [blank(i) for i in range(before)]
            column, next_merges = before, {}
            header = val(row.find('w:trPr', NS), 'w:tblHeader', None) not in {None, '0', 'false', 'off'}
            if row.find('w:trPr/w:tblHeader', NS) is not None and val(row.find('w:trPr', NS), 'w:tblHeader') is None:
                header = True
            for cell, span in source_cells:
                props = cell.find('w:tcPr', NS)
                attrs = {'colspan': span, 'rowspan': 1}
                widths = grid[column:column + span]
                preferred = props.find('w:tcW', NS) if props is not None else None
                if not any(widths) and preferred is not None and preferred.get(tag('w:type')) == 'dxa':
                    twips = preferred.get(tag('w:w'), '')
                    if twips.isdecimal() and int(twips):
                        widths = [max(1, min(2400, round(int(twips) / (15 * span))))] * span
                if any(widths):
                    attrs['colwidth'] = widths
                contents = read_blocks(cell, depth + 1) or [{'type': 'paragraph'}]
                merge = props.find('w:vMerge', NS) if props is not None else None
                if merge is not None and merge.get(tag('w:val'), 'continue') != 'restart':
                    origin = active_merges.get(column)
                    if origin is not None and origin['attrs']['colspan'] == span:
                        if origin['attrs']['rowspan'] >= 100:
                            raise ValueError('A DOCX merged table cell exceeds 100 rows.')
                        origin['attrs']['rowspan'] += 1
                        if visible(contents):
                            origin['content'].extend(contents)
                            warn('Text in vertical-merge continuation cells was retained in the merged cell.')
                        next_merges[column] = origin
                        column += span
                        continue
                    warn('An inconsistent vertical table merge was kept as separate cells to retain its contents.')
                shade = props.find('w:shd', NS) if props is not None else None
                if shade is not None and re.fullmatch(r'[0-9A-Fa-f]{6}', shade.get(tag('w:fill'), '')):
                    attrs['backgroundColor'] = '#' + shade.get(tag('w:fill'))
                entry = {'type': 'tableHeader' if header else 'tableCell', 'attrs': attrs, 'content': contents}
                cells.append(entry)
                if merge is not None:
                    next_merges[column] = entry
                column += span
            if before or after or column < width:
                warn('Omitted or ragged Word table grid positions were filled with empty editable cells.')
            cells.extend(blank(i) for i in range(column, width))
            rows.append({'type': 'tableRow', 'content': cells})
            active_merges = next_merges
        if any(table.find('.//w:' + name, NS) is not None for name in ('tcBorders', 'vAlign', 'textDirection', 'trHeight', 'tblpPr')):
            warn('Custom table borders, vertical text/alignment, row heights or floating table positioning are simplified.')
        return {'type': 'table', 'content': rows}

    number_counters = {}

    def read_blocks(parent, depth=0):
        if depth > 24:
            raise ValueError('DOCX nested tables are too complex.')
        result, list_stack = [], []
        for element in parent:
            if element.tag == tag('w:p'):
                nodes, info = paragraph(element)
                if info:
                    kind, numid, raw_level, start, list_type = info
                    level = min(8, int(raw_level)) if raw_level.isdecimal() else 0
                    key = (kind, numid, level)
                    while list_stack and list_stack[-1]['level'] > level:
                        list_stack.pop()
                    if list_stack and list_stack[-1]['level'] == level and list_stack[-1]['key'] != key:
                        list_stack.pop()
                    if not list_stack or list_stack[-1]['key'] != key:
                        entry = {'type': kind, 'content': []}
                        if kind == 'orderedList':
                            initial = min(100_000, max(1, int(start))) if start.isdecimal() else 1
                            entry['attrs'] = {'start': number_counters.get((numid, level), initial)}
                            if list_type != '1':
                                entry['attrs']['type'] = list_type
                        if list_stack:
                            list_stack[-1]['node']['content'][-1]['content'].append(entry)
                        else:
                            result.append(entry)
                        list_stack.append({'key': key, 'level': level, 'node': entry})
                    if not nodes or nodes[0]['type'] not in {'paragraph', 'heading', 'codeBlock'}:
                        nodes.insert(0, {'type': 'paragraph'})
                    else:
                        nodes[0] = {**nodes[0], 'type': 'paragraph'}
                    list_stack[-1]['node']['content'].append({'type': 'listItem', 'content': nodes})
                    if kind == 'orderedList':
                        current = number_counters.get((numid, level), list_stack[-1]['node']['attrs']['start'])
                        number_counters[(numid, level)] = min(100_000, current + 1)
                    for counter in list(number_counters):
                        if counter[0] == numid and counter[1] > level:
                            del number_counters[counter]
                else:
                    style = val(element.find('w:pPr', NS), 'w:pStyle', '')
                    if list_stack and (style == 'FolioListContinuation' or style.startswith('ListContinue')):
                        list_stack[-1]['node']['content'][-1]['content'].extend(nodes)
                    else:
                        result.extend(nodes)
                        list_stack.clear()
            elif element.tag == tag('w:tbl'):
                result.append(read_table(element, depth))
                list_stack.clear()
            elif element.tag in {tag('w:sdt'), tag('w:sdtContent')}:
                result.extend(read_blocks(element, depth + 1))
                list_stack.clear()
        return result

    page = dict(DEFAULT_PAGE)
    section = body.find('w:sectPr', NS)
    if section is not None:
        size = section.find('w:pgSz', NS)
        margin = section.find('w:pgMar', NS)
        if size is not None:
            width, height = size.get(tag('w:w'), ''), size.get(tag('w:h'), '')
            if width.isdigit() and height.isdigit():
                dimensions = sorted((int(width), int(height)))
                page['size'] = 'letter' if abs(dimensions[0] - 12240) < 100 and abs(dimensions[1] - 15840) < 100 else 'a4'
                page['orientation'] = 'landscape' if int(width) > int(height) else 'portrait'
        if margin is not None and margin.get(tag('w:left'), '').isdigit():
            left = int(margin.get(tag('w:left')))
            page['margin'] = min({'normal': 1440, 'narrow': 720, 'wide': 2160}, key=lambda k: abs({'normal': 1440, 'narrow': 720, 'wide': 2160}[k] - left))
    content = validate_content({'type': 'doc', 'content': read_blocks(body)})
    comments = []
    if 'word/comments.xml' in files:
        for i, comment in enumerate(_parse_xml(files['word/comments.xml']).findall('w:comment', NS)):
            text = '\n'.join(''.join(t.text or '' for t in p.findall('.//w:t', NS)) for p in comment.findall('w:p', NS)).strip()
            if text and i < 500:
                comments.append({'id': 'import-' + str(i), 'text': text[:4000], 'quote': '', 'resolved': False,
                                 'created_at': comment.get(tag('w:date'), '')[:80]})
    return {'content': content, 'page': page, 'comments': validate_comments(comments)}, warnings
