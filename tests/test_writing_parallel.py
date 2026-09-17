from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from pathlib import Path
import threading
import time

import pytest

from originrag import writing_builds as builds
from originrag import writing_compile as compiler
from originrag import writing_scheduler as scheduler


def until(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(.005)


@pytest.mark.parametrize('value,expected', [('0', 1), ('4', 4), ('100', 8), ('bad', 2)])
def test_capacity_configuration(monkeypatch, value, expected):
    monkeypatch.setattr(scheduler.os, 'cpu_count', lambda: 8)
    monkeypatch.setenv('ORIGINWRITE_MAX_PARALLEL_BUILDS', value)
    assert scheduler.configured_capacity() == expected


def test_slots_are_bounded_fifo_and_reusable():
    slots = scheduler.CompileSlots(2, max_queued=2)
    assert slots.acquire() and slots.acquire()
    assert not slots.acquire(blocking=False)
    order = []
    release = [threading.Event(), threading.Event()]

    def worker(index):
        assert slots.acquire(timeout=3)
        try:
            order.append(index)
            assert release[index].wait(3)
        finally:
            slots.release()

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(worker, 0)
        until(lambda: slots.status()['queued_builds'] == 1)
        second = pool.submit(worker, 1)
        until(lambda: slots.status()['queued_builds'] == 2)
        assert not slots.acquire(timeout=3)
        slots.release()
        until(lambda: order == [0])
        slots.release()
        until(lambda: order == [0, 1])
        for event in release:
            event.set()
        first.result()
        second.result()
    assert slots.status()['active_builds'] == slots.status()['queued_builds'] == 0
    with pytest.raises(ValueError):
        slots.release()


def test_queue_timeout_removes_ticket():
    slots = scheduler.CompileSlots(1)
    assert slots.acquire()
    assert not slots.acquire(timeout=.01)
    assert slots.status()['queued_builds'] == 0
    slots.release()
    assert slots.acquire(blocking=False)
    slots.release()


def test_configuration_can_load_after_module_import(monkeypatch):
    monkeypatch.setenv('ORIGINWRITE_MAX_PARALLEL_BUILDS', '2')
    slots = scheduler.CompileSlots()
    monkeypatch.setenv('ORIGINWRITE_MAX_PARALLEL_BUILDS', '4')
    assert slots.status()['parallel_builds'] == 4
    assert slots.acquire(blocking=False)
    slots.release()


@pytest.fixture
def project(monkeypatch):
    monkeypatch.setattr(builds, '_CACHE', builds.OrderedDict())
    monkeypatch.setattr(builds, '_INFLIGHT', {})
    monkeypatch.setattr(builds, '_WAITERS', 0)
    monkeypatch.setattr(builds, 'compiler_fingerprint', lambda: {'engine': 'test'})
    yield {'id': 'project', 'main': 'main.tex', 'files': {'main.tex': 'Hello'}, 'revision': 1}
    assert not builds._INFLIGHT and builds._WAITERS == 0


@pytest.mark.parametrize('failure', [False, True])
def test_identical_builds_share_work_and_propagate_failures(project, tmp_path, monkeypatch, failure):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def compile_one(item):
        calls.append(1)
        entered.set()
        assert release.wait(3)
        if failure:
            raise RuntimeError('engine failed')
        return {'ok': True, 'pdf': b'%PDF-test', 'seconds': 1, 'revision': item['revision'], 'diagnostics': []}

    monkeypatch.setattr(builds, 'compile_project', compile_one)
    with ThreadPoolExecutor(4) as pool:
        futures = [pool.submit(builds.compile_saved_project, project, tmp_path, force=True)]
        assert entered.wait(3)
        for revision in (2, 3, 4):
            futures.append(pool.submit(builds.compile_saved_project, {**project, 'revision': revision}, tmp_path, force=True))
        until(lambda: builds._WAITERS == 3)
        release.set()
        if failure:
            for future in futures:
                with pytest.raises(RuntimeError, match='engine failed'):
                    future.result()
            assert not builds._CACHE
        else:
            results = [future.result() for future in futures]
            assert [r['revision'] for r in results] == [1, 2, 3, 4]
            assert [r['shared_build'] for r in results] == [False, True, True, True]
            results[1]['diagnostics'].append('private mutation')
            assert not results[2]['diagnostics']
            assert not builds.compile_saved_project(project, tmp_path)['diagnostics']
    assert len(calls) == 1


@pytest.mark.parametrize('difference', ['owner', 'project', 'content'])
def test_distinct_builds_run_concurrently(project, tmp_path, monkeypatch, difference):
    barrier = threading.Barrier(2, timeout=3)

    def compile_one(item):
        barrier.wait()
        return {'ok': True, 'pdf': b'%PDF-test', 'seconds': 1, 'revision': item['revision']}

    monkeypatch.setattr(builds, 'compile_project', compile_one)
    other = deepcopy(project)
    owner = tmp_path
    if difference == 'owner':
        owner = tmp_path / 'bob'
    elif difference == 'project':
        other['id'] = 'another'
    else:
        other['files']['main.tex'] = 'Different'
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(builds.compile_saved_project, project, tmp_path)
        second = pool.submit(builds.compile_saved_project, other, owner)
        assert first.result()['ok'] and second.result()['ok']


def test_outstanding_request_limits(project, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(builds, '_MAX_INFLIGHT', 1)
    monkeypatch.setattr(builds, '_MAX_WAITERS', 0)

    def compile_one(item):
        entered.set()
        assert release.wait(3)
        return {'ok': False, 'seconds': 1}

    monkeypatch.setattr(builds, 'compile_project', compile_one)
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(builds.compile_saved_project, project, tmp_path)
        assert entered.wait(3)
        try:
            with pytest.raises(builds.CompilerBusy):
                builds.compile_saved_project(project, tmp_path)
            with pytest.raises(builds.CompilerBusy):
                builds.compile_saved_project({**project, 'id': 'other'}, tmp_path)
        finally:
            release.set()
        assert not first.result()['ok']


def test_linux_memory_monitor_does_not_spawn_process(monkeypatch):
    monkeypatch.setattr(compiler.sys, 'platform', 'linux')
    monkeypatch.setattr(Path, 'read_text', lambda self: 'Name:\ttectonic\nVmRSS:\t12345 kB\n')
    monkeypatch.setattr(compiler.subprocess, 'run', lambda *a, **k: pytest.fail('Unnecessary ps process'))
    assert compiler._resident_kib(123) == 12345


def test_output_monitor_tolerates_disappearing_auxiliary_files(tmp_path, monkeypatch):
    stable = tmp_path / 'main.pdf'
    stable.write_bytes(b'%PDF-test')
    transient = tmp_path / 'transient.aux'
    transient.write_text('temporary')
    original = Path.lstat
    def disappearing(path):
        if path == transient:
            raise FileNotFoundError()
        return original(path)
    monkeypatch.setattr(Path, 'lstat', disappearing)
    assert compiler._output_bytes(tmp_path) == len(b'%PDF-test')


def test_output_monitor_does_not_follow_symlinks(tmp_path):
    (tmp_path / 'result').write_bytes(b'1234')
    (tmp_path / 'alias').symlink_to(tmp_path / 'result')
    assert compiler._output_bytes(tmp_path) == 4


def test_linux_memory_monitor_handles_exited_process_but_not_permission_failure(monkeypatch):
    monkeypatch.setattr(compiler.sys, 'platform', 'linux')
    def missing(self):
        raise FileNotFoundError()
    monkeypatch.setattr(Path, 'read_text', missing)
    assert compiler._resident_kib(123) == 0
    def denied(self):
        raise PermissionError()
    monkeypatch.setattr(Path, 'read_text', denied)
    with pytest.raises(PermissionError):
        compiler._resident_kib(123)


@pytest.mark.skipif(os.environ.get('ORIGINWRITE_TEST_COMPILER') != '1', reason='Requires isolated LaTeX compiler')
def test_real_parallel_pdfs_preserve_project_contents(tmp_path, monkeypatch):
    fitz = pytest.importorskip('fitz')
    monkeypatch.setattr(compiler, 'COMPILE_SLOT', scheduler.CompileSlots(4))
    barrier = threading.Barrier(6, timeout=10)
    def run(index):
        project = {'id': str(index), 'main': 'main.tex', 'revision': index, 'files': {
            'main.tex': r'\documentclass{article}\begin{document}Parallel manuscript ' + str(index) + r'\end{document}'}}
        barrier.wait()
        result = builds.compile_saved_project(project, tmp_path / str(index), force=True)
        assert result['ok'], result['log']
        assert result['revision'] == index
        with fitz.open(stream=result['pdf'], filetype='pdf') as pdf:
            assert f'Parallel manuscript {index}' in pdf[0].get_text()
        return result['queue_seconds']
    with ThreadPoolExecutor(6) as pool:
        waits = list(pool.map(run, range(6)))
    assert sum(value > 0 for value in waits) >= 2
    assert compiler.COMPILE_SLOT.status()['active_builds'] == 0
