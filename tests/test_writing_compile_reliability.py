from pathlib import Path
import subprocess

import pytest

from originrag import writing_builds as builds
from originrag import writing_compile as compiler


PROJECT = {'id': 'example', 'revision': 1, 'files': {'main.tex': 'Example'}, 'main': 'main.tex'}
BACKEND = {'kind': 'rootless', 'executable': '/tools/tectonic', 'sandbox': '/tools/bwrap', 'cache': '/tools/cache'}


def fake_engine(monkeypatch, *, stdout='', final_log='', alive=False, wait_timeout=False):
    commands = []

    class Process:
        pid = 90000001
        returncode = None if alive else 0

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            if wait_timeout:
                raise subprocess.TimeoutExpired('test-worker', timeout)
            return self.returncode

    process = Process()

    def start(command, **options):
        commands.append(command)
        work = Path(options['cwd']).parent
        (work / 'output' / 'main.pdf').write_bytes(b'%PDF-test-output')
        (work / 'output' / 'main.log').write_text(final_log)
        options['stdout'].write(stdout.encode())
        options['stdout'].flush()
        return process

    def kill(_pid, _signal):
        if not wait_timeout:
            process.returncode = -9

    monkeypatch.setattr(compiler.subprocess, 'Popen', start)
    monkeypatch.setattr(compiler.os, 'killpg', kill)
    monkeypatch.setattr(compiler, 'rootless_command', lambda work, main, tools=None: ['isolated-test-engine', *tools, main])
    return commands


def test_selected_rootless_backend_is_used_when_docker_is_missing(monkeypatch):
    monkeypatch.setattr(compiler.sys, 'platform', 'linux')
    monkeypatch.setattr(compiler.shutil, 'which', lambda _: None)
    monkeypatch.setattr(compiler, 'rootless_tools', lambda: ('/tools/bwrap', '/tools/tectonic', '/tools/cache'))
    monkeypatch.setenv('ORIGINWRITE_TEX_IMAGE', 'tex-example:latest')
    assert compiler.compiler_status()['isolation'] == 'rootless Linux sandbox'
    commands = fake_engine(monkeypatch)
    result = compiler.compile_project(PROJECT)
    assert result['ok']
    assert commands == [['isolated-test-engine', '/tools/bwrap', '/tools/tectonic', '/tools/cache', 'main.tex']]


def test_mac_without_sandbox_uses_container_backend_consistently(monkeypatch):
    monkeypatch.setattr(compiler.sys, 'platform', 'darwin')
    monkeypatch.setattr(compiler.shutil, 'which', lambda name: '/tools/' + name)
    monkeypatch.setattr(compiler.Path, 'exists', lambda path: False)
    monkeypatch.setenv('ORIGINWRITE_TEX_IMAGE', 'tex-example:pinned')
    assert compiler.select_compiler_backend()['kind'] == 'container'
    assert compiler.compiler_status()['isolation'] == 'container'


@pytest.mark.parametrize('wait_timeout', [False, True])
def test_watchdog_timeout_returns_structured_failure_and_releases_slot(monkeypatch, wait_timeout):
    monkeypatch.setattr(compiler, 'select_compiler_backend', lambda: BACKEND)
    fake_engine(monkeypatch, alive=True, wait_timeout=wait_timeout)

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired('ps', 2)

    monkeypatch.setattr(compiler.subprocess, 'run', timed_out)
    result = compiler.compile_project(PROJECT)
    assert not result['ok'] and result['pdf'] == b''
    assert 'resource monitoring was unavailable' in result['log']
    assert result['diagnostics']
    if wait_timeout:
        assert 'cleanup timed out' in result['log']
    assert compiler.COMPILE_SLOT.acquire(blocking=False)
    compiler.COMPILE_SLOT.release()


def test_missing_engine_returns_structured_failure(monkeypatch):
    monkeypatch.setattr(compiler, 'select_compiler_backend', lambda: BACKEND)
    monkeypatch.setattr(compiler, 'rootless_command', lambda *args: ['missing-engine'])

    def unavailable(*args, **kwargs):
        raise FileNotFoundError('Compiler was removed')

    monkeypatch.setattr(compiler.subprocess, 'Popen', unavailable)
    result = compiler.compile_project(PROJECT)
    assert not result['ok'] and result['pdf'] == b'' and result['diagnostics']
    assert 'source is saved' in result['log']


@pytest.mark.parametrize('warning', [
    'warning: errors were issued by BibTeX, but were ignored; use --print and/or --keep-logs for details.',
    r'I found no \citation commands---while reading file main.aux',
    "Warning--I didn't find a database entry for \"missing-key\"",
    'I couldn\'t open style file plain.bst',
])
def test_zero_exit_with_bibliography_errors_is_not_success(monkeypatch, warning):
    monkeypatch.setattr(compiler, 'select_compiler_backend', lambda: BACKEND)
    fake_engine(monkeypatch, stdout='Harmless compiler text\n' * 10000 + warning + '\n' + 'More text\n' * 20000)
    result = compiler.compile_project(PROJECT)
    assert result['exit_code'] == 0
    assert not result['ok'] and not result['pdf']
    assert 'Bibliography or citation errors' in result['log']
    assert 'synchronise' in result['diagnostics'][0]['message']


def test_only_final_undefined_citations_are_fatal(monkeypatch):
    monkeypatch.setattr(compiler, 'select_compiler_backend', lambda: BACKEND)
    fake_engine(monkeypatch, stdout="LaTeX Warning: Citation `valid-key' on page 1 undefined.\n",
                final_log='Overfull \\hbox (1pt too wide)\nPackage hyperref Warning: benign warning')
    assert compiler.compile_project(PROJECT)['ok']
    fake_engine(monkeypatch, final_log="Package natbib Warning: There were undefined citations.")
    result = compiler.compile_project(PROJECT)
    assert not result['ok'] and not result['pdf']


def test_cache_changes_with_binary_revision_calendar_and_explicit_refresh(tmp_path, monkeypatch):
    builds._CACHE.clear()
    executable = tmp_path / 'tectonic'
    executable.write_text('version-one')
    cache = tmp_path / 'cache'
    cache.mkdir()
    monkeypatch.setattr(builds, 'select_compiler_backend', lambda: {'kind': 'rootless', 'executable': str(executable), 'cache': str(cache)})
    monkeypatch.setattr(builds, '_calendar_identity', lambda: ['2026-09-16', 'UTC', '+0000', ''])
    calls = []

    def run(project):
        calls.append(1)
        return {'ok': True, 'pdf': b'%PDF-test', 'log': '', 'diagnostics': [], 'seconds': 1, 'revision': project['revision']}

    monkeypatch.setattr(builds, 'compile_project', run)
    assert not builds.compile_saved_project(PROJECT, tmp_path / 'alice')['cached']
    assert builds.compile_saved_project(PROJECT, tmp_path / 'alice')['cached']
    executable.write_text('version-two-with-new-contents')
    assert not builds.compile_saved_project(PROJECT, tmp_path / 'alice')['cached']
    (cache / '.originwrite-cache-revision').write_text('packages-refreshed-1')
    assert not builds.compile_saved_project(PROJECT, tmp_path / 'alice')['cached']
    monkeypatch.setattr(builds, '_calendar_identity', lambda: ['2026-09-17', 'UTC', '+0000', ''])
    assert not builds.compile_saved_project(PROJECT, tmp_path / 'alice')['cached']
    assert builds.compile_saved_project(PROJECT, tmp_path / 'alice')['cached']
    assert not builds.compile_saved_project(PROJECT, tmp_path / 'alice', force=True)['cached']
    monkeypatch.setattr(builds, 'compile_project', lambda _: {'ok': False, 'pdf': b'', 'seconds': 1, 'log': 'failed'})
    assert not builds.compile_saved_project(PROJECT, tmp_path / 'alice', force=True)['ok']
    assert not builds.compile_saved_project(PROJECT, tmp_path / 'alice')['ok']
    assert len(calls) == 5


def test_compiler_removal_and_invalid_force_do_not_reuse_cache(tmp_path, monkeypatch):
    builds._CACHE.clear()
    backend = {'kind': 'container', 'executable': '/tools/docker', 'image': 'pinned:revision'}
    monkeypatch.setattr(builds, 'select_compiler_backend', lambda: backend)
    monkeypatch.setattr(builds, 'compile_project', lambda _: {'ok': True, 'pdf': b'%PDF', 'seconds': 1, 'revision': 1})
    assert not builds.compile_saved_project(PROJECT, tmp_path)['cached']
    assert builds.compile_saved_project(PROJECT, tmp_path)['cached']
    monkeypatch.setattr(builds, 'select_compiler_backend', lambda: None)
    monkeypatch.setattr(builds, 'compile_project', lambda _: {'ok': False, 'pdf': b'', 'seconds': 1})
    assert not builds.compile_saved_project(PROJECT, tmp_path)['ok']
    with pytest.raises(ValueError, match='force option'):
        builds.compile_saved_project(PROJECT, tmp_path, force='false')
