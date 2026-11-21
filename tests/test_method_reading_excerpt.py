from originrag.retrieval.method_description import describes_named_method, source_reading_excerpt


def test_complete_source_quote_and_adjacent_named_claim_only():
    text = ("We present a new pipeline for device calibration. "
            "ToolMap can also invoke an existing adjustment program. Unrelated observation.")
    row = {"paper_id": "P1", "title": "ToolMap: device calibration", "text": text, "page_range": "3"}
    excerpt = source_reading_excerpt({"chunks": [row]}, "ToolMap")
    assert text.removesuffix(" Unrelated observation.") in excerpt
    assert "Unrelated observation" not in excerpt and "(p. 3)" in excerpt
    for override in ({"title": "OtherTool"}, {"title": "ToolMapLite: clinical study"},
                     {"section": "references"}, {"quality_flags": ["table_like"]},
                     {"text": "We present a new pipeline for device [...]"}, {"paper_id": ""}):
        assert not source_reading_excerpt({"chunks": [{**row, **override}]}, "ToolMap")


def test_mention_in_comparison_title_does_not_identify_which_tool_authors_developed():
    title = "Comparison of ToolMap and CalibSim"
    assert not describes_named_method("We developed CalibSim, a calibration tool.", title, "ToolMap")
    assert describes_named_method("We developed ToolMap for measuring drift.", title, "ToolMap")
