const textType = /\.(tex|bib|sty|cls|bst|bbl|txt|md|csv)$/i;
const supported = /\.(tex|bib|sty|cls|bst|bbl|txt|md|csv|png|jpg|jpeg|pdf)$/i;

function fileBytes(name, content) {
  return textType.test(name) ? new TextEncoder().encode(content).length : content.length * 3 / 4 - (content.endsWith('==') ? 2 : content.endsWith('=') ? 1 : 0);
}

export async function prepareProjectUploads(files, existing) {
  if (Object.keys(existing).length + files.length > 200) throw new Error('A project can contain up to 200 files. No files were added.');
  const seen = new Set(Object.keys(existing).map(name => name.toLowerCase()));
  let total = Object.entries(existing).reduce((size, [name, content]) => size + fileBytes(name, content), 0);
  for (const file of files) {
    if (!supported.test(file.name) || !/^[A-Za-z0-9_][A-Za-z0-9_. -]*$/.test(file.name)) throw new Error('Choose LaTeX/BibTeX, text, PNG, JPEG or PDF files with safe filenames. No files were added.');
    if (seen.has(file.name.toLowerCase())) throw new Error(`${file.name} already exists (filenames are case-insensitive). No files were added.`);
    if (file.size > 8000000) throw new Error('Individual uploads are limited to 8 MB. No files were added.');
    seen.add(file.name.toLowerCase()); total += file.size;
  }
  if (total > 12000000) throw new Error('The project would exceed 12 MB. No files were added.');
  const prepared = {};
  for (const file of files) {
    if (textType.test(file.name)) {
      const bytes = await file.arrayBuffer();
      const text = new TextDecoder('utf-8', {fatal: true}).decode(bytes);
      if (text.includes('\0')) throw new Error('Text files cannot contain null bytes. No files were added.');
      prepared[file.name] = text;
    } else {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = '';
      for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
      prepared[file.name] = btoa(binary);
    }
  }
  return prepared;
}
