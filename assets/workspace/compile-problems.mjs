import {streamWritingRequest} from './writing-language.mjs';

const $ = id => document.getElementById(id);
function element(tag, text, cls) {
  const el = document.createElement(tag);
  if (text) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
function action(label, fn) {
  const b = element('button', label); b.type = 'button'; b.onclick = fn; return b;
}
function setupResize() {
  const handle = $('problemsResize'), log = $('logDetails');
  const key = 'quill.problems.height';
  let height = 190, drag;
  try { const stored = Number(localStorage.getItem(key)); if (stored > 0) height = stored; } catch {}
  function size(value) {
    const max = Math.max(72, Math.floor(window.innerHeight * .57));
    height = Math.max(72, Math.min(max, Math.round(value)));
    log.style.setProperty('--problems-height', height + 'px');
    for (const [attr, n] of [['min', 72], ['max', max], ['now', height]]) handle.setAttribute('aria-value' + attr, n);
  }
  function persist() { try { localStorage.setItem(key, String(height)); } catch {} }
  size(height);
  window.addEventListener('resize', () => size(height));
  handle.onpointerdown = e => {
    if (e.button !== 0 || drag) return;
    e.preventDefault(); handle.focus(); drag = {id: e.pointerId, y: e.clientY, height};
    handle.setPointerCapture(e.pointerId);
  };
  handle.onpointermove = e => { if (drag?.id === e.pointerId) size(drag.height + drag.y - e.clientY); };
  handle.onpointerup = handle.onpointercancel = handle.onlostpointercapture = () => { drag = null; persist(); };
  handle.ondblclick = () => { size(190); persist(); };
  handle.onkeydown = e => {
    if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    size(e.key === 'Home' ? 72 : e.key === 'End' ? Infinity : height + (e.key === 'ArrowUp' ? 24 : -24)); persist();
  };
}

export function setupCompileProblems({enabled, editor, project, revision, reveal, recompile, report}) {
  setupResize();
  const root = $('problems');
  let build, pending, last;
  function invalidate() {
    if (!build) return;
    const hadErrors = build.failed;
    build = null; pending?.abort();
    queueMicrotask(() => { if (!build) editor().setCompilerErrors([]); });
    root.replaceChildren(...(hadErrors ? [element('p', 'Source changed. Recompile to update these diagnostics.')] : []));
  }
  function render(result, isCurrent) {
    pending?.abort(); pending = null;
    root.replaceChildren();
    const current = build = {failed: !result.ok};
    const fresh = () => build === current && isCurrent();
    if (!fresh()) { invalidate(); return; }
    const rows = [...(result.diagnostics || [])];
    if (!result.ok && rows.every(p => ['warning','note'].includes(p.severity)))
      rows.push({message: 'Build failed without a source location. Check the compile log.'});
    const seen = new Set(), marks = [], jobs = [], counts = new Map();
    function hasLine(file, line) {
      if (!Object.hasOwn(project().files, file) || !/\.(tex|bib|sty|cls|bst|bbl|txt|md|csv)$/i.test(file) || !Number.isSafeInteger(line) || line < 1) return false;
      if (!counts.has(file)) {
        const source = project().files[file]; let n = 1, pos = -1;
        while ((pos = source.indexOf('\n', pos + 1)) !== -1) n++;
        counts.set(file, n);
      }
      return line <= counts.get(file);
    }
    for (const p of rows) {
      const message = String(p.message || 'Compiler diagnostic');
      const severity = p.severity || (/\bwarning:/i.test(message) || result.ok ? 'warning' : 'error');
      const key = JSON.stringify([p.file, p.line, message]);
      if (seen.has(key)) continue; seen.add(key);
      const located = hasLine(p.file, p.line);
      const row = element('section', '', 'compile-problem');
      const label = located ? action(p.file + ':' + p.line + ' · ' + message, () => { if (fresh()) reveal(p.file, p.line); }) : element('span', message);
      row.append(element('strong', severity, 'problem-' + severity), label); root.append(row);
      if (severity !== 'error') continue;
      if (located) marks.push(p);
      if (result.ok || !enabled) continue;
      const box = element('div', '', 'compiler-advice'); row.append(box);
      const signature = JSON.stringify([project().id, result.revision, key]);
      function present(response) {
        box.replaceChildren(element('p', response.answer));
        const edit = response.edits?.length === 1 && response.edits[0];
        if (response.filtered) box.append(element('small', 'No exact source replacement could be verified.'));
        if (!located || !edit || edit.file !== p.file || !Number.isSafeInteger(edit.from) || !Number.isSafeInteger(edit.to)
            || typeof edit.replacement !== 'string' || !edit.original || project().files[p.file].slice(edit.from, edit.to) !== edit.original) return;
        const before = element('pre', edit.original), after = element('pre', edit.replacement);
        box.append(element('small', 'Current source'), before, element('small', 'Suggested fix'), after);
        for (const note of edit.review_notes || []) box.append(element('small', note));
        const apply = action('Apply fix', async () => {
          if (!fresh() || apply.disabled) return;
          apply.disabled = true;
          try {
            const saved = await revision(project().id);
            if (!fresh() || !box.contains(apply)) return;
            if (saved.revision !== result.revision) {
              invalidate(); report('The saved manuscript changed. Recompile before applying a fix.'); return;
            }
            reveal(p.file, p.line);
            if (!editor().applyProjectEdits([edit], project().files)) { report('The source changed. Recompile to get a fresh suggestion.'); return; }
            await recompile();
          } catch (error) { report(error.message); } finally { apply.disabled = false; }
        });
        box.append(apply, action('Dismiss', () => box.replaceChildren(ask)));
      }
      async function run() {
        if (!fresh() || pending) return;
        const controller = pending = new AbortController();
        box.replaceChildren(element('p', 'Looking for a fix…'));
        try {
          const res = await streamWritingRequest({action:'compile_fix', id:project().id, revision:result.revision,
            file:located ? p.file : '', line:located ? p.line : 0, message:message.slice(0,1200)}, controller.signal, () => {});
          if (!fresh() || controller.signal.aborted) return;
          if (res.revision !== result.revision) { invalidate(); return; }
          last = {signature, response:res}; present(res);
        } catch (error) {
          if (fresh() && !controller.signal.aborted) box.replaceChildren(element('p', error.message), ask);
        } finally {
          if (pending === controller) { pending = null; for (const job of jobs) job.ask.disabled = false; }
        }
      }
      const ask = action(located ? 'Suggest fix' : 'Explain error', () => request());
      function request() {
        if (pending || !fresh()) return;
        for (const job of jobs) job.ask.disabled = true;
        void run();
      }
      box.append(ask); jobs.push({located, ask, start: () => last?.signature === signature ? present(last.response) : request()});
    }
    editor().setCompilerErrors(marks);
    if (current.failed && !enabled) root.append(element('small', 'Model suggestions are disabled on this host.'));
    (jobs.find(job => job.located) || jobs[0])?.start();
  }
  window.addEventListener('pagehide', () => pending?.abort());
  return {render, invalidate};
}
