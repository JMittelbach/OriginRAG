import io
import json
from urllib.parse import parse_qs, urlparse

import pytest

from originrag.discovery import topic_search as discovery


@pytest.fixture(autouse=True)
def no_network_delay(monkeypatch):
    monkeypatch.setattr(discovery, '_CROSSREF_INTERVAL', 0)


def fake_response(monkeypatch, items):
    requests = []

    def respond(request, timeout):
        requests.append((request, timeout))
        return io.BytesIO(json.dumps({"message": {"items": items}}).encode())

    monkeypatch.setattr(discovery, "urlopen", respond)
    return requests


def work(**overrides):
    return {"DOI": "10.1234/TEST", "title": ["Graph colouring"],
            "author": [{"family": "Example"}], "published": {"date-parts": [[2024]]},
            "container-title": ["Journal of Mathematics"], "type": "journal-article", **overrides}


@pytest.mark.parametrize("topic", ["graph colouring", "medieval literature", "solar cells", "macroeconomic policy"])
def test_any_topic_is_forwarded_without_domain_filters(monkeypatch, topic):
    requests = fake_response(monkeypatch, [work()])
    result = discovery.search_topic(topic, since="2020", order="newest")
    request, timeout = requests[0]
    params = parse_qs(urlparse(request.full_url).query)
    assert params["query"] == [topic]
    assert params["sort"] == ["published"]
    assert "from-pub-date:2020-01-01" in params["filter"][0]
    assert "type:journal-article" in params["filter"][0]
    assert "malaria" not in request.full_url
    assert timeout == 15
    assert result["items"][0]["doi"] == "10.1234/test"
    assert result["downloaded"] is False


def test_metadata_deduplication_and_links_are_safe(monkeypatch):
    fake_response(monkeypatch, [work(), work(), work(DOI="javascript:alert(1)"),
                               work(DOI="10.1234/other", title=["<script>attack</script>Safe <i>title</i>"]),
                               work(DOI="10.1234/script-only", title=["<script>attack</script>"]),
                               work(DOI="10.1234/blank", title=[]), None])
    result = discovery.search_topic("anything", limit=12)
    assert len(result["items"]) == 2
    assert all(item["url"].startswith("https://doi.org/") for item in result["items"])
    assert result["items"][1]["title"] == "Safe title"


@pytest.mark.parametrize("kwargs", [{"query": ""}, {"query": "x" * 241},
    {"query": "topic", "since": "1900&bad=yes"}, {"query": "topic", "since": "1799"},
    {"query": "topic", "order": "anything"}, {"query": "topic", "limit": 200},
    {"query": "topic", "kind": "all,type:bad"}])
def test_invalid_inputs_do_not_make_network_calls(monkeypatch, kwargs):
    requests = fake_response(monkeypatch, [])
    with pytest.raises(ValueError):
        discovery.search_topic(**kwargs)
    assert requests == []


def test_errors_and_oversized_responses_are_bounded(monkeypatch):
    monkeypatch.setattr(discovery, "urlopen", lambda *a, **k: io.BytesIO(b"x" * (discovery.MAX_RESPONSE_BYTES + 1)))
    with pytest.raises(discovery.DiscoveryUnavailable, match="unavailable"):
        discovery.search_topic("topic")
    monkeypatch.setattr(discovery, "urlopen", lambda *a, **k: io.BytesIO(b'{}'))
    with pytest.raises(discovery.DiscoveryUnavailable):
        discovery.search_topic("topic")


def test_rate_and_concurrency_limits_and_release_after_failure(monkeypatch):
    service = discovery.TopicDiscovery()
    fake_response(monkeypatch, [])
    assert service.search("alice", query="graphs")["items"] == []
    with pytest.raises(discovery.DiscoveryBusy):
        service.search("alice", query="graphs")
    assert service.slots.acquire(False)
    assert service.slots.acquire(False)
    with pytest.raises(discovery.DiscoveryBusy):
        service.search("bob", query="graphs")
    service.slots.release()
    service.slots.release()
    with pytest.raises(ValueError):
        service.search("invalid", query="")
    assert service.search("next", query="graphs")["items"] == []
    for number in range(150):
        service.search(str(number), query="graphs")
    assert len(service.last_requests) == 128


def test_journal_lookup_normalises_identifiers_and_caches(monkeypatch):
    requests = fake_response(monkeypatch, [
        {"title": "Nature", "publisher": "Springer Nature", "ISSN": ["1476-4687", "0028-0836"]},
        {"title": "Duplicate", "ISSN": ["0028-0836", "1476-4687"]},
        {"title": "Invalid", "ISSN": ["1234-5678", "https://localhost"]},
        {"title": "Bad data", "ISSN": "0028-0836"},
    ])
    service = discovery.TopicDiscovery()
    result = service.journals("alice", "Nature")
    assert len(result["items"]) == 1
    assert result["items"][0]["issns"] == ["0028-0836", "1476-4687"]
    assert service.journals("bob", " nature ") == result
    assert len(requests) == 1
    assert urlparse(requests[0][0].full_url).path == "/journals"
    with pytest.raises(ValueError):
        service.journals("bob", "x")


def test_exact_multi_journal_filter_is_not_fuzzy_title_matching(monkeypatch):
    requests = fake_response(monkeypatch, [work(ISSN=["0028-0836"]),
        work(DOI="10.1234/second", ISSN=["0036-8075"]),
        work(DOI="10.1234/outsider", ISSN=["2041-1723"]),
        work(DOI="10.1234/malformed", ISSN=[{}]), work(DOI="10.1234/missing")])
    result = discovery.search_topic("graphs", issns=["0028-0836", "0036-8075"])
    assert [i["doi"] for i in result["items"]] == ["10.1234/test", "10.1234/second"]
    params = parse_qs(urlparse(requests[0][0].full_url).query)
    assert ",issn:0028-0836,issn:0036-8075" in params["filter"][0]
    assert "query.container-title" not in params


def test_journal_only_browsing_uses_publication_order(monkeypatch):
    requests = fake_response(monkeypatch, [work(ISSN=["0028-0836"])])
    assert discovery.search_topic("", issns=["0028-0836"])["items"]
    assert parse_qs(urlparse(requests[0][0].full_url).query)["sort"] == ["published"]


def test_exact_journal_name_precedes_similar_names(monkeypatch):
    fake_response(monkeypatch, [
        {"title": "Naturen", "ISSN": ["0028-0887"]},
        {"title": "Nature Medicine", "ISSN": ["1078-8956"]},
        {"title": "Nature", "ISSN": ["0028-0836"]},
    ])
    assert discovery.search_journals("nature")["items"][0]["title"] == "Nature"


@pytest.mark.parametrize("issns", ["0028-0836", ["1234-5678"], ["0028-0836,type:all"], [None], ["0028-0836"] * 21])
def test_invalid_journal_restrictions_never_broaden_search(monkeypatch, issns):
    requests = fake_response(monkeypatch, [])
    with pytest.raises(ValueError):
        discovery.search_topic("history", issns=issns)
    assert not requests


def test_abstracts_are_bounded_plain_text_and_paging_is_explicit(monkeypatch):
    requests = fake_response(monkeypatch, [work(abstract='<jats:p>A <italic>graph</italic> &amp; its edges.</jats:p><script>bad()</script><jats:p>Next paragraph.</jats:p>')])
    result = discovery.search_topic('graph algorithms', offset=20)
    assert result['items'][0]['abstract'] == 'A graph & its edges. Next paragraph.'
    params = parse_qs(urlparse(requests[0][0].full_url).query)
    assert params['offset'] == ['20'] and 'abstract' in params['select'][0].split(',')
    assert discovery.abstract_text(None) == ''
    assert len(discovery.abstract_text('a' * 50000)) == 8001
    with pytest.raises(ValueError):
        discovery.search_topic('graphs', offset=201)
    assert len(requests) == 1


def test_chat_search_does_not_fill_requested_count_with_partial_topic_matches(monkeypatch):
    requests = fake_response(monkeypatch, [
        work(DOI='10.1234/unrelated', title=['Graph algorithms']),
        work(DOI='10.1234/first', title=['Graph colouring with fast algorithms']),
        work(DOI='10.1234/second', title=['Graph colouring using fast methods']),
        work(DOI='10.1234/third', title=['Graph colouring using fast heuristics'])])
    result = discovery.search_topic('fast graph colouring', limit=2, match_topic=True, offset=20)
    assert [p['doi'] for p in result['items']] == ['10.1234/first', '10.1234/second']
    assert result['next_offset'] == 23
    assert parse_qs(urlparse(requests[0][0].full_url).query)['rows'] == ['20']
    fake_response(monkeypatch, [work(title=['Unrelated graph theory'])])
    result = discovery.search_topic('fast graph colouring', limit=2, match_topic=True)
    assert result['items'] == [] and result['next_offset'] == 1


def test_topic_screen_reads_abstract_not_journal_and_retains_identifier_boundaries():
    assert discovery.topic_in_metadata('solar cells', 'A solar cell')
    assert not discovery.topic_in_metadata('NOTCH1', 'NOTCH10')
    assert discovery.topic_in_metadata('graphs without labels', 'Graph labelling')
