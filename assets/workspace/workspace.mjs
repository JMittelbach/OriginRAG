import {renderTemplates, openTemplateGallery} from './writing-templates.mjs';
import {openReferences} from './writing-references.mjs';
import {createPDFPreview} from './pdf-preview.mjs';
import {openLibraryImport} from './library-import.mjs';
import {collaborationStyle, snapshotProject, rebaseDraft, openSharing, openProjectCitations} from './writing-collaboration.mjs';
import {setupLanguageMenu} from './writing-language.mjs';
import {setupProjectChat} from './writing-chat.mjs';
import {openModels} from './models-launcher.mjs';
import {setupProjectBackup} from './writing-backups.mjs';
import {prepareProjectUploads} from './project-uploads.mjs';
import {uploadCollectionPDFs} from './collection-drop.mjs';
import {requestJSON} from './request.mjs';
import {getAppProfile} from './profile.mjs';
import {readingNavigation, renderReadingHistory} from './reading-history.mjs';
import {addDialogClose} from './dialog-close.mjs';
import {latexOutline} from './latex-outline.mjs';

const $ = id => document.getElementById(id);
const content = $('content');
document.body.classList.add('workspace-page');
const mode = location.pathname;
if (mode === '/write') document.title = 'Quill';
let sourceEditor, writerLayout, outlineTimer, diffTextChanges, serverProject, syncTimer, syncBusy = false, syncFailures = 0, lastBibliographyCheck = 0;
const textTypes = /\.(tex|bib|sty|cls|bst|bbl|txt|md|csv)$/i;
let state, project, activeFile, editVersion = 0, savedVersion = 0, saving = null, unconfirmedSave = null;
let appProfile = {profile: 'full', features: {rag: true, write: true}};
let saveTimer, compileTimer, compiling = false, saveBlocked = false, pdfURL = '', pdfPreview;
function node(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function notice(message, error = false) { $('notice').textContent = message; $('notice').classList.toggle('error', error); }
async function api(path = '/api/writing', payload, options = {}) {
  const headers = new Headers();
  const token = document.cookie.split('; ').find(v => v.startsWith('pfempire_csrf='))?.split('=')[1];
  if (token) headers.set('X-CSRF-Token', decodeURIComponent(token));
  const timeout = payload?.action === 'compile' ? 150000 : payload?.action === 'language_check' ? 75000 : 30000;
  return requestJSON(path, {payload, headers, timeout, ...options});
}
function button(text, fn, cls) {
  const b = node('button', text, cls); b.type = 'button';
  b.onclick = async () => {
    if (b.disabled || b.getAttribute('aria-busy') === 'true') return;
    b.setAttribute('aria-busy', 'true');
    try { await fn(); } catch (error) { notice(error.message, true); }
    finally { b.removeAttribute('aria-busy'); }
  };
  return b;
}
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
    e.preventDefault(); const submit = e.submitter; submit.disabled = true; const label = submit.textContent; submit.textContent = 'Saving…'; $('createError').textContent = 'Preparing your workspace…';
    try { await action($('newName').value); dialog.close(); } catch (error) { $('createError').textContent = error.message; }
    finally { submit.disabled = false; submit.textContent = label; }
  };
  dialog.showModal(); $('newName').focus();
}
$('cancelCreate').onclick = () => $('createDialog').close();
addDialogClose($('createDialog'), 'Close new project', {id: 'createClose'});
for (const a of document.querySelectorAll('nav a')) a.classList.toggle('active', a.pathname === mode);

function home() {
  let preferred;
  try { preferred = JSON.parse(localStorage.getItem('origin.workspace.settings.v1'))?.startPage; } catch {}
  const allowed = ['/library', '/bookshelf', ...(appProfile.features.rag ? ['/chat'] : []), ...(appProfile.features.write ? ['/write'] : []), ...(appProfile.features.folio ? ['/folio'] : [])];
  location.replace(allowed.includes(preferred) ? preferred : appProfile.home);
}

async function shelf() {
  heading('Your bookshelf', 'A little space for the papers you are working with.');
  const finished = new URLSearchParams(location.search).get('view') === 'read';
  const updateCount = readingNavigation(content, finished);
  if (finished) { await renderReadingHistory(content, {api, notice, updateCount}); return; }
  api('/api/reading?offset=0&limit=1').then(data => updateCount(data.read_count)).catch(() => {});
  const books = node('div', undefined, 'shelf'); content.append(books);
  const data = await api('/api/shelf');
  const paging = node('div', undefined, 'shelf-paging'), label = node('span'); content.append(paging);
  let page = 0;
  const compact = matchMedia('(max-width:700px)');
  const previous = button('Previous', () => { page--; draw(); }), next = button('Next', () => { page++; draw(); });
  paging.append(previous, label, next);
  function draw() {
    const perRow = compact.matches ? 3 : 6, size = perRow * 2;
    page = Math.max(0, Math.min(page, Math.ceil(data.items.length / size) - 1));
    books.replaceChildren();
    for (let level = 0; level < 2; level++) {
      const plank = node('div', undefined, 'shelf-level');
      for (const paper of data.items.slice(page * size + level * perRow, page * size + (level + 1) * perRow)) {
        const b = node('article', undefined, 'book'), title = node('h3', paper.title); title.title = paper.title;
        b.append(title, node('small', [paper.author, paper.year].filter(Boolean).join(' · ')));
        if (paper.is_read) b.append(node('span', 'Read', 'book-read-badge'));
        if (paper.available) b.append(link('Read PDF', '/reader?paper_id=' + encodeURIComponent(paper.paper_id)));
        const remove = button('Remove', async () => { await api('/api/shelf', {action: 'remove', paper_id: paper.paper_id}); data.items = data.items.filter(item => item.paper_id !== paper.paper_id); draw(); });
        remove.setAttribute('aria-label', 'Remove from shelf: ' + paper.title); b.append(remove); plank.append(b);
      }
      if (!data.items.length && level === 0) plank.append(link('Choose your first paper from the Library →', '/library', 'shelf-empty'));
      books.append(plank);
    }
    previous.disabled = page === 0; next.disabled = (page + 1) * size >= data.items.length;
    label.textContent = `${data.items.length} papers · Shelf ${page + 1} of ${Math.max(1, Math.ceil(data.items.length / size))}`;
  }
  compact.addEventListener('change', draw); draw();
}

async function library() {
  const h = heading('Library', 'One shared corpus. Personal collections for each project.');
  h.append(button('+ Collection', () => askName('New collection', async name => { await api('/api/writing', {action: 'create_collection', name}); await refresh(); content.replaceChildren(); await library(); })));
  h.append(button('Find or import papers', () => openLibraryImport({notice, changed: () => load()})));
  content.insertAdjacentHTML('beforeend', '<div class="library-layout"><aside><p class="eyebrow">MY COLLECTIONS</p><div id="collections" class="collection-list"></div></aside><section><form id="searchForm" class="search"><input id="query" type="search" placeholder="Search title, author, journal or DOI…" aria-label="Search library"><button>Search</button></form><p id="libraryCount" class="muted" role="status"></p><div id="libraryFeedback" role="status" hidden></div><div id="papers"></div><div class="actions"><button id="previousPapers">Previous</button><span id="libraryPage" class="muted"></span><button id="nextPapers">Next</button></div></section></div>');
  const params = new URLSearchParams(location.search);
  let selected = state.collections.some(c => c.id === params.get('collection')) ? params.get('collection') : '', offset = 0, sequence = 0, browse = false, autoBrowse = true, searchRequest;
  $('query').value = params.get('query') || '';
  const tools = node('div', undefined, 'library-tools'); $('libraryCount').after(tools);
  const collectionButtons = [];
  let adding = Promise.resolve();
  function addPaper(id, paperId) {
    adding = adding.catch(() => {}).then(async () => {
      notice('Linking paper to your collection…');
      const target = await api('/api/writing?id=' + id + '&kind=collections');
      await api('/api/writing', {action: 'collection_paper', id, revision: target.revision, paper_id: paperId});
      notice('Paper linked to your collection. No extra PDF copy was created.');
      await load();
    });
    return adding;
  }
  function acceptDrop(target, getCollection) {
    target.addEventListener('dragover', event => {
      if (![...event.dataTransfer.types].some(type => ['Files', 'application/x-originrag-paper-id'].includes(type))) return;
      event.preventDefault(); target.classList.add('drop-active');
    });
    target.addEventListener('dragleave', () => target.classList.remove('drop-active'));
    target.addEventListener('drop', event => {
      event.preventDefault(); event.stopPropagation(); target.classList.remove('drop-active');
      const id = getCollection();
      if (event.dataTransfer.files.length) {
        uploadCollectionPDFs([...event.dataTransfer.files], id, {notice, changed: load}).catch(error => notice(error.message, true));
      } else {
        const paperId = event.dataTransfer.getData('application/x-originrag-paper-id');
        if (id && paperId) addPaper(id, paperId).catch(error => notice(error.message, true));
      }
    });
  }
  function updateURL() {
    const query = new URLSearchParams();
    if (selected) query.set('collection', selected);
    if ($('query').value.trim()) query.set('query', $('query').value.trim());
    history.replaceState(null, '', '/library' + (query.size ? '?' + query : ''));
  }
  for (const c of [{id: '', name: 'All papers'}, ...state.collections]) {
    const b = button(c.name, async () => { selected = c.id; browse = false; autoBrowse = true; offset = 0; collectionButtons.forEach(([id, el]) => el.setAttribute('aria-pressed', String(id === selected))); await load(); });
    b.setAttribute('aria-pressed', String(c.id === selected)); collectionButtons.push([c.id, b]); $('collections').append(b);
    if (c.id) { acceptDrop(b, () => c.id); b.title = 'Drop corpus papers or PDFs into ' + c.name; }
  }
  const dropHint = node('p', 'Drop PDFs here, or drag a corpus paper onto a collection.', 'collection-drop-hint'); tools.before(dropHint);
  acceptDrop(content, () => selected);
  const suggestions = node('datalist'); suggestions.id = 'librarySuggestions'; content.append(suggestions); $('query').setAttribute('list', suggestions.id);
  async function load() {
    const current = ++sequence; const q = $('query').value.trim(), selectedId = selected;
    searchRequest?.abort(); searchRequest = new AbortController();
    const options = {signal: searchRequest.signal};
    updateURL();
    const feedback = $('libraryFeedback'); feedback.hidden = true;
    $('papers').setAttribute('aria-busy', 'true'); $('libraryCount').textContent = 'Searching your library…';
    $('papers').inert = true; tools.inert = true;
    $('previousPapers').disabled = $('nextPapers').disabled = true;
    try {
    let data, collection;
    if (selectedId) {
      collection = await api('/api/writing?id=' + selectedId + '&kind=collections', undefined, options);
      if (current !== sequence) return;
      if (autoBrowse) { browse = !collection.items.length; autoBrowse = false; }
      if (browse) data = await api('/api/library?limit=40&offset=' + offset + '&query=' + encodeURIComponent(q), undefined, options);
      else {
        const items = collection.items.filter(p => [p.title, p.author, p.year, p.journal, p.doi].join(' ').toLowerCase().includes(q.toLowerCase()));
        data = {items: items.slice(offset, offset + 40), total: items.length};
      }
    } else data = await api('/api/library?limit=40&offset=' + offset + '&query=' + encodeURIComponent(q), undefined, options);
    if (current !== sequence) return;
    if (offset && offset >= data.total) { offset = Math.max(0, Math.ceil(data.total / 40) - 1) * 40; return await load(); }
    $('papers').inert = false; tools.inert = false;
    suggestions.replaceChildren(...data.items.slice(0, 8).map(p => new Option(p.author ? p.title + ' · ' + p.author : p.title, p.title)));
    tools.replaceChildren();
    if (collection) {
      tools.append(node('p', collection.name + ' · private to your account', 'muted'));
      for (const [value, label] of [[false, 'In this collection'], [true, 'Browse corpus']]) {
        const toggle = button(label, async () => { browse = value; offset = 0; await load(); });
        toggle.setAttribute('aria-pressed', String(browse === value)); tools.append(toggle);
      }
      tools.append(button('Export BibTeX', async () => { const result = await api('/api/writing?collection_refs=' + selected); download(result.bibtex, 'references.bib'); }));
      tools.append(button('Rename', () => askName('Rename personal library', async name => { await api('/api/writing', {action: 'rename_collection', id: selected, revision: collection.revision, name}); await refresh(); content.replaceChildren(); await library(); })));
      if (appProfile.features.write) tools.append(button('Write with this library', () => newManuscript({id: 'article'}, selected)));
    }
    $('papers').replaceChildren(); $('libraryCount').textContent = `${data.total} ${data.total === 1 ? 'paper' : 'papers'}`; $('previousPapers').disabled = offset === 0; $('nextPapers').disabled = offset + 40 >= data.total;
    $('libraryPage').textContent = data.total ? `${offset + 1}–${Math.min(offset + 40, data.total)} of ${data.total}` : '';
    if (!data.items.length) {
      const empty = node('div', undefined, 'library-empty');
      empty.append(node('h3', q ? 'No matching papers' : collection && !browse ? 'This collection is empty' : 'Your library is ready for its first paper'));
      empty.append(node('p', q ? 'Try an author, a few words from the title, or a DOI.' : 'Find a paper online or add a PDF to get started.', 'muted'));
      if (q) empty.append(button('Clear search', async () => { $('query').value = ''; offset = 0; await load(); $('query').focus(); }));
      empty.append(button(collection && !browse ? 'Browse corpus' : 'Find or import papers', () => {
        if (collection && !browse) { browse = true; offset = 0; return load(); }
        return openLibraryImport({notice, changed: load});
      }));
      $('papers').append(empty);
    }
    for (const p of data.items) {
      const row = node('article', undefined, 'paper'); row.append(node('h3', p.title), node('p', [p.author, p.year, p.journal].filter(Boolean).join(' · ')));
      row.draggable = true; row.dataset.paperId = p.paper_id;
      row.addEventListener('dragstart', event => { event.dataTransfer.setData('application/x-originrag-paper-id', p.paper_id); event.dataTransfer.effectAllowed = 'copy'; });
      const actions = node('div', undefined, 'actions');
      if (collection && browse) {
        const present = collection.papers.includes(p.paper_id);
        const add = button(present ? 'In collection' : 'Add to ' + collection.name, () => addPaper(collection.id, p.paper_id)); add.disabled = present; actions.append(add);
      }
      if (p.local_pdf || p.pdf_url) actions.append(link('Read PDF', '/reader?paper_id=' + encodeURIComponent(p.paper_id), 'button'));
      actions.append(button('Save to shelf', async () => { await api('/api/shelf', {action: 'add', paper_id: p.paper_id}); notice('Saved to your bookshelf.'); }));
      const pick = node('select'); pick.setAttribute('aria-label', 'Destination collection'); pick.append(new Option('Add to collection…', ''));
      state.collections.forEach(c => pick.append(new Option(c.name, c.id)));
      pick.onchange = async () => { if (!pick.value) return; pick.disabled = true; try {
        await addPaper(pick.value, p.paper_id); notice('Added to your collection.');
      } catch (e) { notice(e.message, true); } finally { pick.value = ''; pick.disabled = false; } };
      actions.append(pick);
      if (selected && collection.papers.includes(p.paper_id)) actions.append(button('Remove from collection', async () => { await api('/api/writing', {action: 'collection_paper', id: selected, revision: collection.revision, paper_id: p.paper_id, remove: true}); await load(); }));
      row.append(actions); $('papers').append(row);
    }
    } catch (error) {
      if (current !== sequence || error.name === 'AbortError') return;
      $('libraryCount').textContent = 'Search could not finish';
      feedback.replaceChildren(node('p', error.message), button('Retry search', load)); feedback.hidden = false;
    } finally {
      if (current === sequence) $('papers').removeAttribute('aria-busy');
    }
  }
  let searchTimer;
  $('query').oninput = () => { clearTimeout(searchTimer); sequence++; searchRequest?.abort(); offset = 0; searchTimer = setTimeout(load, 220); };
  $('searchForm').onsubmit = e => { e.preventDefault(); clearTimeout(searchTimer); offset = 0; load().catch(e => notice(e.message, true)); };
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
  title.append(node('p', 'YOUR WRITING DESK', 'eyebrow'), node('h1', 'Quill'), node('p', 'Your manuscripts, templates and personal libraries.', 'muted'));
  actions.append(button('+ New manuscript', () => newManuscript(), 'primary')); h.append(title, actions); content.append(h);
  const models = button('Models', openModels); models.id = 'quillHomeModels'; actions.prepend(models);
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
        card.append(node('p', 'Drop PDFs or corpus papers here.', 'hint'));
        card.ondragover = event => { if ([...event.dataTransfer.types].some(type => ['Files', 'application/x-originrag-paper-id'].includes(type))) { event.preventDefault(); card.classList.add('drop-active'); } };
        card.ondragleave = () => card.classList.remove('drop-active');
        card.ondrop = async event => {
          event.preventDefault(); card.classList.remove('drop-active');
          try {
            const changed = async () => { await refresh(); show('libraries'); };
            if (event.dataTransfer.files.length) await uploadCollectionPDFs([...event.dataTransfer.files], c.id, {notice, changed});
            else {
              const paperId = event.dataTransfer.getData('application/x-originrag-paper-id');
              if (!paperId) return;
              const current = await api('/api/writing?id=' + c.id + '&kind=collections');
              await api('/api/writing', {action: 'collection_paper', id: c.id, revision: current.revision, paper_id: paperId});
              await changed(); notice('Paper linked to your collection.');
            }
          } catch (error) { notice(error.message, true); }
        };
        links.append(link('Open library', '/library?collection=' + c.id, 'button'), button('Start manuscript', () => newManuscript({id: 'article'}, c.id))); card.append(links); cards.append(card);
      }
      return;
    }
    const list = node('div', undefined, 'manuscript-list'); panel.append(list);
    for (const p of [...state.projects].sort((a, b) => b.updated - a.updated)) {
      const row = node('article', undefined, 'manuscript-row'), text = link('', '/write?id=' + p.id, 'manuscript-title');
      const date = p.updated ? new Date(p.updated * 1000) : null;
      text.append(node('h3', p.name), node('p', date && !Number.isNaN(date.valueOf()) ? 'Edited ' + date.toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'}) : 'Not edited yet'));
      if (p.collaboration?.shared) text.querySelector('h3').append(node('span', 'Shared', 'sharing-badge'));
      const actions = node('div', undefined, 'manuscript-actions');
      actions.append(link('Open', '/write?id=' + p.id, 'button'), link('Download ZIP', '/api/writing/export?id=' + p.id, 'button'));
      if (p.collaboration?.enabled) actions.append(button('Share', () => openSharing({api, project: p, notice, changed: access => { p.collaboration = access; }})));
      if (p.collaboration?.role !== 'editor') actions.append(button('Delete', async () => {
        if (!confirm(`Delete “${p.name}”? Its source files will be removed for everyone with access. Download a ZIP first if you need a backup.`)) return;
        if (!confirm(`Final confirmation: permanently delete “${p.name}”? This cannot be undone.`)) return;
        const buttons = [...actions.querySelectorAll('button')]; buttons.forEach(b => b.disabled = true); notice('Deleting manuscript…');
        try {
          await api('/api/writing', {action: 'delete_project', id: p.id, revision: p.revision, confirm_name: p.name, confirm_delete: true});
          state.projects = state.projects.filter(item => item.id !== p.id); show('manuscripts'); notice('Manuscript deleted. Your collections and corpus papers were kept.');
        } finally { buttons.forEach(b => b.disabled = false); }
      }, 'danger'));
      row.append(text, actions); list.append(row);
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
let previousOutline = null;
function outline() {
  const pane = $('outline');
  if (!pane) return;
  const source = /\.(tex|sty|cls)$/i.test(activeFile) ? project.files[activeFile] || '' : '';
  if (previousOutline?.pane === pane && previousOutline.source === source) return;
  const entries = latexOutline(source);
  const same = previousOutline?.pane === pane && entries.length === previousOutline.entries.length &&
    entries.every(({kind, title, line}, i) => {
      const old = previousOutline.entries[i];
      return kind === old.kind && title === old.title && line === old.line;
    });
  previousOutline = {pane, source, entries};
  if (same) return;
  const buttons = entries.map(({kind, title, line}) => {
    const b = button(title, () => { writerLayout.showCode(); sourceEditor.jump(line); });
    b.className = 'outline-item ' + kind; b.title = 'Line ' + line;
    return b;
  });
  pane.replaceChildren(...(buttons.length ? buttons : [node('p', 'Sections in this file appear here.', 'hint')]));
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
  $('retrySave').hidden = !problem.retryable;
  $('conflictMessage').textContent = problem.message + (problem.retryable
    ? ' Your edits are still here. Retry saving when the connection is available, or download your draft.'
    : ' Download your draft before replacing it with the saved version.');
}
function collaborationControls() {
  const access = project.collaboration || {enabled: false, role: 'local'};
  $('shareProject').disabled = !access.enabled;
  $('shareProject').title = access.enabled ? 'Manage access to this manuscript' : 'Sharing is available when signed in to a shared server.';
  const editorOnly = access.role === 'editor';
  $('projectLibrary').disabled = editorOnly; $('updateBibliography').hidden = editorOnly;
  $('librarySyncHint').textContent = editorOnly ? 'The owner manages the linked personal library. The project BibTeX files are shared.' : 'Linked libraries update the bibliography automatically. Manual entries and existing citations are kept.';
  $('projectLibrary').value = project.collection || '';
  $('projectShared').hidden = !access.shared;
  const status = project.bibliography_status;
  $('bibliographyState').textContent = status?.state === 'synced' ? 'Library bibliography synced' : '';
  $('bibliographyWarning').hidden = status?.state !== 'needs_review';
  $('bibliographyMessage').textContent = status?.state === 'needs_review' ? status.message : '';
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
    const error = new Error('These changes are too large to safely apply in the open editor. Your draft has been kept.');
    error.syncConflict = true; throw error;
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
  saving = (async () => { let result; try {
    result = await api('/api/writing', payload); unconfirmedSave = null; await finishComposition();
    const dirty = editVersion !== version;
    const updated = dirty ? rebaseDraft(sent, project, result, diffTextChanges) : result;
    adoptProject(updated, result, dirty);
    if (dirty) savedVersion = version;
    window.dispatchEvent(new CustomEvent('origin-document-saved', {detail: {kind: 'write', id: result.id}}));
  } catch (e) {
    if (e.retryable && !result) unconfirmedSave = {sent, version};
    pauseSaving(e); throw e;
  } })();
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
async function synchronizeProject(forceBibliography = false) {
  if (forceBibliography && syncBusy) {
    const deadline = Date.now() + 30000;
    while (syncBusy && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 80));
    if (syncBusy) throw new Error('Bibliography sync is still running. Please retry shortly.');
  }
  clearTimeout(syncTimer);
  if (syncBusy || (document.hidden && !forceBibliography) || saveBlocked || !project) { scheduleSync(); return; }
  syncBusy = true;
  try {
    const checkBib = project.bibliography_auto !== false && (project.collection || project.collaboration?.role === 'editor') && (forceBibliography || Date.now() - lastBibliographyCheck > 15000);
    if (!checkBib && !project.collaboration?.enabled) return;
    const remote = checkBib ? await api('/api/writing', {action: 'refresh_bibliography', id: project.id})
      : await api('/api/writing?revision=' + encodeURIComponent(project.id));
    if (checkBib) lastBibliographyCheck = Date.now();
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
    if (error.syncConflict) pauseSaving(error);
    else if ([401, 403, 404].includes(error.status)) pauseSaving(new Error('Project access is no longer available. Your local draft is still here.'));
    else if (!saveBlocked) $('syncState').textContent = 'Sync reconnecting…';
  } finally { syncBusy = false; scheduleSync(); }
}
function scheduleSync() {
  clearTimeout(syncTimer);
  if (project && !saveBlocked && (project.collaboration?.enabled || (project.collection && project.bibliography_auto !== false))) syncTimer = setTimeout(() => synchronizeProject(), Math.min(30000, (project.collaboration?.enabled ? 2500 : 15000) * 2 ** Math.min(syncFailures, 4)));
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
    await save(); await synchronizeProject(true); await save(); const version = editVersion; startedVersion = version; const result = await api('/api/writing', {action: 'compile', id: project.id, revision: project.revision, force});
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
  project = await api('/api/writing?id=' + encodeURIComponent(id)); document.body.classList.add('writing'); document.title = project.name + ' · Quill';
  serverProject = snapshotProject(project);
  content.innerHTML = `
    <div class="writer-toolbar">
      <a href="/write" class="button back-projects" title="Back to projects">← Projects</a>
      <div class="project-heading"><h1><span id="projectTitle"></span><span id="projectShared" class="sharing-badge" hidden>Shared</span></h1><span id="savedState" role="status">All changes saved</span><span id="syncState" role="status"></span><span id="bibliographyState" role="status"></span></div>
      <div class="writer-actions">
        <button id="projectChat" title="Chat about this manuscript and review proposed edits" aria-controls="projectChatPanel" aria-expanded="false">Project chat</button>
        <button id="quillModels" title="Choose the model for writing assistance">Models</button>
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
          <button id="draft">Download draft</button><button id="backupProject">Backups / Dropbox folder</button><button id="resetLayout">Reset layout</button>
        </div></details>
      </div>
    </div>
    <aside id="collaborationConflict" class="writing-conflict" role="alert" hidden><p id="conflictMessage"></p><button id="retrySave" hidden>Retry saving</button><button id="conflictDraft">Download draft</button><button id="conflictReload">Reload saved version</button></aside>
    <aside id="bibliographyWarning" class="writing-conflict" role="alert" hidden><p id="bibliographyMessage"></p><button id="reviewBibliography">Review references</button></aside>
    <div class="editor-grid" id="writerGrid">
      <aside class="files" id="filesPane" aria-label="Project files">
        <div class="files-heading"><strong>File tree</strong><button id="hideFiles" title="Hide file sidebar" aria-label="Hide file sidebar">×</button></div>
        <div class="file-actions"><button id="addFile" title="New text file">+ New file</button><button id="uploadFile" title="Upload source files or figures">↑ Upload</button><input type="file" id="fileUpload" accept=".tex,.bib,.sty,.cls,.bst,.bbl,.txt,.md,.csv,.png,.jpg,.jpeg,.pdf" multiple hidden></div>
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
          <button id="languageCheck" title="Ask Quill about your selected text" aria-label="Ask Quill">Ask Quill</button>
          <span class="code-zoom"><button id="codeZoomOut" title="Smaller code" aria-label="Decrease code zoom">−</button>
          <label class="code-zoom-value"><input id="codeZoom" type="number" inputmode="decimal" step="any" value="11" aria-label="Code font size" title="Any positive font size in pixels. Enter to apply; Escape to cancel."><span aria-hidden="true">px</span></label>
          <button id="codeZoomIn" title="Larger code" aria-label="Increase code zoom">+</button></span>
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
  sourceEditor = createLatexEditor($('source'), (name, text) => { project.files[name] = text; changed(); clearTimeout(outlineTimer); outlineTimer = setTimeout(outline, 300); }, (line, col) => { $('cursorPosition').textContent = `Ln ${line}, Col ${col}`; }, () => project.files);
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
  $('projectLibrary').onchange = () => { project.collection = $('projectLibrary').value; changed(); scheduleSync(); };
  $('mainFile').onchange = () => { project.main = $('mainFile').value; changed(); };
  $('addFile').onclick = () => askName('New text file (e.g. sections/methods.tex)', async name => { if (!textTypes.test(name) || !/^[A-Za-z0-9_][A-Za-z0-9_./ -]*$/.test(name) || name.split('/').some(p => !p || p.startsWith('.'))) throw new Error('Choose a safe relative text filename.'); if (name in project.files) throw new Error('That file already exists.'); project.files[name] = ''; activeFile = name; fileList(); changed(); });
  $('uploadFile').onclick = () => $('fileUpload').click();
  $('fileUpload').onchange = async () => {
    const upload = $('uploadFile'); upload.disabled = true;
    try {
      const version = editVersion;
      const added = await prepareProjectUploads([...$('fileUpload').files], project.files);
      if (editVersion !== version) throw new Error('The project changed during upload. Please select the files again.');
      if (Object.keys(added).some(name => Object.keys(project.files).some(current => current.toLowerCase() === name.toLowerCase()))) throw new Error('The project changed during upload. Please select the files again.');
      Object.assign(project.files, added); fileList(); changed();
      notice(`${Object.keys(added).length} file(s) added. Waiting for autosave…`);
    } catch (e) { notice(e.message, true); }
    finally { $('fileUpload').value = ''; upload.disabled = false; }
  };
  const insertCitation = key => {
    if (!/\.tex$/i.test(activeFile)) selectFile(project.main);
    if (!/\.tex$/i.test(activeFile)) return false;
    writerLayout.showCode(); return sourceEditor.insertCitation(key);
  };
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
      try {
        const dirty = editVersion !== savedVersion;
        adoptProject(dirty ? rebaseDraft(serverProject, project, updated, diffTextChanges) : updated, updated, dirty);
        if (dirty) saveTimer = setTimeout(() => save().catch(error => notice(error.message, true)), 650);
      } catch (error) { pauseSaving(error); throw error; }
      $('previewState').textContent = 'Bibliography updated · recompile to see changes'; scheduleSync();
      if ($('autoCompile').checked) { clearTimeout(compileTimer); compileTimer = setTimeout(() => compile().catch(e => notice(e.message, true)), 1600); }
    },
    insertCitation
  }).catch(e => notice(e.message, true)); };
  $('referenceButton').onclick = $('insertCitation').onclick = $('updateBibliography').onclick = $('reviewBibliography').onclick = references;
  $('languageCheck').hidden = !state.language_check;
  $('quillModels').onclick = openModels;
  const projectChat = setupProjectChat({api, save, getProject: () => project, getEditor: () => sourceEditor,
    enabled: state.language_check, openModels, notify: notice,
    reveal: (file, line) => { selectFile(file); writerLayout.showCode(); sourceEditor.jump(line); }});
  $('projectChat').onclick = projectChat.open;
  $('languageCheck').onclick = () => projectChat.open({selection: sourceEditor.selection()});
  setupLanguageMenu($('source'), {getEditor: () => sourceEditor, enabled: state.language_check,
    open: selected => projectChat.open({selection: selected})});
  $('templateButton').onclick = () => openTemplateGallery(state.templates, item => newManuscript(item, project.collection));
  $('draft').onclick = () => download(JSON.stringify({name: project.name, main: project.main, files: project.files}, null, 2), 'quill-draft.json', 'application/json');
  const backup = setupProjectBackup({getProject: () => project, save: async () => { await save(); await synchronizeProject(true); await save(); }, notify: notice});
  $('backupProject').onclick = backup.open;
  $('conflictDraft').onclick = $('draft').onclick;
  $('retrySave').onclick = async () => {
    const retry = $('retrySave'); if (retry.disabled) return;
    retry.disabled = true;
    try {
      if (unconfirmedSave) {
        const remote = await api('/api/writing?id=' + encodeURIComponent(project.id));
        const {sent, version} = unconfirmedSave;
        const confirmed = remote.revision > sent.revision && remote.main === sent.main
          && (remote.collection || '') === sent.collection
          && Object.keys(remote.files).length === Object.keys(sent.files).length
          && Object.entries(sent.files).every(([name, value]) => remote.files[name] === value);
        if (confirmed) {
          await finishComposition();
          adoptProject(rebaseDraft(sent, project, remote, diffTextChanges), remote, editVersion !== version);
        }
        unconfirmedSave = null;
      }
      saveBlocked = false;
      await save(); $('collaborationConflict').hidden = true; notice('All changes saved.'); scheduleSync();
      if ($('autoCompile').checked) compile().catch(error => notice(error.message, true));
    } catch (error) { pauseSaving(error); notice(error.message, true); }
    finally { retry.disabled = false; }
  };
  $('conflictReload').onclick = async () => {
    if (!confirm('Replace your open draft with the saved project? Download your draft first if you want to keep it.')) return;
    try {
      const updated = await api('/api/writing?id=' + encodeURIComponent(project.id));
      await finishComposition();
      sourceEditor.destroy();
      sourceEditor = createLatexEditor($('source'), (name, text) => { project.files[name] = text; changed(); clearTimeout(outlineTimer); outlineTimer = setTimeout(outline, 300); }, (line, col) => { $('cursorPosition').textContent = `Ln ${line}, Col ${col}`; }, () => project.files);
      adoptProject(updated); unconfirmedSave = null; saveBlocked = false; $('collaborationConflict').hidden = true; notice('Saved version loaded.'); scheduleSync();
    } catch (error) { notice(error.message, true); }
  };
  $('shareProject').onclick = () => openSharing({api, project, notice, changed: access => { project.collaboration = access; collaborationControls(); }}).catch(error => notice(error.message, true));
  collaborationControls(); scheduleSync();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) synchronizeProject(); });
  window.addEventListener('pagehide', () => clearTimeout(syncTimer));
  $('exportZip').href = '/api/writing/export?id=' + project.id;
  $('exportZip').onclick = async e => { e.preventDefault(); try { await save(); await synchronizeProject(true); await save(); location.href = $('exportZip').href; } catch (err) { notice(err.message, true); } };
  $('compile').disabled = $('compileCode').disabled = !state.compiler.configured; $('autoCompile').disabled = !state.compiler.configured;
  $('compile').title = 'Compile changes or reuse the saved PDF. Shift-click forces a fresh build.';
  $('compile').onclick = event => { $('previewState').textContent = 'Compiling…'; compile(Boolean(event.shiftKey)).catch(e => notice(e.message, true)); };
  $('compileCode').onclick = $('compile').onclick;
  $('autoCompile').onchange = () => { clearTimeout(compileTimer); if ($('autoCompile').checked) compile().catch(e => notice(e.message, true)); };
  if (!state.compiler.configured) notice(state.compiler.message);
  document.addEventListener('keydown', e => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save().catch(e => notice(e.message, true)); } });
  document.addEventListener('keydown', e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && state.compiler.configured) { e.preventDefault(); compile(e.shiftKey).catch(e => notice(e.message, true)); } });
  window.addEventListener('beforeunload', e => { if (editVersion !== savedVersion) { e.preventDefault(); e.returnValue = ''; } });
}

window.originragSaveDraft = async () => { if (project) await save(); };
let openingWorkspace = false;
async function openWorkspace() {
  if (openingWorkspace) return;
  openingWorkspace = true;
  notice('');
  const loading = node('p', 'Opening your workspace…');
  loading.setAttribute('role', 'status');
  content.replaceChildren(loading);
  try {
    const [profile, snapshot] = await Promise.all([getAppProfile(), api()]);
    appProfile = profile; state = snapshot;
    for (const item of document.querySelectorAll('[data-feature]')) item.hidden = !appProfile.features[item.dataset.feature];
    content.replaceChildren();
    if (mode === '/bookshelf') await shelf();
    else if (mode === '/library') await library();
    else if (mode === '/write') { const id = new URLSearchParams(location.search).get('id'); if (id) await editor(id); else await projects(); }
    else home();
  } catch (error) {
    const panel = node('section', undefined, 'library-empty');
    panel.append(node('h2', 'This page could not be loaded'), node('p', error.message), button('Retry loading', openWorkspace));
    content.replaceChildren(panel);
  } finally { openingWorkspace = false; }
}
openWorkspace();
