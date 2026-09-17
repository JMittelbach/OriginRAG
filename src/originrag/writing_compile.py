from __future__ import annotations

import json
from contextlib import ExitStack
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from .writing import decode_file, validate_files
from .writing_scheduler import CompileSlots, QUEUE_WAIT_SECONDS

COMPILE_SLOT = CompileSlots()
TIME_LIMIT = 45
OUTPUT_LIMIT = 32_000_000


class CompilerBusy(ValueError):
    pass


def rootless_tools():
    bubblewrap = shutil.which(os.environ.get('ORIGINWRITE_BWRAP', 'bwrap'))
    tectonic = shutil.which(os.environ.get('ORIGINWRITE_TECTONIC', 'tectonic'))
    cache = os.environ.get('ORIGINWRITE_TECTONIC_CACHE', '')
    if sys.platform.startswith('linux') and bubblewrap and tectonic and cache and Path(cache).is_dir():
        resolved = Path(cache).resolve()
        if resolved in {Path('/'), Path.home(), Path.cwd()}:
            return None
        return bubblewrap, tectonic, str(resolved)
    return None


def mac_cache_path():
    cache = Path.home() / 'Library/Caches/TectonicProject.Tectonic'
    alternate = Path.home() / 'Library/Caches/tectonic'
    return Path(os.environ.get('ORIGINWRITE_TECTONIC_CACHE', str(cache if cache.exists() else alternate))).resolve()


def select_compiler_backend():
    tectonic = shutil.which('tectonic')
    if sys.platform == 'darwin' and tectonic and Path('/usr/bin/sandbox-exec').exists():
        return {'kind': 'macos', 'executable': str(Path(tectonic).resolve()),
                'sandbox': '/usr/bin/sandbox-exec', 'cache': str(mac_cache_path())}
    docker = shutil.which('docker')
    image = os.environ.get('ORIGINWRITE_TEX_IMAGE')
    if docker and image:
        return {'kind': 'container', 'executable': docker, 'image': image}
    tools = rootless_tools()
    if tools:
        return {'kind': 'rootless', 'sandbox': tools[0], 'executable': tools[1], 'cache': tools[2]}
    return None


def compiler_status():
    backend = select_compiler_backend()
    status = COMPILE_SLOT.status()
    if backend and backend['kind'] == 'macos':
        return {**status, 'configured': True, 'engine': 'Tectonic', 'isolation': 'macOS sandbox', 'message': 'Uses installed, cached TeX packages. No network access during compilation.'}
    if backend and backend['kind'] == 'container':
        return {**status, 'configured': True, 'engine': 'pdfLaTeX / latexmk', 'isolation': 'container', 'message': 'Requires a running container engine and a prebuilt TeX image.'}
    if backend and backend['kind'] == 'rootless':
        return {**status, 'configured': True, 'engine': 'Tectonic', 'isolation': 'rootless Linux sandbox', 'message': 'Isolated compilation with cached TeX packages, no network and no access to account files.'}
    return {**status, 'configured': False, 'engine': '', 'message': 'Editing and export are available. The host must configure an isolated LaTeX compiler to enable PDF preview.'}


def mac_command(work, backend=None):
    executable = backend['executable'] if backend else str(Path(shutil.which('tectonic')).resolve())
    selected = Path(backend['cache']) if backend else mac_cache_path()
    # Only the compiler's package cache is readable, never the user's home or project tree.
    if selected == Path.home() or selected == Path('/') or not selected.is_dir():
        raise ValueError('No TeX package cache found. Prepare the compiler cache first (see docs/writing.md).')
    read_roots = ['/System', '/usr', '/bin', '/Library/Fonts', '/Library/Apple', '/opt/homebrew/Cellar', '/opt/homebrew/lib', str(selected)]
    rules = ['(version 1)', '(deny default)', '(allow process*)', '(allow sysctl-read)', '(allow mach-lookup)',
             '(allow file-read-metadata)', '(allow file-read* (literal "/") (literal "/dev/null") (literal "/dev/urandom"))',
             '(allow file-write* (literal "/dev/null"))']
    rules += ['(allow file-read* (subpath ' + json.dumps(p) + '))' for p in read_roots]
    rules += ['(allow file-read* file-write* (subpath ' + json.dumps(str(work)) + '))']
    profile = work / 'compiler.sb'
    profile.write_text('\n'.join(rules))
    command = ['/usr/bin/sandbox-exec', '-f', str(profile), executable, '--untrusted', '--only-cached', '--keep-logs', '--print', '--outdir', str(work / 'output'), 'main.tex']
    return command, selected


def diagnostics(log):
    found = []
    for line in log.splitlines():
        match = re.search(r'(?:^|\s)([^\s:]+\.tex):(\d+):\s*(.*)', line)
        if match:
            found.append({'file': match[1].removeprefix('./'), 'line': int(match[2]), 'message': match[3]})
        elif line.startswith('!') or 'warning:' in line.lower() or 'error:' in line.lower():
            found.append({'file': '', 'line': 0, 'message': line})
    return found[:100]


def rootless_command(work, main, tools=None):
    bubblewrap, tectonic, cache = tools if tools is not None else rootless_tools()
    (work / 'temporary').mkdir()
    command = [bubblewrap, '--unshare-all', '--die-with-parent', '--new-session', '--cap-drop', 'ALL',
               '--clearenv', '--ro-bind', '/usr', '/usr', '--symlink', 'usr/lib', '/lib',
               '--symlink', 'usr/lib64', '/lib64', '--proc', '/proc', '--dev', '/dev',
               '--ro-bind', tectonic, '/engine/tectonic', '--ro-bind', cache, '/tex-cache',
               '--ro-bind', str(work / 'source'), '/source', '--bind', str(work / 'output'), '/output',
               '--bind', str(work / 'temporary'), '/tmp', '--dir', '/etc']
    if Path('/etc/fonts').is_dir():
        command += ['--ro-bind', '/etc/fonts', '/etc/fonts']
    command += ['--remount-ro', '/', '--setenv', 'HOME', '/tmp', '--setenv', 'TMPDIR', '/tmp',
                '--setenv', 'RAYON_NUM_THREADS', '1', '--setenv', 'OMP_NUM_THREADS', '1',
                '--setenv', 'XDG_CACHE_HOME', '/tmp/cache', '--setenv', 'LANG', 'C.UTF-8',
                '--setenv', 'TECTONIC_CACHE_DIR', '/tex-cache', '--setenv', 'TECTONIC_UNTRUSTED_MODE', '1',
                '--chdir', '/source', '/engine/tectonic', '--untrusted', '--only-cached',
                '--keep-logs', '--print', '--outdir', '/output', main]
    limits = ('import os,resource,sys; resource.setrlimit(resource.RLIMIT_CPU,(40,40)); '
              'resource.setrlimit(resource.RLIMIT_AS,(805306368,805306368)); '
              'resource.setrlimit(resource.RLIMIT_FSIZE,(32000000,32000000)); '
              'resource.setrlimit(resource.RLIMIT_NOFILE,(128,128)); '
              'os.execv(sys.argv[1],sys.argv[1:])')
    return [sys.executable, '-I', '-c', limits, *command]


_BIB_ERRORS = re.compile(
    r'errors? were issued by BibTeX|BibTeX[^\n]{0,160}errors?[^\n]{0,80}ignored|'
    r'There (?:was|were) [1-9][0-9]* error messages?|'
    r'I found no \\(?:citation|bibdata|bibstyle) commands|'
    r'I couldn.t open (?:database|style) file|'
    r'Warning--I didn.t find a database entry', re.I)
_CITATION_ERRORS = re.compile(r'Citation[^\n]{0,500}\bundefined\b|There were undefined citations', re.I)


def bibliography_problem(log_file, output_directory):
    paths = [(log_file, False)]
    paths.extend((path, False) for path in output_directory.glob('*.blg'))
    paths.extend((path, True) for path in output_directory.glob('*.log'))
    for path, final_tex_log in paths:
        if not path.is_file() or path.is_symlink():
            continue
        with path.open('r', encoding='utf-8', errors='replace') as handle:
            tail = ''
            while chunk := handle.read(8192):
                text = tail + chunk
                if _BIB_ERRORS.search(text) or (final_tex_log and _CITATION_ERRORS.search(text)):
                    return ('Bibliography or citation errors prevented a reliable PDF. Check the cited keys and synchronise '
                            'the linked .bib file. If there are no citations yet, comment out the bibliography commands. '
                            'The saved source is unchanged; compiler details follow below.')
                tail = text[-600:]
    return ''


def _log_excerpt(path):
    with path.open('rb') as handle:
        start = handle.read(100_000)
        if len(start) < 100_000 or not handle.read(1):
            return start.decode('utf-8', errors='replace')
        handle.seek(-50_000, os.SEEK_END)
        return (start[:50_000] + b'\n[Earlier compiler output shortened]\n' + handle.read(50_000)).decode('utf-8', errors='replace')


def _resident_kib(pid):
    if sys.platform.startswith('linux'):
        try:
            status = Path(f'/proc/{pid}/status').read_text()
            return next((int(line.split()[1]) for line in status.splitlines() if line.startswith('VmRSS:')), 0)
        except FileNotFoundError:
            return 0
    memory = subprocess.run(['/bin/ps', '-o', 'rss=', '-p', str(pid)], capture_output=True, timeout=2)
    return int(memory.stdout.strip()) if memory.stdout.strip().isdigit() else 0


def compile_project(project):
    validate_files(project['files'], project['main'])
    waiting_since = time.monotonic()
    if not COMPILE_SLOT.acquire(timeout=QUEUE_WAIT_SECONDS):
        raise CompilerBusy('The compiler queue is full or the wait limit was reached. Your source is saved; please retry shortly.')
    started = time.monotonic()
    queue_seconds = round(started - waiting_since, 3)
    container_name = 'originwrite-' + uuid.uuid4().hex
    process = None
    is_container = False
    backend = None
    cleanup_error = ''
    def stop_worker():
        nonlocal cleanup_error
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cleanup_error = 'Compiler cleanup timed out after termination. Please retry or contact the host.'
        if is_container:
            try:
                subprocess.run([backend['executable'], 'rm', '-f', container_name], capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                cleanup_error = 'The isolated compiler could not be cleaned up. Please contact the host.'
    try:
        backend = select_compiler_backend()
        if backend is None:
            raise ValueError(compiler_status()['message'])
        with tempfile.TemporaryDirectory(prefix='originwrite-') as temporary, ExitStack() as cleanup:
            cleanup.callback(stop_worker)
            work = Path(temporary).resolve()
            source = work / 'source'
            source.mkdir()
            (work / 'output').mkdir()
            for name, content in project['files'].items():
                dest = source / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(decode_file(name, content))
            env = {'PATH': '/usr/bin:/bin:/opt/homebrew/bin', 'LANG': 'en_US.UTF-8', 'HOME': str(work),
                   'TMPDIR': str(work), 'TECTONIC_UNTRUSTED_MODE': '1', 'RAYON_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
            if backend['kind'] == 'macos':
                command, cache = mac_command(work, backend)
                command[-1] = project['main']
                env['XDG_CACHE_HOME'] = str(cache.parent)
                # Tectonic uses the macOS user cache directory, configured explicitly for the worker.
                env['TECTONIC_CACHE_DIR'] = str(cache)
                runner = 'import os,resource,sys; resource.setrlimit(resource.RLIMIT_CPU,(40,40)); resource.setrlimit(resource.RLIMIT_FSIZE,(32000000,32000000)); resource.setrlimit(resource.RLIMIT_NOFILE,(128,128)); os.execv(sys.argv[1],sys.argv[1:])'
                command = [sys.executable, '-I', '-c', runner, *command]
            elif backend['kind'] == 'rootless':
                command = rootless_command(work, project['main'], (backend['sandbox'], backend['executable'], backend['cache']))
            else:
                is_container = True
                work.chmod(0o755)
                (work / 'output').chmod(0o777)
                command = [backend['executable'], 'run', '--rm', '--pull=never', '--name', container_name,
                           '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                           '--memory=768m', '--memory-swap=768m', '--cpus=1', '--pids-limit=64', '--user=65534:65534',
                           '--ulimit', 'fsize=32000000:32000000', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
                           '-v', f'{source}:/source:ro', '-v', f'{work / "output"}:/output:rw', '-w', '/source',
                           '-e', 'openin_any=p', '-e', 'openout_any=p', '-e', 'HOME=/tmp', '-e', 'OMP_NUM_THREADS=1',
                           backend['image'], 'latexmk', '-pdf', '-no-shell-escape',
                           '-interaction=nonstopmode', '-halt-on-error', '-file-line-error', '-outdir=/output', project['main']]
                env = None
            log_file = work / 'compile-output.txt'
            with log_file.open('wb') as output:
                process = subprocess.Popen(command, cwd=source, env=env, stdin=subprocess.DEVNULL,
                                           stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                reason = ''
                while process.poll() is None:
                    if time.monotonic() - started > TIME_LIMIT:
                        reason = 'Compilation exceeded the 45-second limit.'
                    elif sum(p.stat().st_size for p in work.rglob('*') if p.is_file() and not p.is_symlink()) > OUTPUT_LIMIT + 12_000_000:
                        reason = 'Compilation exceeded the output-size limit.'
                    if not is_container:
                        try:
                            if _resident_kib(process.pid) > 768 * 1024:
                                reason = 'Compilation exceeded the memory limit.'
                        except (OSError, subprocess.TimeoutExpired):
                            reason = 'Compiler resource monitoring was unavailable. Compilation could not continue safely; please retry.'
                    if reason:
                        stop_worker()
                        break
                    time.sleep(.1)
            log = _log_excerpt(log_file)
            log = log.replace(str(work), '[build]')
            if process.returncode and not log.strip():
                log = f'Compiler exited with status {process.returncode}. Check the isolated compiler setup.'
            pdf = work / 'output' / (Path(project['main']).stem + '.pdf')
            reason = reason or bibliography_problem(log_file, work / 'output')
            if cleanup_error:
                reason = (reason + ' ' + cleanup_error).strip()
            success = not reason and process.returncode == 0 and pdf.is_file() and not pdf.is_symlink() and pdf.stat().st_size <= OUTPUT_LIMIT
            pdf_bytes = pdf.read_bytes() if success else b''
            success = success and pdf_bytes.startswith(b'%PDF-')
            return {'ok': bool(success), 'exit_code': process.returncode, 'revision': project['revision'], 'seconds': round(time.monotonic() - started, 2), 'queue_seconds': queue_seconds,
                    'log': (reason + '\n' + log).strip(),
                    'diagnostics': ([{'file': '', 'line': 0, 'message': reason}] if reason else []) + diagnostics(log),
                    'pdf': pdf_bytes if success else b''}
    except (OSError, subprocess.SubprocessError):
        message = 'The isolated compiler could not finish safely. Your source is saved. Please retry or ask the host to check the compiler setup.'
        if cleanup_error:
            message += ' ' + cleanup_error
        return {'ok': False, 'exit_code': process.returncode if process else None, 'revision': project['revision'],
                'seconds': round(time.monotonic() - started, 2), 'queue_seconds': queue_seconds, 'log': message,
                'diagnostics': [{'file': '', 'line': 0, 'message': message}], 'pdf': b''}
    finally:
        COMPILE_SLOT.release()
