const textFiles = /\.(tex|bib|sty|cls|bst|bbl|txt|md|csv)$/i;

function element(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
}

export function collaborationStyle() {
  if (document.querySelector('link[data-writing-collaboration]')) return;
  const style = element('link'); style.rel = 'stylesheet'; style.href = '/assets/workspace/writing-collaboration.css';
  style.dataset.writingCollaboration = ''; document.head.append(style);
}

export function snapshotProject(project) {
  return {files: {...project.files}, main: project.main, collection: project.collection || '', revision: project.revision};
}

function ambiguitySpan(base, change, budget) {
  const {from, to, insert} = change, size = insert.length;
  let left = 0, right = 0;
  while (from - left > 0) {
    const last = size ? (left < size ? insert[size - left - 1] : base[from - left + size - 1]) : base[from - left - 1];
    if (last !== base[to - left - 1]) break;
    left++; if (--budget.work < 0) throw new Error('Repeated text exceeds the safe merge limit. Your draft has been kept.');
  }
  while (to + right < base.length) {
    const first = size ? (right < size ? insert[right] : base[to + right - size]) : base[to + right];
    if (first !== base[from + right]) break;
    right++; if (--budget.work < 0) throw new Error('Repeated text exceeds the safe merge limit. Your draft has been kept.');
  }
  return {from: from - left, to: to + right};
}

function overlapping(a, b, spans) {
  const left = spans.get(a), right = spans.get(b);
  if (a.from === a.to || b.from === b.to) return left.from <= right.to && right.from <= left.to;
  return Math.max(left.from, right.from) < Math.min(left.to, right.to);
}

export function rebaseDraft(base, draft, remote, diff) {
  const conflicts = [], files = {};
  const budget = {work: 2000000, files: 8};
  const scalar = (name, before, ours, theirs) => {
    if (ours === theirs || theirs === before) return ours;
    if (ours === before) return theirs;
    conflicts.push(name); return ours;
  };
  for (const name of new Set([...Object.keys(base.files), ...Object.keys(draft.files), ...Object.keys(remote.files)])) {
    const before = base.files[name], ours = draft.files[name], theirs = remote.files[name];
    let value;
    if (ours === theirs || theirs === before) value = ours;
    else if (ours === before) value = theirs;
    else if ([before, ours, theirs].every(item => typeof item === 'string') && textFiles.test(name)) {
      budget.work -= before.length + ours.length + theirs.length;
      if (budget.work < 0 || --budget.files < 0) throw new Error('These concurrent edits exceed the safe merge limit. Your draft has been kept.');
      const left = diff(before, ours), right = diff(before, theirs);
      if (left === null || right === null) { conflicts.push(name); continue; }
      const spans = new Map([...left, ...right].map(change => [change, ambiguitySpan(before, change, budget)]));
      const combined = [...left];
      let conflict = false;
      for (const change of right) {
        budget.work -= left.length;
        if (budget.work < 0) throw new Error('These concurrent edits exceed the safe merge limit. Your draft has been kept.');
        const duplicate = left.find(other => other.from === change.from && other.to === change.to && other.insert === change.insert);
        if (duplicate) {
          const span = spans.get(duplicate);
          if (span.from !== duplicate.from || span.to !== duplicate.to) { conflict = true; break; }
          continue;
        }
        if (left.some(other => overlapping(change, other, spans))) { conflict = true; break; }
        combined.push(change);
      }
      if (conflict) { conflicts.push(name); continue; }
      let cursor = 0; value = '';
      for (const change of combined.sort((a, b) => a.from - b.from || a.to - b.to)) {
        value += before.slice(cursor, change.from) + change.insert; cursor = change.to;
      }
      value += before.slice(cursor);
    } else { conflicts.push(name); continue; }
    if (value !== undefined) files[name] = value;
  }
  const main = scalar('main document', base.main, draft.main, remote.main);
  const collection = scalar('project library', base.collection || '', draft.collection || '', remote.collection || '');
  if (conflicts.length) throw new Error('Concurrent changes overlap in ' + conflicts.join(', ') + '. Your draft has been kept.');
  return {...remote, files, main, collection};
}

function action(label, run) {
  const button = element('button', label); button.type = 'button'; button.onclick = run; return button;
}

export async function openSharing({api, project, changed, notice}) {
  document.getElementById('sharingDialog')?.remove(); collaborationStyle();
  const dialog = element('dialog', undefined, 'writing-sharing'); dialog.id = 'sharingDialog';
  dialog.setAttribute('aria-labelledby', 'sharingTitle');
  const title = element('h2', 'Share this manuscript'); title.id = 'sharingTitle';
  const header = element('div', undefined, 'sharing-heading'), close = action('Close', () => dialog.close()); close.id = 'sharingClose';
  header.append(title, close);
  const description = element('p', 'Only the people added here can open and edit this project. A copied link does not grant access.', 'sharing-muted');
  const error = element('p', '', 'sharing-error'); error.id = 'sharingError'; error.setAttribute('role', 'alert');
  const members = element('div', undefined, 'sharing-members'); members.id = 'sharingMembers';
  const label = element('label', 'Add a collaborator'); const search = element('input'); search.type = 'search'; search.id = 'sharingSearch';
  search.placeholder = 'Search people by name'; search.autocomplete = 'off'; search.maxLength = 80;
  label.append(search);
  const results = element('div', undefined, 'sharing-results'); results.id = 'sharingResults'; results.setAttribute('aria-live', 'polite');
  search.disabled = true;
  const urlLabel = element('label', 'Project link'), url = element('input'); url.id = 'sharingLink'; url.readOnly = true;
  url.value = new URL('/write?id=' + encodeURIComponent(project.id), location.origin).href; urlLabel.append(url);
  const copy = action('Copy project link', async () => {
    try { await navigator.clipboard.writeText(url.value); copy.textContent = 'Link copied'; }
    catch { url.focus(); url.select(); error.textContent = 'Copy the selected link with Ctrl/Cmd+C.'; }
  }); copy.id = 'sharingCopy';
  dialog.append(header, description, error, members, label, results, urlLabel, copy); document.body.append(dialog); dialog.showModal();
  let current, ticket = 0, timer, closed = false, busy = false;
  dialog.addEventListener('close', () => { closed = true; ticket++; clearTimeout(timer); dialog.remove(); });
  const person = user => user.display_name || user.username;
  const identity = user => `${person(user)} (@${user.username})`;
  function personAction(label, user, run) {
    const control = action(label + ' ' + person(user), run);
    control.setAttribute('aria-label', label + ' ' + identity(user)); control.title = label + ' @' + user.username; return control;
  }
  function render(data) {
    current = data; changed(data.collaboration); members.replaceChildren();
    const owner = element('p', identity(data.collaboration.owner) + ' · Owner'); members.append(owner);
    for (const user of data.members) {
      const row = element('div', undefined, 'sharing-person');
      row.append(element('span', identity(user) + ' · Editor'));
      if (data.collaboration.can_manage) row.append(personAction('Remove', user, () => mutate('remove', user.user_id)));
      members.append(row);
    }
    if (!data.members.length) members.append(element('p', 'This manuscript is private to you.', 'sharing-muted'));
    label.hidden = !data.collaboration.can_manage; results.hidden = label.hidden;
    search.disabled = busy || data.members.length >= data.limit;
  }
  async function mutate(operation, userId) {
    if (busy || closed) return;
    busy = true; error.textContent = ''; dialog.setAttribute('aria-busy', 'true');
    dialog.querySelectorAll('button').forEach(button => { if (button !== close) button.disabled = true; });
    try {
      const data = await api('/api/writing/collaboration', {action: operation, id: project.id, user_id: userId});
      if (closed) return;
      render(data); results.replaceChildren(); search.value = '';
      notice(operation === 'add' ? 'Collaborator added. They can open this project from their writing desk.' : 'Project access removed.');
    } catch (problem) { if (!closed) error.textContent = problem.message; }
    finally {
      busy = false; dialog.removeAttribute('aria-busy');
      dialog.querySelectorAll('button').forEach(button => { button.disabled = false; });
      if (current && !closed) search.disabled = current.members.length >= current.limit;
    }
  }
  search.addEventListener('input', () => {
    clearTimeout(timer); const query = search.value.trim(), sequence = ++ticket; results.replaceChildren();
    if (query.length < 2) return;
    timer = setTimeout(async () => {
      try {
        const data = await api('/api/writing/collaborators?query=' + encodeURIComponent(query));
        if (closed || sequence !== ticket) return;
        const users = data.users.filter(user => user.user_id !== current.collaboration.owner.user_id && !current.members.some(member => member.user_id === user.user_id));
        for (const user of users) {
          const row = element('div', undefined, 'sharing-person'); row.append(element('span', identity(user)), personAction('Add', user, () => mutate('add', user.user_id))); results.append(row);
        }
        if (!users.length) results.append(element('p', 'No additional matching accounts.', 'sharing-muted'));
      } catch (problem) { if (!closed && sequence === ticket) error.textContent = problem.message; }
    }, 250);
  });
  try {
    const data = await api('/api/writing/collaboration?id=' + encodeURIComponent(project.id));
    if (!closed) { render(data); if (!label.hidden) search.focus(); }
  } catch (problem) { if (!closed) { label.hidden = true; error.textContent = problem.message; } }
}

export function openProjectCitations(project, insert) {
  collaborationStyle(); document.getElementById('projectCitations')?.remove();
  const dialog = element('dialog', undefined, 'writing-sharing'); dialog.id = 'projectCitations';
  const title = element('h2', 'Citations in this project'); title.id = 'projectCitationsTitle'; dialog.setAttribute('aria-labelledby', title.id);
  const header = element('div', undefined, 'sharing-heading'); header.append(title, action('Close', () => dialog.close()));
  const search = element('input'); search.type = 'search'; search.placeholder = 'Filter citation keys'; search.setAttribute('aria-label', 'Filter citation keys');
  const list = element('div', undefined, 'sharing-results');
  const error = element('p', '', 'sharing-error'); error.setAttribute('role', 'alert');
  const entries = [];
  for (const [file, source] of Object.entries(project.files)) if (/\.bib$/i.test(file)) {
    for (const match of source.matchAll(/^\s*@(?:article|book|booklet|inbook|incollection|inproceedings|conference|manual|mastersthesis|misc|phdthesis|proceedings|techreport|unpublished|online|report|dataset|software)\s*[({]\s*([^\s,{}()]+)\s*,/gim)) entries.push({key: match[1], file});
  }
  const render = () => {
    list.replaceChildren();
    for (const item of entries.filter(entry => entry.key.toLowerCase().includes(search.value.toLowerCase()))) {
      const row = element('div', undefined, 'sharing-person'); row.append(element('code', item.key), action('Insert citation ' + item.key, () => {
        if (insert(item.key) === false) { error.textContent = 'Open a LaTeX file and use a citation key containing letters, numbers, dots, colons, underscores or hyphens.'; return; }
        dialog.close();
      })); list.append(row);
    }
    if (!list.childElementCount) list.append(element('p', 'No matching entries in the saved project bibliography.', 'sharing-muted'));
  };
  dialog.append(header, element('p', 'The project owner manages its linked personal library. You can use and edit the shared BibTeX files.', 'sharing-muted'), error, search, list);
  document.body.append(dialog); dialog.addEventListener('close', () => dialog.remove()); search.oninput = render; render(); dialog.showModal(); search.focus();
}
