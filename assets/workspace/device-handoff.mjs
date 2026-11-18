import {getAppProfile} from './profile.mjs';

function resumePath(url, home) {
  const path = url.pathname;
  if (path === '/write' || path === '/folio') {
    const ids = url.searchParams.getAll('id');
    return ids.length === 1 && /^[0-9a-f]{32}$/.test(ids[0]) ? path + '?id=' + ids[0] : path;
  }
  return ['/chat', '/library', '/bookshelf'].includes(path) ? path : home;
}

export function bindDeviceHandoff(panel) {
  if (!panel) return;
  const host = panel.querySelector('#handoffHost'), hint = panel.querySelector('#handoffHint');
  const steps = panel.querySelector('#handoffSteps');
  let profile;
  function render() {
    const target = host.value.trim();
    steps.hidden = true;
    hint.textContent = '';
    if (!target) return;
    if (!/^(?:[a-z0-9][a-z0-9._-]*@)?[a-z0-9][a-z0-9._-]*$/i.test(target)
        || /^(?:localhost\.?|127\..*|0\.0\.0\.0)$/i.test(target.split('@').pop())) {
      hint.textContent = 'Use the SSH name or address of the computer running Origin, not a web address or localhost.';
      return;
    }
    const port = profile?.web_port;
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      hint.textContent = profile === undefined ? 'Reading workspace port…' : 'Workspace port unavailable. Reopen this section to retry.';
      return;
    }
    panel.querySelector('#handoffCommand').value = `ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:${port}:127.0.0.1:${port} ${target}`;
    panel.querySelector('#handoffUrl').value = 'http://127.0.0.1:' + port + resumePath(new URL(location.href), profile.home);
    steps.hidden = false;
  }
  panel.addEventListener('toggle', async () => {
    if (!panel.open) return;
    try { profile = await getAppProfile(); } catch { profile = null; }
    render();
  });
  host.addEventListener('input', render);
  panel.closest('dialog').addEventListener('close', () => { panel.open = false; });
  for (const button of steps.querySelectorAll('[data-copy]')) {
    button.onclick = async () => {
      const field = panel.querySelector('#' + button.dataset.copy);
      try {
        await navigator.clipboard.writeText(field.value);
        hint.textContent = 'Copied.';
      } catch {
        field.focus();
        field.select();
        hint.textContent = 'Text selected. Use your usual copy shortcut.';
      }
    };
  }
}
