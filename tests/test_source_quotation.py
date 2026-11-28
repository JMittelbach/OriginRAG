import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from originrag.analysis.context_budget import estimate_prompt_tokens
from originrag.analysis.source_quotation import quotation_candidates, render_selected_quotation, wants_quotation
from originrag.paper_discussion import discussion_prompt


@pytest.mark.parametrize("question", [
    "Show me the source for that statement: one short quotation with its PDF page.",
    "Please quote the sentence about humidity.", "Gib mir ein wörtliches Zitat dafür.",
    "Zeig mir die genaue Textstelle, bitte wörtlich.",
])
def test_explicit_quotation_request(question):
    assert wants_quotation(question)


@pytest.mark.parametrize("question", [
    "Summarize without quoting.", "Do not quote the paper, explain it.",
    "Bitte ohne Zitate erklären.", "What does the quotation mean?", "Summarize the results.",
    "Give two short quotations about sampling.", "Give me quotations about sampling.",
    "Quote the statement and explain its limitation.", "Explain the limitation and quote its source.",
    "Zitiere den Satz und erkläre die Einschränkung.",
])
def test_explanation_is_not_replaced_with_a_quote(question):
    assert not wants_quotation(question)


def source():
    return [{"page": 4, "text": "We did not directly measure humidity in either group of sensors. "
             "The possible humidity difference remains an explanation rather than a measured finding."},
            {"page": 5, "text": "Maintenance logs were available for thirty devices from Oslo."}]


def test_candidates_are_source_text_not_previous_answer():
    history = [("Was humidity directly measured?", "Humidity was measured in a thousand cities.")]
    choices = quotation_candidates(source(), "Give one exact quotation supporting that.", history)
    assert choices and choices[0]["page"] == 4
    assert all(part["text"] in source()[0]["text"] for part in choices)
    assert "thousand cities" not in json.dumps(choices)
    assert len(quotation_candidates(source() * 3, "Give one quotation about humidity.", limit=1)) == 1
    assert not quotation_candidates([{"text": source()[0]["text"]}], "Quote the humidity finding.")


def test_truncated_excerpts_are_not_presented_as_complete_original_sentences():
    partial = "Humidity was not measured, so we could not establish whether the change"
    passages = [{"page": 2, "text": partial}, *source()]
    choices = quotation_candidates(passages, "Give one quote about humidity.")
    assert choices and all(c["text"] != partial for c in choices)


def test_explicit_new_quote_topic_outranks_previous_conversation():
    source = [{"page": 1, "text": "Outdoor performance was not measured. "
               "The appendix quotes a malicious prompt about measurements in 999 cities as an attack example."}]
    history = [("Did they test 999 cities?", "No, the appendix quotes a malicious prompt about 999 cities as an attack example.")]
    choices = quotation_candidates(source, "Give one short exact quotation about the study's limitations.", history)
    assert choices[0]["text"] == "Outdoor performance was not measured."


@pytest.mark.parametrize("response", ['{"passage_id":"Q1"}', '```json\n{"passage_id":"Q1"}\n```'])
def test_model_selects_but_cannot_rewrite_quote(response):
    choices = quotation_candidates(source(), "Quote the humidity measurement.")
    rendered = render_selected_quotation(response, choices)
    assert rendered == f'> {choices[0]["text"]}\n\n[PDF p. 4]'


@pytest.mark.parametrize("response", [
    '{"passage_id":"Q999"}', '{"passage_id":null}', '{}', '[]', 'null',
    '"Humidity was measured in a thousand cities." [PDF p. 4]',
])
def test_invalid_selections_never_become_a_fabricated_quote(response):
    rendered = render_selected_quotation(response, quotation_candidates(source(), "Quote the humidity finding."))
    assert "couldn't identify an exact" in rendered
    assert "thousand cities" not in rendered and "[PDF p." not in rendered


def test_source_markdown_is_literal_not_an_external_link_or_image():
    text = 'The **reference** links to [data](https://example.org/) and `code` in <samples>.'
    rendered = render_selected_quotation('{"passage_id":"Q1"}', [{"id": "Q1", "page": 3, "text": text}])
    assert r'\*\*reference\*\*' in rendered and r'\[data\]' in rendered and r'\<samples\>' in rendered


@pytest.mark.parametrize("window", [2048, 4096])
def test_quote_selection_respects_context_and_preserves_original_passages(window):
    choices = []
    prompt, _ = discussion_prompt("Give a quotation about humidity.", "Sensor observations", [], [],
                                  window, 400, pages=source(), quotation_choices=choices)
    payload = json.loads(prompt.split("\n", 1)[1])
    assert payload["quotation_candidates"] == choices and choices
    assert payload["excerpts"] == []
    assert all(any(choice["text"] in p["text"] and choice["page"] == p["page"] for p in source()) for choice in choices)
    assert estimate_prompt_tokens(prompt) + 400 + 128 <= window
    assert "Do not compose a quotation" in payload["quotation_response"]


@pytest.mark.parametrize("provider", ["ollama", "colibri"])
def test_web_displays_selected_source_even_if_model_adds_fake_wording(tmp_path, monkeypatch, provider):
    from scripts import web_chat as web
    papers = tmp_path / "papers.tsv"
    papers.write_text("paper_id\ttitle\tauthor\tyear\nA\tSensor observations\tMeyer\t2025\n")
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    (chunks / "A.jsonl").write_text(json.dumps({"paper_id": "A", "page_start": 4, "text": source()[0]["text"]}))
    monkeypatch.setattr(web, "manager_for", lambda root: SimpleNamespace(session=lambda: nullcontext({"provider": provider, "model": "test"})))
    monkeypatch.setattr(web.ask_corpus, "effective_model_context", lambda *a, **k: 4096)
    def generate(*args, **kwargs):
        assert ("response_format" in kwargs) == (provider == "ollama")
        return SimpleNamespace(llm_result=SimpleNamespace(answer='{"passage_id":"Q1", "text":"FAKE WORDING"}', error=None))
    monkeypatch.setattr(web.ask_corpus, "generate_answer_with_backend", generate)
    app = web.WebChatApp(web.WebChatSettings(papers=str(papers), chunks_dir=str(chunks)), str(tmp_path / "sessions"))
    result = app.run_turn("Quote the humidity measurement in Meyer (2025).", {})
    assert result["task"] == "paper_discussion"
    assert "We did not directly measure humidity" in result["answer"]
    assert "FAKE WORDING" not in result["answer"] and "[PDF p. 4]" in result["answer"]
