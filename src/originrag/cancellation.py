from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import http.client
import socket
import subprocess
import threading
import urllib.request


class TurnCancelled(BaseException):
    pass


class Cancellation:
    def __init__(self):
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.callbacks = set()
        self.committed = False

    def check(self):
        if self.event.is_set():
            raise TurnCancelled()

    def cancel(self):
        with self.lock:
            if self.committed:
                return False
            self.event.set()
            callbacks = tuple(self.callbacks)
        for callback in callbacks:
            try:
                callback()
            except OSError:
                pass
        return True

    def register(self, callback):
        with self.lock:
            self.callbacks.add(callback)
            cancelled = self.event.is_set()
        if cancelled:
            callback()
        return lambda: self.unregister(callback)

    def unregister(self, callback):
        with self.lock:
            self.callbacks.discard(callback)

    def commit(self):
        with self.lock:
            self.check()
            self.committed = True


current_cancellation = ContextVar("originrag_cancellation", default=None)


def checkpoint():
    token = current_cancellation.get()
    if token:
        token.check()


def commit_answer():
    token = current_cancellation.get()
    if token:
        token.commit()


@contextmanager
def cancellable_lock(lock):
    while not lock.acquire(timeout=0.1):
        checkpoint()
    try:
        checkpoint()
        yield
    finally:
        lock.release()


@contextmanager
def cancellation_scope(token):
    previous = current_cancellation.set(token)
    try:
        token.check()
        yield
        token.check()
    finally:
        current_cancellation.reset(previous)


@contextmanager
def urlopen(request, *, timeout):
    token = current_cancellation.get()
    if token is None:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            yield response
        return
    token.check()
    releases = []

    class ConnectionMixin:
        def connect(self):
            token.check()
            requested_timeout = self.timeout
            self.timeout = min(requested_timeout, 5)
            super().connect()
            sock = self.sock
            sock.settimeout(requested_timeout)
            self.timeout = requested_timeout

            def disconnect():
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            releases.append(token.register(disconnect))
            token.check()

    class HTTPConnection(ConnectionMixin, http.client.HTTPConnection):
        pass

    class HTTPSConnection(ConnectionMixin, http.client.HTTPSConnection):
        pass

    class HTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(HTTPConnection, req)

    class HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(HTTPSConnection, req, context=self._context)

    try:
        opener = urllib.request.build_opener(HTTPHandler(), HTTPSHandler())
        with opener.open(request, timeout=timeout) as response:
            token.check()
            yield response
            token.check()
    except BaseException:
        token.check()
        raise
    finally:
        for release in releases:
            release()


def run_process(args, *, input, text, capture_output, timeout, check=False):
    token = current_cancellation.get()
    if token is None:
        return subprocess.run(args, input=input, text=text, capture_output=capture_output,
                              timeout=timeout, check=check)
    token.check()
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=text)

    def stop():
        try:
            process.kill()
        except ProcessLookupError:
            pass

    release = token.register(stop)
    try:
        stdout, stderr = process.communicate(input, timeout=timeout)
        token.check()
        result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
    finally:
        release()
        if process.poll() is None:
            process.kill()
            process.communicate()
