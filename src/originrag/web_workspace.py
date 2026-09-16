import base64
from http import HTTPStatus
import sqlite3
from urllib.parse import parse_qs

from .writing import WritingConflict, WritingStore
from .writing_compile import CompilerBusy, compiler_status
from .writing_builds import compile_saved_project

PAGES = {'/workspace', '/bookshelf', '/library', '/write'}

HTML = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Workspace · OriginRAG</title><link rel="stylesheet" href="/assets/workspace/workspace.css"><link rel="stylesheet" href="/assets/workspace/writer.css"><link rel="stylesheet" href="/assets/workspace/writer-layout.css"><link rel="stylesheet" href="/assets/workspace/writing-library.css"></head>
<body><header><a class="brand" href="/workspace">Origin<span> / workspace</span></a><nav aria-label="Your workspace"><a href="/chat" data-feature="rag">OriginRAG</a><a href="/bookshelf">Bookshelf</a><a href="/library">Library</a><a href="/write" data-feature="write">OriginWrite</a></nav><div class="workspace-account"><span id="workspaceUser"></span><button id="workspaceLogout" hidden>Sign out</button></div></header>
<div id="notice" role="status" aria-live="polite"></div><main id="content"></main>
<dialog id="createDialog"><form id="createForm"><h2 id="createTitle">New project</h2><label>Name<input id="newName" maxlength="120" required autocomplete="off"></label><p id="createError" role="alert"></p><div class="actions"><button type="button" id="cancelCreate">Cancel</button><button class="primary">Create</button></div></form></dialog>
<script type="module" src="/assets/workspace/workspace.mjs"></script></body></html>'''


def binary_response(handler, data, content_type, name):
    handler.send_response(HTTPStatus.OK)
    handler.send_security_headers()
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Disposition', f'attachment; filename="{name}"')
    handler.send_header('Content-Length', str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def handle_writing(handler, path, query, owner_directory, catalogue, *, post=False):
    store = WritingStore(owner_directory)
    try:
        if not post:
            params = parse_qs(query)
            if path == '/api/writing/export':
                binary_response(handler, store.export(params.get('id', [''])[0]), 'application/zip', 'manuscript.zip')
            elif path == '/api/writing/bibliography':
                handler.respond_json({'bibtex': store.collection_bib(params.get('id', [''])[0], catalogue)})
            elif 'collection_refs' in params:
                handler.respond_json(store.collection_references(params['collection_refs'][0], catalogue))
            elif 'bib_preview' in params:
                handler.respond_json(store.bibliography_preview(params['bib_preview'][0], catalogue,
                    filename=params.get('filename', ['references.bib'])[0],
                    connect_document=params.get('connect_document', ['0'])[0] == '1'))
            elif 'id' in params:
                kind = params.get('kind', ['projects'])[0]
                data = store.get(params['id'][0], kind)
                if kind == 'collections':
                    ids = set(data['papers'])
                    data['items'] = [p for p in catalogue if p['paper_id'] in ids]
                handler.respond_json(data)
            else:
                handler.respond_json({**store.snapshot(), 'compiler': compiler_status()})
        else:
            payload = handler.read_json_body()
            if payload.get('action') == 'compile':
                project = store.get(payload.get('id'))
                if payload.get('revision') != project['revision']:
                    raise WritingConflict('The project changed before compilation. Save or reload first.')
                if not isinstance(payload.get('force', False), bool):
                    raise ValueError('Invalid compile option.')
                result = compile_saved_project(project, owner_directory, force=payload.get('force', False))
                result['pdf'] = base64.b64encode(result['pdf']).decode('ascii')
                handler.respond_json(result)
            else:
                handler.respond_json(store.change(payload, catalogue))
    except (WritingConflict, CompilerBusy) as exc:
        handler.respond_json({'error': str(exc)}, status=HTTPStatus.CONFLICT)
    except KeyError:
        handler.respond_json({'error': 'Project or collection not found in your account.'}, status=HTTPStatus.NOT_FOUND)
    except ValueError as exc:
        handler.respond_json({'error': str(exc)}, status=HTTPStatus.BAD_REQUEST)
    except (OSError, sqlite3.Error):
        handler.respond_json({'error': 'Writing storage or compiler is unavailable. Your saved source files are unchanged.'}, status=HTTPStatus.SERVICE_UNAVAILABLE)
