import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from originrag.analysis.conversation_memory import compact_history, follows_paper_topic, history_turns, relevant_excerpt, returning_paper_id, returning_to_paper
from originrag.analysis.context_budget import estimate_prompt_tokens
from originrag.paper_discussion import discussion_prompt
from originrag.paper_discussion import refers_to_paper
from scripts import web_chat as web


@pytest.mark.parametrize("question", [
    "So does that prove that no protein was produced? Please answer briefly.",
    "Heißt das, dass es gar keinen Unterschied gab?", "Why not?", "Bitte kürzer!",
    "Can we conclude that the measurement is reliable?", "Can you explain that more simply?",
    "Bitte nur zwei Sätze, ohne Fachjargon: was bedeutet das?",
    "Bitte in drei kurzen Sätzen: welche Grenzen hat diese Auswertung?",
    "Does that comparison establish causality?", "What can we infer from this figure?",
    "Bitte in zwei Sätzen: was ist die wichtigste Einschränkung?",
    "What is the main limitation?", "What's the main result?", "Was ist das wichtigste Ergebnis?",
])
def test_conversational_followups_stay_with_the_selected_paper(question):
    assert refers_to_paper(question)


@pytest.mark.parametrize("question", ["How does a microscope work?", "Explain neuronal migration.",
                                        "Which journals publish botanical surveys?",
                                        "What are the limitations of fluorescence microscopy?",
                                        "Was sind die Grenzen von Solarzellen?"])
def test_new_questions_do_not_become_paper_followups(question):
    assert not refers_to_paper(question)


@pytest.mark.parametrize("question,expected", [
    ("Was humidity measured directly?", True),
    ("So were there 200 sensors in total?", True),
    ("Wurde die Luftfeuchtigkeit gemessen?", True),
    ("Does a microscope detect cancer?", False),
    ("What were the findings?", False),
    ("Was there a significant difference in the study?", False),
])
def test_elliptical_followup_uses_content_not_generic_question_words(question, expected):
    previous = "What was measured?"
    answer = "There were 120 sensors. Humidity (Luftfeuchtigkeit) was not measured. The study found a significant difference."
    assert follows_paper_topic(question, previous, answer) == expected


def test_negated_return_does_not_change_scope():
    assert returning_to_paper("Back to the original paper")
    assert not returning_to_paper("Do not go back to the original paper")


@pytest.mark.parametrize("question, expected", [
    ("Back to the original paper", "A"),
    ("Zurück zum ursprünglichen Paper", "A"),
    ("Back to the previous paper", "B"),
    ("Zurück zum vorherigen Paper", "B"),
    ("Back to the paper", "C"),
    ("Do not go back to the original paper", None),
    ("Explain a different topic", None),
])
def test_return_respects_original_previous_and_latest_paper(question, expected):
    turns = [{"task": "paper_discussion", "paper_id": key} for key in ("A", "B", "B", "C", "C")]
    turns.append({"task": "sources", "paper_id": "D"})
    assert returning_paper_id(question, turns) == expected
    assert returning_paper_id(question, []) is None


def test_memory_can_retain_a_relevant_sentence_after_a_long_preamble():
    text = "An unrelated introductory sentence. " * 40 + "The control group was not randomized."
    excerpt = relevant_excerpt(text, {"control", "randomized"}, 160)
    assert "control group was not randomized" in excerpt
    assert len(excerpt) <= 160
    assert excerpt.endswith(" …")


@pytest.mark.parametrize("budget", [80, 400, 1000])
def test_history_budget_is_bounded_and_raw_history_unchanged(budget):
    history = [("Earlier question " + str(i), "The observation was inconclusive. " * 40) for i in range(100)]
    history.append(("What did the control group show?", "No difference."))
    before = list(history)
    memory, compacted = compact_history(history, "Was that randomized?", budget)
    assert compacted and history == before
    assert memory[-1][0] == history[-1][0]
    assert estimate_prompt_tokens(json.dumps(memory, ensure_ascii=False)) <= budget


def test_disk_reads_bounded_and_no_other_paper_history():
    turns = [{"task": "paper_discussion", "paper_id": "A", "user": f"Question {i}"} for i in range(500)]
    turns[50]["user"] = "Was the measurement blinded?"
    turns.append({"task": "paper_discussion", "paper_id": "B", "user": "Blinded other paper"})
    chosen, total = history_turns(turns, "A", "Was that measurement blinded?")
    assert total == 500 and len(chosen) == 32
    assert turns[50] in chosen and chosen[-1] == turns[499] and turns[-1] not in chosen


def test_context_accounting_and_duplicate_passages():
    info = {}
    chunks = [{"page_start": 1, "text": "Repeated exact passage. " * 12}] * 8
    chunks.append({"page_start": 2, "text": "A separate result worth reading."})
    prompt, _ = discussion_prompt("What were the findings?", "Survey", chunks, [], 2048, 400, context_info=info)
    payload = json.loads(prompt.split("\n", 1)[1])
    assert len(payload["excerpts"]) == 2
    assert info["source_scope"] == "excerpts"
    assert info["prompt_tokens_estimated"] + 400 + 128 <= 2048
    pages = [{"page": 1, "text": "The full article."}]
    discussion_prompt("What were the findings?", "Survey", [], [], 2048, 400, pages=pages, context_info=info)
    assert info["source_scope"] == "full_text"


def test_followup_keeps_identical_article_prefix_without_dropping_evidence():
    pages = [{"page": 1, "text": "A complete observation."}, {"page": 2, "text": "A qualification."}]
    first, _ = discussion_prompt("What was observed?", "Survey", [], [], 4096, 400, pages=pages)
    second, _ = discussion_prompt("What can we conclude?", "Survey", [],
                                 [("What was observed?", "An observation.")], 4096, 400, pages=pages)
    assert first.split(', "conversation":')[0] == second.split(', "conversation":')[0]
    assert json.loads(first.split("\n", 1)[1])["excerpts"] == pages
    assert json.loads(second.split("\n", 1)[1])["excerpts"] == pages
    assert "What can we conclude?" in second and "An observation." in second


@pytest.mark.parametrize("question", [
    "Was outdoor performance measured?", "Bitte auf Deutsch: Wie viele Sensoren wurden untersucht?",
    "Please explain in French whether outdoor performance was measured.",
])
def test_paper_discussion_language_follows_current_request_not_old_answer(question):
    prompt, _ = discussion_prompt(question, "Sensor calibration", [],
                                  [("Bitte auf Deutsch", "Es wurden 36 Sensoren untersucht.")],
                                  4096, 400, pages=[{"page": 1, "text": "We evaluated 36 sensors indoors."}])
    payload = json.loads(prompt.split("\n", 1)[1])
    assert payload["question"] == question
    assert "English unless this question explicitly requests another language" in payload["response_language"]
    assert "do not inherit a previous answer's language" in payload["response_language"]


def test_excerpts_report_only_pages_actually_supplied_to_model():
    info = {}
    pages = [{"page": 99, "text": "Unselected original page. " * 3000}]
    chunks = [{"page_start": 3, "text": "Humidity was not measured in either group."}]
    prompt, _ = discussion_prompt("Was humidity measured?", "Sensors", chunks, [], 2048, 400,
                                  pages=pages, context_info=info)
    assert info["source_scope"] == "excerpts"
    assert info["source_pages"] == [3]
    assert '"page": 99' not in prompt


def test_saved_chat_reopens_and_returns_to_paper_after_library_lookup(tmp_path, monkeypatch):
    papers = tmp_path / "papers.tsv"
    papers.write_text("paper_id\ttitle\tauthor\tyear\nA\tMeasuring rain\tKlein\t2020\nB\tObserving light\tYoung\t2021\n")
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    for key in ("A", "B"):
        (chunks / (key + ".jsonl")).write_text(json.dumps({"paper_id": key, "text": "No randomized control group was used."}))
    monkeypatch.setattr(web, "manager_for", lambda root: SimpleNamespace(session=lambda: nullcontext(None)))
    monkeypatch.setattr(web.ask_corpus, "effective_model_context", lambda *a, **k: 4096)
    prompts = []
    def generate(prompt, **kwargs):
        prompts.append(json.loads(prompt.split("\n", 1)[1]))
        return SimpleNamespace(llm_result=SimpleNamespace(answer="No randomized control group was used. Humidity was not measured.", error=None))
    monkeypatch.setattr(web.ask_corpus, "generate_answer_with_backend", generate)
    settings = web.WebChatSettings(papers=str(papers), chunks_dir=str(chunks))
    app = web.WebChatApp(settings, str(tmp_path / "sessions"))
    assert app.run_turn("Explain the methods in Klein (2020)", {})["paper_id"] == "A"
    saved = str(app.session_dir)
    assert app.run_turn("show me papers about light", {})["task"] == "sources"
    reloaded = web.WebChatApp(settings, str(tmp_path / "sessions"))
    reloaded.load_session(saved)
    result = reloaded.run_turn("Back to the original paper: was the control randomized?", {})
    assert result["paper_id"] == "A" and len(prompts[-1]["conversation"]) == 1
    assert result["context_info"]["history_turns_available"] == 1
    assert "Observing light" not in json.dumps(prompts[-1])
    assert web.read_session_turns(app.session_dir)[-1]["paper_id"] == "A"
    assert reloaded.run_turn("So does that prove the method is unbiased?", {})["paper_id"] == "A"
    assert len(prompts[-1]["conversation"]) == 2
    assert reloaded.run_turn("Explain the methods in Young (2021)", {})["paper_id"] == "B"
    assert not prompts[-1]["conversation"]
    assert reloaded.run_turn("Back to the original paper: what were the limitations?", {})["paper_id"] == "A"
    assert len(prompts[-1]["conversation"]) == 3
    assert "Observing light" not in json.dumps(prompts[-1])
    assert reloaded.run_turn("Thanks!", {})["task"] == "conversation"
    assert reloaded.run_turn("Was humidity measured directly?", {})["paper_id"] == "A"
    assert len(prompts[-1]["conversation"]) == 4
    assert "Thanks" not in json.dumps(prompts[-1])
    count = len(prompts)
    calls = []
    def new_topic(args, turn_dir, progress_callback=None):
        calls.append((args.retrieval_question, getattr(args, "conversation_context", None)))
        turn_dir.mkdir(parents=True)
        path = turn_dir / "answer.md"
        path.write_text("A separate topic.")
        return {"answer_path": path, "pack": {"chunks": []}, "answer_qc": {"passed": True}}
    monkeypatch.setattr(web.ask_corpus, "run_question", new_topic)
    question = "New question: what does this microscope measure?"
    result = reloaded.run_turn(question, {})
    assert result["task"] == "answer" and len(prompts) == count
    assert calls == [(question, None)]
