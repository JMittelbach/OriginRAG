const cleared = new Set();

export function quillSession(profile) {
  const owner = profile.authenticated ? profile.user?.user_id : 'local';
  const prefix = 'quill.tab.v1:' + owner + ':';
  function read(key) {
    try { return owner ? JSON.parse(sessionStorage.getItem(prefix + key)) : null; } catch { return null; }
  }
  function write(key, data) {
    if (!owner || cleared.has(prefix)) return;
    try { sessionStorage.setItem(prefix + key, JSON.stringify(data)); }
    catch { try { sessionStorage.removeItem(prefix + key); } catch {} }
  }
  function clear() {
    cleared.add(prefix);
    try { for (const key of Object.keys(sessionStorage)) if (key.startsWith(prefix)) sessionStorage.removeItem(key); } catch {}
  }
  return {read, write, clear};
}
