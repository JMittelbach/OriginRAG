export const FILE_FORMATS = {
  docx: {label: 'Word document (.docx)', mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
  html: {label: 'Web document (.html)', mime: 'text/html'},
  txt: {label: 'Plain text (.txt)', mime: 'text/plain'},
  json: {label: 'Folio document (.json)', mime: 'application/json'},
};

export function localFilename(value, format) {
  if (!Object.hasOwn(FILE_FORMATS, format)) throw new Error('Choose a supported file format.');
  let stem = String(value || '').normalize('NFC').replace(/[<>:"/\\|?*\u0000-\u001f\u007f]/g, '_')
    .replace(/\.(docx|html|txt|json)$/i, '').trim().replace(/[. ]+$/g, '');
  if (!stem) stem = 'Document';
  if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(stem)) stem = '_' + stem;
  return [...stem].slice(0, 120).join('') + '.' + format;
}

export function hasSavePicker() {
  return window.isSecureContext && typeof window.showSaveFilePicker === 'function';
}

export function chooseSaveFile(name, format) {
  if (!hasSavePicker()) return null;
  return window.showSaveFilePicker({
    id: 'folio-documents', suggestedName: name, excludeAcceptAllOption: true,
    types: [{description: FILE_FORMATS[format].label, accept: {[FILE_FORMATS[format].mime]: ['.' + format]}}],
  });
}

export async function writeLocalFile(handle, blob, {check, committing}) {
  let stream;
  try {
    check();
    stream = await handle.createWritable();
    check();
    await stream.write(blob);
    check();
    committing(true);
    await stream.close();
  } catch (error) {
    if (stream) {
      try { await stream.abort(); } catch { /* The browser may already have closed the stream. */ }
    }
    throw error;
  } finally {
    committing(false);
  }
}
