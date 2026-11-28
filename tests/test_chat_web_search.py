from datetime import date
import json
import threading
from types import SimpleNamespace

import pytest

from originrag.discovery.chat_search import web_search_request, resolved_search_plan
from originrag.discovery.topic_search import DiscoveryUnavailable
from test_web_chat import WebChatApp, WebChatSettings, http_request
import web_chat


@pytest.mark.parametrize("message,topic", [
    ("Search the web for papers about graph neural networks", "graph neural networks"),
    ("Webscrape papers about solar cells please", "solar cells"),
    ("Such im Web nach neuen Papern über solar cells", "solar cells"),
    ("Find new papers about graph colouring", "graph colouring"),
    ("Suche neue Paper zu Einzelzell-Transkriptomik", "Einzelzell-Transkriptomik"),
    ("Search outside my library for papers on microscopy", "microscopy"),
    ("Can you look online for papers about non-linear systems?", "non-linear systems"),
    ("Search the web for quantum computing papers", "quantum computing"),
    ("Search online for papers about NOTCH1 and p53", "NOTCH1 and p53"),
    ("Web scrape papers on methods that do not use labels", "methods that do not use labels"),
    ("no thanks but I meant search online for papers about graphs", "graphs"),
    ("Search on the web for papers about solar cells", "solar cells"),
    ("Find papers about solar cells on the web", "solar cells"),
    ("Search the web for papers about library management", "library management"),
])
def test_explicit_external_search_preserves_topic(message, topic):
    request = web_search_request(message)
    assert request and not request.prompt
    assert request.query == topic


@pytest.mark.parametrize("message", [
    "Find papers about microscopy", "Find new papers in my library about microscopy",
    "Don't search the web; find papers about graphs in my library",
    "Search my corpus, not online", "Explain web scraping", "What is web search?",
    "Summarize the new paper in my library", "What did this paper find about online learning?",
    "Show me the two newest papers on microscopy since 2022",
    "Find papers about online learning", "Find papers on web scraping",
    "Find online learning papers", "Search papers about Internet access",
    "Find papers about graphs in my library",
    "Find web scraping papers", "Find web security papers",
])
def test_library_requests_and_explanations_do_not_leave_host(message):
    assert web_search_request(message) is None


def test_filters_missing_topic_scope_and_bounded_pages():
    request = web_search_request("Find 100 new preprints online about graph colouring since 2024")
    assert (request.limit, request.since, request.kind) == (20, "2024", "posted-content")
    assert request.query == "graph colouring"
    assert web_search_request("Search the web for three papers about graph colouring").limit == 3
    assert web_search_request("Find new papers about microscopy").since == str(date.today().year - 2)
    question = web_search_request("Search the web")
    assert question.prompt and not question.query
    assert web_search_request("solar cells", {"web_search": question.public()}).needs_planning
    ambiguity = web_search_request("Search my library or the web for papers about graph colouring")
    assert ambiguity.scope_pending and ambiguity.query == "graph colouring"
    assert not web_search_request("web", {"web_search": ambiguity.public()}).prompt
    next_page = web_search_request("five more", {"web_search": request.public()})
    assert (next_page.query, next_page.offset, next_page.limit) == ("graph colouring", 20, 5)
    filtered = web_search_request('Only since 2025', {'web_search': next_page.public()})
    assert filtered.query == request.query and filtered.since == '2025' and filtered.offset == 0
    assert web_search_request('Nur Preprints', {'web_search': request.public()}).kind == 'posted-content'


@pytest.fixture
def external_app(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(web_chat, 'TOPIC_DISCOVERY', web_chat.TopicDiscovery())
    monkeypatch.setattr(web_chat, "library_catalog", lambda *a, **kw: ())
    monkeypatch.setattr(web_chat.ask_corpus, "run_question", lambda *a, **kw: pytest.fail("No LLM or local retrieval for web discovery"))
    def search(owner, plan, issns=()):
        kwargs = {**plan.parameters(), **({'issns': issns} if issns else {})}
        calls.append(kwargs)
        return {"sources": ["Crossref"], "items": [{"doi": f"10.1234/test{kwargs['offset']}",
            "title": "A study of graphs", "author": "Example", "journal": "Graphs", "year": "2025",
            "abstract": "We compared two graph algorithms.", "type": "journal-article"}]}
    monkeypatch.setattr(web_chat.TOPIC_DISCOVERY, "research", search)
    def complete(settings, prompt, schema, **kwargs):
        if 'relevant' in schema['properties']:
            return '{"relevant":[{"id":0,"support":"A study of graphs"}]}'
        data = json.loads(prompt.split('\n')[-1])
        request = web_search_request(data['message'])
        topic = request.query if request and request.query else data['message']
        return json.dumps(plan_data(subject=topic, limit=request.limit if request else 8))
    monkeypatch.setattr(WebChatApp, 'discovery_completion', lambda self, *a, **kw: complete(*a, **kw))
    monkeypatch.setattr(web_chat.ask_corpus, 'generate_answer_with_backend', lambda *a, **kw:
                        SimpleNamespace(llm_result=SimpleNamespace(answer='They compared two graph algorithms.', error=None)))
    return WebChatApp(WebChatSettings(), str(tmp_path / "sessions")), calls


def test_web_turn_is_saved_reloaded_and_continued_without_llm(external_app):
    app, calls = external_app
    result = app.run_turn("Search the web for papers about graph colouring", {})
    assert result["task"] == "web_discovery" and not result["sources"]
    assert result["external_papers"][0]["in_library"] is False
    saved = web_chat.session_messages(app.session_dir)
    assert saved[-1]["result"]["external_papers"] == result["external_papers"]
    app.load_session(str(app.session_dir))
    more = app.run_turn("five more", {})
    assert len(calls) == 2 and calls[-1]["offset"] == 8
    assert more["external_papers"][0]["doi"] != result["external_papers"][0]["doi"]
    abstract = app.run_turn("Summarize the first paper", {})
    assert "not the full text" in abstract["answer"]
    assert "two graph algorithms" in abstract["answer"] and len(calls) == 2


def test_missing_topic_never_sends_private_context(external_app):
    app, calls = external_app
    result = app.run_turn("Search the web", {})
    assert "What topic" in result["answer"] and not calls
    app.run_turn("solar cells", {})
    assert len(calls) == 1 and calls[0]["query"] == "solar cells"


@pytest.mark.parametrize('year,expected', [('2024', 1), ('2026', 0)])
def test_filter_refinement_keeps_previously_verified_matches_only_when_still_eligible(external_app, monkeypatch, year, expected):
    app, _ = external_app
    first = app.run_turn('Search the web for papers about graph colouring', {})
    assert first['external_papers'][0]['relevance_passage']
    monkeypatch.setattr(app, 'discovery_completion', lambda *a, **kw: '{"relevant":[]}')
    refined = app.run_turn(f'Only since {year}', {})
    assert len(refined['external_papers']) == expected


def test_refinement_does_not_promote_unchecked_candidates(external_app, monkeypatch):
    app, _ = external_app
    original = app.discovery_completion
    def fail_ranking(settings, prompt, schema, **kwargs):
        if 'relevant' in schema['properties']:
            raise ValueError('model unavailable')
        return original(settings, prompt, schema, **kwargs)
    monkeypatch.setattr(app, 'discovery_completion', fail_ranking)
    first = app.run_turn('Search the web for papers about graph colouring', {})
    assert first['relevance_unchecked']
    monkeypatch.setattr(app, 'discovery_completion', lambda *a, **kw: '{"relevant":[]}')
    refined = app.run_turn('Only since 2024', {})
    assert not refined['external_papers']


def test_external_service_failure_is_not_a_library_failure(external_app, monkeypatch):
    app, _ = external_app
    def fail(*a, **kw):
        raise DiscoveryUnavailable("The literature service is unavailable.")
    monkeypatch.setattr(web_chat.TOPIC_DISCOVERY, "research", fail)
    result = app.run_turn("Search the web for papers about graphs", {})
    assert result["web_search_failed"]
    assert "not been searched or changed" in result["answer"]
    assert len(web_chat.read_session_turns(app.session_dir)) == 1


def test_external_selection_uses_local_fulltext_only_after_import(external_app, monkeypatch):
    app, _ = external_app
    app.run_turn("Search the web for papers about graphs", {})
    monkeypatch.setattr(web_chat, "library_catalog", lambda *a, **kw: ({"doi": "10.1234/test0", "paper_id": "LOCAL", "searchable": True},))
    monkeypatch.setattr(app, "discuss", lambda session, message, settings, paper_id, *a, **kw: {"paper_id": paper_id})
    assert app.run_turn("Summarize the first paper", {})["paper_id"] == "LOCAL"


@pytest.mark.parametrize('message', [
    'könnew ir uns übe das 2. paper unterhalten mastd u da einmal durchlesen bitte?',
    'Can we discuss the second paper, could you read it please?',
    'Lass uns das zweite Paper besprechen.',
])
def test_natural_numbered_discussion_uses_the_web_list_not_corpus_retrieval(external_app, monkeypatch, message):
    app, calls = external_app
    research = web_chat.TOPIC_DISCOVERY.research
    def two_results(*args, **kwargs):
        found = research(*args, **kwargs)
        found['items'].append({**found['items'][0], 'doi': '10.1234/second', 'title': 'A second study of graphs',
                               'abstract': 'We measured graph connectivity in the second study.'})
        return found
    monkeypatch.setattr(web_chat.TOPIC_DISCOVERY, 'research', two_results)
    original = app.discovery_completion
    def complete(settings, prompt, schema, **kwargs):
        if 'relevant' in schema['properties']:
            return '{"relevant":[{"id":0,"support":"A study of graphs"},{"id":1,"support":"A second study of graphs"}]}'
        return original(settings, prompt, schema, **kwargs)
    monkeypatch.setattr(app, 'discovery_completion', complete)
    listed = app.run_turn('Search the web for two papers about graph algorithms', {})
    assert len(listed['external_papers']) == 2
    reply = app.run_turn(message, {})
    assert 'A second study of graphs' in reply['answer']
    assert 'not the full text' in reply['answer']
    assert len(calls) == 1
    monkeypatch.setattr(web_chat, 'library_catalog', lambda *a, **kw: (
        {'doi': '10.1234/second', 'paper_id': 'SECOND', 'searchable': True},))
    monkeypatch.setattr(app, 'discuss', lambda session, question, settings, paper_id, *a, **kw: paper_id)
    assert app.run_turn(message, {}) == 'SECOND'


def test_web_filters_survive_fulltext_discussion_and_session_reload(external_app, monkeypatch):
    import time
    import originrag.summaries.reading_card as reading
    import originrag.paper_discussion as discussion
    app, calls = external_app
    first = app.run_turn('Search online for two papers about graph colouring', {})
    monkeypatch.setattr(reading, 'load_paper', lambda *a: ({'paper_id': 'LOCAL', 'title': 'Graphs'}, []))
    monkeypatch.setattr(discussion, 'bibliography', lambda *a: 'References from the paper.')
    result = app.discuss(app.session_dir, 'Show all references.', app.settings, 'LOCAL',
                         web_chat.read_session_turns(app.session_dir), None, time.monotonic(), web_context=first)
    assert result['web_search']['query'] == 'graph colouring'
    assert not result['show_external_cards']
    app.load_session(str(app.session_dir))
    next_result = app.run_turn('Only since 2025', {})
    assert next_result['task'] == 'web_discovery' and calls[-1]['since'] == '2025'
    assert calls[-1]['query'] == 'graph colouring' and calls[-1]['limit'] == 2


def test_more_after_abstract_reuses_validated_candidates(external_app, monkeypatch):
    app, calls = external_app
    def research(owner, plan, **kwargs):
        calls.append(plan.parameters())
        return {'items': [{'doi': f'10.1234/{i}', 'title': f'Graph study number {i}', 'abstract': 'Graph algorithms',
                           'year': '2025', 'journal': 'Graphs'} for i in range(6)],
                'sources': ['Crossref'], 'next_offset': 20}
    monkeypatch.setattr(web_chat.TOPIC_DISCOVERY, 'research', research)
    original = app.discovery_completion
    def complete(settings, prompt, schema, **kwargs):
        if 'relevant' in schema['properties']:
            return json.dumps({'relevant': [{'id': i, 'support': f'Graph study number {i}'} for i in range(6)]})
        return original(settings, prompt, schema, **kwargs)
    monkeypatch.setattr(app, 'discovery_completion', complete)
    first = app.run_turn('Search the web for two papers about graphs', {})
    app.run_turn('Summarize the first paper', {})
    app.load_session(str(app.session_dir))
    second = app.run_turn('two more', {})
    third = app.run_turn('two more', {})
    assert len(calls) == 1
    all_dois = [p['doi'] for result in (first, second, third) for p in result['external_papers']]
    assert len(set(all_dois)) == len(all_dois) == 6


def test_scope_clarification_can_choose_library(external_app):
    app, calls = external_app
    result = app.run_turn("Search my library or the web for papers about graph colouring", {})
    assert "library or the web" in result["answer"] and not calls
    result = app.run_turn("library", {})
    assert result["task"] == "sources" and not calls


def test_web_search_can_switch_back_and_does_not_search_on_courtesy(external_app):
    app, calls = external_app
    app.run_turn("Search the web for papers about graph colouring", {})
    app.run_turn("Thanks", {})
    assert len(calls) == 1
    assert app.run_turn("in my library instead", {})["task"] == "sources"
    assert len(calls) == 1


def test_number_outside_external_list_asks_without_model(external_app):
    app, calls = external_app
    app.run_turn("Search the web for papers about graphs", {})
    response = app.run_turn("Summarize the second paper", {})
    assert 'list has 1 papers' in response['answer'] and len(calls) == 1


def test_retry_preserves_query_and_failed_page(external_app, monkeypatch):
    app, calls = external_app
    search = web_chat.TOPIC_DISCOVERY.research
    app.run_turn("Search the web for papers about graphs", {})
    monkeypatch.setattr(web_chat.TOPIC_DISCOVERY, 'research', lambda *a, **kw: (_ for _ in ()).throw(DiscoveryUnavailable('Offline')))
    assert app.run_turn('five more', {})['web_search_failed']
    monkeypatch.setattr(web_chat.TOPIC_DISCOVERY, 'research', search)
    app.run_turn('retry', {})
    assert calls[-1]['query'] == 'graphs' and calls[-1]['offset'] == 8


def test_topic_prompt_courtesy_and_future_year_do_not_leak_or_fail():
    pending = web_search_request('Search the web')
    assert web_search_request('thanks', {'web_search': pending.public()}) is None
    future = web_search_request(f'Search the web for papers about graphs since {date.today().year + 1}')
    assert 'future' in future.prompt
    fixed = web_search_request('2024', {'web_search': future.public()})
    assert not fixed.prompt and fixed.since == '2024' and fixed.query == 'graphs'


def test_chat_http_queue_completes_external_search_without_a_model(external_app):
    app, calls = external_app
    handler = web_chat.build_handler(app)
    try:
        server = web_chat.create_server('127.0.0.1', 0, handler)
    except PermissionError:
        handler.turn_job_manager.shutdown()
        handler.paper_import_manager.shutdown()
        pytest.skip('Loopback unavailable')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, raw, _ = http_request(server.server_address[1], 'POST', '/api/chat',
            payload={'message': 'Search the web for three papers about graph colouring', 'settings': {}})
        assert status == 202, raw
        job = handler.turn_job_manager.wait_for_terminal(json.loads(raw)['job_id'])
        assert job['state'] == 'complete', job
        status, _, raw, _ = http_request(server.server_address[1], 'GET', '/api/job?job_id=' + job['job_id'])
        assert status == 200 and json.loads(raw)['result']['task'] == 'web_discovery'
        assert calls[0]['limit'] == 3 and calls[0]['query'] == 'graph colouring'
        assert handler.paper_import_manager.snapshot('anonymous') == []
    finally:
        server.shutdown()
        server.server_close()
        handler.turn_job_manager.shutdown()
        handler.paper_import_manager.shutdown()
        thread.join(timeout=5)


SPOKEN = ('Test, funktioniert das? Ja, großartig. Magst du bitte einmal kurz mir ein... '
          'kannst du mir zwei Paper finden, die online sind irgendwie? Und ich brauche zwei Paper, '
          'die am besten in Nature sind und was mit solar cells zu tun haben und am liebsten '
          'irgendwas mit perovskites.')


@pytest.mark.parametrize('message', [SPOKEN,
    'Hey, could you find two papers online about solar cells, preferably published in Nature?',
    'Find two online papers about solar cells, preferably in Nature',
    'I need two papers online about solar cells published in Nature',
])
def test_natural_web_request_is_prepared_not_mistaken_for_local_retrieval(message):
    request = web_search_request(message)
    assert request and request.needs_planning and request.limit == 2
    assert not request.query


@pytest.mark.parametrize('message', [
    'Could you look onlien for 2 papers about pervoskite solar cells, preferably in Nature Energy?',
    'Find papers on the intrenet about butterfly migration',
    'Search the web for papers about solar cells and battery storage',
    'Search online for papers about either diet or migration in penguins',
    'Webscraep papers about astronomy',
])
def test_typo_routing_and_all_new_topics_get_semantic_planning(message):
    request = web_search_request(message)
    assert request and request.needs_planning


def test_informal_negation_still_keeps_search_local():
    assert web_search_request('dont search online, find papers about astronomy') is None


@pytest.mark.parametrize('message', [
    'Hey, can you find papers in my library about online learning?',
    'Test, funktioniert das? Kannst du mir Paper finden, aber bitte nicht online suchen?',
    'Could you explain what this online paper found?',
    'I need papers about online learning',
])
def test_conversational_words_do_not_authorize_external_search(message):
    assert web_search_request(message) is None


def plan_data(**changes):
    if 'topic' in changes:
        changes['subject'] = changes.pop('topic')
    changes.pop('queries', None)
    return {'intent': 'web_search', 'subject': 'perovskite solar cells', 'topics': [], 'topic_mode': 'any',
            'journal': '', 'journal_mode': 'any', 'recent': False, 'since': '', 'limit': 2,
            'clarification': '', **changes}


def mock_search_plan(monkeypatch, *, response=None, error=None):
    calls = []
    def generate(self, settings, prompt, schema, **kwargs):
        if 'relevant' in schema['properties']:
            return '{"relevant":[{"id":0,"support":"A study of graphs"}]}'
        calls.append((prompt, kwargs))
        if error:
            raise ValueError(error)
        return response if response is not None else json.dumps(plan_data(journal='Nature', journal_mode='prefer'))
    monkeypatch.setattr(WebChatApp, 'discovery_completion', generate)
    return calls


def test_spoken_web_search_uses_verified_journal_filters_and_real_metadata(external_app, monkeypatch):
    app, searches = external_app
    planners = mock_search_plan(monkeypatch)
    monkeypatch.setattr(web_chat.TOPIC_DISCOVERY, 'journals', lambda *a: {'items': [
        {'title': 'Nature Communications', 'issns': ['2041-1723']},
        {'title': 'Nature', 'issns': ['0028-0836', '1476-4687']}]})
    result = app.run_turn(SPOKEN, {})
    assert result['task'] == 'web_discovery'
    assert len(planners) == len(searches) == 1
    assert searches[0]['issns'] == ['0028-0836', '1476-4687']
    assert searches[0]['query'] == 'perovskite solar cells' and searches[0]['limit'] == 2
    assert result['external_papers'][0]['journal'] == 'Graphs'
    assert 'Other journals are included' in result['answer']
    app.run_turn('include other journals', {})
    assert 'issns' not in searches[-1] and len(planners) == 1
    assert searches[-1]['query'] == 'perovskite solar cells'


@pytest.mark.parametrize('response,error', [('', 'Model unavailable'), ('not json', None)])
def test_failed_extraction_asks_instead_of_silent_local_fallback(external_app, monkeypatch, response, error):
    app, searches = external_app
    mock_search_plan(monkeypatch, response=response, error=error)
    result = app.run_turn(SPOKEN, {})
    assert not searches and result['task'] == 'web_discovery'
    assert 'could not prepare' in result['answer'] and result['web_search_failed']
    mock_search_plan(monkeypatch, response=json.dumps(plan_data()))
    app.run_turn('perovskite solar cells', {})
    assert searches[-1]['query'] == 'perovskite solar cells'


def test_journal_not_identified_does_not_substitute_a_related_journal(external_app, monkeypatch):
    app, searches = external_app
    mock_search_plan(monkeypatch, response=json.dumps(plan_data(journal='Nature', journal_mode='only')))
    monkeypatch.setattr(web_chat.TOPIC_DISCOVERY, 'journals', lambda *a: {'items': [
        {'title': 'Nature Communications', 'issns': ['2041-1723']}]})
    result = app.run_turn(SPOKEN, {})
    assert not searches and 'exact journal' in result['answer']
    assert result['web_search_failed']
    mock_search_plan(monkeypatch, response=json.dumps(plan_data(journal='Nature Communications', journal_mode='only')))
    result = app.run_turn('Nature Communications', {})
    assert searches[-1]['issns'] == ['2041-1723']
    assert result['external_papers'] and not result.get('web_search_failed')


@pytest.mark.parametrize('query', ['https://private.example/secret', 'email@private.example', '/Users/test/draft', ''])
def test_search_plan_cannot_export_paths_links_or_personal_addresses(query):
    request = web_search_request(SPOKEN)
    result = resolved_search_plan(request, SPOKEN, json.dumps(plan_data(topic=query, queries=[query])))
    assert result.prompt and result.planning_error


def test_more_search_uses_consumed_candidate_offset_not_display_count():
    prior = {'web_search': {'query': 'graphs', 'limit': 2, 'offset': 0, 'next_offset': 11}}
    request = web_search_request('two more', prior)
    assert request.offset == 11 and request.next_offset == 0
    assert web_search_request('only since 2025', prior).next_offset == 0


def test_journal_is_optional_not_a_reason_to_discard_a_usable_topic():
    request = web_search_request(SPOKEN)
    result = resolved_search_plan(request, SPOKEN, json.dumps(plan_data(journal_mode='prefer')))
    assert not result.prompt and result.query and result.journal_mode == 'any'
