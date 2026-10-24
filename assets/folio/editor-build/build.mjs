import {build} from 'esbuild';
import {readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root = path.dirname(fileURLToPath(import.meta.url));
const result = await build({absWorkingDir: root, entryPoints: ['editor.js'], outfile: '../editor.mjs', bundle: true,
  format: 'esm', minify: true, target: ['es2022'], legalComments: 'eof', metafile: true});
const packages = new Set(Object.keys(result.metafile.inputs).filter(p => p.includes('node_modules/')).map(p => {
  const parts = p.split('node_modules/').pop().split('/');
  return parts[0].startsWith('@') ? parts.slice(0, 2).join('/') : parts[0];
}));
let notices = 'Folio editor — bundled third-party licences\n';
for (const name of [...packages].sort()) {
  const folder = path.join(root, 'node_modules', name);
  const metadata = JSON.parse(await readFile(path.join(folder, 'package.json'), 'utf8'));
  if (name.startsWith('@tiptap/') && metadata.version !== '3.20.4') {
    throw new Error(`Unexpected Tiptap version for ${name}: ${metadata.version}; run npm ci.`);
  }
  let license;
  for (const filename of ['LICENSE', 'LICENSE.md', 'LICENSE.txt']) {
    try { license = await readFile(path.join(folder, filename), 'utf8'); break; } catch {}
  }
  if (!license) throw new Error(`Missing licence for ${name}`);
  notices += `\n--- ${name} ${metadata.version} ---\n${license}\n`;
}
await writeFile(path.join(root, '../EDITOR-LICENSES.txt'), notices);
