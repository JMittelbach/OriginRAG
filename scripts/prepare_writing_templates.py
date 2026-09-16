"""Vendor pinned publisher assets, or warm only the bundled trusted starters."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

SOURCES = {
    'oup.zip': ('oup-authoring-template-1.5.zip', '025434f53ed3a46b9fd84d50a78c734c309476bc47b1cb7a90c383734df38e97'),
    'lppl-1-3c.txt': ('LPPL-1.3c.txt', '3d262cdf34dafa6955f703c634a8c238ec44109bc8dd6ef34fb7aa54809f7e66'),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, help='Directory of the two downloaded, pinned official assets.')
    parser.add_argument('--warm-cache', action='store_true', help='Compile only trusted bundled starters, allowing TeX package downloads.')
    parser.add_argument('--previews', action='store_true', help='Render bundled first-page thumbnails with the isolated offline compiler.')
    parser.add_argument('--tectonic', default='tectonic')
    args = parser.parse_args()
    if args.source_dir:
        target = ROOT / 'assets/writing_templates/vendor'
        target.mkdir(parents=True, exist_ok=True)
        for source, (destination, checksum) in SOURCES.items():
            data = (args.source_dir / source).read_bytes()
            if hashlib.sha256(data).hexdigest() != checksum:
                raise ValueError(f'Unexpected checksum: {source}')
            shutil.copyfile(args.source_dir / source, target / destination)
    if args.warm_cache:
        from originrag.writing import decode_file
        from originrag.writing_templates import list_templates, template_files
        for item in list_templates():
            files, main_file = template_files(item['id'])
            with tempfile.TemporaryDirectory(prefix='originwrite-trusted-template-') as directory:
                root = Path(directory)
                for name, value in files.items():
                    dest = root / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(decode_file(name, value))
                print('Preparing ' + item['name'], flush=True)
                subprocess.run([args.tectonic, '--untrusted', '--keep-logs', main_file], cwd=root, check=True, timeout=300)
                sample = files[main_file].replace('% \\bibliographystyle', '\\bibliographystyle').replace('% \\bibliography', '\\bibliography')
                sample = sample.replace('\\section{Introduction}', '\\section{Introduction}\nCitation test \\cite{originwriteTest}.')
                (root / main_file).write_text(sample, encoding='utf-8')
                (root / 'references.bib').write_text('@article{originwriteTest, author={Doe, Jane}, title={Synthetic package preparation test}, journal={Test journal}, year={2026}}\n', encoding='utf-8')
                subprocess.run([args.tectonic, '--untrusted', '--keep-logs', main_file], cwd=root, check=True, timeout=300)
    if args.previews:
        import fitz
        from originrag.writing_compile import compile_project
        from originrag.writing_templates import ASSETS, list_templates, template_files
        for item in list_templates():
            files, main_file = template_files(item['id'])
            result = compile_project({'files': files, 'main': main_file, 'revision': 1})
            if not result['ok']:
                raise ValueError(result['log'])
            with fitz.open(stream=result['pdf'], filetype='pdf') as document:
                page = document[0]
                page.get_pixmap(matrix=fitz.Matrix(420 / page.rect.width, 420 / page.rect.width), alpha=False).save(ASSETS / item['id'] / 'preview.png')
            print(item['id'] + ': preview generated from compiled PDF', flush=True)


if __name__ == '__main__':
    main()
