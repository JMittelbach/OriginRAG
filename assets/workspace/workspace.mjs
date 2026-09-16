const $ = id => document.getElementById(id);
const content = $('content');
const mode = location.pathname;
let sourceEditor, writerLayout, outlineTimer;
const textTypes = /\.(tex|bib|sty|cls|bst|bbl|txt|md|csv)$/i;
let state, project, activeFile, editVersion = 0, savedVersion = 0, saving = null;
let saveTimer, compileTimer, compiling = false, saveBlocked = false, pdfURL = '', pdfDocument, pdfLoadingTask;
function node(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function notice(message, error = false) { $('notice').textContent = message; $('notice').classList.toggle('error', error); }
async function api(path = '/api/writing', payload) {
  const headers = new Headers();
  const token = document.cookie.split('; ').find(v => v.startsWith('pfempire_csrf='))?.split('=')[1];
  if (token) headers.set('X-CSRF-Token', decodeURIComponent(token));
  if (payload) headers.set('Content-Type', 'application/json');
  const r = await fetch(path, {method: payload ? 'POST' : 'GET', headers, body: payload ? JSON.stringify(payload) : undefined});
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status}).`);
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
   ['04', 'OriginWrite', 'Your LaTeX manuscript, source and PDF side by side.', '/write']].forEach(([num, name, desc, href]) => {
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
  h.append(link('Find or import papers', '/chat', 'button'));
  content.insertAdjacentHTML('beforeend', '<div class="library-layout"><aside><p class="eyebrow">MY COLLECTIONS</p><div id="collections" class="collection-list"></div></aside><section><form id="searchForm" class="search"><input id="query" type="search" placeholder="Search title, author, journal…" aria-label="Search library"><button>Search</button></form><p id="libraryCount" class="muted"></p><div id="papers"></div><div class="actions"><button id="previousPapers">Previous</button><button id="nextPapers">Next</button></div></section></div>');
  let selected = '', offset = 0, sequence = 0;
  const collectionButtons = [];
  for (const c of [{id: '', name: 'All papers'}, ...state.collections]) {
    const b = button(c.name, async () => { selected = c.id; offset = 0; collectionButtons.forEach(([id, el]) => el.setAttribute('aria-pressed', String(id === selected))); await load(); });
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

async function projects() {
  const h = heading('OriginWrite', 'A quiet space to turn your research into a manuscript.');
  h.append(button('+ New manuscript', () => askName('New manuscript', async name => { const p = await api('/api/writing', {action: 'create_project', name}); location.href = '/write?id=' + p.id; }), 'primary'));
  const upload = node('input'); upload.type = 'file'; upload.accept = '.zip'; upload.hidden = true; content.append(upload);
  h.append(button('Import LaTeX ZIP', () => upload.click()));
  upload.onchange = async () => {
    const f = upload.files[0]; if (!f) return;
    if (f.size > 12000000) return notice('Choose a ZIP under 12 MB.', true);
    askName('Import manuscript', async name => { const bytes = await f.arrayBuffer(); const zip = bytes64(bytes); const p = await api('/api/writing', {action: 'create_project', name, zip}); location.href = '/write?id=' + p.id; });
  };
  const cards = node('div', undefined, 'cards'); content.append(cards);
  for (const p of state.projects) { const c = link('', '/write?id=' + p.id, 'card'); c.append(node('h2', p.name), node('p', `${p.count} files · revision ${p.revision}`)); cards.append(c); }
  if (!state.projects.length) cards.append(node('p', 'Start a manuscript or import an existing LaTeX project.', 'muted'));
  const templates = node('section', undefined, 'template-list'); templates.style.marginTop = '40px'; templates.append(node('h2', 'Journal templates'), node('p', 'Get the current template from the publisher, then import its LaTeX ZIP. These are official-source links, not bundled or modified journal templates.', 'muted'));
  for (const t of state.templates) { const a = link(t.name + ' ↗', t.url); a.target = '_blank'; a.rel = 'noopener noreferrer'; templates.append(a); }
  templates.append(node('p', 'Check the journal’s instructions. Package and engine requirements differ between journals.', 'hint')); content.append(templates);
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
  saveTimer = setTimeout(() => save().catch(e => notice(e.message, true)), 650);
  if ($('autoCompile').checked) compileTimer = setTimeout(() => compile().catch(e => notice(e.message, true)), 1600);
}
async function save() {
  if (saveBlocked) throw new Error('Automatic saving paused after an error. Download your draft before reloading.');
  if (saving) { await saving; return save(); }
  if (editVersion === savedVersion) return;
  const version = editVersion;
  const payload = {action: 'save_project', id: project.id, revision: project.revision, files: {...project.files}, main: project.main, collection: project.collection};
  $('savedState').textContent = 'Saving…';
  saving = (async () => { try { const result = await api('/api/writing', payload); project.revision = result.revision; savedVersion = version; $('savedState').textContent = editVersion === savedVersion ? 'All changes saved' : 'Unsaved changes'; }
    catch (e) { saveBlocked = true; $('savedState').textContent = 'Not saved · download draft'; throw e; } })();
  try { await saving; } finally { saving = null; }
  if (editVersion !== savedVersion) await save();
}
function selectFile(name) {
  activeFile = name; $('fileLabel').textContent = name; const editable = textTypes.test(name);
  sourceEditor.open(name, editable ? project.files[name] : 'Binary asset. Included in compilation and ZIP export.', editable);
  for (const b of $('fileList').querySelectorAll('[data-file]')) { b.classList.toggle('active', b.dataset.file === name); b.setAttribute('aria-current', String(b.dataset.file === name)); }
  for (const b of document.querySelectorAll('[data-edit-command]')) b.disabled = !editable && b.dataset.editCommand !== 'search';
  outline();
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
  const zoom = $('pdfZoom').value;
  for (const canvas of $('pdfPages').querySelectorAll('canvas')) {
    canvas.style.width = zoom === 'fit' ? '100%' : `${Number(canvas.dataset.pageWidth) * Number(zoom)}px`;
    canvas.style.maxWidth = zoom === 'fit' ? '100%' : 'none';
  }
}
async function renderPDF(bytes) {
  if (pdfLoadingTask) await pdfLoadingTask.destroy();
  const pdfjs = await import('/assets/vendor/pdfjs/legacy/build/pdf.min.mjs');
  pdfjs.GlobalWorkerOptions.workerSrc = '/assets/vendor/pdfjs/legacy/build/pdf.worker.min.mjs';
  pdfLoadingTask = pdfjs.getDocument({data: bytes, isEvalSupported: false, cMapUrl: '/assets/vendor/pdfjs/cmaps/', cMapPacked: true, standardFontDataUrl: '/assets/vendor/pdfjs/standard_fonts/', wasmUrl: '/assets/vendor/pdfjs/wasm/'});
  pdfDocument = await pdfLoadingTask.promise;
  $('pdfPages').replaceChildren();
  for (let n = 1; n <= Math.min(pdfDocument.numPages, 40); n++) {
    const page = await pdfDocument.getPage(n); const unscaled = page.getViewport({scale: 1}); const scale = Math.min(2, Math.sqrt(2000000 / (unscaled.width * unscaled.height))); const viewport = page.getViewport({scale}); const canvas = node('canvas'); canvas.width = viewport.width; canvas.height = viewport.height; canvas.dataset.pageWidth = unscaled.width; canvas.setAttribute('aria-label', `PDF page ${n}`); $('pdfPages').append(canvas); zoomPDF(); await page.render({canvasContext: canvas.getContext('2d'), viewport}).promise;
  }
  if (pdfDocument.numPages > 40) $('pdfPages').append(node('p', 'Preview shows the first 40 pages. Download the PDF for all pages.'));
  $('pdfCount').textContent = `${pdfDocument.numPages} ${pdfDocument.numPages === 1 ? 'page' : 'pages'}`;
}
async function compile() {
  if (compiling) return;
  compiling = true; $('compile').disabled = true;
  $('compileCode').disabled = true;
  let startedVersion = editVersion;
  $('previewState').textContent = 'Compiling…';
  try {
    await save(); const version = editVersion; startedVersion = version; const result = await api('/api/writing', {action: 'compile', id: project.id, revision: project.revision});
    $('compileLog').textContent = result.log || 'Compilation completed.'; $('problems').replaceChildren();
    for (const p of result.diagnostics) $('problems').append(button(`${p.file ? p.file + ':' + p.line + ' · ' : ''}${p.message}`, () => {
      if (!(p.file in project.files) || !p.line) return; selectFile(p.file); writerLayout.showCode(); sourceEditor.jump(p.line);
    }));
    if (!result.ok) { $('logDetails').open = true; $('previewState').textContent = 'Compilation failed · previous PDF may be out of date'; notice('Compilation failed. Check the log below; your source is saved.', true); return; }
    const bytes = Uint8Array.from(atob(result.pdf), c => c.charCodeAt(0));
    if (pdfURL) URL.revokeObjectURL(pdfURL); pdfURL = URL.createObjectURL(new Blob([bytes], {type: 'application/pdf'})); $('downloadPDF').href = pdfURL; $('downloadPDF').hidden = false;
    await renderPDF(bytes);
    $('previewState').textContent = version === editVersion ? `PDF · revision ${result.revision} · ${result.seconds}s` : 'Previous revision · new changes pending'; notice(result.diagnostics.length ? 'PDF compiled. Review the warnings below.' : 'PDF compiled.');
  } catch (e) { $('previewState').textContent = 'Preview not updated'; throw e; }
  finally { compiling = false; $('compile').disabled = $('compileCode').disabled = !state.compiler.configured; if (!saveBlocked && $('autoCompile').checked && editVersion !== startedVersion) { clearTimeout(compileTimer); compileTimer = setTimeout(() => compile().catch(e => notice(e.message, true)), 600); } }
}

async function editor(id) {
  const [{createLatexEditor}, {setupWriterLayout}] = await Promise.all([import('./latex-editor.mjs'), import('./writer-layout.mjs')]);
  project = await api('/api/writing?id=' + encodeURIComponent(id)); document.body.classList.add('writing'); document.title = project.name + ' · OriginWrite';
  content.innerHTML = `
    <div class="writer-toolbar">
      <a href="/write" class="button back-projects" title="Back to projects">← Projects</a>
      <div class="project-heading"><h1 id="projectTitle"></h1><span id="savedState" role="status">All changes saved</span></div>
      <div class="writer-actions">
        <button id="toggleFiles" aria-controls="filesPane" aria-expanded="true" title="Show or hide the file sidebar">☷ <span>Files</span></button>
        <select id="layoutMode" aria-label="Editor layout"><option value="split">Code & PDF</option><option value="code">Code only</option><option value="pdf">PDF only</option></select>
        <button id="swapPanes" title="Swap code and PDF positions" aria-label="Swap code and PDF positions">⇄</button>
        <button id="editorTheme" title="Toggle dark code editor" aria-label="Dark code editor" aria-pressed="false">◐</button>
        <details class="writer-menu"><summary aria-label="Project options" title="Project options">•••</summary><div>
          <a id="exportZip" class="button">Export ZIP</a><button id="draft">Download draft</button><button id="resetLayout">Reset layout</button>
        </div></details>
      </div>
    </div>
    <div class="editor-grid" id="writerGrid">
      <aside class="files" id="filesPane" aria-label="Project files">
        <div class="files-heading"><strong>File tree</strong><button id="hideFiles" title="Hide file sidebar" aria-label="Hide file sidebar">×</button></div>
        <div class="file-actions"><button id="addFile" title="New text file">+ New file</button><button id="uploadFile" title="Upload source files or figures">↑ Upload</button><input type="file" id="fileUpload" multiple hidden></div>
        <div id="fileList"></div>
        <details class="outline-panel" open><summary>File outline</summary><div id="outline"></div></details>
        <details class="project-settings"><summary>Project settings & references</summary>
          <label>Main document<select id="mainFile"></select></label>
          <label>Project library<select id="projectLibrary"><option value="">No collection</option></select></label>
          <button id="updateBibliography">Update references.bib</button><p class="hint">Replaces references.bib with this collection’s metadata. Check imported citation details.</p>
        </details>
      </aside>
      <div id="filesResize" class="splitter files-resize" role="separator" tabindex="0" aria-label="Resize file sidebar" aria-controls="filesPane" aria-orientation="vertical" aria-valuemin="160" aria-valuemax="400" aria-valuenow="230" title="Drag to resize files · double-click to reset"></div>
      <section class="source-pane" id="sourcePane" aria-label="Code editor">
        <div class="source-tab"><span class="file-icon">TₑX</span><span id="fileLabel"></span><span class="tab-dot" aria-hidden="true"></span></div>
        <div class="code-toolbar" role="toolbar" aria-label="Code tools">
          <button data-edit-command="undo" title="Undo (Ctrl/Cmd+Z)" aria-label="Undo">↶</button><button data-edit-command="redo" title="Redo (Ctrl/Cmd+Shift+Z)" aria-label="Redo">↷</button><span class="tool-divider"></span>
          <button data-edit-command="bold" title="Bold selection" aria-label="Bold selection"><b>B</b></button><button data-edit-command="italic" title="Italic selection" aria-label="Italic selection"><i>I</i></button><button data-edit-command="comment" title="Toggle comment (Ctrl/Cmd+/)" aria-label="Toggle comment">%</button>
          <button data-edit-command="search" class="search-code" title="Find and replace (Ctrl/Cmd+F)" aria-label="Find and replace">⌕ <span>Find</span></button>
          <button id="compileCode" title="Recompile (Ctrl/Cmd+Enter)" aria-label="Recompile manuscript">▶</button>
        </div>
        <div id="source" class="code-editor"></div><div class="code-status"><span>LaTeX</span><span id="cursorPosition">Ln 1, Col 1</span><span>UTF-8</span></div>
      </section>
      <div id="panesResize" class="splitter panes-resize" role="separator" tabindex="0" aria-label="Resize code and PDF panes" aria-controls="sourcePane previewPane" aria-orientation="vertical" aria-valuemin="20" aria-valuemax="80" aria-valuenow="50" title="Drag to resize code and PDF · double-click for equal widths"></div>
      <section class="preview-pane" id="previewPane" aria-label="PDF preview">
        <div class="pdf-toolbar"><button id="compile" class="primary">▶ Recompile</button><label class="auto-compile"><input id="autoCompile" type="checkbox">Auto</label><a id="downloadPDF" class="button" download="manuscript.pdf" title="Download PDF" aria-label="Download PDF" hidden>↓</a>
          <select id="pdfZoom" aria-label="PDF zoom"><option value="fit">Fit width</option><option value="0.75">75%</option><option value="1">100%</option><option value="1.25">125%</option><option value="1.5">150%</option><option value="2">200%</option></select>
        </div><div id="pdfPages"><div class="pdf-empty"><span aria-hidden="true">▤</span><h2>Your manuscript, on paper</h2><p>Recompile to bring your LaTeX to life.</p></div></div>
        <div class="pdf-status"><span id="previewState" role="status">Compile to see your PDF</span><span id="pdfCount"></span></div>
      </section>
    </div>
    <details id="logDetails"><summary>Errors, warnings & compile log</summary><div class="log-body"><div id="problems"></div><pre id="compileLog">No compilation yet.</pre></div></details>`;
  sourceEditor = createLatexEditor($('source'), (name, text) => { project.files[name] = text; changed(); clearTimeout(outlineTimer); outlineTimer = setTimeout(outline, 300); }, (line, col) => { $('cursorPosition').textContent = `Ln ${line}, Col ${col}`; });
  writerLayout = setupWriterLayout(() => sourceEditor.measure());
  for (const b of document.querySelectorAll('[data-edit-command]')) b.onclick = () => sourceEditor.command(b.dataset.editCommand);
  $('pdfZoom').onchange = zoomPDF;
  $('projectTitle').textContent = project.name; activeFile = project.main; fileList();
  state.collections.forEach(c => $('projectLibrary').append(new Option(c.name, c.id))); $('projectLibrary').value = project.collection;
  $('projectLibrary').onchange = () => { project.collection = $('projectLibrary').value; changed(); };
  $('mainFile').onchange = () => { project.main = $('mainFile').value; changed(); };
  $('addFile').onclick = () => askName('New text file (e.g. sections/methods.tex)', async name => { if (!textTypes.test(name) || !/^[A-Za-z0-9_][A-Za-z0-9_./ -]*$/.test(name) || name.split('/').some(p => !p || p.startsWith('.'))) throw new Error('Choose a safe relative text filename.'); if (name in project.files) throw new Error('That file already exists.'); project.files[name] = ''; activeFile = name; fileList(); changed(); });
  $('uploadFile').onclick = () => $('fileUpload').click();
  $('fileUpload').onchange = async () => { try { for (const f of $('fileUpload').files) { if (f.size > 8000000) throw new Error('Individual uploads are limited to 8 MB.'); if (!/\.(tex|bib|sty|cls|bst|bbl|txt|md|csv|png|jpg|jpeg|pdf)$/i.test(f.name)) throw new Error('Unsupported file type.'); if (f.name in project.files) throw new Error(`${f.name} already exists. Edit it or choose another name.`); project.files[f.name] = textTypes.test(f.name) ? await f.text() : bytes64(await f.arrayBuffer()); changed(); } fileList(); } catch (e) { notice(e.message, true); } };
  $('updateBibliography').onclick = async () => { try { if (!project.collection) throw new Error('Choose a project library first.'); if (project.files['references.bib']?.trim() && !confirm('Replace references.bib with the selected collection? Export a ZIP first to preserve manual edits.')) return; const result = await api('/api/writing/bibliography?id=' + project.collection); project.files['references.bib'] = result.bibtex; activeFile = 'references.bib'; fileList(); changed(); } catch (e) { notice(e.message, true); } };
  $('draft').onclick = () => download(JSON.stringify({name: project.name, main: project.main, files: project.files}, null, 2), 'originwrite-draft.json', 'application/json');
  $('exportZip').href = '/api/writing/export?id=' + project.id;
  $('exportZip').onclick = async e => { e.preventDefault(); try { await save(); location.href = $('exportZip').href; } catch (err) { notice(err.message, true); } };
  $('compile').disabled = $('compileCode').disabled = !state.compiler.configured; $('autoCompile').disabled = !state.compiler.configured;
  $('compile').onclick = () => { $('previewState').textContent = 'Compiling…'; compile().catch(e => notice(e.message, true)); };
  $('compileCode').onclick = $('compile').onclick;
  $('autoCompile').onchange = () => { clearTimeout(compileTimer); if ($('autoCompile').checked) compile().catch(e => notice(e.message, true)); };
  if (!state.compiler.configured) notice(state.compiler.message);
  document.addEventListener('keydown', e => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save().catch(e => notice(e.message, true)); } });
  document.addEventListener('keydown', e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && state.compiler.configured) { e.preventDefault(); compile().catch(e => notice(e.message, true)); } });
  window.addEventListener('beforeunload', e => { if (editVersion !== savedVersion) { e.preventDefault(); e.returnValue = ''; } });
}

try {
  await refresh();
  if (mode === '/bookshelf') await shelf();
  else if (mode === '/library') await library();
  else if (mode === '/write') { const id = new URLSearchParams(location.search).get('id'); if (id) await editor(id); else await projects(); }
  else home();
} catch (e) { notice(e.message, true); }
