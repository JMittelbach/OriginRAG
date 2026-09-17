export function openLanguageCheck({api, save, getProject, editor, enabled, reveal}) {
  if (document.getElementById('languageDialog')) return;
  const selected = editor.selection();
  const dialog = document.createElement('dialog');
  dialog.id = 'languageDialog'; dialog.className = 'writing-sharing';
  const element = (tag, text) => { const node = document.createElement(tag); node.textContent = text; return node; };
  const heading = element('h2', 'Check selected prose');
  const about = element('p', 'Spelling and grammar suggestions from the configured Ollama model. Only the selected text is sent. Nothing is changed automatically. Suggestions can be wrong; scientific claims are not checked.');
  about.className = 'sharing-muted';
  const status = element('p', ''); status.setAttribute('role', 'status');
  const results = element('div', '');
  const run = element('button', 'Check language');
  const close = element('button', 'Close'); close.onclick = () => dialog.close();
  const valid = /\.tex$/i.test(selected.file) && selected.to > selected.from && selected.to - selected.from <= 3000;
  if (!enabled) status.textContent = 'Language checking is not enabled on this host.';
  else if (!valid) status.textContent = 'Select up to 3,000 characters of LaTeX prose first, then reopen this check.';
  else status.textContent = `${selected.text.length} characters selected. English and German are supported.`;
  run.disabled = !enabled || !valid;
  run.onclick = async () => {
    run.disabled = true; results.replaceChildren();
    status.textContent = 'Saving and checking… This may take up to a minute.';
    const originalSource = getProject().files[selected.file];
    try {
      await save();
      const snapshot = getProject();
      if (snapshot.files[selected.file] !== originalSource) throw new Error('The selected file changed. Close this window and select the passage again.');
      const response = await api('/api/writing', {action: 'language_check', id: snapshot.id, revision: snapshot.revision, ...selected});
      if (!dialog.isConnected) return;
      const current = () => getProject().revision === response.revision && getProject().files[selected.file] === originalSource;
      if (!current()) throw new Error('The manuscript changed during the check. Close this window and select the passage again.');
      status.textContent = response.suggestions.length ? `${response.suggestions.length} suggestion(s). Review before editing.` : 'No usable suggestions returned. This does not guarantee error-free text.';
      if (response.filtered) status.textContent += ' Unusable suggestions were omitted.';
      for (const suggestion of response.suggestions) {
        const item = element('section', ''); item.className = 'sharing-person';
        const words = element('div', '');
        words.append(element('p', `${suggestion.original} → ${suggestion.replacement}`), element('small', suggestion.reason));
        const jump = element('button', `Line ${suggestion.line}`);
        jump.onclick = () => {
          if (!current()) { status.textContent = 'These suggestions are outdated. Select the passage again.'; return; }
          dialog.close(); reveal(selected.file, suggestion.line);
        };
        item.append(words, jump); results.append(item);
      }
    } catch (error) { if (dialog.isConnected) status.textContent = error.message; }
    finally { if (dialog.isConnected) run.disabled = true; }
  };
  dialog.append(heading, about, status, results, run, close);
  dialog.addEventListener('close', () => dialog.remove(), {once: true});
  document.body.append(dialog); dialog.showModal();
}
