from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from .writing import validate_files
from .writing_compile import compile_project

_CACHE = OrderedDict()
_LOCK = threading.Lock()
_MAX_BYTES = 64_000_000
_MAX_ITEMS = 16
_TTL = 1800


def compile_saved_project(project, owner_directory):
    """Reuse successful, unchanged builds only within the same account and project."""
    started = time.monotonic()
    validate_files(project['files'], project['main'])
    configuration = {key: os.environ.get(key, '') for key in (
        'ORIGINWRITE_BWRAP', 'ORIGINWRITE_TECTONIC', 'ORIGINWRITE_TECTONIC_CACHE', 'ORIGINWRITE_TEX_IMAGE')}
    content = json.dumps({'files': project['files'], 'main': project['main'], 'compiler': configuration}, sort_keys=True).encode()
    key = (str(Path(owner_directory).resolve()), project.get('id', ''), hashlib.sha256(content).hexdigest())
    with _LOCK:
        for old in list(_CACHE):
            if time.monotonic() - _CACHE[old][0] > _TTL:
                _CACHE.pop(old)
        if key in _CACHE:
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
