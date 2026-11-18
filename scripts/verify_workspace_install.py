"""Verify the installed apps before recording the interpreter for ./web."""
import importlib
import os
from pathlib import Path
import sys
import tempfile

from dotenv import dotenv_values
from originrag.app_profiles import app_profile


def verify(root, name):
    profile = app_profile(name)
    if dotenv_values(root / '.env').get('ORIGINRAG_APP_PROFILE') != name:
        raise SystemExit(f'The saved application profile does not match the requested profile: {name}.')
    modules = ['dotenv', 'fitz', 'originrag.web_reader']
    assets = ['workspace/workspace.mjs', 'reader/reader.mjs', 'vendor/pdfjs/web/pdf_viewer.css',
              'vendor/pdfjs/legacy/build/pdf.min.mjs', 'vendor/pdfjs/legacy/build/pdf.worker.min.mjs']
    apps = ['Library', 'Bookshelf', 'PDF reader']
    if profile.rag:
        apps.insert(0, 'OriginRAG')
        modules.extend(['pandas', 'pydantic', 'yaml', 'rich', 'prompt_toolkit', 'typer', 'rapidfuzz'])
    if profile.write:
        apps.append('Quill')
        modules.extend(['originrag.writing_compile', 'originrag.web_workspace'])
        assets.append('workspace/latex-editor.mjs')
    if profile.folio:
        apps.append('Folio')
        modules.extend(['originrag.folio_docx', 'originrag.web_folio'])
        assets.append('folio/editor.mjs')
    for module in modules:
        importlib.import_module(module)
    for asset in assets:
        path = root / 'assets' / asset
        if not path.is_file() or not path.stat().st_size:
            raise SystemExit(f'Missing bundled web asset: {asset}. Please check the clone.')
    directory = root / 'runtime'
    directory.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=directory, delete=False) as handle:
        handle.write(sys.executable + '\n')
    os.replace(handle.name, directory / 'setup-python')
    print('Workspace ready: ' + ', '.join(apps))


if __name__ == '__main__':
    verify(Path(__file__).resolve().parents[1], sys.argv[1])
