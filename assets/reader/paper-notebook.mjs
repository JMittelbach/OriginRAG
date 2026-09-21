import {requestJSON} from '/assets/workspace/request.mjs';

const MAX_CHARACTERS = 50000;
const AUTOSAVE_DELAY = 1200;
const length = text => Array.from(text).length;

// Drafts deliberately live only in this page, never in shared browser storage.
export function paperNotebook({paperId, status = () => {}}) {
  const $ = id => document.getElementById(id);
  const section = $('paperNotebook'), input = $('paperNotes');
  const listeners = [], controller = new AbortController();
  let saved = {paper_id:paperId, text:'', revision:0, updated_at:null};
  let ready = false, disposed = false, initialized = false, loading = false, composing = false;
  let timer, initialLoad, loadPromise, writePromise, conflict = null, uncertain = null;
  let error = '', paused = false, sessionToken;
  const dirty = () => Boolean(uncertain || conflict || (ready && input.value !== saved.text));
  const saving = () => Boolean(writePromise);
  const listen = (target, event, handler) => {
    if (!target) return;
    target.addEventListener(event, handler);
    listeners.push(() => target.removeEventListener(event, handler));
  };
  function csrf() {
    const value = document.cookie.split(';').map(item => item.trim())
      .find(item => item.startsWith('pfempire_csrf='))?.slice('pfempire_csrf='.length) || '';
    try { return decodeURIComponent(value); } catch { return value; }
  }
  function checkSession(expected = sessionToken) {
    if (disposed) throw new Error('The notebook has been closed.');
    if (csrf() !== expected) {
      throw new Error('Your sign-in changed. Export your draft before reloading saved notes for the current account.');
    }
  }
  function validate(data) {
    if (!data || data.paper_id !== paperId || typeof data.text !== 'string' ||
        length(data.text) > MAX_CHARACTERS || !Number.isSafeInteger(data.revision) || data.revision < 0 ||
        !(data.updated_at === null || (Number.isFinite(data.updated_at) && data.updated_at >= 0))) {
      throw new Error('The saved notebook could not be verified. Your draft has been kept.');
    }
    return data;
  }
  async function read(expected = sessionToken) {
    checkSession(expected);
    const data = validate(await requestJSON('/api/reader/notebook?' + new URLSearchParams({paper_id:paperId}), {
      timeout:15000, signal:controller.signal,
    }));
    checkSession(expected);
    return data;
  }
  function render() {
    if (disposed || !section || !input) return;
    const busy = loading || saving(), changed = dirty();
    section.setAttribute('aria-busy', String(busy));
    input.disabled = !ready || loading;
    $('paperNotesCount').textContent = length(input.value).toLocaleString('en-GB') + ' / 50,000 characters';
    const state = loading ? 'loading' : saving() ? 'saving' : conflict ? 'conflict'
      : error ? 'error' : changed ? 'dirty' : 'saved';
    $('paperNotesState').dataset.state = state;
    $('paperNotesState').textContent = loading ? 'Loading saved notes…' : saving() ? 'Saving…'
      : conflict ? 'Saved notes changed in another tab. Your draft is still here. Compare the versions below.'
      : error || (changed ? 'Unsaved changes' : saved.revision ? 'Saved' : 'No notes yet');
    $('paperNotesSave').disabled = !ready || busy || Boolean(conflict) || (!changed && !error);
    $('paperNotesSave').textContent = saving() ? 'Saving…' : error ? 'Retry save' : 'Save notes';
    $('paperNotesReload').disabled = busy;
    $('paperNotesReload').textContent = !ready ? 'Retry loading' : 'Reload saved';
    $('paperNotesExport').disabled = !ready && !input.value;
    if ($('paperNotesConflict')) $('paperNotesConflict').hidden = !conflict;
    if ($('paperNotesSavedVersion')) $('paperNotesSavedVersion').value = conflict?.text || '';
    if ($('paperNotesUseSaved')) $('paperNotesUseSaved').disabled = busy || !conflict;
    if ($('paperNotesKeepDraft')) $('paperNotesKeepDraft').disabled = busy || !conflict;
  }
  function accept(data, replace = false) {
    saved = data; ready = true; conflict = null; uncertain = null; error = ''; paused = false;
    if (replace) input.value = data.text;
  }
  function fail(problem) {
    if (disposed) return;
    paused = true;
    error = (problem.message || 'The save was not confirmed.') + ' Your draft stays in this tab; export a copy before leaving.';
    status(error, true); render();
  }
  function stopTimer() { clearTimeout(timer); timer = undefined; }
  function schedule() {
    stopTimer();
    if (disposed || !ready || paused || conflict || composing || saving() || loading || !dirty()) return;
    timer = setTimeout(() => { timer = undefined; void save().catch(() => {}); }, AUTOSAVE_DELAY);
  }
  // Only a GET may resolve an uncertain acknowledgement. Never blindly retry a POST.
  async function reconcile(attempt, forceConflict = false) {
    const latest = await read();
    if (latest.text === attempt?.text || latest.text === input.value) {
      accept(latest);
      return true;
    }
    if (forceConflict || latest.revision !== saved.revision || latest.text !== saved.text) {
      conflict = latest;
      uncertain = null;
      throw new Error('Saved notes changed in another tab. Compare the saved version with your draft before choosing which to keep.');
    }
    return false;
  }
  async function drain() {
    if (loadPromise) await loadPromise;
    checkSession();
    if (!ready) throw new Error('Load your saved notebook before saving.');
    if (conflict) throw new Error('Resolve the notebook conflict before saving or leaving.');
    if (uncertain || error) {
      await reconcile(uncertain);
      uncertain = null; error = ''; paused = false;
    }
    while (input.value !== saved.text) {
      checkSession();
      if (length(input.value) > MAX_CHARACTERS) throw new Error('Paper notes can contain at most 50,000 characters.');
      const attempt = {paper_id:paperId, text:input.value, revision:saved.revision};
      uncertain = attempt;
      try {
        const data = validate(await requestJSON('/api/reader/notebook', {
          payload:attempt, headers:{'X-CSRF-Token':sessionToken}, timeout:30000, signal:controller.signal,
        }));
        checkSession();
        if (data.text !== attempt.text || data.revision <= attempt.revision) {
          throw new Error('The server did not confirm this version of your notes.');
        }
        accept(data);
      } catch (problem) {
        checkSession();
        paused = true;
        try {
          if (await reconcile(attempt, problem.status === 409)) continue;
        } catch (checkError) {
          if (conflict) throw checkError;
          throw new Error((problem.message || 'Save not confirmed.') + ' The saved version could not be checked. Retry save when connected.');
        }
        throw problem;
      }
      // Text entered during this request is kept, then saved against the confirmed revision.
    }
    error = ''; paused = false;
  }
  function save() {
    stopTimer();
    if (writePromise) return writePromise;
    if (disposed) return Promise.reject(new Error('The notebook has been closed.'));
    writePromise = drain().catch(problem => { fail(problem); throw problem; }).finally(() => {
      writePromise = null; render();
    });
    render();
    return writePromise;
  }
  function reload() {
    if (disposed || loading || saving()) return loadPromise || Promise.resolve();
    if (dirty() && !confirm('Replace your unsaved draft with the latest saved notes? Export a copy first if you want to keep both.')) return Promise.resolve();
    stopTimer(); loading = true; render();
    const expected = csrf();
    loadPromise = (async () => {
      try {
        const data = await read(expected);
        sessionToken = expected;
        accept(data, true);
      } catch (problem) { fail(problem); }
      finally { loading = false; render(); }
    })();
    return loadPromise;
  }
  async function keepDraft() {
    if (disposed || loading || saving() || !conflict) return;
    const reviewed = conflict;
    stopTimer(); loading = true; render();
    let canSave = false;
    try {
      const latest = await read();
      if (latest.revision !== reviewed.revision || latest.text !== reviewed.text) {
        conflict = latest;
        status('Saved notes changed again. Review the newer saved version before keeping your draft.', true);
      } else {
        accept(latest);
        canSave = true;
      }
    } catch (problem) { fail(problem); }
    finally { loading = false; render(); }
    if (canSave) await save().catch(() => {});
  }
  function exportDraft() {
    const url = URL.createObjectURL(new Blob([input.value], {type:'text/plain;charset=utf-8'}));
    const link = document.createElement('a');
    link.href = url; link.download = 'originrag-paper-notes-' + String(paperId).replace(/[^a-z0-9_-]/gi, '_') + '.txt';
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    status('Exported the current notebook text, including unsaved changes.');
  }
  function init() {
    if (initialized || disposed || !paperId || !section || !input) return initialLoad || Promise.resolve();
    initialized = true; sessionToken = csrf();
    input.maxLength = MAX_CHARACTERS;
    listen(input, 'input', () => { render(); schedule(); });
    listen(input, 'compositionstart', () => { composing = true; stopTimer(); });
    listen(input, 'compositionend', () => { composing = false; schedule(); });
    listen($('paperNotesSave'), 'click', () => { void save().catch(() => {}); });
    listen($('paperNotesReload'), 'click', () => { void reload(); });
    listen($('paperNotesExport'), 'click', exportDraft);
    listen($('paperNotesUseSaved'), 'click', () => { void reload(); });
    listen($('paperNotesKeepDraft'), 'click', () => { void keepDraft(); });
    initialLoad = reload();
    return initialLoad;
  }
  function destroy() {
    disposed = true; stopTimer(); controller.abort();
    for (const remove of listeners) remove();
  }
  return {init, dirty, saving, save, destroy};
}
