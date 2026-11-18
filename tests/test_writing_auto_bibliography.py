import sqlite3
import os

import pytest

from originrag.writing import STARTER, WritingStore


CATALOGUE = [
    {'paper_id': 'P1', 'title': 'First result', 'author': 'Doe, Jane', 'year': '2026', 'journal': 'Journal'},
    {'paper_id': 'P2', 'title': 'Second result', 'author': 'Doe, John', 'year': '2026', 'journal': 'Journal'},
]


def test_whole_library_is_opt_in_stable_and_refreshes_beyond_collection_limit(tmp_path):
    store = WritingStore(tmp_path)
    rows = [{'paper_id': f'P{i}', 'author': f'Author{i}', 'year': '2024', 'title': f'Study {i}'} for i in range(620)]
    p = store.change({'action': 'create_project', 'name': 'Manuscript'}, rows)
    assert not p['files']['references.bib']
    link = dict(action='configure_bibliography', whole_library=True, enabled=True, filename='refs/all.bib')
    p = store.change({**p, **link}, rows)
    assert p['files']['refs/all.bib'].count('@article{') == 620
    assert store.refresh_bibliography(p['id'], rows)['revision'] == p['revision']
    rows.append({'paper_id': 'New', 'author': 'Author0', 'year': '2024', 'title': 'Follow-up'})
    updated = store.refresh_bibliography(p['id'], rows)
    assert '@article{Author02024a,' in updated['files']['refs/all.bib']
    p = store.change({**updated, **link, 'enabled': False, 'whole_library': False}, rows)
    rows.append({'paper_id': 'Later', 'title': 'Not linked'})
    assert store.refresh_bibliography(p['id'], rows)['files'] == updated['files']


def setup(store, papers=('P1',), automatic=True):
    collection = store.change({'action': 'create_collection', 'name': 'Research library'}, CATALOGUE)
    for paper in papers:
        collection = store.change({**collection, 'action': 'collection_paper', 'paper_id': paper}, CATALOGUE)
    project = store.change({'action': 'create_project', 'name': 'Automatic manuscript', 'collection': collection['id'],
                            'bibliography_auto': automatic}, CATALOGUE)
    return project, collection


def save(store, project, files, **extra):
    return store.change({**project, 'action': 'save_project', 'files': files, **extra}, CATALOGUE)


def test_link_populates_bibliography_and_noop_refresh_never_bumps_revision(tmp_path):
    store = WritingStore(tmp_path)
    project, collection = setup(store)
    assert project['bibliography_status']['state'] == 'synced'
    assert '@article{Doe2026,' in project['files']['references.bib']
    assert project['files']['main.tex'] == STARTER
    assert project['bibliography_sync']['references.bib']['collection_revision'] == collection['revision']
    for _ in range(5):
        assert store.refresh_bibliography(project['id'], CATALOGUE) == project


def test_empty_library_does_not_activate_an_empty_bibtex_run(tmp_path):
    store = WritingStore(tmp_path)
    project, collection = setup(store, papers=())
    assert project['files']['main.tex'] == STARTER
    assert project['files']['references.bib'] == ''
    assert project['bibliography_status']['state'] == 'synced'
    store.change({**collection, 'action': 'collection_paper', 'paper_id': 'P1'}, CATALOGUE)
    updated = store.get(project['id'])
    assert updated['files']['main.tex'] == STARTER
    updated = save(store, updated, {**updated['files'], 'main.tex': STARTER.replace('Your manuscript starts here.', 'A citation \\cite{Doe2026}.')})
    assert '\n\\bibliography{references}' in updated['files']['main.tex']


@pytest.mark.skipif(os.environ.get('ORIGINWRITE_TEST_COMPILER') != '1', reason='Requires isolated local compiler')
@pytest.mark.parametrize('papers', [(), ('P1',)])
def test_linked_library_without_citations_compiles(tmp_path, papers):
    from originrag.writing_compile import compile_project
    project, _ = setup(WritingStore(tmp_path), papers=papers)
    result = compile_project(project)
    assert result['ok'], result['log']


def test_membership_updates_closed_projects_but_not_other_accounts_or_manual_projects(tmp_path):
    store = WritingStore(tmp_path / 'alice')
    first, collection = setup(store)
    second = store.change({'action': 'create_project', 'name': 'Second manuscript', 'collection': collection['id']}, CATALOGUE)
    manual = store.change({'action': 'create_project', 'name': 'Manual manuscript', 'collection': collection['id'], 'bibliography_auto': False}, CATALOGUE)
    bob = WritingStore(tmp_path / 'bob')
    unrelated, _ = setup(bob)
    store.change({**collection, 'action': 'collection_paper', 'paper_id': 'P2'}, CATALOGUE)
    for prior in (first, second):
        current = store.get(prior['id'])
        assert 'Doe2026a' in current['files']['references.bib']
        assert current['revision'] == prior['revision'] + 1
    assert store.get(manual['id']) == manual
    assert bob.get(unrelated['id']) == unrelated


def test_metadata_refresh_stable_keys_manual_entries_and_removed_citations(tmp_path):
    store = WritingStore(tmp_path)
    project, collection = setup(store)
    manual = '@book{manual, title={Keep me}, author={Editor, A.}, year={1999}}\n'
    project = save(store, project, {**project['files'], 'references.bib': manual + project['files']['references.bib']})
    enriched = [{**CATALOGUE[0], 'title': 'Corrected first result', 'year': '2027'}, CATALOGUE[1]]
    current = store.refresh_bibliography(project['id'], enriched)
    assert current['files']['references.bib'].startswith(manual)
    assert '@article{Doe2026,' in current['files']['references.bib']
    assert 'Corrected first result' in current['files']['references.bib']
    store.change({**collection, 'action': 'collection_paper', 'paper_id': 'P1', 'remove': True}, enriched)
    removed = store.get(project['id'])
    assert 'Corrected first result' in removed['files']['references.bib']
    assert removed['files']['references.bib'].startswith(manual)


def test_manual_edits_raise_visible_conflict_without_blocking_other_safe_additions(tmp_path):
    store = WritingStore(tmp_path)
    project, collection = setup(store)
    altered = project['files']['references.bib'].replace('First result', 'My editorial correction')
    project = save(store, project, {**project['files'], 'references.bib': altered})
    assert project['bibliography_status']['state'] == 'needs_review'
    assert project['bibliography_status']['conflicts'] == ['Doe2026']
    store.change({**collection, 'action': 'collection_paper', 'paper_id': 'P2'}, CATALOGUE)
    current = store.get(project['id'])
    assert 'My editorial correction' in current['files']['references.bib']
    assert '@article{Doe2026a,' in current['files']['references.bib']
    assert store.export(project['id']).startswith(b'PK')  # Recovery exports remain available.
    restored = save(store, current, {**current['files'], 'references.bib': current['files']['references.bib'].replace('My editorial correction', 'First result')})
    assert restored['bibliography_status']['state'] == 'synced'


def test_missing_metadata_and_malformed_bib_are_never_silently_erased(tmp_path):
    store = WritingStore(tmp_path)
    project, _ = setup(store)
    missing = store.refresh_bibliography(project['id'], [])
    assert missing['files'] == project['files']
    assert missing['bibliography_status']['missing_count'] == 1
    project = store.refresh_bibliography(project['id'], CATALOGUE)
    broken = '@article{Doe2026, title={Unfinished'
    current = save(store, project, {**project['files'], 'references.bib': broken})
    assert current['files']['references.bib'] == broken
    assert current['bibliography_status']['state'] == 'needs_review'


def test_user_draft_merges_after_background_membership_change(tmp_path):
    store = WritingStore(tmp_path)
    project, collection = setup(store)
    base = {k: project[k] for k in ('files', 'main', 'collection', 'revision')}
    store.change({**collection, 'action': 'collection_paper', 'paper_id': 'P2'}, CATALOGUE)
    edited = project['files']['main.tex'].replace('Your manuscript starts here.', 'My new unsaved paragraph.')
    current = save(store, project, {**project['files'], 'main.tex': edited}, base=base)
    assert 'My new unsaved paragraph.' in current['files']['main.tex']
    assert 'Doe2026a' in current['files']['references.bib']
    assert current['bibliography_status']['state'] == 'synced'


def test_single_pass_catalogue_updates_every_linked_manuscript(tmp_path):
    store = WritingStore(tmp_path)
    first, collection = setup(store)
    second = store.change({'action': 'create_project', 'name': 'Second', 'collection': collection['id']}, CATALOGUE)
    store.change({**collection, 'action': 'collection_paper', 'paper_id': 'P2'}, iter(CATALOGUE))
    assert 'Doe2026a' in store.get(first['id'])['files']['references.bib']
    assert 'Doe2026a' in store.get(second['id'])['files']['references.bib']


@pytest.mark.parametrize('source,target', [
    ('\\begin{document}\\bibliography{custom}\\end{document}', 'custom.bib'),
    ('\\usepackage{biblatex}\\addbibresource{refs/papers.bib}\\begin{document}\\printbibliography\\end{document}', 'refs/papers.bib'),
])
def test_existing_bibliography_target_is_respected(tmp_path, source, target):
    store = WritingStore(tmp_path)
    project, collection = setup(store, automatic=False)
    project = save(store, project, {'main.tex': source})
    project = store.change({**project, 'action': 'configure_bibliography', 'enabled': True, 'filename': target}, CATALOGUE)
    assert project['files']['main.tex'] == source
    assert project['bibliography_target'] == target
    assert 'Doe2026' in project['files'][target]
    assert project['bibliography_status']['state'] == 'synced'


def test_multiple_targets_require_explicit_choice_and_manual_mode_is_reversible(tmp_path):
    store = WritingStore(tmp_path)
    project = store.change({'action': 'create_project', 'name': 'Custom'}, CATALOGUE)
    _, collection = setup(store)
    project = save(store, project, {'main.tex': '\\begin{document}\\bibliography{first,second}\\end{document}'}, collection=collection['id'])
    assert project['bibliography_status']['state'] == 'needs_review'
    assert 'references.bib' not in project['files']
    selected = store.change({**project, 'action': 'configure_bibliography', 'enabled': True, 'filename': 'second.bib'}, CATALOGUE)
    assert selected['bibliography_status']['state'] == 'synced'
    assert 'Doe2026' in selected['files']['second.bib']
    disabled = store.change({**selected, 'action': 'configure_bibliography', 'enabled': False, 'filename': 'second.bib'}, CATALOGUE)
    assert disabled['files'] == selected['files'] and 'bibliography_status' not in disabled


def test_storage_failure_rolls_back_membership_and_all_manuscripts(tmp_path, monkeypatch):
    store = WritingStore(tmp_path)
    project, collection = setup(store)
    remember = store._remember_summary
    def fail_project(db, key, revision, data):
        if 'files' in data:
            raise sqlite3.OperationalError('Simulated storage failure')
        remember(db, key, revision, data)
    monkeypatch.setattr(store, '_remember_summary', fail_project)
    with pytest.raises(sqlite3.OperationalError):
        store.change({**collection, 'action': 'collection_paper', 'paper_id': 'P2'}, CATALOGUE)
    assert store.get(project['id']) == project
    assert store.get(collection['id'], 'collections') == collection


def test_project_size_limit_reports_problem_without_losing_draft(tmp_path):
    store = WritingStore(tmp_path)
    project, _ = setup(store)
    source = STARTER + 'x' * (12_000_000 - len(STARTER) - 1)
    current = save(store, project, {'main.tex': source})
    assert current['files']['main.tex'] == source
    assert 'references.bib' not in current['files']
    assert current['bibliography_status']['state'] == 'needs_review'
    assert '12 MB' in current['bibliography_status']['message']
