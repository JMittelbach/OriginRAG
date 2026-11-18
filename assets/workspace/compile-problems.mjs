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

function diagnosticLines(rows, files) {
  const requests = new Map(), ranges = new Map();
  for (const {file, line} of rows) {
    if (!Object.hasOwn(files, file) || !/\.(tex|bib|sty|cls|bst|bbl|txt|md|csv)$/i.test(file) || !Number.isSafeInteger(line) || line < 1) continue;
    if (!requests.has(file)) requests.set(file, new Set());
    requests.get(file).add(line);
  }
  for (const [file, numbers] of requests) {
    const source = files[file], locations = new Map();
    let n = 1, from = 0, end = source.indexOf('\n');
    for (const target of [...numbers].sort((a, b) => a - b)) {
      while (n < target && end !== -1) {
        n++; from = end + 1; end = source.indexOf('\n', from);
      }
      if (n !== target) break;
      let to = end === -1 ? source.length : end;
      if (to > from && source[to - 1] === '\r') to--;
      locations.set(target, {from, to});
    }
    ranges.set(file, locations);
  }
  return ranges;
}

const plain = text => !/[\\{}%$#&_^~\r\n]/.test(text);
function simpleFix(message, text) {
  const onlyMatch = pattern => {
    const m = pattern.exec(text);
    return m && plain(text.slice(0, m.index)) && plain(text.slice(m.index + m[0].length)) ? m : null;
  };
  if (/^Too many \}'s\.?$/i.test(message)) {
    const trimmed = text.trimEnd();
    if (!trimmed.endsWith('}')) return null;
    const before = trimmed.slice(0, -1);
    if (before.trim() && plain(before.replace(/\\(?:textbf|textit|texttt|emph)\{[^\\{}%$#&_^~]+\}/, '')))
      return {label: 'Remove the extra closing brace.', replacement: before + text.slice(trimmed.length)};
  } else if (/^Missing \} inserted\.?$/i.test(message)) {
    const m = onlyMatch(/\$[A-Za-z0-9]+[_^]\{[A-Za-z0-9+-]+\$/);
    if (m) {
      const at = m.index + m[0].length - 1;
      return {label: 'Close the math group before the final $.', replacement: text.slice(0, at) + '}' + text.slice(at)};
    }
  } else if (/^Undefined control sequence\.?$/i.test(message)) {
    const m = onlyMatch(/\\(textbff|textitt|textttt|emphh)\{[^\\{}%$#&_^~]+\}/);
    if (m) return {label: 'Remove the doubled final letter in the command.', replacement: text.slice(0, m.index + m[1].length) + text.slice(m.index + m[1].length + 1)};
  }
  return null;
}

export function setupCompileProblems({editor, project, revision, reveal, recompile, report}) {
  setupResize();
  const root = $('problems');
  let build;
  function invalidate() {
    if (!build) return;
    const hadErrors = build.failed;
    build = null;
    queueMicrotask(() => { if (!build) editor().setCompilerErrors([]); });
    root.replaceChildren(...(hadErrors ? [element('p', 'Source changed. Recompile to update these diagnostics.')] : []));
  }
  function render(result, isCurrent) {
    root.replaceChildren();
    const current = build = {failed: !result.ok};
    const fresh = () => build === current && isCurrent();
    if (!fresh()) { invalidate(); return; }
    const rows = [...(result.diagnostics || [])];
    if (!result.ok && rows.every(p => ['warning','note'].includes(p.severity)))
      rows.push({message: 'Build failed without a source location. Check the compile log.'});
    const seen = new Set(), marks = [], files = project().files, ranges = diagnosticLines(rows, files);
    for (const p of rows) {
      const message = String(p.message || 'Compiler diagnostic');
      const severity = p.severity || (/\bwarning:/i.test(message) || result.ok ? 'warning' : 'error');
      const key = JSON.stringify([p.file, p.line, message]);
      if (seen.has(key)) continue; seen.add(key);
      const range = ranges.get(p.file)?.get(p.line);
      const row = element('section', '', 'compile-problem');
      const label = range ? action(p.file + ':' + p.line + ' · ' + message, () => { if (fresh()) reveal(p.file, p.line); }) : element('span', message);
      row.append(element('strong', severity, 'problem-' + severity), label); root.append(row);
      if (severity !== 'error' || !range) continue;
      marks.push(p);
      if (result.ok || !/\.tex$/i.test(p.file) || range.to - range.from > 320) continue;
      const original = files[p.file].slice(range.from, range.to), fix = simpleFix(message.trim(), original);
      if (!fix) continue;
      const edit = {...range, file: p.file, original, replacement: fix.replacement};
      const box = element('div', '', 'compiler-advice');
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
      box.append(element('span', fix.label), element('pre', fix.replacement), apply, action('Dismiss', () => box.remove()));
      row.append(box);
    }
    editor().setCompilerErrors(marks);
  }
  return {render, invalidate};
}
