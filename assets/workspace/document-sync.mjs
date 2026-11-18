import {requestJSON} from './request.mjs';

export function bindDocumentSync(panel) {
  if (!panel) return;
  const el = id => document.getElementById('docSync' + id);
  const endpoint = '/api/document-sync';
  let token = '', busy = false, feedback = '';
  const current = () => {
    const kind = location.pathname.slice(1), id = new URLSearchParams(location.search).get('id');
    return ['write', 'folio'].includes(kind) && /^[0-9a-f]{32}$/.test(id || '') ? {kind, id} : null;
  };
  const mark = (doc, write = false) => {
    const key = 'origin.sync.v1.' + doc.kind + '.' + doc.id;
    try { if (write) localStorage.setItem(key, '1'); return localStorage.getItem(key) === '1'; } catch { return false; }
  };
  const tell = message => { feedback = message; el('Notice').textContent = message; };
  async function send(payload) {
    if (!token) token = (await requestJSON(endpoint)).token;
    return requestJSON(endpoint, {payload, headers: {'X-OriginRAG-Local-Token': token}});
  }
  async function refresh() {
    const state = await requestJSON(endpoint);
    token = state.token;
    el('Connected').hidden = !state.folder;
    el('Connect').hidden = el('Setup').hidden = Boolean(state.folder);
    el('Now').hidden = !current();
    el('Now').disabled = !state.available;
    el('Path').textContent = state.folder;
    el('Folders').replaceChildren(...state.suggestions.map(path => new Option(path, path)));
    if (!state.folder && !el('Folder').value && state.suggestions.length === 1) el('Folder').value = state.suggestions[0];
    el('List').replaceChildren();
    for (const doc of state.documents) {
      const row = document.createElement('li'), title = document.createElement('span');
      title.textContent = doc.title + (doc.conflict ? ' · conflicting version ' + doc.device.slice(0, 6) : '');
      row.append(title);
      for (const [action, label] of (doc.conflict ? [['copy', 'Open as copy']] : [['open', 'Open'], ['copy', 'Open as copy']])) {
        const button = document.createElement('button');
        button.type = 'button'; button.textContent = label;
        button.onclick = () => run(async () => {
          await window.originragSaveDraft?.();
          const res = await send({action, kind: doc.kind, id: doc.id, device: doc.device});
          if (action !== 'copy') mark({kind: doc.kind, id: res.id}, true);
          await window.originragSaveDraft?.();
          location.assign(res.url);
        });
        row.append(button);
      }
      el('List').append(row);
    }
    el('Notice').textContent = state.folder && !state.available ? 'Folder offline. Reconnect the drive or disconnect this folder.' : state.unreadable ? 'Some shared files could not be read. Wait for the folder to finish syncing, then refresh.' : feedback || (state.folder && !state.documents.length
      ? 'No shared documents yet. Open a manuscript and choose Sync this document.' : '');
  }
  async function run(task) {
    if (busy) return;
    busy = true;
    panel.setAttribute('aria-busy', 'true');
    try { await task(); } catch (error) { tell(error.message); token = ''; }
    finally { busy = false; panel.removeAttribute('aria-busy'); }
  }
  panel.addEventListener('toggle', () => { if (panel.open) run(refresh); });
  panel.closest('dialog').addEventListener('close', () => { panel.open = false; });
  el('Link').onclick = () => run(async () => {
    await send({action:'connect', folder: el('Folder').value});
    tell('Folder connected. Only documents you choose will be shared.'); await refresh();
  });
  el('Disconnect').onclick = () => run(async () => {
    await send({action:'disconnect'});
    try { for (const key of Object.keys(localStorage)) if (key.startsWith('origin.sync.v1.')) localStorage.removeItem(key); } catch {}
    document.getElementById('globalSettings').removeAttribute('data-sync-alert');
    tell('Disconnected. Saved documents are unchanged.'); await refresh();
  });
  el('Refresh').onclick = () => run(refresh);
  el('Now').onclick = () => run(async () => {
    const doc = current(); if (!doc) return;
    await window.originragSaveDraft?.();
    const res = await send({action:'sync', ...doc});
    mark(doc, true); tell(res.message);
    document.getElementById('globalSettings').removeAttribute('data-sync-alert');
    if (res.received) { await window.originragSaveDraft?.(); location.reload(); return; }
    await refresh();
  });
  const timers = new Map();
  window.addEventListener('origin-document-saved', ({detail: doc}) => {
    if (panel.hidden || !mark(doc)) return;
    const key = doc.kind + '.' + doc.id;
    clearTimeout(timers.get(key));
    timers.set(key, setTimeout(async () => {
      timers.delete(key);
      try {
        const res = await send({action:'auto', ...doc});
        if (!res.skipped) tell(res.message);
      } catch (error) {
        tell(error.message);
        document.getElementById('globalSettings').setAttribute('data-sync-alert', '');
        document.getElementById('globalSettings').title = 'Document sync: ' + error.message;
      }
    }, 1500));
  });
}
