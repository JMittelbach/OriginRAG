from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path, PurePosixPath
import zipfile


ASSETS = Path(__file__).resolve().parents[2] / 'assets/writing_templates'
ARCHIVE_HASH = '025434f53ed3a46b9fd84d50a78c734c309476bc47b1cb7a90c383734df38e97'
LICENCE_HASH = '3d262cdf34dafa6955f703c634a8c238ec44109bc8dd6ef34fb7aa54809f7e66'
PLOS_STYLE_HASH = '8470ef5e547189a61d8eef2cfd7a65fbb84b77b881ccb42ed6815da86a04c490'
TEMPLATES = (
    {'id': 'article', 'name': 'Research article', 'journals': [], 'description': 'A clean, lightweight manuscript with the standard article class.', 'licence': 'MIT', 'source': 'https://github.com/JMittelbach/OriginRAG', 'main': 'main.tex', 'publisher': 'Quill', 'layout': 'Single column'},
    {'id': 'bioinformatics', 'name': 'Bioinformatics', 'journals': ['Bioinformatics'], 'description': 'Official OUP class with a concise Quill starter and structured abstract.', 'licence': 'LPPL-1.3-or-later + MIT starter', 'source': 'https://academic.oup.com/bioinformatics/pages/author-guidelines', 'main': 'main.tex', 'publisher': 'Oxford University Press', 'layout': 'Two columns'},
    {'id': 'g3', 'name': 'G3 · OUP authoring', 'journals': ['G3: Genes, Genomes, Genetics'], 'description': 'The generic OUP class offered by G3, with a Quill starter. Not the separate GSA template.', 'licence': 'LPPL-1.3-or-later + MIT starter', 'source': 'https://academic.oup.com/g3journal/pages/author-guidelines', 'main': 'main.tex', 'publisher': 'Oxford University Press', 'layout': 'Two columns'},
    *({'id': identifier, 'name': name, 'journals': [name],
       'description': 'Quill manuscript starter with the official PLOS reference style' + (' and an author summary.' if identifier != 'plos-one' else '.'),
       'licence': 'MIT starter + LPPL-1.3-or-later bibliography style',
       'source': f'https://journals.plos.org/{journal}/s/submission-guidelines',
       'main': 'main.tex', 'publisher': 'PLOS', 'layout': 'Single column · line numbers'}
      for identifier, name, journal in (
          ('plos-one', 'PLOS ONE', 'plosone'),
          ('plos-compbiol', 'PLOS Computational Biology', 'ploscompbiol'),
          ('plos-genetics', 'PLOS Genetics', 'plosgenetics'))),
)


def list_templates():
    return [{**entry, 'journals': list(entry['journals'])} for entry in TEMPLATES]


def _verified(path, expected):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('The bundled template failed its integrity check. Reinstall the template assets.')
    return data


def template_files(template_id):
    from .writing import validate_files

    if not isinstance(template_id, str) or template_id not in {entry['id'] for entry in TEMPLATES}:
        raise ValueError('Choose a template from the built-in gallery.')
    is_plos = template_id.startswith('plos-')
    files = {
        'main.tex': (ASSETS / ('plos' if is_plos else template_id) / 'main.tex').read_text(encoding='utf-8'),
        'references.bib': '',
        'Quill-LICENSE.txt': (ASSETS.parents[1] / 'LICENSE').read_text(encoding='utf-8'),
    }
    if is_plos:
        summary = '' if template_id == 'plos-one' else '\\section*{Author summary}\nExplain the study and its significance for readers outside the field.\n'
        files['main.tex'] = files['main.tex'].replace('@@AUTHOR_SUMMARY@@', summary)
        files['plos2025.bst'] = _verified(ASSETS / 'vendor/plos2025.bst', PLOS_STYLE_HASH).decode('utf-8')
        files['LPPL-1.3c.txt'] = _verified(ASSETS / 'vendor/LPPL-1.3c.txt', LICENCE_HASH).decode('utf-8')
        files['Template-source.txt'] = (ASSETS / 'plos/README.md').read_text(encoding='utf-8')
    elif template_id != 'article':
        files['originwrite-oup-xetex.sty'] = (ASSETS / 'originwrite-oup-xetex.sty').read_text(encoding='utf-8')
        data = _verified(ASSETS / 'vendor/oup-authoring-template-1.5.zip', ARCHIVE_HASH)
        files['LPPL-1.3c.txt'] = _verified(ASSETS / 'vendor/LPPL-1.3c.txt', LICENCE_HASH).decode('utf-8')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                prefix = 'oup-authoring-template/'
                if not member.filename.startswith(prefix):
                    raise ValueError('Unexpected publisher template path.')
                name = member.filename[len(prefix):]
                if name == 'README':
                    name = 'OUP-README.txt'
                raw = archive.read(member)
                files[name] = base64.b64encode(raw).decode('ascii') if PurePosixPath(name).suffix == '.pdf' else raw.decode('utf-8')
        files['Template-source.txt'] = (ASSETS / 'README.md').read_text(encoding='utf-8')
    validate_files(files, 'main.tex')
    return files, 'main.tex'
