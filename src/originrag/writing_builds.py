from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from .writing import validate_files
from .writing_compile import compile_project, select_compiler_backend

_CACHE = OrderedDict()
_LOCK = threading.Lock()
_MAX_BYTES = 64_000_000
_MAX_ITEMS = 16
_TTL = 1800


def _path_identity(value):
    if not value:
        return None
    path = Path(value).resolve()
    try:
        stat = path.stat()
        return [str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    except OSError:
        return [str(path), 'unavailable']


def _calendar_identity():
    current = datetime.now().astimezone()
    return [current.date().isoformat(), current.tzname(), current.strftime('%z'), os.environ.get('TZ', '')]


def compiler_fingerprint():
    configuration = {key: os.environ.get(key, '') for key in (
        'ORIGINWRITE_BWRAP', 'ORIGINWRITE_TECTONIC', 'ORIGINWRITE_TECTONIC_CACHE',
        'ORIGINWRITE_TEX_IMAGE', 'ORIGINWRITE_TEX_REVISION')}
    backend = select_compiler_backend()
    identity = {'configuration': configuration, 'backend': backend, 'calendar': _calendar_identity()}
    if backend:
        identity['files'] = {name: _path_identity(backend.get(name)) for name in ('executable', 'sandbox', 'cache')}
        if backend.get('cache'):
            revision = Path(backend['cache']) / '.originwrite-cache-revision'
            identity['cache_revision'] = _path_identity(revision)
            try:
                with revision.open('rb') as handle:
                    identity['cache_revision_digest'] = hashlib.sha256(handle.read(4096)).hexdigest()
            except OSError:
                identity['cache_revision_digest'] = None
    return identity


def compile_saved_project(project, owner_directory, *, force=False):
    """Reuse successful, unchanged builds only within the same account and project."""
    started = time.monotonic()
    if not isinstance(force, bool):
        raise ValueError('The force option must be true or false.')
    validate_files(project['files'], project['main'])
    configuration = compiler_fingerprint()
    content = json.dumps({'files': project['files'], 'main': project['main'], 'compiler': configuration}, sort_keys=True).encode()
    key = (str(Path(owner_directory).resolve()), project.get('id', ''), hashlib.sha256(content).hexdigest())
    with _LOCK:
        for old in list(_CACHE):
            if time.monotonic() - _CACHE[old][0] > _TTL:
                _CACHE.pop(old)
        if force:
            _CACHE.pop(key, None)
        elif key in _CACHE:
            _CACHE.move_to_end(key)
            result = deepcopy(_CACHE[key][1])
            result.update(revision=project['revision'], cached=True, build_seconds=result['seconds'], seconds=round(time.monotonic() - started, 3))
            return result
    result = compile_project(project)
    if result['ok']:
        size = len(result['pdf']) + len(result.get('log', '').encode()) + 100_000
        with _LOCK:
            _CACHE[key] = (time.monotonic(), deepcopy(result), size)
            while len(_CACHE) > _MAX_ITEMS or sum(entry[2] for entry in _CACHE.values()) > _MAX_BYTES:
                _CACHE.popitem(last=False)
    return {**result, 'cached': False}
