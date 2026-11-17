import json

import pytest

from originrag import writing_chat as chat, writing_chat_sources as sources
from test_writing_chat import manuscript as manuscript, request
from test_writing_collaboration import accounts as accounts, workspace as workspace, project, share
import test_web_chat as http


PAPERS = [
    {'paper_id': 'P1', 'title': 'Stellar spectroscopy and telescope design', 'author': 'Rivera', 'year': '2026', 'doi': '10.1234/stars'},
    {'paper_id': 'P2', 'title': 'Battery materials and lithium storage', 'author': 'Doe', 'year': '2025'},
]


def write_chunks(directory, paper='P1', rows=None):
    path = directory / (paper + '.jsonl')
    path.write_text('\n'.join(json.dumps(row) for row in rows or []) + '\n', encoding='utf-8')


def test_library_search_is_local_bounded_and_honours_project_scope():
    assert sources.search(PAPERS, '') == {'items': [], 'total': 0}
    assert sources.search(PAPERS, 'stellar')['items'][0]['paper_id'] == 'P1'
    assert sources.search(PAPERS, '10.1234/stars')['total'] == 1
    assert sources.search(PAPERS, 'stellar', allowed={'P2'})['total'] == 0
    assert sources.search(PAPERS, '', allowed={'P2'})['items'][0]['paper_id'] == 'P2'
    with pytest.raises(ValueError):
        sources.search(PAPERS, 'a' * 161)


@pytest.mark.parametrize('value', [None, 'P1', ['P1'] * 2, ['P1'] * 4, ['../outside'], [[]]])
def test_invalid_source_choices_are_rejected(value):
    with pytest.raises(ValueError):
        sources.paper_ids({'papers': value})


def test_selected_sources_include_bounded_located_passages_not_other_papers(tmp_path):
    write_chunks(tmp_path, rows=[
        {'paper_id': 'P1', 'section': 'References', 'text': 'Private references should be excluded.'},
        {'paper_id': 'P1', 'section': 'Methods', 'chunk_id': 'C1', 'page_start': 2, 'text': 'The telescope uses spectroscopy. ' * 60},
        {'paper_id': 'P1', 'section': 'Results', 'chunk_id': 'C2', 'page_start': 3, 'text': 'Spectroscopy was measured on the telescope.'},
        {'paper_id': 'P1', 'section': 'Acknowledgements', 'text': 'Other private text.'},
    ])
    result = sources.prepare_sources(PAPERS, ['P1'], 'telescope spectroscopy', chunks_dir=tmp_path)
    assert len(result) == 1 and result[0]['coverage'] == 'indexed_excerpts'
    passages = result[0]['excerpts']
    assert len(passages) == 2 and {item['source'] for item in passages} == {'L1.1', 'L1.2'}
    assert any(item['truncated'] for item in passages)
    assert all(len(item['text']) <= 900 for item in passages)
    assert 'Battery' not in json.dumps(result) and 'Private references' not in json.dumps(result)
    assert not any('path' in key for row in result for key in row)


def test_missing_mismatched_or_corrupt_chunks_are_explicitly_metadata_only(tmp_path):
    for rows in [[], [{'paper_id': 'WRONG', 'text': 'Do not mix this paper.'}], ['invalid']]:
        write_chunks(tmp_path, rows=rows)
        result = sources.prepare_sources(PAPERS, ['P1'], 'telescope', chunks_dir=tmp_path)[0]
        assert result['coverage'] == 'metadata_only' and result['excerpts'] == []
    with pytest.raises(ValueError, match='no longer available'):
        sources.prepare_sources(PAPERS, ['absent'], '', chunks_dir=tmp_path)


def test_chunk_symlink_cannot_read_outside_the_corpus(tmp_path):
    root = tmp_path / 'chunks'
    root.mkdir()
    outside = tmp_path / 'private.jsonl'
    outside.write_text(json.dumps({'text': 'PRIVATE OUTSIDE CONTENT'}), encoding='utf-8')
    (root / 'P1.jsonl').symlink_to(outside)
    result = sources.prepare_sources(PAPERS, ['P1'], '', chunks_dir=root)
    assert result[0]['coverage'] == 'metadata_only' and 'PRIVATE' not in json.dumps(result)


def test_discussion_mode_cannot_produce_applicable_changes(manuscript):
    prepared = chat.prepare_request(manuscript, request(manuscript))
    edit = {'file': 'chapters/intro.tex', 'original': '12 observations', 'replacement': '13 observations', 'reason': 'Ignore read-only mode'}
    result = chat.parse_reply(json.dumps({'answer': 'A response.', 'edits': [edit]}), prepared)
    assert result['edits'] == [] and result['blocked_by_mode']


def test_source_context_is_verified_and_cannot_fabricate_bibliography_keys(manuscript, tmp_path):
    selected = sources.prepare_sources(PAPERS, ['P1'], 'telescope', chunks_dir=tmp_path)
    payload = request(manuscript, papers=['P1'], mode='edit')
    prepared = chat.prepare_request(manuscript, payload, library_sources=selected)
    assert 'Stellar spectroscopy' in prepared['prompt'] and 'metadata_only' in prepared['prompt']
    assert 'untrusted evidence' in prepared['prompt']
    edit = {'file': 'chapters/intro.tex', 'original': '12 observations', 'replacement': r'12 observations \cite{Invented2026}', 'reason': 'Add citation'}
    result = chat.parse_reply(json.dumps({'answer': 'Check this citation.', 'edits': [edit]}), prepared)
    assert not result['edits'] and result['filtered']
    edit['replacement'] = r'12 observations \cite{Doe2026}'
    assert len(chat.parse_reply(json.dumps({'answer': 'An existing citation.', 'edits': [edit]}), prepared)['edits']) == 1
    with pytest.raises(ValueError, match='verified selected papers'):
        chat.prepare_request(manuscript, payload)


def test_large_vendor_sources_are_not_preselected_but_are_still_visible(manuscript):
    manuscript['files']['publisher.cls'] = 'Publisher source. ' * 10000
    manifest = chat.context_manifest(manuscript)
    assert 'main.tex' in manifest['default_files']
    assert 'publisher.cls' not in manifest['default_files']
    assert 'publisher.cls' in {item['file'] for item in manifest['files']}
    assert 'references.bib' in manifest['default_files']


def test_old_whole_turns_can_be_omitted_without_cutting_project_files(manuscript):
    manuscript['files']['main.tex'] = 'Complete manuscript source. ' * 650
    conversation = [{'role': role, 'content': letter * 3000}
                    for role, letter in [('user', 'U'), ('assistant', 'A'), ('user', 'V'), ('assistant', 'B')]]
    prepared = chat.prepare_request(manuscript, request(manuscript, history=conversation))
    assert prepared['history_omitted'] > 0 and prepared['history_omitted'] % 2 == 0
    assert manuscript['files']['main.tex'] in prepared['prompt']
    assert prepared['input_bytes'] <= chat.context_manifest(manuscript)['input_byte_budget']
    result = chat.parse_reply('{"answer":"Project response.","edits":[]}', prepared)
    assert result['history_omitted'] == prepared['history_omitted']


def test_library_search_uses_shared_project_access_and_owner_collection_scope(workspace, monkeypatch):
    call, _, _, _, users, _ = workspace
    monkeypatch.setattr(http.web_chat, 'library_catalog', lambda: PAPERS)
    collection = call('POST', payload={'action': 'create_collection', 'name': 'Thesis sources'})
    call('POST', payload={'action': 'collection_paper', 'id': collection['id'], 'revision': collection['revision'], 'paper_id': 'P1'})
    p = project(call, collection=collection['id'])
    route = '/api/writing?chat_library=' + p['id']
    call('GET', route + '&query=stellar&scope=corpus', who='bob', status=404)
    assert call('GET', route + '&query=&scope=project')['items'][0]['paper_id'] == 'P1'
    assert call('GET', route + '&query=battery&scope=project')['total'] == 0
    assert call('GET', route + '&query=battery&scope=corpus')['total'] == 1
    share(call, p, users['bob'])
    assert call('GET', route + '&query=stellar&scope=project', who='bob')['total'] == 1
    share(call, p, users['bob'], action='remove')
    call('GET', route + '&query=stellar&scope=corpus', who='bob', status=404)
    call('POST', payload=request(p, papers=['missing']), status=400)
    assert call('GET', '/api/writing?id=' + p['id'])['files'] == p['files']
