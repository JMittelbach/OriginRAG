import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import web_chat


def function_body(name, next_name):
    return web_chat.INDEX_HTML.split(f"function {name}(", 1)[1].split(f"function {next_name}(", 1)[0]


def test_answers_use_progressive_disclosure_without_removing_source_information():
    sources = function_body("buildSourcesPanel", "buildPaperSearchTable")
    assert "details.open = false" in sources
    assert 'metadata.className = "reference-details"' in sources
    assert "${statusHtml}${ids}${typeHtml}" in sources
    assert "Trace cited sources" in sources
    assert "(none cited)" in sources
    assurance = function_body("buildAssurancePanel", "buildStudyAuditAction")
    assert "panel.open = false" in assurance
    assert "assuranceStatus(assurance)" in assurance
    assert "assuranceNote(assurance)" in assurance
    assert "buildStudyAuditAction(assurance)" in assurance


def test_critical_warnings_remain_outside_collapsed_details():
    warnings = function_body("buildAnswerWarnings", "resultMeta")
    for text in ("Answer withheld", "Provisional answer", "Citation check failed", "Citations have not been checked"):
        assert text in warnings
    result = function_body("resultMeta", "buildClaimSourceChains")
    assert "box.appendChild(warnings)" in result
    assert "box.appendChild(buildAnswerDetails(summary))" in result
    assert "buildPaperSearchTable(" in result


def test_answer_styles_keep_native_disclosure_focus_visible():
    html = web_chat.INDEX_HTML
    assert '.result-meta summary:focus-visible' in html
    assert '.assurance-status { white-space: normal; }' in html
    assert 'legend.innerHTML = "<summary>About these references</summary>' in html
