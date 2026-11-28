import pytest

from originrag.retrieval.paper_lookup import citation_lookup, citation_candidates
from originrag.paper_discussion import mentioned_papers
from scripts import web_chat


def paper(key, title, year, journal="Nature Communications"):
    return dict(paper_id=key, author=key, title=title, year=year, journal=journal,
                doi_url="https://doi.org/10.1234/" + key, searchable=True)


@pytest.fixture
def chat(tmp_path, monkeypatch):
    rows = [paper("old", "Measuring X9B", "2023"), paper("new", "Seasonal X9B trends", "2026"),
            paper("other", "Tracking X9B", "2027", "Nature"),
            paper("topic", "Forest cover", "2027")]
    monkeypatch.setattr(web_chat, "library_catalog", lambda *a, **k: rows)
    monkeypatch.setattr(web_chat, "manager_for", lambda *a: pytest.fail("Unexpected model call"))
    return web_chat.WebChatApp(web_chat.WebChatSettings(), str(tmp_path / "sessions")), rows


@pytest.mark.parametrize("question", [
    "show me the resenctly published X9B paper in natuer comm",
    "show me the recently published paper on X9B in Nature Communications",
    "Zeig mir das kürzlich erschienene X9B Paper in Nature Comm",
    "coudlyou show me the latest X9B papre in natuercomm",
])
def test_show_one_paper_then_ask_about_it(chat, question, monkeypatch):
    app, _ = chat
    result = app.run_turn(question, {})
    assert result["task"] == "library_status"
    assert [p["paper_id"] for p in result["sources"]] == ["new"]
    assert "(https://doi.org/10.1234/new)" in result["answer"]
    monkeypatch.setattr(app, "discuss", lambda session, q, settings, paper_id, *a, **k: paper_id)
    assert app.run_turn("What were their findings?", {}) == "new"


@pytest.mark.parametrize("other_year", ["2026", ""])
def test_no_arbitrary_tiebreak_and_choice_needs_no_model(chat, other_year):
    app, rows = chat
    rows.append(paper("extra", "X9B observation", other_year))
    result = app.run_turn("Show me the latest X9B paper in Nature Comm", {})
    assert "Which title" in result["answer"]
    assert {p["paper_id"] for p in result["sources"]} == (
        {"old", "new", "extra"} if not other_year else {"new", "extra"})
    choice = app.run_turn("the second", {})
    assert choice["sources"] == [result["sources"][1]]


def test_no_joint_match_does_not_expand_to_wrong_papers(chat):
    app, _ = chat
    r = app.run_turn("Show me the latest X9D paper in Nature Comm", {})
    assert r["sources"] == [] and "No title" in r["answer"]


@pytest.mark.parametrize("correction", [
    "no thanks ubt i meant onyl the one new paper fpür X9B in nature comm",
    "No, thanks. I meant just the newest paper about X9B in Nature Communications.",
    "Nein danke, ich meinte nur das eine neue Paper zu X9B in Nature Comm",
])
def test_revise_previous_list_to_one_paper_without_rewrite(chat, correction):
    app, _ = chat
    before = app.run_turn("show me three papers about X9B", {})
    assert before["task"] == "sources" and len(before["sources"]) == 3
    after = app.run_turn(correction, {})
    assert after["task"] == "library_status"
    assert [p["paper_id"] for p in after["sources"]] == ["new"]


def test_negative_journal_is_not_a_positive_citation_clue(chat):
    _, rows = chat
    q = "No thanks, I meant the new X9B paper not in Nature Comm"
    assert citation_lookup(q) is None
    assert mentioned_papers(q, rows) == []


def test_followup_after_list_asks_for_paper_and_preserves_question(chat, monkeypatch):
    app, _ = chat
    app.run_turn("show me three papers about X9B", {})
    question = "What did this paper conclude?"
    result = app.run_turn(question, {})
    assert result["task"] == "library_status" and "Which title" in result["answer"]
    assert result["pending_paper_question"] == question
    def discuss(session, q, settings, paper_id, *args, clarified=None):
        assert clarified == question
        return paper_id
    monkeypatch.setattr(app, "discuss", discuss)
    assert app.run_turn("the second", {}) == result["sources"][1]["paper_id"]


def test_courtesy_preserves_lookup_context_and_pending_clarification(chat, monkeypatch):
    app, _ = chat
    app.run_turn("show me three papers about X9B", {})
    result = app.run_turn("What did this paper conclude?", {})
    assert app.run_turn("Thanks!", {})["task"] == "conversation"
    saved = str(app.session_dir)
    app.load_session(saved)
    assert len(app.read_session(saved)["messages"]) == 6
    def discuss(session, q, settings, paper_id, *args, clarified=None):
        assert clarified == "What did this paper conclude?"
        return paper_id
    monkeypatch.setattr(app, "discuss", discuss)
    assert app.run_turn("the second", {}) == result["sources"][1]["paper_id"]


def test_courtesy_does_not_load_the_catalogue_or_consume_attachment(chat, monkeypatch):
    app, _ = chat
    monkeypatch.setattr(web_chat, "library_catalog", lambda *a, **k: pytest.fail("Unneeded catalogue access"))
    monkeypatch.setattr(app, "finish_attachment", lambda *a: pytest.fail("Attachment must remain available"))
    assert app.run_turn("Danke!", {"_discussion_id": "A"})["answer"] == "Gern!"


@pytest.mark.parametrize("question,expected", [
    ("No thanks, I only meant the newest paper from that list.", "other"),
    ("Show me the oldest paper of those.", "old"),
    ("Zeig mir nur das neueste Paper aus dieser Liste.", "other"),
])
def test_recency_selects_only_from_displayed_list(chat, question, expected):
    app, _ = chat
    app.run_turn("show me three papers about X9B", {})
    app.run_turn("Thanks!", {})
    app.load_session(str(app.session_dir))
    result = app.run_turn(question, {})
    assert result["task"] == "library_status"
    assert [p["paper_id"] for p in result["sources"]] == [expected]


def test_ambiguous_recency_keeps_display_order_and_asks(chat):
    app, rows = chat
    rows[0]["year"] = "2027"
    result = app.run_turn("show me three papers about X9B", {})
    expected = [p for p in result["sources"] if p["year"] == "2027"]
    narrowed = app.run_turn("Only the newest paper from that list", {})
    assert [p["paper_id"] for p in narrowed["sources"]] == [p["paper_id"] for p in expected]
    chosen = app.run_turn("the second", {})
    assert [p["paper_id"] for p in chosen["sources"]] == [p["paper_id"] for p in expected[1:2]]


def test_discussion_follows_displayed_number_not_catalogue_order(chat, monkeypatch):
    app, _ = chat
    listed = app.run_turn("show me three papers about X9B", {})
    clarified = app.run_turn("What did this paper conclude?", {})
    assert [p["paper_id"] for p in clarified["sources"]] == [p["paper_id"] for p in listed["sources"]]
    monkeypatch.setattr(app, "discuss", lambda session, q, settings, paper_id, *a, **k: paper_id)
    assert app.run_turn("What did the second paper conclude?", {}) == listed["sources"][1]["paper_id"]


@pytest.mark.parametrize('question', [
    'Summarize the second one briefly.',
    'coudlyou please summraize the secnodpapre',
    'kannstdumirbitte das zwietePaper zusammenfassen',
    'plase summmarise paper2',
    'sum ma rize the second paper',
])
def test_numbered_summary_works_outside_repository_after_reload(chat, monkeypatch, question):
    app, _ = chat
    listed = app.run_turn("show me three papers about X9B", {})
    app.run_turn("Thanks!", {})
    app.load_session(str(app.session_dir))
    calls = []
    def summarize(session, q, settings, paper_id, *args, reading_choices=None, **kwargs):
        calls.append((paper_id, q))
        assert reading_choices == listed["sources"]
        return {"task": "paper_discussion"}
    monkeypatch.setattr(app, "discuss", summarize)
    app.run_turn(question, {})
    assert calls == [(listed["sources"][1]["paper_id"], question)]
    turn = web_chat.read_session_turns(app.session_dir)[0]
    context = web_chat.turn_context_from_saved_turn(turn, app.session_dir)
    assert context.source_titles == tuple(p["title"] for p in listed["sources"])
    assert context.answer_summary


def test_numbered_discussion_retains_original_list_through_followups_and_reload(chat, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from originrag.summaries import reading_card
    app, rows = chat
    by_id = {row["paper_id"]: row for row in rows}
    monkeypatch.setattr(reading_card, "load_paper", lambda paper_id, *a: (
        by_id[paper_id], [{"page_start": 1, "text": "This study measured observations at one site."}]))
    monkeypatch.setattr(web_chat, "manager_for", lambda *a: SimpleNamespace(session=lambda: nullcontext(None)))
    monkeypatch.setattr(web_chat.ask_corpus, "effective_model_context", lambda *a, **k: 4096)
    monkeypatch.setattr(web_chat.ask_corpus, "generate_answer_with_backend", lambda *a, **k:
                        SimpleNamespace(llm_result=SimpleNamespace(answer="One site was observed. [PDF p. 1]", error=None)))
    listed = app.run_turn("show me three papers about X9B", {})
    original = [row["paper_id"] for row in listed["sources"]]
    second = app.run_turn("What did the second paper conclude?", {})
    assert second["paper_id"] == original[1]
    assert [row["paper_id"] for row in second["reading_choices"]] == original
    followup = app.run_turn("What are its main limitations?", {})
    assert followup["paper_id"] == original[1]
    assert [row["paper_id"] for row in followup["reading_choices"]] == original
    app.load_session(str(app.session_dir))
    assert app.run_turn("What did the third paper conclude?", {})["paper_id"] == original[2]
    assert app.run_turn("Summarize the first one.", {})["paper_id"] == original[0]


def test_external_session_cannot_read_a_sibling_session(chat, tmp_path):
    import json
    app, _ = chat
    outside = tmp_path / "other" / "turn_001"
    outside.mkdir(parents=True)
    (outside / "public_result.json").write_text(json.dumps({"answer": "private"}))
    turn = {"output": str(outside)}
    assert web_chat.saved_turn_result(turn, app.session_dir) == {}
    assert not web_chat.turn_context_from_saved_turn(turn, app.session_dir).answer_summary


def test_saved_chat_files_cannot_follow_links_outside_their_turn(chat, tmp_path):
    app, _ = chat
    private = tmp_path / "private.json"
    private.write_text('{"answer":"Other session content"}')
    directory = app.session_dir / "turn_001"
    directory.mkdir()
    (directory / "public_result.json").symlink_to(private)
    (directory / "answer.md").symlink_to(private)
    turn = {"output": str(directory)}
    assert web_chat.saved_turn_result(turn, app.session_dir) == {}
    assert web_chat.saved_turn_answer(turn, app.session_dir) == ""
    assert not web_chat.turn_context_from_saved_turn(turn, app.session_dir).answer_summary


def test_empty_list_recency_does_not_search_again(chat):
    app, _ = chat
    result = app.run_turn("Only the newest paper from that list", {})
    assert result["task"] == "library_status" and not result["sources"]


@pytest.mark.parametrize("q", [
    "show me ten papers about X9B in Nature Comm",
    "show me the papers on X9B in Nature Comm",
    "show me the X9B paper in Nature Comm and explain its methods",
    "What did the X9B paper in Nature Comm find?",
    "Show me the latest paper on X9B in my library",
])
def test_other_chat_intents_not_claimed(q):
    assert citation_lookup(q) is None


@pytest.mark.parametrize('question', [
    'bitte einaml das neuste abc1xy paper geben das mit dem switchign aus nature comm',
    'Kannst du mir das neueste abc1xy Paper mit dem switchign aus Nature Comm geben?',
    'please show me the newest abc1xy paper about switchign in Nature Comm',
    'Could you give me the latest abc1xy switchign paper in natuer comm',
])
def test_flexible_citation_word_order_and_two_title_typos(chat, monkeypatch, question):
    app, rows = chat
    rows.extend([paper('switching', 'ABC1YX switching regulates transcription', '2026'),
                 paper('older', 'ABC1YX switching observations', '2023'),
                 paper('wrong-journal', 'ABC1YX switching', '2027', 'Nature')])
    # A standalone lookup must not call generation or enter the broad retrieval pipeline.
    monkeypatch.setattr(web_chat.ask_corpus, 'run_question', lambda *a, **kw: pytest.fail('Wrong RAG route'))
    result = app.run_turn(question, {})
    assert result['task'] == 'library_status'
    assert [p['paper_id'] for p in result['sources']] == ['switching']
    assert '“abc1xy” → “abc1yx”' in result['answer']
    assert '“switchign” → “switching”' in result['answer']
    monkeypatch.setattr(app, 'discuss', lambda session, q, settings, paper_id, *a, **kw: paper_id)
    assert app.run_turn('What did they find?', {}) == 'switching'


def test_typo_resolution_never_changes_digits_or_known_distinct_names():
    rows = [paper('one', 'ABCA1 transcription', '2026'),
            paper('two', 'ABCAX transcription', '2025')]
    for query in ['Show me the latest ABCA2 paper in Nature Comm',
                  'Show me the latest ABCAX paper in Nature Comm']:
        matches = citation_candidates(citation_lookup(query), rows)
        assert [p['paper_id'] for p in matches] == ([] if 'ABCA2' in query else ['two'])
    rows = [paper('exact', 'Migration and population structure', '2025'),
            paper('other', 'Migrations and population structure', '2026')]
    assert [p['paper_id'] for p in citation_candidates(citation_lookup(
        'Show me the latest migration paper in Nature Comm'), rows)] == ['exact']
    rows[0]['journal'] = 'Ecology'
    assert not citation_candidates(citation_lookup(
        'Show me the latest migration paper in Nature Comm'), rows)
    assert not citation_candidates(citation_lookup('Show me the latest SCNA1 paper in Nature Comm'),
                                   [paper('channel', 'SCN1A regulation', '2026')])


def test_ambiguous_title_typo_requires_a_choice(chat):
    app, rows = chat
    rows.extend([paper('one', 'Penguin migration', '2026'), paper('two', 'Penguin migrations', '2026')])
    result = app.run_turn('Show me the latest pengiun paper in Nature Comm', {})
    assert 'Which title' in result['answer']
    assert {p['paper_id'] for p in result['sources']} == {'one', 'two'}


def test_empty_quick_list_retries_spelling_then_paginates_corrected_topic(chat, monkeypatch):
    from originrag.retrieval import quick_list
    app, rows = chat
    monkeypatch.setattr(quick_list, 'indexed_list_ids', lambda *a, **k: [])
    rows.extend(paper(f'spatial-{n}', f'Spatial transcriptomics study {n}', '2026') for n in range(4))
    question = 'Coudlyou showme 2 papres about sptial transcritpomics'
    result = app.run_turn(question, {})
    assert len(result['sources']) == 2
    assert result['list_page']['topic'] == 'spatial transcriptomics'
    assert '“sptial” → “spatial”' in result['answer']
    assert '“sptial” → “spatial”' in result['search_note']
    assert web_chat.read_session_turns(app.session_dir)[-1]['user'] == question
    app.load_session(str(app.session_dir))
    more = app.run_turn('Shwo me 2 more papres', {})
    assert len(more['sources']) == 2
    assert not ({p['paper_id'] for p in result['sources']} & {p['paper_id'] for p in more['sources']})
    exhausted = app.run_turn('Shwo me 2 more papres', {})
    assert exhausted['sources'] == []


def test_exact_quick_list_matches_never_trigger_spelling_retry(chat, monkeypatch):
    from originrag.retrieval import quick_list
    app, _ = chat
    monkeypatch.setattr(quick_list, 'topic_spelling_candidate', lambda *a: pytest.fail('Unneeded spelling retry'))
    assert app.run_turn('Show me 2 papers about X9B', {})['sources']


@pytest.mark.parametrize('category,since', [('private', 0), ('', 2027)])
def test_spelling_retry_keeps_category_and_date_filters(category, since, monkeypatch):
    from originrag.retrieval import quick_list
    monkeypatch.setattr(quick_list, 'indexed_list_ids', lambda *a, **k: [])
    row = {**paper('spatial', 'Spatial transcriptomics', '2026'), 'category': 'public'}
    request = quick_list.PaperList('sptial transcritpomics', 2, 'en', bool(since), since)
    result = web_chat.quick_paper_list(request, [row], web_chat.WebChatSettings(category=category))
    assert result['sources'] == []


@pytest.mark.parametrize('question', [
    'Can you discuss the new ABC1XY paper in Nature Comm?',
    'Bitte das ABC1XY Paper aus Nature Comm erklären',
    'Kannst du mir das neue ABC1XY Paper in Nature Comm zusammenfassen?',
    'Please give me three ABC1XY papers in Nature Comm',
    'Bitte das ABC1XY Paper nicht aus Nature Comm geben',
])
def test_conversational_lookup_does_not_swallow_other_actions(question):
    assert citation_lookup(question) is None


@pytest.mark.parametrize('question', [
    'Give the full title and first author of this paper, in one sentence.',
    'Please summarize this paper in English.',
    'Gib mir den Titel dieses Papers in zwei Sätzen.',
])
def test_output_instructions_are_not_a_journal_name(question):
    assert citation_lookup(question) is None
