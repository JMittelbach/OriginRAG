import {Editor, Extension, Node, mergeAttributes} from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import {TextStyleKit, Color, FontFamily, FontSize} from '@tiptap/extension-text-style';
import TextAlign from '@tiptap/extension-text-align';
import Highlight from '@tiptap/extension-highlight';
import Image from '@tiptap/extension-image';
import {TaskList, TaskItem, OrderedList} from '@tiptap/extension-list';
import {TableKit, TableCell, TableHeader} from '@tiptap/extension-table';
import Subscript from '@tiptap/extension-subscript';
import Superscript from '@tiptap/extension-superscript';
import {Selection, NodeSelection, Plugin, PluginKey} from '@tiptap/pm/state';
import {Decoration, DecorationSet} from '@tiptap/pm/view';
import {Fragment, Slice} from '@tiptap/pm/model';
import {closeHistory} from '@tiptap/pm/history';
import {FolioSearch} from './search.js';
import {FolioShape} from './shape.js';
export {NodeSelection};
export {folioSearchState} from './search.js';
export {closeHistory};

export function safeLink(value) {
  if (typeof value !== 'string' || value.length > 2000 || /[\s\u0000-\u0020\u007f\\]/.test(value)) return false;
  if (value.startsWith('#')) return true;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? /^https?:\/\//i.test(value) && !!url.hostname : ['mailto:', 'tel:'].includes(url.protocol);
  } catch { return false; }
}

let colorContext;
function safeColor(value, resolve = true) {
  if (typeof value !== 'string') return null;
  const color = value.trim().toLowerCase();
  if (/^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/.test(color) ||
      /^(?:black|white|red|blue|green|yellow|orange|purple|gray|grey|transparent|navy|teal|maroon)$/.test(color)) return color;
  const rgb = /^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$/.exec(color);
  if (rgb && rgb.slice(1).every(channel => Number(channel) <= 255)) return color;
  const rgba = /^rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(0(?:\.\d+)?|1(?:\.0+)?|\.\d+)\s*\)$/.exec(color);
  if (rgba && rgba.slice(1, 4).every(channel => Number(channel) <= 255)) {
    return '#' + rgba.slice(1, 4).map(channel => Number(channel).toString(16).padStart(2, '0')).join('') +
      Math.round(Number(rgba[4]) * 255).toString(16).padStart(2, '0');
  }
  // Paste can use named colours, HSL or CSS Color 4. Resolve concrete colours
  // through the browser, then persist sRGB; never retain arbitrary CSS strings.
  if (resolve && color.length <= 200 && CSS.supports('color', color) &&
      !/\b(?:var|env|currentcolor|inherit|initial|unset|revert|revert-layer|light-dark)\b/.test(color)) {
    colorContext ||= document.createElement('canvas').getContext('2d', {willReadFrequently: true});
    colorContext.fillStyle = '#000000';
    colorContext.fillStyle = color;
    const normalized = safeColor(colorContext.fillStyle, false);
    if (normalized) return normalized;
    colorContext.clearRect(0, 0, 1, 1);
    colorContext.fillRect(0, 0, 1, 1);
    return '#' + [...colorContext.getImageData(0, 0, 1, 1).data].map(v => v.toString(16).padStart(2, '0')).join('');
  }
  return null;
}

function safeFontFamily(value) {
  return typeof value === 'string' && value.length <= 100 && /^[\p{L}\p{N}\p{M} ,.'"_-]+$/u.test(value) ? value : null;
}

function pastedFontSize(element, fallback = 16, depth = 0) {
  if (!element || depth > 32) return fallback + 'px';
  const value = element.style?.fontSize || '';
  const match = /^(\d+(?:\.\d+)?)(px|pt|em|rem|%)$/.exec(value);
  if (!match) return value ? null : pastedFontSize(element.parentElement, fallback, depth + 1);
  let size = Number(match[1]), unit = match[2];
  if (unit === 'em' || unit === '%') {
    const parent = pastedFontSize(element.parentElement, fallback, depth + 1);
    size *= parseFloat(parent || fallback) * (parent?.endsWith('pt') ? 4 / 3 : 1) / (unit === '%' ? 100 : 1);
    unit = 'px';
  } else if (unit === 'rem') {
    size *= parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
    unit = 'px';
  }
  return Math.round(Math.min(96, Math.max(6, size)) * 100) / 100 + unit;
}

const SafeColor = Color.extend({
  addGlobalAttributes() {
    return [{types: ['textStyle'], attributes: {color: {default: null,
      parseHTML: element => safeColor(element.style.color),
      renderHTML: attrs => safeColor(attrs.color) ? {style: `color: ${safeColor(attrs.color)}`} : {},
    }}}];
  },
});
const SafeFontFamily = FontFamily.extend({
  addGlobalAttributes() {
    return [{types: ['textStyle'], attributes: {fontFamily: {default: null,
      parseHTML: element => safeFontFamily(element.style.fontFamily),
      renderHTML: attrs => safeFontFamily(attrs.fontFamily) ? {style: `font-family: ${attrs.fontFamily}`} : {},
    }}}];
  },
});
const SafeFontSize = host => FontSize.extend({
  addGlobalAttributes() {
    return this.parent().map(extension => ({...extension, attributes: {...extension.attributes, fontSize: {
      ...extension.attributes.fontSize,
      parseHTML: element => element.style.fontSize ? pastedFontSize(element,
        parseFloat(getComputedStyle(host.querySelector('.tiptap') || host).fontSize) || 16) : null,
    }}}));
  },
});
const SafeHighlight = Highlight.extend({
  addAttributes() {
    return {color: {default: null,
      parseHTML: element => safeColor(element.getAttribute('data-color') || element.style.backgroundColor),
      renderHTML: attrs => safeColor(attrs.color) ? {'data-color': safeColor(attrs.color), style: `background-color: ${safeColor(attrs.color)}; color: inherit`} : {},
    }};
  },
  parseHTML() {
    return [...this.parent(), {tag: 'span[style]', getAttrs: element => safeColor(element.style.backgroundColor) ? null : false}];
  },
});
const SafeOrderedList = OrderedList.extend({
  addAttributes() {
    return {...this.parent(),
      start: {default: 1, parseHTML: element => Math.min(100000, Math.max(1, parseInt(element.getAttribute('start'), 10) || 1))},
      type: {default: null, parseHTML: element => ['1', 'a', 'A', 'i', 'I'].includes(element.getAttribute('type')) ? element.getAttribute('type') : null},
    };
  },
});

const spanValue = value => Math.min(100, Math.max(1, parseInt(value, 10) || 1));
const columnMaps = new WeakMap();
function pastedColumnWidth(value) {
  const match = /^(\d+(?:\.\d+)?)(px|pt)?$/.exec(String(value ?? '').trim());
  return match ? Math.min(2400, Math.round(Number(match[1]) * (match[2] === 'pt' ? 4 / 3 : 1))) : 0;
}
function pastedCellWidths(cell) {
  const colspan = spanValue(cell.getAttribute('colspan'));
  const explicit = cell.getAttribute('colwidth');
  if (explicit) {
    const widths = explicit.split(',').slice(0, colspan).map(pastedColumnWidth);
    while (widths.length < colspan) widths.push(0);
    return widths.some(Boolean) ? widths : null;
  }
  const table = cell.closest('table');
  if (table && !columnMaps.has(table)) {
    const positions = new WeakMap(), occupied = [];
    [...table.rows].slice(0, 100).forEach((row, rowIndex) => {
      let column = 0;
      [...row.cells].slice(0, 100).forEach(item => {
        while (column < 100 && (occupied[column] || 0) > rowIndex) column++;
        positions.set(item, column);
        const span = spanValue(item.getAttribute('colspan')), rows = spanValue(item.getAttribute('rowspan'));
        for (let index = column; index < Math.min(100, column + span); index++) occupied[index] = rowIndex + rows;
        column += span;
      });
    });
    columnMaps.set(table, positions);
  }
  const column = columnMaps.get(table)?.get(cell) ?? 0;
  const cols = [...(table?.querySelectorAll(':scope > colgroup > col') || [])].slice(0, 100).flatMap(col =>
    Array(spanValue(col.getAttribute('span'))).fill(pastedColumnWidth(col.getAttribute('width') || col.style.width))).slice(0, 100);
  const widths = cols.slice(column, column + colspan);
  if (widths.length === colspan && widths.some(Boolean)) return widths;
  const width = pastedColumnWidth(cell.getAttribute('width') || cell.style.width);
  return width ? Array(colspan).fill(Math.round(width / colspan)) : null;
}
const boundedCell = extension => extension.extend({
  addAttributes() {
    return {...this.parent(),
      colspan: {default: 1, parseHTML: element => spanValue(element.getAttribute('colspan'))},
      rowspan: {default: 1, parseHTML: element => spanValue(element.getAttribute('rowspan'))},
      colwidth: {default: null, parseHTML: pastedCellWidths},
    };
  },
});

const TableAppearance = Extension.create({
  name: 'tableAppearance',
  addGlobalAttributes() {
    return [{types: ['tableCell', 'tableHeader'], attributes: {
      backgroundColor: {default: null,
        parseHTML: element => safeColor(element.style.backgroundColor),
        renderHTML: attrs => {
          const color = safeColor(attrs.backgroundColor);
          return color ? {style: `background-color: ${color}`} : {};
        }},
    }}];
  },
});

function imageDimension(value) {
  const match = /^(\d{1,5}(?:\.\d{1,2})?)(?:px)?$/.exec(String(value ?? '').trim());
  return match && Number(match[1]) > 0 ? Math.min(2400, Math.max(1, Math.round(Number(match[1])))) : null;
}

const ParagraphLayout = Extension.create({
  name: 'paragraphLayout',
  addGlobalAttributes() {
    return [{types: ['paragraph', 'heading'], attributes: {
      anchorId: {default: null, keepOnSplit: false,
        parseHTML: element => /^folio-anchor-[0-9a-f]{32}$/.test(element.id) ? element.id : null,
        renderHTML: attrs => /^folio-anchor-[0-9a-f]{32}$/.test(attrs.anchorId || '') ? {id: attrs.anchorId} : {}},
      ...Object.fromEntries(['spaceBefore', 'spaceAfter'].map((key, index) => [key, {default: null,
        parseHTML: element => {
          const value = element.style[index ? 'marginBottom' : 'marginTop'];
          const match = /^(\d+(?:\.\d+)?)(pt|px)$/.exec(value);
          return match ? Math.min(144, Math.round(Number(match[1]) * (match[2] === 'px' ? 0.75 : 1) * 100) / 100) : null;
        },
        renderHTML: attrs => typeof attrs[key] === 'number' && attrs[key] >= 0 && attrs[key] <= 144
          ? {style: `${index ? 'margin-bottom' : 'margin-top'}: ${attrs[key]}pt`} : {},
      }])),
      indent: {default: 0,
        parseHTML: element => Math.min(8, Math.max(0, parseInt(element.getAttribute('data-indent'), 10) || 0)),
        renderHTML: attrs => attrs.indent ? {'data-indent': attrs.indent, style: `margin-left: ${attrs.indent * 0.5}in`} : {}},
      lineHeight: {default: null,
        parseHTML: element => { const v = Number(element.style.lineHeight); return v >= 1 && v <= 3 ? String(Math.round(v * 100) / 100) : null; },
        renderHTML: attrs => attrs.lineHeight ? {style: `line-height: ${attrs.lineHeight}`} : {}},
    }}, {types: ['paragraph'], attributes: {tocEntry: {default: false, keepOnSplit: false,
      parseHTML: element => element.getAttribute('data-folio-toc-entry') === 'true',
      renderHTML: attrs => attrs.tocEntry ? {'data-folio-toc-entry': 'true'} : {},
    }}}];
  },
  addCommands() {
    return {
      setParagraphSpacing: value => ({commands}) => {
        const paragraph = commands.updateAttributes('paragraph', {lineHeight: value});
        const heading = commands.updateAttributes('heading', {lineHeight: value});
        return paragraph || heading;
      },
      changeIndent: amount => ({state, dispatch}) => {
        const transaction = state.tr;
        state.doc.nodesBetween(state.selection.from, state.selection.to, (node, pos) => {
          if (['paragraph', 'heading'].includes(node.type.name)) transaction.setNodeMarkup(pos, undefined, {...node.attrs, indent: Math.min(8, Math.max(0, (node.attrs.indent || 0) + amount))});
        });
        if (dispatch && transaction.docChanged) dispatch(transaction);
        return true;
      },
    };
  },
});
const PageBreak = Node.create({
  name: 'pageBreak', group: 'block', atom: true, selectable: true,
  parseHTML() { return [{tag: 'div[data-page-break]'}]; },
  renderHTML() { return ['div', {'data-page-break': '', class: 'folio-page-break', contenteditable: 'false'}, 'Page break']; },
});

const noteKey = new PluginKey('folioNotes');
const noteId = () => [...crypto.getRandomValues(new Uint8Array(16))].map(value => value.toString(16).padStart(2, '0')).join('');
const validNoteText = value => typeof value === 'string' && value.length <= 4000 && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ud800-\udfff]/u.test(value);
const validNoteKind = value => value === 'footnote' || value === 'endnote';

export function collectFolioNotes(doc) {
  const notes = [], counters = {footnote: 0, endnote: 0};
  doc.descendants((node, pos) => {
    if (node.type.name !== 'footnote') return;
    const {id, text, kind} = node.attrs;
    notes.push({id, text, kind, pos, number: ++counters[kind]});
  });
  return notes;
}

function noteDecorations(doc) {
  return DecorationSet.create(doc, collectFolioNotes(doc).map(note => Decoration.node(note.pos, note.pos + 1,
    {'data-note-number': String(note.number), 'aria-label': `${note.kind === 'endnote' ? 'Endnote' : 'Footnote'} ${note.number}`})));
}

function normalizeNoteJSON(content) {
  if (!content || typeof content !== 'object') return content;
  const seen = new Set();
  const visit = node => {
    if (node.type === 'footnote') {
      const attrs = {...node.attrs};
      if (!/^[0-9a-f]{32}$/.test(attrs.id || '') || seen.has(attrs.id)) attrs.id = noteId();
      seen.add(attrs.id);
      return {...node, attrs};
    }
    return Array.isArray(node.content) ? {...node, content: node.content.map(visit)} : node;
  };
  return visit(content);
}

const FolioNote = Node.create({
  priority: 1000,
  name: 'footnote', group: 'inline', inline: true, atom: true, selectable: true, marks: '',
  addAttributes() { return {
    id: {default: null, rendered: false},
    text: {default: '', rendered: false},
    kind: {default: 'footnote', rendered: false},
  }; },
  parseHTML() { return [{tag: 'sup[data-folio-note-id]', priority: 100, getAttrs: element => {
    const text = element.getAttribute('data-note-text') || '', kind = element.getAttribute('data-note-kind') || 'footnote';
    return validNoteText(text) && validNoteKind(kind) ? {id: noteId(), text, kind} : false;
  }}]; },
  renderHTML({node}) {
    return ['sup', {'data-folio-note-id': node.attrs.id, 'data-note-kind': node.attrs.kind,
      'data-note-text': node.attrs.text, class: 'folio-note-reference', role: 'doc-noteref'}, '*'];
  },
  addNodeView() { return ({node, decorations}) => {
    const dom = document.createElement('sup');
    dom.className = 'folio-note-reference';
    dom.setAttribute('role', 'doc-noteref');
    dom.contentEditable = 'false';
    const update = (next, decorationList) => {
      if (next.type.name !== 'footnote') return false;
      const number = decorationList.find(item => item.type.attrs?.['data-note-number'])?.type.attrs['data-note-number'] || '*';
      dom.dataset.folioNoteId = next.attrs.id;
      dom.dataset.noteKind = next.attrs.kind;
      dom.dataset.noteText = next.attrs.text;
      dom.textContent = number;
      dom.title = next.attrs.text;
      dom.setAttribute('aria-label', `${next.attrs.kind === 'endnote' ? 'Endnote' : 'Footnote'} ${number}: ${next.attrs.text}`);
      return true;
    };
    update(node, decorations);
    return {dom, update};
  }; },
  addCommands() { return {
    insertFolioNote: ({text = '', kind = 'footnote'} = {}) => ({state, tr, commands}) => {
      if (!validNoteText(text) || !validNoteKind(kind) || collectFolioNotes(state.doc).length >= 500) return false;
      if (state.doc.resolve(state.selection.to).parent.type.name === 'codeBlock') return false;
      closeHistory(tr);
      return commands.insertContentAt(state.selection.to, {type: 'footnote', attrs: {id: noteId(), text, kind}});
    },
    updateFolioNote: (id, attrs = {}) => ({state, dispatch}) => {
      const note = collectFolioNotes(state.doc).find(item => item.id === id);
      if (!note) return false;
      const text = attrs.text ?? note.text, kind = attrs.kind ?? note.kind;
      if (!validNoteText(text) || !validNoteKind(kind)) return false;
      if (dispatch) dispatch(closeHistory(state.tr.setNodeMarkup(note.pos, undefined, {id, text, kind})));
      return true;
    },
    removeFolioNote: id => ({state, dispatch}) => {
      const note = collectFolioNotes(state.doc).find(item => item.id === id);
      if (!note) return false;
      if (dispatch) dispatch(closeHistory(state.tr.delete(note.pos, note.pos + 1)));
      return true;
    },
  }; },
  addProseMirrorPlugins() { return [new Plugin({
    key: noteKey,
    state: {init: (_, state) => noteDecorations(state.doc), apply: (tr, previous) => tr.docChanged ? noteDecorations(tr.doc) : previous},
    filterTransaction(tr) {
      if (!tr.docChanged) return true;
      const notes = collectFolioNotes(tr.doc);
      return notes.length <= 500 && notes.every(note => validNoteText(note.text) && validNoteKind(note.kind));
    },
    props: {
      decorations: state => noteKey.getState(state),
      transformPasted(slice) {
        const copy = fragment => {
          const nodes = [];
          fragment.forEach(node => {
            if (node.type.name === 'footnote') nodes.push(node.type.create({...node.attrs, id: noteId()}));
            else if (['paragraph', 'heading'].includes(node.type.name)) {
              const attrs = {...node.attrs, anchorId: null};
              if (node.type.name === 'paragraph') attrs.tocEntry = false;
              nodes.push(node.type.create(attrs, copy(node.content), node.marks));
            } else nodes.push(node.copy(copy(node.content)));
          });
          return Fragment.fromArray(nodes);
        };
        return new Slice(copy(slice.content), slice.openStart, slice.openEnd);
      },
    },
    appendTransaction(transactions, _, state) {
      if (!transactions.some(tr => tr.docChanged)) return null;
      const seen = new Set(), tr = state.tr;
      for (const note of collectFolioNotes(state.doc)) {
        if (!/^[0-9a-f]{32}$/.test(note.id || '') || seen.has(note.id)) {
          const id = noteId();
          tr.setNodeMarkup(note.pos, undefined, {id, text: note.text, kind: note.kind});
          seen.add(id);
        } else seen.add(note.id);
      }
      return tr.docChanged ? tr : null;
    },
  })]; },
});
const EmbeddedImage = Image.extend({
  addAttributes() {
    return {...this.parent?.(), ...Object.fromEntries(['width', 'height'].map(key => [key, {
      default: null,
      parseHTML: element => imageDimension(element.getAttribute(key) || element.style[key]),
      renderHTML: attrs => {
        const value = imageDimension(attrs[key]);
        return value ? {[key]: value} : {};
      },
    }]))};
  },
  parseHTML() {
    return [{tag: 'img[src]', getAttrs: element => /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(element.getAttribute('src') || '') ? null : false}];
  },
  renderHTML({node, HTMLAttributes}) {
    const width = imageDimension(node.attrs.width), height = imageDimension(node.attrs.height);
    return ['img', mergeAttributes(this.options.HTMLAttributes, HTMLAttributes,
      width && height ? {style: `aspect-ratio: ${width} / ${height}`} : {})];
  },
});

export function createFolioEditor(element, content, callbacks = {}) {
  const acceptImage = files => {
    if (files.length !== 1) { callbacks.onError?.('Insert one image at a time.'); return false; }
    if (!/^image\/(png|jpeg|webp)$/.test(files[0].type)) {
      callbacks.onError?.('Insert a PNG, JPEG or WebP image.'); return false;
    }
    return true;
  };
  return new Editor({
    element, content: normalizeNoteJSON(content), injectCSS: false,
    extensions: [
      StarterKit.configure({heading: {levels: [1, 2, 3, 4, 5, 6]}, orderedList: false, link: {
        openOnClick: false, defaultProtocol: 'https', isAllowedUri: safeLink,
        HTMLAttributes: {rel: 'noopener noreferrer', target: '_blank'},
      }}),
      TextStyleKit.configure({backgroundColor: false, lineHeight: false, color: false, fontFamily: false, fontSize: false}),
      SafeColor, SafeFontFamily, SafeFontSize(element), SafeOrderedList,
      TextAlign.configure({types: ['heading', 'paragraph']}),
      SafeHighlight.configure({multicolor: true}), EmbeddedImage.configure({allowBase64: true}),
      TableKit.configure({table: {resizable: true}, tableCell: false, tableHeader: false}),
      boundedCell(TableCell), boundedCell(TableHeader), TaskList, TaskItem.configure({nested: true}),
      Subscript, Superscript, ParagraphLayout, TableAppearance, PageBreak, FolioNote, FolioSearch, FolioShape,
    ],
    editorProps: {
      attributes: {role: 'textbox', 'aria-label': 'Document content', 'aria-multiline': 'true', spellcheck: 'true', tabindex: '0'},
      handleClickOn(view, _pos, node, nodePos, event, direct) {
        if (!direct || !['image', 'shape'].includes(node.type.name) || event.button !== 0) return false;
        view.dispatch(view.state.tr.setSelection(NodeSelection.create(view.state.doc, nodePos)));
        view.focus();
        return true;
      },
      handlePaste(_view, event) {
        const files = [...(event.clipboardData?.files || [])];
        if (!files.length) return false;
        if (acceptImage(files)) callbacks.onImage?.(files[0]);
        return true;
      },
      handleDrop(view, event) {
        const files = [...(event.dataTransfer?.files || [])];
        if (!files.length) return false;
        if (!acceptImage(files)) return true;
        const position = view.posAtCoords({left: event.clientX, top: event.clientY});
        if (!position) { callbacks.onError?.('Drop the image inside the document.'); return true; }
        view.dispatch(view.state.tr.setSelection(Selection.near(view.state.doc.resolve(position.pos))));
        callbacks.onImage?.(files[0]);
        return true;
      },
    },
    onUpdate: typeof callbacks.onUpdate === 'function' ? callbacks.onUpdate : () => {},
    onSelectionUpdate: typeof callbacks.onSelection === 'function' ? callbacks.onSelection : () => {},
    onTransaction: typeof callbacks.onTransaction === 'function' ? callbacks.onTransaction : () => {},
  });
}
