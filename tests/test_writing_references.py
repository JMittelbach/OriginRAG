import hashlib
import json

import pytest

from originrag.writing import (
    WritingConflict, WritingStore, bibtex_entries, citation_key, reference_entry,
)


CATALOGUE = [
    {'paper_id': 'P1', 'title': 'Known first result', 'author': 'Ada Example', 'year': '2026', 'doi': '10.1/first'},
    {'paper_id': 'P2', 'title': 'Another result', 'author': 'Ben Example', 'year': '2025'},
]


def prepared(tmp_path, paper_ids=('P1',)):
    store = WritingStore(tmp_path)
    collection = store.change({'action': 'create_collection', 'name': 'My manuscript'}, CATALOGUE)
    for paper_id in paper_ids:
        collection = store.change({**collection, 'action': 'collection_paper', 'paper_id': paper_id}, CATALOGUE)
    project = store.change({'action': 'create_project', 'name': 'Manuscript', 'bibliography_auto': False}, CATALOGUE)
    project = store.change({**project, 'action': 'save_project', 'collection': collection['id']}, CATALOGUE)
    return store, project, collection


def sync_payload(preview):
    return {'action': 'sync_bibliography', 'id': preview['project_id'], 'revision': preview['project_revision'],
            'collection_revision': preview['collection_revision'], 'preview_token': preview['preview_token'],
            'filename': preview['filename'], 'connect_document': preview['connect_document']}


def save_file(store, project, name, content):
    return store.change({**project, 'action': 'save_project', 'files': {**project['files'], name: content}}, CATALOGUE)


def test_stable_keys_and_structured_authors():
    paper = {**CATALOGUE[0], 'authors': [{'family': 'Müller', 'given': 'Ada'}, {'name': 'A & B Consortium'}],
             'volume': '12', 'issue': '3', 'pages': '1--8', 'title': r'PfEMP1 {quoted} \input{secret}'}
    assert citation_key(paper) == 'Muller2026'
    entry = reference_entry(paper)
    assert r'author = {Müller, Ada and {A \& B Consortium}}' in entry
    assert 'volume = {12}' in entry and 'number = {3}' in entry and 'pages = {1--8}' in entry
    assert r'\textbackslash{}input\{secret\}' in entry
    assert list(bibtex_entries(entry)) == [citation_key(paper)]


def test_full_author_json_and_document_type():
    paper = {**CATALOGUE[0], 'full_authors': json.dumps([{'family': 'Jones', 'given': 'B.'}, 'Lee, A.']),
             'document_type': 'book-chapter'}
    entry = reference_entry(paper)
    assert entry.startswith('@incollection{')
    assert 'author = {Jones, B. and Lee, A.}' in entry


@pytest.mark.parametrize('author,expected', [('Jane Doe', 'Doe2026'), ('Doe, Jane', 'Doe2026'),
    ('Doe J', 'Doe2026'), ('Müller A.', 'Muller2026'), ('Doe et al.', 'Doe2026'), ('', 'Unknown2026')])
def test_readable_author_year_keys(author, expected):
    assert citation_key({'paper_id': 'paper', 'author': author, 'year': '2026'}) == expected


def test_keys_persist_across_collections_metadata_changes_and_collisions(tmp_path):
    catalogue = [{**CATALOGUE[0], 'author': 'Jane Doe'}, {**CATALOGUE[1], 'author': 'Doe J', 'year': '2026'}]
    store, project, collection = prepared(tmp_path, ('P1', 'P2'))
    first = store.collection_references(collection['id'], catalogue)
    assert [r['key'] for r in first['references']] == ['Doe2026', 'Doe2026a']
    # No re-keying when metadata is corrected or a library is reordered.
    changed = [{**p, 'year': '2027', 'author': 'New name'} for p in catalogue]
    second = WritingStore(tmp_path).collection_references(collection['id'], changed)
    assert [r['key'] for r in second['references']] == ['Doe2026', 'Doe2026a']
    preview = store.bibliography_preview(project['id'], changed)
    assert [r['key'] for r in preview['added']] == ['Doe2026', 'Doe2026a']


def test_collisions_past_z_are_alphabetical_and_case_insensitive(tmp_path):
    catalogue = [{'paper_id': f'P{i:03}', 'author': 'Doe', 'year': '2026', 'title': f'Study {i}'} for i in range(30)]
    store = WritingStore(tmp_path)
    collection = store.change({'action': 'create_collection', 'name': 'Many'}, catalogue)
    for paper in catalogue:
        collection = store.change({**collection, 'action': 'collection_paper', 'paper_id': paper['paper_id']}, catalogue)
    keys = [r['key'] for r in store.collection_references(collection['id'], catalogue)['references']]
    assert len(set(k.casefold() for k in keys)) == 30
    assert keys[:2] == ['Doe2026', 'Doe2026a'] and keys[27] == 'Doe2026aa'


def test_existing_legacy_keys_remain_citable(tmp_path):
    store, project, collection = prepared(tmp_path)
    legacy = 'paper' + hashlib.sha256(b'P1').hexdigest()[:12]
    entry = reference_entry(CATALOGUE[0], legacy)
    project = save_file(store, project, 'references.bib', entry)
    project = save_file(store, project, 'main.tex', project['files']['main.tex'] + '\n\\cite{' + legacy + '}')
    refs = store.collection_references(collection['id'], CATALOGUE)
    assert refs['references'][0]['key'] == legacy
    preview = store.bibliography_preview(project['id'], CATALOGUE)
    assert len(preview['unchanged']) == 1 and not preview['added']
    assert store.get(project['id'])['files'] == project['files']


def test_export_checks_the_saved_revision(tmp_path):
    store, project, _ = prepared(tmp_path)
    assert store.export(project['id'], expected_revision=project['revision']).startswith(b'PK')
    with pytest.raises(WritingConflict, match='before export'):
        store.export(project['id'], expected_revision=0)


def test_collection_references_order_missing_metadata_and_private_rename(tmp_path):
    store, project, collection = prepared(tmp_path / 'alice', ('P2', 'P1'))
    refs = store.collection_references(collection['id'], CATALOGUE[:1])
    assert refs['missing'] == ['P2']
    assert refs['references'][0]['key'] == citation_key(CATALOGUE[0])
    assert refs['references'][0]['warnings']
    renamed = store.change({**collection, 'action': 'rename_collection', 'name': 'Final references'}, CATALOGUE)
    assert renamed['name'] == 'Final references' and renamed['papers'] == ['P2', 'P1']
    with pytest.raises(WritingConflict):
        store.change({**collection, 'action': 'rename_collection', 'name': 'Stale'}, CATALOGUE)
    bob = WritingStore(tmp_path / 'bob')
    with pytest.raises(KeyError):
        bob.collection_references(collection['id'], CATALOGUE)
    with pytest.raises(KeyError):
        bob.bibliography_preview(project['id'], CATALOGUE)
    preview = store.bibliography_preview(project['id'], CATALOGUE)
    with pytest.raises(KeyError):
        bob.change(sync_payload(preview), CATALOGUE)


def test_preview_is_read_only_sync_is_explicit_and_idempotent(tmp_path):
    store, project, collection = prepared(tmp_path)
    preview = store.bibliography_preview(project['id'], CATALOGUE)
    assert len(preview['added']) == 1 and not preview['conflicts']
    assert store.get(project['id']) == project
    first = store.change(sync_payload(preview), CATALOGUE)
    assert first['files']['references.bib'] == preview['bibtex']
    assert first['revision'] == project['revision'] + 1
    again = store.bibliography_preview(first['id'], CATALOGUE)
    assert len(again['unchanged']) == 1 and again['added'] == []
    second = store.change(sync_payload(again), CATALOGUE)
    assert second['files'] == first['files']
    store.change({**collection, 'action': 'collection_paper', 'paper_id': 'P2'}, CATALOGUE)
    assert store.get(project['id']) == second


def test_manual_entries_and_directives_preserved_byte_for_byte(tmp_path):
    store, project, _ = prepared(tmp_path)
    manual = '% A manual header\n@string{J = "Journal"}\n@preamble{"Text"}\n@comment{Keep {all} comments}\n@article(manual, title = "A } title", journal = J)\n'
    project = save_file(store, project, 'references.bib', manual)
    preview = store.bibliography_preview(project['id'], CATALOGUE)
    assert preview['bibtex'].startswith(manual)
    assert preview['retained'] == ['manual']
    synced = store.change(sync_payload(preview), CATALOGUE)
    assert synced['files']['references.bib'].startswith(manual)


def test_unchanged_generated_metadata_can_update_only_on_request(tmp_path):
    store, project, _ = prepared(tmp_path)
    first = store.change(sync_payload(store.bibliography_preview(project['id'], CATALOGUE)), CATALOGUE)
    enriched = [{**CATALOGUE[0], 'title': 'Corrected title'}, CATALOGUE[1]]
    preview = store.bibliography_preview(project['id'], enriched)
    assert len(preview['updated']) == 1 and not preview['conflicts']
    assert store.get(first['id']) == first
    updated = store.change(sync_payload(preview), enriched)
    assert 'Corrected title' in updated['files']['references.bib']


@pytest.mark.parametrize('already_managed', [False, True])
def test_manual_key_collision_and_edited_generated_entry_block_overwrite(tmp_path, already_managed):
    store, project, _ = prepared(tmp_path)
    if already_managed:
        project = store.change(sync_payload(store.bibliography_preview(project['id'], CATALOGUE)), CATALOGUE)
    custom = reference_entry(CATALOGUE[0]).replace('Known first result', 'My manual correction')
    project = save_file(store, project, 'references.bib', custom)
    preview = store.bibliography_preview(project['id'], CATALOGUE)
    assert len(preview['conflicts']) == 1
    assert preview['bibtex'] == custom
    with pytest.raises(WritingConflict, match='manual changes'):
        store.change(sync_payload(preview), CATALOGUE)
    assert store.get(project['id']) == project


def test_removed_collection_items_remain_in_bib_and_citations(tmp_path):
    store, project, collection = prepared(tmp_path, ('P1', 'P2'))
    project = store.change(sync_payload(store.bibliography_preview(project['id'], CATALOGUE)), CATALOGUE)
    cited = '\\cite{' + citation_key(CATALOGUE[0]) + '}\n' + project['files']['main.tex']
    project = save_file(store, project, 'main.tex', cited)
    store.change({**collection, 'action': 'collection_paper', 'paper_id': 'P1', 'remove': True}, CATALOGUE)
    preview = store.bibliography_preview(project['id'], CATALOGUE)
    assert preview['retained'] == [citation_key(CATALOGUE[0])]
    updated = store.change(sync_payload(preview), CATALOGUE)
    assert updated['files'] == project['files']


@pytest.mark.parametrize('stale', ['project', 'collection', 'catalogue', 'target', 'connect'])
def test_stale_preview_never_writes(tmp_path, stale):
    store, project, collection = prepared(tmp_path)
    preview = store.bibliography_preview(project['id'], CATALOGUE)
    payload, catalogue = sync_payload(preview), CATALOGUE
    if stale == 'project':
        project = save_file(store, project, 'main.tex', project['files']['main.tex'] + '\n')
    elif stale == 'collection':
        store.change({**collection, 'action': 'collection_paper', 'paper_id': 'P2'}, CATALOGUE)
    elif stale == 'catalogue':
        catalogue = [{**CATALOGUE[0], 'title': 'New metadata'}, CATALOGUE[1]]
    elif stale == 'target':
        payload['filename'] = 'other.bib'
    else:
        payload['connect_document'] = True
    before = store.get(project['id'])
    with pytest.raises(WritingConflict):
        store.change(payload, catalogue)
    assert store.get(project['id']) == before


def test_custom_bib_target_and_document_connection_are_explicit(tmp_path):
    store, project, _ = prepared(tmp_path)
    source = '\\documentclass{article}\n\\begin{document}\n% \\bibliography{references}\nText\n\\end{document}\n'
    project = save_file(store, project, 'main.tex', source)
    preview = store.bibliography_preview(project['id'], CATALOGUE, 'refs/library.bib', True)
    assert preview['connection_needed'] and preview['document_change']['filename'] == 'main.tex'
    assert store.get(project['id'])['files']['main.tex'] == source
    synced = store.change(sync_payload(preview), CATALOGUE)
    assert '\\bibliography{refs/library}' in synced['files']['main.tex']
    assert synced['files']['refs/library.bib'] == preview['bibtex']
    next_preview = store.bibliography_preview(project['id'], CATALOGUE, 'refs/library.bib', True)
    assert not next_preview['connection_needed'] and next_preview['document_change'] is None


@pytest.mark.parametrize('command', [r'\bibliography{my-own}', r'\addbibresource{custom.bib}', r'\printbibliography',
                                   r'\begin{thebibliography}{9}', r'\usepackage[backend=biber]{biblatex}'])
def test_custom_bibliography_configuration_never_replaced(tmp_path, command):
    store, project, _ = prepared(tmp_path)
    source = command + '\n\\begin{document}\n\\end{document}\n'
    project = save_file(store, project, 'main.tex', source)
    preview = store.bibliography_preview(project['id'], CATALOGUE, connect_document=True)
    assert not preview['connection_needed'] and preview['document_change'] is None
    updated = store.change(sync_payload(preview), CATALOGUE)
    assert updated['files']['main.tex'] == source


def test_included_bibliography_configuration_is_preserved(tmp_path):
    store, project, _ = prepared(tmp_path)
    project = save_file(store, project, 'main.tex', '\\begin{document}\n\\input{ending}\n\\end{document}\n')
    project = save_file(store, project, 'ending.tex', '\\bibliography{custom}')
    preview = store.bibliography_preview(project['id'], CATALOGUE, connect_document=True)
    assert not preview['connection_needed'] and preview['document_change'] is None


def test_escaped_percent_does_not_hide_existing_bibliography(tmp_path):
    store, project, _ = prepared(tmp_path)
    project = save_file(store, project, 'main.tex', '\\begin{document}\n50\\% \\bibliography{custom}\n\\end{document}\n')
    preview = store.bibliography_preview(project['id'], CATALOGUE, connect_document=True)
    assert not preview['connection_needed']


@pytest.mark.parametrize('source', ['@article{missing, title={No end}', '@article{same,title={A}}\n@book{SAME,title={B}}', '@article{,title={No key}}'])
def test_malformed_bibtex_is_not_overwritten(tmp_path, source):
    store, project, _ = prepared(tmp_path)
    project = save_file(store, project, 'references.bib', source)
    with pytest.raises(ValueError):
        store.bibliography_preview(project['id'], CATALOGUE)
    assert store.get(project['id']) == project


def test_sync_file_count_limit_is_atomic(tmp_path):
    store, project, _ = prepared(tmp_path)
    files = {'main.tex': project['files']['main.tex'], **{f'f{i}.tex': '' for i in range(199)}}
    project = store.change({**project, 'action': 'save_project', 'files': files}, CATALOGUE)
    preview = store.bibliography_preview(project['id'], CATALOGUE)
    with pytest.raises(ValueError, match='1–200'):
        store.change(sync_payload(preview), CATALOGUE)
    assert store.get(project['id']) == project


def test_sync_project_byte_limit_is_atomic(tmp_path, monkeypatch):
    import originrag.writing as writing

    store, project, _ = prepared(tmp_path)
    preview = store.bibliography_preview(project['id'], CATALOGUE)
    monkeypatch.setattr(writing, 'MAX_PROJECT_BYTES', sum(len(c.encode()) for c in project['files'].values()) + 1)
    with pytest.raises(ValueError, match='12 MB'):
        store.change(sync_payload(preview), CATALOGUE)
    assert store.get(project['id']) == project


def test_missing_collection_and_unsafe_target(tmp_path):
    store, project, _ = prepared(tmp_path)
    with pytest.raises(ValueError):
        store.bibliography_preview(project['id'], CATALOGUE, '../references.bib')
    with pytest.raises(ValueError):
        store.bibliography_preview(project['id'], CATALOGUE, 'main.tex')
    project = store.change({**project, 'action': 'save_project', 'collection': ''}, CATALOGUE)
    with pytest.raises(ValueError, match='Link a personal library'):
        store.bibliography_preview(project['id'], CATALOGUE)
