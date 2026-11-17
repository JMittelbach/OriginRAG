from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from html.parser import HTMLParser
import json
import re
import threading
import time
from urllib.parse import quote, urlencode, urlsplit
import urllib.request


_ID = re.compile(r'[a-z0-9][a-z0-9._-]{0,79}(?:/[a-z0-9][a-z0-9._-]{0,79})?(?::[a-z0-9][a-z0-9._-]{0,79})?\Z', re.I)
_SLOTS = threading.BoundedSemaphore(2)
_LOCK = threading.Lock()
_CACHE = {}


def invalidate_model(model):
    with _LOCK:
        for query, (_, rows) in list(_CACHE.items()):
            if any(row['model'] == model for row in rows):
                del _CACHE[query]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError('The model catalogue returned an unexpected redirect.')


def _read(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in {'ollama.com', 'registry.ollama.ai'}:
        raise ValueError('Only the public Ollama catalogue is supported.')
    request = urllib.request.Request(url, headers={'User-Agent': 'OriginRAG-model-catalogue/1'})
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=6) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError('The model catalogue response was too large.')
    return data


def model_metadata(model):
    if not isinstance(model, str) or not _ID.fullmatch(model) or model.lower().endswith('-cloud'):
        raise ValueError('Enter a local Ollama model name, such as qwen3.5:9b.')
    name, separator, tag = model.partition(':')
    tag = tag if separator else 'latest'
    namespace = name if '/' in name else 'library/' + name
    raw = _read('https://registry.ollama.ai/v2/' + quote(namespace, safe='/') + '/manifests/' + quote(tag, safe=''))
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise ValueError('The model manifest could not be read.')
    layers = manifest.get('layers', [])
    if (not isinstance(layers, list) or len(layers) > 512
            or not any(isinstance(row, dict) and row.get('mediaType') == 'application/vnd.ollama.image.model'
                       and isinstance(row.get('size'), int) and row['size'] > 0 for row in layers)):
        raise ValueError('No downloadable local model weights were found for this name.')
    if any(not isinstance(row, dict) or not isinstance(row.get('size'), int) or row['size'] < 0 for row in layers):
        raise ValueError('The model size could not be verified.')
    return {'provider': 'ollama', 'model': name + ':' + tag, 'name': name + ':' + tag,
            'bytes': sum(row['size'] for row in layers), 'installed': False, 'loaded': False,
            'downloadable': True, 'catalog_verified': True,
            'catalog_digest': hashlib.sha256(raw).hexdigest(),
            'source_url': 'https://ollama.com/' + (name if '/' in name else 'library/' + name) + ':' + tag,
            'note': 'Public Ollama catalogue. Model quality and runtime compatibility have not been tested here.'}


class _Models(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names = []

    def handle_starttag(self, tag, attrs):
        path = dict(attrs).get('href', '')
        if tag == 'a' and path.startswith('/library/'):
            name = path[len('/library/'):]
            if _ID.fullmatch(name) and '/' not in name and name not in self.names:
                self.names.append(name)


def search_models(query):
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 80 or any(ord(c) < 32 for c in query):
        raise ValueError('Search with 2–80 characters, or enter an exact model:tag.')
    query = query.strip()
    with _LOCK:
        cached = _CACHE.get(query.casefold())
        if cached and time.monotonic() - cached[0] < 600:
            return cached[1]
    if not _SLOTS.acquire(blocking=False):
        raise ValueError('The model catalogue is busy. Try again shortly.')
    try:
        exact = _ID.fullmatch(query) and (':' in query or '/' in query)
        if exact:
            names = [query]
        else:
            parser = _Models()
            parser.feed(_read('https://ollama.com/search?' + urlencode({'q': query})).decode('utf-8'))
            names = parser.names[:4]
        def lookup(name):
            try:
                return model_metadata(name)
            except (OSError, ValueError, TypeError, KeyError):
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(filter(None, pool.map(lookup, names)))
        if exact and not rows:
            raise ValueError('This local model or tag could not be verified. Check the name and try again.')
        with _LOCK:
            if len(_CACHE) >= 64:
                del _CACHE[next(iter(_CACHE))]
            _CACHE[query.casefold()] = (time.monotonic(), rows)
        return rows
    finally:
        _SLOTS.release()
