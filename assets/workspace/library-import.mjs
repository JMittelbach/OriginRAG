import {requestJSON} from './request.mjs';

function el(tag, text, cls) { const item = document.createElement(tag); if (text !== undefined) item.textContent = text; if (cls) item.className = cls; return item; }

export async function openLibraryImport({notice, changed}) {
  if (document.getElementById('libraryImportDialog')) return;
  const dialog = el('dialog', undefined, 'library-import-dialog'); dialog.id = 'libraryImportDialog';
  const heading = el('div', undefined, 'panel-heading'), close = el('button', 'Close'); close.type = 'button'; close.onclick = () => dialog.close();
  const title = el('h2', 'Add papers to the library'); title.id = 'libraryImportTitle'; dialog.setAttribute('aria-labelledby', title.id); heading.append(title, close);
  const message = el('p', '', 'hint'); message.id = 'libraryImportStatus'; message.setAttribute('role', 'status');
  const files = el('input'); files.type = 'file'; files.accept = '.pdf,application/pdf'; files.multiple = true; files.disabled = true; files.id = 'libraryImportFiles'; files.setAttribute('aria-label', 'Upload article PDFs');
  const form = el('form', undefined, 'library-find-form'), query = el('input'); query.type = 'search'; query.placeholder = 'Topic, title or author'; query.setAttribute('aria-label', 'Find papers');
  const journals = el('input'); journals.type = 'search'; journals.placeholder = 'Journal name or ISSN (optional)'; journals.setAttribute('aria-label', 'Find journals');
  const journalOptions = el('div', undefined, 'library-journal-options'), search = el('button', 'Search papers', 'primary'); search.type = 'submit';
  const results = el('div', undefined, 'library-find-results'), jobs = el('div', undefined, 'library-import-jobs'); jobs.setAttribute('aria-live', 'polite');
  const refresh = el('button', 'Refresh progress'), sync = el('button', 'Resume library update');
  refresh.type = sync.type = 'button'; sync.hidden = true;
  const progressActions = el('div', undefined, 'import-actions'); progressActions.append(refresh, sync);
  form.append(query, journals, journalOptions, search);
  dialog.append(heading, el('p', 'Upload PDFs, or find an open-access copy online. You can close this panel once your files have uploaded; processing continues in the background.', 'muted'), files, form, message, jobs, progressActions, results);
  document.body.append(dialog); dialog.showModal(); query.focus();
  let access, timer, journalTimer, searchId = 0, journalId = 0, closing = false, busy = false, polling = false, pollAgain = false, failures = 0, restored = false;
  const selected = new Map(), tracked = new Set(), completed = new Set(), requests = new Set(), controls = new Map();
  const activeStates = ['queued', 'finding', 'downloading', 'indexing'];
  function headers(extra = {}) {
    const token = document.cookie.split('; ').find(value => value.startsWith('pfempire_csrf='))?.split('=')[1];
    return {...extra, ...(token ? {'X-CSRF-Token': decodeURIComponent(token)} : {}), ...(access?.csrf_token ? {'X-OriginRAG-Local-Token': access.csrf_token} : {})};
  }
  async function request(path, payload, file) {
    const controller = new AbortController();
    if (!payload && !file) requests.add(controller);
    try {
      return await requestJSON(path, {signal: controller.signal,
        headers: headers(file ? {'Content-Type': 'application/pdf', 'X-Paper-Filename': encodeURIComponent(file.name), 'X-Paper-Defer-Sync': '1'} : payload ? {'Content-Type': 'application/json'} : {}),
        payload: payload || undefined, body: file || undefined, timeout: file ? 120000 : 30000});
    } finally { requests.delete(controller); }
  }
  function failure(error) { if (!closing && error.name !== 'AbortError') { message.textContent = error.message; message.className = 'error'; } }
  async function poll() {
    clearTimeout(timer); if (closing) return;
    if (polling) { pollAgain = true; return; }
    polling = true; refresh.disabled = true; let next = 0, changedLibrary = false;
    try {
      const [imports, library] = await Promise.all([request('/api/paper-imports'), request('/api/library-status')]);
      if (closing) return;
      const reconnected = failures > 0; failures = 0;
      if (reconnected) { message.className = 'hint'; message.textContent = 'Connected. Import progress is up to date.'; }
      if (!restored) { for (const job of (imports.jobs || []).slice(-8)) tracked.add(job.job_id); restored = true; }
      let active = ['queued', 'running'].includes(library.state);
      jobs.replaceChildren();
      for (const job of imports.jobs || []) {
        if (activeStates.includes(job.state)) tracked.add(job.job_id);
        if (!tracked.has(job.job_id)) continue;
        const row = el('div', undefined, 'import-job'); row.append(el('p', `${job.doi}: ${job.message || job.state}`)); jobs.append(row);
        const running = activeStates.includes(job.state);
        const control = controls.get(job.job_id)?.add.isConnected ? controls.get(job.job_id) : null;
        if (!control) controls.delete(job.job_id);
        if (running) active = true;
        if (control) {
          control.status.textContent = job.message || job.state;
          control.add.textContent = running ? 'Importing…' : job.state === 'imported' ? 'Added to library' : job.state === 'duplicate' ? 'Check Library' : 'Retry import';
          control.add.disabled = running || job.state === 'imported' || job.state === 'duplicate' || !access?.enabled;
        }
        if (!running && !completed.has(job.job_id)) { completed.add(job.job_id); if (job.state === 'imported' || job.state === 'duplicate') changedLibrary = true; }
        if (['failed', 'error', 'review_required'].includes(job.state)) {
          row.classList.add('error');
          if (!control && access?.enabled) {
            const retry = el('button', 'Retry import'); retry.type = 'button';
            retry.onclick = () => startImport(job.doi, retry, row.querySelector('p'));
            row.append(retry);
          }
        }
      }
      sync.hidden = !access?.enabled || active || (library.state !== 'error' && !library.pending_files);
      if (library.state === 'error') jobs.append(el('p', library.error || 'Library update failed. Your uploaded files have been kept.', 'error'));
      else if (['queued', 'running'].includes(library.state)) {
        jobs.append(el('p', library.stage || 'Updating the library…'));
        const progress = el('progress', undefined, 'import-progress'); progress.max = 100; progress.value = library.progress || 0;
        progress.setAttribute('aria-label', 'Library update progress'); jobs.append(progress);
      }
      if (['complete', 'idle'].includes(library.state) && library.batch_id) {
        const counts = [['imported', 'added'], ['duplicates', 'already present'], ['review_required', 'need review'], ['rejected', 'not accepted']]
          .filter(([key]) => library[key] > 0).map(([key, label]) => `${library[key]} ${label}`);
        jobs.append(el('p', 'Library updated' + (counts.length ? ': ' + counts.join(' · ') : '.')));
        if (!completed.has(library.batch_id)) { completed.add(library.batch_id); changedLibrary = true; }
      }
      if (active) next = 2000;
    } catch (error) {
      if (error.retryable && !closing) {
        next = Math.min(15000, 2000 * 2 ** Math.min(failures++, 3));
        message.textContent = 'Progress is temporarily unavailable. Reconnecting… You do not need to add the papers again.'; message.className = 'hint';
      } else failure(error);
    } finally {
      polling = false; refresh.disabled = false;
      if (!closing && (next || pollAgain)) timer = setTimeout(poll, pollAgain ? 0 : next);
      pollAgain = false;
    }
    if (changedLibrary && !closing) { try { await changed(); } catch (error) { notice(error.message, true); } }
  }
  refresh.onclick = poll;
  sync.onclick = async () => {
    if (sync.disabled) return; sync.disabled = true;
    try { await request('/api/library-sync', {}); message.textContent = 'Library update resumed.'; await poll(); }
    catch (error) { failure(error); } finally { sync.disabled = false; }
  };
  async function startImport(doi, add, status) {
    if (add.disabled || closing) return;
    add.disabled = true; add.textContent = 'Starting import…';
    try {
      const job = await request('/api/paper-import', {doi}); tracked.add(job.job_id); controls.set(job.job_id, {add, status});
      status.textContent = 'Import started. You can keep browsing.'; await poll();
    } catch (error) { status.textContent = error.message; add.textContent = 'Retry import'; add.disabled = !access?.enabled; }
  }
  files.onchange = async () => {
    if (busy || !access?.enabled) return; busy = true; files.disabled = true;
    let uploaded = 0, uploadError;
    try {
      const batch = [...files.files];
      if (!batch.length || batch.length > 20) throw new Error('Choose between 1 and 20 PDFs at a time.');
      if (batch.some(file => !file.name.toLowerCase().endsWith('.pdf') || !file.size || file.size > 100 * 1024 * 1024)) throw new Error('Choose non-empty PDFs no larger than 100 MB each. No files were uploaded.');
      message.className = 'hint';
      for (const [index, file] of batch.entries()) {
        message.textContent = `Uploading ${index + 1} of ${batch.length}: ${file.name}…`;
        try { await request('/api/library-upload', null, file); uploaded++; }
        catch (error) { uploadError = error; break; }
      }
      if (uploaded) {
        sync.hidden = false;
        await request('/api/library-sync', {}); sync.hidden = true;
        message.textContent = `${uploaded} PDF${uploaded === 1 ? '' : 's'} uploaded. Preparing your papers…`;
        if (closing) await changed(); else await poll();
      }
      if (uploadError) throw new Error(uploadError.message + (uploaded ? ' Earlier uploads are being processed.' : ''));
    } catch (error) { failure(error); }
    finally { busy = false; files.disabled = !access?.enabled; files.value = ''; }
  };
  journals.oninput = () => {
    clearTimeout(journalTimer); const id = ++journalId;
    if (journals.value.trim().length < 2) return;
    journalTimer = setTimeout(async () => {
      try {
        const data = await request('/api/discover-journals?query=' + encodeURIComponent(journals.value.trim()));
        if (id !== journalId || closing) return;
        const all = new Map([...selected, ...(data.items || []).map(item => [item.issns.join(','), item])]); journalOptions.replaceChildren();
        for (const [key, item] of all) {
          const label = el('label'), input = el('input'); input.type = 'checkbox'; input.checked = selected.has(key);
          input.onchange = () => { if (input.checked) { if (selected.size >= 5) { input.checked = false; message.textContent = 'Choose up to five journals.'; return; } selected.set(key, item); } else selected.delete(key); };
          label.append(input, el('span', item.title + ' · ' + key)); journalOptions.append(label);
        }
      } catch (error) { failure(error); }
    }, 350);
  };
  form.onsubmit = async event => {
    event.preventDefault();
    if (!query.value.trim() && !selected.size) { message.textContent = 'Enter a topic or select a journal.'; return; }
    if (journals.value.trim() && !selected.size) { message.textContent = 'Tick a matching journal, or clear the journal field.'; return; }
    const id = ++searchId; search.disabled = true; message.className = 'hint'; message.textContent = 'Searching publications…';
    try {
      const params = new URLSearchParams({query: query.value.trim(), kind: 'journal-article', order: 'relevance'});
      for (const issn of new Set([...selected.values()].flatMap(item => item.issns))) params.append('issn', issn);
      const data = await request('/api/discover-papers?' + params);
      if (closing || id !== searchId) return;
      results.replaceChildren(); message.textContent = `${data.items.length} publications found`;
      for (const item of data.items) {
        const row = el('article', undefined, 'paper'), add = el('button', item.in_library ? 'Check / add PDF' : 'Add to library'); add.type = 'button'; add.disabled = !access?.enabled;
        row.append(el('h3', item.title), el('p', [item.author, item.year, item.journal].filter(Boolean).join(' · ')));
        const status = el('p', '', 'hint');
        add.onclick = () => startImport(item.doi, add, status);
        row.append(add, status); results.append(row);
      }
    } catch (error) { failure(error); }
    finally { if (id === searchId) search.disabled = false; }
  };
  dialog.addEventListener('close', () => {
    closing = true; clearTimeout(timer); clearTimeout(journalTimer); requests.forEach(controller => controller.abort()); dialog.remove();
    changed().catch(error => notice(error.message, true));
  });
  try { access = (await request('/api/library-access')).library_access; files.disabled = !access?.enabled; if (!access?.enabled) message.textContent = 'Importing requires local access or a signed-in server account.'; await poll(); }
  catch (error) { files.disabled = true; failure(error); }
}
