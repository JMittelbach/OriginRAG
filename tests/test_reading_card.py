import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from originrag.analysis.ollama_client import OllamaResult
from originrag.summaries import reading_card as rc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ask_corpus  # noqa: E402
import web_chat  # noqa: E402

PAPER = {"paper_id": "P1", "title": "A numerical method", "doi": "10.1234/numerical", "year": "2025"}
TEXT = "The authors compared two numerical methods on synthetic examples."
CHUNKS = [{"paper_id": "P1", "chunk_id": "P1_1", "section": "abstract", "text": TEXT, "page_start": 1, "page_end": 1}]


def response(data, error=None):
    return OllamaResult(json.dumps(data) if data is not None else None, "test", "http://localhost", True, error)


def draft():
    return {"overview": [{"text": "Two numerical methods were compared on synthetic examples.",
                          "source": "S1", "quote": TEXT}]}


def fake_model(monkeypatch, *, output=None, supported=None):
    calls = []
    def call(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return response({"supported": [0] if supported is None else supported} if "Check a reading card" in prompt
                        else draft() if output is None else output)
    monkeypatch.setattr(rc, "call_ollama_generate", call)
    return calls


def corpus(tmp_path, rows=None, chunks=CHUNKS):
    papers = tmp_path / "papers.tsv"
    with papers.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*PAPER, "abstract"], delimiter="\t")
        writer.writeheader()
        writer.writerows(rows or [PAPER])
    root = tmp_path / "chunks"
    root.mkdir(exist_ok=True)
    if chunks is not None:
        (root / "P1.jsonl").write_text("\n".join(json.dumps(c) for c in chunks))
    return papers, root


@pytest.mark.parametrize("message,expected", [
    ("Summarize this paper: 10.1234/numerical", ("10.1234/numerical", "en")),
    ("/summary A numerical method", ("A numerical method", "en")),
    ("Zusammenfassung: A numerical method", ("A numerical method", "en")),
    ("Zusammenfassung auf Deutsch: A numerical method", ("A numerical method", "de")),
    ("Fasse dieses Paper zusammen: 10.1234/numerical", ("10.1234/numerical", "en")),
    ("Which methods have independent replication?", None),
    ("Could you summarize the paper titled A numerical method?", ("A numerical method", "en")),
    ("Fass mir bitte das Paper A numerical method zusammen.", ("A numerical method", "de")),
    ("Summarize the findings of the papers on calibration.", None),
])
def test_explicit_request(message, expected):
    assert rc.summary_request(message) == expected


@pytest.mark.parametrize("target", ["P1", "A numerical method", "numerical method", "https://doi.org/10.1234/numerical"])
def test_resolves_one_local_paper(tmp_path, target):
    paths = corpus(tmp_path)
    paper, chunks = rc.load_paper(target, *paths)
    assert paper["paper_id"] == "P1" and chunks == CHUNKS


def test_ambiguous_and_unknown_targets_fail(tmp_path):
    paths = corpus(tmp_path, [PAPER, dict(PAPER, paper_id="P2")])
    for target in (PAPER["doi"], "../secret", "unknown"):
        with pytest.raises(ValueError, match="uniquely"):
            rc.load_paper(target, *paths)


def test_cannot_follow_text_symlink_outside_corpus(tmp_path):
    papers, root = corpus(tmp_path, chunks=None)
    outside = tmp_path / "private.jsonl"
    outside.write_text(json.dumps(CHUNKS[0]))
    (root / "P1.jsonl").symlink_to(outside)
    with pytest.raises(ValueError, match="inside"):
        rc.load_paper("P1", papers, root)


def test_summarize_first_two_uses_separate_cards_without_registry(tmp_path, monkeypatch):
    from contextlib import nullcontext
    monkeypatch.setattr(web_chat, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ask_corpus, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(web_chat, "manager_for", lambda *a: SimpleNamespace(session=lambda: nullcontext(None)))
    monkeypatch.setattr(ask_corpus, "understand_question", lambda *a, **k: pytest.fail("Not a corpus synthesis"))
    calls = fake_model(monkeypatch)
    papers, chunks = corpus(tmp_path, [dict(PAPER, paper_id=f"P{i}", title=f"Numerical method {i}",
                                            doi="", abstract=TEXT) for i in range(4)], chunks=None)
    app = web_chat.WebChatApp(web_chat.WebChatSettings(papers=str(papers), chunks_dir=str(chunks)),
                              str(tmp_path / "sessions"))
    assert len(app.run_turn("List papers on numerical method", {})["sources"]) == 4
    assert app.run_turn("Summarize those papers", {})["task"] == "library_status"
    assert not calls
    result = app.run_turn("Summarize the first two papers", {})
    assert result["task"] == "paper_summaries" and len(calls) == 4
    assert [p["title"] for p in result["sources"]] == ["Numerical method 0", "Numerical method 1"]
    assert all(p["reading_card"]["coverage"]["basis"] == "abstract_only" for p in result["parts"])


def test_wrong_paper_text_rejected(tmp_path):
    paths = corpus(tmp_path, chunks=[dict(CHUNKS[0], paper_id="P2")])
    with pytest.raises(ValueError, match="different paper"):
        rc.load_paper("P1", *paths)


def test_metadata_only_never_fabricates_summary(tmp_path):
    paths = corpus(tmp_path, chunks=None)
    with pytest.raises(ValueError, match="metadata"):
        rc.load_paper("P1", *paths)


def test_abstract_fallback_is_labelled(tmp_path):
    paper, chunks = rc.load_paper("P1", *corpus(tmp_path, [dict(PAPER, abstract=TEXT)], chunks=None))
    selected, coverage = rc.select_passages(chunks)
    assert coverage["basis"] == "abstract_only" and selected[0]["text"] == TEXT


def test_selection_balances_sections_and_bounds_prompt():
    chunks = [dict(CHUNKS[0], section=s, text=TEXT * 100) for s in
              ["abstract"] * 30 + ["methods", "results", "discussion", "references"]]
    selected, coverage = rc.select_passages(chunks)
    assert {"methods", "results", "discussion"} <= {s["section"] for s in selected}
    assert all(s["section"] != "references" for s in selected)
    assert len(selected) <= 12 and sum(len(s["text"]) for s in selected) <= 18000
    assert coverage["truncated_passages"] > 0
    assert coverage["available_passages"] == len(chunks)


def test_unknown_source_rejected_and_model_quotes_never_used():
    passages, _ = rc.select_passages(CHUNKS)
    content = draft()
    content["findings"] = [dict(content["overview"][0], source="S99"),
                           dict(content["overview"][0], quote="An invented quotation not in the source.")]
    claims, rejected = rc.literal_claims(content, passages)
    assert len(claims) == 2 and rejected == 1
    assert all(c["quote"] == TEXT for c in claims)


def test_card_checked_cached_and_invalidated(tmp_path, monkeypatch):
    calls = fake_model(monkeypatch)
    card = rc.create_card(PAPER, CHUNKS, model="test", language="de", cache_dir=tmp_path)
    assert len(calls) == 2 and card["claims"] and not card["cache_hit"]
    assert "nur der Abstract" in rc.card_markdown(card)
    assert "p. 1" in rc.card_markdown(card)
    assert "nicht ausreichend belegt" in rc.card_markdown(card)
    second = rc.create_card(PAPER, CHUNKS, model="test", language="de", cache_dir=tmp_path)
    assert len(calls) == 2 and second["cache_hit"]
    changed = [dict(CHUNKS[0], text=TEXT + " Additional text.")]
    rc.create_card(PAPER, changed, model="test", language="de", cache_dir=tmp_path)
    assert len(calls) == 4
    rc.create_card(PAPER, CHUNKS, model="another", language="de", cache_dir=tmp_path)
    assert len(calls) == 6


@pytest.mark.parametrize("supported", [[], [True], ["0"]])
def test_failed_support_check_never_caches(tmp_path, monkeypatch, supported):
    fake_model(monkeypatch, supported=supported)
    with pytest.raises(ValueError):
        rc.create_card(PAPER, CHUNKS, model="test", language="en", cache_dir=tmp_path)
    assert not list(tmp_path.glob("*.json"))


def test_model_failure_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "call_ollama_generate", lambda *a, **k: response(None, "timeout"))
    with pytest.raises(ValueError, match="model"):
        rc.create_card(PAPER, CHUNKS, model="test", language="en", cache_dir=tmp_path)
    assert not list(tmp_path.glob("*.json"))


def test_unsupported_finding_is_removed_not_rewritten_as_a_fact(tmp_path, monkeypatch):
    output = draft()
    output["findings"] = [{"text": "The first method is always better on every possible dataset.", "source": "S1"}]
    fake_model(monkeypatch, output=output, supported=[0])
    card = rc.create_card(PAPER, CHUNKS, model="test", language="en", cache_dir=tmp_path)
    assert card["omitted_claims"] == 1
    assert "always better" not in rc.card_markdown(card)
    assert "were omitted" in rc.card_markdown(card)


def test_structured_abstract_is_not_described_as_full_text():
    _, coverage = rc.select_passages([dict(CHUNKS[0], section="Structured Abstract")])
    assert coverage["basis"] == "abstract_only"


def test_short_paper_text_is_not_unnecessarily_truncated():
    chunks = [dict(CHUNKS[0], text=TEXT * 50), dict(CHUNKS[0], section="results", text=TEXT)]
    selected, coverage = rc.select_passages(chunks)
    assert coverage["truncated_passages"] == 0
    assert selected[0]["text"] == rc.compact(chunks[0]["text"])


def test_summary_uses_single_paper_not_rag_and_survives_saved_chat(tmp_path, monkeypatch):
    paths = corpus(tmp_path)
    fake_model(monkeypatch)
    monkeypatch.setattr(ask_corpus, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(web_chat, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ask_corpus, "understand_question", lambda *a, **k: pytest.fail("No corpus-wide query preparation"))
    monkeypatch.setattr(ask_corpus, "validate_corpus_inputs", lambda *a, **k: pytest.fail("No full-corpus retrieval validation"))
    monkeypatch.setattr(web_chat, "build_public_assurance", lambda *a, **k: pytest.fail("No sufficiency certificate for a summary"))
    monkeypatch.setattr(web_chat, "format_public_sources", lambda *a, **k: [dict(PAPER, url="https://doi.org/" + PAPER["doi"])])
    args = SimpleNamespace(question="Summarize this paper: P1", backend="qwen", answer_model="test",
                           papers=str(paths[0]), chunks_dir=str(paths[1]))
    session = tmp_path / "chats/test"
    out = session / "turn_001_summary"
    result = ask_corpus.run_question(args, out)
    assert result["pack"]["task"] == "paper_summary"
    assert all(c["paper_id"] == "P1" for c in result["pack"]["chunks"])
    public = web_chat.serialize_turn_result(result, out, PAPER["title"], "paper_summary")
    assert public["task"] == "paper_summary" and "assurance" not in public
    web_chat.append_turn_summary(session, args.question, PAPER["title"], "paper_summary", result, out)
    web_chat.write_json(out / "public_result.json", public)
    turns = web_chat.read_session_turns(session)
    context = web_chat.turn_context_from_saved_turn(turns[0])
    assert PAPER["title"] in context.source_titles and context.answer_summary
    messages = web_chat.session_messages(session)
    assert messages[-1]["result"]["task"] == "paper_summary"
    ask_corpus.print_summary(args.question, args.backend, result, out)


def test_markdown_escapes_untrusted_content(tmp_path, monkeypatch):
    output = draft()
    output["overview"][0]["text"] = "[Click here](https://bad.invalid) <script>bad</script>"
    fake_model(monkeypatch, output=output)
    card = rc.create_card(PAPER, CHUNKS, model="test", language="en", cache_dir=tmp_path)
    markdown = rc.card_markdown(card)
    assert "\\[Click here\\]" in markdown and "<script>" not in markdown
