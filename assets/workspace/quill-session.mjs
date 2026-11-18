const cleared = new Set();

export function restoredChats(saved) {
  if (!Array.isArray(saved?.threads)) return null;
  const threads = [], ids = new Set();
  for (const row of saved.threads.slice(0, 20)) {
    if (!Number.isSafeInteger(row?.id) || row.id <= 0 || row.id > Number.MAX_SAFE_INTEGER - 100 || ids.has(row.id)) continue;
    ids.add(row.id);
    const history = [];
    if (Array.isArray(row.history)) {
      const items = row.history.slice(-24);
      for (let i = 1; i < items.length; i++) {
        const user = items[i - 1], reply = items[i];
        if (user?.role === 'user' && typeof user.content === 'string' && user.content.length <= 4000 &&
            reply?.role === 'assistant' && typeof reply.content === 'string' && reply.content.length <= 12000) {
          history.push(user, reply);
        }
      }
    }
    threads.push({...row, name: typeof row.name === 'string' && row.name.trim() ? row.name.slice(0, 80) : 'Chat ' + row.id,
      draft: typeof row.draft === 'string' ? row.draft.slice(0, 4000) : '', history});
  }
  return threads.length ? {open: saved.open === true, active: saved.active, threads} : null;
}

export function quillSession(profile) {
  const owner = profile.authenticated ? profile.user?.user_id : 'local';
  const prefix = 'quill.tab.v1:' + owner + ':';
  function read(key) {
    try { return owner ? JSON.parse(sessionStorage.getItem(prefix + key)) : null; } catch { return null; }
  }
  function write(key, data) {
    if (!owner || cleared.has(prefix)) return;
    const store = value => sessionStorage.setItem(prefix + key, JSON.stringify(value));
    try { store(data); return; } catch {}
    if (key.startsWith('chat:') && Array.isArray(data?.threads)) {
      // Keep drafts ahead of old transcripts if the browser's shared tab quota is full.
      const threads = data.threads.map(t => ({...t, history: t.history.slice(-2)}));
      try { store({...data, threads}); return; } catch {}
      const active = threads.find(t => t.id === data.active);
      if (active) {
        try { store({...data, threads:[{...active, history:[], selection:null, review:null}]}); return; } catch {}
      }
    }
    try { sessionStorage.removeItem(prefix + key); } catch {}
  }
  function clear() {
    cleared.add(prefix);
    try { for (const key of Object.keys(sessionStorage)) if (key.startsWith(prefix)) sessionStorage.removeItem(key); } catch {}
  }
  return {read, write, clear};
}
