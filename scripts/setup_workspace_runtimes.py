"""Provision the selected workspace's non-Python runtimes, without sudo."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import urlopen

from originrag.paths import load_project_env
from originrag.writing_compile import compile_project, mac_cache_path, select_compiler_backend, tectonic_path
from originrag.writing_templates import template_files

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'
RELEASE = '0.17.0'
TECTONIC = {
    ('Darwin', 'arm64'): ('aarch64-apple-darwin', 'a3f1cac7c5678f01661a92212f58480ae3b0634115d880dbc59e2953ded45667'),
    ('Darwin', 'x86_64'): ('x86_64-apple-darwin', '7c90ef5b6ddb1eb1937e4337add5237b79338e4b9676459fa91187d24d6cdf80'),
    ('Linux', 'aarch64'): ('aarch64-unknown-linux-musl', 'b10954a95404f3ab2328d2fa59a5ebab8e657f893fab096f98be8db7c0c979b8'),
    ('Linux', 'x86_64'): ('x86_64-unknown-linux-musl', '8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7'),
}


def conda_install(package):
    subprocess.run(['conda', 'install', '--yes', '--prefix', sys.prefix, '--override-channels',
                    '-c', 'conda-forge', package], check=True)


def install_tectonic():
    key = (platform.system(), platform.machine())
    if key not in TECTONIC:
        raise ValueError(f'No bundled Tectonic installer for {key}. See docs/writing.md.')
    target, digest = TECTONIC[key]
    url = (f'https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40{RELEASE}/'
           f'tectonic-{RELEASE}-{target}.tar.gz')
    tools = RUNTIME / 'tools'
    tools.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tools) as tmp:
        archive = Path(tmp) / 'package.tar.gz'
        with urlopen(url, timeout=90) as response, archive.open('wb') as output:
            shutil.copyfileobj(response, output)
        with archive.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != digest:
                raise ValueError('Tectonic download checksum mismatch.')
        with tarfile.open(archive) as tar:
            member = next((m for m in tar if m.isfile() and Path(m.name).name == 'tectonic'), None)
            if member is None:
                raise ValueError('Tectonic executable missing from its distribution.')
            binary = Path(tmp) / 'tectonic'
            with tar.extractfile(member) as source, binary.open('wb') as output:
                shutil.copyfileobj(source, output)
            binary.chmod(0o755)
            binary.replace(tools / 'tectonic')


def prepare_tex():
    backend = select_compiler_backend()
    if not backend or backend['kind'] != 'container':
        if not tectonic_path():
            if os.environ.get('ORIGINWRITE_TECTONIC'):
                raise ValueError('Configured ORIGINWRITE_TECTONIC is not executable.')
            install_tectonic()
        if sys.platform.startswith('linux') and not shutil.which(os.getenv('ORIGINWRITE_BWRAP', 'bwrap')):
            conda_install('bubblewrap')
        cache = (mac_cache_path() if sys.platform == 'darwin' else
                 Path(os.getenv('ORIGINWRITE_TECTONIC_CACHE') or RUNTIME / 'tex-cache').resolve())
        if cache in (Path('/'), Path.home(), ROOT):
            raise ValueError('Choose a dedicated TeX package cache.')
        cache.mkdir(parents=True, exist_ok=True)
        backend = select_compiler_backend()
        if not backend:
            raise ValueError('No isolated TeX backend is available. See docs/writing.md.')
        stamp = cache / '.originrag-templates-v1'
        if not stamp.exists():
            subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_writing_templates.py'),
                            '--warm-cache', '--tectonic', backend['executable']],
                           env={**os.environ, 'TECTONIC_CACHE_DIR': str(cache)}, check=True)
    files, main = template_files('article')
    result = compile_project({'files': files, 'main': main, 'revision': 1})
    if not result['ok']:
        if backend['kind'] != 'container':
            stamp.unlink(missing_ok=True)
        raise ValueError('Isolated test compile failed: ' + result['log'][-1200:] +
                         '\nSee docs/writing.md (Linux also needs permitted user namespaces or a configured container).')
    if backend['kind'] != 'container':
        stamp.touch()
    print('Quill: test manuscript compiled to PDF.', flush=True)


def tags(url):
    with urlopen(url.rstrip('/') + '/api/tags', timeout=4) as response:
        rows = json.load(response)['models']
    return {row['name'] for row in rows}


def prepare_model(*, start_only=False):
    url = os.getenv('ORIGINRAG_OLLAMA_BASE_URL') or 'http://127.0.0.1:11434'
    parsed = urlsplit(url)
    local = (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1'}
             and parsed.path in {'', '/'} and not parsed.username and not parsed.password)
    cli_env = {**os.environ, 'OLLAMA_HOST': f'127.0.0.1:{parsed.port or 80}'} if local else None
    try:
        installed = tags(url)
    except HTTPError as exc:
        raise ValueError(f'Configured model endpoint returned HTTP {exc.code}; check the Ollama URL.')
    except OSError:
        if not local:
            raise ValueError('Configured Ollama endpoint is unavailable; no remote host was changed.')
        if not shutil.which('ollama'):
            if start_only:
                raise ValueError('Ollama is missing. Rerun ./setup.sh.')
            conda_install('ollama')
        with (RUNTIME / 'ollama-startup.log').open('a') as log:
            proc = subprocess.Popen(['ollama', 'serve'], env=cli_env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(60):
            try:
                installed = tags(url)
                break
            except OSError:
                if proc.poll() is not None:
                    raise ValueError('Ollama did not start. See runtime/ollama-startup.log.')
                time.sleep(.5)
        else:
            raise ValueError('Ollama is not responding. See runtime/ollama-startup.log.')
    if start_only:
        return
    models = {os.getenv(k) for k in ('ORIGINRAG_QWEN_MODEL', 'ORIGINRAG_OLLAMA_MODEL',
                                     'ORIGINRAG_OLLAMA_PLANNER_MODEL', 'ORIGINRAG_OLLAMA_ANSWER_MODEL',
                                     'ORIGINRAG_QUEUE_MODEL')}
    for model in sorted(models - {None, ''} or {'qwen3:4b'}):
        if model not in installed and model + ':latest' not in installed:
            if not local:
                raise ValueError(f'Model {model} is missing on the configured remote host.')
            if not shutil.which('ollama'):
                conda_install('ollama')
            print('Downloading answer model: ' + model, flush=True)
            subprocess.run(['ollama', 'pull', model], env=cli_env, check=True)
            installed = tags(url)
            if model not in installed and model + ':latest' not in installed:
                raise ValueError('Downloaded model not listed by the configured Ollama service: ' + model)
        print('Answer model ready: ' + model, flush=True)
    if local:
        (RUNTIME / 'setup-ollama').touch()


def prepare_dictation():
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--only-binary=:all:',
                    'pywhispercpp>=1.5.1,<2'], check=True)
    subprocess.run([sys.executable, str(ROOT / 'src/originrag/dictation.py'), '--prepare'], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-only', action='store_true')
    parser.add_argument('--profile', choices=('full', 'write', 'rag'), default='full')
    args = parser.parse_args()
    load_project_env(ROOT / '.env')
    errors = []
    steps = []
    if not args.start_only and args.profile in ('full', 'write'):
        steps.append(('Quill PDF preview', prepare_tex))
    if args.profile in ('full', 'rag'):
        steps.append(('Ollama', lambda: prepare_model(start_only=args.start_only)))
        if not args.start_only:
            steps.append(('Chat dictation', prepare_dictation))
    for name, action in steps:
        try:
            action()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as exc:
            print(f'{name}: {exc}', file=sys.stderr, flush=True)
            errors.append(name)
    if errors:
        sys.exit('Not ready: ' + ', '.join(errors) + f'. Apps remain installed; rerun ./setup.sh --profile {args.profile} to retry.')


if __name__ == '__main__':
    main()
