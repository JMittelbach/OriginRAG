import {closeHistory, NodeSelection} from './editor.mjs';

const shapes = {rect: 'Rectangle', roundRect: 'Rounded rectangle', ellipse: 'Ellipse'};
const element = (tag, text) => {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  return e;
};
const isObject = node => node && ['shape', 'image'].includes(node.type.name);

export function installFormatTools({editor, host, header, open}) {
  let current, position, kind, refresh;
  const popup = element('div');
  popup.className = 'folio-object-menu'; popup.setAttribute('popover', 'manual');
  popup.setAttribute('role', 'group'); popup.setAttribute('aria-label', 'Object actions');
  host.after(popup);
  let listeners;
  function dismiss() {
    listeners?.abort();
    if (popup.matches(':popover-open')) popup.hidePopover();
  }
  function menu(items, x, y) {
    dismiss();
    const previous = document.activeElement;
    listeners = new AbortController();
    const options = {capture: true, signal: listeners.signal};
    document.addEventListener('pointerdown', e => { if (!popup.contains(e.target)) dismiss(); }, options);
    window.addEventListener('resize', dismiss, options);
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); dismiss(); previous?.focus({preventScroll: true}); }
      if (e.key === 'Tab') setTimeout(() => { if (!popup.contains(document.activeElement)) dismiss(); }, 0);
      if (popup.contains(e.target) && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
        e.preventDefault();
        const b = [...popup.children], i = b.indexOf(document.activeElement);
        b[e.key === 'Home' ? 0 : e.key === 'End' ? b.length - 1 : (i + (e.key === 'ArrowDown' ? 1 : -1) + b.length) % b.length].focus();
      }
    }, options);
    popup.replaceChildren();
    for (const [label, command, geometry] of items) {
      const b = element('button', label); b.type = 'button';
      if (geometry) b.dataset.geometry = geometry;
      b.onclick = () => { dismiss(); command(); };
      popup.append(b);
    }
    popup.showPopover();
    popup.style.left = Math.max(4, Math.min(x, innerWidth - popup.offsetWidth - 4)) + 'px';
    popup.style.top = Math.max(4, Math.min(y, innerHeight - popup.offsetHeight - 4)) + 'px';
    popup.firstElementChild.focus({preventScroll: true});
  }
  function selectTarget(target) {
    const dom = target.closest?.('img, [data-folio-shape]');
    if (!dom || !editor.view.dom.contains(dom)) return false;
    const pos = editor.view.posAtDOM(dom.parentNode, Array.prototype.indexOf.call(dom.parentNode.childNodes, dom));
    if (!isObject(editor.state.doc.nodeAt(pos)) || editor.view.nodeDOM(pos) !== dom) return false;
    editor.commands.setNodeSelection(pos);
    return true;
  }
  const label = () => editor.state.selection.node?.type.name === 'shape' ? 'Format Shape' : 'Format Picture';
  const openPane = () => { open(); host.ownerDocument.getElementById('folioSidebar').querySelector('button')?.focus(); };
  host.addEventListener('dblclick', e => {
    if (selectTarget(e.target)) { e.preventDefault(); openPane(); }
  });
  host.addEventListener('contextmenu', e => {
    if (e.shiftKey || !selectTarget(e.target)) return;
    e.preventDefault();
    menu([[label(), openPane]], e.clientX, e.clientY);
  });
  host.addEventListener('keydown', e => {
    if ((e.key === 'ContextMenu' || e.shiftKey && e.key === 'F10') && isObject(editor.state.selection.node)) {
      e.preventDefault(); e.stopPropagation();
      const rect = editor.view.nodeDOM(editor.state.selection.from).getBoundingClientRect();
      menu([[label(), openPane]], rect.left + 10, rect.top + 10);
    }
  });
  host.addEventListener('load', e => {
    if (kind === 'image' && current === editor.state.selection.node && editor.view.nodeDOM(position) === e.target
        && !current.attrs.width && !e.target.ownerDocument.getElementById('folioSidebar')?.contains(document.activeElement)) refresh?.();
  }, true);
  function insert(geometry) {
    const doc = editor.state.doc;
    const shape = editor.schema.nodes.shape.create({geometry});
    const tr = editor.state.tr.replaceSelectionWith(shape);
    if (doc === tr.doc) return;
    let pos;
    tr.mapping.maps[0]?.forEach((_a, _b, from, to) => tr.doc.nodesBetween(from, to, (n, p) => {
      if (n === shape) pos = p;
    }));
    if (pos === undefined) return;
    editor.view.dispatch(closeHistory(tr.setSelection(NodeSelection.create(tr.doc, pos)).scrollIntoView()));
    editor.view.dispatch(closeHistory(editor.state.tr)); openPane();
  }
  function sync(force = false) {
    const {node, from} = editor.state.selection;
    if (!force && current === node && position === from) return;
    current = node; position = from;
    const nextKind = isObject(node) ? node.type.name : '';
    if (force || kind !== nextKind) {
      kind = nextKind;
      const pane = header(kind ? label() : 'Format Object');
      if (!kind) { pane.append(element('p', 'Select a shape or picture in the document.')); refresh = null; return; }
      refresh = build(pane, kind);
    }
    refresh?.();
  }
  function build(pane, type) {
    const controls = {};
    const message = element('p'); message.setAttribute('role', 'status');
    function commit(attrs) {
      const s = editor.state.selection;
      if (!pane.isConnected || !pane.contains(message) || !current || s.node !== current || s.from !== position || current.type.name !== type) return;
      message.textContent = '';
      if (Object.keys(attrs).every(k => current.attrs[k] === attrs[k])) return;
      const tr = editor.state.tr.setNodeMarkup(position, undefined, {...current.attrs, ...attrs});
      editor.view.dispatch(closeHistory(tr.setSelection(NodeSelection.create(tr.doc, position))));
      editor.view.dispatch(closeHistory(editor.state.tr));
    }
    function group(name) {
      const d = element('details'); d.className = 'folio-format-group'; d.open = true;
      d.append(element('summary', name)); pane.append(d); return d;
    }
    function field(parent, caption, key, input) {
      controls[key] = input;
      const row = element('label'); row.append(element('span', caption), input); parent.append(row);
      return input;
    }
    if (type === 'shape') {
      const colours = group('Fill & Line');
      for (const [key, title, fallback] of [['fill', 'Fill', '#729ad0'], ['stroke', 'Line', '#315887']]) {
        const colour = field(colours, title + ' colour', key, element('input')); colour.type = 'color';
        colour.onchange = () => commit({[key]: colour.value});
        const off = field(colours, 'No ' + title.toLowerCase(), key + 'Off', element('input')); off.type = 'checkbox';
        off.onchange = () => commit({[key]: off.checked ? 'none' : colour.value || fallback});
      }
      const w = field(colours, 'Line weight (px)', 'weight', element('input'));
      w.type = 'number'; w.min = '1'; w.max = '16'; w.required = true;
      w.onchange = () => { if (w.reportValidity()) commit({weight: w.valueAsNumber}); };
    }
    const size = group('Size & Properties');
    let ratio = 1;
    for (const key of ['width', 'height']) {
      const input = field(size, key === 'width' ? 'Width (px)' : 'Height (px)', key, element('input'));
      input.type = 'number'; input.min = '1'; input.max = '2400'; input.required = true;
      input.onchange = () => {
        if (!input.reportValidity()) return;
        const value = input.valueAsNumber, attrs = {[key]: value};
        if (controls.lock.checked) {
          const other = Math.max(1, Math.round(key === 'width' ? value / ratio : value * ratio));
          if (other > 2400) { message.textContent = 'The other dimension would exceed 2,400 px.'; refresh(); return; }
          attrs[key === 'width' ? 'height' : 'width'] = other;
        }
        commit(attrs);
      };
      input.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); input.blur(); } };
    }
    const lock = field(size, 'Lock aspect ratio', 'lock', element('input')); lock.type = 'checkbox'; lock.checked = type === 'image';
    if (type === 'image') {
      const alt = field(group('Accessibility'), 'Alt text', 'alt', element('textarea')); alt.maxLength = 1000;
      alt.onchange = () => commit({alt: alt.value.trim()});
    }
    pane.append(message);
    return () => {
      const a = current.attrs, dom = editor.view.nodeDOM(position);
      const naturalRatio = dom?.naturalWidth && dom?.naturalHeight ? dom.naturalWidth / dom.naturalHeight : 1;
      const width = a.width || (a.height ? Math.round(a.height * naturalRatio) : dom?.width);
      const height = a.height || Math.round(width / naturalRatio);
      ratio = width && height ? width / height : 1;
      controls.width.value = width || ''; controls.height.value = height || '';
      if (type === 'image') controls.alt.value = a.alt || '';
      else {
        for (const [key, fallback] of [['fill', '#729ad0'], ['stroke', '#315887']]) {
          const off = a[key] === 'none';
          controls[key + 'Off'].checked = off; controls[key].disabled = off;
          controls[key].value = off ? fallback : a[key];
        }
        controls.weight.value = a.weight;
      }
    };
  }
  return {sync, openPane, destroy: dismiss, shapes(anchor) {
    const rect = anchor.getBoundingClientRect();
    menu(Object.entries(shapes).map(([geometry, name]) => [name, () => insert(geometry), geometry]), rect.left, rect.bottom);
  }};
}
