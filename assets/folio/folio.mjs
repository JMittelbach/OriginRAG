import { requestJSON } from "../workspace/request.mjs";
import {addDialogClose} from '../workspace/dialog-close.mjs';
import { createFolioEditor, safeLink, collectFolioNotes, folioSearchState, closeHistory } from "./editor.mjs";
import {captureFormatting, applyFormatting, fontSizePoints, applyFontSize} from './editing-tools.mjs';
import {installFormatTools} from './format-pane.mjs';
import { icon, decorateRibbon } from './icons.mjs';
import { openDrawing, drawingIcon } from './drawing.mjs';
import { STYLE_SETS, applyStyleSet, documentHeadings, updateContents, insertCrossReference } from './document-tools.mjs';
import { FILE_FORMATS, localFilename, hasSavePicker, chooseSaveFile, writeLocalFile } from './file-save.mjs';
const app = document.getElementById("folioApp");
const noticeElement = document.getElementById("folioNotice");
const state = { doc: null, editor: null, version: 0, saved: 0, saving: null, copying: false, paused: false, timer: null, tab: "Home", sidebar: "", zoom: 100, list: [], viewTimer: null };
const $ = (id) => document.getElementById(id);
const tabId = name => 'folioTab' + name.replaceAll(' ', '');
const bookmarks = new Set();
let formatBrush = null, findRefresh = null, paragraphMarks = false, formatTools = null;
const wordTotals = new WeakMap();
let countTimer;
function rememberSelection() {
  const saved = {editor: state.editor, bookmark: state.editor.state.selection.getBookmark()};
  bookmarks.add(saved);
  return saved;
}
function restoreSelection(saved) {
  if (!saved || saved.editor !== state.editor || state.editor.isDestroyed) throw new Error('The document changed. Please select the content again.');
  const selection = saved.bookmark.resolve(state.editor.state.doc);
  state.editor.view.dispatch(state.editor.state.tr.setSelection(selection));
}
function editorTransaction({transaction}) {
  if (transaction.docChanged || transaction.selectionSet) scheduleWordCount();
  if (transaction.docChanged) {
    for (const saved of bookmarks) {
      if (saved.editor === state.editor) saved.bookmark = saved.bookmark.map(transaction.mapping);
    }
    findRefresh?.();
  }
  updateToolbar();
}
function objectAtSelection(type) {
  const selection = state.editor.state.selection;
  if (selection.node?.type.name === type) return {node: selection.node, pos: selection.from};
  for (let depth = selection.$from.depth; depth > 0; depth--) {
    const node = selection.$from.node(depth);
    if (node.type.name === type) return {node, pos: selection.$from.before(depth)};
  }
  return null;
}
function el(tag, text, className) {
  const n = document.createElement(tag);
  if (text !== void 0) n.textContent = text;
  if (className) n.className = className;
  return n;
}
function notice(text, error = false) {
  noticeElement.textContent = text;
  noticeElement.classList.toggle("error", error);
}
function btn(label, action, className = "", text = label) {
  const n = el("button", text, className);
  n.type = "button";
  n.setAttribute("aria-label", label);
  n.title = label;
  n.addEventListener("click", async () => {
    if (n.disabled) return;
    const tab = n.getAttribute('role') === 'tab';
    if (!tab) n.disabled = true;
    try {
      await action();
    } catch (error) {
      notice(error.message, true);
    } finally {
      if (n.isConnected && !tab) n.disabled = false;
      updateToolbar();
    }
  });
  return n;
}
function select(label, options, value, action) {
  const n = el("select");
  n.setAttribute("aria-label", label);
  n.title = label;
  for (const [key, text] of options) {
    const option = el("option", text);
    option.value = key;
    n.append(option);
  }
  n.value = value;
  n.addEventListener("change", () => {
    try {
      action(n.value);
    } catch (error) {
      notice(error.message, true);
    }
  });
  return n;
}
function field(label, input) {
  const n = el("label", void 0, "folio-field");
  n.append(el("span", label), input);
  return n;
}
async function api(payload, path = "/api/folio") {
  const headers = {};
  const token = document.cookie.split("; ").find((v) => v.startsWith("pfempire_csrf="))?.split("=")[1];
  if (token) headers["X-CSRF-Token"] = decodeURIComponent(token);
  return requestJSON(path, { payload, headers });
}
function dirty() {
  return state.doc && state.version !== state.saved;
}
function status(text) {
  if ($("folioSaveStatus")) $("folioSaveStatus").textContent = text;
}
function changed() {
  state.version++;
  state.paused = false;
  status("Unsaved changes");
  clearTimeout(state.timer);
  state.timer = setTimeout(() => save().catch(() => {
  }), 700);
  refreshDocumentInfo();
}
function snapshot() {
  return { id: state.doc.id, revision: state.doc.revision, title: $("folioTitle")?.value.trim() || "Untitled document", content: state.editor.getJSON(), page: { ...state.doc.page }, comments: structuredClone(state.doc.comments || []) };
}
async function save() {
  clearTimeout(state.timer);
  if (state.copying) return;
  if (state.saving) return state.saving;
  if (!dirty()) return;
  const version = state.version, data = snapshot(), targetDocument = state.doc;
  status("Saving…");
  state.saving = (async () => {
    try {
      const saved = await api({ action: "save", ...data });
      if (state.doc !== targetDocument) return;
      state.doc.revision = saved.revision;
      state.doc.updated_at = saved.updated_at;
      state.doc.title = data.title;
      state.saved = version;
      window.dispatchEvent(new CustomEvent('origin-document-saved', {detail: {kind: 'folio', id: saved.id}}));
      state.paused = false;
      status(dirty() ? "Unsaved changes" : "Saved");
      notice("");
    } catch (error) {
      if (state.doc !== targetDocument) throw error;
      state.paused = true;
      status("Save failed");
      notice(`${error.message} Your edits are still here. Retry Save, save a copy, or download a recovery JSON from File.`, true);
      throw error;
    } finally {
      if (state.doc === targetDocument) {
        state.saving = null;
        if (dirty() && !state.paused && !state.copying) state.timer = setTimeout(() => save().catch(() => {}), 350);
      }
    }
  })();
  return state.saving;
}
async function flush() {
  if (state.copying) throw new Error('A copy is being saved. Please wait a moment.');
  for (let i = 0; i < 3 && (dirty() || state.saving); i++) {
    if (state.saving) await state.saving;
    if (dirty()) await save();
  }
  if (dirty()) throw new Error("Please pause typing for a moment, then try again.");
}
window.originragSaveDraft = flush;
window.addEventListener('beforeprint', preparePrint);
window.addEventListener("beforeunload", (event) => {
  if (dirty()) {
    event.preventDefault();
    event.returnValue = "";
  }
});
document.addEventListener("click", (event) => {
  const anchor = event.target.closest("a[href]");
  if (state.copying && anchor) { event.preventDefault(); notice('A copy is being saved. Please wait a moment.'); return; }
  if (!dirty() || !anchor || app.contains(anchor) || event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || anchor.target === "_blank" || anchor.download || anchor.getAttribute("href").startsWith("#")) return;
  event.preventDefault();
  flush().then(() => location.assign(anchor.href)).catch((error) => notice(error.message, true));
}, true);
document.addEventListener("keydown", (event) => {
  if (event.isComposing) return;
  if (event.key === 'Escape' && formatBrush) { formatBrush = null; updateToolbar(); notice(''); }
  if (app.inert) { if ((event.ctrlKey || event.metaKey) && ['s', 'f'].includes(event.key.toLowerCase())) event.preventDefault(); return; }
  if (event.key === 'Escape') app.classList.remove('folio-focus');
  if (state.editor && !event.defaultPrevented && !event.target.closest('input, textarea, select, dialog')) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault(); linkDialog(); return;
    }
    if ((event.ctrlKey || event.metaKey) && event.altKey && event.key.toLowerCase() === 'm') {
      event.preventDefault(); showSidebar('comments', true); return;
    }
    if (event.altKey && event.key === 'F10') {
      event.preventDefault(); $(tabId(state.tab))?.focus(); return;
    }
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    if (event.shiftKey && state.editor) {
      if (!document.querySelector('dialog[open]')) saveAsDialog();
      return;
    }
    flush().catch((error) => notice(error.message, true));
  }
  if (state.editor && !event.defaultPrevented && !document.querySelector('dialog[open]') && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
    event.preventDefault();
    showSidebar("find");
  }
});
const importInput = el("input");
importInput.type = "file";
importInput.accept = ".docx,.json";
importInput.id = "folioImport";
importInput.hidden = true;
document.body.append(importInput);
importInput.addEventListener("change", async () => {
  const file = importInput.files[0];
  importInput.value = "";
  if (!file) return;
  try {
    if (file.size > 8_000_000) throw new Error("Please choose a document smaller than 8 MB.");
    await flush();
    const content_base64 = (await readFile(file)).split(",")[1];
    const doc = await api({ action: "import", filename: file.name, content_base64 });
    await openDocument(doc);
    const warnings = Array.isArray(doc.warnings) ? doc.warnings.filter(warning => typeof warning === 'string') : [];
    notice(warnings.length ? `Document imported. ${warnings.join(' ')}` : "Document imported. Complex Word layouts may need adjustment.");
  } catch (error) {
    notice(error.message, true);
  }
});
function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("The file could not be read."));
    reader.readAsDataURL(file);
  });
}
function downloadBlob(content, name, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = el("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1e4);
}
async function exportBlob(format, targetDocument = state.doc, targetEditor = state.editor, signal) {
  const checkDocument = () => {
    if (signal?.aborted) throw new DOMException('File save cancelled.', 'AbortError');
    if (state.doc !== targetDocument || state.editor !== targetEditor) throw new Error('The document changed. Open Save as again for the document you want to save.');
  };
  checkDocument();
  await flush();
  checkDocument();
  const revision = targetDocument.revision, version = state.version;
  const check = () => {
    checkDocument();
    if (state.version !== version || targetDocument.revision !== revision || dirty()) throw new Error('The document changed while preparing the file. Please try again to include your latest edits.');
  };
  const response = await fetch(`/api/folio/export?id=${encodeURIComponent(targetDocument.id)}&format=${encodeURIComponent(format)}&revision=${revision}`, {signal});
  if (response.status === 409) throw new Error('This document was updated in another tab. Reopen it to review the saved version before exporting. Your current edits remain here.');
  if (!response.ok || response.redirected) throw new Error('The document could not be prepared. Check your connection and sign-in, then try again.');
  const mime = (response.headers.get('content-type') || '').split(';')[0].trim();
  if (mime !== FILE_FORMATS[format]?.mime) throw new Error('The server did not return the requested document format. No file was saved.');
  const blob = await response.blob();
  check();
  return {blob, check, title: targetDocument.title};
}
async function download(format) {
  const {blob, title} = await exportBlob(format);
  downloadBlob(blob, localFilename(title, format), blob.type);
}
function saveAsDialog() {
  if (!state.doc || !state.editor) return;
  const targetDocument = state.doc, targetEditor = state.editor;
  const filename = el('input');
  filename.setAttribute('aria-label', 'Filename');
  filename.maxLength = 160;
  filename.required = true;
  filename.value = localFilename($('folioTitle')?.value || targetDocument.title, 'docx');
  const format = select('Format', Object.entries(FILE_FORMATS).map(([key, value]) => [key, value.label]), 'docx', value => {
    filename.value = localFilename(filename.value, value);
  });
  const hint = el('p', hasSavePicker()
    ? 'Choose a filename and format. The next dialog lets you choose a folder on your computer.'
    : 'Your browser saves this file as a download. To choose a folder each time, enable “Always ask where to save files” in its download settings.', 'folio-muted');
  let closingFile = false;
  const formDialog = dialog('Save as…', [field('Filename', filename), field('Format', format), hint,
    el('p', 'This saves a separate file. Autosave continues in Origin and does not update the downloaded file. For PDF, use File → Print or save PDF.', 'folio-muted')], 'Save file', async signal => {
    const chosenFormat = format.value;
    const name = localFilename(filename.value, chosenFormat);
    filename.value = name;
    let handle;
    try {
      handle = await chooseSaveFile(name, chosenFormat);
    } catch (error) {
      if (error.name === 'AbortError') return false;
      throw new Error('The file dialog could not be opened. Check the browser’s file permissions, or use the Download buttons in File.');
    }
    if (signal.aborted) return false;
    const {blob, check} = await exportBlob(chosenFormat, targetDocument, targetEditor, signal);
    if (!handle) {
      check();
      downloadBlob(blob, name, blob.type);
      notice('Download started. Your browser controls the save location.');
      return;
    }
    try {
      await writeLocalFile(handle, blob, {check, committing: value => {
        closingFile = value;
        formDialog.querySelector('[aria-label="Cancel"]').disabled = value;
      }});
    } catch (error) {
      if (signal.aborted) return false;
      throw new Error(`The file could not be saved. Check folder permissions and free disk space, then try again. ${error.message || ''}`);
    }
    notice(`File saved: ${handle.name || name}. Autosave continues in Origin.`);
  }, {canCancel: () => !closingFile});
}
function recovery() {
  downloadBlob(JSON.stringify(snapshot(), null, 2), "Folio-recovery.json", "application/json");
  notice("A copy of your current edits has been downloaded.");
}
async function create(template = "blank") {
  await flush();
  const titles = { blank: "Untitled document", report: "Project report", meeting: "Meeting notes", letter: "Letter" };
  await openDocument(await api({ action: "create", title: titles[template], template }));
}
async function openDocument(doc, push = true) {
  await flush();
  if (typeof doc === "string") doc = await api(void 0, `/api/folio?id=${encodeURIComponent(doc)}`);
  await flush();
  formatTools?.destroy(); formatTools = null;
  state.editor?.destroy();
  formatBrush = null; findRefresh = null;
  clearTimeout(state.viewTimer);
  state.doc = doc;
  state.version = 0;
  state.saved = 0;
  state.paused = false;
  state.tab = "Home";
  state.sidebar = "";
  renderEditor();
  if (push) history.pushState({}, "", `/folio?id=${encodeURIComponent(doc.id)}`);
  document.title = `${doc.title} · Folio`;
  notice("");
}
async function dashboard(push = true) {
  await flush();
  const data = await api();
  await flush();
  formatTools?.destroy(); formatTools = null;
  state.editor?.destroy();
  state.editor = null;
  state.doc = null;
  state.list = data.documents || [];
  if (push) history.pushState({}, "", "/folio");
  document.title = "Folio · Origin";
  app.replaceChildren();
  app.className = "folio-dashboard";
  const heading = el("div", void 0, "folio-dashboard-head");
  const copy = el("div");
  copy.append(el("span", "ORIGIN WORKSPACE", "folio-eyebrow"), el("h1", "A little room for big ideas."), el("p", "Bring your next document to life with Folio.", "folio-muted"));
  const actions = el("div", void 0, "folio-actions");
  actions.append(btn("Import document", () => importInput.click(), "folio-button"), btn("New document", () => create(), "folio-button folio-primary", "+  New document"));
  heading.append(copy, actions);
  app.append(heading);
  const templates = el("section", void 0, "folio-templates");
  templates.setAttribute("aria-label", "Document templates");
  for (const [key, name, detail] of [["blank", "Blank document", "Start with a clean page"], ["report", "Project report", "Give your work a clear structure"], ["meeting", "Meeting notes", "Keep decisions moving"], ["letter", "Letter", "Make a thoughtful impression"]]) {
    const b = btn(name, () => create(key), "folio-template", "");
    const preview = el("div", void 0, `folio-template-preview folio-template-${key}`);
    preview.append(el("span", key === "blank" ? "+" : "Folio", "folio-template-mark"));
    for (let i = 0; i < (key === "blank" ? 0 : 5); i++) preview.append(el("i"));
    b.append(preview, el("strong", name), el("span", detail));
    templates.append(b);
  }
  app.append(templates);
  const row = el("div", void 0, "folio-section-title");
  row.append(el("h2", "Your documents"));
  const filter = el("input");
  filter.type = "search";
  filter.placeholder = "Search your documents";
  filter.setAttribute("aria-label", "Search documents");
  row.append(filter);
  app.append(row);
  const list = el("div", void 0, "folio-document-list");
  app.append(list);
  const renderList = () => {
    list.replaceChildren();
    const docs = state.list.filter((doc) => doc.title.toLowerCase().includes(filter.value.toLowerCase()));
    if (!docs.length) {
      const empty = el("div", void 0, "folio-empty");
      empty.append(el("div", "▤", "folio-empty-icon"), el("h3", state.list.length ? "No matching documents" : "Your next chapter starts here"), el("p", state.list.length ? "Try another search." : "Choose a template above, or import a Word document."));
      list.append(empty);
      return;
    }
    for (const doc of docs) {
      const item = el("div", void 0, "folio-document-row");
      const open = btn(doc.title, () => openDocument(doc.id), "folio-document-open", "");
      open.append(el("span", "▤", "folio-document-icon"), el("strong", doc.title), el("span", `Edited ${new Date(doc.updated_at * 1000).toLocaleDateString(void 0, { month: "short", day: "numeric", year: "numeric" })}`, "folio-muted"));
      item.append(open, btn(`Delete ${doc.title}`, async () => {
        if (!confirm(`Delete “${doc.title}”? This cannot be undone.`)) return;
        await api({ action: "delete", id: doc.id, revision: doc.revision });
        state.list = state.list.filter((d) => d.id !== doc.id);
        renderList();
      }, "folio-delete", "×"));
      list.append(item);
    }
  };
  filter.addEventListener("input", renderList);
  renderList();
}
function group(label, ...controls) {
  const n = el("div", void 0, "folio-ribbon-group");
  const c = el("div", void 0, "folio-ribbon-controls");
  c.append(...controls);
  n.append(c, el("span", label, "folio-group-label"));
  return n;
}
function command(label, method, argument, text = label) {
  const b = btn(label, () => {
    const chain = state.editor.chain().focus();
    if (argument === void 0) chain[method]().run();
    else chain[method](argument).run();
  }, "folio-tool", text);
  b.dataset.command = method;
  return b;
}
function renderEditor() {
  app.replaceChildren();
  app.className = "folio-workbench";
  const titlebar = el("div", void 0, "folio-titlebar");
  titlebar.append(btn("Documents", () => dashboard(), "folio-back", "‹  Documents"), el("span", "F", "folio-brand"));
  const title = el("input");
  title.id = "folioTitle";
  title.setAttribute("aria-label", "Document title");
  title.value = state.doc.title;
  title.maxLength = 180;
  title.addEventListener("input", changed);
  const saveStatus = el("span", "Saved", "folio-save-status");
  saveStatus.id = "folioSaveStatus";
  saveStatus.title = 'Automatically saved in Origin. Use File → Save as… for a file on your computer.';
  saveStatus.setAttribute("role", "status");
  titlebar.append(title, saveStatus, btn("Save document", flush, "folio-title-button", "Save"), btn("Comments", () => showSidebar("comments"), "folio-title-button"));
  const quick = el('div', undefined, 'folio-quick-access');
  quick.setAttribute('role', 'group'); quick.setAttribute('aria-label', 'Quick access');
  for (const [label, symbol, action, method] of [
    ['Quick undo', 'undo', () => state.editor.chain().focus().undo().run(), 'undo'],
    ['Quick redo', 'redo', () => state.editor.chain().focus().redo().run(), 'redo'],
    ['Quick print', 'print', printDocument],
  ]) {
    const button = btn(label, action, 'folio-title-button', '');
    button.append(icon(symbol));
    if (method) button.dataset.command = method;
    quick.append(button);
  }
  titlebar.insertBefore(quick, title);
  app.append(titlebar);
  const tabs = el("div", void 0, "folio-tabs");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Document ribbon");
  for (const name of ["File", "Home", "Insert", "Draw", "Design", "Layout", "References", "Review", "View", "Table", "Picture", "Shape Format"]) {
    const b = btn(name, () => {
      state.tab = name;
      renderRibbon();
    }, "folio-tab");
    b.setAttribute("role", "tab");
    b.id = tabId(name);
    b.setAttribute('aria-controls', 'folioRibbon');
    b.dataset.tab = name;
    b.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const choices = [...tabs.querySelectorAll('[role=tab]')].filter(tab => !tab.hidden);
      const current = choices.indexOf(b);
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? choices.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + choices.length) % choices.length;
      choices[index].focus(); choices[index].click();
    });
    tabs.append(b);
  }
  app.append(tabs);
  const ribbon = el("div", void 0, "folio-ribbon");
  ribbon.id = "folioRibbon";
  ribbon.setAttribute('role', 'tabpanel');
  app.append(ribbon);
  const work = el("div", void 0, "folio-work-area");
  const canvas = el("div", void 0, "folio-canvas");
  canvas.id = "folioCanvas";
  const ruler = el("div", undefined, "folio-ruler");
  ruler.id = 'folioRuler';
  ruler.setAttribute("aria-hidden", "true");
  const paper = el("div", void 0, "folio-paper");
  paper.id = "folioPaper";
  const editorHost = el("div");
  editorHost.id = "folioEditor";
  editorHost.classList.toggle('folio-paragraph-marks', paragraphMarks);
  paper.append(editorHost);
  canvas.append(ruler, paper);
  const sidebar = el("aside", void 0, "folio-sidebar");
  sidebar.id = "folioSidebar";
  sidebar.hidden = true;
  sidebar.addEventListener('keydown', event => {
    if (!event.isComposing && event.key === 'Escape' && ['find', 'format'].includes(state.sidebar)) {
      event.preventDefault(); event.stopPropagation(); showSidebar(''); state.editor.view.focus();
    }
  });
  work.append(canvas, sidebar);
  app.append(work);
  const statusbar = el("div", void 0, "folio-statusbar");
  const stats = btn("Document statistics", wordCountDialog, "folio-count", "0 words");
  stats.id = "folioWordCount";
  statusbar.append(stats, el("span", "Spelling: browser", "folio-muted"));
  const zoom = el("div", void 0, "folio-zoom");
  zoom.append(btn("Zoom out", () => setZoom(state.zoom - 10), "folio-tool", "−"));
  const slider = el("input");
  slider.type = "range";
  slider.min = "25";
  slider.max = "150";
  slider.step = "1";
  slider.value = state.zoom;
  slider.setAttribute("aria-label", "Zoom");
  slider.id = "folioZoom";
  slider.oninput = () => setZoom(Number(slider.value));
  const zoomLabel = el("span", `${state.zoom}%`);
  zoomLabel.id = "folioZoomLabel";
  zoom.append(slider, btn("Zoom in", () => setZoom(state.zoom + 10), "folio-tool", "+"), zoomLabel);
  statusbar.append(zoom);
  app.append(statusbar);
  state.editor = createFolioEditor(editorHost, state.doc.content, { onUpdate: changed, onTransaction: editorTransaction, onImage: (file) => insertImage(file).catch((error) => notice(error.message, true)), onError: message => notice(message, true) });
  const paintSelection = event => {
    if (event.isComposing || !formatBrush) return;
    if (event.type === 'keyup' && event.shiftKey) return;
    if (event.type === 'keyup' && !event.key.startsWith('Arrow') && event.key !== 'Shift') return;
    if (state.editor.state.selection.node) return;
    const view = state.editor.view, native = view.dom.ownerDocument.getSelection();
    if ('$cursor' in state.editor.state.selection) {
      if (!native?.rangeCount || !view.dom.contains(native.anchorNode) || !view.dom.contains(native.focusNode)) return;
      try {
        const from = view.posAtDOM(native.anchorNode, native.anchorOffset), to = view.posAtDOM(native.focusNode, native.focusOffset);
        state.editor.commands.setTextSelection({from, to});
      } catch { return; }
    }
    if (state.editor.state.selection.empty) return;
    if (applyFormatting(state.editor, formatBrush)) { formatBrush = null; updateToolbar(); notice('Formatting applied.'); }
  };
  editorHost.addEventListener('pointerup', event => {
    if (event.button !== 0) return;
    requestAnimationFrame(() => { if (editorHost.isConnected) paintSelection({type: 'pointerup'}); });
  });
  editorHost.addEventListener('keyup', paintSelection);
  editorHost.addEventListener('click', event => {
    const note = event.target.closest('[data-folio-note-id]');
    if (note) { event.preventDefault(); noteDialog(undefined, note.dataset.folioNoteId); return; }
    const link = event.target.closest('a[href^="#folio-anchor-"]');
    if (link) {
      event.preventDefault();
      const anchor = editorHost.querySelector(`[id="${CSS.escape(link.getAttribute('href').slice(1))}"]`);
      anchor?.scrollIntoView({block: 'center'});
      return;
    }
  });
  formatTools = installFormatTools({editor: state.editor, host: editorHost, header: sidebarHeader, open: () => showSidebar('format')});
  renderRibbon();
  applyPage();
  refreshDocumentInfo();
  renderNotes();
}
function renderRibbon() {
  if (!state.editor) return;
  const ribbon = $("folioRibbon");
  ribbon.replaceChildren();
  for (const tab of app.querySelectorAll("[data-tab]")) {
    const active = tab.dataset.tab === state.tab;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  }
  ribbon.setAttribute('aria-labelledby', tabId(state.tab));
  ribbon.dataset.tab = state.tab;
  const editor = () => state.editor;
  if (state.tab === "Home") {
    ribbon.append(group("History", command("Undo", "undo", void 0, "↶"), command("Redo", "redo", void 0, "↷")));
    const painter = btn('Format painter', () => {
      formatBrush = formatBrush ? null : captureFormatting(state.editor);
      notice(formatBrush ? 'Select text in the document to apply this formatting. Escape cancels.' : '');
    }, 'folio-tool');
    painter.id = 'folioFormatPainter';
    ribbon.append(group('Clipboard', painter));
    const font = select("Font family", [["Arial", "Arial"], ["Calibri", "Calibri"], ["Georgia", "Georgia"], ["Times New Roman", "Times New Roman"], ["Verdana", "Verdana"], ["Courier New", "Courier New"]], "Arial", (value) => editor().chain().focus().setFontFamily(value).run());
    font.id = "folioFont";
    const size = el('input'); size.type = 'number'; size.min = '6'; size.max = '96'; size.step = '0.5';
    size.setAttribute('aria-label', 'Font size'); size.title = 'Font size in points'; size.setAttribute('list', 'folioFontSizes');
    const sizes = el('datalist'); sizes.id = 'folioFontSizes';
    for (const value of [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 36, 48, 72, 96]) { const item = el('option'); item.value = value; sizes.append(item); }
    const commitSize = () => { try { applyFontSize(editor(), size.value); notice(''); } catch (error) { notice(error.message, true); } };
    size.onchange = commitSize;
    size.onkeydown = event => {
      if (event.isComposing) return;
      if (event.key === 'Enter') { event.preventDefault(); commitSize(); }
      if (event.key === 'Escape') { event.preventDefault(); size.value = fontSizePoints(editor()); editor().commands.focus(); }
    };
    size.id = "folioFontSize";
    const fontControls = el("div", void 0, "folio-font-controls");
    const fontRow = el("div", void 0, "folio-control-row");
    fontRow.append(font, size, sizes,
      btn('Increase font size', () => applyFontSize(editor(), Math.min(96, fontSizePoints(editor()) + 1)), 'folio-tool'),
      btn('Decrease font size', () => applyFontSize(editor(), Math.max(6, fontSizePoints(editor()) - 1)), 'folio-tool'));
    const marks = el("div", void 0, "folio-control-row");
    marks.append(command("Bold", "toggleBold", void 0, "B"), command("Italic", "toggleItalic", void 0, "I"), command("Underline", "toggleUnderline", void 0, "U"), command("Strikethrough", "toggleStrike", void 0, "S"), command("Subscript", "toggleSubscript", void 0, "x₂"), command("Superscript", "toggleSuperscript", void 0, "x²"));
    const color = el("input");
    color.type = "color";
    color.value = "#17243b";
    color.setAttribute("aria-label", "Text color");
    color.title = "Text color";
    color.oninput = () => editor().chain().focus().setColor(color.value).run();
    const highlight = el("input");
    highlight.type = "color";
    highlight.value = "#fff1a8";
    highlight.setAttribute("aria-label", "Highlight color");
    highlight.title = "Highlight color";
    highlight.oninput = () => editor().chain().focus().setHighlight({ color: highlight.value }).run();
    marks.append(color, highlight, command("Clear formatting", "unsetAllMarks", void 0, "A̸"));
    fontControls.append(fontRow, marks);
    ribbon.append(group("Font", fontControls));
    const paragraph = el("div");
    const align = el("div", void 0, "folio-control-row");
    for (const [name, symbol] of [["left", "≡"], ["center", "☰"], ["right", "≡"], ["justify", "▤"]]) {
      const b = command(`Align ${name}`, "setTextAlign", name, symbol);
      b.dataset.align = name;
      align.append(b);
    }
    const lists = el("div", void 0, "folio-control-row");
    lists.append(command("Bulleted list", "toggleBulletList", void 0, "• ≡"), command("Numbered list", "toggleOrderedList", void 0, "1. ≡"), command("Decrease indent", "changeIndent", -1, "⇤"), command("Increase indent", "changeIndent", 1, "⇥"));
    lists.append(select("Line spacing", [["1", "1.0"], ["1.15", "1.15"], ["1.5", "1.5"], ["2", "2.0"], ["2.5", "2.5"], ["3", "3.0"]], "1.5", (value) => editor().chain().focus().setParagraphSpacing(value).run()));
    const marksButton = btn('Show paragraph marks', () => {
      paragraphMarks = !paragraphMarks; $('folioEditor').classList.toggle('folio-paragraph-marks', paragraphMarks);
    }, 'folio-tool'); marksButton.id = 'folioParagraphMarks'; lists.append(marksButton);
    paragraph.append(align, lists);
    ribbon.append(group("Paragraph", paragraph));
    const styles = el("div", void 0, "folio-style-gallery");
    for (const [level, name] of [[0, "Normal"], [1, "Heading 1"], [2, "Heading 2"], [3, "Heading 3"]]) {
      const b = btn(name, () => {
        const chain = editor().chain().focus();
        level ? chain.setHeading({ level }).run() : chain.setParagraph().run();
      }, `folio-style folio-style-${level}`);
      b.dataset.heading = level;
      styles.append(b);
    }
    ribbon.append(group("Styles", styles), group("Editing", btn("Find and replace", () => showSidebar("find"), "folio-tool", "⌕  Find"), btn("Document outline", () => showSidebar("outline"), "folio-tool", "☷  Outline")));
  } else if (state.tab === "File") {
    ribbon.append(group("Document", btn("New document", () => create(), "folio-tool", "＋ New"), btn("Import document", () => importInput.click(), "folio-tool", "↑ Import"), btn("Save document", flush, "folio-tool", "Save"), btn('Save as…', saveAsDialog, 'folio-tool'), btn("Save a copy", saveCopy, "folio-tool")));
    ribbon.append(group("Download", ...["docx", "html", "txt", "json"].map((format) => btn(`Download ${format === "txt" ? "text" : format.toUpperCase()}`, () => download(format), "folio-tool"))), group("Print & recovery", btn("Print or save PDF", printDocument, "folio-tool"), btn("Download recovery JSON", recovery, "folio-tool")));
  } else if (state.tab === "Insert") {
    ribbon.append(group("Pages", command("Page break", "insertContent", { type: "pageBreak" }, "Page break"), command("Horizontal line", "setHorizontalRule", void 0, "Horizontal line")), group("Content", btn("Insert table", tableDialog, "folio-tool", "▦ Table"), btn("Insert image", imageDialog, "folio-tool", "▧ Image"), btn("Insert link", linkDialog, "folio-tool", "↗ Link"), btn("Insert date", () => editor().chain().focus().insertContent((/* @__PURE__ */ new Date()).toLocaleDateString()).run(), "folio-tool", "Date")), group("Table tools", command("Add row below", "addRowAfter", void 0, "＋ Row"), command("Add column after", "addColumnAfter", void 0, "＋ Column"), command("Delete row", "deleteRow", void 0, "− Row"), command("Delete column", "deleteColumn", void 0, "− Column"), command("Merge cells", "mergeCells", void 0, "Merge"), command("Split cell", "splitCell", void 0, "Split"), command("Delete table", "deleteTable", void 0, "Delete table")));
    const shapes = btn('Shapes', () => formatTools.shapes(shapes), 'folio-tool');
    ribbon.append(group('Illustrations', shapes, btn('Insert drawing', drawingDialog, 'folio-tool')), group('Symbols', btn('Insert symbol', symbolDialog, 'folio-tool')));
  } else if (state.tab === 'Draw') {
    const pens = el('div', undefined, 'folio-pen-gallery');
    for (const [label, initialTool, initialColour, initialWidth] of [
      ['Black pen', 'pen', '#202020', 3], ['Red pen', 'pen', '#d62d36', 3],
      ['Fine pen', 'pen', '#718c98', 1], ['Highlighter', 'highlighter', '#f4db20', 20],
    ]) {
      const pen = btn(label, () => drawingDialog({initialTool, initialColour, initialWidth}), 'folio-pen-preset', '');
      pen.style.setProperty('--pen-colour', initialColour);
      pen.append(drawingIcon(initialTool), el('span', label)); pens.append(pen);
    }
    ribbon.append(group('Drawing', btn('Insert drawing', drawingDialog, 'folio-tool')), group('Pens', pens), group('Ink tools', el('p', 'Select a pen to open the drawing canvas. Add your finished sketch to the page.', 'folio-ribbon-hint')));
  } else if (state.tab === 'Design') {
    const gallery = el('div', undefined, 'folio-design-gallery');
    for (const style of STYLE_SETS) {
      const button = btn(`Apply ${style.name} style set`, () => {
        applyStyleSet(state.editor, style);
        notice(`${style.name} formatting applied to the document. Undo restores the previous formatting.`);
      }, 'folio-design-style', '');
      button.style.setProperty('--sample-font', style.font);
      button.style.setProperty('--sample-color', style.color);
      const sample = el('span', undefined, 'folio-design-sample');
      sample.setAttribute('aria-hidden', 'true');
      sample.append(el('strong', 'Title'), el('b', 'Heading 1'), el('span', 'Your document, beautifully arranged.'), el('i', 'A thoughtful detail.'));
      button.append(sample, el('span', style.name, 'folio-design-name'));
      gallery.append(button);
    }
    ribbon.append(group('Document formatting', gallery), group('Paragraph', btn('Paragraph spacing', paragraphDialog, 'folio-tool')));
  } else if (state.tab === 'References') {
    ribbon.append(group('Table of contents', btn('Table of contents', () => { updateContents(state.editor); notice('Linked contents added at the start. Use Update table after changing headings.'); }, 'folio-tool'), btn('Update table of contents', () => { updateContents(state.editor, true); notice('Contents updated from the current headings.'); }, 'folio-tool')),
      group('Footnotes', btn('Insert footnote', () => noteDialog('footnote'), 'folio-tool'), btn('Insert endnote', () => noteDialog('endnote'), 'folio-tool'), btn('Next note', nextNote, 'folio-tool')),
      group('References', btn('Insert cross-reference', crossReferenceDialog, 'folio-tool'), btn('Insert caption', () => captionDialog(objectAtSelection('image') ? 'image' : 'table'), 'folio-tool')));
  } else if (state.tab === 'Shape Format') {
    ribbon.append(group('Shape', btn('Format Shape', () => formatTools.openPane(), 'folio-tool'), btn('Remove shape', () => state.editor.chain().focus().deleteSelection().run(), 'folio-tool')));
  } else if (state.tab === 'Picture') {
    ribbon.append(group('Picture', btn('Format Picture', () => formatTools.openPane(), 'folio-tool'), btn('Picture size and description', pictureDialog, 'folio-tool', 'Size & description'), btn('Insert picture caption', () => captionDialog('image'), 'folio-tool', 'Caption'), btn('Remove picture', () => state.editor.chain().focus().deleteSelection().run(), 'folio-tool')),
      group('Display', btn('Fit picture to page', () => fitPicture(1), 'folio-tool'), btn('Half-page picture', () => fitPicture(0.5), 'folio-tool'),
        btn('Reset picture size', () => state.editor.chain().focus().updateAttributes('image', {width: null, height: null}).run(), 'folio-tool', 'Original size'),
        el('p', 'Pictures stay in the text flow. Double-click a picture to edit its size and description.', 'folio-ribbon-hint')));
  } else if (state.tab === 'Table') {
    const fill = el('input'); fill.type = 'color'; fill.value = '#e8eff9'; fill.setAttribute('aria-label', 'Cell fill color'); fill.title = 'Cell fill color';
    fill.oninput = () => state.editor.chain().focus().setCellAttribute('backgroundColor', fill.value).run();
    ribbon.append(
      group('Rows', command('Add row above', 'addRowBefore', undefined, 'Insert above'), command('Add row below', 'addRowAfter', undefined, 'Insert below'), command('Delete row', 'deleteRow')),
      group('Columns', command('Add column before', 'addColumnBefore', undefined, 'Insert left'), command('Add column after', 'addColumnAfter', undefined, 'Insert right'), command('Delete column', 'deleteColumn')),
      group('Cells', command('Merge cells', 'mergeCells'), command('Split cell', 'splitCell'), btn('Select table', () => selectTableCells('table'), 'folio-tool'), btn('Select row', () => selectTableCells('row'), 'folio-tool'), btn('Select column', () => selectTableCells('column'), 'folio-tool')),
      group('Table style', command('Toggle header row', 'toggleHeaderRow', undefined, 'Header row'), command('Toggle header column', 'toggleHeaderColumn', undefined, 'Header column'), field('Cell shading', fill), btn('Clear cell shading', () => state.editor.chain().focus().setCellAttribute('backgroundColor', null).run(), 'folio-tool')),
      group('Table', btn('Insert table caption', () => captionDialog('table'), 'folio-tool', 'Caption'), command('Delete table', 'deleteTable')));
  } else if (state.tab === "Layout") {
    ribbon.append(group("Page setup", field("Paper size", select("Paper size", [["a4", "A4"], ["letter", "US Letter"]], state.doc.page.size, (value) => pageSetting("size", value))), field("Orientation", select("Orientation", [["portrait", "Portrait"], ["landscape", "Landscape"]], state.doc.page.orientation, (value) => pageSetting("orientation", value))), field("Margins", select("Margins", [["normal", "Normal · 1 inch"], ["narrow", "Narrow · ½ inch"], ["wide", "Wide · 1½ inch"]], state.doc.page.margin, (value) => pageSetting("margin", value)))), group("Page flow", command("Page break", "insertContent", { type: "pageBreak" }, "Insert page break"), el("p", "A continuous writing canvas. Printed pages follow your paper settings.", "folio-ribbon-hint")));
  } else if (state.tab === "Review") {
    ribbon.append(group("Review", btn("Add comment", () => showSidebar("comments", true), "folio-tool", "＋ Comment"), btn("Comments", () => showSidebar("comments"), "folio-tool"), btn("Find and replace", () => showSidebar("find"), "folio-tool")), group("Proofing", btn("Word count", wordCountDialog, "folio-tool"), btn("Toggle spell check", () => {
      const dom = editor().view.dom;
      dom.spellcheck = !dom.spellcheck;
      notice(`Browser spell check ${dom.spellcheck ? "enabled" : "disabled"}.`);
    }, "folio-tool"), el("p", "Comments stay with the document and include selected text.", "folio-ribbon-hint")));
  } else {
    ribbon.append(group("Show", btn("Document outline", () => showSidebar("outline"), "folio-tool"), btn("Comments", () => showSidebar("comments"), "folio-tool"), btn("Close sidebar", () => showSidebar(""), "folio-tool")), group("Zoom", btn("100% zoom", () => setZoom(100), "folio-tool"), btn("Fit page width", fitPageWidth, "folio-tool")), group("Focus", btn("Focus mode", () => { app.classList.toggle("folio-focus"); notice('Focus mode. Press Escape to return to the ribbon.'); }, "folio-tool"), btn("Print or save PDF", printDocument, "folio-tool")));
  }
  if (state.tab === 'Layout') ribbon.append(group('Paragraph', btn('Paragraph spacing', paragraphDialog, 'folio-tool'), command('Decrease indent', 'changeIndent', -1), command('Increase indent', 'changeIndent', 1)));
  decorateRibbon(ribbon);
  updateToolbar();
}
function updateToolbar() {
  if (!state.editor || state.editor.isDestroyed) return;
  if (state.sidebar === 'format') formatTools?.sync();
  const contextual = {Picture: !!objectAtSelection('image'), Table: !!objectAtSelection('table'), 'Shape Format': !!objectAtSelection('shape')};
  for (const [name, active] of Object.entries(contextual)) if ($(tabId(name))) $(tabId(name)).hidden = !active;
  if (state.tab in contextual && !contextual[state.tab]) { state.tab = 'Home'; renderRibbon(); return; }
  const marks = { toggleBold: "bold", toggleItalic: "italic", toggleUnderline: "underline", toggleStrike: "strike", toggleSubscript: "subscript", toggleSuperscript: "superscript", toggleBulletList: "bulletList", toggleOrderedList: "orderedList" };
  for (const b of app.querySelectorAll("[data-command]")) {
    const mark = marks[b.dataset.command];
    if (mark) b.setAttribute("aria-pressed", String(state.editor.isActive(mark)));
    if (b.dataset.align) b.setAttribute("aria-pressed", String(state.editor.isActive({ textAlign: b.dataset.align })));
    if (['undo', 'redo', 'addRowBefore', 'addRowAfter', 'addColumnBefore', 'addColumnAfter', 'deleteRow', 'deleteColumn', 'mergeCells', 'splitCell', 'deleteTable', 'toggleHeaderRow', 'toggleHeaderColumn'].includes(b.dataset.command)) {
      b.disabled = !state.editor.can()[b.dataset.command]();
    }
  }
  for (const b of app.querySelectorAll('[data-heading]')) {
    const level = Number(b.dataset.heading);
    b.setAttribute('aria-pressed', String(level ? state.editor.isActive('heading', {level}) : state.editor.isActive('paragraph')));
  }
  const textStyle = state.editor.getAttributes("textStyle");
  if ($("folioFont")) $("folioFont").value = textStyle.fontFamily || "Arial";
  if ($("folioFontSize") && document.activeElement !== $('folioFontSize')) $("folioFontSize").value = fontSizePoints(state.editor);
  if ($('folioFormatPainter')) $('folioFormatPainter').setAttribute('aria-pressed', String(!!formatBrush));
  $('folioEditor')?.classList.toggle('folio-format-brush', !!formatBrush);
  if ($('folioParagraphMarks')) $('folioParagraphMarks').setAttribute('aria-pressed', String(paragraphMarks));
}
function pageSetting(key, value) {
  state.doc.page[key] = value;
  applyPage();
  changed();
}
function applyPage() {
  const paper = $("folioPaper");
  if (!paper) return;
  const page = state.doc.page;
  let width = page.size === "letter" ? 816 : 794, height = page.size === "letter" ? 1056 : 1123;
  if (page.orientation === "landscape") [width, height] = [height, width];
  paper.style.setProperty("--folio-page-width", `${width}px`);
  paper.style.setProperty("--folio-page-height", `${height}px`);
  paper.style.setProperty("--folio-page-margin", `${{ normal: 96, narrow: 48, wide: 144 }[page.margin] || 96}px`);
  const ruler = $('folioRuler');
  ruler.replaceChildren();
  Object.assign(ruler.style, {width: `${width}px`, maxWidth: 'none', position: 'relative', wordSpacing: 'normal'});
  for (let inch = 1; inch * 96 < width; inch++) {
    const tick = el('span', String(inch));
    Object.assign(tick.style, {position: 'absolute', left: `${inch * 96}px`, bottom: '5px', transform: 'translateX(-50%)'});
    ruler.append(tick);
  }
  setZoom(state.zoom);
}
function setZoom(value) {
  state.zoom = Math.max(25, Math.min(150, Math.round(value)));
  if ($("folioPaper")) $("folioPaper").style.zoom = state.zoom / 100;
  if ($('folioRuler')) $('folioRuler').style.zoom = state.zoom / 100;
  if ($("folioZoom")) $("folioZoom").value = state.zoom;
  if ($("folioZoomLabel")) $("folioZoomLabel").textContent = `${state.zoom}%`;
}
function fitPageWidth() {
  const canvas = $('folioCanvas');
  const style = getComputedStyle(canvas);
  const width = parseFloat($('folioPaper').style.getPropertyValue('--folio-page-width'));
  const available = canvas.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  setZoom(Math.floor(available / width * 100));
}
function plainText(doc, from = 0, to = doc.content.size) {
  return doc.textBetween(from, to, '\n', node => node.type.name === 'hardBreak' ? '\n' : '');
}
function wordCount(text) {
  let words = 0;
  const tokens = /\S+/gu;
  while (tokens.test(text)) words++;
  return words;
}
function selectionText({doc, selection}) {
  if (selection.empty) return '';
  return selection.ranges.map(({$from, $to}) => plainText(doc, $from.pos, $to.pos)).join('\n');
}
function scheduleWordCount() {
  clearTimeout(countTimer);
  countTimer = setTimeout(() => {
    const label = $('folioWordCount');
    if (!label || !state.editor) return;
    const {doc, selection} = state.editor.state;
    if (!wordTotals.has(doc)) wordTotals.set(doc, wordCount(plainText(doc)));
    const total = wordTotals.get(doc);
    const selected = selection.empty ? '' : wordCount(selectionText(state.editor.state)).toLocaleString() + ' of ';
    const count = selected + total.toLocaleString() + (total === 1 ? ' word' : ' words');
    if (label.textContent !== count) label.textContent = count;
    const description = count + ' · Document statistics';
    if (label.getAttribute('aria-label') !== description) label.setAttribute('aria-label', description);
  }, 80);
}
function refreshDocumentInfo() {
  scheduleWordCount();
  clearTimeout(state.viewTimer);
  state.viewTimer = setTimeout(() => {
    if (!state.editor) return;
    if (state.sidebar === "outline") renderOutline();
    renderNotes();
  }, 180);
}
function sidebarHeader(title) {
  const pane = $("folioSidebar");
  pane.replaceChildren();
  const head = el("div", void 0, "folio-sidebar-head");
  head.append(el("h2", title), btn("Close sidebar", () => showSidebar(""), "folio-tool", "×"));
  pane.append(head);
  return pane;
}
function showSidebar(kind, add = false) {
  const existing = kind === 'find' && state.sidebar === 'find' && $('folioSidebar').querySelector('input[type=search]');
  if (existing) { existing.focus(); existing.select(); return; }
  findRefresh = null;
  if (kind !== 'find') state.editor.commands.setFolioSearch('');
  state.sidebar = kind;
  const pane = $("folioSidebar");
  pane.hidden = !kind;
  if (!kind) return;
  if (kind === "outline") renderOutline();
  if (kind === "comments") renderComments(add);
  if (kind === "find") renderFind();
  if (kind === 'format') formatTools.sync(true);
}
function renderOutline() {
  const pane = sidebarHeader("Document outline");
  let count = 0;
  state.editor.state.doc.descendants((node, pos) => {
    if (node.type.name === "heading") {
      const b = btn(node.textContent || "Untitled heading", () => state.editor.chain().focus().setTextSelection(pos + 1).scrollIntoView().run(), "folio-outline-item");
      b.style.paddingLeft = `${12 + (node.attrs.level - 1) * 12}px`;
      pane.append(b);
      count++;
    }
  });
  if (!count) pane.append(el("p", "Use heading styles to give your document an outline.", "folio-muted"));
}
function renderComments(add = false) {
  const pane = sidebarHeader("Comments");
  const selection = state.editor.state.selection;
  const quote = state.editor.state.doc.textBetween(selection.from, selection.to, " ").slice(0, 1e3);
  const form = el("form", void 0, "folio-comment-form");
  if (quote) form.append(el("blockquote", quote));
  const input = el("textarea");
  input.placeholder = "Add a comment…";
  input.setAttribute("aria-label", "Comment text");
  input.maxLength = 4000;
  const submit = el("button", "Add comment", "folio-button folio-primary");
  submit.type = "submit";
  form.append(input, submit);
  form.onsubmit = (event) => {
    event.preventDefault();
    if (!input.value.trim()) return;
    if (state.doc.comments.length >= 500) { notice("A document supports up to 500 comments.", true); return; }
    const id = crypto.randomUUID?.() || Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
    state.doc.comments.push({ id, text: input.value.trim(), quote, created_at: (/* @__PURE__ */ new Date()).toISOString(), resolved: false });
    changed();
    renderComments();
  };
  pane.append(form);
  for (const comment of state.doc.comments || []) {
    const card = el("article", void 0, `folio-comment${comment.resolved ? " resolved" : ""}`);
    card.append(el("span", comment.resolved ? "Resolved" : "Note", "folio-comment-label"));
    if (comment.quote) card.append(el("blockquote", comment.quote));
    card.append(el("p", comment.text), btn(comment.resolved ? "Reopen comment" : "Resolve comment", () => {
      comment.resolved = !comment.resolved;
      changed();
      renderComments();
    }, "folio-link-button"), btn("Delete comment", () => {
      state.doc.comments = state.doc.comments.filter((c) => c.id !== comment.id);
      changed();
      renderComments();
    }, "folio-link-button"));
    pane.append(card);
  }
  if (!(state.doc.comments || []).length) pane.append(el("p", "Select text to include a quote with your comment. Comments are document notes, not live text anchors.", "folio-muted"));
  if (add) input.focus();
}
function renderFind() {
  const pane = sidebarHeader("Find and replace");
  const find = el("input");
  find.type = "search";
  find.setAttribute("aria-label", "Find text");
  find.placeholder = "Find in document";
  const {selection} = state.editor.state;
  if (!selection.empty && selection.$from.sameParent(selection.$to) && selection.to - selection.from <= 256
      && selection.$from.parent.inlineContent) {
    const term = state.editor.state.doc.textBetween(selection.from, selection.to, '\n', '\ufffc');
    if (term.trim() && !/[\n\ufffc]/.test(term)) find.value = term;
  }
  const replace = el("input");
  replace.setAttribute("aria-label", "Replace with");
  replace.placeholder = "Replace with";
  const result = el("p", "Enter text to find.", "folio-muted");
  result.setAttribute('role', 'status');
  const options = el('div', undefined, 'folio-find-options');
  const matchCase = el('input'), wholeWord = el('input');
  for (const [input, label] of [[matchCase, 'Match case'], [wholeWord, 'Whole words']]) {
    input.type = 'checkbox'; input.setAttribute('aria-label', label);
    const item = el('label'); item.append(input, document.createTextNode(label)); options.append(item);
  }
  const settings = () => ({matchCase: matchCase.checked, wholeWord: wholeWord.checked});
  let index = -1;
  const refresh = (reset = true) => {
    if (reset) index = -1;
    state.editor.commands.setFolioSearch(find.value, settings(), index);
    const all = folioSearchState(state.editor).matches;
    result.textContent = !find.value ? 'Enter text to find.' : `${all.length} matches`;
    return all;
  };
  const next = (direction = 1) => {
    const all = refresh(false);
    index = index < 0 && direction < 0 ? all.length - 1 : (index + direction + all.length) % Math.max(1, all.length);
    result.textContent = !find.value ? 'Enter text to find.' : all.length ? `${index + 1} of ${all.length} matches` : "No matches";
    if (all[index]) {
      const chain = state.editor.chain();
      if (![find, replace].includes(document.activeElement)) chain.focus();
      chain.setFolioSearch(find.value, settings(), index).setTextSelection(all[index]).scrollIntoView().run();
    }
  };
  find.oninput = () => refresh();
  matchCase.onchange = wholeWord.onchange = () => refresh();
  findRefresh = () => {
    index = -1;
    result.textContent = find.value ? `${folioSearchState(state.editor).matches.length} matches` : 'Enter text to find.';
  };
  find.onkeydown = (event) => {
    if (event.isComposing) return;
    if (event.key === "Enter") {
      event.preventDefault();
      next(event.shiftKey ? -1 : 1);
    }
  };
  const navigation = el('div', undefined, 'folio-find-actions');
  navigation.append(btn('Find previous', () => next(-1), 'folio-button'), btn('Find next', next, 'folio-button'));
  const replacements = el('div', undefined, 'folio-find-actions');
  replacements.append(btn('Replace current', () => {
    const all = refresh(false), current = all[index < 0 ? 0 : index];
    if (!current) { result.textContent = 'No matches'; return; }
    state.editor.view.dispatch(closeHistory(state.editor.state.tr.insertText(replace.value, current.from, current.to)));
    state.editor.view.dispatch(closeHistory(state.editor.state.tr));
    const remaining = folioSearchState(state.editor).matches;
    const following = remaining.findIndex(match => match.from >= current.from + replace.value.length);
    index = following < 0 ? -1 : following - 1;
    if (remaining.length) next(); else result.textContent = 'Replaced 1 match. No matches remaining.';
  }, 'folio-button'), btn("Replace all", () => {
    const all = refresh(false);
    if (!all.length) {
      result.textContent = "No matches";
      return;
    }
    const tr = state.editor.state.tr;
    for (const match of [...all].reverse()) tr.insertText(replace.value, match.from, match.to);
    state.editor.view.dispatch(closeHistory(tr));
    state.editor.view.dispatch(closeHistory(state.editor.state.tr));
    result.textContent = `Replaced ${all.length} matches`;
  }, "folio-button"));
  pane.append(field("Find", find), options, navigation, field("Replace", replace), replacements, result);
  if (find.value) {
    const all = refresh();
    index = all.findIndex(match => match.from === selection.from && match.to === selection.to);
    if (index !== -1) {
      state.editor.commands.setFolioSearch(find.value, settings(), index);
      result.textContent = (index + 1) + ' of ' + all.length + ' matches';
    }
  }
  find.focus();
  find.select();
}
function dialog(title, fields, submitLabel, action, {canCancel = () => true} = {}) {
  const selection = state.editor ? rememberSelection() : null;
  const controller = new AbortController();
  let committed = false;
  const d = el("dialog", void 0, "folio-dialog");
  addDialogClose(d, 'Close ' + title, {onClose: () => {
    if (!canCancel()) return;
    controller.abort();
    d.close();
  }});
  const form = el("form");
  form.method = "dialog";
  const heading = el('h2', title);
  heading.id = `folioDialogTitle${Date.now()}`;
  d.setAttribute('aria-labelledby', heading.id);
  form.append(heading);
  for (const f of fields) form.append(f);
  const error = el("p", void 0, "folio-dialog-error");
  error.setAttribute("role", "alert");
  const buttons = el("div", void 0, "folio-dialog-actions");
  buttons.append(btn("Cancel", () => {
    if (!canCancel()) return;
    controller.abort();
    d.close();
  }, "folio-button"));
  const submit = el("button", submitLabel, "folio-button folio-primary");
  submit.type = "submit";
  buttons.append(submit);
  form.append(error, buttons);
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    submit.disabled = true;
    error.textContent = '';
    try {
      if (selection) restoreSelection(selection);
      const result = await action(controller.signal);
      if (controller.signal.aborted || result === false) return;
      committed = true;
      d.close();
    } catch (problem) {
      error.textContent = problem.message;
    } finally {
      submit.disabled = false;
    }
  };
  d.append(form);
  document.body.append(d);
  d.addEventListener('cancel', event => {
    if (!canCancel()) { event.preventDefault(); return; }
    controller.abort();
  });
  d.addEventListener("close", () => {
    controller.abort();
    if (selection) {
      bookmarks.delete(selection);
      if (!committed && state.editor === selection.editor && !state.editor.isDestroyed) restoreSelection(selection);
    }
    d.remove();
    if (committed && state.editor === selection?.editor && !state.editor.isDestroyed) state.editor.commands.focus(undefined, {scrollIntoView: false});
  });
  d.showModal();
  return d;
}
function selectTableCells(scope) {
  const table = objectAtSelection('table');
  if (!table) throw new Error('Place the cursor in a table first.');
  const grid = [], cells = [];
  table.node.forEach((row, rowOffset, rowIndex) => {
    grid[rowIndex] ||= [];
    let column = 0;
    row.forEach((node, cellOffset) => {
      while (grid[rowIndex][column]) column++;
      const cell = {pos: table.pos + 2 + rowOffset + cellOffset, row: rowIndex, column};
      cells.push(cell);
      for (let down = 0; down < node.attrs.rowspan; down++) {
        grid[rowIndex + down] ||= [];
        for (let across = 0; across < node.attrs.colspan; across++) grid[rowIndex + down][column + across] = cell;
      }
      column += node.attrs.colspan;
    });
  });
  const selected = objectAtSelection('tableCell') || objectAtSelection('tableHeader');
  const anchor = state.editor.state.selection.$anchorCell?.pos ?? selected?.pos;
  const current = cells.find(cell => cell.pos === anchor) || cells[0];
  let first = grid[0][0], last = grid.at(-1).at(-1);
  if (scope === 'row') { first = grid[current.row][0]; last = grid[current.row].at(-1); }
  if (scope === 'column') { first = grid[0][current.column]; last = grid.at(-1)[current.column]; }
  state.editor.chain().focus().setCellSelection({anchorCell: first.pos, headCell: last.pos}).run();
}
function drawingDialog(preset = {}) {
  const selection = rememberSelection();
  try {
    openDrawing({
      ...preset,
      onInsert: image => {
        restoreSelection(selection);
        if (!state.editor.chain().focus().setImage(image).run()) throw new Error('Place the cursor in the document and try again.');
      },
      onClose: () => bookmarks.delete(selection),
    });
  } catch (error) { bookmarks.delete(selection); throw error; }
}
function renderNotes() {
  if (!state.editor || state.editor.isDestroyed || !$('folioPaper')) return;
  const notes = collectFolioNotes(state.editor.state.doc);
  let section = $('folioNotes');
  if (!notes.length) { section?.remove(); return; }
  if (!section) { section = el('section'); section.id = 'folioNotes'; $('folioPaper').append(section); }
  const previous = section.folioNotes || [];
  if (previous.length === notes.length && notes.every((note, index) => ['id', 'text', 'kind', 'number'].every(key => note[key] === previous[index][key]))) return;
  section.folioNotes = notes;
  section.replaceChildren();
  section.setAttribute('aria-label', 'Document notes');
  for (const [kind, label] of [['footnote', 'Footnotes'], ['endnote', 'Endnotes']]) {
    const selected = notes.filter(note => note.kind === kind);
    if (!selected.length) continue;
    section.append(el('h2', label));
    const list = el('ol');
    for (const note of selected) {
      const item = el('li'); item.id = `folio-note-${note.id}`;
      item.value = note.number;
      item.append(btn(`Edit ${kind} ${note.number}`, () => noteDialog(kind, note.id), 'folio-note-text', note.text));
      list.append(item);
    }
    section.append(list);
  }
}
function noteDialog(kind = 'footnote', id) {
  const existing = id ? collectFolioNotes(state.editor.state.doc).find(note => note.id === id) : null;
  if (id && !existing) throw new Error('That note is no longer in the document.');
  const input = el('textarea', undefined, 'folio-note-input');
  input.setAttribute('aria-label', 'Note text'); input.maxLength = 4000; input.required = true; input.value = existing?.text || '';
  const type = select('Note type', [['footnote', 'Footnote'], ['endnote', 'Endnote']], existing?.kind || kind, () => {});
  const fields = [field('Type', type), field('Text', input), el('p', 'Notes are numbered automatically. Word exports place footnotes at the page bottom. Here and in browser printing, notes appear after the document.', 'folio-muted')];
  let d;
  if (existing) fields.push(btn('Remove note', () => {
    if (!state.editor.commands.removeFolioNote(id)) throw new Error('That note is no longer available.');
    d.close(); renderNotes();
  }, 'folio-button'));
  d = dialog(existing ? 'Edit note' : `Insert ${kind}`, fields, existing ? 'Save note' : 'Insert note', () => {
    const text = input.value.trim();
    if (!text) throw new Error('Enter some note text.');
    if (!existing && state.editor.state.selection.$to.parent.type.name === 'codeBlock') throw new Error('Place the cursor in a regular paragraph or heading, outside the code block.');
    const ok = existing ? state.editor.commands.updateFolioNote(id, {text, kind: type.value}) : state.editor.commands.insertFolioNote({text, kind: type.value});
    if (!ok) throw new Error('The note could not be saved. A document supports up to 500 notes.');
    renderNotes();
  });
  input.focus();
}
function nextNote() {
  const notes = collectFolioNotes(state.editor.state.doc);
  if (!notes.length) throw new Error('This document has no notes yet.');
  const current = state.editor.state.selection.from;
  const note = notes.find(item => item.pos > current) || notes[0];
  state.editor.chain().focus().setNodeSelection(note.pos).scrollIntoView().run();
}
function paragraphDialog() {
  const current = state.editor.isActive('heading') ? state.editor.getAttributes('heading') : state.editor.getAttributes('paragraph');
  const inputs = {};
  const grid = el('div', undefined, 'folio-paragraph-grid');
  for (const [name, label, min, max, step, initial] of [
    ['spaceBefore', 'Before (pt)', 0, 144, .01, current.spaceBefore ?? 0],
    ['spaceAfter', 'After (pt)', 0, 144, .01, current.spaceAfter ?? 8],
    ['lineHeight', 'Line spacing', 1, 3, .01, current.lineHeight ?? 1.5],
    ['indent', 'Indent (½-inch steps)', 0, 8, 1, current.indent ?? 0],
  ]) {
    const input = el('input'); input.type = 'number'; input.min = min; input.max = max; input.step = step; input.value = initial; input.required = true; input.setAttribute('aria-label', label);
    inputs[name] = input; grid.append(field(label, input));
  }
  dialog('Paragraph spacing', [grid, el('p', 'Applies to the current paragraph or every selected paragraph.', 'folio-muted')], 'Apply spacing', () => {
    const attrs = {};
    for (const [name, input] of Object.entries(inputs)) {
      const value = Number(input.value);
      if (!input.value || !Number.isFinite(value) || value < Number(input.min) || value > Number(input.max) || (name === 'indent' && !Number.isInteger(value))) throw new Error('Enter values within the displayed limits.');
      attrs[name] = value;
    }
    const {from, to} = state.editor.state.selection;
    const tr = state.editor.state.tr;
    tr.doc.nodesBetween(from, to, (node, pos) => {
      if (['paragraph', 'heading'].includes(node.type.name)) tr.setNodeMarkup(pos, undefined, {...node.attrs, ...attrs});
    });
    if (!tr.docChanged) throw new Error('Place the cursor in a paragraph first.');
    state.editor.view.dispatch(tr);
  });
}
function crossReferenceDialog() {
  const headings = documentHeadings(state.editor.state.doc);
  if (!headings.length) throw new Error('Add a heading before inserting a cross-reference.');
  const target = select('Reference heading', headings.map(heading => [String(heading.pos), heading.text]), String(headings[0].pos), () => {});
  dialog('Insert cross-reference', [field('Heading', target), el('p', 'Inserts linked heading text. Reinsert a reference if its heading is renamed. Page numbers are not guessed.', 'folio-muted')], 'Insert reference', () => insertCrossReference(state.editor, Number(target.value)));
}
function symbolDialog() {
  const input = el('input'); input.setAttribute('aria-label', 'Symbol text'); input.maxLength = 100; input.required = true;
  const grid = el('div', undefined, 'folio-symbol-grid');
  for (const symbol of ['α', 'β', 'γ', 'δ', 'ε', 'θ', 'λ', 'μ', 'π', 'σ', 'φ', 'ω', 'Δ', 'Σ', 'Ω', '±', '×', '÷', '≤', '≥', '≠', '≈', '∞', '∑', '∫', '√', '°', '©', '®', '™', '→', '↔']) {
    grid.append(btn(`Symbol ${symbol}`, () => { if (input.value.length < 100) input.value += symbol; input.focus(); }, '', symbol));
  }
  dialog('Insert symbol', [grid, field('Symbols or special characters', input)], 'Insert symbols', () => {
    if (!input.value.trim()) throw new Error('Choose a symbol or enter text.');
    state.editor.chain().focus().insertContent({type: 'text', text: input.value}).run();
  });
}
function fitPicture(fraction) {
  const current = objectAtSelection('image');
  if (!current) throw new Error('Select a picture first.');
  const image = state.editor.view.nodeDOM(current.pos);
  const explicitSize = current.node.attrs.width && current.node.attrs.height;
  const originalWidth = explicitSize ? current.node.attrs.width : image?.naturalWidth;
  const originalHeight = explicitSize ? current.node.attrs.height : image?.naturalHeight;
  if (!originalWidth || !originalHeight) throw new Error('Wait for the picture to load, then try again.');
  const scale = Math.min(state.editor.view.dom.clientWidth * fraction / originalWidth, 2400 / originalWidth, 2400 / originalHeight);
  const attrs = {...current.node.attrs, width: Math.max(1, Math.round(originalWidth * scale)), height: Math.max(1, Math.round(originalHeight * scale))};
  state.editor.view.dispatch(closeHistory(state.editor.state.tr.setNodeMarkup(current.pos, undefined, attrs)));
  state.editor.view.dispatch(closeHistory(state.editor.state.tr));
}
function pictureDialog() {
  const image = objectAtSelection('image');
  if (!image) throw new Error('Select a picture to change its size or description.');
  const dom = state.editor.view.nodeDOM(image.pos);
  const attrs = image.node.attrs;
  const ratio = attrs.width && attrs.height ? attrs.width / attrs.height : (dom.naturalWidth || 1) / (dom.naturalHeight || 1);
  const width = el('input'), height = el('input');
  for (const [input, label] of [[width, 'Picture width'], [height, 'Picture height']]) {
    input.type = 'number'; input.min = '1'; input.max = '2400'; input.step = '1'; input.required = true; input.setAttribute('aria-label', label);
  }
  width.value = Math.min(2400, Math.max(1, Math.round(attrs.width || dom.width || 400)));
  height.value = Math.min(2400, Math.max(1, Math.round(attrs.height || Number(width.value) / ratio)));
  const lock = el('input'); lock.type = 'checkbox'; lock.checked = true; lock.setAttribute('aria-label', 'Lock aspect ratio');
  width.addEventListener('input', () => { if (lock.checked && Number(width.value) > 0) height.value = Math.max(1, Math.round(Number(width.value) / ratio)); });
  height.addEventListener('input', () => { if (lock.checked && Number(height.value) > 0) width.value = Math.max(1, Math.round(Number(height.value) * ratio)); });
  const alt = el('textarea'); alt.value = attrs.alt || ''; alt.maxLength = 1000; alt.setAttribute('aria-label', 'Picture description');
  const dimensions = el('div', undefined, 'folio-inline-fields'); dimensions.append(field('Width (pixels)', width), field('Height (pixels)', height));
  const locked = el('label', undefined, 'folio-checkbox-field'); locked.append(lock, el('span', 'Lock aspect ratio'));
  dialog('Picture size and description', [dimensions, locked, field('Description for accessibility', alt), el('p', 'Enter dimensions from 1 to 2,400 pixels. Pictures wider than the page scale down to fit. This does not crop the image.', 'folio-muted')], 'Apply picture changes', () => {
    if (!objectAtSelection('image')) throw new Error('That picture is no longer selected. Close this dialog and select it again.');
    const w = Number(width.value), h = Number(height.value);
    if (![w, h].every(value => Number.isInteger(value) && value >= 1 && value <= 2400)) throw new Error('Picture dimensions must be whole numbers from 1 to 2,400.');
    state.editor.chain().focus().updateAttributes('image', {width: w, height: h, alt: alt.value.trim()}).run();
  });
}
function captionDialog(type) {
  const object = objectAtSelection(type);
  if (!object) throw new Error(`Select a ${type === 'image' ? 'picture' : 'table'} first.`);
  const caption = el('input'); caption.setAttribute('aria-label', 'Caption text'); caption.maxLength = 2000;
  caption.value = type === 'image' ? 'Figure 1. ' : 'Table 1. ';
  dialog('Insert caption', [field('Caption', caption), el('p', 'The caption is an editable paragraph after the selected object. Numbering is manual.', 'folio-muted')], 'Insert caption', () => {
    const current = objectAtSelection(type);
    if (!current) throw new Error('The selected object is no longer available.');
    const text = caption.value.trim(); if (!text) throw new Error('Enter a caption.');
    state.editor.chain().focus().insertContentAt(current.pos + current.node.nodeSize, {type: 'paragraph', content: [{type: 'text', text, marks: [{type: 'italic'}]}]}).run();
  });
}
function linkDialog() {
  const href = el("input");
  href.type = "text";
  href.inputMode = "url";
  href.value = state.editor.getAttributes("link").href || "";
  href.placeholder = "https://example.com";
  href.setAttribute("aria-label", "Link URL");
  dialog("Insert a link", [field("Address", href), el("p", "Use an https://, http://, mailto:, tel: or # link. Leave empty to remove a link.", "folio-muted")], "Apply link", () => {
    const value = href.value.trim();
    if (value && !safeLink(value)) throw new Error("Please enter a safe, complete link.");
    if (value && state.editor.state.selection.empty && !state.editor.isActive('link')) {
      state.editor.chain().focus().insertContent({type: 'text', text: value, marks: [{type: 'link', attrs: {href: value}}]}).run();
      return;
    }
    const chain = state.editor.chain().focus().extendMarkRange("link");
    value ? chain.setLink({ href: value }).run() : chain.unsetLink().run();
  });
  href.focus();
}
function tableDialog() {
  const rows = el("input");
  rows.type = "number";
  rows.min = "1";
  rows.max = "20";
  rows.value = "3";
  rows.setAttribute("aria-label", "Table rows");
  const columns = el("input");
  columns.type = "number";
  columns.min = "1";
  columns.max = "10";
  columns.value = "3";
  columns.setAttribute("aria-label", "Table columns");
  const header = el('input'); header.type = 'checkbox'; header.checked = true; header.setAttribute('aria-label', 'Header row');
  const headerLabel = el('label', undefined, 'folio-check'); headerLabel.append(header, document.createTextNode('Header row'));
  const grid = el('div', undefined, 'folio-table-picker'); grid.setAttribute('role', 'grid'); grid.setAttribute('aria-label', 'Table size');
  grid.setAttribute('aria-rowcount', '8'); grid.setAttribute('aria-colcount', '10');
  const preview = el('p', '3 × 3 table', 'folio-table-preview'); preview.setAttribute('aria-live', 'polite');
  let tableWindow;
  const cells = [];
  const highlight = (r, c) => {
    preview.textContent = `${c} × ${r} table`;
    for (const cell of cells) cell.classList.toggle('chosen', Number(cell.dataset.row) <= r && Number(cell.dataset.column) <= c);
  };
  for (let r = 1; r <= 8; r++) {
    const row = el('div'); row.setAttribute('role', 'row');
    for (let c = 1; c <= 10; c++) {
      const cell = el('button'); cell.type = 'button'; cell.setAttribute('role', 'gridcell');
      cell.setAttribute('aria-label', `${c} columns, ${r} rows`); cell.setAttribute('aria-rowindex', r); cell.setAttribute('aria-colindex', c);
      cell.dataset.row = r; cell.dataset.column = c; cell.tabIndex = r === 1 && c === 1 ? 0 : -1;
      cell.onpointerenter = cell.onfocus = () => highlight(r, c);
      cell.onclick = () => { rows.value = r; columns.value = c; tableWindow.querySelector('form').requestSubmit(); };
      cell.onkeydown = event => {
        const steps = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -10, ArrowDown: 10, Home: -(c - 1), End: 10 - c};
        if (!(event.key in steps)) return;
        event.preventDefault();
        const target = cells[Math.min(79, Math.max(0, (r - 1) * 10 + c - 1 + steps[event.key]))];
        cells.forEach(item => { item.tabIndex = item === target ? 0 : -1; }); target.focus();
      };
      row.append(cell); cells.push(cell);
    }
    grid.append(row);
  }
  grid.onpointerleave = () => highlight(Number(rows.value), Number(columns.value));
  rows.oninput = columns.oninput = grid.onpointerleave;
  highlight(3, 3);
  const dimensions = el('div', undefined, 'folio-paragraph-grid'); dimensions.append(field('Rows', rows), field('Columns', columns));
  tableWindow = dialog("Insert table", [preview, grid, dimensions, headerLabel], "Insert table", () => {
    const r = Number(rows.value), c = Number(columns.value);
    if (!Number.isInteger(r) || !Number.isInteger(c) || r < 1 || r > 20 || c < 1 || c > 10) throw new Error("Choose 1–20 rows and 1–10 columns.");
    state.editor.chain().focus().insertTable({ rows: r, cols: c, withHeaderRow: header.checked }).run();
  });
}
function imageDialog() {
  const file = el("input");
  file.type = "file";
  file.accept = "image/png,image/jpeg,image/webp";
  file.setAttribute("aria-label", "Image file");
  const alt = el("input");
  alt.maxLength = 1000;
  alt.setAttribute("aria-label", "Image description");
  dialog("Insert an image", [field("Image", file), field("Description for accessibility", alt), el("p", "PNG, JPEG or WebP, up to 2 MB. Images are embedded in this document.", "folio-muted")], "Insert image", async signal => {
    if (!file.files[0]) throw new Error("Choose an image first.");
    await insertImage(file.files[0], alt.value, signal);
  });
}
async function insertImage(file, alt = "", signal) {
  if (!/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > 2_000_000) throw new Error("Choose a PNG, JPEG or WebP image smaller than 2 MB.");
  const selection = rememberSelection();
  try {
    const src = await readFile(file);
    if (signal?.aborted) return;
    restoreSelection(selection);
    state.editor.chain().focus().setImage({ src, alt: alt || file.name }).run();
  } finally {
    bookmarks.delete(selection);
  }
}
async function saveCopy() {
  // Freeze user interaction while taking an atomic copy; never relabel edits
  // typed during an older in-flight save as already saved.
  if (state.copying) return;
  state.copying = true;
  app.inert = true;
  try {
    clearTimeout(state.timer);
    if (state.saving) await state.saving.catch(() => {});
    clearTimeout(state.timer);
    const version = state.version;
    const data = snapshot();
    data.title = `${data.title.slice(0, 173)} (copy)`;
    const content_base64 = (await readFile(new Blob([JSON.stringify(data)], {type: 'application/json'}))).split(',')[1];
    const copied = await api({action: 'import', filename: 'Folio-copy.json', content_base64});
    if (state.version !== version) {
      notice('A copy was created. Your newer edits remain in this document; save or copy them before leaving.');
      return;
    }
    state.saved = version;
    state.paused = false;
    state.copying = false;
    await openDocument(copied);
    notice("Your edits have been saved as a separate document.");
  } finally {
    state.copying = false;
    app.inert = false;
  }
}
function wordCountDialog() {
  const detail = (title, text) => {
    let characters = 0, withoutSpaces = 0;
    for (const c of text) { characters++; if (!/\s/u.test(c)) withoutSpaces++; }
    return [el('h3', title), el('p', wordCount(text).toLocaleString() + ' words'),
      el('p', characters.toLocaleString() + ' characters (including spaces)'),
      el('p', withoutSpaces.toLocaleString() + ' characters (without spaces)')];
  };
  const current = state.editor.state;
  const fields = current.selection.empty ? [] : detail('Selection', selectionText(current));
  dialog('Document statistics', [...fields, ...detail('Document', plainText(current.doc))], 'Done', () => {});
}
function preparePrint() {
  if (!state.doc || !state.editor) return;
  let style = $("folioPrintPage");
  if (!style) {
    style = el("style");
    style.id = "folioPrintPage";
    document.head.append(style);
  }
  const p = state.doc.page;
  style.textContent = `@page { size: ${p.size === "letter" ? "letter" : "A4"} ${p.orientation}; margin: ${{ normal: "1in", narrow: "0.5in", wide: "1.5in" }[p.margin]}; }`;
  renderNotes();
}
async function printDocument() {
  await flush();
  preparePrint();
  window.print();
}
window.addEventListener("popstate", () => {
  const id = new URLSearchParams(location.search).get("id");
  const prior = state.doc ? `/folio?id=${encodeURIComponent(state.doc.id)}` : "/folio";
  (id ? openDocument(id, false) : dashboard(false)).catch((error) => {
    history.replaceState({}, "", prior);
    notice(error.message, true);
  });
});
app.append(el("div", "Opening Folio…", "folio-loading"));
const initialId = new URLSearchParams(location.search).get("id");
(initialId ? openDocument(initialId, false) : dashboard(false)).catch((error) => {
  app.replaceChildren(el("div", error.message, "folio-loading"), btn("Try again", () => location.reload(), "folio-button"));
  notice(error.message, true);
});
