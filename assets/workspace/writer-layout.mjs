const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const key = 'originwrite.layout.v1';
const defaultFontSize = 11;
const validFontSize = value => typeof value === 'number' && Number.isFinite(value) && value > 0;

export function setupWriterLayout(onResize = () => {}) {
  const grid = document.getElementById('writerGrid'), files = document.getElementById('filesPane');
  const filesHandle = document.getElementById('filesResize'), splitHandle = document.getElementById('panesResize');
  const directionControl = document.getElementById('paneDirection'), zoomControl = document.getElementById('codeZoom');
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(key)) || {}; } catch {}
  const layout = {width: Number.isFinite(saved.width) ? clamp(saved.width, 160, 400) : 230,
    ratio: Number.isFinite(saved.ratio) ? clamp(saved.ratio, .2, .8) : .5,
    stackRatio: Number.isFinite(saved.stackRatio) ? clamp(saved.stackRatio, .2, .8) : .5,
    direction: saved.direction === 'stack' ? 'stack' : 'side',
    fontSize: validFontSize(saved.fontSize) ? saved.fontSize : defaultFontSize,
    collapsed: saved.collapsed === true, swapped: saved.swapped === true,
    mode: ['split', 'code', 'pdf'].includes(saved.mode) ? saved.mode : 'split', dark: saved.dark === true};
  let drag = null, frame = null;
  function store() { try { localStorage.setItem(key, JSON.stringify(layout)); } catch {} }
  function maxFilesWidth() { return Math.min(400, Math.max(160, grid.clientWidth - 520)); }
  function ratioKey() { return grid.dataset.direction === 'stack' ? 'stackRatio' : 'ratio'; }
  function apply() {
    const narrow = grid.clientWidth < 760, stacked = narrow || layout.direction === 'stack';
    const ratio = stacked ? layout.stackRatio : layout.ratio;
    grid.dataset.narrow = String(narrow);
    grid.dataset.direction = stacked ? 'stack' : 'side';
    grid.dataset.preferredDirection = layout.direction;
    grid.dataset.collapsed = String(layout.collapsed);
    grid.dataset.swapped = String(layout.swapped);
    grid.dataset.mode = layout.mode;
    document.body.dataset.editorTheme = layout.dark ? 'dark' : 'light';
    const maxWidth = maxFilesWidth();
    grid.style.setProperty('--files-width', `${clamp(layout.width, 160, maxWidth)}px`);
    grid.style.setProperty('--code-fr', `${ratio}fr`);
    grid.style.setProperty('--pdf-fr', `${1 - ratio}fr`);
    grid.style.setProperty('--editor-font-size', `${layout.fontSize}px`);
    files.inert = layout.collapsed;
    document.getElementById('toggleFiles').setAttribute('aria-expanded', String(!layout.collapsed));
    document.getElementById('editorTheme').setAttribute('aria-pressed', String(layout.dark));
    document.getElementById('layoutMode').value = layout.mode;
    if (directionControl) {
      directionControl.value = layout.direction;
      directionControl.disabled = narrow || layout.mode !== 'split';
      directionControl.title = narrow ? 'Panes stack automatically on small screens' : 'Arrange code and PDF side by side or vertically';
    }
    if (zoomControl && document.activeElement !== zoomControl) zoomControl.value = String(layout.fontSize);
    for (const [id, unavailable] of [['codeZoomOut', layout.fontSize / 2 === 0], ['codeZoomIn', !Number.isFinite(layout.fontSize + Math.max(1, layout.fontSize * Number.EPSILON))]]) {
      const button = document.getElementById(id);
      if (button) button.disabled = unavailable;
    }
    filesHandle.setAttribute('aria-valuenow', String(Math.round(clamp(layout.width, 160, maxWidth))));
    filesHandle.setAttribute('aria-valuemax', String(maxWidth));
    filesHandle.tabIndex = narrow || layout.collapsed ? -1 : 0;
    splitHandle.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
    splitHandle.setAttribute('aria-valuetext', `Code ${Math.round(ratio * 100)}%, PDF ${Math.round((1 - ratio) * 100)}%`);
    splitHandle.setAttribute('aria-orientation', stacked ? 'horizontal' : 'vertical');
    splitHandle.setAttribute('aria-label', `Resize code and PDF pane ${stacked ? 'heights' : 'widths'}`);
    splitHandle.title = `Drag to resize code and PDF · double-click for equal ${stacked ? 'heights' : 'widths'}`;
    splitHandle.tabIndex = layout.mode === 'split' ? 0 : -1;
    if (frame === null) frame = requestAnimationFrame(() => {
      frame = null;
      if (grid.isConnected) onResize({fontSize: layout.fontSize, direction: grid.dataset.direction, narrow: grid.dataset.narrow === 'true'});
    });
  }
  document.getElementById('toggleFiles').onclick = () => { layout.collapsed = !layout.collapsed; apply(); store(); };
  document.getElementById('hideFiles').onclick = () => { layout.collapsed = true; apply(); store(); document.getElementById('toggleFiles').focus(); };
  document.getElementById('swapPanes').onclick = () => { layout.swapped = !layout.swapped; apply(); store(); };
  document.getElementById('layoutMode').onchange = e => { layout.mode = e.target.value; apply(); store(); };
  document.getElementById('editorTheme').onclick = () => { layout.dark = !layout.dark; apply(); store(); };
  document.getElementById('resetLayout').onclick = () => {
    Object.assign(layout, {width: 230, ratio: .5, stackRatio: .5, direction: 'side', fontSize: defaultFontSize, collapsed: false, swapped: false, mode: 'split'});
    zoomControl?.removeAttribute('aria-invalid');
    apply(); store();
  };
  if (directionControl) directionControl.onchange = e => { layout.direction = e.target.value === 'stack' ? 'stack' : 'side'; apply(); store(); };
  if (zoomControl) zoomControl.onchange = e => {
    const value = Number(e.target.value);
    if (validFontSize(value)) { layout.fontSize = value; zoomControl.removeAttribute('aria-invalid'); }
    else zoomControl.setAttribute('aria-invalid', 'true');
    zoomControl.value = String(layout.fontSize);
    apply(); store();
  };
  if (zoomControl) zoomControl.onkeydown = event => {
    if (event.key === 'Enter') { event.preventDefault(); zoomControl.blur(); }
    else if (event.key === 'Escape') { zoomControl.value = String(layout.fontSize); zoomControl.removeAttribute('aria-invalid'); zoomControl.blur(); }
  };
  for (const [id, change] of [['codeZoomIn', 1], ['codeZoomOut', -1]]) {
    const button = document.getElementById(id);
    if (button) button.onclick = () => {
      const value = layout.fontSize < 1 ? layout.fontSize * (change > 0 ? 2 : .5)
        : change < 0 && layout.fontSize <= 1 ? layout.fontSize / 2 : layout.fontSize + change;
      if (validFontSize(value)) layout.fontSize = Number(value.toPrecision(12));
      zoomControl?.removeAttribute('aria-invalid');
      apply(); store();
    };
  }
  function stop() {
    const prior = drag;
    drag = null;
    document.body.classList.remove('resizing', 'resizing-rows');
    if (prior && prior.handle.hasPointerCapture(prior.id)) prior.handle.releasePointerCapture(prior.id);
    store();
  }
  for (const handle of [filesHandle, splitHandle]) {
    handle.onpointerdown = e => {
      if (e.button !== 0 || drag) return;
      e.preventDefault();
      const rect = grid.getBoundingClientRect(), stacked = grid.dataset.direction === 'stack';
      const field = ratioKey();
      drag = {id: e.pointerId, handle, x: e.clientX, y: e.clientY,
        width: clamp(layout.width, 160, maxFilesWidth()), ratio: layout[field], field, stacked,
        extent: stacked ? rect.height - 6 : rect.width - (layout.collapsed ? 0 : files.getBoundingClientRect().width + 6) - 6};
      handle.setPointerCapture(e.pointerId);
      document.body.classList.add('resizing');
      document.body.classList.toggle('resizing-rows', handle === splitHandle && stacked);
    };
    handle.onpointermove = e => {
      if (!drag || drag.id !== e.pointerId || drag.handle !== handle) return;
      if (handle === filesHandle) layout.width = clamp(drag.width + e.clientX - drag.x, 160, maxFilesWidth());
      else {
        const delta = drag.stacked ? e.clientY - drag.y : e.clientX - drag.x;
        layout[drag.field] = clamp(drag.ratio + delta / Math.max(1, drag.extent) * (layout.swapped ? -1 : 1), .2, .8);
      }
      apply();
    };
    handle.onpointerup = stop; handle.onpointercancel = stop; handle.onlostpointercapture = stop;
    handle.ondblclick = () => { if (handle === filesHandle) layout.width = 230; else layout[ratioKey()] = .5; apply(); store(); };
    handle.onkeydown = e => {
      const stacked = handle === splitHandle && grid.dataset.direction === 'stack';
      const decrease = stacked ? 'ArrowUp' : 'ArrowLeft', increase = stacked ? 'ArrowDown' : 'ArrowRight';
      if (![decrease, increase, 'Home', 'End'].includes(e.key)) return;
      e.preventDefault();
      const delta = e.key === decrease ? -1 : 1;
      if (handle === filesHandle) layout.width = e.key === 'Home' ? 160 : e.key === 'End' ? maxFilesWidth() : clamp(layout.width + delta * 16, 160, maxFilesWidth());
      else {
        const field = ratioKey();
        layout[field] = e.key === 'Home' ? .2 : e.key === 'End' ? .8 : clamp(layout[field] + delta * .025 * (layout.swapped ? -1 : 1), .2, .8);
      }
      apply(); store();
    };
  }
  const observer = new ResizeObserver(apply); observer.observe(grid); apply();
  return {
    showCode() { if (layout.mode === 'pdf') { layout.mode = 'code'; apply(); store(); } },
    destroy() { observer.disconnect(); stop(); if (frame !== null) cancelAnimationFrame(frame); }
  };
}
