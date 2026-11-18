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
import unicodedata
import zipfile

from .writing_templates import list_templates, template_files
from .writing_merge import MergeConflict, merge_files, merge_value, project_basis_digest

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
% Use References to link a personal library and connect the bibliography.
% \bibliographystyle{plain}
% \bibliography{references}
\end{document}
'''

TEMPLATES = list_templates()


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


def citation_key(paper):
    authors = paper.get('authors') or paper.get('full_authors') or paper.get('author') or 'Unknown'
    if isinstance(authors, str) and authors.lstrip().startswith('['):
        try:
            authors = json.loads(authors)
        except ValueError:
            pass
    if isinstance(authors, list):
        first = authors[0] if authors else 'Unknown'
        family = first.get('family') or first.get('name') or 'Unknown' if isinstance(first, dict) else first
    else:
        first = re.split(r'\s+and\s+|;', str(authors), maxsplit=1)[0]
        family = first.split(',', 1)[0] if ',' in first else re.sub(r'\s+et\s+al\.?$', '', first).strip()
        words = family.split()
        if len(words) > 1:
            family = words[0] if all(re.fullmatch(r'[A-Z]\.?', w) for w in words[1:]) else words[-1]
    family = unicodedata.normalize('NFKD', str(family)).encode('ascii', 'ignore').decode()
    family = re.sub(r'[^A-Za-z0-9]', '', family)[:48] or 'Unknown'
    year = re.search(r'\b(?:18|19|20|21)\d{2}\b', str(paper.get('year') or ''))
    return family + (year.group(0) if year else 'nd')


def _key_suffix(number):
    result = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(97 + remainder) + result
    return result


def reference_entry(paper, key=None):
    replacements = {'\\': r'\textbackslash{}', '{': r'\{', '}': r'\}', '%': r'\%', '&': r'\&', '#': r'\#', '_': r'\_', '$': r'\$', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}

    def escape(value):
        return ''.join(replacements.get(c, c) for c in str(value or '').replace('\n', ' ')[:4000])

    authors = paper.get('authors') or paper.get('full_authors') or paper.get('author') or ''
    if isinstance(authors, str) and authors.lstrip().startswith('['):
        try:
            decoded = json.loads(authors)
            if isinstance(decoded, list):
                authors = decoded
        except (ValueError, TypeError):
            pass
    if isinstance(authors, list):
        names = []
        for author in authors:
            if isinstance(author, dict):
                if author.get('family'):
                    names.append(', '.join(escape(author.get(k)) for k in ('family', 'given') if author.get(k)))
                elif author.get('name'):
                    names.append('{' + escape(author['name']) + '}')
            elif isinstance(author, str) and author.strip():
                names.append(escape(author))
        author_field = ' and '.join(names)
    else:
        author_field = escape(authors)
    values = [(field, escape(paper.get(source))) for field, source in (
        ('title', 'title'), ('year', 'year'), ('journal', 'journal'), ('volume', 'volume'),
        ('number', 'issue'), ('pages', 'pages'), ('doi', 'doi'), ('url', 'doi_url'), ('publisher', 'publisher'))
        if paper.get(source)]
    if author_field:
        values.insert(1, ('author', author_field))
    kind = {'book': 'book', 'book-chapter': 'incollection', 'proceedings-article': 'inproceedings'}.get(paper.get('type') or paper.get('document_type'), 'article')
    fields = ',\n'.join('  ' + field + ' = {' + value + '}' for field, value in values)
    return '@' + kind + '{' + (key or citation_key(paper)) + ',\n' + fields + '\n}'


def bibtex(papers):
    return '\n\n'.join(reference_entry(p) for p in papers) + '\n'


def collection_reference_data(collection, catalogue, keys=None):
    available = {p['paper_id']: p for p in catalogue}
    references, missing = [], []
    for paper_id in collection['papers']:
        paper = available.get(paper_id)
        if paper is None:
            missing.append(paper_id)
            continue
        authors = paper.get('authors') or paper.get('full_authors') or paper.get('author') or ''
        warnings = []
        if not authors:
            warnings.append('Author metadata is missing. Check the original publication.')
        elif isinstance(authors, str) and (re.search(r'\bet\s+al\b', authors, re.I) or (' and ' not in authors and ',' not in authors)):
            warnings.append('The author list may be abbreviated. Check it before submission.')
        if not paper.get('year'):
            warnings.append('Publication year is missing.')
        key = (keys or {}).get(paper_id) or citation_key(paper)
        references.append({**paper, 'key': key, 'entry': reference_entry(paper, key), 'warnings': warnings})
    return {'id': collection['id'], 'name': collection['name'], 'revision': collection['revision'],
            'references': references, 'missing': missing,
            'bibtex': '\n\n'.join(r['entry'] for r in references) + ('\n' if references else '')}


def bibtex_entries(source):
    entries, seen, pos = {}, set(), 0
    opener_pattern = re.compile(r'@([A-Za-z]+)\s*([({])')
    key_pattern = re.compile(r'\s*([^\s,{}()]+)\s*,')
    while pos < len(source):
        if source[pos] == '%':
            newline = source.find('\n', pos)
            pos = len(source) if newline < 0 else newline + 1
            continue
        match = opener_pattern.match(source, pos) if source[pos] == '@' else None
        if not match:
            pos += 1
            continue
        start = pos
        kind, opener = match.groups()
        pos = match.end()
        body_start = pos
        braces = 1 if opener == '{' else 0
        quoted = False
        while pos < len(source):
            char = source[pos]
            if char == '\\':
                pos += 2
                continue
            if char == '"' and braces == (1 if opener == '{' else 0):
                quoted = not quoted
            if not quoted:
                if char == '{':
                    braces += 1
                elif char == '}':
                    braces -= 1
                    if braces < 0:
                        raise ValueError('Unbalanced BibTeX braces. Fix the bibliography before synchronising.')
                if (opener == '{' and braces == 0) or (opener == '(' and char == ')' and braces == 0):
                    pos += 1
                    break
            pos += 1
        else:
            raise ValueError('Unterminated BibTeX entry. Fix the bibliography before synchronising.')
        if kind.lower() in {'comment', 'string', 'preamble'}:
            continue
        key_match = key_pattern.match(source, body_start, pos - 1)
        if not key_match:
            raise ValueError('A BibTeX entry has no valid citation key.')
        key = key_match.group(1)
        if key.casefold() in seen:
            raise ValueError('Duplicate BibTeX citation keys. Resolve duplicates before synchronising.')
        seen.add(key.casefold())
        entries[key] = {'start': start, 'end': pos, 'entry': source[start:pos]}
    return entries


def entry_digest(entry):
    return hashlib.sha256(entry.strip().encode()).hexdigest()


def tex_without_comments(source):
    chars = list(source)
    start = source.find('%')
    while start >= 0:
        backslashes = 0
        pos = start - 1
        while pos >= 0 and source[pos] == '\\':
            backslashes += 1
            pos -= 1
        if backslashes % 2 == 0:
            end = source.find('\n', start)
            end = len(source) if end < 0 else end
            chars[start:end] = ' ' * (end - start)
            start = source.find('%', end)
        else:
            start = source.find('%', start + 1)
    return ''.join(chars)


def document_tex_sources(project):
    pending, seen, sources, uncertain = [project['main']], set(), [], False
    while pending:
        name = pending.pop(0)
        if name in seen:
            continue
        seen.add(name)
        source = project['files'][name]
        active = tex_without_comments(source)
        end = re.search(r'\\end\s*\{document\}', active)
        if end:
            active = active[:end.end()]
        sources.append((name, source, active))
        for command in re.finditer(r'\\(?:input|include)\b', active):
            argument = re.match(r'\s*\{\s*([^{}]+?)\s*\}', active[command.end():])
            if not argument:
                uncertain = True
                continue
            target = argument.group(1)
            target += '' if target.endswith('.tex') else '.tex'
            try:
                file_name(target)
            except ValueError:
                uncertain = True
                continue
            if target not in project['files']:
                uncertain = True
            elif target not in seen:
                pending.append(target)
    return sources, uncertain


def bibliography_connection(project, filename, connect_document):
    sources, uncertain = document_tex_sources(project)
    source = project['files'][project['main']]
    active = sources[0][2]
    all_active = '\n'.join(item[2] for item in sources)
    bibliography = r'\\(?:bibliography|addbibresource|printbibliography)\b|\\begin\s*\{thebibliography\}'
    biblatex = r'\\(?:usepackage|RequirePackage)(?:\s*\[[^\]]*\])?\s*\{[^}]*\bbiblatex\b'
    configured = re.search(bibliography + '|' + biblatex, all_active) is not None
    for command, loaded in re.findall(r'\\(documentclass|usepackage)(?:\s*\[[^\]]*\])?\s*\{([^{}]+)\}', all_active):
        for package in loaded.split(','):
            name = package.strip() + ('.cls' if command == 'documentclass' else '.sty')
            content = project['files'].get(name)
            if content:
                unknown_class = command == 'documentclass' and name != 'oup-authoring-template.cls'
                if unknown_class or re.search(biblatex, tex_without_comments(content)):
                    uncertain = True
    needed = not (configured or uncertain)
    change = None
    ending = re.search(r'\\end\s*\{document\}', active)
    if needed and ending and connect_document:
        style = 'oup-abbrvnat' if project.get('template') in {'bioinformatics', 'g3'} else 'plain'
        for _, document, _ in sources:
            for candidate in re.findall(r'^\s*%\s*\\bibliographystyle\s*\{([^{}]+)\}', document, re.M):
                try:
                    file_name(candidate + '.bst')
                except ValueError:
                    continue
                if candidate in {'plain', 'abbrv', 'unsrt', 'alpha', 'plainnat', 'abbrvnat', 'unsrtnat'} or candidate + '.bst' in project['files']:
                    style = candidate
                    break
        command = '' if re.search(r'\\bibliographystyle\b', all_active) else '\\bibliographystyle{' + style + '}\n'
        command += '\\bibliography{' + filename[:-4] + '}\n'
        pos = ending.start()
        change = {'filename': project['main'], 'before': source, 'after': source[:pos] + command + source[pos:]}
    return needed, change


def bibliography_plan(project, collection, catalogue, filename, connect_document=False, keys=None):
    if not isinstance(connect_document, bool):
        raise ValueError('Invalid bibliography connection option.')
    file_name(filename)
    if not filename.lower().endswith('.bib'):
        raise ValueError('Choose a .bib file for the bibliography.')
    source = project['files'].get(filename, '')
    existing = bibtex_entries(source)
    prior = project.get('bibliography_sync', {}).get(filename, {}).get('entries', {})
    refs = collection_reference_data(collection, catalogue, keys)
    desired = {r['key']: r for r in refs['references']}
    if len(desired) != len(refs['references']):
        raise ValueError('Two papers have the same generated citation key. Resolve the collision before synchronising.')
    added, updated, unchanged, conflicts = [], [], [], []
    replacements, append, managed = [], [], dict(prior)
    folded = {key.casefold(): key for key in existing}
    for key, ref in desired.items():
        previous = existing.get(key)
        if previous is None and key.casefold() not in folded:
            added.append(ref)
            append.append(ref['entry'])
            managed[key] = entry_digest(ref['entry'])
        elif previous is not None and previous['entry'].strip() == ref['entry']:
            unchanged.append(ref)
            if key in prior:
                managed[key] = entry_digest(ref['entry'])
        elif previous is not None and prior.get(key) == entry_digest(previous['entry']):
            updated.append(ref)
            replacements.append((previous['start'], previous['end'], ref['entry']))
            managed[key] = entry_digest(ref['entry'])
        else:
            conflicts.append({**ref, 'reason': 'This citation key already has a manually created or edited entry. It has not been changed.'})
    merged = source
    for start, end, value in sorted(replacements, reverse=True):
        merged = merged[:start] + value + merged[end:]
    if append:
        merged += ('\n\n' if merged else '') + '\n\n'.join(append) + '\n'
    retained = [key for key in existing if key not in desired]
    connection_needed, document_change = bibliography_connection(project, filename, connect_document)
    token_content = [project['id'], project['revision'], collection['id'], collection['revision'], filename,
                     source, refs['references'], refs['missing'], prior, connect_document, document_change]
    token = hashlib.sha256(json.dumps(token_content, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return {'project_id': project['id'], 'project_revision': project['revision'], 'collection_id': collection['id'],
            'collection_revision': collection['revision'], 'filename': filename, 'added': added, 'updated': updated,
            'unchanged': unchanged, 'retained': retained, 'conflicts': conflicts, 'missing': refs['missing'],
            'bibtex': merged, 'preview_token': token, 'connect_document': connect_document,
            'connection_needed': connection_needed, 'document_change': document_change}, managed


class WritingStore:
    def __init__(self, directory):
        from .workspace_storage import workspace_database
        self.directory = Path(directory)
        self.path = workspace_database(self.directory, '.writing.sqlite3')

    def connect(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        os.chmod(self.path, 0o600)
        db.execute('CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, kind TEXT, data TEXT, revision INTEGER, updated REAL)')
        db.execute('CREATE INDEX IF NOT EXISTS documents_updated ON documents(updated DESC)')
        db.execute('CREATE INDEX IF NOT EXISTS documents_revision ON documents(id, kind, revision)')
        db.execute('CREATE INDEX IF NOT EXISTS documents_listing ON documents(updated DESC, id, kind, revision)')
        db.execute('CREATE TABLE IF NOT EXISTS writing_summaries (document_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, data TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS writing_bases (document_id TEXT, revision INTEGER, digest TEXT NOT NULL, PRIMARY KEY(document_id, revision))')
        db.execute('CREATE TABLE IF NOT EXISTS writing_citation_keys (paper_id TEXT PRIMARY KEY, citation_key TEXT NOT NULL COLLATE NOCASE UNIQUE)')
        db.execute("CREATE INDEX IF NOT EXISTS writing_linked_collection ON documents(json_extract(data, '$.collection')) WHERE kind='projects'")
        return db

    def _automatic_bibliography(self, db, key, data, catalogue):
        if not data.get('collection') or data.get('bibliography_auto', True) is False:
            data.pop('bibliography_status', None)
            return
        try:
            row = db.execute("SELECT data,revision FROM documents WHERE id=? AND kind='collections'", (data['collection'],)).fetchone()
            if not row:
                raise ValueError('The linked personal library is unavailable. Choose a library in References.')
            collection = {**json.loads(row[0]), 'id': data['collection'], 'revision': row[1]}
            target = data.get('bibliography_target')
            sources, _ = document_tex_sources(data)
            cited = any(re.search(r'(?<!\\)\\(?:[Cc]ite(?:[pt]|alt|alp|author|yearpar|year|num|alias)?|autocite|parencite|textcite|footcite|nocite)\b', source)
                        for _, _, source in sources)
            connected = set()
            for _, _, source in sources:
                for match in re.finditer(r'\\(?:bibliography|addbibresource)(?:\[[^\]]*\])?\s*\{([^{}]+)\}', source):
                    for name in match[1].split(','):
                        name = name.strip().removeprefix('./')
                        connected.add(name if name.lower().endswith('.bib') else name + '.bib')
            if not target:
                managed = list(data.get('bibliography_sync', {}))
                choices = connected or set(managed)
                if len(choices) > 1:
                    raise ValueError('This project uses several bibliography files. Choose the automatic sync target in References.')
                target = next(iter(choices), 'references.bib')
            catalogue = list(catalogue)
            keys = self.citation_keys(collection, catalogue, db)
            preview, managed = bibliography_plan({**data, 'id': key, 'revision': 0}, collection, catalogue, target, True, keys)
            has_entries = bool(bibtex_entries(preview['bibtex']))
            files = {**data['files'], target: preview['bibtex']}
            if has_entries and cited and preview['document_change']:
                change = preview['document_change']
                files[change['filename']] = change['after']
            validate_files(files, data['main'])
            status = {'state': 'synced', 'filename': target, 'message': 'Library bibliography is up to date.',
                      'conflicts': [r['key'] for r in preview['conflicts']], 'missing_count': len(preview['missing'])}
            if preview['conflicts']:
                status.update(state='needs_review', message='Some bibliography entries have manual changes. They were kept; review them in References.')
            elif preview['missing']:
                status.update(state='needs_review', message='Some linked papers are missing from the corpus. Existing entries were kept.')
            elif has_entries and target not in connected and not preview['document_change']:
                status.update(state='needs_review', message='The bibliography cannot be connected safely. Check the main document’s bibliography setup.')
            data.update(files=files, bibliography_target=target, bibliography_status=status)
            data.setdefault('bibliography_sync', {})[target] = {'entries': managed, 'collection': collection['id'],
                                                               'collection_revision': collection['revision']}
        except ValueError as exc:
            data['bibliography_status'] = {'state': 'needs_review', 'message': str(exc), 'conflicts': [], 'missing_count': 0}

    def _write_refreshed_project(self, db, key, before, revision, data):
        if data == before:
            return revision
        encoded = json.dumps(data)
        used = db.execute('SELECT COALESCE(SUM(length(data)),0) FROM documents WHERE id != ?', (key,)).fetchone()[0]
        if used + len(encoded) > 100_000_000:
            raise ValueError('Account writing storage limit reached (100 MB).')
        self._remember_basis(db, key, revision, before)
        db.execute('UPDATE documents SET data=?,revision=?,updated=? WHERE id=?', (encoded, revision + 1, time.time(), key))
        self._remember_summary(db, key, revision + 1, data)
        self._remember_basis(db, key, revision + 1, data)
        return revision + 1

    def refresh_bibliography(self, key, catalogue):
        key = identifier(key)
        db = self.connect()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT data,revision FROM documents WHERE id=? AND kind='projects'", (key,)).fetchone()
                if not row:
                    raise KeyError('Project not found.')
                data, revision = json.loads(row[0]), row[1]
                before = json.loads(row[0])
                self._automatic_bibliography(db, key, data, catalogue)
                revision = self._write_refreshed_project(db, key, before, revision, data)
                return {**data, 'id': key, 'revision': revision}
        finally:
            db.close()

    def _refresh_collection_projects(self, db, collection, catalogue):
        keys = [row[0] for row in db.execute("SELECT id FROM documents WHERE kind='projects' AND json_extract(data, '$.collection')=?", (collection,))]
        if not keys:
            return
        catalogue = list(catalogue)
        for key in keys:
            raw, revision = db.execute('SELECT data,revision FROM documents WHERE id=?', (key,)).fetchone()
            data, before = json.loads(raw), json.loads(raw)
            self._automatic_bibliography(db, key, data, catalogue)
            self._write_refreshed_project(db, key, before, revision, data)

    def citation_keys(self, collection, catalogue, db=None):
        owns_db = db is None
        db = db or self.connect()
        try:
            if owns_db:
                db.execute('BEGIN IMMEDIATE')
            assigned = dict(db.execute('SELECT paper_id,citation_key FROM writing_citation_keys'))
            used = {key.casefold() for key in assigned.values()}
            papers = {p['paper_id']: p for p in catalogue}
            requested = set(collection['papers']) & papers.keys()
            for paper_id in sorted(requested - assigned.keys()):
                legacy = 'paper' + hashlib.sha256(str(paper_id).encode()).hexdigest()[:12]
                # Preserve keys already used by older Quill projects. Never rewrite citations.
                legacy_used = db.execute("SELECT 1 FROM documents WHERE kind='projects' AND instr(data, ?) > 0 LIMIT 1", (legacy,)).fetchone()
                base = legacy if legacy_used else citation_key(papers[paper_id])
                key, suffix = base, 0
                while key.casefold() in used:
                    suffix += 1
                    key = base + _key_suffix(suffix)
                db.execute('INSERT INTO writing_citation_keys VALUES(?,?)', (paper_id, key))
                assigned[paper_id] = key
                used.add(key.casefold())
            if owns_db:
                db.commit()
            return {pid: assigned[pid] for pid in requested}
        finally:
            if owns_db:
                db.close()

    @staticmethod
    def _remember_summary(db, key, revision, data):
        summary = {'name': data['name'], 'count': len(data.get('papers', data.get('files', {}))),
                   'collection': data.get('collection', ''), 'template': data.get('template', '')}
        db.execute('INSERT INTO writing_summaries VALUES(?,?,?) ON CONFLICT(document_id) '
                   'DO UPDATE SET revision=excluded.revision, data=excluded.data',
                   (key, revision, json.dumps(summary)))

    @staticmethod
    def _remember_basis(db, key, revision, data):
        db.execute('INSERT OR REPLACE INTO writing_bases VALUES(?,?,?)', (key, revision, project_basis_digest(data)))
        db.execute('DELETE FROM writing_bases WHERE document_id=? AND revision NOT IN '
                   '(SELECT revision FROM writing_bases WHERE document_id=? ORDER BY revision DESC LIMIT 64)', (key, key))

    def revision(self, key):
        db = self.connect()
        try:
            row = db.execute("SELECT revision FROM documents INDEXED BY documents_revision WHERE id=? AND kind='projects'", (identifier(key),)).fetchone()
            if not row:
                raise KeyError('Project not found.')
            return row[0]
        finally:
            db.close()

    def _summaries(self, key=None):
        db = self.connect()
        try:
            query = ('SELECT d.id,d.kind,s.data,d.revision,d.updated '
                     'FROM documents AS d ' + ('INDEXED BY documents_listing ' if key is None else '') +
                     'LEFT JOIN writing_summaries AS s ON s.document_id=d.id AND s.revision=d.revision ')
            parameters = ()
            if key is not None:
                query += "WHERE d.id=? AND d.kind='projects' "
                parameters = (identifier(key),)
            query += 'ORDER BY d.updated DESC'
            rows = db.execute(query, parameters).fetchall()
            if any(row[2] is None for row in rows):
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    for document_id, _, raw, _, _ in db.execute(query, parameters).fetchall():
                        if raw is None:
                            data, revision = db.execute('SELECT data,revision FROM documents WHERE id=?', (document_id,)).fetchone()
                            self._remember_summary(db, document_id, revision, json.loads(data))
                    rows = db.execute(query, parameters).fetchall()
            result = []
            for key, kind, raw, revision, updated in rows:
                result.append((kind, {**json.loads(raw), 'id': key, 'revision': revision, 'updated': updated}))
            return result
        finally:
            db.close()

    def snapshot(self):
        result = {'projects': [], 'collections': [], 'templates': TEMPLATES}
        for kind, item in self._summaries():
            result[kind].append(item)
        return result

    def summary(self, key):
        rows = self._summaries(key)
        if not rows:
            raise KeyError('Project not found.')
        return rows[0][1]

    def get(self, key, kind='projects'):
        db = self.connect()
        try:
            row = db.execute('SELECT data, revision FROM documents WHERE id=? AND kind=?', (identifier(key), kind)).fetchone()
            if not row:
                raise KeyError('Project or collection not found.')
            return {**json.loads(row[0]), 'id': key, 'revision': row[1]}
        finally:
            db.close()

    def change(self, payload, catalogue, *, from_document_sync=False):
        action = payload.get('action')
        if action == 'sync_bibliography':
            return self.sync_bibliography(payload, catalogue)
        allowed = {'create_project', 'save_project', 'delete_project', 'create_collection', 'collection_paper', 'rename_collection', 'configure_bibliography'}
        if not isinstance(action, str) or action not in allowed:
            raise ValueError('Unknown writing action.')
        if action == 'collection_paper':
            catalogue = list(catalogue)
        kind = 'collections' if action in {'create_collection', 'collection_paper', 'rename_collection'} else 'projects'
        create = action.startswith('create_')
        key = uuid.uuid4().hex if create else identifier(payload.get('id'))
        original = None
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
                        automatic = payload.get('bibliography_auto', True)
                        if type(automatic) is not bool:
                            raise ValueError('Invalid automatic bibliography option.')
                        data['bibliography_auto'] = automatic
                        if payload.get('zip') and payload.get('template'):
                            raise ValueError('Choose a template or a ZIP, not both.')
                        if from_document_sync:
                            files, main = payload.get('files'), payload.get('main')
                            validate_files(files, main)
                        elif payload.get('template'):
                            files, main = template_files(payload['template'])
                            data['template'] = payload['template']
                        else:
                            files, main = unpack_project(payload['zip']) if payload.get('zip') else ({'main.tex': STARTER, 'references.bib': ''}, 'main.tex')
                        collection = payload.get('collection', '')
                        if collection:
                            identifier(collection)
                            if not db.execute("SELECT 1 FROM documents WHERE id=? AND kind='collections'", (collection,)).fetchone():
                                raise ValueError('Personal library not found in your account.')
                        data.update(files=files, main=main, collection=collection)
                    else:
                        data['papers'] = []
                else:
                    row = db.execute('SELECT data, revision FROM documents WHERE id=? AND kind=?', (key, kind)).fetchone()
                    if not row:
                        raise KeyError('Project or collection not found.')
                    original = row[0]
                    data, revision = json.loads(original), row[1]
                    concurrent_save = action == 'save_project' and 'base' in payload
                    if type(payload.get('revision')) is not int or (payload['revision'] != revision and not concurrent_save):
                        raise WritingConflict('Changed in another window. Export your draft before reloading.')
                    if action == 'delete_project':
                        if payload.get('confirm_name') != data['name'] or payload.get('confirm_delete') is not True:
                            raise ValueError('Confirm the manuscript name and deletion.')
                        db.execute('DELETE FROM writing_bases WHERE document_id=?', (key,))
                        db.execute('DELETE FROM writing_summaries WHERE document_id=?', (key,))
                        db.execute("DELETE FROM documents WHERE id=? AND kind='projects'", (key,))
                        return {'id': key, 'deleted': True}
                    if action == 'configure_bibliography':
                        automatic = payload.get('enabled')
                        if type(automatic) is not bool:
                            raise ValueError('Invalid automatic bibliography option.')
                        target = file_name(payload.get('filename', 'references.bib'))
                        if not target.lower().endswith('.bib'):
                            raise ValueError('Choose a .bib file for automatic synchronisation.')
                        data.update(bibliography_auto=automatic, bibliography_target=target)
                    elif action == 'save_project':
                        files, main = payload.get('files'), payload.get('main')
                        validate_files(files, main)
                        if from_document_sync:
                            name = payload.get('name')
                            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
                                raise ValueError('Invalid synced manuscript name.')
                            data['name'] = name.strip()
                        collection = payload.get('collection', '')
                        if concurrent_save:
                            base = payload['base']
                            if not isinstance(base, dict) or type(base.get('revision')) is not int or base['revision'] != payload['revision']:
                                raise ValueError('Invalid synchronisation basis.')
                            validate_files(base.get('files'), base.get('main'))
                            if base['revision'] == revision:
                                expected = project_basis_digest(data)
                            else:
                                remembered = db.execute('SELECT digest FROM writing_bases WHERE document_id=? AND revision=?',
                                                        (key, base['revision'])).fetchone()
                                expected = remembered[0] if remembered else None
                            if expected != project_basis_digest(base):
                                raise WritingConflict('The saved basis is outdated or does not match this manuscript. Keep your draft and reload the saved version.')
                            try:
                                files = merge_files(base['files'], files, data['files'], TEXT_TYPES)
                                main = merge_value(base['main'], main, data['main'])
                                collection = merge_value(base.get('collection', ''), collection, data.get('collection', ''))
                            except MergeConflict as exc:
                                raise WritingConflict(f'Concurrent changes need review. {exc} Your draft has not overwritten the saved manuscript.') from exc
                            validate_files(files, main)
                        if collection:
                            identifier(collection)
                            if not db.execute("SELECT 1 FROM documents WHERE id=? AND kind='collections'", (collection,)).fetchone():
                                raise ValueError('Collection not found in your account.')
                        data.update(files=files, main=main, collection=collection)
                    elif action == 'rename_collection':
                        name = payload.get('name')
                        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
                            raise ValueError('Choose a name of 1–120 characters.')
                        data['name'] = name.strip()
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
                if kind == 'projects':
                    self._automatic_bibliography(db, key, data, catalogue)
                encoded = json.dumps(data)
                if original is not None and (encoded == original or data == json.loads(original)):
                    if action == 'collection_paper':
                        self._refresh_collection_projects(db, key, catalogue)
                    return {**data, 'id': key, 'revision': revision}
                used = db.execute('SELECT COALESCE(SUM(length(data)),0) FROM documents WHERE id != ?', (key,)).fetchone()[0]
                if used + len(encoded) > 100_000_000:
                    raise ValueError('Account writing storage limit reached (100 MB).')
                if kind == 'projects' and original is not None:
                    self._remember_basis(db, key, revision, json.loads(original))
                db.execute('INSERT INTO documents VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data, revision=excluded.revision, updated=excluded.updated',
                           (key, kind, encoded, revision + 1, time.time()))
                self._remember_summary(db, key, revision + 1, data)
                if kind == 'projects':
                    self._remember_basis(db, key, revision + 1, data)
                elif action == 'collection_paper':
                    self._refresh_collection_projects(db, key, catalogue)
            return {**data, 'id': key, 'revision': revision + 1}
        finally:
            db.close()

    def collection_bib(self, key, catalogue):
        return self.collection_references(key, catalogue)['bibtex']

    def collection_references(self, key, catalogue):
        collection = self.get(key, 'collections')
        catalogue = list(catalogue)
        return collection_reference_data(collection, catalogue, self.citation_keys(collection, catalogue))

    def bibliography_preview(self, key, catalogue, filename='references.bib', connect_document=False):
        project = self.get(key)
        if not project.get('collection'):
            raise ValueError('Link a personal library to this project first.')
        collection = self.get(project['collection'], 'collections')
        catalogue = list(catalogue)
        return bibliography_plan(project, collection, catalogue, filename, connect_document,
                                 self.citation_keys(collection, catalogue))[0]

    def sync_bibliography(self, payload, catalogue):
        key = identifier(payload.get('id'))
        db = self.connect()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT data, revision FROM documents WHERE id=? AND kind='projects'", (key,)).fetchone()
                if not row:
                    raise KeyError('Project not found.')
                data, revision = json.loads(row[0]), row[1]
                if type(payload.get('revision')) is not int or payload['revision'] != revision:
                    raise WritingConflict('The project changed. Preview bibliography changes again.')
                if not data.get('collection'):
                    raise ValueError('Link a personal library to this project first.')
                row = db.execute("SELECT data, revision FROM documents WHERE id=? AND kind='collections'", (data['collection'],)).fetchone()
                if not row:
                    raise KeyError('Personal library not found.')
                collection = {**json.loads(row[0]), 'id': data['collection'], 'revision': row[1]}
                if type(payload.get('collection_revision')) is not int or payload['collection_revision'] != collection['revision']:
                    raise WritingConflict('The personal library changed. Preview bibliography changes again.')
                filename = payload.get('filename', 'references.bib')
                catalogue = list(catalogue)
                keys = self.citation_keys(collection, catalogue, db)
                preview, managed = bibliography_plan({**data, 'id': key, 'revision': revision}, collection, catalogue,
                                                    filename, payload.get('connect_document', False), keys)
                if payload.get('preview_token') != preview['preview_token']:
                    raise WritingConflict('Bibliographic metadata or sync options changed. Preview changes again.')
                if preview['conflicts']:
                    raise WritingConflict('Some citation keys have manual changes. Resolve the listed conflicts before synchronising.')
                self._remember_basis(db, key, revision, data)
                data['files'][filename] = preview['bibtex']
                if preview['document_change']:
                    edit = preview['document_change']
                    data['files'][edit['filename']] = edit['after']
                data.setdefault('bibliography_sync', {})[filename] = {'entries': managed, 'collection': collection['id'],
                                                                    'collection_revision': collection['revision']}
                data['bibliography_target'] = filename
                self._automatic_bibliography(db, key, data, catalogue)
                validate_files(data['files'], data['main'])
                encoded = json.dumps(data)
                used = db.execute('SELECT COALESCE(SUM(length(data)),0) FROM documents WHERE id != ?', (key,)).fetchone()[0]
                if used + len(encoded) > 100_000_000:
                    raise ValueError('Account writing storage limit reached (100 MB).')
                db.execute('UPDATE documents SET data=?, revision=?, updated=? WHERE id=?', (encoded, revision + 1, time.time(), key))
                self._remember_summary(db, key, revision + 1, data)
                self._remember_basis(db, key, revision + 1, data)
            return {**data, 'id': key, 'revision': revision + 1}
        finally:
            db.close()

    def export(self, key, expected_revision=None):
        project = self.get(key)
        if expected_revision is not None and project['revision'] != expected_revision:
            raise WritingConflict('The project changed before export. Save again before backing up.')
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, content in project['files'].items():
                archive.writestr(name, decode_file(name, content))
        return stream.getvalue()
