import {addDialogClose} from '../workspace/dialog-close.mjs';

const WIDTH = 960;
const HEIGHT = 540;
const MAX_STROKES = 300;
const MAX_POINTS = 40000;
const MAX_STROKE_POINTS = 4000;
const MAX_IMAGE_BYTES = 2_000_000;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

export function drawingIcon(tool) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 40 48');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(svg.namespaceURI, 'path');
  path.setAttribute('fill', 'none');
  path.setAttribute('stroke', 'currentColor');
  path.setAttribute('stroke-width', '1.8');
  path.setAttribute('stroke-linejoin', 'round');
  path.setAttribute('d', {
    pen: 'M14 3h12v27l-6 13-6-13V3z M14 28h12 M18 37h4',
    highlighter: 'M11 3h18v26l-5 8v6H14v-6l-3-8V3z M11 28h18 M14 37h10',
    eraser: 'M21 8l14 12-19 22H8L2 36 21 8z M10 26l14 12 M16 42h20',
  }[tool]);
  svg.append(path);
  return svg;
}

export function openDrawing({onInsert, onClose, initialTool = 'pen', initialColour = '#24324a', initialWidth = 3} = {}) {
  if (typeof onInsert !== 'function') throw new TypeError('A drawing insertion handler is required.');
  const existing = document.querySelector('.folio-drawing-dialog');
  if (existing) {
    existing.focus();
    return existing;
  }
  if (!document.querySelector('link[data-folio-drawing-style]')) {
    const link = element('link');
    link.rel = 'stylesheet';
    link.href = new URL('./drawing.css', import.meta.url).href;
    link.dataset.folioDrawingStyle = '';
    document.head.append(link);
  }
  const previousFocus = document.activeElement;
  const dialog = element('dialog', undefined, 'folio-drawing-dialog');
  dialog.setAttribute('aria-labelledby', 'folioDrawingTitle');
  dialog.setAttribute('aria-describedby', 'folioDrawingHelp');
  addDialogClose(dialog, 'Close drawing', {onClose: close});
  const header = element('header', undefined, 'folio-drawing-header');
  const heading = element('h2', 'Draw');
  heading.id = 'folioDrawingTitle';
  const subtitle = element('p', 'Sketch with your mouse, trackpad, pen or touch.');
  header.append(heading, subtitle);
  const toolbar = element('div', undefined, 'folio-drawing-toolbar');
  toolbar.setAttribute('role', 'toolbar');
  toolbar.setAttribute('aria-label', 'Drawing tools');
  const tools = element('div', undefined, 'folio-drawing-pens');
  const history = element('div', undefined, 'folio-drawing-history');
  const colourGroup = element('label', undefined, 'folio-drawing-field');
  colourGroup.append(element('span', 'Ink colour'));
  const colour = element('input');
  colour.type = 'color';
  colour.value = /^#[0-9a-f]{6}$/i.test(initialColour) ? initialColour : '#24324a';
  colour.setAttribute('aria-label', 'Ink colour');
  colourGroup.append(colour);
  const widthGroup = element('label', undefined, 'folio-drawing-field');
  widthGroup.append(element('span', 'Thickness'));
  const width = element('input');
  width.type = 'range';
  width.min = '1';
  width.max = '40';
  width.value = Number.isFinite(initialWidth) ? Math.max(1, Math.min(40, initialWidth)) : 3;
  width.setAttribute('aria-label', 'Ink thickness');
  const widthValue = element('output', '3 px');
  widthGroup.append(width, widthValue);
  const backgroundGroup = element('label', undefined, 'folio-drawing-field');
  backgroundGroup.append(element('span', 'Background'));
  const background = element('select');
  background.setAttribute('aria-label', 'Drawing background');
  for (const [value, label] of [['transparent', 'Transparent'], ['white', 'White']]) {
    const option = element('option', label);
    option.value = value;
    background.append(option);
  }
  backgroundGroup.append(background);
  const canvasFrame = element('div', undefined, 'folio-drawing-canvas-wrap');
  const canvas = element('canvas');
  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  canvas.tabIndex = 0;
  canvas.setAttribute('aria-label', 'Drawing canvas');
  canvas.setAttribute('aria-describedby', 'folioDrawingHelp');
  canvasFrame.append(canvas);
  const context = canvas.getContext('2d');
  const committed = document.createElement('canvas');
  committed.width = WIDTH;
  committed.height = HEIGHT;
  const committedContext = committed.getContext('2d');
  if (!context || !committedContext) throw new Error('This browser cannot open the drawing canvas.');
  const help = element('p', 'Inserted as a PNG picture. Individual pen strokes cannot be edited after insertion.', 'folio-drawing-help');
  help.id = 'folioDrawingHelp';
  const details = element('label', undefined, 'folio-drawing-description');
  details.append(element('span', 'Picture description'));
  const description = element('input');
  description.type = 'text';
  description.maxLength = 400;
  description.placeholder = 'Describe your drawing for readers using assistive technology';
  description.setAttribute('aria-label', 'Drawing description');
  details.append(description);
  const error = element('p', '', 'folio-drawing-error');
  error.setAttribute('role', 'alert');
  const counter = element('output', 'Blank canvas', 'folio-drawing-counter');
  counter.setAttribute('aria-live', 'polite');
  const confirmation = element('div', undefined, 'folio-drawing-confirm');
  confirmation.hidden = true;
  confirmation.append(element('span', 'Clear this drawing? You can undo this afterwards.'));
  const footer = element('footer', undefined, 'folio-drawing-footer');
  const actions = element('div', undefined, 'folio-drawing-actions');
  let tool = ['pen', 'highlighter', 'eraser'].includes(initialTool) ? initialTool : 'pen', current = null, pointerId = null, frame = 0, busy = false, closed = false;
  let strokes = [], future = [], totalPoints = 0;
  const toolButtons = new Map();
  const abort = new AbortController();
  const listen = (target, type, fn) => target.addEventListener(type, fn, {signal: abort.signal});
  const button = (label, action, className = '') => {
    const node = element('button', label, className);
    node.type = 'button';
    listen(node, 'click', action);
    return node;
  };
  function update() {
    undo.disabled = busy || !strokes.length;
    redo.disabled = busy || !future.length;
    clear.disabled = busy || !strokes.length;
    insert.disabled = busy || !strokes.some((stroke) => stroke.kind !== 'eraser' && stroke.kind !== 'clear');
    counter.textContent = strokes.length ? `${strokes.length} ${strokes.length === 1 ? 'stroke' : 'strokes'}` : 'Blank canvas';
    canvas.style.cursor = tool === 'eraser' ? 'cell' : 'crosshair';
    canvas.style.setProperty('--folio-ink', colour.value);
    for (const [kind, node] of toolButtons) node.setAttribute('aria-pressed', String(kind === tool));
    widthValue.value = `${width.value} px`;
  }
  function paint(target, stroke) {
    if (stroke.kind === 'clear') {
      target.clearRect(0, 0, WIDTH, HEIGHT);
      return;
    }
    if (!stroke.points.length) return;
    target.save();
    target.globalCompositeOperation = stroke.kind === 'eraser' ? 'destination-out' : 'source-over';
    target.globalAlpha = stroke.kind === 'highlighter' ? 0.3 : 1;
    target.strokeStyle = stroke.colour;
    target.fillStyle = stroke.colour;
    target.lineWidth = stroke.width;
    target.lineCap = 'round';
    target.lineJoin = 'round';
    const [first, ...rest] = stroke.points;
    target.beginPath();
    if (!rest.length) {
      target.arc(first.x, first.y, stroke.width / 2, 0, Math.PI * 2);
      target.fill();
    } else {
      target.moveTo(first.x, first.y);
      for (const point of rest) target.lineTo(point.x, point.y);
      target.stroke();
    }
    target.restore();
  }
  function draw() {
    frame = 0;
    context.clearRect(0, 0, WIDTH, HEIGHT);
    context.drawImage(committed, 0, 0);
    if (current) paint(context, current);
  }
  function schedule() {
    if (!frame) frame = requestAnimationFrame(draw);
  }
  function rebuild() {
    totalPoints = strokes.reduce((total, stroke) => total + stroke.points.length, 0);
    committedContext.clearRect(0, 0, WIDTH, HEIGHT);
    for (const stroke of strokes) paint(committedContext, stroke);
    schedule();
    update();
  }
  function finishStroke() {
    if (current) {
      strokes.push(current);
      totalPoints += current.points.length;
      paint(committedContext, current);
      current = null;
      future = [];
    }
    if (pointerId !== null && canvas.hasPointerCapture(pointerId)) canvas.releasePointerCapture(pointerId);
    pointerId = null;
    schedule();
    update();
  }
  function close() {
    if (closed || busy) return;
    closed = true;
    abort.abort();
    cancelAnimationFrame(frame);
    dialog.close();
    dialog.remove();
    committed.width = committed.height = canvas.width = canvas.height = 0;
    strokes = future = [];
    if (previousFocus?.isConnected) previousFocus.focus({preventScroll: true});
    onClose?.();
  }
  for (const [kind, label] of [['pen', 'Pen'], ['highlighter', 'Highlighter'], ['eraser', 'Eraser']]) {
    const node = button(label, () => {
      finishStroke();
      tool = kind;
      width.value = kind === 'pen' ? '3' : kind === 'highlighter' ? '24' : '30';
      if (kind === 'highlighter') colour.value = '#ffd62e';
      if (kind === 'pen') colour.value = '#24324a';
      update();
    }, `folio-drawing-pen folio-drawing-${kind}`);
    node.prepend(drawingIcon(kind));
    toolButtons.set(kind, node);
    tools.append(node);
  }
  const undo = button('Undo stroke', () => {
    finishStroke();
    if (!strokes.length) return;
    future.push(strokes.pop());
    rebuild();
  });
  const redo = button('Redo stroke', () => {
    finishStroke();
    if (!future.length) return;
    strokes.push(future.pop());
    rebuild();
  });
  const clear = button('Clear canvas', () => {
    finishStroke();
    confirmation.hidden = false;
    keep.focus();
  });
  const keep = button('Keep drawing', () => { confirmation.hidden = true; clear.focus(); });
  const clearConfirmed = button('Clear drawing', () => {
    confirmation.hidden = true;
    if (strokes.length >= MAX_STROKES) {
      error.textContent = 'The drawing has reached its stroke limit. Undo a stroke first, or cancel and start a new drawing.';
      return;
    }
    strokes.push({kind: 'clear', points: []});
    future = [];
    rebuild();
    canvas.focus();
  });
  confirmation.append(keep, clearConfirmed);
  history.append(undo, redo, clear);
  toolbar.append(tools, colourGroup, widthGroup, backgroundGroup, history);
  const cancel = button('Cancel drawing', close);
  const insert = button('Insert drawing', async () => {
    if (busy) return;
    finishStroke();
    error.textContent = '';
    const pixels = committedContext.getImageData(0, 0, WIDTH, HEIGHT).data;
    let hasInk = false;
    for (let i = 3; i < pixels.length; i += 4) {
      if (pixels[i]) { hasInk = true; break; }
    }
    if (!hasInk) {
      error.textContent = 'The canvas is empty. Add a pen stroke before inserting a drawing.';
      return;
    }
    const output = document.createElement('canvas');
    output.width = WIDTH;
    output.height = HEIGHT;
    const outputContext = output.getContext('2d');
    if (background.value === 'white') {
      outputContext.fillStyle = '#ffffff';
      outputContext.fillRect(0, 0, WIDTH, HEIGHT);
    }
    outputContext.drawImage(committed, 0, 0);
    const src = output.toDataURL('image/png');
    output.width = output.height = 0;
    const imageBytes = (src.length - src.indexOf(',') - 1) * 3 / 4 - (src.endsWith('==') ? 2 : src.endsWith('=') ? 1 : 0);
    if (imageBytes > MAX_IMAGE_BYTES) {
      error.textContent = 'This drawing is too large to insert. Undo some strokes and try again.';
      return;
    }
    busy = true;
    dialog.setAttribute('aria-busy', 'true');
    for (const node of dialog.querySelectorAll('button,input,select')) node.disabled = true;
    insert.textContent = 'Inserting…';
    try {
      await onInsert({src, alt: description.value.trim() || 'Hand-drawn sketch', width: WIDTH, height: HEIGHT});
      busy = false;
      close();
    } catch (failure) {
      error.textContent = `${failure?.message || 'The drawing could not be inserted.'} Your drawing is still here. You can try again.`;
      busy = false;
      dialog.removeAttribute('aria-busy');
      for (const node of dialog.querySelectorAll('button,input,select')) node.disabled = false;
      insert.textContent = 'Insert drawing';
      update();
    }
  }, 'folio-drawing-primary');
  actions.append(cancel, insert);
  footer.append(counter, actions);
  dialog.append(header, toolbar, confirmation, canvasFrame, help, details, error, footer);
  function point(event) {
    const rect = canvas.getBoundingClientRect();
    return {x: Math.max(0, Math.min(WIDTH, (event.clientX - rect.left) * WIDTH / rect.width)),
      y: Math.max(0, Math.min(HEIGHT, (event.clientY - rect.top) * HEIGHT / rect.height))};
  }
  listen(canvas, 'pointerdown', (event) => {
    if (busy || current || event.button !== 0 || !event.isPrimary) return;
    event.preventDefault();
    error.textContent = '';
    if (strokes.length >= MAX_STROKES || totalPoints >= MAX_POINTS) {
      error.textContent = 'This drawing has reached its size limit. Undo a stroke or insert it and start another drawing.';
      return;
    }
    canvas.focus({preventScroll: true});
    pointerId = event.pointerId;
    canvas.setPointerCapture(pointerId);
    current = {kind: tool, colour: colour.value, width: Number(width.value), points: [point(event)]};
    schedule();
  });
  listen(canvas, 'pointermove', (event) => {
    if (!current || event.pointerId !== pointerId || busy) return;
    event.preventDefault();
    const events = event.getCoalescedEvents?.() || [event];
    for (const sample of events.length ? events : [event]) {
      const next = point(sample), last = current.points.at(-1);
      if (Math.hypot(next.x - last.x, next.y - last.y) < 0.8) continue;
      if (current.points.length >= MAX_STROKE_POINTS || totalPoints + current.points.length >= MAX_POINTS) {
        error.textContent = 'This stroke reached the drawing limit. Lift the pointer before continuing.';
        break;
      }
      current.points.push(next);
    }
    schedule();
  });
  const pointerEnd = (event) => { if (event.pointerId === pointerId) finishStroke(); };
  listen(canvas, 'pointerup', pointerEnd);
  listen(canvas, 'pointercancel', pointerEnd);
  listen(canvas, 'lostpointercapture', pointerEnd);
  listen(colour, 'input', update);
  listen(width, 'input', update);
  listen(background, 'change', () => { canvasFrame.classList.toggle('folio-drawing-white', background.value === 'white'); });
  listen(dialog, 'cancel', (event) => { event.preventDefault(); close(); });
  listen(window, 'beforeunload', (event) => {
    if (strokes.length || current) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  listen(dialog, 'keydown', (event) => {
    if (busy || /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      (event.shiftKey ? redo : undo).click();
    }
  });
  document.body.append(dialog);
  dialog.showModal();
  update();
  canvas.focus();
  return dialog;
}
