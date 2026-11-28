import json

import pytest

from originrag.analysis.reading_support import interpretation_passages
from originrag.analysis.context_budget import estimate_prompt_tokens
from originrag.paper_discussion import bibliography, discussion_prompt, reference_request, refers_to_paper


def test_scientific_sentences_keep_citations_and_parenthetical_conditions():
    from originrag.analysis.reading_support import source_sentences
    text = ('Smith et al. Reported a failed knockout (Fig. S2). '
            'A conditional mutant grew (Fig. 3 and Supplementary text), but only with the inducer. '
            'The authors did not infer essentiality.')
    assert list(source_sentences(text)) == [
        'Smith et al. Reported a failed knockout (Fig. S2).',
        'A conditional mutant grew (Fig. 3 and Supplementary text), but only with the inducer.',
        'The authors did not infer essentiality.',
    ]


@pytest.mark.parametrize("q", [
    "Show me the source for that statement.",
    "Which references support this claim?",
    "Zeig mir bitte die Belege für diese Aussage.",
])
def test_claim_support_is_not_the_bibliography(q):
    assert refers_to_paper(q)
    assert not reference_request(q)


@pytest.mark.parametrize("q", ["Show all references.", "List all sources.", "Zeig mir das Literaturverzeichnis."])
def test_explicit_bibliography_still_works(q):
    assert reference_request(q)


def sample_passages():
    return [
        {"page": 2, "text": "We did not directly measure temperature; we inferred it from resistance."},
        {"page": 3, "text": "One explanation for the temperature drift is a change in resistance."},
        {"page": 4, "text": "No significant rainfall difference was detected between the two seasons."},
    ]


def test_qualifiers_are_verbatim_relevant_and_page_backed():
    original = sample_passages()
    chosen = interpretation_passages(original, "Was temperature directly measured or inferred?")
    assert {x["page"] for x in chosen} == {2, 3}
    for x in chosen:
        assert x["text"] in next(p["text"] for p in original if p["page"] == x["page"])
    assert interpretation_passages(original, "How many authors contributed?") == []
    assert interpretation_passages(original, "Was wind speed directly measured?") == []


@pytest.mark.parametrize("window", [2048, 4096])
def test_qualifiers_do_not_blow_context_budget_or_come_from_old_answers(window):
    info = {}
    history = [("Was temperature directly measured?", "Hallucinated claim: definitely measured!")]
    prompt, _ = discussion_prompt("Does that prove there was drift?", "Sensors", [], history,
                                  window, 400, pages=sample_passages(), context_info=info)
    payload = json.loads(prompt.split("\n", 1)[1])
    anchors = payload["measurement_and_inference_passages"]
    assert "Hallucinated" not in json.dumps(anchors)
    assert anchors == info["interpretation_passages"]
    assert estimate_prompt_tokens(prompt) + 400 + 128 <= window
    assert info["prompt_tokens_estimated"] == estimate_prompt_tokens(prompt)


def test_no_fabricated_page_numbers():
    assert not interpretation_passages([{"text": "We did not directly measure temperature in the sample."}],
                                       "Did they directly measure temperature?")


def test_causal_question_does_not_highlight_unrelated_nonsignificant_findings():
    unrelated = {"page": 2, "text": "No significant association was found between a different measure and the outcome."}
    relevant = {"page": 3, "text": "The observational design does not establish a causal effect of tree cover on temperature."}
    assert interpretation_passages([unrelated, relevant],
        "Does the observational association prove causality?") == [relevant]


def test_experimental_systems_keep_failed_and_conditional_results_separate():
    from originrag.analysis.reading_support import experimental_context_passages
    passages = [
        {'page': 2, 'text': 'In the loss-of-function clone, activating regulator X failed to restore differentiation.'},
        {'page': 3, 'text': 'Deleting the repressor in a conditional mutant increased differentiation in the permissive condition.'},
    ]
    assert experimental_context_passages(passages, 'What did they modify, and what happened?') == passages
    assert not experimental_context_passages(passages, 'Who funded the study?')
    prompt, _ = discussion_prompt('Summarize in three sentences.', 'A study', [], [], 4096, 400, pages=passages)
    payload = json.loads(prompt.split('\n', 1)[1])
    assert payload['experimental_context_passages'] == passages
    assert 'Do not merge them' in payload['experimental_context_instruction']
    assert estimate_prompt_tokens(prompt) + 400 + 128 <= 4096


@pytest.mark.parametrize("content", ["{broken", "[]", '{"paper_id":"OTHER","pages":[]}',
                                      '{"paper_id":"A","pages":[null,{"text":42}]}'])
def test_broken_extraction_uses_registered_pdf_fallback(tmp_path, content):
    path = tmp_path / "A.json"
    path.write_text(content)
    answer = bibliography(path, "A", lambda: [{"page": 3, "text": "References\n1. Sample paper. 2020."}])
    assert "Sample paper" in answer
