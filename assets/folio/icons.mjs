const NS = 'http://www.w3.org/2000/svg';
const paths = {
  paint: ['M5 3h14v7H5z', 'M8 10v4h5v7h4v-9h-5v-2'],
  growFont: ['M3 20 8 6l5 14M5 15h6', 'M18 4v8M14 8h8'],
  shrinkFont: ['M3 20 8 6l5 14M5 15h6', 'M15 8h7'],
  document: ['M5 3h10l4 4v14H5z', 'M15 3v5h4', 'M8 12h8M8 15h8M8 18h5'],
  new: ['M5 3h10l4 4v6M5 3v18h7', 'M15 3v5h4', 'M18 15v6M15 18h6'],
  save: ['M4 3h14l3 3v15H3V3z', 'M7 3v6h10V3M7 21v-8h10v8'],
  saveAs: ['M4 3h14l3 3v6M3 3v18h9M7 3v6h10V3M7 21v-8h5', 'm14 18 5-5 3 3-5 5-4 1z'],
  copy: ['M8 7h12v14H8z', 'M16 7V3H4v14h4'],
  import: ['M12 3v12m-4-4 4 4 4-4', 'M4 14v7h16v-7'],
  download: ['M12 3v12m-4-4 4 4 4-4', 'M4 19v2h16v-2'],
  print: ['M7 8V3h10v5M7 17H3V9h18v8h-4', 'M7 14h10v7H7z', 'M17 11h1'],
  home: ['m3 11 9-8 9 8', 'M5 9v12h5v-7h4v7h5V9'],
  undo: ['M8 4 3 9l5 5', 'M3 9h10a7 7 0 0 1 0 14'],
  redo: ['m16 4 5 5-5 5', 'M21 9H11a7 7 0 0 0 0 14'],
  table: ['M3 4h18v16H3z', 'M3 9h18M3 14h18M9 4v16M15 4v16'],
  image: ['M3 4h18v16H3z', 'm3 17 6-6 4 4 3-3 5 5', 'M17 8h.01'],
  shapes: ['M3 3h11v11H3z', 'M16 9a6 6 0 1 1-6 7'],
  size: ['M5 3H3v2M19 3h2v2M3 19v2h2M21 19v2h-2', 'M7 7h10v10H7z', 'm7 13 4-4m-4 0h4v4'],
  reset: ['M4 9a8 8 0 1 1-.1 6M4 3v6h6'],
  link: ['m10 14 4-4', 'm8 16-2 2a4 4 0 0 1-6-6l6-6a4 4 0 0 1 6 0', 'm16 8 2-2a4 4 0 0 1 6 6l-6 6a4 4 0 0 1-6 0'],
  calendar: ['M4 5h16v16H4z', 'M8 3v5M16 3v5M4 10h16M8 14h2M14 14h2M8 17h2'],
  pageBreak: ['M5 9V3h14v6M5 15v6h14v-6', 'M2 12h3m3 0h3m3 0h3m3 0h2'],
  rule: ['M3 12h18'],
  comment: ['M3 4h18v13H9l-5 4v-4H3z', 'M7 8h10M7 12h7'],
  addComment: ['M3 4h18v13H9l-5 4v-4H3z', 'M12 7v7M8.5 10.5h7'],
  find: ['M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14', 'm15 15 6 6'],
  outline: ['M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01'],
  closeSidebar: ['M3 4h18v16H3zM15 4v16', 'm6 9 5 6m0-6-5 6'],
  count: ['M5 3h14v18H5z', 'M8 8h8M8 12h5M8 16h8'],
  spelling: ['m3 13 4-9 4 9M4 10h6', 'm12 17 3 3 6-7'],
  focus: ['M3 9V3h6M15 3h6v6M3 15v6h6M15 21h6v-6', 'M8 12h8'],
  fit: ['M7 3h10v18H7z', 'm2 10 3 2-3 2m20-4-3 2 3 2'],
  alignLeft: ['M3 5h18M3 10h12M3 15h18M3 20h12'],
  alignCenter: ['M3 5h18M6 10h12M3 15h18M6 20h12'],
  alignRight: ['M3 5h18M9 10h12M3 15h18M9 20h12'],
  justify: ['M3 5h18M3 10h18M3 15h18M3 20h18'],
  bullets: ['M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01'],
  indent: ['M10 4h11M10 9h11M10 14h11M3 20h18', 'm3 6 4 4-4 4'],
  outdent: ['M10 4h11M10 9h11M10 14h11M3 20h18', 'm7 6-4 4 4 4'],
  erase: ['m4 16 10-12 7 6-9 11H8z', 'm9 10 7 6M14 21h8'],
  caption: ['M4 3h16v11H4z', 'm4 12 5-5 4 4 3-3 4 4', 'M4 18h16M7 21h10'],
  rowAbove: ['M3 8h18v13H3zM3 14h18M9 8v13M15 8v13', 'M12 1v5M9.5 3.5h5'],
  rowBelow: ['M3 3h18v13H3zM3 9h18M9 3v13M15 3v13', 'M12 18v5M9.5 20.5h5'],
  colBefore: ['M8 3h13v18H8zM14 3v18M8 9h13M8 15h13', 'M1 12h5M3.5 9.5v5'],
  colAfter: ['M3 3h13v18H3zM9 3v18M3 9h13M3 15h13', 'M18 12h5M20.5 9.5v5'],
  delete: ['M4 6h16M9 3h6M6 6l1 15h10l1-15', 'M10 10v7M14 10v7'],
  merge: ['M3 4h18v16H3z', 'm6 8 4 4-4 4m12-8-4 4 4 4'],
  split: ['M3 4h18v16H3zM12 4v16', 'm8 9-3 3 3 3m8-6 3 3-3 3'],
  select: ['M3 3h18v18H3z', 'm8 7 7 6-4 1-2 4z'],
  header: ['M3 4h18v16H3zM3 10h18M9 4v16M15 4v16'],
  shade: ['m4 13 9-9 7 7-9 9z', 'm8 3 8 8M4 22h17M21 13c0 0-2 3-2 4a2 2 0 0 0 4 0c0-1-2-4-2-4'],
  pen: ['m4 17 12-13 5 5L8 21H3z', 'm13 7 5 5M4 17l4 4'],
  highlighter: ['m5 16 10-13 7 6-10 12z', 'm5 16 7 5M5 16l-2 5h9'],
  toc: ['M5 3h14v18H5z', 'M8 7h8M8 11h5M8 15h5M15 11h1M15 15h1M8 18h8'],
  note: ['M5 3h14v18H5z', 'M8 7h8M8 11h8M8 15h4M8 18h8'],
  next: ['M4 4h10v16H4z', 'M8 8h3M8 12h3m7-4 4 4-4 4M13 12h9'],
  bookmark: ['M6 3h12v18l-6-5-6 5z'],
  crossref: ['M3 3h10v14H3zM11 7h10v14H11', 'M7 8h10m-3-3 3 3-3 3'],
  spacing: ['M9 5h12M9 12h12M9 19h12', 'M4 3v18M1 6l3-3 3 3M1 18l3 3 3-3'],
  design: ['M3 3h18v18H3zM3 9h18M9 3v18'],
  margins: ['M5 3h14v18H5z', 'M8 6h8v12H8z'],
  orientation: ['M3 9h14v12H3zM9 9V3h12v14h-4', 'm6 15 3-3 3 3M9 12v6'],
};
const letters = {
  paragraph: ['¶', {size: '20'}],
  bold: ['B', {weight: '750'}], italic: ['I', {style: 'italic'}],
  underline: ['U', {line: true}], strike: ['S', {strike: true}],
  subscript: ['x₂', {}], superscript: ['x²', {}], numbers: ['1≡', {}],
  symbol: ['Ω', {}], case: ['Aa', {size: '15'}], zoom: ['1:1', {size: '12'}],
};
const controls = {
  'Format painter': ['paint', 'Format painter'],
  'Increase font size': ['growFont', 'Grow font'], 'Decrease font size': ['shrinkFont', 'Shrink font'],
  'Show paragraph marks': ['paragraph', 'Paragraph marks'],
  'New document': ['new', 'New'], 'Import document': ['import', 'Open'],
  'Save document': ['save', 'Save'], 'Save as…': ['saveAs', 'Save as…'],
  'Save a copy': ['copy', 'Save a copy'], 'Download recovery JSON': ['save', 'Recovery copy'],
  'Download DOCX': ['download', 'Word (.docx)'], 'Download HTML': ['download', 'Web page'],
  'Download text': ['download', 'Plain text'], 'Download JSON': ['download', 'Folio (.json)'],
  'Print or save PDF': ['print', 'Print / PDF'], Print: ['print', 'Print'],
  Undo: ['undo', 'Undo'], Redo: ['redo', 'Redo'],
  Bold: ['bold', 'Bold'], Italic: ['italic', 'Italic'], Underline: ['underline', 'Underline'],
  Strikethrough: ['strike', 'Strikethrough'], Subscript: ['subscript', 'Subscript'], Superscript: ['superscript', 'Superscript'],
  'Clear formatting': ['erase', 'Clear formatting'],
  'Align left': ['alignLeft', 'Align left'], 'Align center': ['alignCenter', 'Align center'],
  'Align right': ['alignRight', 'Align right'], 'Align justify': ['justify', 'Justify'],
  'Bulleted list': ['bullets', 'Bulleted list'], 'Numbered list': ['numbers', 'Numbered list'],
  'Increase indent': ['indent', 'Increase indent'], 'Decrease indent': ['outdent', 'Decrease indent'],
  'Find and replace': ['find', 'Find / replace'], 'Document outline': ['outline', 'Navigation'],
  'Page break': ['pageBreak', 'Page break'], 'Horizontal line': ['rule', 'Horizontal line'],
  'Insert table': ['table', 'Table'], 'Insert image': ['image', 'Pictures'],
  'Insert link': ['link', 'Link'], 'Insert date': ['calendar', 'Date'],
  'Picture size and description': ['size', 'Size & description'],
  'Insert picture caption': ['caption', 'Caption'], 'Insert table caption': ['caption', 'Caption'],
  'Insert caption': ['caption', 'Insert caption'], 'Remove picture': ['delete', 'Remove picture'],
  'Reset picture size': ['reset', 'Original size'],
  'Fit picture to page': ['fit', 'Page width'], 'Half-page picture': ['size', 'Half page'],
  'Add row above': ['rowAbove', 'Insert above'], 'Add row below': ['rowBelow', 'Insert below'],
  'Add column before': ['colBefore', 'Insert left'], 'Add column after': ['colAfter', 'Insert right'],
  'Delete row': ['delete', 'Delete row'], 'Delete column': ['delete', 'Delete column'],
  'Delete table': ['delete', 'Delete table'], 'Merge cells': ['merge', 'Merge cells'],
  'Split cell': ['split', 'Split cell'], 'Select table': ['select', 'Select table'],
  'Select row': ['select', 'Select row'], 'Select column': ['select', 'Select column'],
  'Toggle header row': ['header', 'Header row'], 'Toggle header column': ['header', 'Header column'],
  'Clear cell shading': ['shade', 'Clear shading'],
  'Add comment': ['addComment', 'New comment'], Comments: ['comment', 'Comments'],
  'Word count': ['count', 'Word count'], 'Toggle spell check': ['spelling', 'Spelling'],
  'Close sidebar': ['closeSidebar', 'Close sidebar'], '100% zoom': ['zoom', '100%'],
  'Fit page width': ['fit', 'Page width'], 'Focus mode': ['focus', 'Focus'],
  Draw: ['pen', 'Draw'], 'Insert drawing': ['pen', 'Drawing'], Pen: ['pen', 'Pen'],
  Shapes: ['shapes', 'Shapes'], 'Format Shape': ['shapes', 'Format Shape'], 'Format Picture': ['image', 'Format Picture'],
  Highlighter: ['highlighter', 'Highlighter'], Eraser: ['erase', 'Eraser'],
  'Table of contents': ['toc', 'Table of contents'],
  'Update table of contents': ['reset', 'Update table'],
  Footnote: ['note', 'Footnote'], 'Insert footnote': ['note', 'Footnote'],
  Endnote: ['note', 'Endnote'], 'Insert endnote': ['note', 'Endnote'],
  'Edit note': ['pen', 'Edit note'], 'Next note': ['next', 'Next note'],
  'Style set': ['design', 'Style set'], 'Paragraph spacing': ['spacing', 'Paragraph spacing'],
  'Paper size': ['document', 'Size'], Orientation: ['orientation', 'Orientation'], Margins: ['margins', 'Margins'],
  'Change case': ['case', 'Change case'], 'Insert symbol': ['symbol', 'Symbol'],
  Bookmark: ['bookmark', 'Bookmark'], 'Insert bookmark': ['bookmark', 'Bookmark'],
  'Cross-reference': ['crossref', 'Cross-reference'], 'Insert cross-reference': ['crossref', 'Cross-reference'],
};
const compact = new Set(['Undo', 'Redo', 'Bold', 'Italic', 'Underline', 'Strikethrough', 'Subscript', 'Superscript', 'Clear formatting', 'Align left', 'Align center', 'Align right', 'Align justify', 'Bulleted list', 'Numbered list', 'Increase indent', 'Decrease indent', 'Change case', 'Increase font size', 'Decrease font size', 'Show paragraph marks']);
const accent = new Set(['table', 'image', 'calendar', 'comment', 'addComment', 'caption', 'header', 'bookmark', 'design', 'pen', 'highlighter']);

function svgElement(tag, attributes = {}) {
  const element = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  return element;
}

export function icon(name) {
  const key = controls[name]?.[0] || name;
  const svg = svgElement('svg', {viewBox: '0 0 24 24', width: '24', height: '24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.45', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false', class: 'folio-icon'});
  svg.dataset.icon = key;
  if (accent.has(key)) {
    const shape = key === 'image' || key === 'caption' ? {x: '4', y: '5', width: '16', height: '10'} : {x: '4', y: '4', width: '16', height: '5'};
    svg.append(svgElement('rect', {...shape, stroke: 'none', fill: 'var(--folio-icon-accent, #397abd)', opacity: '.45', rx: '.5'}));
  }
  if (letters[key]) {
    const [letter, style] = letters[key];
    const text = svgElement('text', {x: '12', y: '18', fill: 'currentColor', stroke: 'none', 'text-anchor': 'middle', 'font-family': 'Arial, sans-serif', 'font-size': style.size || '19', 'font-weight': style.weight || '500', 'font-style': style.style || 'normal'});
    text.textContent = letter;
    svg.append(text);
    if (style.line) svg.append(svgElement('path', {d: 'M5 21h14'}));
    if (style.strike) svg.append(svgElement('path', {d: 'M4 12h16'}));
  } else {
    for (const d of paths[key] || paths.document) svg.append(svgElement('path', {d}));
  }
  return svg;
}

export function decorateRibbon(container) {
  container.classList.add('folio-ribbon-detailed');
  for (const group of container.querySelectorAll('.folio-ribbon-group')) {
    group.dataset.group = group.querySelector('.folio-group-label')?.textContent.trim().toLowerCase().replace(/[^a-z]+/g, '-') || '';
  }
  for (const button of container.querySelectorAll('button.folio-tool')) {
    if (button.dataset.decorated === 'true') continue;
    const label = button.getAttribute('aria-label') || button.textContent.trim();
    const definition = controls[label] || ['document', label];
    const text = document.createElement('span');
    text.className = 'folio-tool-label';
    text.textContent = definition[1];
    button.replaceChildren(icon(definition[0]), text);
    const isCompact = compact.has(label);
    const isRow = !isCompact && (container.getAttribute('aria-labelledby') === 'folioTabTable' || button.closest('[data-group="editing"], [data-group="table-tools"]'));
    button.classList.add(isCompact ? 'folio-tool-compact' : isRow ? 'folio-tool-row' : 'folio-tool-large');
    button.dataset.decorated = 'true';
  }
  for (const style of container.querySelectorAll('.folio-style')) {
    if (style.dataset.decorated === 'true') continue;
    const label = document.createElement('span');
    label.className = 'folio-style-label';
    label.textContent = style.textContent;
    const sample = document.createElement('span');
    sample.className = 'folio-style-sample';
    sample.textContent = 'AaBbCc';
    sample.setAttribute('aria-hidden', 'true');
    style.replaceChildren(sample, label);
    style.dataset.decorated = 'true';
  }
  for (const field of container.querySelectorAll('[data-group="page-setup"] .folio-field')) {
    if (field.dataset.decorated === 'true') continue;
    const label = field.querySelector('select')?.getAttribute('aria-label');
    if (!label || !controls[label]) continue;
    field.prepend(icon(controls[label][0]));
    field.classList.add('folio-page-field');
    field.dataset.decorated = 'true';
  }
}
