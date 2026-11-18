import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/windows-workspace.sh'
pytestmark = pytest.mark.skipif(os.name == 'nt', reason='guest launcher runs inside Ubuntu')


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


@pytest.fixture
def launch(tmp_path):
    source = tmp_path / 'Windows clone (with spaces)'
    workspace = tmp_path / 'Ubuntu home' / 'OriginRAG'
    workspace.parent.mkdir()
    (source / 'scripts').mkdir(parents=True)
    (source / 'scripts/setup_workspace_runtimes.py').touch()
    (source / 'scripts/configure_app_profile.py').write_text('print("full")')
    (source / 'setup.sh').write_text(
        '#!/bin/bash\nset -eu\necho "$*" >> setup-calls\n'
        'test -z "${FAIL_SETUP:-}"\nprintf "%s\\n" "$TEST_PYTHON" > runtime/setup-python\n'
        'touch .env\n')
    (source / 'web').write_text('#!/bin/bash\necho "$*|$ORIGINRAG_WEB_OPEN|${BROWSER:-}" >> web-calls\n')
    for filename in ['setup.sh', 'web']:
        (source / filename).chmod(0o755)
    git(source, 'init', '-q')
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-qm', 'source')
    (source / 'private-paper.pdf').write_bytes(b'private')
    tools = tmp_path / 'tools'
    tools.mkdir()
    for tool in ['rundll32.exe', *([] if shutil.which('flock') else ['flock'])]:
        (tools / tool).write_text('#!/bin/sh\nexit 0\n')
        (tools / tool).chmod(0o755)

    def run(**env):
        return subprocess.run(
            ['bash', '-c', 'source "$1"; start_workspace "$2" "$3"', 'test',
             str(SCRIPT), str(source), str(workspace)], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, env={**os.environ, 'TEST_PYTHON': sys.executable,
            'PATH': str(tools) + ':' + os.environ['PATH'], **env})
    return workspace, run


def test_clone_and_reopen_same_full_workspace(launch):
    workspace, run = launch
    first = run()
    assert first.returncode == 0, first.stderr
    assert git(workspace, 'remote', 'get-url', 'origin') == 'https://github.com/JMittelbach/OriginRAG.git'
    assert not (workspace / 'private-paper.pdf').exists()
    (workspace / 'my-paper.pdf').write_bytes(b'mine')
    assert run().returncode == 0
    assert (workspace / 'my-paper.pdf').read_bytes() == b'mine'
    assert (workspace / 'setup-calls').read_text().splitlines() == ['--profile full']
    assert (workspace / 'web-calls').read_text().splitlines() == [
        '--host 127.0.0.1 --strict-port|1|rundll32.exe url.dll,FileProtocolHandler %s &'] * 2


def test_incomplete_install_retries_in_place(launch):
    workspace, run = launch
    assert run(FAIL_SETUP='1').returncode != 0
    assert not (workspace / 'runtime/windows-full-ready').exists()
    assert not (workspace / 'web-calls').exists()
    assert run().returncode == 0
    assert len((workspace / 'setup-calls').read_text().splitlines()) == 2


def test_unrelated_existing_folder_left_untouched(launch):
    workspace, run = launch
    workspace.mkdir()
    (workspace / 'my-notes').write_text('keep')
    assert run().returncode != 0
    assert [p.name for p in workspace.iterdir()] == ['my-notes']
