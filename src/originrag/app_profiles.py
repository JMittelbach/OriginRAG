from __future__ import annotations

import importlib
import os
import threading
from dataclasses import dataclass


PROFILES = ('full', 'rag', 'write')
CHAT_ROUTES = frozenset({
    '/chat', '/api/chat', '/api/reset', '/api/sessions', '/api/session', '/api/session-title',
    '/api/session-delete', '/api/source-trace', '/api/source-request', '/api/job', '/api/jobs',
    '/api/job/cancel', '/api/progress', '/file',
})
COLLECTION_ACTIONS = frozenset({'create_collection', 'rename_collection', 'collection_paper'})


@dataclass(frozen=True)
class AppProfile:
    name: str

    @property
    def rag(self):
        return self.name != 'write'

    @property
    def write(self):
        return self.name != 'rag'

    @property
    def home(self):
        return '/write' if self.name == 'write' else '/chat'

    def public(self):
        return {'profile': self.name, 'home': self.home,
                'features': {'rag': self.rag, 'write': self.write, 'library': True, 'shelf': True, 'reader': True}}

    def permits(self, path):
        if not self.rag and path in CHAT_ROUTES:
            return False
        if not self.write and (path == '/write' or path == '/api/writing/export'):
            return False
        return True


def app_profile(value=None):
    name = value if value is not None else os.environ.get('ORIGINRAG_APP_PROFILE', 'full')
    if name not in PROFILES:
        raise ValueError('Choose an application profile: full, rag or write.')
    return AppProfile(name)


class LazyModule:
    def __init__(self, name):
        self.name = name
        self.lock = threading.RLock()
        self.module = None

    def __getattr__(self, name):
        with self.lock:
            if self.module is None:
                self.module = importlib.import_module(self.name)
            return getattr(self.module, name)


def lazy_function(module, name):
    def call(*args, **kwargs):
        return getattr(importlib.import_module(module), name)(*args, **kwargs)
    return call
