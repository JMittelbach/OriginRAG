const MAX_PAGES = 1000, MAX_CANVASES = 4, MAX_PIXELS = 2000000;

export function createPDFPreview(container, count, onError = () => {}) {
  let task, document, views = [], zoom = 'fit', generation = 0, frame = 0, rendering = false, again = false, destroyed = false;
  let firstReady;
  const resize = new ResizeObserver(() => schedule()); resize.observe(container);
  container.addEventListener('scroll', schedule, {passive: true});
  function release(view) {
    view.renderTask?.cancel(); view.renderTask = null;
    if (view.canvas) { view.canvas.width = 0; view.canvas.height = 0; view.canvas.remove(); view.canvas = null; }
    view.ready = false; view.placeholder.hidden = false; view.page?.cleanup(); view.page = null;
  }
  function size(view) {
    const width = zoom === 'fit' ? Math.max(80, container.clientWidth - 48) : view.width * Number(zoom);
    view.element.style.width = width + 'px'; view.element.style.height = width * view.height / view.width + 'px';
  }
  function schedule() {
    if (destroyed || frame) return;
    frame = requestAnimationFrame(() => { frame = 0; renderNearby().catch(onError); });
  }
  async function renderNearby() {
    if (rendering) { again = true; return; }
    if (!document || !views.length || destroyed || container.clientHeight === 0) { firstReady?.(); return; }
    rendering = true; const token = generation;
    try {
      for (const view of views) size(view);
      const bounds = container.getBoundingClientRect(), centre = bounds.top + Math.min(bounds.height / 2, 400);
      const nearest = views.map(view => ({view, rect: view.element.getBoundingClientRect()}))
        .map(({view, rect}) => ({view, distance: rect.bottom < centre ? centre - rect.bottom : rect.top > centre ? rect.top - centre : 0}))
        .sort((a, b) => a.distance - b.distance).slice(0, MAX_CANVASES).map(item => item.view);
      for (const view of views) if (!nearest.includes(view)) release(view);
      for (const view of nearest) {
        if (token !== generation || destroyed) break;
        if (view.ready) continue;
        try {
          const page = await document.getPage(view.number);
          if (token !== generation || destroyed) break;
          view.page = page;
          const unscaled = page.getViewport({scale: 1}); view.width = unscaled.width; view.height = unscaled.height; size(view);
          const scale = Math.min(2, Math.sqrt(MAX_PIXELS / (unscaled.width * unscaled.height)));
          const viewport = page.getViewport({scale});
          const canvas = window.document.createElement('canvas'); canvas.width = Math.floor(viewport.width); canvas.height = Math.floor(viewport.height);
          canvas.dataset.pageWidth = view.width; canvas.setAttribute('aria-label', `PDF page ${view.number}`);
          view.canvas = canvas; view.element.append(canvas);
          view.renderTask = page.render({canvasContext: canvas.getContext('2d'), viewport});
          await view.renderTask.promise;
          if (token !== generation || destroyed) break;
          view.ready = true; view.placeholder.hidden = true; view.renderTask = null;
        } catch (error) {
          release(view);
          if (token !== generation || error.name === 'RenderingCancelledException') break;
          view.placeholder.textContent = `Page ${view.number} unavailable. Download the PDF to read it.`;
          onError(error);
        }
        firstReady?.(); firstReady = null;
        if (again) break;
      }
    } finally {
      rendering = false;
      firstReady?.(); firstReady = null;
      if (again) { again = false; schedule(); }
    }
  }
  return {
    async load(bytes) {
      const token = ++generation;
      for (const view of views) release(view);
      const previous = task; task = null; document = null; views = [];
      if (previous) await previous.destroy();
      if (token !== generation || destroyed) return;
      const pdfjs = await import('/assets/vendor/pdfjs/legacy/build/pdf.min.mjs');
      if (token !== generation || destroyed) return;
      pdfjs.GlobalWorkerOptions.workerSrc = '/assets/vendor/pdfjs/legacy/build/pdf.worker.min.mjs';
      task = pdfjs.getDocument({data: bytes, isEvalSupported: false, cMapUrl: '/assets/vendor/pdfjs/cmaps/', cMapPacked: true, standardFontDataUrl: '/assets/vendor/pdfjs/standard_fonts/', wasmUrl: '/assets/vendor/pdfjs/wasm/'});
      const loaded = await task.promise;
      if (token !== generation || destroyed) return;
      document = loaded;
      const first = await loaded.getPage(1), dimensions = first.getViewport({scale: 1});
      if (token !== generation || destroyed) return;
      const fragment = window.document.createDocumentFragment();
      for (let number = 1; number <= Math.min(loaded.numPages, MAX_PAGES); number++) {
        const element = window.document.createElement('div'); element.className = 'write-pdf-page'; element.dataset.page = number;
        const placeholder = window.document.createElement('span'); placeholder.className = 'write-pdf-placeholder'; placeholder.textContent = `Page ${number}`;
        element.append(placeholder); const view = {number, element, placeholder, width: dimensions.width, height: dimensions.height};
        views.push(view); size(view); fragment.append(element);
      }
      if (loaded.numPages > MAX_PAGES) {
        const more = window.document.createElement('p'); more.textContent = `Preview shows the first ${MAX_PAGES} pages. Download the PDF for all pages.`; fragment.append(more);
      }
      const top = container.scrollTop; container.replaceChildren(fragment); container.scrollTop = top;
      count.textContent = `${loaded.numPages} ${loaded.numPages === 1 ? 'page' : 'pages'}`;
      await new Promise(resolve => { firstReady = resolve; schedule(); });
    },
    zoom(value) { zoom = value; for (const view of views) size(view); schedule(); },
    async destroy() {
      destroyed = true; generation++; resize.disconnect(); cancelAnimationFrame(frame); container.removeEventListener('scroll', schedule);
      for (const view of views) release(view);
      firstReady?.(); firstReady = null; const previous = task; task = document = null; views = [];
      if (previous) await previous.destroy();
    },
  };
}
