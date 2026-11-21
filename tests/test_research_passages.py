from originrag.retrieval.research_passages import reading_passages, reading_result, report_author
from originrag.retrieval.research_passages import interleave_passages, report_queries
from originrag.retrieval.research_passages import report_needs_translation


def test_scoped_query_does_not_search_for_the_identifier_or_mix_question_parts():
    doi = "10.1234/example.b"
    assert report_queries("Where were sensors placed and how were they calibrated? "
                          f"Describe this single report only: https://doi.org/{doi}.", doi) == [
        "Where were sensors placed", "how were they calibrated"]
    assert report_queries(f"Beschreibe nur diesen einzelnen Bericht: DOI {doi}.", doi) == []
    assert report_queries("Were both length and width measured?") == ["Were both length and width measured"]


def test_report_questions_in_another_language_keep_subquestions_and_do_not_translate_english():
    clauses = report_queries("Was ist der Unterschied? Worauf muss ich achten?")
    assert clauses == ["Was ist der Unterschied", "Worauf muss ich achten"]
    assert report_needs_translation(clauses)
    assert report_needs_translation(["Quelles méthodes sont utilisées"])
    assert not report_needs_translation(["How was the die cast", "When was it measured"])


def test_clause_rankings_share_budget_and_never_duplicate_evidence():
    a,b,c,d = ({"chunk_id": key} for key in "abcd")
    assert interleave_passages([[a,b,c], [d,a]], 3) == [a,d,b]
    assert interleave_passages([[], [c,a]], 5) == [c,a]


def test_shared_chunk_keeps_one_contiguous_window_covering_both_questions(monkeypatch):
    from originrag.analysis import evidence_pack as ep

    left, right = "Which sensors in London?", "Which controls in Oxford?"
    one = "The sensors in London were calibrated."
    both = "The sensors in London were calibrated against controls in Oxford."
    monkeypatch.setattr(ep, "evidence_window", lambda question, text, **kw: one if question == left else both)
    a = {"chunk_id": "A", "text": one + " " + both, "report_search_clause": left}
    rows = interleave_passages([[a], [{**a, "report_search_clause": right}]], 1)
    assert len(rows) == 1
    assert ep.evidence_text_for_pack("original", rows[0], 1000) == both


def chunk(i, text, **extra):
    return {"paper_id": "REPORT", "evidence_id": f"E{i:03}", "title": "Field trial", "text": text, **extra}


def test_quote_is_from_body_has_source_and_does_not_repeat():
    quote = "The calibration used two independent optical sensors."
    rows = [chunk(1, "We evaluated multiple fixtures.\nReferences\n1. Calibration using optical sensors."),
            chunk(2, quote, page_range="4"), chunk(3, quote)]
    hits = reading_passages("How did the optical sensor calibration work?", rows)
    assert hits == [(rows[1], quote)]
    assert "Field trial, p. 4 (E002)" in reading_result(hits)


def test_unrepaired_count_prioritises_verbatim_source_sentence():
    source = "At this site we enrolled 12 age-matched participants."
    row = chunk(1, "Which groups were eligible was determined during screening. " + source)
    refs = [{"claim": "21 age-matched participants", "source": "12 age-matched participants",
             "evidence_ids": ["E001"]}]
    assert reading_passages("Which groups were eligible?", [row], count_conflicts=refs) == [(row, source)]


def test_no_hit_does_not_answer_from_title_or_rejected_draft():
    rows = [chunk(1, "The technicians checked the paper records before running an analysis.", title="Optical sensors"),
            chunk(2, "Optical sensor calibration with two probes.", section="references")]
    assert not reading_passages("Optical sensor calibration?", rows)
    assert "in this search" in reading_result([])


def test_front_page_affiliations_are_not_presented_as_findings():
    quote = "Paired measurements reduced the calibration error in both instruments."
    r = chunk(1, "RESEARCH ARTICLE Calibration error in paired instruments Department of Calibration Abstract " + quote,
              page_range="1-2")
    assert reading_passages("What was the calibration error?", [r]) == [(r, quote)]


def test_singular_author_scope_is_not_a_comparison():
    assert report_author("Did the imaging paper by Kim-Liu et al. study adults or children?") == ("Kim-Liu", "")
    assert report_author("Welche Methode hat die Studie von Müller et al. 2020 verwendet?") == ("Müller", "2020")
    assert report_author("Did other papers replicate the study by Kim et al.?") is None
    assert report_author("How does the study by Kim et al. compare to Ray et al.?") is None
