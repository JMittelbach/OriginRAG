from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import time
import uuid
import zipfile

TEXT_TYPES = {'.tex', '.bib', '.sty', '.cls', '.bst', '.bbl', '.txt', '.md', '.csv'}
BINARY_TYPES = {'.png', '.jpg', '.jpeg', '.pdf'}
MAX_PROJECT_BYTES = 12_000_000

STARTER = r'''\documentclass[11pt]{article}
\usepackage[margin=2.5cm]{geometry}
\usepackage{graphicx}
\usepackage{hyperref}
\title{A new manuscript}
\author{Your name}
\date{\today}
\begin{document}
\maketitle
\begin{abstract}
Start with the question, the approach, and the main finding.
\end{abstract}
\section{Introduction}
Your manuscript starts here.
\section{Methods}
Describe the study design and analysis.
\section{Results}
Present the findings.
\section{Discussion}
Discuss their meaning and limitations.
\bibliographystyle{plain}
\bibliography{references}
\end{document}
'''

TEMPLATES = [
    {'name': 'G3: Genes, Genomes, Genetics', 'url': 'https://academic.oup.com/G3JOURNAL/pages/author-guidelines'},
    {'name': 'Bioinformatics', 'url': 'https://academic.oup.com/bioinformatics/pages/author-guidelines'},
    {'name': 'Bioinformatics Advances', 'url': 'https://academic.oup.com/bioinformaticsadvances/pages/author-guidelines'},
    {'name': 'Briefings in Bioinformatics', 'url': 'https://academic.oup.com/bib/pages/msprep_submission'},
    {'name': 'Springer Nature / Nature Portfolio', 'url': 'https://www.springernature.com/gp/authors/campaigns/latex-author-support'},
]


class WritingConflict(ValueError):
    pass


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('Invalid project or collection identifier.')
    return value


def file_name(value):
    if not isinstance(value, str) or len(value) > 160 or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_./ -]*', value):
        raise ValueError('Use a relative filename with letters, numbers, spaces, underscores or hyphens.')
    path = PurePosixPath(value)
    if any(p in {'', '.', '..'} or p.startswith('.') for p in value.split('/')) or path.suffix.lower() not in TEXT_TYPES | BINARY_TYPES:
        raise ValueError('Unsupported file type or unsafe path.')
    return value


def decode_file(name, value):
    file_name(name)
    if not isinstance(value, str) or len(value) > MAX_PROJECT_BYTES * 2:
        raise ValueError('Invalid file contents.')
    if PurePosixPath(name).suffix.lower() in TEXT_TYPES:
        if '\x00' in value:
            raise ValueError('Text files cannot contain null bytes.')
        return value.encode('utf-8')
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ValueError('Binary files must be base64 encoded.') from exc


def validate_files(files, main):
    if not isinstance(files, dict) or not 1 <= len(files) <= 200:
        raise ValueError('A project must contain 1–200 files.')
    size = 0
    if not isinstance(main, str):
        raise ValueError('Choose a main document filename.')
    folded = {str(name).casefold() for name in files}
    if len(folded) != len(files):
        raise ValueError('Filenames must also be distinct on case-insensitive systems.')
    for name, content in files.items():
        size += len(decode_file(name, content))
        parents = PurePosixPath(name).parents
        if any(str(p).casefold() in folded for p in parents):
            raise ValueError('A file and folder cannot have the same path.')
    if size > MAX_PROJECT_BYTES:
        raise ValueError('Project exceeds 12 MB.')
    if main not in files or not main.endswith('.tex'):
        raise ValueError('Choose a .tex file in this project as the main document.')


def unpack_project(encoded):
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_PROJECT_BYTES:
            raise ValueError('ZIP exceeds 12 MB.')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            if len(members) > 200 or sum(m.file_size for m in members) > MAX_PROJECT_BYTES:
                raise ValueError('ZIP exceeds project limits.')
            files = {}
            for member in members:
                name = file_name(member.filename)
                if name in files or (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('ZIP contains duplicate paths or symbolic links.')
                content = archive.read(member)
                files[name] = content.decode('utf-8') if PurePosixPath(name).suffix.lower() in TEXT_TYPES else base64.b64encode(content).decode('ascii')
            choices = sorted(n for n in files if n.endswith('.tex'))
            main = 'main.tex' if 'main.tex' in choices else next(iter(choices), '')
            validate_files(files, main)
            return files, main
    except (zipfile.BadZipFile, UnicodeError, RuntimeError, NotImplementedError, TypeError) as exc:
        raise ValueError('Use a UTF-8 LaTeX project ZIP with supported files only.') from exc


def bibtex(papers):
    replacements = {'\\': r'\textbackslash{}', '{': r'\{', '}': r'\}', '%': r'\%', '&': r'\&', '#': r'\#', '_': r'\_', '$': r'\$', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    def escape(value):
        return ''.join(replacements.get(c, c) for c in str(value or '').replace('\n', ' ')[:4000])
    entries = []
    for p in papers:
        key = 'paper' + hashlib.sha256(str(p['paper_id']).encode()).hexdigest()[:12]
        fields = ',\n'.join('  ' + field + ' = {' + escape(p.get(source)) + '}' for field, source in [('title', 'title'), ('author', 'author'), ('year', 'year'), ('journal', 'journal'), ('doi', 'doi')] if p.get(source))
        entries.append('@article{' + key + ',\n' + fields + '\n}')
    return '\n\n'.join(entries) + '\n'


class WritingStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / '.writing.sqlite3'

    def connect(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        os.chmod(self.path, 0o600)
        db.execute('CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, kind TEXT, data TEXT, revision INTEGER, updated REAL)')
        return db

    def snapshot(self):
        db = self.connect()
        try:
            rows = db.execute('SELECT id, kind, data, revision FROM documents ORDER BY updated DESC').fetchall()
            result = {'projects': [], 'collections': [], 'templates': TEMPLATES}
            for key, kind, raw, revision in rows:
                data = json.loads(raw)
                result[kind].append({'id': key, 'name': data['name'], 'revision': revision,
                                     'count': len(data.get('papers', data.get('files', {}))),
                                     'collection': data.get('collection', '')})
            return result
        finally:
            db.close()

    def get(self, key, kind='projects'):
        db = self.connect()
        try:
            row = db.execute('SELECT data, revision FROM documents WHERE id=? AND kind=?', (identifier(key), kind)).fetchone()
            if not row:
                raise KeyError('Project or collection not found.')
            return {**json.loads(row[0]), 'id': key, 'revision': row[1]}
        finally:
            db.close()

    def change(self, payload, catalogue):
        action = payload.get('action')
        allowed = {'create_project', 'save_project', 'create_collection', 'collection_paper'}
        if not isinstance(action, str) or action not in allowed:
            raise ValueError('Unknown writing action.')
        kind = 'collections' if action in {'create_collection', 'collection_paper'} else 'projects'
        create = action.startswith('create_')
        key = uuid.uuid4().hex if create else identifier(payload.get('id'))
        db = self.connect()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                if create:
                    name = payload.get('name')
                    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
                        raise ValueError('Choose a name of 1–120 characters.')
                    if db.execute('SELECT COUNT(*) FROM documents').fetchone()[0] >= 150:
                        raise ValueError('Account project/collection limit reached (150).')
                    revision = 0
                    data = {'name': name.strip()}
                    if kind == 'projects':
                        files, main = unpack_project(payload['zip']) if payload.get('zip') else ({'main.tex': STARTER, 'references.bib': ''}, 'main.tex')
                        data.update(files=files, main=main, collection='')
                    else:
                        data['papers'] = []
                else:
                    row = db.execute('SELECT data, revision FROM documents WHERE id=? AND kind=?', (key, kind)).fetchone()
                    if not row:
                        raise KeyError('Project or collection not found.')
                    data, revision = json.loads(row[0]), row[1]
                    if type(payload.get('revision')) is not int or payload['revision'] != revision:
                        raise WritingConflict('Changed in another window. Export your draft before reloading.')
                    if action == 'save_project':
                        files, main = payload.get('files'), payload.get('main')
                        validate_files(files, main)
                        collection = payload.get('collection', '')
                        if collection:
                            identifier(collection)
                            if not db.execute("SELECT 1 FROM documents WHERE id=? AND kind='collections'", (collection,)).fetchone():
                                raise ValueError('Collection not found in your account.')
                        data.update(files=files, main=main, collection=collection)
                    else:
                        paper = payload.get('paper_id')
                        if not isinstance(paper, str) or not isinstance(payload.get('remove', False), bool):
                            raise ValueError('Invalid collection membership.')
                        if payload.get('remove'):
                            data['papers'] = [p for p in data['papers'] if p != paper]
                        elif paper not in data['papers']:
                            if not any(p['paper_id'] == paper for p in catalogue):
                                raise ValueError('Paper not found in the corpus.')
                            if len(data['papers']) >= 500:
                                raise ValueError('A collection holds up to 500 papers.')
                            data['papers'].append(paper)
                encoded = json.dumps(data)
                used = db.execute('SELECT COALESCE(SUM(length(data)),0) FROM documents WHERE id != ?', (key,)).fetchone()[0]
                if used + len(encoded) > 100_000_000:
                    raise ValueError('Account writing storage limit reached (100 MB).')
                db.execute('INSERT INTO documents VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data, revision=excluded.revision, updated=excluded.updated',
                           (key, kind, encoded, revision + 1, time.time()))
            return {**data, 'id': key, 'revision': revision + 1}
        finally:
            db.close()

    def collection_bib(self, key, catalogue):
        ids = set(self.get(key, 'collections')['papers'])
        return bibtex([p for p in catalogue if p['paper_id'] in ids])

    def export(self, key):
        project = self.get(key)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, content in project['files'].items():
                archive.writestr(name, decode_file(name, content))
        return stream.getvalue()
