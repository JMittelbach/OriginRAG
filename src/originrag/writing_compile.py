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
import threading
import time
import uuid

from .writing import decode_file, validate_files

COMPILE_SLOT = threading.BoundedSemaphore(1)
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


def compiler_status():
    if sys.platform == 'darwin' and shutil.which('tectonic') and Path('/usr/bin/sandbox-exec').exists():
        return {'configured': True, 'engine': 'Tectonic', 'isolation': 'macOS sandbox', 'message': 'Uses installed, cached TeX packages. No network access during compilation.'}
    if shutil.which('docker') and os.environ.get('ORIGINWRITE_TEX_IMAGE'):
        return {'configured': True, 'engine': 'pdfLaTeX / latexmk', 'isolation': 'container', 'message': 'Requires a running container engine and a prebuilt TeX image.'}
    if rootless_tools():
        return {'configured': True, 'engine': 'Tectonic', 'isolation': 'rootless Linux sandbox', 'message': 'Isolated compilation with cached TeX packages, no network and no access to account files.'}
    return {'configured': False, 'engine': '', 'message': 'Editing and export are available. The host must configure an isolated LaTeX compiler to enable PDF preview.'}


def mac_command(work):
    executable = str(Path(shutil.which('tectonic')).resolve())
    cache = Path.home() / 'Library/Caches/TectonicProject.Tectonic'
    alternate = Path.home() / 'Library/Caches/tectonic'
    selected = Path(os.environ.get('ORIGINWRITE_TECTONIC_CACHE', str(cache if cache.exists() else alternate))).resolve()
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


def rootless_command(work, main):
    bubblewrap, tectonic, cache = rootless_tools()
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


def compile_project(project):
    validate_files(project['files'], project['main'])
    if not COMPILE_SLOT.acquire(blocking=False):
        raise CompilerBusy('Another manuscript is compiling. Please try again shortly.')
    started = time.monotonic()
    container_name = 'originwrite-' + uuid.uuid4().hex
    process = None
    is_container = False
    def stop_worker():
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        if is_container:
            try:
                subprocess.run([shutil.which('docker'), 'rm', '-f', container_name], capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
    try:
        if not compiler_status()['configured']:
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
                   'TMPDIR': str(work), 'TECTONIC_UNTRUSTED_MODE': '1'}
            if sys.platform == 'darwin' and shutil.which('tectonic'):
                command, cache = mac_command(work)
                command[-1] = project['main']
                env['XDG_CACHE_HOME'] = str(cache.parent)
                # Tectonic uses the macOS user cache directory, configured explicitly for the worker.
                env['TECTONIC_CACHE_DIR'] = str(cache)
                runner = 'import os,resource,sys; resource.setrlimit(resource.RLIMIT_CPU,(40,40)); resource.setrlimit(resource.RLIMIT_FSIZE,(32000000,32000000)); resource.setrlimit(resource.RLIMIT_NOFILE,(128,128)); os.execv(sys.argv[1],sys.argv[1:])'
                command = [sys.executable, '-I', '-c', runner, *command]
            elif rootless_tools() and not os.environ.get('ORIGINWRITE_TEX_IMAGE'):
                command = rootless_command(work, project['main'])
            else:
                is_container = True
                work.chmod(0o755)
                (work / 'output').chmod(0o777)
                command = [shutil.which('docker'), 'run', '--rm', '--pull=never', '--name', container_name,
                           '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                           '--memory=768m', '--memory-swap=768m', '--cpus=1', '--pids-limit=64', '--user=65534:65534',
                           '--ulimit', 'fsize=32000000:32000000', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
                           '-v', f'{source}:/source:ro', '-v', f'{work / "output"}:/output:rw', '-w', '/source',
                           '-e', 'openin_any=p', '-e', 'openout_any=p', '-e', 'HOME=/tmp',
                           os.environ['ORIGINWRITE_TEX_IMAGE'], 'latexmk', '-pdf', '-no-shell-escape',
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
                        memory = subprocess.run(['/bin/ps', '-o', 'rss=', '-p', str(process.pid)], capture_output=True, timeout=2)
                        if memory.stdout.strip().isdigit() and int(memory.stdout.strip()) > 768 * 1024:
                            reason = 'Compilation exceeded the memory limit.'
                    if reason:
                        stop_worker()
                        break
                    time.sleep(.1)
            with log_file.open('rb') as log_handle:
                log = log_handle.read(100_000).decode('utf-8', errors='replace')
            log = log.replace(str(work), '[build]')
            if process.returncode and not log.strip():
                log = f'Compiler exited with status {process.returncode}. Check the isolated compiler setup.'
            pdf = work / 'output' / (Path(project['main']).stem + '.pdf')
            success = not reason and process.returncode == 0 and pdf.is_file() and not pdf.is_symlink() and pdf.stat().st_size <= OUTPUT_LIMIT
            pdf_bytes = pdf.read_bytes() if success else b''
            success = success and pdf_bytes.startswith(b'%PDF-')
            return {'ok': bool(success), 'exit_code': process.returncode, 'revision': project['revision'], 'seconds': round(time.monotonic() - started, 2),
                    'log': (reason + '\n' + log).strip(), 'diagnostics': diagnostics(log),
                    'pdf': pdf_bytes if success else b''}
    finally:
        COMPILE_SLOT.release()
