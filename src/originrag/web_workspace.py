import base64
from contextlib import contextmanager
from http import HTTPStatus
import sqlite3
from urllib.parse import parse_qs

from .writing import WritingConflict, WritingStore
from .writing_compile import CompilerBusy, compiler_status
from .writing_builds import compile_saved_project
from .writing_collaboration import LOCAL_COLLABORATION, ProjectAccess
from . import writing_language
from .web_appearance import with_appearance
from .web_navigation import with_navigation

PAGES = {'/workspace', '/bookshelf', '/library', '/write'}

HTML = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Origin</title><link rel="stylesheet" href="/assets/workspace/workspace.css"><link rel="stylesheet" href="/assets/workspace/writer.css"><link rel="stylesheet" href="/assets/workspace/writer-layout.css"><link rel="stylesheet" href="/assets/workspace/writing-library.css"><style></style><link rel="stylesheet" href="/assets/workspace/workspace-enhancements.css"></head>
<body>
<div id="notice" role="status" aria-live="polite"></div><main id="content"></main>
<dialog id="createDialog"><form id="createForm"><h2 id="createTitle">New project</h2><label>Name<input id="newName" maxlength="120" required autocomplete="off"></label><p id="createError" role="alert"></p><div class="actions"><button type="button" id="cancelCreate">Cancel</button><button class="primary">Create</button></div></form></dialog>
<script type="module" src="/assets/workspace/workspace.mjs"></script></body></html>'''
HTML = with_navigation(with_appearance(HTML))


def binary_response(handler, data, content_type, name):
    handler.send_response(HTTPStatus.OK)
    handler.send_security_headers()
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Disposition', f'attachment; filename="{name}"')
    handler.send_header('Content-Length', str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


@contextmanager
def project_access(store, collaboration, user, key, *, owner_only=False):
    if collaboration is not None:
        with collaboration.access(user, key, owner_only=owner_only) as access:
            yield access
    else:
        yield ProjectAccess(store, dict(LOCAL_COLLABORATION))


def handle_writing(handler, path, query, owner_directory, catalogue, *, post=False, collaboration=None, user=None, language_lock=None):
    store = WritingStore(owner_directory)
    try:
        if path in {'/api/writing/collaboration', '/api/writing/collaborators'}:
            if collaboration is None or user is None:
                handler.respond_json({'error': 'not found'}, status=HTTPStatus.NOT_FOUND)
                return
            if post:
                if path != '/api/writing/collaboration':
                    raise KeyError('Route not found.')
                handler.respond_json(collaboration.change(user, handler.read_json_body()))
            else:
                params = parse_qs(query)
                field = 'query' if path.endswith('/collaborators') else 'id'
                if set(params) != {field} or len(params[field]) != 1:
                    raise ValueError('Invalid collaboration request.')
                handler.respond_json(collaboration.search(user, params[field][0]) if field == 'query'
                                     else collaboration.members(user, params[field][0]))
            return
        if not post:
            params = parse_qs(query)
            if path == '/api/writing/export':
                key = params.get('id', [''])[0]
                with project_access(store, collaboration, user, key) as access:
                    data = access.store.export(key)
                binary_response(handler, data, 'application/zip', 'manuscript.zip')
            elif path == '/api/writing/bibliography':
                handler.respond_json({'bibtex': store.collection_bib(params.get('id', [''])[0], catalogue)})
            elif 'collection_refs' in params:
                handler.respond_json(store.collection_references(params['collection_refs'][0], catalogue))
            elif 'bib_preview' in params:
                key = params['bib_preview'][0]
                with project_access(store, collaboration, user, key, owner_only=True) as access:
                    result = access.store.bibliography_preview(key, catalogue,
                        filename=params.get('filename', ['references.bib'])[0],
                        connect_document=params.get('connect_document', ['0'])[0] == '1')
                handler.respond_json(result)
            elif 'revision' in params:
                key = params['revision'][0]
                with project_access(store, collaboration, user, key) as access:
                    result = {'id': key, 'revision': access.store.revision(key), 'collaboration': access.info}
                handler.respond_json(result)
            elif 'id' in params:
                kind = params.get('kind', ['projects'])[0]
                if kind == 'collections':
                    data = store.get(params['id'][0], kind)
                    ids = set(data['papers'])
                    data['items'] = [p for p in catalogue if p['paper_id'] in ids]
                elif kind == 'projects':
                    key = params['id'][0]
                    with project_access(store, collaboration, user, key) as access:
                        data = access.public(access.store.get(key))
                else:
                    raise ValueError('Invalid document type.')
                handler.respond_json(data)
            else:
                snapshot = collaboration.snapshot(user, store) if collaboration else store.snapshot()
                if not collaboration:
                    for item in snapshot['projects']:
                        item['collaboration'] = dict(LOCAL_COLLABORATION)
                handler.respond_json({**snapshot, 'compiler': compiler_status(), 'language_check': writing_language.enabled()})
        else:
            payload = handler.read_json_body()
            if payload.get('action') == 'language_check':
                if not writing_language.enabled() or language_lock is None:
                    raise writing_language.LanguageUnavailable('Language checking is not enabled on this host.')
                key = payload.get('id')
                with project_access(store, collaboration, user, key) as access:
                    selected = writing_language.selection(access.store.get(key), payload)
                if not language_lock.acquire(blocking=False):
                    raise WritingConflict('The model is busy with another request. Please try again shortly.')
                try:
                    result = writing_language.check_language(*selected)
                finally:
                    language_lock.release()
                with project_access(store, collaboration, user, key) as access:
                    if access.store.revision(key) != payload['revision']:
                        raise WritingConflict('The manuscript changed during the check. Select the passage again.')
                handler.respond_json({**result, 'revision': payload['revision'], 'file': payload['file']})
            elif payload.get('action') == 'compile':
                key = payload.get('id')
                with project_access(store, collaboration, user, key) as access:
                    project = access.store.get(key)
                    if payload.get('revision') != project['revision']:
                        raise WritingConflict('The project changed before compilation. Save or reload first.')
                    if not isinstance(payload.get('force', False), bool):
                        raise ValueError('Invalid compile option.')
                    compile_directory = access.store.directory
                result = compile_saved_project(project, compile_directory, force=payload.get('force', False))
                with project_access(store, collaboration, user, key):
                    pass
                result['pdf'] = base64.b64encode(result['pdf']).decode('ascii')
                handler.respond_json(result)
            elif payload.get('action') in {'save_project', 'sync_bibliography', 'delete_project'}:
                key = payload.get('id')
                with project_access(store, collaboration, user, key,
                                    owner_only=payload['action'] in {'sync_bibliography', 'delete_project'}) as access:
                    if access.info['role'] == 'editor':
                        if payload.get('collection'):
                            raise PermissionError('Only the owner can link a personal library to this project.')
                        current = access.store.get(key)
                        payload['collection'] = current.get('collection', '')
                        if isinstance(payload.get('base'), dict):
                            payload['base'] = {**payload['base'], 'collection': current.get('collection', '')}
                    data = access.public(access.store.change(payload, catalogue))
                handler.respond_json(data)
            else:
                data = store.change(payload, catalogue)
                if payload.get('action') == 'create_project':
                    with project_access(store, collaboration, user, data['id']) as access:
                        data = access.public(data)
                handler.respond_json(data)
    except writing_language.LanguageUnavailable as exc:
        handler.respond_json({'error': str(exc)}, status=HTTPStatus.SERVICE_UNAVAILABLE)
    except PermissionError as exc:
        handler.respond_json({'error': str(exc)}, status=HTTPStatus.FORBIDDEN)
    except (WritingConflict, CompilerBusy) as exc:
        handler.respond_json({'error': str(exc)}, status=HTTPStatus.CONFLICT)
    except KeyError:
        handler.respond_json({'error': 'Project or collection not found in your account.'}, status=HTTPStatus.NOT_FOUND)
    except ValueError as exc:
        handler.respond_json({'error': str(exc)}, status=HTTPStatus.BAD_REQUEST)
    except (OSError, sqlite3.Error):
        handler.respond_json({'error': 'Writing storage or compiler is unavailable. Your saved source files are unchanged.'}, status=HTTPStatus.SERVICE_UNAVAILABLE)
