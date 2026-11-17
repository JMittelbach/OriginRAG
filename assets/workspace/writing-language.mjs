export async function streamWritingRequest(payload, signal, onEvent, maxBytes = 256000) {
  const headers = {'Content-Type': 'application/json'};
  const csrf = document.cookie.split('; ').find(value => value.startsWith('pfempire_csrf='))?.split('=')[1];
  if (csrf) headers['X-CSRF-Token'] = decodeURIComponent(csrf);
  const response = await fetch('/api/writing', {method: 'POST', headers,
    body: JSON.stringify({...payload, stream: true}), signal});
  if (!response.ok || response.redirected) {
    const data = await response.json().catch(() => null);
    throw new Error(data?.error || 'The request could not start. Check your connection and sign-in, then retry.');
  }
  if (!response.headers.get('content-type')?.includes('application/x-ndjson') || !response.body) {
    throw new Error('Live writing assistance is unavailable. Update or restart the web service, then retry.');
  }
  const reader = response.body.getReader(), decoder = new TextDecoder('utf-8', {fatal: true});
  let buffer = '', total = 0, completed = false, result;
  const event = line => {
    if (!line.trim()) return;
    const data = JSON.parse(line);
    if (data.type === 'error') throw new Error(data.error || 'The request was interrupted. Nothing has been changed.');
    if (completed) throw new Error('Unexpected response after the completed request.');
    if (data.type === 'done') { completed = true; result = data; }
    onEvent(data);
  };
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) { buffer += decoder.decode(); break; }
      total += value.length;
      if (total > maxBytes) throw new Error('The request exceeded its response limit. Nothing has been changed.');
      buffer += decoder.decode(value, {stream: true});
      let end;
      while ((end = buffer.indexOf('\n')) !== -1) { event(buffer.slice(0, end)); buffer = buffer.slice(end + 1); }
    }
    if (buffer.trim()) event(buffer);
    if (!completed) throw new Error('The request was interrupted before completion. Nothing has been changed.');
    return result;
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

let disposeMenu;

export function setupLanguageMenu(root, {getEditor, enabled, open}) {
  disposeMenu?.();
  const controller = new AbortController(), options = {signal: controller.signal};
  let menu;
  const close = () => { menu?.remove(); menu = null; };
  root.addEventListener('contextmenu', event => {
    const selected = getEditor().selection();
    if (!enabled || !/\.(tex|bib|sty|cls|bst|bbl|txt|md|csv)$/i.test(selected.file) || !selected.text.trim() || selected.text.length > 8000) return;
    event.preventDefault(); close();
    menu = document.createElement('div'); menu.className = 'language-context-menu'; menu.setAttribute('role', 'menu');
    const action = (label, handler) => {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
      button.setAttribute('role', 'menuitem'); button.onclick = handler; menu.append(button); return button;
    };
    const ask = action('Ask Quill…', () => { close(); open(selected); });
    action('Copy selected text', async () => { try { await navigator.clipboard.writeText(selected.text); } catch {} finally { close(); } });
    action('Cancel', close);
    document.body.append(menu);
    menu.style.left = `${Math.max(8, Math.min(event.clientX || root.getBoundingClientRect().left, innerWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(event.clientY || root.getBoundingClientRect().top, innerHeight - menu.offsetHeight - 8))}px`;
    menu.addEventListener('keydown', key => {
      const items = [...menu.querySelectorAll('button')], index = items.indexOf(document.activeElement);
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(key.key)) {
        key.preventDefault();
        items[key.key === 'Home' ? 0 : key.key === 'End' ? items.length - 1 : (index + (key.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus();
      }
    });
    ask.focus();
  }, options);
  document.addEventListener('pointerdown', event => { if (menu && !menu.contains(event.target)) close(); }, options);
  document.addEventListener('keydown', event => { if (menu && event.key === 'Escape') { event.preventDefault(); close(); getEditor().focus(); } }, options);
  window.addEventListener('blur', close, options);
  disposeMenu = () => { close(); controller.abort(); };
  return disposeMenu;
}
