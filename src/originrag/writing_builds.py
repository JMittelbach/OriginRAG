from collections import OrderedDict
from concurrent.futures import Future, TimeoutError
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

from .writing import validate_files
from .writing_compile import compile_project, select_compiler_backend, CompilerBusy, TIME_LIMIT
from .writing_scheduler import QUEUE_WAIT_SECONDS

_CACHE = OrderedDict()
_LOCK = threading.Lock()
_MAX_BYTES = 32 * 1024 * 1024
_MAX_ITEMS = 16
_TTL = 1800
_INFLIGHT = {}
_WAITERS = 0
_MAX_INFLIGHT = 24
_MAX_WAITERS = 16


def _cache_budget():
    value = os.environ.get('ORIGINWRITE_BUILD_CACHE_MIB')
    if value is None:
        return _MAX_BYTES
    try:
        return min(128, max(0, int(value))) * 1024 * 1024
    except ValueError:
        return _MAX_BYTES


def _object_bytes(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_object_bytes(k, seen) + _object_bytes(v, seen) for k, v in value.items())
    elif isinstance(value, (list, tuple, set, frozenset)):
        size += sum(_object_bytes(item, seen) for item in value)
    return size


def _prune_cache(budget):
    now = time.monotonic()
    for key in list(_CACHE):
        if now - _CACHE[key][0] > _TTL:
            _CACHE.pop(key)
    while _CACHE and (len(_CACHE) > _MAX_ITEMS or sys.getsizeof(_CACHE) + sum(entry[2] for entry in _CACHE.values()) > budget):
        _CACHE.popitem(last=False)


def _project_digest(project, configuration):
    digest = hashlib.sha256(b'originwrite-build-v2\0')
    digest.update(json.dumps({'main': project['main'], 'compiler': configuration}, sort_keys=True).encode())
    for name, content in sorted(project['files'].items()):
        digest.update(hashlib.sha256(name.encode()).digest())
        file_digest = hashlib.sha256()
        for offset in range(0, len(content), 65536):
            file_digest.update(content[offset:offset + 65536].encode())
        digest.update(file_digest.digest())
    return digest.hexdigest()


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
    global _WAITERS
    if not isinstance(force, bool):
        raise ValueError('The force option must be true or false.')
    validate_files(project['files'], project['main'])
    configuration = compiler_fingerprint()
    key = (str(Path(owner_directory).resolve()), project.get('id', ''), _project_digest(project, configuration))
    with _LOCK:
        _prune_cache(_cache_budget())
        if force:
            _CACHE.pop(key, None)
        elif key in _CACHE:
            _CACHE.move_to_end(key)
            result = deepcopy(_CACHE[key][1])
            result.update(revision=project['revision'], cached=True, shared_build=False, queue_seconds=0,
                          build_seconds=result['seconds'], seconds=round(time.monotonic() - started, 3))
            return result
        follower = key in _INFLIGHT
        if follower:
            if _WAITERS >= _MAX_WAITERS:
                raise CompilerBusy('Too many requests are waiting for PDFs. Please retry shortly.')
            _WAITERS += 1
            future = _INFLIGHT[key]
        else:
            if len(_INFLIGHT) >= _MAX_INFLIGHT:
                raise CompilerBusy('The compiler queue is full. Please retry shortly.')
            future = _INFLIGHT[key] = Future()
    if follower:
        try:
            result = deepcopy(future.result(timeout=QUEUE_WAIT_SECONDS + TIME_LIMIT + 15))
            return {**result, 'revision': project['revision'], 'cached': False, 'shared_build': True,
                    'wait_seconds': round(time.monotonic() - started, 3)}
        except TimeoutError:
            raise CompilerBusy('The shared build has not finished. Please retry shortly.') from None
        finally:
            with _LOCK:
                _WAITERS -= 1
    try:
        result = compile_project(project)
        _remember_result(key, result)
        future.set_result(result)
        return {**result, 'cached': False, 'shared_build': False}
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        with _LOCK:
            _INFLIGHT.pop(key, None)


def _remember_result(key, result):
    if result['ok']:
        with _LOCK:
            budget = _cache_budget()
            if budget and _object_bytes((key, (0.0, result, 0))) < budget:
                cached = deepcopy(result)
                size = _object_bytes((key, (0.0, cached, 0))) + sys.getsizeof(0)
                _CACHE.pop(key, None)
                while _CACHE and (len(_CACHE) >= _MAX_ITEMS or sys.getsizeof(_CACHE) + sum(entry[2] for entry in _CACHE.values()) + size + 512 > budget):
                    _CACHE.popitem(last=False)
                if size + sys.getsizeof(_CACHE) + 512 <= budget:
                    _CACHE[key] = (time.monotonic(), cached, size)
            _prune_cache(budget)
