import json
import random

import pytest

from originrag import writing_chat_context as context, writing_chat_sources as sources
from originrag.writing import WritingStore


def catalogue(count):
    rng = random.Random(2026)
    return [{'paper_id': f'P{index}', 'title': rng.choice([
        'Stellar spectroscopy', 'Spectroscopy with a telescope', 'Other results']),
        'author': rng.choice(['Doe', 'Rivera', 'Müller']), 'year': str(2020 + index % 7),
        'journal': 'Telescope journal', 'doi': f'10.1234/{index}'} for index in range(count)]


def reference_search(papers, query, allowed):
    query = query.strip().casefold()
    matches = []
    for paper in papers:
        if allowed is not None and paper['paper_id'] not in allowed:
            continue
        fields = [str(paper.get(key) or '').casefold() for key in
                  ('title', 'author', 'journal', 'doi', 'year', 'paper_id', 'topic_label')]
        if all(any(token in field for field in fields) for token in query.split()):
            rank = (100 if query and query in fields[0] else 0) + sum(
                10 for token in query.split() if token in fields[0])
            matches.append((-rank, fields[1], fields[0], paper))
    matches.sort(key=lambda row: row[:3])
    return {'total': len(matches), 'items': [sources.metadata(row[3]) for row in matches[:8]]}


@pytest.mark.parametrize('query', ['spectroscopy', 'stellar spectroscopy', 'MÜLLER', '2026 telescope', '10.1234/1', 'missing'])
@pytest.mark.parametrize('scoped', [False, True])
def test_bounded_library_search_preserves_ranking_counts_and_scope(query, scoped):
    papers = catalogue(500)
    allowed = {paper['paper_id'] for paper in papers[::3]} if scoped else None
    assert sources.search(iter(papers), query, allowed=allowed) == reference_search(papers, query, allowed)


def test_library_search_formats_only_visible_results_and_keeps_stable_ties(monkeypatch):
    papers = [{'paper_id': f'P{index}', 'title': 'Identical study', 'author': 'Doe'} for index in range(5000)]
    formatted = []
    metadata = sources.metadata

    def observe(paper):
        formatted.append(paper['paper_id'])
        return metadata(paper)

    monkeypatch.setattr(sources, 'metadata', observe)
    result = sources.search(iter(papers), 'study')
    assert result['total'] == 5000
    assert [row['paper_id'] for row in result['items']] == [f'P{index}' for index in range(8)]
    assert formatted == [row['paper_id'] for row in result['items']]
    assert sources.search(iter(papers), '', allowed={'P7', 'P3'})['total'] == 2


def test_large_context_does_not_serialize_whole_files_before_excerpt_selection(monkeypatch):
    source = '\\documentclass{article}\n\\begin{document}\n' + 'An unrelated paragraph.\n' * 10000
    source += '\\newcommand{\\specialsetting}{precise value}\n'
    project = {'main': 'main.tex', 'files': {'main.tex': source}}
    encoded_size = context.encoded_size
    sizes = []

    def observe(value):
        size = encoded_size(value)
        sizes.append(size)
        return size

    monkeypatch.setattr(context, 'encoded_size', observe)
    result, included = context.project_context(project, set(project['files']),
        'Explain specialsetting in main.tex', 'main.tex', None, None, 24000)
    assert not result[0]['complete']
    assert 'precise value' in json.dumps(result)
    assert all(source[start:end] in [part['content'] for part in result[0]['excerpts']]
               for start, end in included['main.tex'])
    assert max(sizes) < 12000


@pytest.mark.parametrize('text', ['ASCII', '🪶 Müller', '\\command{"quoted"}\n'])
def test_small_context_keeps_complete_unicode_and_escaped_source(text):
    project = {'main': 'main.tex', 'files': {'main.tex': text * 3, 'references.bib': ''}}
    expected = [{'file': name, 'content': source, 'complete': True} for name, source in sorted(project['files'].items())]
    result, included = context.project_context(project, set(project['files']), 'Explain',
        'main.tex', None, None, context.encoded_size(expected))
    assert result == expected
    assert included == {name: [(0, len(source))] for name, source in project['files'].items()}


def test_excerpt_ranking_keeps_highest_scores_and_earliest_ties():
    lines = [f'Background line {number}.\n' for number in range(2000)]
    for number in (200, 300, 500, 600, 700, 800, 900):
        lines[number] = f'alpha beta gamma MARKER_{number}\n'
    lines[100] = 'alpha MARKER_100\n'
    lines[400] = 'alpha beta MARKER_400\n'
    project = {'main': 'main.tex', 'files': {'main.tex': ''.join(lines)}}
    result, _ = context.project_context(project, {'main.tex'}, 'alpha beta gamma',
        'main.tex', None, None, 24000)
    text = json.dumps(result)
    assert all(f'MARKER_{number}' in text for number in (200, 300, 500, 600, 700, 800))
    assert all(f'MARKER_{number}' not in text for number in (100, 400, 900))


def test_long_single_line_selection_stays_exact_and_oversized_selection_is_rejected():
    source = '🪶 \\quoted{"value"} ' * 1000 + 'Selected passage.'
    project = {'main': 'main.tex', 'files': {'main.tex': source}}
    selection = {'file': 'main.tex'}
    selected_range = (len(source) - len('Selected passage.'), len(source))
    result, included = context.project_context(project, {'main.tex'}, 'Review',
        'main.tex', selection, selected_range, 24000)
    assert result[0]['excerpts'][0]['content'] == 'Selected passage.'
    assert included['main.tex'][0] == selected_range
    with pytest.raises(ValueError, match='selected passage is too large'):
        context.project_context(project, {'main.tex'}, 'Review', 'main.tex', selection,
                                (0, len(source)), 24000)


def test_single_project_summary_uses_index_lookup_not_dashboard_scan(tmp_path, monkeypatch):
    store = WritingStore(tmp_path)
    project = store.change({'action': 'create_project', 'name': 'Index lookup'}, [])
    connect = store.connect
    queries = []

    def observe():
        db = connect()
        db.set_trace_callback(queries.append)
        return db

    monkeypatch.setattr(store, 'connect', observe)
    assert store.summary(project['id'])['name'] == 'Index lookup'
    query = next(sql for sql in queries if sql.startswith('SELECT d.id,d.kind'))
    db = connect()
    try:
        plan = [row[3] for row in db.execute('EXPLAIN QUERY PLAN ' + query)]
    finally:
        db.close()
    assert any('SEARCH d USING' in step for step in plan), plan
    assert not any('SCAN d' in step for step in plan), plan
