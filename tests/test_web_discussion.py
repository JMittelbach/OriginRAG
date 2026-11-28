import json
from types import SimpleNamespace

import pytest

from test_chat_web_search import external_app
import web_chat
from originrag.analysis.chat_commands import discussion_number, reading_selection
from originrag.discovery.reading import discussion_items, library_continuation, reading_prompt
from originrag.discovery.chat_search import WebSearch
from originrag.discovery import chat_engine


@pytest.mark.parametrize('question', [
    'Couldyou summraize the secnod paper in three short sentences?',
    'Please summarize the second paper and tell me its limitations.',
    'What did the second paper find?',
    'No thanks, I meant the second paper, not the first.',
])
def test_numbered_reading_survives_length_constraints_and_corrections(question):
    selection = reading_selection(question)
    assert (selection.number if selection else discussion_number(question)) == 2


@pytest.mark.parametrize('question', [
    'Compare the first and second papers.',
    'Do not summarize the second paper.',
    'What happened after the second experiment?',
    'Does gene PF3D7_0202000 regulate commitment?',
])
def test_numbers_are_not_always_paper_selections(question):
    assert reading_selection(question) is None
    assert discussion_number(question) is None


def test_continuous_external_discussion_keeps_identity_and_source_scope(external_app, monkeypatch):
    app, calls = external_app
    original = app.discovery_completion
    monkeypatch.setattr(web_chat.TOPIC_DISCOVERY, 'research', lambda owner, plan, **kw: {
        'items': [{'doi': f'10.1234/graph{i}', 'title': f'Graph study {i}',
                   'abstract': f'This study examined graph method {i}. Sample size was 12.'} for i in range(4)],
        'sources': ['Crossref'], 'next_offset': 20})
    def complete(settings, prompt, schema, **kwargs):
        if 'relevant' in schema['properties']:
            return json.dumps({'relevant': [{'id': i, 'support': f'Graph study {i}'} for i in range(4)]})
        return original(settings, prompt, schema, **kwargs)
    monkeypatch.setattr(app, 'discovery_completion', complete)
    prompts = []
    def generate(prompt, **kwargs):
        prompts.append(json.loads(prompt.split('\n')[-1]))
        return SimpleNamespace(llm_result=SimpleNamespace(answer='A brief grounded answer.', error=None))
    monkeypatch.setattr(web_chat.ask_corpus, 'generate_answer_with_backend', generate)
    first = app.run_turn('Find two papers online about graph methods.', {})
    selected = first['external_papers'][1]['doi']
    for question in ('Couldyou summraize the secnod paper in three short sentences?',
                     'What sample types and sequencing methods did they use?',
                     'Can you give me the exact sample size? If you do not have the full text, say so.',
                     'Explain that again in simple English.'):
        app.load_session(str(app.session_dir))
        result = app.run_turn(question, {})
        assert result['selected_external_dois'] == [selected]
        assert result['source_scope'] == 'abstract'
        assert result['show_external_cards'] is False
        assert [p['doi'] for p in prompts[-1]['papers']] == [selected]
        assert not result['sources']
    assert len(prompts[-1]['conversation']) == 3
    correction = app.run_turn('No thanks, I meant the first paper, not the second.', {})
    assert correction['selected_external_dois'] == [first['external_papers'][0]['doi']]
    assert prompts[-1]['conversation'] == []
    before = len(prompts)
    metadata = app.run_turn('Give me its title and DOI again. Do not search for anything else.', {})
    assert len(prompts) == before and first['external_papers'][0]['doi'] in metadata['answer']
    app.run_turn('Thanks!', {})
    more = app.run_turn('Show me two more papers from that same web search.', {})
    assert len(more['external_papers']) == 2
    assert not set(p['doi'] for p in first['external_papers']) & set(p['doi'] for p in more['external_papers'])
    assert not more.get('selected_external_dois')


def test_external_metadata_never_turns_into_fabricated_fulltext(external_app, monkeypatch):
    app, _ = external_app
    listed = app.run_turn('Find papers online about graphs', {})
    monkeypatch.setattr(web_chat.ask_corpus, 'generate_answer_with_backend', lambda *a, **kw:
                        SimpleNamespace(llm_result=SimpleNamespace(answer='', error='timeout')))
    failure = app.run_turn('Summarize the first paper.', {})
    assert failure['discussion_failed']
    assert failure['selected_external_dois'] == [listed['external_papers'][0]['doi']]
    assert not failure.get('web_search_failed')
    app.load_session(str(app.session_dir))
    assert listed['external_papers'][0]['doi'] in app.run_turn('Give me its title and DOI.', {})['answer']


def test_metadata_for_imported_web_paper_needs_no_generation(external_app, monkeypatch):
    app, _ = external_app
    listed = app.run_turn('Find papers online about graphs', {})
    doi = listed['external_papers'][0]['doi']
    app.run_turn('Summarize the first paper.', {})
    monkeypatch.setattr(web_chat, 'library_catalog', lambda *a, **kw:
                        [{'paper_id': 'LOCAL', 'doi': doi, 'searchable': True}])
    monkeypatch.setattr(app, 'discuss', lambda *a, **kw: pytest.fail('No model needed for known title/DOI'))
    result = app.run_turn('Give me its title and DOI again.', {})
    assert result['source_scope'] == 'metadata' and doi in result['answer']


def test_followup_is_not_a_new_subject_or_explicit_different_paper():
    items = [{'title': 'Penguin diet', 'doi': '10.1234/a', 'abstract': 'Penguins ate krill.'},
             {'title': 'Penguin migration', 'doi': '10.1234/b', 'abstract': 'Penguins migrated.'}]
    prior = {'external_papers': items, 'selected_external_dois': ['10.1234/a']}
    assert discussion_items('What did they eat?', prior) == [items[0]]
    assert discussion_items('Compare those two papers.', prior) == items
    assert not discussion_items('New topic: what did they find about bears?', prior)
    assert not discussion_items('What did the paper by Smith say?', prior)
    assert not discussion_items('How do transistors work?', prior)
    assert library_continuation('Actually search my library instead, on the same topic.')
    assert not library_continuation('Search my library instead about penguins.')


def test_abstract_context_budget_compacts_history_but_preserves_sources():
    paper = {'doi': '10.1234/a', 'title': 'Example', 'abstract': 'We measured 12 samples.'}
    history = [('What did they measure?', '12 samples. ' * 500)] * 50
    prompt, compacted = reading_prompt('How many?', [paper], history, window=4096, reserve=1000)
    data = json.loads(prompt.split('\n')[-1])
    assert compacted and data['papers'][0]['abstract'] == paper['abstract']
    assert 'ABSTRACTS ONLY' in prompt and 'untrusted source data' in prompt
    with pytest.raises(ValueError, match='exceed'):
        reading_prompt('Summarize.', [{**paper, 'abstract': 'too long ' * 20000}], [], window=2048)


def test_abstract_quote_is_copied_not_written_by_the_model():
    from originrag.discovery.reading import abstract_quotation
    candidates = [{'id': 'Q1', 'text': 'We observed *three* samples.', 'page': 1}]
    assert abstract_quotation('{"passage_id":"Q1"}', candidates) == '> We observed \\*three\\* samples.'
    assert 'could not identify' in abstract_quotation('{"passage_id":"Q2"}', candidates)
    assert 'could not identify' in abstract_quotation('Invented quotation', candidates)


def test_compound_subject_requires_actual_identifier_not_just_species():
    plan = WebSearch(subject='ABC2-Z in Species example', topics=('differentiation',),
                     queries=('ABC2-Z Species example differentiation',))
    rows = [{'doi': '10.1234/wrong', 'title': 'Differentiation in Species example'},
            {'doi': '10.1234/right', 'title': 'ABC2-Z controls differentiation in Species example'},
            {'doi': '10.1234/other', 'title': 'ABC2-Y controls differentiation in Species example'}]
    assert [p['doi'] for p in chat_engine.shortlist(rows, plan.queries, plan)] == ['10.1234/right']


def test_compound_subject_is_not_a_single_phrase_in_provider_query(monkeypatch):
    import io
    from urllib.parse import parse_qs, urlparse
    requests = []
    def response(request, timeout):
        requests.append(parse_qs(urlparse(request.full_url).query)['query'][0])
        return io.BytesIO(b'{"resultList":{"result":[]}}')
    monkeypatch.setattr(chat_engine, 'urlopen', response)
    chat_engine.europepmc('ABC2-Z Species example differentiation', subject='ABC2-Z Species example',
                         topics=('differentiation',), since='2024')
    assert 'TITLE_ABS:"ABC2-Z" AND TITLE_ABS:"Species" AND TITLE_ABS:"example"' in requests[0]
    assert 'FIRST_PDATE:[2024-01-01' in requests[0]
