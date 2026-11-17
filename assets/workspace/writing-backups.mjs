function el(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

export function setupProjectBackup({getProject, save, notify}) {
  let directory = null, automatic = false, busy = false, lastRevision = null;
  let dialog, status, folderButton, automaticInput, saveButton;
  function message(text, error = false) {
    if (status) { status.textContent = text; status.classList.toggle('error', error); }
    if (error) notify(text, true);
  }
  async function backup(downloadOnly = false, scheduled = false) {
    if (busy) return;
    busy = true;
    if (saveButton) saveButton.disabled = true;
    try {
      if (scheduled && document.hidden) return;
      await save();
      const project = getProject(), revision = project.revision;
      if (scheduled && revision === lastRevision) return;
      message('Preparing a backup of your saved project…');
      const response = await fetch('/api/writing/export?id=' + encodeURIComponent(project.id) + '&revision=' + revision, {credentials: 'same-origin', signal: AbortSignal.timeout(30000)});
      if (!response.ok || !response.headers.get('content-type')?.includes('application/zip')) throw new Error('Backup could not be exported. Save your project and try again.');
      const blob = await response.blob();
      const name = (project.name || 'manuscript').replace(/[^\p{L}\p{N}_-]+/gu, '-').slice(0, 60);
      const filename = `${name}-${project.id.slice(0, 8)}-r${revision}-${new Date().toISOString().replace(/[:.]/g, '-')}.zip`;
      if (directory && !downloadOnly) {
        if (await directory.queryPermission({mode: 'readwrite'}) !== 'granted') throw new Error('Folder access expired. Choose your backup folder again.');
        const handle = await directory.getFileHandle(filename, {create: true});
        const stream = await handle.createWritable();
        try { await stream.write(blob); await stream.close(); }
        catch (error) { await stream.abort().catch(() => {}); throw error; }
        message(`Saved revision ${revision} to ${directory.name}. Dropbox sync is handled by your desktop app.`);
      } else {
        const url = URL.createObjectURL(blob), link = el('a'); link.href = url; link.download = filename; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
        message('Backup downloaded. Move the ZIP into your Dropbox folder if you want a cloud copy.');
      }
      lastRevision = revision;
    } catch (error) {
      automatic = false; if (automaticInput) automaticInput.checked = false;
      message('Backup failed: ' + error.message + ' Your saved manuscript is unchanged.', true);
    } finally { busy = false; if (saveButton) saveButton.disabled = false; }
  }
  function open() {
    if (dialog?.open) return;
    dialog?.remove();
    dialog = el('dialog'); dialog.id = 'backupDialog'; dialog.setAttribute('aria-labelledby', 'backupTitle');
    const title = el('h2', 'Project backups'); title.id = 'backupTitle';
    const description = el('p', 'Keep timestamped LaTeX ZIP copies on your own device. Choose a folder inside your synced Dropbox, OneDrive or another backup folder. No cloud password or token is stored in Quill.');
    const scope = el('p', 'Includes this manuscript’s source, bibliography and uploaded figures, not the shared paper corpus or other users’ data. Restore through Quill → Import LaTeX ZIP. Your desktop sync app handles cloud upload; Quill cannot verify that it reached the cloud.');
    folderButton = el('button', 'Choose backup folder…'); folderButton.id = 'chooseBackupFolder';
    folderButton.hidden = !('showDirectoryPicker' in window) || !window.isSecureContext;
    folderButton.onclick = async () => {
      try {
        const selected = await window.showDirectoryPicker({mode: 'readwrite', id: 'quill-backups'});
        directory = selected; lastRevision = null;
        automaticInput.disabled = false; message(`Folder selected: ${selected.name}. Choose Save backup to create a copy.`);
      } catch (error) { if (error.name !== 'AbortError') message(error.message, true); }
    };
    const label = el('label'); automaticInput = el('input'); automaticInput.type = 'checkbox'; automaticInput.id = 'autoBackup';
    automaticInput.checked = automatic; automaticInput.disabled = !directory;
    automaticInput.onchange = () => { automatic = automaticInput.checked; };
    label.append(automaticInput, ' Back up changed revisions every five minutes while this tab is open');
    const note = el('p', folderButton.hidden ? 'This browser uses ZIP downloads. For direct folder backups, use a Chromium browser on localhost or HTTPS.' : 'Folder access and automatic backups last only for this tab. Existing backups are never deleted automatically.');
    status = el('p', directory ? `Backup folder: ${directory.name}` : 'No backup folder selected.'); status.id = 'backupStatus'; status.setAttribute('role', 'status');
    const actions = el('div'); actions.className = 'actions';
    saveButton = el('button', 'Save backup'); saveButton.id = 'saveProjectBackup'; saveButton.onclick = () => backup();
    const download = el('button', 'Download backup ZIP'); download.onclick = () => backup(true);
    const close = el('button', 'Close'); close.onclick = () => dialog.close();
    actions.append(saveButton, download, close); dialog.append(title, description, scope, folderButton, label, note, status, actions);
    document.body.append(dialog); dialog.showModal();
  }
  const timer = setInterval(() => { if (automatic && directory) backup(false, true); }, 300000);
  window.addEventListener('pagehide', () => clearInterval(timer), {once: true});
  return {open};
}
