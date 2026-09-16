function element(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
}

function input(id, placeholder, type = 'text') {
  const result = element('input'); result.id = id; result.type = type;
  result.placeholder = placeholder; result.autocomplete = 'off'; return result;
}

function hasCitation(project, filename, key) {
  const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = new RegExp('^\\s*@\\w+\\s*[({]\\s*' + escaped + '\\s*,', 'm');
  return pattern.test(project.files[filename] || '');
}

function activeTex(source) {
  return source.split('\n').map(line => {
    for (let i = 0; i < line.length; i++) {
      if (line[i] !== '%') continue;
      let slashes = 0; for (let j = i - 1; j >= 0 && line[j] === '\\'; j--) slashes++;
      if (slashes % 2 === 0) return line.slice(0, i);
    }
    return line;
  }).join('\n');
}

function bibliographyConnected(project, filename) {
  const normalise = name => name.trim().replace(/^\.\//, '').replace(/\.bib$/i, '');
  const target = normalise(filename); const queued = [project.main], seen = new Set();
  while (queued.length) {
    const name = queued.pop();
    if (seen.has(name)) continue;
    seen.add(name);
    const source = activeTex(project.files[name] || '');
    for (const match of source.matchAll(/\\(?:bibliography|addbibresource)(?:\[[^\]]*\])?\s*\{([^{}]+)\}/g)) {
      if (match[1].split(',').some(part => normalise(part) === target)) return true;
    }
    for (const match of source.matchAll(/\\(input|include|usepackage|documentclass)(?:\[[^\]]*\])?\s*\{([^{}]+)\}/g)) {
      const suffix = match[1] === 'usepackage' ? '.sty' : match[1] === 'documentclass' ? '.cls' : '.tex';
      for (const file of match[2].split(',')) {
        const clean = file.trim();
        if (!/^[\w./ -]+$/.test(clean)) continue;
        const relative = clean.endsWith(suffix) ? clean : clean + suffix;
        for (const candidate of [relative, name.slice(0, name.lastIndexOf('/') + 1) + relative]) {
          if (candidate in project.files && !seen.has(candidate)) queued.push(candidate);
        }
      }
    }
  }
  return false;
}

export async function openReferences(ctx) {
  const existing = document.getElementById('referencesDialog');
  if (existing?.open) { existing.querySelector('input,select,button')?.focus(); return; }
  existing?.remove();
  if (!document.querySelector('link[data-writing-references]')) {
    const style = element('link'); style.rel = 'stylesheet'; style.href = '/assets/workspace/writing-references.css';
    style.dataset.writingReferences = ''; document.head.append(style);
  }
  const dialog = element('dialog', undefined, 'writing-references'); dialog.id = 'referencesDialog';
  dialog.setAttribute('aria-labelledby', 'referencesTitle');
  const header = element('div', undefined, 'references-heading');
  const heading = element('div'); const title = element('h2', 'Manuscript references'); title.id = 'referencesTitle';
  heading.append(title, element('p', 'Collect papers, insert citations and keep your bibliography in sync.', 'references-muted'));
  const close = element('button', 'Close'); close.type = 'button'; close.id = 'referencesClose'; close.onclick = () => dialog.close();
  header.append(heading, close);
  const error = element('p', '', 'references-error'); error.id = 'referencesError'; error.setAttribute('role', 'alert');
  const layout = element('div', undefined, 'references-layout');
  const sidebar = element('aside', undefined, 'references-sidebar');
  const libraryLabel = element('label', 'Personal library'); const library = element('select'); library.id = 'referencesLibrary';
  libraryLabel.append(library); sidebar.append(libraryLabel, element('p', 'Only your account can see these collections. Linking does not change your manuscript.', 'references-muted'));
  const createForm = element('form', undefined, 'references-create');
  const newName = input('referencesNewName', 'Name your library'); newName.maxLength = 120; newName.required = true;
  newName.setAttribute('aria-label', 'New personal library name');
  const create = element('button', '+ New personal library'); create.id = 'referencesCreate'; create.type = 'submit';
  createForm.append(newName, create); sidebar.append(createForm);
  const targetLabel = element('label', 'Bibliography file'); const filename = input('referencesFilename', 'references.bib'); filename.value = 'references.bib';
  targetLabel.append(filename); sidebar.append(targetLabel);
  const connectLabel = element('label', undefined, 'references-check');
  const connect = input('referencesConnect', '', 'checkbox'); connect.checked = true;
  connectLabel.append(connect, element('span', 'Connect to the main document if it has no bibliography yet')); sidebar.append(connectLabel);
  sidebar.append(element('p', 'Existing entries and citations are kept. Changes are applied only after your confirmation.', 'references-muted'));
  const previewButton = element('button', 'Preview bibliography sync', 'primary'); previewButton.id = 'referencesPreview'; previewButton.type = 'button'; sidebar.append(previewButton);
  const main = element('section', undefined, 'references-main');
  const tabs = element('div', undefined, 'references-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Paper source');
  const personalTab = element('button', 'Your library'); personalTab.id = 'referencesLibraryTab';
  const corpusTab = element('button', 'Search corpus'); corpusTab.id = 'referencesCorpusTab';
  for (const tab of [personalTab, corpusTab]) { tab.type = 'button'; tab.setAttribute('role', 'tab'); tabs.append(tab); }
  const search = input('referencesSearch', 'Filter title, author, year or citation key', 'search'); search.setAttribute('aria-label', 'Search reference papers');
  const count = element('p', '', 'references-muted'); count.id = 'referencesCount'; count.setAttribute('role', 'status');
  const results = element('div', undefined, 'references-results'); results.id = 'referencesResults'; results.setAttribute('role', 'tabpanel');
  for (const tab of [personalTab, corpusTab]) tab.setAttribute('aria-controls', results.id);
  const pagination = element('div', undefined, 'references-pagination');
  const previous = element('button', 'Previous'); previous.type = 'button'; previous.id = 'referencesPrevious';
  const next = element('button', 'Next'); next.type = 'button'; next.id = 'referencesNext'; pagination.append(previous, next);
  const review = element('section', undefined, 'references-review'); review.id = 'referencesReview'; review.hidden = true;
  main.append(tabs, search, count, results, pagination, review); layout.append(sidebar, main);
  dialog.append(header, error, layout); document.body.append(dialog);
  let collections = [], refs = null, tab = 'library', offset = 0, total = 0, sequence = 0, timer;
  let plan = null, pendingKey = '', busy = false, closed = false;
  dialog.addEventListener('close', () => { closed = true; clearTimeout(timer); sequence++; dialog.remove(); });
  dialog.showModal();

  function fail(problem) {
    const message = problem?.message || String(problem);
    if (closed) ctx.notice(message, true); else error.textContent = message;
  }
  async function run(action) {
    if (busy || closed) return;
    busy = true; error.textContent = ''; dialog.setAttribute('aria-busy', 'true');
    const controls = [...dialog.querySelectorAll('button,input,select')].filter(el => el !== close);
    const disabled = controls.map(el => el.disabled); controls.forEach(el => { el.disabled = true; });
    try { await action(); } catch (problem) { fail(problem); }
    finally {
      busy = false; dialog.removeAttribute('aria-busy');
      controls.forEach((el, index) => { el.disabled = disabled[index]; });
      updateAvailability();
    }
  }
  function updateAvailability() {
    previewButton.disabled = !library.value || busy;
    previous.disabled = busy || offset === 0; next.disabled = busy || offset + 20 >= total;
    const apply = review.querySelector('#referencesApply');
    if (apply) apply.disabled = busy || !plan || plan.conflicts.length > 0 || Boolean(pendingKey && !canConnect());
  }
  function invalidate() { plan = null; pendingKey = ''; review.hidden = true; review.replaceChildren(); }
  function showLibraries() {
    library.replaceChildren(new Option('Choose a personal library…', ''));
    for (const item of collections) library.append(new Option(item.name, item.id));
    library.value = ctx.getProject().collection || ''; updateAvailability();
  }
  async function fetchReferences() {
    refs = library.value ? await ctx.api('/api/writing?collection_refs=' + encodeURIComponent(library.value)) : null;
  }
  function setTab(value) {
    tab = value; offset = 0;
    personalTab.setAttribute('aria-selected', String(tab === 'library'));
    corpusTab.setAttribute('aria-selected', String(tab === 'corpus'));
    search.placeholder = tab === 'library' ? 'Filter title, author, year or citation key' : 'Search the corpus by title, author or journal';
    loadResults().catch(fail);
  }
  async function insert(key) {
    const inserted = await ctx.insertCitation(key);
    if (inserted === false) throw new Error('Open a LaTeX source file before inserting a citation.');
    dialog.close(); ctx.notice('Citation inserted. Your source will be saved automatically.');
  }
  async function chooseCitation(key) {
    await ctx.save();
    pendingKey = key;
    await preview();
    if (closed || !plan) return;
    const exists = hasCitation(ctx.getProject(), plan.filename, key) && !plan.added.some(ref => ref.key === key);
    if (exists && bibliographyConnected(ctx.getProject(), plan.filename)) await insert(key);
  }
  function canConnect() {
    return plan && (bibliographyConnected(ctx.getProject(), plan.filename) || Boolean(plan.document_change));
  }
  async function membership(paper, remove = false) {
    if (!library.value) throw new Error('Create or choose a personal library first.');
    const selected = library.value;
    const current = await ctx.api('/api/writing?collection_refs=' + encodeURIComponent(selected));
    await ctx.api('/api/writing', {action: 'collection_paper', id: selected, revision: current.revision, paper_id: paper.paper_id, remove});
    invalidate(); await fetchReferences(); await loadResults();
  }
  function actionButton(label, action, className) {
    const button = element('button', label, className); button.type = 'button'; button.onclick = () => run(action); return button;
  }
  function renderPaper(paper, personal) {
    const row = element('article', undefined, 'references-paper'); row.dataset.paperId = paper.paper_id;
    row.append(element('h3', paper.title || paper.paper_id), element('p', [paper.author, paper.year, paper.journal].filter(Boolean).join(' · '), 'references-muted'));
    if (paper.key) row.append(element('code', paper.key, 'references-key'));
    if (paper.doi) row.append(element('small', 'DOI: ' + paper.doi, 'references-doi'));
    for (const warning of paper.warnings || []) row.append(element('p', warning, 'references-warning'));
    const actions = element('div', undefined, 'references-actions');
    if (personal) {
      actions.append(actionButton('Insert citation', () => chooseCitation(paper.key), 'primary'));
      actions.append(actionButton('Remove from library', () => membership(paper, true)));
    } else {
      const included = refs?.references.some(ref => ref.paper_id === paper.paper_id);
      const add = actionButton(included ? 'In your library' : 'Add to personal library', () => membership(paper));
      add.disabled = Boolean(included); actions.append(add);
    }
    if (paper.local_pdf) {
      const reader = element('a', 'Read paper ↗'); reader.href = '/reader?paper_id=' + encodeURIComponent(paper.paper_id);
      reader.target = '_blank'; reader.rel = 'noopener'; actions.append(reader);
    }
    row.append(actions); return row;
  }
  async function loadResults() {
    const ticket = ++sequence; const query = search.value.trim();
    count.textContent = 'Looking for papers…';
    let data;
    if (tab === 'corpus') data = await ctx.api('/api/library?limit=20&offset=' + offset + '&query=' + encodeURIComponent(query));
    else {
      const found = (refs?.references || []).filter(p => [p.title, p.author, p.year, p.journal, p.doi, p.key].join(' ').toLowerCase().includes(query.toLowerCase()));
      data = {items: found.slice(offset, offset + 20), total: found.length};
    }
    if (closed || ticket !== sequence) return;
    total = data.total;
    count.textContent = `${data.total} papers` + (tab === 'library' && refs?.missing.length ? ` · ${refs.missing.length} no longer present in the corpus` : '');
    results.replaceChildren();
    if (!data.items.length) results.append(element('p', tab === 'library'
      ? (library.value ? 'No papers here yet. Search the corpus to collect references for this manuscript.' : 'Choose a personal library, or create one to begin.')
      : 'No matching papers. Try a title, author or another term.', 'references-empty'));
    for (const paper of data.items) results.append(renderPaper(paper, tab === 'library'));
    previous.disabled = offset === 0; next.disabled = offset + 20 >= data.total;
    pagination.hidden = data.total <= 20;
  }
  async function preview() {
    if (!library.value) throw new Error('Choose a personal library first.');
    await ctx.save();
    const project = ctx.getProject();
    plan = await ctx.api('/api/writing?bib_preview=' + encodeURIComponent(project.id) + '&filename=' + encodeURIComponent(filename.value.trim() || 'references.bib') + '&connect_document=' + (connect.checked ? '1' : '0'));
    if (closed) return;
    review.replaceChildren(); review.hidden = false;
    review.append(element('h3', 'Review bibliography changes'));
    review.append(element('p', `${plan.added.length} added · ${plan.updated.length} updated · ${plan.unchanged.length} unchanged · ${plan.retained.length} existing entries kept`));
    if (pendingKey && canConnect()) review.append(element('p', `Apply the bibliography changes first, then ${pendingKey} will be inserted into your source.`, 'references-pending'));
    else if (pendingKey) review.append(element('p', `The main document is not connected to ${plan.filename}, and its existing bibliography configuration cannot be changed safely here. Choose the bibliography file your document uses, or connect it in your source first. No citation has been inserted.`, 'references-warning references-connection-warning'));
    if (plan.document_change) review.append(element('p', `The bibliography will also be connected in ${plan.document_change.filename}.`));
    else if (plan.connection_needed) review.append(element('p', 'The bibliography is not connected to the main document. Add your preferred bibliography commands when ready.', 'references-warning'));
    if (plan.missing.length) review.append(element('p', `${plan.missing.length} papers are missing from the corpus and cannot supply new metadata. Existing entries are kept.`, 'references-warning'));
    if (plan.conflicts.length) {
      const conflicts = element('div', undefined, 'references-conflicts'); conflicts.setAttribute('role', 'alert');
      conflicts.append(element('strong', 'Some entries have manual changes. Nothing will be overwritten.'));
      for (const conflict of plan.conflicts) conflicts.append(element('p', `${conflict.key}: ${conflict.reason}`));
      conflicts.append(element('p', 'Close this panel and review these entries in the bibliography file before synchronising.'));
      review.append(conflicts);
    }
    const details = element('details'); details.append(element('summary', 'Preview bibliography file'), element('pre', plan.bibtex)); review.append(details);
    const apply = actionButton(pendingKey ? 'Apply sync and insert citation' : 'Apply bibliography sync', applySync, 'primary');
    apply.id = 'referencesApply'; apply.disabled = plan.conflicts.length > 0 || Boolean(pendingKey && !canConnect()); review.append(apply);
    review.scrollIntoView({block: 'nearest', behavior: 'instant'});
  }
  async function applySync() {
    if (!plan || plan.conflicts.length || (pendingKey && !canConnect())) return;
    await ctx.save();
    if (ctx.getProject().revision !== plan.project_revision) throw new Error('Your manuscript changed. Preview the bibliography again before applying.');
    const updated = await ctx.api('/api/writing', {action: 'sync_bibliography', id: plan.project_id,
      revision: plan.project_revision, collection_revision: plan.collection_revision, preview_token: plan.preview_token,
      filename: plan.filename, connect_document: plan.connect_document});
    await ctx.acceptProject(updated);
    const citation = pendingKey, target = plan.filename; invalidate();
    if (citation && hasCitation(updated, target, citation) && bibliographyConnected(updated, target)) await insert(citation);
    else if (citation) throw new Error('Bibliography saved, but its connection to the main document could not be verified. No citation was inserted.');
    else { error.textContent = ''; ctx.notice('Bibliography synced. Manual entries and existing citations were kept.'); await fetchReferences(); await loadResults(); }
  }
  library.onchange = () => run(async () => {
    const selected = library.value;
    try { await ctx.save(); await ctx.linkCollection(selected); }
    finally { library.value = ctx.getProject().collection || ''; }
    invalidate(); offset = 0; await fetchReferences(); await loadResults();
  });
  createForm.onsubmit = event => {
    event.preventDefault(); run(async () => {
      await ctx.save();
      const created = await ctx.api('/api/writing', {action: 'create_collection', name: newName.value.trim()});
      collections.unshift(created); await ctx.linkCollection(created.id); showLibraries();
      newName.value = ''; invalidate(); await fetchReferences(); setTab('library');
    });
  };
  for (const field of [filename, connect]) field.addEventListener('input', invalidate);
  previewButton.onclick = () => run(async () => { pendingKey = ''; await preview(); });
  personalTab.onclick = () => { if (!busy) setTab('library'); };
  corpusTab.onclick = () => { if (!busy) setTab('corpus'); };
  search.oninput = () => { clearTimeout(timer); offset = 0; timer = setTimeout(() => loadResults().catch(fail), 180); };
  previous.onclick = () => { offset = Math.max(0, offset - 20); loadResults().catch(fail); };
  next.onclick = () => { offset += 20; loadResults().catch(fail); };
  await run(async () => {
    const snapshot = await ctx.api('/api/writing'); collections = snapshot.collections;
    showLibraries(); await fetchReferences(); setTab('library');
  });
  if (!closed) (library.value ? search : library).focus();
}
