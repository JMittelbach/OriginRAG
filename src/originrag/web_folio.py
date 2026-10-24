"""Folio page shell and authenticated-owner API adapter."""
from http import HTTPStatus
import re
import sqlite3
from urllib.parse import parse_qs

from .folio import FolioConflict, FolioStore
from .web_appearance import with_appearance
from .web_navigation import with_navigation

HTML = with_navigation(with_appearance('''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Folio · Origin</title>
<style>:root{--text:#e9f0ed}body{margin:0;background:var(--bg);color:var(--text)}</style>
<link rel="stylesheet" href="/assets/folio/folio.css">
<link rel="stylesheet" href="/assets/folio/ribbon.css">
<link rel="stylesheet" href="/assets/folio/document-tools.css"></head>
<body><div id="folioNotice" role="status" aria-live="polite"></div>
<main id="folioApp" aria-label="Folio document editor"></main>
<script type="module" src="/assets/folio/folio.mjs"></script></body></html>'''))


def handle_folio(handler, path, query, owner_directory, *, post=False):
    """Caller authenticates/CSRF-checks and supplies its server-owned directory."""
    try:
        if path not in {'/api/folio', '/api/folio/export'} or (post and path != '/api/folio'):
            raise KeyError('Route not found.')
        store = FolioStore(owner_directory)
        if post:
            handler.respond_json(store.change(handler.read_json_body(reject_duplicate_fields=True)))
            return
        params = parse_qs(query, keep_blank_values=True)
        if any(len(values) != 1 for values in params.values()):
            raise ValueError('Query parameters must not be repeated.')
        if path == '/api/folio/export':
            if set(params) not in ({'id', 'format'}, {'id', 'format', 'revision'}):
                raise ValueError('Choose a document and export format.')
            export_options = {}
            if 'revision' in params:
                value = params['revision'][0]
                if not re.fullmatch(r'[1-9][0-9]*', value):
                    raise ValueError('Export revision must be a positive integer.')
                export_options['revision'] = int(value)
            data, kind, filename = store.export(params['id'][0], params['format'][0], **export_options)
            handler.send_response(HTTPStatus.OK)
            handler.send_security_headers()
            handler.send_header('Content-Type', kind)
            handler.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            handler.send_header('Content-Length', str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
        elif not params:
            handler.respond_json(store.snapshot())
        elif set(params) == {'id'}:
            handler.respond_json(store.get(params['id'][0]))
        else:
            raise ValueError('Invalid document query.')
    except FolioConflict as exc:
        handler.respond_json({'error': str(exc), 'conflict': True}, status=HTTPStatus.CONFLICT)
    except KeyError:
        handler.respond_json({'error': 'Document not found.'}, status=HTTPStatus.NOT_FOUND)
    except (ValueError, TypeError, RecursionError) as exc:
        handler.respond_json({'error': str(exc) if isinstance(exc, ValueError) else 'Invalid document request.'}, status=HTTPStatus.BAD_REQUEST)
    except (sqlite3.Error, OSError):
        handler.respond_json({'error': 'Document storage is unavailable. Your changes have not been saved.'}, status=HTTPStatus.SERVICE_UNAVAILABLE)
