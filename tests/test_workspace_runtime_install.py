import importlib.util
import sys

import pytest

from scripts import setup_workspace_runtimes as setup
from scripts import verify_workspace_install


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, 'RUNTIME', tmp_path)
    monkeypatch.setenv('ORIGINRAG_OLLAMA_BASE_URL', 'http://127.0.0.1:29292')
    for key in ('QWEN_MODEL', 'OLLAMA_MODEL', 'OLLAMA_PLANNER_MODEL', 'OLLAMA_ANSWER_MODEL', 'QUEUE_MODEL'):
        monkeypatch.delenv('ORIGINRAG_' + key, raising=False)
    return tmp_path


def test_download_only_missing_and_keep_existing_models(local, monkeypatch):
    monkeypatch.setenv('ORIGINRAG_OLLAMA_ANSWER_MODEL', 'custom:small')
    monkeypatch.setenv('ORIGINRAG_OLLAMA_PLANNER_MODEL', 'already:latest')
    installed = {'already:latest'}
    monkeypatch.setattr(setup, 'tags', lambda _: installed)
    monkeypatch.setattr(setup.shutil, 'which', lambda _: '/bin/ollama')
    calls = []
    def pull(args, *, env, check):
        assert env['OLLAMA_HOST'] == '127.0.0.1:29292' and check
        calls.append(args)
        installed.add(args[-1])
    monkeypatch.setattr(setup.subprocess, 'run', pull)
    setup.prepare_model()
    setup.prepare_model()
    assert calls == [['ollama', 'pull', 'custom:small']]
    assert (local / 'setup-ollama').exists()


def test_remote_is_never_started_or_modified(local, monkeypatch):
    monkeypatch.setenv('ORIGINRAG_OLLAMA_BASE_URL', 'https://models.example.org')
    monkeypatch.setattr(setup, 'tags', lambda _: set())
    monkeypatch.setattr(setup.subprocess, 'run', lambda *a, **kw: pytest.fail('unexpected remote mutation'))
    with pytest.raises(ValueError, match='missing on the configured remote'):
        setup.prepare_model()
    assert not (local / 'setup-ollama').exists()


def test_web_restart_never_downloads_weights(local, monkeypatch):
    monkeypatch.setattr(setup, 'tags', lambda _: set())
    monkeypatch.setattr(setup.subprocess, 'run', lambda *a, **kw: pytest.fail('unexpected download'))
    setup.prepare_model(start_only=True)


def test_full_install_verifies_bundled_editors_before_success(tmp_path):
    if not importlib.util.find_spec('fitz'):
        pytest.skip('requires the installed project')
    (tmp_path / '.env').write_text('ORIGINRAG_APP_PROFILE=rag\n')
    with pytest.raises(SystemExit, match='saved application profile'):
        verify_workspace_install.verify(tmp_path, 'full')
    (tmp_path / '.env').write_text('ORIGINRAG_APP_PROFILE=full\n')
    for name in ['workspace/workspace.mjs', 'workspace/latex-editor.mjs', 'folio/editor.mjs']:
        asset = tmp_path / 'assets' / name
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.touch()
    verify_workspace_install.verify(tmp_path, 'full')
    assert (tmp_path / 'runtime/setup-python').read_text().strip() == sys.executable
    (tmp_path / 'assets/folio/editor.mjs').unlink()
    with pytest.raises(SystemExit, match='Missing bundled editor: folio'):
        verify_workspace_install.verify(tmp_path, 'full')


def test_unavailable_compiler_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(setup, 'select_compiler_backend', lambda: {'kind': 'container'})
    monkeypatch.setattr(setup, 'compile_project', lambda *_: {'ok': False, 'log': 'container unavailable'})
    with pytest.raises(ValueError, match='Isolated test compile failed: container unavailable'):
        setup.prepare_tex()
