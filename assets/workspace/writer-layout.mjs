const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const key = 'originwrite.layout.v1';

export function setupWriterLayout(onResize) {
  const grid = document.getElementById('writerGrid'), files = document.getElementById('filesPane');
  const filesHandle = document.getElementById('filesResize'), splitHandle = document.getElementById('panesResize');
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(key)) || {}; } catch {}
  const layout = {width: Number.isFinite(saved.width) ? clamp(saved.width, 160, 400) : 230,
    ratio: Number.isFinite(saved.ratio) ? clamp(saved.ratio, .2, .8) : .5,
    collapsed: saved.collapsed === true, swapped: saved.swapped === true,
    mode: ['split', 'code', 'pdf'].includes(saved.mode) ? saved.mode : 'split', dark: saved.dark === true};
  let drag = null;
  function store() { try { localStorage.setItem(key, JSON.stringify(layout)); } catch {} }
  function apply() {
    const narrow = grid.clientWidth < 760;
    grid.dataset.narrow = String(narrow);
    grid.dataset.collapsed = String(layout.collapsed);
    grid.dataset.swapped = String(layout.swapped);
    grid.dataset.mode = layout.mode;
    document.body.dataset.editorTheme = layout.dark ? 'dark' : 'light';
    const maxWidth = Math.min(400, Math.max(160, grid.clientWidth - 520));
    grid.style.setProperty('--files-width', `${clamp(layout.width, 160, maxWidth)}px`);
    grid.style.setProperty('--code-fr', `${layout.ratio}fr`);
    grid.style.setProperty('--pdf-fr', `${1 - layout.ratio}fr`);
    files.inert = layout.collapsed;
    document.getElementById('toggleFiles').setAttribute('aria-expanded', String(!layout.collapsed));
    document.getElementById('editorTheme').setAttribute('aria-pressed', String(layout.dark));
    document.getElementById('layoutMode').value = layout.mode;
    filesHandle.setAttribute('aria-valuenow', String(Math.round(clamp(layout.width, 160, maxWidth))));
    filesHandle.setAttribute('aria-valuemax', String(maxWidth));
    splitHandle.setAttribute('aria-valuenow', String(Math.round(layout.ratio * 100)));
    splitHandle.setAttribute('aria-orientation', narrow ? 'horizontal' : 'vertical');
    onResize();
  }
  document.getElementById('toggleFiles').onclick = () => { layout.collapsed = !layout.collapsed; apply(); store(); };
  document.getElementById('hideFiles').onclick = () => { layout.collapsed = true; apply(); store(); document.getElementById('toggleFiles').focus(); };
  document.getElementById('swapPanes').onclick = () => { layout.swapped = !layout.swapped; apply(); store(); };
  document.getElementById('layoutMode').onchange = e => { layout.mode = e.target.value; apply(); store(); };
  document.getElementById('editorTheme').onclick = () => { layout.dark = !layout.dark; apply(); store(); };
  document.getElementById('resetLayout').onclick = () => { Object.assign(layout, {width: 230, ratio: .5, collapsed: false, swapped: false, mode: 'split'}); apply(); store(); };
  for (const handle of [filesHandle, splitHandle]) {
    handle.onpointerdown = e => {
      if (e.button !== 0) return;
      e.preventDefault();
      const rect = grid.getBoundingClientRect(), narrow = grid.dataset.narrow === 'true';
      drag = {id: e.pointerId, x: e.clientX, y: e.clientY, width: layout.width, ratio: layout.ratio,
        narrow, extent: narrow ? rect.height - 6 : rect.width - (layout.collapsed ? 0 : files.getBoundingClientRect().width + 6) - 6};
      handle.setPointerCapture(e.pointerId); document.body.classList.add('resizing');
    };
    handle.onpointermove = e => {
      if (!drag || drag.id !== e.pointerId) return;
      if (handle === filesHandle) layout.width = clamp(drag.width + e.clientX - drag.x, 160, Math.min(400, Math.max(160, grid.clientWidth - 520)));
      else {
        const delta = drag.narrow ? e.clientY - drag.y : e.clientX - drag.x;
        layout.ratio = clamp(drag.ratio + delta / Math.max(1, drag.extent) * (layout.swapped ? -1 : 1), .2, .8);
      }
      apply();
    };
    const stop = () => { drag = null; document.body.classList.remove('resizing'); store(); };
    handle.onpointerup = stop; handle.onpointercancel = stop; handle.onlostpointercapture = stop;
    handle.ondblclick = () => { if (handle === filesHandle) layout.width = 230; else layout.ratio = .5; apply(); store(); };
    handle.onkeydown = e => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return;
      e.preventDefault();
      const delta = ['ArrowLeft', 'ArrowUp'].includes(e.key) ? -1 : 1;
      if (handle === filesHandle) layout.width = e.key === 'Home' ? 160 : e.key === 'End' ? 400 : clamp(layout.width + delta * 16, 160, 400);
      else layout.ratio = e.key === 'Home' ? .2 : e.key === 'End' ? .8 : clamp(layout.ratio + delta * .025 * (layout.swapped ? -1 : 1), .2, .8);
      apply(); store();
    };
  }
  const observer = new ResizeObserver(apply); observer.observe(grid); apply();
  return {showCode() { if (layout.mode === 'pdf') { layout.mode = 'code'; apply(); store(); } }};
}
