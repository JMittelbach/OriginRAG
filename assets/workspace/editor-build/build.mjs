import {build} from 'esbuild';
import {readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root = path.dirname(fileURLToPath(import.meta.url));
process.chdir(root);
const result = await build({entryPoints: ['editor.js'], outfile: '../latex-editor.mjs', bundle: true,
  format: 'esm', minify: true, target: ['es2022'], legalComments: 'eof', metafile: true});
const packages = new Set(Object.keys(result.metafile.inputs).filter(p => p.includes('node_modules/')).map(p => {
  const parts = p.split('node_modules/').pop().split('/');
  return parts[0].startsWith('@') ? parts.slice(0, 2).join('/') : parts[0];
}));
let notices = 'OriginWrite editor — bundled third-party licences\n';
for (const name of [...packages].sort()) {
  const folder = path.join(root, 'node_modules', name);
  const metadata = JSON.parse(await readFile(path.join(folder, 'package.json'), 'utf8'));
  const license = await readFile(path.join(folder, 'LICENSE'), 'utf8');
  notices += `\n--- ${name} ${metadata.version} ---\n${license}\n`;
}
await writeFile('../EDITOR-LICENSES.txt', notices);
