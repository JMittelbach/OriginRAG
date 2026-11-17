export function projectChatWindow(panel, {name, status, disconnected, closed}) {
  const host = window, hostDocument = document;
  const manuscriptURL = new URL(host.location.href).href;
  const handle = panel.querySelector('#projectChatHandle');
  const button = panel.querySelector('#projectChatWindow');
  let popup = null, childClosed = null, restoring = false, disposed = false, drag = null, sizing = null, savedStyle = '';
  const detached = () => Boolean(popup && !popup.closed);

  function clampPosition() {
    if (disposed || detached() || panel.hidden || !panel.style.left) return;
    const bounds = panel.getBoundingClientRect();
    panel.style.left = Math.max(4, Math.min(bounds.left, host.innerWidth - bounds.width - 4)) + 'px';
    panel.style.top = Math.max(4, Math.min(bounds.top, host.innerHeight - bounds.height - 4)) + 'px';
  }
  function dock(show = true) {
    const child = popup;
    if (child && childClosed) child.removeEventListener('pagehide', childClosed);
    childClosed = null;
    popup = null; restoring = true;
    hostDocument.body.append(panel);
    panel.style.cssText = savedStyle;
    panel.hidden = !show;
    hostDocument.getElementById('projectChat')?.setAttribute('aria-expanded', String(show));
    button.textContent = '↗'; button.title = 'Open on another screen';
    button.setAttribute('aria-label', 'Open project chat in a separate window');
    handle.setAttribute('aria-label', 'Move project chat');
    if (child && !child.closed) child.close();
    restoring = false; clampPosition();
    if (show) panel.querySelector('#projectChatInput')?.focus();
  }
  function openWindow() {
    if (disposed) return;
    if (detached()) { dock(); return; }
    const child = host.open('', 'quill-assistant-' + crypto.randomUUID(), 'popup=yes,width=420,height=620,resizable=yes,scrollbars=yes');
    if (!child) { status('The browser blocked the chat window. Allow pop-ups for this workspace, or keep using the docked chat.'); return; }
    try {
      const doc = child.document;
      doc.title = name() + ' · Quill assistant';
      const charset = doc.createElement('meta'); charset.charset = 'utf-8';
      const viewport = doc.createElement('meta'); viewport.name = 'viewport'; viewport.content = 'width=device-width,initial-scale=1';
      doc.head.append(charset, viewport);
      for (const file of ['design-system.css', 'writing-chat.css']) {
        const style = doc.createElement('link'); style.rel = 'stylesheet';
        style.href = new URL('/assets/workspace/' + file, host.location.origin).href; doc.head.append(style);
      }
      const theme = getComputedStyle(hostDocument.documentElement), editor = getComputedStyle(panel);
      for (const key of ['--bg', '--panel', '--panel-2', '--text', '--muted', '--line', '--teal', '--warning', '--danger', '--ui-font']) {
        const value = theme.getPropertyValue(key) || editor.getPropertyValue(key);
        if (value.trim()) doc.documentElement.style.setProperty(key, value);
      }
      for (const key of ['--code-bg', '--code-text']) {
        const value = editor.getPropertyValue(key);
        if (value.trim()) doc.documentElement.style.setProperty(key, value);
      }
      doc.documentElement.dataset.theme = hostDocument.documentElement.dataset.theme || 'dark';
      doc.body.className = 'project-chat-window';
      savedStyle = panel.style.cssText;
      panel.removeAttribute('style'); popup = child;
      doc.body.append(panel);
      button.textContent = '↙'; button.title = 'Dock back in Quill';
      button.setAttribute('aria-label', 'Dock project chat back in Quill');
      handle.setAttribute('aria-label', 'Project chat in a separate window');
      childClosed = () => {
        if (!restoring && popup === child && !disposed) { closed?.(); dock(false); }
      };
      child.addEventListener('pagehide', childClosed);
      panel.querySelector('#projectChatInput')?.focus(); child.focus();
    } catch {
      popup = child; dock();
      status('A separate window is unavailable in this browser. Your conversation is still here.');
    }
  }
  button.onclick = openWindow;
  handle.addEventListener('pointerdown', event => {
    if (detached() || event.button !== 0 || event.target.closest('button, a, input, select')) return;
    const bounds = panel.getBoundingClientRect();
    drag = {id: event.pointerId, x: event.clientX, y: event.clientY, left: bounds.left, top: bounds.top};
    panel.style.left = bounds.left + 'px'; panel.style.top = bounds.top + 'px'; panel.style.right = 'auto';
    handle.setPointerCapture(event.pointerId); handle.classList.add('dragging'); event.preventDefault();
  });
  handle.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    panel.style.left = drag.left + event.clientX - drag.x + 'px';
    panel.style.top = drag.top + event.clientY - drag.y + 'px'; clampPosition();
  });
  const stop = () => { drag = null; handle.classList.remove('dragging'); };
  for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) handle.addEventListener(event, stop);
  function sizePanel(width, height, box, left) {
    const minimumWidth = Math.min(300, host.innerWidth - 16), minimumHeight = Math.min(300, host.innerHeight - 16);
    width = Math.max(minimumWidth, Math.min(width, host.innerWidth - 16));
    height = Math.max(minimumHeight, Math.min(height, host.innerHeight - 16));
    panel.style.right = 'auto';
    panel.style.left = Math.max(4, Math.min(left ? box.x + box.width - width : box.x, host.innerWidth - width - 4)) + 'px';
    panel.style.top = Math.max(4, Math.min(box.y, host.innerHeight - height - 4)) + 'px';
    panel.style.width = width + 'px'; panel.style.height = height + 'px';
  }
  for (const grip of panel.querySelectorAll('.project-chat-resize')) {
    const left = grip.classList.contains('project-chat-resize-left');
    grip.addEventListener('pointerdown', event => {
      if (disposed || detached() || event.button !== 0) return;
      sizing = {id: event.pointerId, x: event.clientX, y: event.clientY, box: panel.getBoundingClientRect(), left};
      grip.setPointerCapture(event.pointerId); event.preventDefault();
    });
    grip.addEventListener('pointermove', event => {
      if (!sizing || event.pointerId !== sizing.id) return;
      sizePanel(sizing.box.width + (event.clientX - sizing.x) * (sizing.left ? -1 : 1),
        sizing.box.height + event.clientY - sizing.y, sizing.box, sizing.left);
    });
    for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) grip.addEventListener(event, () => { sizing = null; });
    grip.addEventListener('keydown', event => {
      if (detached() || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault(); const box = panel.getBoundingClientRect();
      const horizontal = event.key === 'ArrowRight' ? 20 : event.key === 'ArrowLeft' ? -20 : 0;
      sizePanel(box.width + horizontal * (left ? -1 : 1), box.height + (event.key === 'ArrowDown' ? 20 : event.key === 'ArrowUp' ? -20 : 0), box, left);
    });
  }
  handle.addEventListener('keydown', event => {
    if (detached() || event.target !== handle || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    const bounds = panel.getBoundingClientRect();
    panel.style.right = 'auto';
    panel.style.left = bounds.left + (event.key === 'ArrowLeft' ? -20 : event.key === 'ArrowRight' ? 20 : 0) + 'px';
    panel.style.top = bounds.top + (event.key === 'ArrowUp' ? -20 : event.key === 'ArrowDown' ? 20 : 0) + 'px'; clampPosition();
  });
  function disconnect() {
    if (disposed) return;
    disposed = true;
    host.removeEventListener('resize', clampPosition); host.removeEventListener('pagehide', disconnect); observer?.disconnect();
    disconnected();
    if (detached()) {
      if (childClosed) popup.removeEventListener('pagehide', childClosed);
      childClosed = null;
      popup.document.title = 'Quill assistant';
      const notice = popup.document.createElement('p'); notice.className = 'project-chat-disconnected';
      notice.textContent = 'The connected editor was closed or switched projects. Project context and chat have been cleared from this window. ';
      const link = popup.document.createElement('a'); link.href = manuscriptURL;
      link.textContent = 'Reopen the manuscript'; notice.append(link);
      popup.document.body.replaceChildren(notice);
    } else panel.remove();
  }
  host.addEventListener('resize', clampPosition);
  host.addEventListener('pagehide', disconnect);
  const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(clampPosition) : null;
  observer?.observe(panel);
  return {detached, dock, destroy: disconnect, focus() { if (detached()) popup.focus(); }};
}
