import json
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse
import pytest

from originrag.discovery import search_plan, chat_engine
from originrag.discovery.chat_search import WebSearch, resolved_search_plan
from originrag.discovery.topic_search import DiscoveryUnavailable


def plan(**changes):
    return dict(intent='web_search', subject='penguins', topics=['diet', 'migration'],
                topic_mode='any', journal='', journal_mode='any', since='', recent=True,
                limit=2, clarification='', **changes)


def encoded(**changes):
    data = plan()
    data.update(changes)
    return json.dumps(data)


def test_alternatives_and_required_intersection_are_distinct():
    assert search_plan.parse(encoded())['queries'] == ['penguins diet', 'penguins migration']
    assert search_plan.parse(encoded(topic_mode='all'))['queries'] == ['penguins diet migration']


def test_explicit_conjunction_cannot_be_relaxed_to_disjunction():
    request = resolved_search_plan(WebSearch(), 'Find papers on penguin diet and migration', encoded())
    assert request.topic_mode == 'all' and request.queries == ('penguins diet migration',)
    request = resolved_search_plan(WebSearch(), 'Find papers on penguin diet or migration', encoded())
    assert request.topic_mode == 'any' and len(request.queries) == 2


def test_journal_preference_never_crowds_out_the_common_subject():
    request = WebSearch(subject='penguins', topics=('diet',), query='penguins diet', journal='Nature')
    rows = [item(title='Penguin diet', abstract='Fish consumption'),
            item(doi='10.1234/unrelated', title='Healthy diet in humans', abstract='Nutrition')]
    rows[1]['journal'] = 'Nature'
    assert [p['doi'] for p in chat_engine.shortlist(rows, ['penguins diet'], request)] == ['10.1234/one']


def test_only_search_state_not_conversation_or_documents_in_prompt():
    prompt = search_plan.prompt('preferably Science', {
        'query': 'penguins diet', 'journal': 'Nature', 'limit': 2,
        'answer': 'private answer', 'sources': ['private paper'], 'chat_history': 'private draft'})
    state = json.loads(prompt.split('\n')[-1])
    assert state['previous_search'] == {'query': 'penguins diet', 'journal': 'Nature', 'limit': 2}
    assert 'private draft' not in prompt and 'private paper' not in prompt


@pytest.mark.parametrize('changes', [
    {'intent': 'invent'}, {'topics': ['a', 'b', 'c', 'd']}, {'topics': [1]},
    {'subject': '/Users/user/private.tex'}, {'journal': 'https://private.example'},
    {'topics': ['password=secret']}, {'recent': 'yes'}, {'limit': True},
    {'since': '2099'}, {'journal_mode': 'sometimes'}, {'topic_mode': 'either'},
    {'subject': '', 'topics': []},
])
def test_invalid_or_private_plan_never_reaches_providers(changes):
    request = resolved_search_plan(WebSearch(), 'please find it', encoded(**changes))
    assert request.planning_error and request.prompt and not request.queries


def test_clarification_only_when_no_usable_topic_and_journal_optional():
    parsed = search_plan.parse(encoded(intent='clarify', subject='', topics=[],
                                      clarification='What topic should I search?'))
    assert not parsed['queries']
    assert search_plan.parse(encoded())['journal_mode'] == 'any'
    assert search_plan.parse(encoded(journal='Science', journal_mode='prefer'))['journal_mode'] == 'prefer'
    assert search_plan.parse(encoded(journal='Science', journal_mode='only'))['journal_mode'] == 'only'


def test_refinement_preserves_count_and_recovers_previous_aspect():
    previous = WebSearch(limit=2, topics=('diet', 'migration'))
    refined = resolved_search_plan(previous, 'emperor penguins preferably in Science',
        encoded(subject='emperor penguins diet migration', topics=[], limit=8, journal='Science', journal_mode='prefer'))
    assert refined.limit == 2 and refined.subject == 'emperor penguins'
    assert refined.queries == ('emperor penguins diet', 'emperor penguins migration')
    assert resolved_search_plan(refined, 'just 3 please', encoded()).limit == 3


def item(doi='10.1234/one', title='Penguin diet', abstract='Penguins eat fish', **kw):
    return dict(doi=doi, title=title, abstract=abstract, journal='Science', year='2025', **kw)


def test_semantic_ranking_can_only_reorder_actual_provider_items():
    rows = [item(), item(doi='10.1234/two', title='Unrelated study')]
    selected = chat_engine.ranked_items('{"relevant":[{"id":0,"support":"Penguin diet"},{"id":0,"support":"Penguin diet"}]}', rows)
    assert len(selected) == 1 and selected[0]['doi'] == rows[0]['doi']
    assert chat_engine.ranked_items('{"relevant":[]}', rows) == []
    for value in ['{"relevant":[2]}', '{"relevant":[-1]}', '{"relevant":[true]}', '{"relevant":["0"]}', 'null']:
        with pytest.raises(ValueError):
            chat_engine.ranked_items(value, rows)
    with pytest.raises(ValueError, match='relevance_support'):
        chat_engine.ranked_items('{"relevant":[{"id":1,"support":"Invented support"}]}', rows)


def test_ranking_passage_ids_cannot_invent_quotes_or_borrow_another_papers_passage():
    rows = [item(abstract='Penguins eat fish. Diet varies by season.'), item(abstract='Another subject.')]
    response = json.dumps({'relevant': [{'id': 0, 'support': 'A2'}, {'id': 1, 'support': 'A2'}]})
    result = chat_engine.ranked_items(response, rows)
    assert len(result) == 1 and result[0]['relevance_passage'] == 'Diet varies by season.'
    assert chat_engine.ranked_items('{"relevant":[{"id":0,"support":"T"}]}', rows)[0]['relevance_passage'] == rows[0]['title']


def test_newer_matches_sorted_from_metadata_not_model_guess():
    rows = [item(doi='10.1234/a'), item(doi='10.1234/b')]
    rows[0]['year'] = '2010'
    assert chat_engine.order_matches(rows, WebSearch(recent=True))[0]['doi'] == '10.1234/b'


def test_structured_background_is_not_evidence_of_the_studied_subject():
    row = item(title='Migration in gulls', abstract='BackgroundPenguin migration is important. '
               'MethodsWe studied gull migration. ResultsGulls travelled far. ConclusionsGulls migrate.')
    assert chat_engine.ranking_abstract(row).startswith('MethodsWe studied gull migration')
    assert 'Penguin' not in chat_engine.rank_prompt(WebSearch(query='penguin migration'), [row])
    with pytest.raises(ValueError, match='relevance_support'):
        chat_engine.ranked_items(json.dumps({'relevant': [{'id': 0, 'support': 'Penguin migration is important.'}]}), [row])
    assert chat_engine.ranking_abstract(item(abstract='Unstructured study abstract.')) == 'Unstructured study abstract.'


def test_refinement_keeps_matching_previous_web_hits_not_other_topics():
    rows = [item(), item(doi='10.1234/old'), item(doi='10.1234/other')]
    rows[1]['year'] = '2010'
    rows[2]['journal'] = 'Another journal'
    prior = {'web_search': {'query': 'penguin diet'}, 'external_papers': rows[:2],
             'remaining_web_papers': rows[2:]}
    request = WebSearch(query='penguin diet', since='2023', journal='Science', journal_mode='only')
    assert chat_engine.previous_matches(prior, request) == [rows[0]]
    assert not chat_engine.previous_matches(prior, WebSearch(query='solar cells'))
    assert not chat_engine.previous_matches(prior, WebSearch(query='penguin diet', offset=20))


def test_shortlist_accepts_either_aspect_not_every_word():
    request = WebSearch(query='penguins: diet or migration', queries=('penguins diet', 'penguins migration'))
    rows = [item(), item(doi='10.1234/two', title='Penguin migration', abstract='Movement of penguins'),
            item(doi='10.1234/no', title='Solar energy', abstract='Materials and optics')]
    assert {p['doi'] for p in chat_engine.shortlist(rows, request.queries, request)} == {'10.1234/one', '10.1234/two'}


def test_collect_deduplicates_and_uses_richer_abstract(monkeypatch):
    monkeypatch.setattr(chat_engine, 'search_topic', lambda **kw: {'items': [item(abstract='')]})
    monkeypatch.setattr(chat_engine, 'europepmc', lambda **kw: {'items': [item(abstract='Penguin diet and migration study.')]})
    result = chat_engine.collect(WebSearch(query='penguins diet'))
    assert len(result['items']) == 1
    assert result['items'][0]['abstract'] == 'Penguin diet and migration study.'
    assert result['sources'] == ['Crossref', 'Europe PMC'] and len(result['checks']) == 2


def test_provider_failure_is_partial_or_explicit_never_no_papers(monkeypatch):
    def fail(**kwargs):
        raise DiscoveryUnavailable('Offline')
    monkeypatch.setattr(chat_engine, 'search_topic', fail)
    monkeypatch.setattr(chat_engine, 'europepmc', lambda **kw: {'items': [item()]})
    result = chat_engine.collect(WebSearch(query='penguins diet'))
    assert result['items'] and result['unavailable'] == ['Crossref']
    monkeypatch.setattr(chat_engine, 'europepmc', fail)
    with pytest.raises(DiscoveryUnavailable, match='could not be reached'):
        chat_engine.collect(WebSearch(query='penguins diet'))


@pytest.mark.parametrize('mode', ['only', 'prefer'])
def test_strict_and_preferred_journal_restrictions(monkeypatch, mode):
    calls = []
    def collect(**kwargs):
        calls.append(kwargs)
        return {'items': []}
    monkeypatch.setattr(chat_engine, 'search_topic', collect)
    monkeypatch.setattr(chat_engine, 'europepmc', collect)
    request = WebSearch(query='penguins diet', queries=('penguins diet',), journal='Science', journal_mode=mode)
    chat_engine.collect(request, issns=('0036-8075',))
    if mode == 'only':
        assert all(c['issns'] == ('0036-8075',) for c in calls)
    else:
        assert any(c['issns'] for c in calls) and any(not c['issns'] for c in calls)


def test_provider_cache_is_bounded_expires_and_does_not_cache_failures(monkeypatch):
    monkeypatch.setattr(chat_engine, '_cache', chat_engine.OrderedDict())
    now = [1000.]
    monkeypatch.setattr(chat_engine.time, 'monotonic', lambda: now[0])
    calls = []
    def provider(query):
        calls.append(query)
        return {'items': []}
    chat_engine.cached_call(provider, {'query': 'same'})
    chat_engine.cached_call(provider, {'query': 'same'})
    assert calls == ['same']
    now[0] += 121
    chat_engine.cached_call(provider, {'query': 'same'})
    assert calls == ['same', 'same']
    for i in range(70):
        chat_engine.cached_call(provider, {'query': str(i)})
    assert len(chat_engine._cache) == 64
    def fail(query):
        raise DiscoveryUnavailable('Offline')
    before = len(chat_engine._cache)
    with pytest.raises(DiscoveryUnavailable):
        chat_engine.cached_call(fail, {'query': 'bad'})
    assert len(chat_engine._cache) == before


def test_europepmc_queries_titles_abstracts_and_sanitizes_metadata(monkeypatch):
    requests = []
    def respond(request, timeout):
        requests.append(request.full_url)
        rows = [{'doi': '10.1234/a', 'title': 'A <i>penguin</i> study', 'abstractText': '<p>Diet</p>',
                 'journalInfo': {'journal': {'title': 'Science'}}, 'pubYear': '2025'},
                {'doi': 'javascript:alert(1)', 'title': 'Unsafe'},
                {'doi': '10.1234/b', 'title': 'Retraction notice to penguins'},
                {'doi': '10.1234/c', 'title': 'Broken metadata', 'journalInfo': 'not a dictionary'}]
        return io.BytesIO(json.dumps({'resultList': {'result': rows}}).encode())
    monkeypatch.setattr(chat_engine, 'urlopen', respond)
    result = chat_engine.europepmc('penguins diet', since='2023', issns=('0036-8075',))
    expression = parse_qs(urlparse(requests[0]).query)['query'][0]
    assert 'TITLE_ABS:"penguins"' in expression
    assert 'ISSN:0036-8075' in expression
    assert len(result['items']) == 1 and result['items'][0]['title'] == 'A penguin study'


def test_cancelling_a_search_does_not_wait_for_http_workers(monkeypatch):
    from originrag.cancellation import Cancellation, TurnCancelled, cancellation_scope
    entered, release = threading.Event(), threading.Event()
    def provider(**kwargs):
        entered.set()
        release.wait(3)
        return {'items': []}
    monkeypatch.setattr(chat_engine, 'search_topic', provider)
    monkeypatch.setattr(chat_engine, 'europepmc', provider)
    token = Cancellation()
    def search():
        with cancellation_scope(token):
            chat_engine.collect(WebSearch(query='cancellation test'))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(search)
        try:
            assert entered.wait(2)
            token.cancel()
            with pytest.raises(TurnCancelled):
                future.result(timeout=1)
        finally:
            release.set()


def test_crossref_requests_are_serialized_across_users(monkeypatch):
    from originrag.discovery import topic_search
    entered, release = threading.Event(), threading.Event()
    active = 0
    peak = 0
    lock = threading.Lock()
    def response(request, timeout):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        entered.set()
        release.wait(2)
        with lock:
            active -= 1
        return io.BytesIO(b'{"message":{"items":[]}}')
    monkeypatch.setattr(topic_search, 'urlopen', response)
    monkeypatch.setattr(topic_search, '_CROSSREF_INTERVAL', 0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(topic_search.search_topic, 'penguin diet')
        assert entered.wait(1)
        second = pool.submit(topic_search.search_topic, 'solar energy')
        release.set()
        first.result(timeout=2)
        second.result(timeout=2)
    assert peak == 1
