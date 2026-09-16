import {renderTemplates, openTemplateGallery} from './writing-templates.mjs';
import {openReferences} from './writing-references.mjs';
import {createPDFPreview} from './pdf-preview.mjs';
import {openLibraryImport} from './library-import.mjs';
import {collaborationStyle, snapshotProject, rebaseDraft, openSharing, openProjectCitations} from './writing-collaboration.mjs';

const $ = id => document.getElementById(id);
const content = $('content');
const mode = location.pathname;
let sourceEditor, writerLayout, outlineTimer, diffTextChanges, serverProject, syncTimer, syncBusy = false, syncFailures = 0;
const textTypes = /\.(tex|bib|sty|cls|bst|bbl|txt|md|csv)$/i;
let state, project, activeFile, editVersion = 0, savedVersion = 0, saving = null;
let appProfile = {profile: 'full', features: {rag: true, write: true}};
let saveTimer, compileTimer, compiling = false, saveBlocked = false, pdfURL = '', pdfPreview;
function node(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function notice(message, error = false) { $('notice').textContent = message; $('notice').classList.toggle('error', error); }
async function api(path = '/api/writing', payload) {
  const headers = new Headers();
  const token = document.cookie.split('; ').find(v => v.startsWith('pfempire_csrf='))?.split('=')[1];
  if (token) headers.set('X-CSRF-Token', decodeURIComponent(token));
  if (payload) headers.set('Content-Type', 'application/json');
  const r = await fetch(path, {method: payload ? 'POST' : 'GET', headers, body: payload ? JSON.stringify(payload) : undefined});
  const data = await r.json();
  if (!r.ok) { const error = new Error(data.error || `Request failed (${r.status}).`); error.status = r.status; throw error; }
  return data;
}
function button(text, fn, cls) { const b = node('button', text, cls); b.type = 'button'; b.onclick = () => Promise.resolve().then(fn).catch(e => notice(e.message, true)); return b; }
function link(text, href, cls) { const a = node('a', text, cls); a.href = href; return a; }
function download(data, name, type = 'text/plain') { const u = URL.createObjectURL(new Blob([data], {type})); const a = link('', u); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(u), 30000); }
async function refresh() { state = await api(); }
function heading(title, description) {
  const h = node('div', undefined, 'section-head'); h.append(node('h1', title)); content.append(h);
  if (description) content.append(node('p', description, 'muted')); return h;
}
function askName(title, action) {
  const dialog = $('createDialog'); $('createTitle').textContent = title; $('newName').value = ''; $('createError').textContent = '';
  $('createForm').onsubmit = async e => {
    e.preventDefault(); const submit = e.submitter; submit.disabled = true;
    try { await action($('newName').value); dialog.close(); } catch (error) { $('createError').textContent = error.message; }
    finally { submit.disabled = false; }
  };
  dialog.showModal(); $('newName').focus();
}
$('cancelCreate').onclick = () => $('createDialog').close();
for (const a of document.querySelectorAll('nav a')) a.classList.toggle('active', a.pathname === mode);

function home() {
  content.innerHTML = '<div class="intro"><p class="eyebrow">YOUR RESEARCH, TOGETHER</p><h1>From a first question<br>to the final manuscript.</h1><p class="muted">A place to explore, collect, read and write.</p></div><div class="cards" id="modules"></div>';
  [['01', 'OriginRAG', 'Ask questions of your corpus and follow the evidence.', '/chat'],
   ['02', 'Bookshelf', 'Your current reads, highlights and thoughts.', '/bookshelf'],
   ['03', 'Library', 'Explore the corpus. Gather papers into personal project collections.', '/library'],
   ['04', 'OriginWrite', 'Your LaTeX manuscript, source and PDF side by side.', '/write']].filter(item => (item[3] !== '/chat' || appProfile.features.rag) && (item[3] !== '/write' || appProfile.features.write)).forEach(([num, name, desc, href]) => {
    const card = link('', href, 'card'); card.append(node('span', num, 'number'), node('h2', name), node('p', desc)); $('modules').append(card);
  });
}

async function shelf() {
  heading('Your bookshelf', 'A little space for the papers you are working with.');
  const books = node('div', undefined, 'shelf'); content.append(books);
  const data = await api('/api/shelf');
  if (!data.items.length) books.append(node('p', 'Your shelf is waiting. Save a paper from the Library to begin.'));
  for (const paper of data.items) {
    const b = node('article', undefined, 'book'); b.append(node('h3', paper.title), node('small', [paper.author, paper.year].filter(Boolean).join(' · ')));
    if (paper.available) b.append(link('Open reader ↗', '/reader?paper_id=' + encodeURIComponent(paper.paper_id)));
    b.append(button('Remove from shelf', async () => { await api('/api/shelf', {action: 'remove', paper_id: paper.paper_id}); content.replaceChildren(); await shelf(); }));
    books.append(b);
  }
}

async function library() {
  const h = heading('Library', 'One shared corpus. Personal collections for each project.');
  h.append(button('+ Collection', () => askName('New collection', async name => { await api('/api/writing', {action: 'create_collection', name}); await refresh(); content.replaceChildren(); await library(); })));
  h.append(button('Find or import papers', () => openLibraryImport({notice, changed: () => load()})));
  content.insertAdjacentHTML('beforeend', '<div class="library-layout"><aside><p class="eyebrow">MY COLLECTIONS</p><div id="collections" class="collection-list"></div></aside><section><form id="searchForm" class="search"><input id="query" type="search" placeholder="Search title, author, journal…" aria-label="Search library"><button>Search</button></form><p id="libraryCount" class="muted"></p><div id="papers"></div><div class="actions"><button id="previousPapers">Previous</button><button id="nextPapers">Next</button></div></section></div>');
  const params = new URLSearchParams(location.search);
  let selected = state.collections.some(c => c.id === params.get('collection')) ? params.get('collection') : '', offset = 0, sequence = 0;
  $('query').value = params.get('query') || '';
  const tools = node('div', undefined, 'library-tools'); $('libraryCount').after(tools);
  const collectionButtons = [];
  for (const c of [{id: '', name: 'All papers'}, ...state.collections]) {
    const b = button(c.name, async () => { selected = c.id; offset = 0; history.replaceState(null, '', '/library' + (selected ? '?collection=' + selected : '')); collectionButtons.forEach(([id, el]) => el.setAttribute('aria-pressed', String(id === selected))); await load(); });
    b.setAttribute('aria-pressed', String(c.id === selected)); collectionButtons.push([c.id, b]); $('collections').append(b);
  }
  async function load() {
    const current = ++sequence; const q = $('query').value.trim();
    let data, collection;
    if (selected) {
      collection = await api('/api/writing?id=' + selected + '&kind=collections');
      const items = collection.items.filter(p => [p.title, p.author, p.year, p.journal, p.doi].join(' ').toLowerCase().includes(q.toLowerCase()));
      data = {items: items.slice(offset, offset + 40), total: items.length};
    } else data = await api('/api/library?limit=40&offset=' + offset + '&query=' + encodeURIComponent(q));
    if (current !== sequence) return;
    tools.replaceChildren();
    if (collection) {
      tools.append(node('p', collection.name + ' · private to your account', 'muted'));
      tools.append(button('Export BibTeX', async () => { const result = await api('/api/writing?collection_refs=' + selected); download(result.bibtex, 'references.bib'); }));
      tools.append(button('Rename', () => askName('Rename personal library', async name => { await api('/api/writing', {action: 'rename_collection', id: selected, revision: collection.revision, name}); await refresh(); content.replaceChildren(); await library(); })));
      if (appProfile.features.write) tools.append(button('Write with this library', () => newManuscript({id: 'article'}, selected)));
    }
    $('papers').replaceChildren(); $('libraryCount').textContent = `${data.total} papers`; $('previousPapers').disabled = offset === 0; $('nextPapers').disabled = offset + 40 >= data.total;
    for (const p of data.items) {
      const row = node('article', undefined, 'paper'); row.append(node('h3', p.title), node('p', [p.author, p.year, p.journal].filter(Boolean).join(' · ')));
      const actions = node('div', undefined, 'actions');
      if (p.local_pdf || p.pdf_url) actions.append(link('Read PDF', '/reader?paper_id=' + encodeURIComponent(p.paper_id), 'button'));
      actions.append(button('Save to shelf', async () => { await api('/api/shelf', {action: 'add', paper_id: p.paper_id}); notice('Saved to your bookshelf.'); }));
      const pick = node('select'); pick.setAttribute('aria-label', 'Destination collection'); pick.append(new Option('Add to collection…', ''));
      state.collections.forEach(c => pick.append(new Option(c.name, c.id)));
      pick.onchange = async () => { if (!pick.value) return; pick.disabled = true; try {
        const target = await api('/api/writing?id=' + pick.value + '&kind=collections');
        await api('/api/writing', {action: 'collection_paper', id: target.id, revision: target.revision, paper_id: p.paper_id}); notice('Added to your collection.'); if (selected) await load();
      } catch (e) { notice(e.message, true); } finally { pick.value = ''; pick.disabled = false; } };
      actions.append(pick);
      if (selected) actions.append(button('Remove from collection', async () => { await api('/api/writing', {action: 'collection_paper', id: selected, revision: collection.revision, paper_id: p.paper_id, remove: true}); await load(); }));
      row.append(actions); $('papers').append(row);
    }
  }
  $('searchForm').onsubmit = e => { e.preventDefault(); offset = 0; load().catch(e => notice(e.message, true)); };
  $('previousPapers').onclick = () => { offset = Math.max(0, offset - 40); load().catch(e => notice(e.message, true)); };
  $('nextPapers').onclick = () => { offset += 40; load().catch(e => notice(e.message, true)); };
  await load();
}

function newManuscript(template = {id: 'article'}, collection = '') {
  askName(template.name ? `New manuscript · ${template.name}` : 'New manuscript', async name => {
    if (project) await save();
    const created = await api('/api/writing', {action: 'create_project', name, template: template.id, collection});
    location.href = '/write?id=' + created.id;
  });
}
async function projects() {
  collaborationStyle();
  content.classList.add('write-home');
  const h = node('div', undefined, 'write-heading'), title = node('div'), actions = node('div', undefined, 'desk-actions');
  title.append(node('p', 'YOUR WRITING DESK', 'eyebrow'), node('h1', 'OriginWrite'), node('p', 'Your manuscripts, templates and personal libraries.', 'muted'));
  actions.append(button('+ New manuscript', () => newManuscript(), 'primary')); h.append(title, actions); content.append(h);
  const upload = node('input'); upload.type = 'file'; upload.accept = '.zip'; upload.hidden = true; content.append(upload);
  actions.append(button('Import LaTeX ZIP', () => upload.click()));
  upload.onchange = async () => {
    const f = upload.files[0]; if (!f) return;
    if (f.size > 12000000) return notice('Choose a ZIP under 12 MB.', true);
    askName('Import manuscript', async name => { const bytes = await f.arrayBuffer(); const zip = bytes64(bytes); const p = await api('/api/writing', {action: 'create_project', name, zip}); location.href = '/write?id=' + p.id; });
  };
  const tabs = node('div', undefined, 'desk-tabs'), panel = node('section'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Writing desk'); panel.id = 'deskPanel'; panel.setAttribute('role', 'tabpanel'); content.append(tabs, panel);
  function show(section) {
    for (const tab of tabs.children) tab.setAttribute('aria-selected', String(tab.dataset.section === section));
    panel.replaceChildren();
    if (section === 'templates') { renderTemplates(panel, state.templates, item => newManuscript(item)); return; }
    if (section === 'libraries') {
      panel.append(node('p', 'Gather papers from the corpus into your own libraries, then link them to a manuscript.', 'muted'));
      panel.append(button('+ Personal library', () => askName('New personal library', async name => { await api('/api/writing', {action: 'create_collection', name}); await refresh(); show('libraries'); })));
      const cards = node('div', undefined, 'cards'); panel.append(cards);
      for (const c of state.collections) {
        const card = node('article', undefined, 'card personal-library-card'), links = node('div', undefined, 'actions');
        card.append(node('h3', c.name), node('p', `${c.count} papers · private to your account`));
        links.append(link('Open library', '/library?collection=' + c.id, 'button'), button('Start manuscript', () => newManuscript({id: 'article'}, c.id))); card.append(links); cards.append(card);
      }
      return;
    }
    const list = node('div', undefined, 'manuscript-list'); panel.append(list);
    for (const p of state.projects) {
      const row = link('', '/write?id=' + p.id, 'manuscript-row'), text = node('div');
      const date = p.updated ? new Date(p.updated * 1000) : null;
      text.append(node('h3', p.name), node('p', `${p.count} files${date && !Number.isNaN(date.valueOf()) ? ' · Updated ' + date.toLocaleDateString() : ''}`));
      if (p.collaboration?.shared) text.querySelector('h3').append(node('span', 'Shared', 'sharing-badge'));
      row.append(node('span', 'TₑX', 'manuscript-icon'), text, node('span', 'Open →')); list.append(row);
    }
    if (!state.projects.length) {
      const empty = node('div', undefined, 'desk-empty'); empty.append(node('h2', 'Start with a blank page. Or a head start.'), node('p', 'Choose a ready-to-compile template, or import your existing LaTeX project.', 'muted'), button('Browse templates', () => show('templates'))); list.append(empty);
    }
  }
  for (const [id, label] of [['manuscripts', 'Manuscripts'], ['templates', 'Templates'], ['libraries', 'My libraries']]) {
    const tab = button(label, () => show(id)); tab.dataset.section = id; tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', panel.id); tabs.append(tab);
  }
  show('manuscripts');
}
function bytes64(buffer) { const bytes = new Uint8Array(buffer); let raw = ''; for (let i = 0; i < bytes.length; i += 8192) raw += String.fromCharCode(...bytes.subarray(i, i + 8192)); return btoa(raw); }
function outline() {
  $('outline').replaceChildren();
  const text = textTypes.test(activeFile) ? project.files[activeFile] : '';
  const pattern = /^\s*\\(part|chapter|section|subsection|subsubsection|paragraph)\*?(?:\[[^\]\n]*\])?\{([^}\n]+)\}/gm;
  let match, count = 0;
  while ((match = pattern.exec(text)) && count++ < 200) {
    const line = text.slice(0, match.index).split('\n').length;
    const b = button(match[2], () => { writerLayout.showCode(); sourceEditor.jump(line); });
    b.className = 'outline-item ' + match[1]; b.title = `Line ${line}`; $('outline').append(b);
  }
  if (!count) $('outline').append(node('p', 'Sections in this file appear here.', 'hint'));
}
function changed() {
  editVersion++; $('savedState').textContent = 'Unsaved changes'; $('previewState').textContent = 'Source changed · preview needs recompiling';
  clearTimeout(saveTimer); clearTimeout(compileTimer);
  if (saveBlocked) { $('savedState').textContent = 'Draft kept · saving paused'; return; }
  saveTimer = setTimeout(() => save().catch(e => notice(e.message, true)), 650);
  if ($('autoCompile').checked) compileTimer = setTimeout(() => compile().catch(e => notice(e.message, true)), 1600);
}
function pauseSaving(problem) {
  saveBlocked = true; clearTimeout(saveTimer); clearTimeout(compileTimer);
  $('savedState').textContent = 'Draft kept · saving paused'; $('syncState').textContent = 'Review changes';
  $('collaborationConflict').hidden = false;
  $('conflictMessage').textContent = problem.message + ' Download your draft before replacing it with the saved version.';
}
function collaborationControls() {
  const access = project.collaboration || {enabled: false, role: 'local'};
  $('shareProject').disabled = !access.enabled;
  $('shareProject').title = access.enabled ? 'Manage access to this manuscript' : 'Sharing is available when signed in to a shared server.';
  const editorOnly = access.role === 'editor';
  $('projectLibrary').disabled = editorOnly; $('updateBibliography').hidden = editorOnly;
  $('librarySyncHint').textContent = editorOnly ? 'The owner manages the linked personal library. The project BibTeX files are shared.' : 'Preview bibliography changes before applying them. Your existing entries are kept.';
  $('projectLibrary').value = project.collection || '';
  $('projectShared').hidden = !access.shared;
}
async function finishComposition() {
  const deadline = Date.now() + 30000;
  while (sourceEditor?.isComposing()) {
    if (Date.now() > deadline) throw new Error('Text input is still in progress. Your draft has not been replaced.');
    await new Promise(resolve => setTimeout(resolve, 80));
  }
}
function adoptProject(updated, accepted = updated, dirty = false) {
  const name = activeFile in updated.files ? activeFile : updated.main;
  const treeChanged = name !== activeFile || project.main !== updated.main || Object.keys(project.files).sort().join('\0') !== Object.keys(updated.files).sort().join('\0');
  const editable = textTypes.test(name);
  if (!sourceEditor.open(name, editable ? updated.files[name] : 'Binary asset. Included in compilation and ZIP export.', editable)) {
    throw new Error('These changes are too large to safely apply in the open editor. Your draft has been kept.');
  }
  project = updated; serverProject = snapshotProject(accepted); activeFile = name;
  editVersion++;
  if (!dirty) savedVersion = editVersion;
  if (treeChanged) fileList(); else selectFile(activeFile);
  collaborationControls();
  $('savedState').textContent = dirty ? 'Unsaved changes' : 'All changes saved';
  $('previewState').textContent = 'Source updated · preview needs recompiling';
}
async function save() {
  if (saveBlocked) throw new Error('Automatic saving paused after an error. Download your draft before reloading.');
  if (saving) { await saving; return save(); }
  if (editVersion === savedVersion) return;
  const version = editVersion;
  const sent = snapshotProject(project);
  const payload = {action: 'save_project', id: project.id, ...sent, base: snapshotProject(serverProject)};
  $('savedState').textContent = 'Saving…';
  saving = (async () => { try {
    const result = await api('/api/writing', payload); await finishComposition();
    const dirty = editVersion !== version;
    const updated = dirty ? rebaseDraft(sent, project, result, diffTextChanges) : result;
    adoptProject(updated, result, dirty);
    if (dirty) savedVersion = version;
  } catch (e) { pauseSaving(e); throw e; } })();
  try { await saving; } finally { saving = null; }
  if (editVersion !== savedVersion) await save();
}
function selectFile(name) {
  const editable = textTypes.test(name);
  if (!sourceEditor.open(name, editable ? project.files[name] : 'Binary asset. Included in compilation and ZIP export.', editable)) {
    if (sourceEditor.isComposing()) {
      $('source').addEventListener('compositionend', () => setTimeout(() => { if (name in project.files) selectFile(name); }, 0), {once: true});
      return;
    }
    pauseSaving(new Error('The selected file could not be updated safely. Your current editor contents have been kept.')); return;
  }
  activeFile = name; $('fileLabel').textContent = name;
  for (const b of $('fileList').querySelectorAll('[data-file]')) { b.classList.toggle('active', b.dataset.file === name); b.setAttribute('aria-current', String(b.dataset.file === name)); }
  for (const b of document.querySelectorAll('[data-edit-command]')) b.disabled = !editable && b.dataset.editCommand !== 'search';
  outline();
}
async function synchronizeProject() {
  clearTimeout(syncTimer);
  if (syncBusy || document.hidden || saveBlocked || !project?.collaboration?.enabled) { scheduleSync(); return; }
  syncBusy = true;
  try {
    const remote = await api('/api/writing?revision=' + encodeURIComponent(project.id));
    project.collaboration = remote.collaboration; collaborationControls();
    if (remote.revision !== serverProject.revision) {
      if (saving || editVersion !== savedVersion) await save();
      else if (!sourceEditor.isComposing()) {
        const updated = await api('/api/writing?id=' + encodeURIComponent(project.id));
        if (editVersion !== savedVersion || saving) await save();
        else if (updated.revision > serverProject.revision && !sourceEditor.isComposing()) adoptProject(updated);
      }
    }
    syncFailures = 0; if (!saveBlocked) $('syncState').textContent = project.collaboration.shared ? 'Live sync' : '';
  } catch (error) {
    syncFailures++;
    if ([401, 403, 404].includes(error.status)) pauseSaving(new Error('Project access is no longer available. Your local draft is still here.'));
    else if (!saveBlocked) $('syncState').textContent = 'Sync reconnecting…';
  } finally { syncBusy = false; scheduleSync(); }
}
function scheduleSync() {
  clearTimeout(syncTimer);
  if (project?.collaboration?.enabled && !saveBlocked) syncTimer = setTimeout(synchronizeProject, Math.min(30000, 2500 * 2 ** Math.min(syncFailures, 4)));
}
function fileList() {
  $('fileList').replaceChildren(); $('mainFile').replaceChildren();
  const folders = new Map([['', $('fileList')]]);
  for (const name of Object.keys(project.files).sort()) {
    const parts = name.split('/'); let folder = '';
    for (const part of parts.slice(0, -1)) {
      const next = folder ? folder + '/' + part : part;
      if (!folders.has(next)) { const d = node('details', undefined, 'file-folder'); d.open = true; d.append(node('summary', part)); folders.get(folder).append(d); folders.set(next, d); }
      folder = next;
    }
    const b = button('', () => { selectFile(name); writerLayout.showCode(); });
    b.append(node('span', /\.tex$/.test(name) ? 'TₑX' : /\.bib$/.test(name) ? 'Bib' : '◇', 'file-icon'), node('span', parts.at(-1)));
    b.dataset.file = name; b.title = name; folders.get(folder).append(b);
    if (name.endsWith('.tex')) $('mainFile').append(new Option(name, name));
  }
  $('mainFile').value = project.main; selectFile(activeFile in project.files ? activeFile : project.main);
}
function zoomPDF() {
  pdfPreview?.zoom($('pdfZoom').value);
}
async function renderPDF(bytes) {
  if (!pdfPreview) pdfPreview = createPDFPreview($('pdfPages'), $('pdfCount'), error => notice('PDF preview: ' + error.message, true));
  zoomPDF(); await pdfPreview.load(bytes);
}
async function compile(force = false, retry = 0) {
  if (compiling) return;
  compiling = true; $('compile').disabled = true;
  $('compileCode').disabled = true;
  let startedVersion = editVersion;
  $('previewState').textContent = 'Compiling…';
  try {
    await save(); const version = editVersion; startedVersion = version; const result = await api('/api/writing', {action: 'compile', id: project.id, revision: project.revision, force});
    $('compileLog').textContent = result.log || 'Compilation completed.'; $('problems').replaceChildren();
    for (const p of result.diagnostics) $('problems').append(button(`${p.file ? p.file + ':' + p.line + ' · ' : ''}${p.message}`, () => {
      if (!(p.file in project.files) || !p.line) return; selectFile(p.file); writerLayout.showCode(); sourceEditor.jump(p.line);
    }));
    if (!result.ok) { $('logDetails').open = true; $('previewState').textContent = 'Compilation failed · previous PDF may be out of date'; notice('Compilation failed. Check the log below; your source is saved.', true); return; }
    const bytes = Uint8Array.from(atob(result.pdf), c => c.charCodeAt(0));
    if (pdfURL) URL.revokeObjectURL(pdfURL); pdfURL = URL.createObjectURL(new Blob([bytes], {type: 'application/pdf'})); $('downloadPDF').href = pdfURL; $('downloadPDF').hidden = false;
    await renderPDF(bytes);
    $('previewState').textContent = version === editVersion ? `PDF · revision ${result.revision} · ${result.cached ? 'saved build · ' : ''}${result.seconds}s` : 'Previous revision · new changes pending'; notice(result.diagnostics.length ? 'PDF compiled. Review the warnings below.' : 'PDF ready.');
  } catch (e) {
    if (e.status === 409 && /another manuscript/i.test(e.message) && !force && $('autoCompile').checked && retry < 3) {
      $('previewState').textContent = 'Compiler busy · retrying shortly';
      clearTimeout(compileTimer); compileTimer = setTimeout(() => compile(false, retry + 1).catch(error => notice(error.message, true)), 2500 * (retry + 1));
    } else { $('previewState').textContent = 'Preview not updated'; throw e; }
  }
  finally { compiling = false; $('compile').disabled = $('compileCode').disabled = !state.compiler.configured; if (!saveBlocked && $('autoCompile').checked && editVersion !== startedVersion) { clearTimeout(compileTimer); compileTimer = setTimeout(() => compile().catch(e => notice(e.message, true)), 600); } }
}

async function editor(id) {
  const [editorModule, {setupWriterLayout}] = await Promise.all([import('./latex-editor.mjs'), import('./writer-layout.mjs')]);
  const {createLatexEditor} = editorModule; diffTextChanges = editorModule.diffTextChanges; collaborationStyle();
  project = await api('/api/writing?id=' + encodeURIComponent(id)); document.body.classList.add('writing'); document.title = project.name + ' · OriginWrite';
  serverProject = snapshotProject(project);
  content.innerHTML = `
    <div class="writer-toolbar">
      <a href="/write" class="button back-projects" title="Back to projects">← Projects</a>
      <div class="project-heading"><h1><span id="projectTitle"></span><span id="projectShared" class="sharing-badge" hidden>Shared</span></h1><span id="savedState" role="status">All changes saved</span><span id="syncState" role="status"></span></div>
      <div class="writer-actions">
        <button id="shareProject">Share</button>
        <a id="exportZip" class="button">Download LaTeX ZIP</a>
        <button id="referenceButton" title="Personal library, citations and bibliography">References</button>
        <button id="templateButton" title="Start a new manuscript from a template">Templates</button>
        <button id="toggleFiles" aria-controls="filesPane" aria-expanded="true" title="Show or hide the file sidebar">☷ <span>Files</span></button>
        <select id="layoutMode" aria-label="Editor layout"><option value="split">Code & PDF</option><option value="code">Code only</option><option value="pdf">PDF only</option></select>
        <select id="paneDirection" aria-label="Pane arrangement"><option value="side">Side by side</option><option value="stack">Stacked</option></select>
        <button id="swapPanes" title="Swap code and PDF positions" aria-label="Swap code and PDF positions">⇄</button>
        <button id="editorTheme" title="Toggle dark code editor" aria-label="Dark code editor" aria-pressed="false">◐</button>
        <details class="writer-menu"><summary aria-label="Project options" title="Project options">•••</summary><div>
          <button id="draft">Download draft</button><button id="resetLayout">Reset layout</button>
        </div></details>
      </div>
    </div>
    <aside id="collaborationConflict" class="writing-conflict" role="alert" hidden><p id="conflictMessage"></p><button id="conflictDraft">Download draft</button><button id="conflictReload">Reload saved version</button></aside>
    <div class="editor-grid" id="writerGrid">
      <aside class="files" id="filesPane" aria-label="Project files">
        <div class="files-heading"><strong>File tree</strong><button id="hideFiles" title="Hide file sidebar" aria-label="Hide file sidebar">×</button></div>
        <div class="file-actions"><button id="addFile" title="New text file">+ New file</button><button id="uploadFile" title="Upload source files or figures">↑ Upload</button><input type="file" id="fileUpload" multiple hidden></div>
        <div id="fileList"></div>
        <details class="outline-panel" open><summary>File outline</summary><div id="outline"></div></details>
        <details class="project-settings"><summary>Project settings & references</summary>
          <label>Main document<select id="mainFile"></select></label>
          <label>Project library<select id="projectLibrary"><option value="">No collection</option></select></label>
          <button id="updateBibliography">Manage references</button><p class="hint" id="librarySyncHint">Preview bibliography changes before applying them. Your existing entries are kept.</p>
        </details>
      </aside>
      <div id="filesResize" class="splitter files-resize" role="separator" tabindex="0" aria-label="Resize file sidebar" aria-controls="filesPane" aria-orientation="vertical" aria-valuemin="160" aria-valuemax="400" aria-valuenow="230" title="Drag to resize files · double-click to reset"></div>
      <section class="source-pane" id="sourcePane" aria-label="Code editor">
        <div class="source-tab"><span class="file-icon">TₑX</span><span id="fileLabel"></span><span class="tab-dot" aria-hidden="true"></span></div>
        <div class="code-toolbar" role="toolbar" aria-label="Code tools">
          <button data-edit-command="undo" title="Undo (Ctrl/Cmd+Z)" aria-label="Undo">↶</button><button data-edit-command="redo" title="Redo (Ctrl/Cmd+Shift+Z)" aria-label="Redo">↷</button><span class="tool-divider"></span>
          <button data-edit-command="bold" title="Bold selection" aria-label="Bold selection"><b>B</b></button><button data-edit-command="italic" title="Italic selection" aria-label="Italic selection"><i>I</i></button><button data-edit-command="comment" title="Toggle comment (Ctrl/Cmd+/)" aria-label="Toggle comment">%</button>
          <button data-edit-command="search" class="search-code" title="Find and replace (Ctrl/Cmd+F)" aria-label="Find and replace">⌕ <span>Find</span></button>
          <button id="insertCitation" title="Insert a citation from your library" aria-label="Insert citation">Cite</button>
          <button id="codeZoomOut" title="Smaller code" aria-label="Decrease code zoom">−</button>
          <select id="codeZoom" aria-label="Code font size"><option value="11">11 px</option><option value="12">12 px</option><option value="13">13 px</option><option value="14" selected>14 px</option><option value="16">16 px</option><option value="18">18 px</option><option value="20">20 px</option><option value="24">24 px</option></select>
          <button id="codeZoomIn" title="Larger code" aria-label="Increase code zoom">+</button>
          <button id="compileCode" title="Recompile (Ctrl/Cmd+Enter)" aria-label="Recompile manuscript">▶</button>
        </div>
        <div id="source" class="code-editor"></div><div class="code-status"><span>LaTeX</span><span id="cursorPosition">Ln 1, Col 1</span><span>UTF-8</span></div>
      </section>
      <div id="panesResize" class="splitter panes-resize" role="separator" tabindex="0" aria-label="Resize code and PDF panes" aria-controls="sourcePane previewPane" aria-orientation="vertical" aria-valuemin="20" aria-valuemax="80" aria-valuenow="50" title="Drag to resize code and PDF · double-click for equal widths"></div>
      <section class="preview-pane" id="previewPane" aria-label="PDF preview">
        <div class="pdf-toolbar"><button id="compile" class="primary">▶ Recompile</button><label class="auto-compile"><input id="autoCompile" type="checkbox">Auto</label><a id="downloadPDF" class="button" download="manuscript.pdf" title="Download PDF" aria-label="Download PDF" hidden>↓</a>
          <button id="pdfZoomOut" title="Zoom out PDF" aria-label="Decrease PDF zoom">−</button>
          <select id="pdfZoom" aria-label="PDF zoom"><option value="fit">Fit width</option><option value="0.5">50%</option><option value="0.75">75%</option><option value="1">100%</option><option value="1.25">125%</option><option value="1.5">150%</option><option value="2">200%</option><option value="3">300%</option></select>
          <button id="pdfZoomIn" title="Zoom in PDF" aria-label="Increase PDF zoom">+</button>
        </div><div id="pdfPages"><div class="pdf-empty"><span aria-hidden="true">▤</span><h2>Your manuscript, on paper</h2><p>Recompile to bring your LaTeX to life.</p></div></div>
        <div class="pdf-status"><span id="previewState" role="status">Compile to see your PDF</span><span id="pdfCount"></span></div>
      </section>
    </div>
    <details id="logDetails"><summary>Errors, warnings & compile log</summary><div class="log-body"><div id="problems"></div><pre id="compileLog">No compilation yet.</pre></div></details>`;
  sourceEditor = createLatexEditor($('source'), (name, text) => { project.files[name] = text; changed(); clearTimeout(outlineTimer); outlineTimer = setTimeout(outline, 300); }, (line, col) => { $('cursorPosition').textContent = `Ln ${line}, Col ${col}`; });
  writerLayout = setupWriterLayout(() => sourceEditor.measure());
  for (const b of document.querySelectorAll('[data-edit-command]')) b.onclick = () => sourceEditor.command(b.dataset.editCommand);
  $('pdfZoom').onchange = zoomPDF;
  for (const [id, delta] of [['pdfZoomOut', -1], ['pdfZoomIn', 1]]) $(id).onclick = () => {
    const choices = [.5, .75, 1, 1.25, 1.5, 2, 3], select = $('pdfZoom');
    const first = $('pdfPages').querySelector('canvas');
    const current = select.value === 'fit' ? (first ? first.clientWidth / Number(first.dataset.pageWidth) : 1) : Number(select.value);
    select.value = String(delta > 0 ? choices.find(value => value > current + .001) || 3 : choices.findLast(value => value < current - .001) || .5); zoomPDF();
  };
  $('projectTitle').textContent = project.name; activeFile = project.main; fileList();
  state.collections.forEach(c => $('projectLibrary').append(new Option(c.name, c.id))); $('projectLibrary').value = project.collection;
  $('projectLibrary').onchange = () => { project.collection = $('projectLibrary').value; changed(); };
  $('mainFile').onchange = () => { project.main = $('mainFile').value; changed(); };
  $('addFile').onclick = () => askName('New text file (e.g. sections/methods.tex)', async name => { if (!textTypes.test(name) || !/^[A-Za-z0-9_][A-Za-z0-9_./ -]*$/.test(name) || name.split('/').some(p => !p || p.startsWith('.'))) throw new Error('Choose a safe relative text filename.'); if (name in project.files) throw new Error('That file already exists.'); project.files[name] = ''; activeFile = name; fileList(); changed(); });
  $('uploadFile').onclick = () => $('fileUpload').click();
  $('fileUpload').onchange = async () => { try { for (const f of $('fileUpload').files) { if (f.size > 8000000) throw new Error('Individual uploads are limited to 8 MB.'); if (!/\.(tex|bib|sty|cls|bst|bbl|txt|md|csv|png|jpg|jpeg|pdf)$/i.test(f.name)) throw new Error('Unsupported file type.'); if (f.name in project.files) throw new Error(`${f.name} already exists. Edit it or choose another name.`); project.files[f.name] = textTypes.test(f.name) ? await f.text() : bytes64(await f.arrayBuffer()); changed(); } fileList(); } catch (e) { notice(e.message, true); } };
  const insertCitation = key => { if (!/\.tex$/i.test(activeFile)) selectFile(project.main); writerLayout.showCode(); return sourceEditor.insertCitation(key); };
  const references = () => {
    if (project.collaboration?.role === 'editor') { openProjectCitations(project, insertCitation); return; }
    return openReferences({api, getProject: () => project, save, notice,
    linkCollection: async id => {
      project.collection = id; changed(); await save(); await refresh();
      $('projectLibrary').replaceChildren(new Option('No collection', ''));
      state.collections.forEach(c => $('projectLibrary').append(new Option(c.name, c.id))); $('projectLibrary').value = id;
    },
    acceptProject: updated => {
      clearTimeout(saveTimer);
      try { adoptProject(updated); } catch (error) { pauseSaving(error); throw error; }
      $('savedState').textContent = 'All changes saved'; $('previewState').textContent = 'Bibliography updated · recompile to see changes';
      if ($('autoCompile').checked) { clearTimeout(compileTimer); compileTimer = setTimeout(() => compile().catch(e => notice(e.message, true)), 1600); }
    },
    insertCitation
  }).catch(e => notice(e.message, true)); };
  $('referenceButton').onclick = $('insertCitation').onclick = $('updateBibliography').onclick = references;
  $('templateButton').onclick = () => openTemplateGallery(state.templates, item => newManuscript(item, project.collection));
  $('draft').onclick = () => download(JSON.stringify({name: project.name, main: project.main, files: project.files}, null, 2), 'originwrite-draft.json', 'application/json');
  $('conflictDraft').onclick = $('draft').onclick;
  $('conflictReload').onclick = async () => {
    if (!confirm('Replace your open draft with the saved project? Download your draft first if you want to keep it.')) return;
    try {
      const updated = await api('/api/writing?id=' + encodeURIComponent(project.id));
      await finishComposition();
      sourceEditor.destroy();
      sourceEditor = createLatexEditor($('source'), (name, text) => { project.files[name] = text; changed(); clearTimeout(outlineTimer); outlineTimer = setTimeout(outline, 300); }, (line, col) => { $('cursorPosition').textContent = `Ln ${line}, Col ${col}`; });
      adoptProject(updated); saveBlocked = false; $('collaborationConflict').hidden = true; notice('Saved version loaded.'); scheduleSync();
    } catch (error) { notice(error.message, true); }
  };
  $('shareProject').onclick = () => openSharing({api, project, notice, changed: access => { project.collaboration = access; collaborationControls(); }}).catch(error => notice(error.message, true));
  collaborationControls(); scheduleSync();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) synchronizeProject(); });
  window.addEventListener('pagehide', () => clearTimeout(syncTimer));
  $('exportZip').href = '/api/writing/export?id=' + project.id;
  $('exportZip').onclick = async e => { e.preventDefault(); try { await save(); location.href = $('exportZip').href; } catch (err) { notice(err.message, true); } };
  $('compile').disabled = $('compileCode').disabled = !state.compiler.configured; $('autoCompile').disabled = !state.compiler.configured;
  $('compile').onclick = () => { $('previewState').textContent = 'Compiling…'; compile(true).catch(e => notice(e.message, true)); };
  $('compileCode').onclick = $('compile').onclick;
  $('autoCompile').onchange = () => { clearTimeout(compileTimer); if ($('autoCompile').checked) compile().catch(e => notice(e.message, true)); };
  if (!state.compiler.configured) notice(state.compiler.message);
  document.addEventListener('keydown', e => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save().catch(e => notice(e.message, true)); } });
  document.addEventListener('keydown', e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && state.compiler.configured) { e.preventDefault(); compile(true).catch(e => notice(e.message, true)); } });
  window.addEventListener('beforeunload', e => { if (editVersion !== savedVersion) { e.preventDefault(); e.returnValue = ''; } });
}

try {
  appProfile = await api('/api/app-profile');
  for (const item of document.querySelectorAll('[data-feature]')) item.hidden = !appProfile.features[item.dataset.feature];
  if (appProfile.user) {
    $('workspaceUser').textContent = appProfile.user.display_name || appProfile.user.username || '';
    $('workspaceLogout').hidden = false;
    $('workspaceLogout').onclick = async () => { try { if (project) await save(); await api('/api/logout', {}); location.href = '/login'; } catch (error) { notice(error.message, true); } };
  }
  await refresh();
  if (mode === '/bookshelf') await shelf();
  else if (mode === '/library') await library();
  else if (mode === '/write') { const id = new URLSearchParams(location.search).get('id'); if (id) await editor(id); else await projects(); }
  else home();
} catch (e) { notice(e.message, true); }
