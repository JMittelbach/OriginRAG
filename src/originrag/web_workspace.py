import base64
from contextlib import contextmanager, nullcontext
from http import HTTPStatus
import json
import sqlite3
import time
from urllib.parse import parse_qs

from .writing import WritingConflict, WritingStore
from .writing_compile import CompilerBusy, compiler_status
from .writing_builds import compile_saved_project
from .writing_collaboration import LOCAL_COLLABORATION, ProjectAccess
from . import writing_language, writing_chat, writing_chat_sources
from .web_appearance import with_appearance
from .web_navigation import with_navigation

PAGES = {'/workspace', '/bookshelf', '/library', '/write'}


class DeferredCatalogue:
    def __init__(self, load):
        self.load = load
        self.value = None

    def __iter__(self):
        if self.value is None:
            self.value = self.load()
        return iter(self.value)

HTML = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Origin</title><link rel="stylesheet" href="/assets/workspace/workspace.css"><link rel="stylesheet" href="/assets/workspace/writer.css"><link rel="stylesheet" href="/assets/workspace/writer-layout.css"><link rel="stylesheet" href="/assets/workspace/writing-library.css"><link rel="stylesheet" href="/assets/workspace/writing-language.css"><style></style><link rel="stylesheet" href="/assets/workspace/workspace-enhancements.css"></head>
<body>
<div id="notice" role="status" aria-live="polite"></div><main id="content"></main>
<dialog id="createDialog"><form id="createForm"><h2 id="createTitle">New project</h2><label>Name<input id="newName" maxlength="120" required autocomplete="off"></label><p id="createError" role="alert"></p><div class="actions"><button type="button" id="cancelCreate">Cancel</button><button class="primary">Create</button></div></form></dialog>
<script type="module" src="/assets/workspace/workspace.mjs"></script></body></html>'''
HTML = with_navigation(with_appearance(HTML.replace('</head>',
    '<link rel="stylesheet" href="/assets/workspace/library-polish.css"><link rel="stylesheet" href="/assets/workspace/reading-history.css"><link rel="stylesheet" href="/assets/workspace/writing-chat.css"></head>', 1)))


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


def stream_language_review(handler, store, collaboration, user, payload, selected, model_manager, project_request=None):
    handler.send_response(HTTPStatus.OK)
    handler.send_security_headers()
    handler.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Accel-Buffering', 'no')
    handler.send_header('Connection', 'close')
    handler.end_headers()
    handler.close_connection = True

    def current():
        with project_access(store, collaboration, user, payload['id']) as access:
            if access.store.revision(payload['id']) != payload['revision']:
                raise WritingConflict('The manuscript changed during the check. Select the passage again.')

    last_access_check = 0.0
    def emit(event):
        nonlocal last_access_check
        if event['type'] in {'preview', 'edit_preview', 'done'} or (
                event['type'] in {'answer_delta', 'edit_delta'} and time.monotonic() - last_access_check >= .5):
            current()
            last_access_check = time.monotonic()
        handler.wfile.write((json.dumps(event, ensure_ascii=False) + '\n').encode())
        handler.wfile.flush()

    try:
        emit({'type': 'started'})
        override = None
        if project_request is not None and payload.get('model'):
            override = {'provider': 'ollama', 'model': payload['model'], 'mode': 'fast'}
        elif project_request is not None and payload.get('auto_context') is True and model_manager:
            state = model_manager().snapshot(False)
            active = state.get('effective_selection')
            if active and active['provider'] != 'ollama':
                raise writing_language.LanguageUnavailable('Choose an installed Ollama model in Models to use the project assistant.')
            if active:
                override = {'provider': 'ollama', 'model': active['model'], 'mode': 'fast'}
            else:
                installed = [row for row in state['models'] if row['provider'] == 'ollama' and row['installed']]
                if len(installed) != 1:
                    raise writing_language.LanguageUnavailable('Choose an installed model in Models, then try again.')
                override = {'provider': 'ollama', 'model': installed[0]['model'], 'mode': 'fast'}
        session = (model_manager().session(override=override) if project_request is not None
                   else model_manager().session()) if model_manager else nullcontext()
        with session:
            result = (writing_chat.chat(project_request, emit) if project_request is not None
                      else writing_language.check_language_stream(*selected, emit))
        emit({'type': 'done', **result, 'revision': payload['revision'], 'file': payload.get('file')})
    except (KeyError, PermissionError, ValueError, OSError) as exc:
        message = str(exc) if isinstance(exc, (writing_language.LanguageUnavailable, WritingConflict)) else 'The check could not be completed. Nothing has been changed.'
        try:
            emit({'type': 'error', 'error': message})
        except OSError:
            pass


def handle_writing(handler, path, query, owner_directory, catalogue, *, post=False, collaboration=None, user=None, language_lock=None, chat_slots=None, model_manager=None, chunks_dir=None):
    if callable(catalogue):
        catalogue = DeferredCatalogue(catalogue)
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
                expected = int(params['revision'][0]) if 'revision' in params else None
                with project_access(store, collaboration, user, key) as access:
                    access.store.refresh_bibliography(key, catalogue)
                    data = access.store.export(key, expected_revision=expected)
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
            elif 'chat_library' in params:
                key = params['chat_library'][0]
                scope = params.get('scope', ['project'])[0]
                if scope not in ('project', 'corpus'):
                    raise ValueError('Choose the project library or whole corpus.')
                with project_access(store, collaboration, user, key) as access:
                    project = access.store.get(key)
                    allowed = None
                    if scope == 'project':
                        allowed = set(access.store.get(project['collection'], 'collections')['papers']) if project.get('collection') else set()
                    result = writing_chat_sources.search(catalogue, params.get('query', [''])[0], allowed=allowed)
                handler.respond_json({**result, 'scope': scope})
            elif 'chat_context' in params:
                key = params['chat_context'][0]
                with project_access(store, collaboration, user, key) as access:
                    result = writing_chat.context_manifest(access.store.get(key))
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
                        data = access.public(access.store.refresh_bibliography(key, catalogue))
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
            if payload.get('action') == 'project_chat':
                slots = chat_slots if chat_slots is not None else language_lock
                if not writing_language.enabled() or slots is None:
                    raise writing_language.LanguageUnavailable('Writing assistance is disabled on this host.')
                with project_access(store, collaboration, user, payload.get('id')) as access:
                    project = access.store.get(payload['id'])
                    question = payload.get('message')
                    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 4000:
                        raise ValueError('Ask a question using between 1 and 4,000 characters.')
                    ids = writing_chat_sources.paper_ids(payload)
                    sources = writing_chat_sources.prepare_sources(catalogue, ids, question, chunks_dir=chunks_dir) if ids else []
                    prepared = writing_chat.prepare_request(project, payload, library_sources=sources)
                if payload.get('model') is not None and (
                        not isinstance(payload['model'], str) or len(payload['model']) > 200):
                    raise ValueError('Choose an installed model for project chat.')
                if not slots.acquire(blocking=False):
                    raise WritingConflict('The model is busy with another request. Please try again shortly.')
                try:
                    stream_language_review(handler, store, collaboration, user, payload, None, model_manager, prepared)
                finally:
                    slots.release()
            elif payload.get('action') == 'language_check':
                if not writing_language.enabled() or language_lock is None:
                    raise writing_language.LanguageUnavailable('Language checking is not enabled on this host.')
                key = payload.get('id')
                with project_access(store, collaboration, user, key) as access:
                    selected = writing_language.selection(access.store.get(key), payload)
                if not language_lock.acquire(blocking=False):
                    raise WritingConflict('The model is busy with another request. Please try again shortly.')
                try:
                    if payload.get('stream') is True:
                        stream_language_review(handler, store, collaboration, user, payload, selected, model_manager)
                        return
                    with model_manager().session() if model_manager else nullcontext():
                        result = writing_language.check_language(*selected)
                finally:
                    language_lock.release()
                with project_access(store, collaboration, user, key) as access:
                    if access.store.revision(key) != payload['revision']:
                        raise WritingConflict('The manuscript changed during the check. Select the passage again.')
                handler.respond_json({**result, 'revision': payload['revision'], 'file': payload['file']})
            elif payload.get('action') == 'refresh_bibliography':
                key = payload.get('id')
                with project_access(store, collaboration, user, key) as access:
                    project = access.store.refresh_bibliography(key, catalogue)
                    result = {'id': key, 'revision': project['revision'], 'collaboration': access.info,
                              'bibliography_status': project.get('bibliography_status')}
                handler.respond_json(result)
            elif payload.get('action') == 'compile':
                key = payload.get('id')
                with project_access(store, collaboration, user, key) as access:
                    project = access.store.refresh_bibliography(key, catalogue)
                    if project.get('bibliography_status', {}).get('state') == 'needs_review':
                        raise WritingConflict(project['bibliography_status']['message'])
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
            elif payload.get('action') in {'save_project', 'sync_bibliography', 'delete_project', 'configure_bibliography'}:
                key = payload.get('id')
                with project_access(store, collaboration, user, key,
                                    owner_only=payload['action'] in {'sync_bibliography', 'delete_project', 'configure_bibliography'}) as access:
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
