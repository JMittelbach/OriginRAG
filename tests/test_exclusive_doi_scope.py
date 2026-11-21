from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ask_corpus  # noqa: E402


DOI = "10.1234/journal.database.001"
QUESTION = f"What query latency was measured in the paper with DOI {DOI}? Describe this single report only."


@pytest.mark.parametrize("question", [
    QUESTION,
    f"Describe only this paper: https://doi.org/{DOI}.",
    f"Use exclusively the single study with DOI {DOI}.",
    f"Beschreibe nur diesen einzelnen Bericht: {DOI}.",
    f"Bitte ausschließlich dieses Paper: {DOI}.",
    f"Describe this single report only: {DOI} ({DOI}).",
    f"In the paper with DOI {DOI}, where were participants recruited?",
    f"According to https://doi.org/{DOI}, how long did follow-up last?",
    f"In der Studie mit DOI {DOI}: Wo wurden die Teilnehmenden rekrutiert?",
    f"Beschreibe ausschließlich dieses Paper mit DOI {DOI.upper()}; {DOI}.",
])
def test_exclusive_single_report_scope_requires_explicit_user_intent(question):
    assert ask_corpus.exclusive_single_report_doi(question) == DOI


@pytest.mark.parametrize("question", [
    f"What do studies report about {DOI}?",
    f"Describe this paper: {DOI}.",
    f"Describe this single report only: {DOI} and 10.1234/journal.database.002.",
    f"Compare this paper with other studies: {DOI}.",
    f"Compare the findings. Describe this single report only: {DOI}.",
    f"Describe not only this paper: {DOI}.",
    f"Do not restrict the answer to only this paper: {DOI}.",
    f"Beschreibe nicht nur dieses Paper: {DOI}.",
    f"Use only this paper and related studies: {DOI}.",
    f"Does this paper only include software benchmarks? DOI {DOI}.",
    f"In DOI {DOI}, which other papers have replicated the findings?",
    f"According to DOI {DOI}, how does this compare to later reports?",
    f"Describe only this paper's introduction and other literature: {DOI}.",
    "Describe this single report only.",
])
def test_ambiguous_or_broader_requests_do_not_gain_exclusive_scope(question):
    assert ask_corpus.exclusive_single_report_doi(question) is None


def test_scoped_design_question_is_not_a_request_for_a_link():
    from originrag.analysis.chat import resolve_chat_task
    q = f"Where were participants recruited and how long were they followed? Describe this single report only: DOI {DOI}."
    assert resolve_chat_task(q) == "answer"
    assert resolve_chat_task(f"Find the paper with DOI {DOI}") == "sources"
    assert resolve_chat_task(q, configured_task="sources") == "sources"


def test_single_report_qc_keeps_citation_checks_without_mandatory_speculation():
    from originrag.analysis.answer_qc import assess_answer_quality
    from originrag.analysis.prompts import build_answer_repair_prompt, build_citation_answer_prompt
    pack = {"research_mode": True, "research_paper_id": "P1",
            "chunks": [{"paper_id": "P1", "evidence_id": "E001", "text": "The authors surveyed eighteen patients."}]}
    text = "## Evidence-backed statements\nThe authors surveyed eighteen patients (E001).\n\n## References\nE001"
    assert assess_answer_quality(text, pack, "answer")["passed"]
    assert not assess_answer_quality(text.replace("E001", "E999"), pack, "answer")["passed"]
    assert not assess_answer_quality(text, {**pack, "research_mode": False}, "answer")["passed"]
    assert "single heading" in build_citation_answer_prompt("How many?", pack)
    assert "single heading" in build_answer_repair_prompt("How many?", pack, "answer", "Bad draft.", {"warnings":[]})
    assert "Publication status:" not in build_citation_answer_prompt("How many?", pack)
    pack["chunks"][0]["publication_status"] = "preprint"
    assert "Publication status: preprint" in build_citation_answer_prompt("How many?", pack)


@pytest.fixture
def corpus(tmp_path):
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    metadata = tmp_path / "papers.tsv"
    metadata.write_text(
        "paper_id\ttitle\tdoi\tfulltext_status\n"
        f"TARGET\tDatabase query latency\t{DOI}\tchunked\n"
        "OTHER\tDatabase query latency in another system\t10.1234/journal.database.002\tchunked\n",
        encoding="utf-8",
    )
    rows = []
    for paper_id, doi in (("TARGET", DOI), ("OTHER", "10.1234/journal.database.002")):
        passages = []
        for index, section in enumerate(("abstract", "methods", "results")):
            row = {
                "paper_id": paper_id, "chunk_id": f"{paper_id}_{index}",
                "chunk_index": index, "doi": doi, "section": section, "score": 20 - index,
                "text": (
                    f"In this study we measured database query latency in the {section}. "
                    "Repeated timed benchmark runs used fixed input tables and identical hardware. "
                    "We observed lower median latency when an index was available. "
                    "The measurements describe this software configuration and do not establish broad superiority. "
                ) * 4,
            }
            passages.append(row)
            rows.append(row)
        (chunks / f"{paper_id}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in passages), encoding="utf-8",
        )
    return chunks, metadata, rows


def test_exclusive_doi_anchor_retains_distinct_same_report_passages(corpus):
    chunks, metadata, rows = corpus
    anchored = ask_corpus.merge_explicit_doi_anchors(
        QUESTION, rows, chunks_dir=chunks, papers_tsv=metadata, preserve_matching_passages=True,
    )
    selected, scope = ask_corpus.retain_single_report_doi_candidates(DOI, anchored, papers_tsv=metadata)

    assert [row["chunk_id"] for row in selected] == ["TARGET_0", "TARGET_1", "TARGET_2"]
    assert selected[0]["explicit_identifier_anchor"] is True
    assert len({row["chunk_id"] for row in selected}) == 3
    assert scope["paper_ids"] == ["TARGET"]
    assert scope["scope_only_not_evidence_sufficiency"] is True
    assert scope["candidate_count_after_scope"] == 3


def test_doi_scope_trusts_catalogue_identity_not_candidate_doi(corpus):
    _chunks, metadata, rows = corpus
    forged = {**rows[-1], "doi": DOI}
    selected, _scope = ask_corpus.retain_single_report_doi_candidates(
        DOI, [rows[0], forged], papers_tsv=metadata,
    )
    assert [row["paper_id"] for row in selected] == ["TARGET"]


def test_multiple_catalogue_records_with_same_doi_remain_available(corpus):
    _chunks, metadata, rows = corpus
    with metadata.open("a", encoding="utf-8") as handle:
        handle.write(f"VERSION\tDatabase query latency version\thttps://doi.org/{DOI}\tchunked\n")
    selected, scope = ask_corpus.retain_single_report_doi_candidates(
        DOI, [rows[0], {**rows[0], "paper_id": "VERSION", "chunk_id": "VERSION_0"}], papers_tsv=metadata,
    )
    assert {row["paper_id"] for row in selected} == {"TARGET", "VERSION"}
    assert scope["paper_ids"] == ["TARGET", "VERSION"]


@pytest.mark.parametrize("doi, expected", [("10.1234/journal.missing", "not present"), (DOI, "no available")])
def test_missing_report_never_substitutes_same_journal_hits(corpus, doi, expected):
    _chunks, metadata, rows = corpus
    with pytest.raises(ValueError, match=expected):
        ask_corpus.retain_single_report_doi_candidates(
            doi, [row for row in rows if row["paper_id"] == "OTHER"], papers_tsv=metadata,
        )


@pytest.mark.parametrize("fixed_pool", [False, True])
@pytest.mark.parametrize("selector", ["current", "trace_flow", "research"])
def test_exclusive_scope_precedes_answer_evidence_selection(
    corpus, tmp_path, monkeypatch, fixed_pool, selector,
):
    chunks, metadata, rows = corpus
    monkeypatch.setenv("PFEMPIRE_OLLAMA_NUM_CTX", "16384")
    monkeypatch.setattr(sys, "argv", [
        "ask_corpus.py", QUESTION, "--chunks-dir", str(chunks), "--papers", str(metadata),
        "--backend", "none", "--no-understand-question", "--evidence-selector", selector,
        "--preset", "quick", "--limit", "10", "--max-per-paper", "3",
        "--repair-attempts", "0", "--no-template-fallback",
    ])
    args = ask_corpus.parse_args()
    if fixed_pool:
        pool = tmp_path / "candidates.jsonl"
        pool.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        args.candidate_pool = str(pool)
    if selector == "research":
        monkeypatch.setattr(ask_corpus, "run_trace_selection", lambda *a, **k: pytest.fail("Unexpected origin audit"))
    result = ask_corpus.run_question(args, tmp_path / "run")

    assert {row["paper_id"] for row in result["results"]} == {"TARGET"}
    assert {row["paper_id"] for row in result["pack"]["chunks"]} <= {"TARGET"}
    assert result["pack"]["requested_report_scope"]["doi"] == DOI
    if selector == "research":
        assert result["research_fallback"] == "passages"
        assert "database query latency" in result["answer_path"].read_text().lower()
        assert "trace_sufficiency_certificate" not in result["pack"]
    saved = json.loads((tmp_path / "run" / "run_config.json").read_text())
    assert saved["requested_report_scope"]["scope_only_not_evidence_sufficiency"] is True
    assert saved["answer_call_performed"] is False


def test_research_reads_named_report_without_using_other_reports_or_a_rejected_draft(corpus, tmp_path, monkeypatch):
    chunks, metadata, _ = corpus
    metadata.with_name("paper_registry.tsv").write_text("paper_id\tauthor\nTARGET\tSmith\nOTHER\tJones\n")
    monkeypatch.setattr(sys, "argv", [
        "ask_corpus.py", "Did the study by Smith et al. measure database query latency?",
        "--chunks-dir", str(chunks), "--papers", str(metadata), "--backend", "none",
    ])
    monkeypatch.setattr(ask_corpus, "understand_question", lambda *a, **k: pytest.fail("Scoped lookup ran the planner"))
    monkeypatch.setattr(ask_corpus, "run_trace_selection", lambda *a, **k: pytest.fail("Reading ran the audit"))
    def invalid_draft(**kwargs):
        assert kwargs["evidence_is_sufficient"] and not kwargs["force_answer"]
        return ask_corpus.LLMResult(backend="qwen", answer="Fabricated result."), {"passed": False}, [], {}
    monkeypatch.setattr(ask_corpus, "generate_with_qc_repair", invalid_draft)
    result = ask_corpus.run_question(ask_corpus.parse_args(), tmp_path / "reading")
    assert result["pack"]["research_paper_id"] == "TARGET"
    assert {p["paper_id"] for p in result["pack"]["chunks"]} == {"TARGET"}
    assert result["research_fallback"] == "passages"
    assert "Fabricated result" not in result["answer_path"].read_text()
    assert result["rejected_answer_path"].read_text().strip() == "Fabricated result."


def test_original_exclusive_scope_survives_preparation_that_drops_only_marker(corpus, tmp_path, monkeypatch):
    chunks, metadata, _rows = corpus
    monkeypatch.setattr(sys, "argv", [
        "ask_corpus.py", QUESTION, "--chunks-dir", str(chunks), "--papers", str(metadata),
        "--backend", "none", "--evidence-selector", "current", "--preset", "quick",
        "--understand-question", "--repair-attempts", "0", "--no-template-fallback",
    ])
    args = ask_corpus.parse_args()
    args.configured_answer_backend = "qwen"
    monkeypatch.setattr(ask_corpus, "understand_question", lambda *args, **kwargs: {
        "status": "ready", "intent": "corpus_inventory", "model": "test-model",
        "original_question": QUESTION,
        "normalized_question": f"Which database methods occur in the corpus? DOI {DOI}.",
        "search_queries": [f"database query latency {DOI}"],
    })
    result = ask_corpus.run_question(args, tmp_path / "run")

    assert result["resolved_task"] == "answer"
    assert {row["paper_id"] for row in result["results"]} == {"TARGET"}
    assert result["pack"]["requested_report_scope"]["doi"] == DOI
    assert "corpus_inventory" not in result["pack"]


@pytest.mark.parametrize("ready", [True, False])
def test_translation_inside_a_report_never_changes_report_identity(corpus, tmp_path, monkeypatch, ready):
    chunks, metadata, _ = corpus
    question = f"In der Studie mit DOI {DOI}: Wie wurde die Datenbanklatenz gemessen?"
    monkeypatch.setattr(sys, "argv", ["ask_corpus.py", question, "--chunks-dir", str(chunks),
                        "--papers", str(metadata), "--backend", "none", "--understand-question"])
    args = ask_corpus.parse_args()
    args.configured_answer_backend = "qwen"
    def translate(text, **unused):
        assert DOI not in text
        return ({"status": "ready", "normalized_question": "How was database query latency measured?"}
                if ready else {"status": "unavailable"})
    monkeypatch.setattr(ask_corpus, "understand_question", translate)
    if not ready:
        with pytest.raises(ValueError, match="No other reports were substituted"):
            ask_corpus.run_question(args, tmp_path / "run")
        return
    pack = ask_corpus.run_question(args, tmp_path / "run")["pack"]
    assert pack["requested_report_scope"]["doi"] == DOI
    assert pack["question"] == question
    assert pack["question_understanding"]["status"] == "ready"
    assert {c["paper_id"] for c in pack["chunks"]} == {"TARGET"}
