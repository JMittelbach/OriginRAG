function el(tag, text, cls) { const item = document.createElement(tag); if (text !== undefined) item.textContent = text; if (cls) item.className = cls; return item; }

export async function openLibraryImport({notice, changed}) {
  if (document.getElementById('libraryImportDialog')) return;
  const dialog = el('dialog', undefined, 'library-import-dialog'); dialog.id = 'libraryImportDialog';
  const heading = el('div', undefined, 'panel-heading'), close = el('button', 'Close'); close.type = 'button'; close.onclick = () => dialog.close();
  const title = el('h2', 'Add papers to the library'); title.id = 'libraryImportTitle'; dialog.setAttribute('aria-labelledby', title.id); heading.append(title, close);
  const message = el('p', '', 'hint'); message.id = 'libraryImportStatus'; message.setAttribute('role', 'status');
  const files = el('input'); files.type = 'file'; files.accept = '.pdf,application/pdf'; files.multiple = true; files.id = 'libraryImportFiles'; files.setAttribute('aria-label', 'Upload article PDFs');
  const form = el('form', undefined, 'library-find-form'), query = el('input'); query.type = 'search'; query.placeholder = 'Topic, title or author'; query.setAttribute('aria-label', 'Find papers');
  const journals = el('input'); journals.type = 'search'; journals.placeholder = 'Journal name or ISSN (optional)'; journals.setAttribute('aria-label', 'Find journals');
  const journalOptions = el('div', undefined, 'library-journal-options'), search = el('button', 'Search papers', 'primary'); search.type = 'submit';
  const results = el('div', undefined, 'library-find-results'), jobs = el('div', undefined, 'library-import-jobs'); jobs.setAttribute('aria-live', 'polite');
  form.append(query, journals, journalOptions, search);
  dialog.append(heading, el('p', 'Upload PDFs you may legally use, or find publications and import an accessible open-access copy. Papers enter the shared corpus; your personal collections stay private.', 'muted'), files, form, message, jobs, results);
  document.body.append(dialog); dialog.showModal(); query.focus();
  let access, timer, journalTimer, searchId = 0, journalId = 0, closing = false, busy = false;
  const selected = new Map(), tracked = new Set(), completed = new Set(), requests = new Set();
  function headers(extra = {}) {
    const token = document.cookie.split('; ').find(value => value.startsWith('pfempire_csrf='))?.split('=')[1];
    return {...extra, ...(token ? {'X-CSRF-Token': decodeURIComponent(token)} : {}), ...(access?.csrf_token ? {'X-OriginRAG-Local-Token': access.csrf_token} : {})};
  }
  async function request(path, payload, file) {
    const controller = new AbortController(); requests.add(controller);
    try {
      const response = await fetch(path, {signal: controller.signal, method: payload || file ? 'POST' : 'GET',
        headers: headers(file ? {'Content-Type': 'application/pdf', 'X-Paper-Filename': encodeURIComponent(file.name), 'X-Paper-Defer-Sync': '1'} : payload ? {'Content-Type': 'application/json'} : {}),
        body: file || (payload ? JSON.stringify(payload) : undefined)});
      const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Library request failed.'); return data;
    } finally { requests.delete(controller); }
  }
  function failure(error) { if (!closing && error.name !== 'AbortError') { message.textContent = error.message; message.className = 'error'; } }
  async function poll() {
    clearTimeout(timer); if (closing) return;
    try {
      const [imports, library] = await Promise.all([request('/api/paper-imports'), request('/api/library-status')]);
      let active = ['queued', 'running'].includes(library.state);
      jobs.replaceChildren();
      for (const job of imports.jobs || []) {
        if (!tracked.has(job.job_id)) continue;
        jobs.append(el('p', `${job.doi}: ${job.message || job.state}`));
        if (['queued', 'finding', 'downloading', 'indexing'].includes(job.state)) active = true;
        if (job.state === 'imported' && !completed.has(job.job_id)) { completed.add(job.job_id); await changed(); }
      }
      if (library.state === 'error') jobs.append(el('p', library.error || 'Library update failed. Check the uploaded PDF.', 'error'));
      else if (active) jobs.append(el('p', library.stage || 'Updating the library…'));
      if (library.state === 'complete' && library.batch_id && !completed.has(library.batch_id)) { completed.add(library.batch_id); await changed(); jobs.append(el('p', 'Library updated.')); }
      if (active && !closing) timer = setTimeout(poll, 2000);
    } catch (error) { failure(error); }
  }
  files.onchange = async () => {
    if (busy || !access?.enabled) return; busy = true; files.disabled = true;
    try {
      for (const file of files.files) {
        if (!file.name.toLowerCase().endsWith('.pdf') || file.size > 100 * 1024 * 1024) throw new Error('Choose PDFs no larger than 100 MB each.');
        message.textContent = 'Uploading ' + file.name + '…'; await request('/api/library-upload', null, file);
      }
      await request('/api/library-sync', {}); message.textContent = 'Uploaded. Preparing your papers…'; await poll();
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
        add.onclick = async () => {
          add.disabled = true;
          try { const job = await request('/api/paper-import', {doi: item.doi}); tracked.add(job.job_id); status.textContent = 'Import started. Progress appears above.'; await poll(); }
          catch (error) { status.textContent = error.message; add.disabled = !access?.enabled; }
        };
        row.append(add, status); results.append(row);
      }
    } catch (error) { failure(error); }
    finally { if (id === searchId) search.disabled = false; }
  };
  dialog.addEventListener('close', () => {
    closing = true; clearTimeout(timer); clearTimeout(journalTimer); requests.forEach(controller => controller.abort()); dialog.remove();
    changed().catch(error => notice(error.message, true));
  });
  try { access = (await request('/api/status')).library_access; files.disabled = !access?.enabled; if (!access?.enabled) message.textContent = 'Importing requires local access or a signed-in server account.'; }
  catch (error) { files.disabled = true; failure(error); }
}
