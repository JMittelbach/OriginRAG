from originrag import writing_builds as builds


def test_build_reuse_is_account_project_content_and_configuration_scoped(tmp_path, monkeypatch):
    builds._CACHE.clear()
    calls = []

    def run(project):
        calls.append(project)
        return {'ok': True, 'pdf': b'%PDF-test', 'log': '', 'diagnostics': [], 'seconds': 2, 'revision': project['revision']}

    monkeypatch.setattr(builds, 'compile_project', run)
    project = {'id': 'p1', 'main': 'main.tex', 'files': {'main.tex': 'Hello'}, 'revision': 1}
    first = builds.compile_saved_project(project, tmp_path / 'alice')
    assert not first['cached']
    hit = builds.compile_saved_project({**project, 'revision': 2}, tmp_path / 'alice')
    assert hit['cached'] and hit['revision'] == 2 and hit['build_seconds'] == 2
    hit['diagnostics'].append('must not mutate cache')
    assert not builds.compile_saved_project(project, tmp_path / 'alice')['diagnostics']
    builds.compile_saved_project(project, tmp_path / 'bob')
    builds.compile_saved_project({**project, 'id': 'p2'}, tmp_path / 'alice')
    builds.compile_saved_project({**project, 'files': {'main.tex': 'Changed'}}, tmp_path / 'alice')
    monkeypatch.setenv('ORIGINWRITE_TECTONIC_CACHE', '/different/compiler/cache')
    builds.compile_saved_project(project, tmp_path / 'alice')
    assert len(calls) == 5


def test_failed_builds_not_cached_and_cache_is_bounded(tmp_path, monkeypatch):
    builds._CACHE.clear()
    calls = []

    def run(project):
        calls.append(1)
        return {'ok': False, 'pdf': b'', 'seconds': 1}

    monkeypatch.setattr(builds, 'compile_project', run)
    project = {'id': 'p', 'main': 'main.tex', 'files': {'main.tex': ''}, 'revision': 1}
    builds.compile_saved_project(project, tmp_path)
    builds.compile_saved_project(project, tmp_path)
    assert len(calls) == 2 and not builds._CACHE
    monkeypatch.setattr(builds, 'compile_project', lambda _: {'ok': True, 'pdf': b'%PDF', 'seconds': 1})
    monkeypatch.setattr(builds, '_MAX_ITEMS', 2)
    for n in range(5):
        builds.compile_saved_project({**project, 'id': str(n)}, tmp_path)
    assert len(builds._CACHE) == 2
