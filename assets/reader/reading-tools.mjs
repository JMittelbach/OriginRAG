const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));

export function pageFitScale(pageWidth, pageHeight, availableWidth, availableHeight) {
  if (![pageWidth, pageHeight, availableWidth, availableHeight].every(value => Number.isFinite(value) && value > 0)) return null;
  return clamp(Math.min(availableWidth / pageWidth, availableHeight / pageHeight), .35, 3);
}

export function readingProgress(scrollTop, scrollHeight, clientHeight) {
  if (![scrollTop, scrollHeight, clientHeight].every(Number.isFinite) || clientHeight <= 0) return 0;
  const distance = scrollHeight - clientHeight;
  return distance <= 1 ? 100 : clamp(scrollTop / distance * 100, 0, 100);
}

export function readingTools({getPdf, getPage, getZoom, renderPage, status = () => {}, getReady = () => Boolean(getPdf()), getSaving = () => false}) {
  const $ = id => document.getElementById(id);
  const workspace = $('workspace'), panel = $('notesPanel'), resize = $('notesResize');
  const fullscreen = $('fullscreen'), fitPage = $('fitPage'), help = $('shortcuts'), dialog = $('shortcutsDialog');
  const removers = [];
  let frame = 0, fitting = false, drag = null, disposed = false, fitSequence = 0;
  let fullscreenPending = false, nativeFocus = false;
  const root = document.documentElement;
  const fullscreenElement = () => document.fullscreenElement || document.webkitFullscreenElement;
  let notesWidth = panel?.getBoundingClientRect().width || 330;
  const listen = (target, name, handler, options) => {
    target?.addEventListener(name, handler, options);
    removers.push(() => target?.removeEventListener(name, handler, options));
  };
  const editable = target => target instanceof Element && target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"]');
  const selectionActive = () => Boolean(window.getSelection()?.toString());

  function updateProgress() {
    frame = 0;
    if (disposed) return;
    const pdf = getPdf(), progress = $('readingProgress'), position = $('readingPosition');
    const page = clamp(Number(getPage()) || 1, 1, pdf?.numPages || 1);
    if (progress) {
      progress.max = 100;
      progress.value = pdf ? readingProgress(workspace.scrollTop, workspace.scrollHeight, workspace.clientHeight) : 0;
      progress.setAttribute('aria-valuetext', pdf ? `Page ${page} of ${pdf.numPages}` : 'No paper open');
    }
    const label = pdf ? `Page ${page} of ${pdf.numPages}` : 'Opening paper…';
    if (position && position.textContent !== label) position.textContent = label;
  }

  function scheduleProgress() {
    if (!disposed && !frame) frame = requestAnimationFrame(updateProgress);
  }

  function resizeMaximum() {
    return Math.min(480, Math.max(240, (workspace.parentElement?.clientWidth || innerWidth) - 320 - (resize?.offsetWidth || 8)));
  }

  function setNotesWidth(value) {
    const maximum = resizeMaximum();
    notesWidth = Math.round(clamp(value, 240, maximum));
    document.documentElement.style.setProperty('--notes-width', notesWidth + 'px');
    resize?.setAttribute('aria-valuemin', '240');
    resize?.setAttribute('aria-valuemax', String(maximum));
    resize?.setAttribute('aria-valuenow', String(notesWidth));
    resize?.setAttribute('aria-valuetext', `${notesWidth} pixels`);
    scheduleProgress();
  }

  function endResize() {
    if (!drag) return;
    const pointerId = drag.pointerId;
    drag = null;
    try { if (resize?.hasPointerCapture(pointerId)) resize.releasePointerCapture(pointerId); } catch {}
    resize?.classList.remove('is-resizing');
    document.documentElement.classList.remove('notes-resizing');
  }

  function update() {
    if (disposed) return;
    if (fitPage) fitPage.disabled = !getPdf() || !getReady() || getSaving() || fitting;
    if (fullscreen) {
      const focused = document.documentElement.classList.contains('reader-focus');
      fullscreen.disabled = false;
      const label = focused ? 'Exit focus' : 'Focus view';
      if (fullscreen.textContent !== label) fullscreen.textContent = label;
      fullscreen.setAttribute('aria-pressed', String(focused));
      fullscreen.title = focused ? 'Exit focus view (F or Esc)' : 'Read full screen (F)';
    }
    if (resize) {
      resize.hidden = Boolean(panel?.hidden);
      if (resize.hidden || innerWidth <= 720) endResize();
    }
    scheduleProgress();
  }

  async function fit() {
    const pdf = getPdf();
    if (!pdf || fitting || !getReady() || getSaving()) return;
    fitting = true;
    const token = ++fitSequence, number = getPage(), previousZoom = getZoom();
    update();
    try {
      const page = await pdf.getPage(number);
      if (disposed || token !== fitSequence || pdf !== getPdf() || number !== getPage() || previousZoom !== getZoom()) return;
      const viewport = page.getViewport({scale: 1}), style = getComputedStyle(workspace);
      const padding = property => parseFloat(style[property]) || 0;
      const width = workspace.clientWidth - padding('paddingLeft') - padding('paddingRight');
      const height = workspace.clientHeight - padding('paddingTop') - padding('paddingBottom');
      const scale = pageFitScale(viewport.width, viewport.height, width, height);
      if (scale === null) throw new Error('Page dimensions unavailable');
      await renderPage(number, scale, 0);
    } catch {
      if (!disposed) status('This page could not be fitted. Try Fit width or the zoom controls.', true);
    } finally {
      fitting = false;
      update();
    }
  }

  function setFocus(focused) {
    root.classList.toggle('reader-focus', focused);
    setNotesWidth(notesWidth);
    update();
  }

  async function exitNativeFocus() {
    if (!nativeFocus || fullscreenElement() !== root) return;
    try {
      const exit = document.exitFullscreen || document.webkitExitFullscreen;
      await exit?.call(document);
    } catch {
      if (!disposed && fullscreenElement() === root) setFocus(true);
    }
  }

  function syncFullscreen() {
    if (fullscreenElement() === root) {
      nativeFocus = true;
      if (disposed || !root.classList.contains('reader-focus')) void exitNativeFocus();
    } else if (nativeFocus) {
      nativeFocus = false;
      if (!disposed) setFocus(false);
    }
  }

  async function toggleFocus() {
    if (disposed) return;
    const focused = !root.classList.contains('reader-focus');
    setFocus(focused);
    if (!focused) {await exitNativeFocus(); return;}
    const request = root.requestFullscreen || root.webkitRequestFullscreen;
    if (fullscreenPending || fullscreenElement() || !request || document.fullscreenEnabled === false || document.webkitFullscreenEnabled === false) return;
    fullscreenPending = true;
    try {
      await request.call(root);
      syncFullscreen();
    } catch {
      // Keep the in-window reading layout when full screen is unavailable or denied.
    } finally {
      fullscreenPending = false;
    }
  }

  function openHelp() {
    if (!dialog || dialog.open) return;
    dialog.showModal();
  }

  listen(fitPage, 'click', fit);
  listen(fullscreen, 'click', toggleFocus);
  listen(document, 'fullscreenchange', syncFullscreen);
  listen(document, 'webkitfullscreenchange', syncFullscreen);
  listen(help, 'click', openHelp);
  listen($('shortcutsClose'), 'click', () => dialog?.close());
  listen(workspace, 'scroll', scheduleProgress, {passive: true});
  listen(window, 'resize', () => {setNotesWidth(notesWidth); update();});
  listen(window, 'blur', endResize);
  listen(document, 'keydown', event => {
    if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || editable(event.target) || document.querySelector('dialog[open]') || selectionActive()) return;
    if (event.key === '?') {event.preventDefault(); openHelp();}
    else if (event.key.toLowerCase() === 'f' && !event.repeat) {event.preventDefault(); toggleFocus();}
    else if (event.key === 'Escape' && !event.repeat && document.documentElement.classList.contains('reader-focus') && $('searchBar')?.hidden !== false) {event.preventDefault(); toggleFocus();}
  });
  listen(resize, 'pointerdown', event => {
    if (event.button !== 0 || innerWidth <= 720 || panel?.hidden) return;
    drag = {pointerId: event.pointerId, x: event.clientX, width: panel.getBoundingClientRect().width || notesWidth};
    resize.focus();
    try { resize.setPointerCapture(event.pointerId); } catch {}
    resize.classList.add('is-resizing');
    document.documentElement.classList.add('notes-resizing');
    event.preventDefault();
  });
  listen(window, 'pointermove', event => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    setNotesWidth(drag.width + drag.x - event.clientX);
    event.preventDefault();
  });
  for (const name of ['pointerup', 'pointercancel']) listen(window, name, event => {if (drag && event.pointerId === drag.pointerId) endResize();});
  listen(resize, 'lostpointercapture', endResize);
  listen(resize, 'keydown', event => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing || innerWidth <= 720 || panel?.hidden) return;
    const step = event.shiftKey ? 40 : 16;
    const next = {'ArrowLeft': notesWidth + step, 'ArrowRight': notesWidth - step, 'Home': 240, 'End': resizeMaximum()}[event.key];
    if (next === undefined) return;
    event.preventDefault();
    event.stopPropagation();
    setNotesWidth(next);
  });
  const observer = panel ? new MutationObserver(update) : null;
  observer?.observe(panel, {attributes: true, attributeFilter: ['hidden']});
  setNotesWidth(notesWidth);
  update();
  return {update, destroy() {
    document.documentElement.classList.remove('reader-focus');
    void exitNativeFocus();
    update();
    disposed = true;
    fitSequence++;
    endResize();
    if (frame) cancelAnimationFrame(frame);
    observer?.disconnect();
    for (const remove of removers) remove();
  }};
}
