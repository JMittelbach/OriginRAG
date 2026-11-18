import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('configure_app_profile', ROOT / 'scripts/configure_app_profile.py')
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


def test_profile_read_precedence_and_no_mutation(tmp_path, monkeypatch):
    monkeypatch.delenv(config.KEY, raising=False)
    assert config.read_profile(tmp_path) == 'full'
    assert not (tmp_path / '.env').exists()
    (tmp_path / '.env').write_text('export ORIGINRAG_APP_PROFILE="write" # personal\n')
    assert config.read_profile(tmp_path) == 'write'
    monkeypatch.setenv(config.KEY, 'rag')
    assert config.read_profile(tmp_path) == 'rag'
    assert config.read_profile(tmp_path, 'full') == 'full'
    monkeypatch.setenv(config.KEY, 'unknown')
    with pytest.raises(ValueError):
        config.read_profile(tmp_path)


def test_configuration_preserves_private_settings_and_permissions(tmp_path):
    source = '# My private configuration\nSECRET="a=b # token"\nORIGINRAG_BIB_DIR=/my/papers\n\nORIGINRAG_APP_PROFILE=full\nAUTH=value\n'
    path = tmp_path / '.env'
    path.write_text(source)
    path.chmod(0o640)
    config.configure(tmp_path, 'write')
    assert path.read_text() == source.replace('ORIGINRAG_APP_PROFILE=full', 'ORIGINRAG_APP_PROFILE=write')
    assert path.stat().st_mode & 0o777 == 0o640
    changed = path.stat().st_mtime_ns
    config.configure(tmp_path, 'write')
    assert path.stat().st_mtime_ns == changed


def test_fresh_configuration_is_private_and_uses_own_library(tmp_path):
    (tmp_path / '.env.example').write_text('ORIGINRAG_APP_PROFILE=full\nORIGINRAG_BIB_DIR=/placeholder\nOTHER=unchanged\n')
    config.configure(tmp_path, 'write')
    assert json.dumps(str(tmp_path / 'bib')) in (tmp_path / '.env').read_text()
    assert 'OTHER=unchanged' in (tmp_path / '.env').read_text()
    assert (tmp_path / '.env').stat().st_mode & 0o777 == 0o600


def test_symlink_configuration_is_not_overwritten(tmp_path):
    target = tmp_path / 'private-settings'
    target.write_text('SECRET=keep\n')
    (tmp_path / '.env').symlink_to(target)
    with pytest.raises(ValueError, match='symbolic link'):
        config.configure(tmp_path, 'write')
    assert target.read_text() == 'SECRET=keep\n'


def fixture_checkout(tmp_path):
    root = tmp_path / 'checkout'
    (root / 'scripts').mkdir(parents=True)
    for name in ('setup.sh', 'originrag', 'environment.yml', 'environment-write.yml', '.env.example'):
        shutil.copyfile(ROOT / name, root / name)
    shutil.copyfile(ROOT / 'scripts/configure_app_profile.py', root / 'scripts/configure_app_profile.py')
    return root


def conda_fixture(tmp_path, monkeypatch, *, exists=False):
    root = fixture_checkout(tmp_path)
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    log = tmp_path / 'commands.jsonl'
    fake = binaries / 'conda'
    fake.write_text(f'#!{sys.executable}\n' + '''import json, os, subprocess, sys
with open(os.environ['INSTALL_TEST_LOG'], 'a') as handle:
    handle.write(json.dumps(sys.argv[1:]) + '\\n')
args = sys.argv[1:]
if args[:2] == ['env', 'list']:
    print('custom-workspace /tmp/synthetic-environment' if os.environ.get('INSTALL_TEST_EXISTS') else 'base /tmp/synthetic-base')
elif args[:1] == ['run'] and 'scripts/configure_app_profile.py' in args:
    start = args.index('scripts/configure_app_profile.py')
    sys.exit(subprocess.call([sys.executable] + args[start:]))
''')
    fake.chmod(0o755)
    monkeypatch.setenv('PATH', str(binaries) + ':/usr/bin:/bin')
    monkeypatch.setenv('INSTALL_TEST_LOG', str(log))
    monkeypatch.setenv('ORIGINRAG_CONDA_ENV', 'custom-workspace')
    monkeypatch.delenv(config.KEY, raising=False)
    monkeypatch.delenv('INSTALL_TEST_EXISTS', raising=False)
    if exists:
        monkeypatch.setenv('INSTALL_TEST_EXISTS', '1')
    return root, log


def commands(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_setup_write_is_lean_and_honours_custom_environment(tmp_path, monkeypatch):
    root, log = conda_fixture(tmp_path, monkeypatch)
    result = subprocess.run(['/bin/bash', 'setup.sh', '--profile', 'write'], cwd=root, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    calls = commands(log)
    assert ['env', 'create', '-n', 'custom-workspace', '-f', 'environment-write.yml'] in calls
    assert ['run', '-n', 'custom-workspace', 'python', '-m', 'pip', 'install', '-e', '.[write]', '--no-deps'] in calls
    assert not any('scripts/init_local_data.py' in call or 'scripts/validate_metadata.py' in call for call in calls)
    assert not any('setup_local_qwen.sh' in str(call) or 'ollama' in str(call) for call in calls)
    assert config.read_profile(root) == 'write'


@pytest.mark.parametrize('flags', [[], ['--profile', 'full']])
def test_setup_repairs_partial_install_and_preserves_private_data(tmp_path, monkeypatch, flags):
    root, log = conda_fixture(tmp_path, monkeypatch, exists=True)
    (root / '.env').write_text('SECRET=retained\nORIGINRAG_APP_PROFILE=rag\n')
    monkeypatch.setenv(config.KEY, 'rag')
    result = subprocess.run(['/bin/bash', 'setup.sh', *flags], cwd=root, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert ['env', 'update', '-n', 'custom-workspace', '-f', 'environment.yml'] in commands(log)
    assert not any('--prune' in call for call in commands(log))
    assert 'SECRET=retained' in (root / '.env').read_text()
    assert 'ORIGINRAG_APP_PROFILE=full' in (root / '.env').read_text()
    assert any('scripts/setup_workspace_runtimes.py' in call for call in commands(log))
    assert any('scripts/verify_workspace_install.py' in call for call in commands(log))


def test_apps_only_explicitly_skips_runtimes_without_disabling_apps(tmp_path, monkeypatch):
    root, log = conda_fixture(tmp_path, monkeypatch)
    result = subprocess.run(['/bin/bash', 'setup.sh', '--apps-only'], cwd=root)
    assert result.returncode == 0
    assert config.read_profile(root) == 'full'
    assert not any('scripts/setup_workspace_runtimes.py' in call for call in commands(log))


@pytest.mark.parametrize('profile', ['rag', 'full'])
def test_setup_rag_profiles_install_extra_and_initialise_catalogue(tmp_path, monkeypatch, profile):
    root, log = conda_fixture(tmp_path, monkeypatch)
    result = subprocess.run(['/bin/bash', 'setup.sh', '--profile=' + profile, '--no-checks'], cwd=root, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    calls = commands(log)
    assert ['env', 'create', '-n', 'custom-workspace', '-f', 'environment.yml'] in calls
    assert any('scripts/init_local_data.py' in call for call in calls)
    assert any('.[' + profile + ']' in call for call in calls)


def test_invalid_profile_does_not_start_environment_installation(tmp_path, monkeypatch):
    root, log = conda_fixture(tmp_path, monkeypatch)
    result = subprocess.run(['/bin/bash', 'setup.sh', '--profile', 'not-a-profile'], cwd=root, text=True, capture_output=True)
    assert result.returncode != 0
    assert not log.exists()


@pytest.mark.parametrize('arguments', [['web', '--profile', 'write', '--auth'], []])
def test_write_launcher_does_not_require_ollama_yaml_or_rag_initialisation(tmp_path, monkeypatch, arguments):
    root = fixture_checkout(tmp_path)
    (root / '.env').write_text('ORIGINRAG_APP_PROFILE=write\n')
    log = tmp_path / 'python-calls.jsonl'
    fake = tmp_path / 'python'
    fake.write_text(f'#!{sys.executable}\n' + '''import json, os, subprocess, sys
with open(os.environ['INSTALL_TEST_LOG'], 'a') as handle:
    handle.write(json.dumps(sys.argv[1:]) + '\\n')
if 'scripts/configure_app_profile.py' in sys.argv:
    sys.exit(subprocess.call([sys.executable] + sys.argv[1:]))
''')
    fake.chmod(0o755)
    monkeypatch.setenv('ORIGINRAG_PYTHON', str(fake))
    monkeypatch.setenv('INSTALL_TEST_LOG', str(log))
    monkeypatch.delenv(config.KEY, raising=False)
    result = subprocess.run(['/bin/bash', 'originrag', *arguments], cwd=root, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    calls = commands(log)
    assert any('scripts/start_web_chat.py' in call for call in calls)
    assert not any('yaml' in str(call) or 'init_local_data' in str(call) for call in calls)
    assert 'Ollama' not in result.stdout + result.stderr


def test_dependency_profiles_keep_write_lean_and_dev_complete():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
    assert set(project['dependencies']) == {'python-dotenv', 'pymupdf'}
    assert project['optional-dependencies']['write'] == []
    for name in ('rag', 'full', 'dev'):
        assert 'pandas' in project['optional-dependencies'][name]
        assert 'pyyaml' in project['optional-dependencies'][name]
    assert 'pytest' in project['optional-dependencies']['dev']
    write_environment = (ROOT / 'environment-write.yml').read_text()
    assert 'pandas' not in write_environment and 'pydantic' not in write_environment


@pytest.mark.parametrize('extra,expected', [([], 'full'), (['--profile', 'write'], 'write')])
def test_web_uses_saved_full_profile_over_stale_shell_but_honours_cli(tmp_path, monkeypatch, extra, expected):
    root = fixture_checkout(tmp_path)
    (root / '.env').write_text('ORIGINRAG_APP_PROFILE=full\n')
    (root / 'runtime').mkdir()
    (root / 'runtime/setup-python').write_text(sys.executable + '\n')
    (root / 'scripts/start_web_chat.py').write_text(
        'import argparse\np = argparse.ArgumentParser()\np.add_argument("--profile")\nprint(p.parse_args().profile)\n')
    monkeypatch.delenv('ORIGINRAG_PYTHON', raising=False)
    monkeypatch.delenv('ORIGINRAG_CONDA_ENV', raising=False)
    monkeypatch.setenv(config.KEY, 'rag')
    monkeypatch.setenv('VIRTUAL_ENV', str(tmp_path / 'unrelated-venv'))
    result = subprocess.run(['/bin/bash', 'originrag', 'web', *extra], cwd=root, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
