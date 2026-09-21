import {requestJSON} from '/assets/workspace/request.mjs';

export function readingStatus({paperId, status = () => {}}) {
  const $ = id => document.getElementById(id);
  const dialog = $('readStatusDialog'), button = $('readStatus');
  let state = null, pending = null, mode = 'mark_read', error = '';
  let loading = false, writing = false, checked = false, needsCheck = false;
  let initialized = false, disposed = false, sequence = 0, lastRead = 0, controller;
  const listeners = [];
  const listen = (target, event, handler) => { target.addEventListener(event, handler); listeners.push(() => target.removeEventListener(event, handler)); };
  const satisfied = (data, intent) => data.is_read === (intent.action === 'mark_read') && (!intent.remove_from_shelf || !data.on_shelf);

  function validate(data) {
    if (!data || data.paper_id !== paperId || typeof data.is_read !== 'boolean' || typeof data.on_shelf !== 'boolean' ||
        !Number.isSafeInteger(data.read_count) || data.read_count < 0 ||
        (data.is_read ? !Number.isFinite(data.read_at) || data.read_at <= 0 || data.read_count < 1 : data.read_at !== null)) {
      throw new Error('Reading status could not be verified. Please reload it before making a change.');
    }
    return data;
  }
  function headers() {
    const token = document.cookie.split('; ').find(item => item.startsWith('pfempire_csrf='))?.slice('pfempire_csrf='.length) || '';
    return {'X-CSRF-Token': decodeURIComponent(token)};
  }
  function render() {
    if (disposed) return;
    button.disabled = writing;
    button.setAttribute('aria-busy', String(loading || writing));
    button.dataset.state = state?.is_read ? 'read' : 'unread';
    if (state?.is_read) {
      const date = new Date(state.read_at).toLocaleDateString('en-GB', {day:'numeric', month:'short', year:'numeric'});
      button.textContent = 'Read · ' + date;
      button.title = 'Marked as read on ' + date + '. Change reading status.';
    } else {
      button.textContent = !state && error ? 'Reading status' : 'Mark as read';
      button.title = !state && error ? 'Reading status unavailable. Open to retry.' : 'Add this paper to your personal reading history';
    }
    const action = pending?.action || mode, unread = action === 'mark_unread';
    $('readStatusTitle').textContent = unread ? 'Mark this paper as unread?' : 'Mark this paper as read?';
    $('readStatusDescription').textContent = unread
      ? 'Remove this paper from your reading history. Your highlights, notes and bookshelf stay unchanged.'
      : 'Keep track of the papers you have finished. Your highlights and notes stay unchanged.';
    $('readStatusRemoveRow').hidden = unread || !state?.on_shelf;
    $('readStatusRemove').disabled = loading || writing || Boolean(pending);
    $('readStatusSubmit').disabled = loading || writing || !state || !checked || needsCheck;
    $('readStatusSubmit').textContent = writing ? 'Saving…' : pending ? 'Try again' : unread ? 'Mark as unread' : 'Mark as read';
    $('readStatusRetry').hidden = !error || writing || loading;
    $('readStatusRetry').textContent = pending ? 'Check saved status' : 'Retry loading';
    $('readStatusMessage').hidden = !error && !loading && !writing;
    $('readStatusMessage').textContent = writing ? 'Saving your reading status…' : loading ? 'Checking your reading status…' : error;
    $('readStatusMessage').classList.toggle('error', Boolean(error) && !loading && !writing);
  }
  function complete(data, intent) {
    state = data; pending = null; error = ''; checked = true; needsCheck = false;
    mode = data.is_read ? 'mark_unread' : 'mark_read';
    if (dialog.open) dialog.close();
    render();
    status(intent.action === 'mark_read'
      ? `Marked as read. ${data.read_count} ${data.read_count === 1 ? 'paper' : 'papers'} finished.`
      : 'Marked as unread. Your bookshelf was left unchanged.');
  }
  function unfinished() {
    return pending?.remove_from_shelf && state?.is_read && state.on_shelf
      ? 'Marked as read, but it is still on your bookshelf. Try again to finish removing it.'
      : 'The change was not confirmed. Check the current status, then try again.';
  }
  async function read() {
    controller?.abort(); controller = new AbortController();
    return validate(await requestJSON('/api/reading?' + new URLSearchParams({paper_id:paperId}), {timeout:12000, signal:controller.signal}));
  }
  async function refresh({force = false} = {}) {
    if (disposed || writing || (!force && (loading || Date.now() - lastRead < 15000))) return state;
    const token = ++sequence;
    loading = true; render();
    try {
      const data = await read();
      if (disposed || token !== sequence) return null;
      state = data; lastRead = Date.now(); checked = true; needsCheck = false;
      if (pending && satisfied(data, pending)) { complete(data, pending); return data; }
      if (!pending) {
        const next = data.is_read ? 'mark_unread' : 'mark_read';
        if (next !== mode || !data.on_shelf) $('readStatusRemove').checked = false;
        mode = next;
      }
      error = pending ? unfinished() : '';
      return data;
    } catch (problem) {
      if (disposed || token !== sequence || problem.name === 'AbortError') return null;
      checked = false; needsCheck = Boolean(pending);
      error = problem.message || 'Reading status is unavailable. Please try again.';
      return null;
    } finally {
      if (!disposed && token === sequence) { loading = false; render(); }
    }
  }
  async function submit() {
    if (disposed || loading || writing || !state || !checked || needsCheck) return;
    if (pending) {
      const previous = pending;
      const fresh = await refresh({force:true});
      if (!fresh || pending !== previous || !checked) return;
    }
    const intent = pending || {action:mode, paper_id:paperId, remove_from_shelf:mode === 'mark_read' && state.on_shelf && $('readStatusRemove').checked};
    pending = intent; writing = true; error = ''; ++sequence; controller?.abort(); render();
    try {
      const data = validate(await requestJSON('/api/reading', {payload:intent, headers:headers(), timeout:20000}));
      if (disposed) return;
      state = data; lastRead = Date.now(); checked = true;
      if (satisfied(data, intent)) complete(data, intent);
      else { error = unfinished(); needsCheck = true; }
    } catch (problem) {
      if (disposed) return;
      error = problem.message || 'The change was not confirmed.';
      needsCheck = true; checked = false;
      try {
        const data = await read();
        if (disposed) return;
        state = data; lastRead = Date.now(); checked = true; needsCheck = false;
        if (satisfied(data, intent)) complete(data, intent);
        else error = unfinished();
      } catch (checkError) {
        if (!disposed) error += ' The saved status could not be checked. Use “Check saved status” before trying again.';
      }
    } finally {
      if (!disposed) { writing = false; render(); }
    }
  }
  function open() {
    if (disposed || writing) return;
    if (!pending) {
      mode = state?.is_read ? 'mark_unread' : 'mark_read';
      $('readStatusRemove').checked = false;
    }
    checked = false;
    dialog.showModal(); render();
    void refresh({force:true});
  }
  function revalidate() {
    if (!document.hidden && !dialog.open && !writing) void refresh();
  }
  async function init() {
    if (initialized || disposed || !paperId || !dialog || !button) return;
    initialized = true; button.hidden = false;
    listen(button, 'click', open);
    listen($('readStatusClose'), 'click', () => dialog.close());
    listen($('readStatusSubmit'), 'click', () => { void submit(); });
    listen($('readStatusRetry'), 'click', () => { void refresh({force:true}); });
    listen(document, 'visibilitychange', revalidate);
    listen(window, 'pageshow', revalidate);
    listen(window, 'online', revalidate);
    await refresh({force:true});
  }
  function destroy() {
    disposed = true; ++sequence; controller?.abort();
    for (const remove of listeners) remove();
  }
  return {init, refresh, destroy};
}
