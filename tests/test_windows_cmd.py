"""Exercise the Windows command processor; only the WSL executable is a test double."""
import os
from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='requires the Windows command processor')
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def launcher(tmp_path_factory):
    folder = tmp_path_factory.mktemp('Windows checkout (spaces)')
    stub = folder / 'FakeWsl.cs'
    stub.write_text('''
using System;
using System.IO;
class FakeWsl {
    static int Main(string[] args) {
        File.AppendAllText("calls.txt", String.Join("|", args) + Environment.NewLine);
        string mode = Environment.GetEnvironmentVariable("TEST_WSL_MODE");
        if (Array.IndexOf(args, "grep") >= 0) return mode == "default" ? 0 : 1;
        if (Array.IndexOf(args, "/bin/true") >= 0) return mode == "missing" ? 1 : 0;
        return mode == "broken" ? 17 : 0;
    }
}
''')
    compiler = Path(os.environ['SystemRoot']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    exe = folder / 'wsl.exe'
    subprocess.run([str(compiler), '/nologo', '/out:' + str(exe), str(stub)], check=True)
    script = (ROOT / 'OriginRAG.cmd').read_text()
    script = script.replace(r'%SystemRoot%\System32\wsl.exe', str(exe)).replace(
        r'%SystemRoot%\Sysnative\wsl.exe', str(folder / 'absent.exe'))
    (folder / 'OriginRAG.cmd').write_text(script)
    return folder


@pytest.mark.parametrize(('mode', 'distro', 'expected'), [
    ('default', '', 0), ('named', '', 0), ('named', 'Ubuntu-24.04', 0),
    ('missing', '', 1), ('broken', 'Ubuntu', 17),
])
def test_windows_starts_correct_guest_and_propagates_errors(launcher, mode, distro, expected):
    log = launcher / 'calls.txt'
    log.unlink(missing_ok=True)
    env = {**os.environ, 'TEST_WSL_MODE': mode, 'ORIGINRAG_WSL_DISTRO': distro}
    result = subprocess.run([os.environ['ComSpec'], '/d', '/c', 'OriginRAG.cmd'],
                            cwd=launcher, input='\n', capture_output=True, text=True, env=env, timeout=20)
    assert result.returncode == expected, result.stdout + result.stderr
    calls = log.read_text().splitlines()
    if mode == 'missing':
        assert 'wsl --install -d Ubuntu' in result.stdout
        assert not any('windows-workspace.sh' in row for row in calls)
    else:
        prefix = '' if mode == 'default' else '--distribution|' + (distro or 'Ubuntu') + '|'
        assert calls[-1] == prefix + '--cd|' + str(launcher) + r'\.' + '|--exec|bash|./scripts/windows-workspace.sh'
