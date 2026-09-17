export async function uploadCollectionPDFs(files, collection, {notice, changed}) {
  if (!files.length || files.length > 20) throw new Error('Drop between 1 and 20 PDFs at a time.');
  if (files.some(file => !file.name.toLowerCase().endsWith('.pdf') || file.size > 100 * 1024 * 1024)) throw new Error('Choose PDFs up to 100 MB each.');
  const status = await fetch('/api/library-access').then(response => response.json());
  const access = status.library_access;
  if (!access?.enabled) throw new Error('PDF import is not enabled for this account. You can still link papers already in the corpus.');
  const token = document.cookie.split('; ').find(value => value.startsWith('pfempire_csrf='))?.split('=')[1];
  const security = {...(token ? {'X-CSRF-Token': decodeURIComponent(token)} : {}), ...(access.csrf_token ? {'X-OriginRAG-Local-Token': access.csrf_token} : {})};
  const uploads = new Set();
  let uploadError;
  for (const file of files) {
    notice('Uploading ' + file.name + '… PDFs enter the shared corpus; collection membership stays private.');
    try {
      const response = await fetch('/api/library-upload', {method: 'POST', headers: {...security,
        'Content-Type': 'application/pdf', 'X-Paper-Filename': encodeURIComponent(file.name), 'X-Paper-Defer-Sync': '1',
        ...(collection ? {'X-OriginRAG-Collection': collection} : {})}, body: file});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Upload failed.');
      uploads.add(result.upload_id);
    } catch (error) { uploadError = error; break; }
  }
  if (!uploads.size) throw uploadError;
  const sync = await fetch('/api/library-sync', {method: 'POST', headers: {...security, 'Content-Type': 'application/json'}, body: '{}'});
  if (!sync.ok) throw new Error('PDFs were uploaded, but processing could not start. Retry the library update.');
  notice(uploadError ? uploadError.message + ' Earlier uploads are still being processed.' : 'Preparing PDFs… accepted papers will be linked automatically.');
  const deadline = Date.now() + 360000;
  let issues = false;
  while (uploads.size && Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 2000));
    const response = await fetch('/api/library-status');
    if (!response.ok) throw new Error('Import continues on the server. Reload the collection to check its progress.');
    const progress = await response.json();
    if (['queued', 'running'].includes(progress.state)) notice((progress.stage || 'Preparing PDFs…') + (progress.progress ? ` · ${progress.progress}%` : ''));
    for (const result of progress.results || []) {
      if (!uploads.has(result.upload_id)) continue;
      uploads.delete(result.upload_id);
      if (result.status !== 'accepted' || (collection && result.collection_link_state !== 'added')) issues = true;
    }
    if (progress.state === 'error') throw new Error('The library update needs attention. Your uploaded PDFs have been kept.');
  }
  await changed();
  if (uploads.size) notice('Processing continues on the server. Accepted papers will be linked even if you leave this page.');
  else if (issues || uploadError) notice('Some PDFs need review or were already in the corpus. Use Browse corpus to link existing papers; no uncertain match was added.', true);
  else notice(collection ? 'PDFs imported and linked to your collection.' : 'PDFs imported into the corpus.');
}
