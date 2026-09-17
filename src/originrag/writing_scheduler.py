from collections import deque
import os
import threading
import time


QUEUE_WAIT_SECONDS = 60
MAX_QUEUED_BUILDS = 16


def configured_capacity():
    default = min(2, os.cpu_count() or 1)
    try:
        return min(8, max(1, int(os.environ.get('ORIGINWRITE_MAX_PARALLEL_BUILDS', default))))
    except ValueError:
        return default


class CompileSlots:
    def __init__(self, capacity=None, max_queued=MAX_QUEUED_BUILDS):
        self._capacity = capacity
        self.max_queued = max_queued
        self.active = 0
        self.waiting = deque()
        self.condition = threading.Condition()

    @property
    def capacity(self):
        return configured_capacity() if self._capacity is None else self._capacity

    def acquire(self, blocking=True, timeout=None):
        with self.condition:
            if self.active < self.capacity and not self.waiting:
                self.active += 1
                return True
            if not blocking or len(self.waiting) >= self.max_queued:
                return False
            ticket = object()
            self.waiting.append(ticket)
            deadline = time.monotonic() + (QUEUE_WAIT_SECONDS if timeout is None else timeout)
            try:
                while self.waiting[0] is not ticket or self.active >= self.capacity:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self.condition.wait(remaining)
                self.waiting.popleft()
                self.active += 1
                return True
            finally:
                if ticket in self.waiting:
                    self.waiting.remove(ticket)
                self.condition.notify_all()

    def release(self):
        with self.condition:
            if self.active <= 0:
                raise ValueError('No active compiler slot to release.')
            self.active -= 1
            self.condition.notify_all()

    def status(self):
        with self.condition:
            return {'parallel_builds': self.capacity, 'active_builds': self.active,
                    'queued_builds': len(self.waiting), 'queue_limit': self.max_queued}
