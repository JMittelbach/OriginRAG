from originrag.retrieval.research_passages import reading_passages, reading_result, report_author


def chunk(i, text, **extra):
    return {"paper_id": "REPORT", "evidence_id": f"E{i:03}", "title": "Field trial", "text": text, **extra}


def test_quote_is_from_body_has_source_and_does_not_repeat():
    quote = "The calibration used two independent optical sensors."
    rows = [chunk(1, "We evaluated multiple fixtures.\nReferences\n1. Calibration using optical sensors."),
            chunk(2, quote, page_range="4"), chunk(3, quote)]
    hits = reading_passages("How did the optical sensor calibration work?", rows)
    assert hits == [(rows[1], quote)]
    assert "Field trial, p. 4 (E002)" in reading_result(hits)


def test_no_hit_does_not_answer_from_title_or_rejected_draft():
    rows = [chunk(1, "The technicians checked the paper records before running an analysis.", title="Optical sensors"),
            chunk(2, "Optical sensor calibration with two probes.", section="references")]
    assert not reading_passages("Optical sensor calibration?", rows)
    assert "in this search" in reading_result([])


def test_singular_author_scope_is_not_a_comparison():
    assert report_author("Did the imaging paper by Kim-Liu et al. study adults or children?") == ("Kim-Liu", "")
    assert report_author("Welche Methode hat die Studie von Müller et al. 2020 verwendet?") == ("Müller", "2020")
    assert report_author("Did other papers replicate the study by Kim et al.?") is None
    assert report_author("How does the study by Kim et al. compare to Ray et al.?") is None
