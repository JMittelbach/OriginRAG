from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from originrag.analysis.ollama_client import OllamaResult


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ask_corpus  # noqa: E402


PAPERS = [{"paper_id": "P1", "title": "Human cell annotation", "year": "2025"}]
FILLER = "Unrelated measurement context. " * 100


def reason_excerpts(question: str, texts: list[str]) -> list[str]:
    candidates = ask_corpus.paper_reason_candidates(
        PAPERS,
        [{"paper_id": "P1", "text": text} for text in texts],
        question=question,
    )
    return candidates[0]["retrieved_excerpts"]


def assert_literal_excerpts(excerpts: list[str], texts: list[str], budget: int = 800) -> None:
    assert sum(map(len, excerpts)) <= budget
    sources = [" ".join(text.split()) for text in texts]
    for excerpt in excerpts:
        body = excerpt.removeprefix("[...] ").removesuffix(" [...]")
        assert body
        assert any(body in source for source in sources)


@pytest.mark.parametrize("offset", [800, 825, 833, 2112, 2327])
@pytest.mark.parametrize("target", ["ACTB", "actb"])
def test_late_literal_target_survives_the_original_explanation_budget(offset, target):
    text = FILLER[:offset] + f" ACTB was used only as an annotation marker. {FILLER}"

    excerpts = reason_excerpts(f"Find papers that mention {target}.", [text])

    assert "ACTB was used only as an annotation marker." in " ".join(excerpts)
    assert_literal_excerpts(excerpts, [text])


def test_german_lowercase_lookup_retains_literal_target():
    text = FILLER + " ACTB was used only as an annotation marker."

    excerpts = reason_excerpts(
        "Finde Paper im Korpus, die das Gen actb ausdrücklich erwähnen.", [text],
    )

    assert "ACTB was used only as an annotation marker." in " ".join(excerpts)
    assert_literal_excerpts(excerpts, [text])


def test_uppercase_context_does_not_hide_a_lowercase_question_anchor():
    texts = ["RNA measurements were discussed. " + FILLER, FILLER + " actb was listed as a marker."]

    excerpts = reason_excerpts("Find papers mentioning actb in RNA measurements.", texts)

    assert "actb was listed as a marker." in " ".join(excerpts)
    assert_literal_excerpts(excerpts, texts)


def test_a_later_retrieved_chunk_supplies_the_target_not_the_first_chunk_prefix():
    texts = [FILLER, FILLER + " CD3D was listed among human cell markers."]

    excerpts = reason_excerpts("Find papers that mention CD3D.", texts)

    assert "CD3D was listed among human cell markers." in " ".join(excerpts)
    assert_literal_excerpts(excerpts, texts)


@pytest.mark.parametrize("separate_chunks", [False, True])
def test_distinct_anchors_share_the_budget_even_when_one_anchor_is_frequent(separate_chunks):
    texts = [
        "ACTB was listed as a marker. " * 300,
        FILLER + " CD3D was listed as a marker. " + FILLER,
        FILLER + " HLA−DRA was listed as a marker. " + FILLER,
    ]
    if not separate_chunks:
        texts = [" ".join(texts)]

    excerpts = reason_excerpts("Find papers mentioning ACTB, CD3D and HLA-DRA.", texts)

    assert all(target in " ".join(excerpts) for target in ("ACTB", "CD3D", "HLA−DRA"))
    assert_literal_excerpts(excerpts, texts)


def test_nearby_anchor_windows_merge_without_duplicating_or_inventing_evidence():
    text = FILLER + " ACTB and CD3D were listed, not tested as causal factors. " + FILLER

    excerpts = reason_excerpts("Find papers mentioning ACTB and CD3D.", [text])

    assert len(excerpts) == 1
    assert "ACTB and CD3D were listed, not tested as causal factors." in excerpts[0]
    assert_literal_excerpts(excerpts, [text])


def test_no_literal_match_preserves_the_existing_prefix_without_inserting_the_query():
    texts = [" \n First measurement description. " + FILLER, "A later unrelated description."]

    excerpts = reason_excerpts("Find papers mentioning ACTB.", texts)

    assert excerpts == [" ".join(texts[0].split())[:800]]
    assert "ACTB" not in " ".join(excerpts)


def test_empty_evidence_stays_empty_even_when_the_question_contains_an_anchor():
    assert reason_excerpts("Find papers mentioning ACTB.", ["", " \n "]) == []


@pytest.mark.parametrize(
    "decoy",
    ["ACTB2", "XACTB", "ACTB_AS1", "ACTB-AS1", "ACTB−AS1", "éACTB", "ACTBé"],
)
def test_identifier_boundaries_skip_partial_matches_and_find_later_exact_mentions(decoy):
    texts = [f"{decoy} is a distinct identifier. " + FILLER, "The exact ACTB marker was listed."]

    excerpts = reason_excerpts("Find papers mentioning ACTB.", texts)

    assert excerpts == ["The exact ACTB marker was listed."]


@pytest.mark.parametrize("dash", ["-", "‐", "‑", "‒", "–", "—", "―", "−"])
def test_hyphenated_identifiers_accept_typographic_dash_variants_without_changing_text(dash):
    text = FILLER + f" hla{dash}dra was listed as a human marker."

    excerpts = reason_excerpts("Find papers mentioning HLA-DRA.", [text])

    assert f"hla{dash}dra was listed as a human marker." in " ".join(excerpts)
    assert_literal_excerpts(excerpts, [text])


def test_hyphenated_identifier_does_not_match_a_component_or_longer_identifier():
    texts = ["HLA and DRA are separate strings; HLA-DRA1 is different. " + FILLER]

    assert reason_excerpts("Find papers mentioning HLA-DRA.", texts) == [texts[0][:800]]


def test_empty_question_retains_legacy_first_retrieved_excerpt():
    texts = [FILLER, "ACTB was listed as a marker."]

    assert reason_excerpts("", texts) == [texts[0][:800]]


def test_empty_question_retains_legacy_front_matter_document_fallback():
    documents = [{"section": "title", "text": FILLER}, {"section": "abstract", "text": FILLER}]
    candidates = ask_corpus.paper_reason_candidates(PAPERS, [], document_context={"P1": documents})

    assert candidates[0]["retrieved_excerpts"] == ask_corpus._paper_reason_document_excerpts(documents)


def test_document_fallback_finds_a_late_anchor_within_the_same_small_budget():
    text = FILLER + " ACTB was listed as a marker. " + FILLER
    candidates = ask_corpus.paper_reason_candidates(
        PAPERS,
        [],
        question="Find papers mentioning ACTB.",
        document_context={"P1": [{"section": "abstract", "text": FILLER}, {"text": text}]},
    )

    excerpts = candidates[0]["retrieved_excerpts"]
    assert "ACTB was listed as a marker." in " ".join(excerpts)
    assert_literal_excerpts(excerpts, [text])


def test_paper_order_metadata_and_inputs_are_unchanged_and_evidence_does_not_cross_papers():
    papers = [*PAPERS, {"paper_id": "P2", "title": "Synthetic sensor measurements"}]
    chunks = [
        {"paper_id": "P2", "text": "SENSOR_A was used in the sensor measurements."},
        {"paper_id": "P1", "text": FILLER + " ACTB was listed as a marker."},
    ]
    original = deepcopy((papers, chunks))

    candidates = ask_corpus.paper_reason_candidates(papers, chunks, question="ACTB SENSOR_A")

    assert [row["paper_id"] for row in candidates] == ["P1", "P2"]
    assert candidates[0]["title"] == PAPERS[0]["title"]
    assert "SENSOR_A" not in " ".join(candidates[0]["retrieved_excerpts"])
    assert "ACTB" not in " ".join(candidates[1]["retrieved_excerpts"])
    assert (papers, chunks) == original


@pytest.mark.parametrize("budget", [0, 1, 15, 80, 100, 799, 800])
def test_budget_includes_all_omission_markers_and_never_returns_partial_anchors(budget):
    patterns = ask_corpus._paper_reason_anchor_patterns("ACTB CD3D HLA-DRA")
    texts = [FILLER + f" {target} marker. " + FILLER for target in ("ACTB", "CD3D", "HLA-DRA")]

    excerpts = ask_corpus._paper_reason_anchor_excerpts(
        [{"text": text} for text in texts], patterns, max_chars=budget,
    )

    assert_literal_excerpts(excerpts, texts, budget)
    assert all(any(target in excerpt for target in ("ACTB", "CD3D", "HLA-DRA")) for excerpt in excerpts)


def test_many_distinct_sensor_anchors_have_bounded_sampling_and_output():
    targets = [f"SENSOR_{index}" for index in range(40)]
    texts = [FILLER + f" {target} was measured. " + FILLER for target in targets]
    patterns = ask_corpus._paper_reason_anchor_patterns("Compare " + " and ".join(targets))

    excerpts = reason_excerpts("Compare " + " and ".join(targets), texts)

    assert len(patterns) == 8
    assert len(excerpts) <= 8
    assert all(target in " ".join(excerpts) for target in targets[:8])
    assert_literal_excerpts(excerpts, texts)


def test_a_complete_short_excerpt_does_not_reserve_unneeded_omission_markers():
    patterns = ask_corpus._paper_reason_anchor_patterns("ACTB")

    assert ask_corpus._paper_reason_anchor_excerpts([{"text": "ACTB"}], patterns, max_chars=4) == ["ACTB"]


def test_requested_fields_continue_to_use_the_existing_detail_extraction_path():
    retrieved = [{"paper_id": "P1", "section": "results", "text": "ACTB was measured. " + FILLER}]
    documents = [{"section": "methods", "text": "Human samples were measured by sequencing. " + FILLER}]
    requested_fields = ("assay", "cohort")
    expected = ask_corpus._paper_detail_excerpts(
        retrieved, documents, requested_fields=requested_fields, max_chars=3000,
    )

    candidates = ask_corpus.paper_reason_candidates(
        PAPERS,
        retrieved,
        question="Find ACTB papers and give the assay and cohort for each paper.",
        document_context={"P1": documents},
        requested_fields=requested_fields,
    )

    assert candidates[0]["retrieved_excerpts"] == expected
    assert sum(map(len, expected)) <= 3000


def test_explanation_call_passes_the_question_to_literal_excerpt_selection():
    prompts = []

    def generate(prompt, **_kwargs):
        prompts.append(prompt)
        return OllamaResult(
            text=None, model="test-model", base_url="http://localhost",
            call_performed=True, error="intentionally unavailable",
        )

    text = FILLER + " CD3D was listed as a human cell marker."
    ask_corpus.explain_paper_search_matches(
        "Find papers mentioning CD3D.",
        PAPERS,
        [{"paper_id": "P1", "text": text}],
        model="test-model",
        num_ctx=16384,
        generator=generate,
    )

    supplied = prompts[0].split("Candidate records and retrieved excerpts:\n", 1)[1]
    candidates, _end = json.JSONDecoder().raw_decode(supplied)
    assert "CD3D was listed as a human cell marker." in " ".join(candidates[0]["retrieved_excerpts"])
    assert_literal_excerpts(candidates[0]["retrieved_excerpts"], [text])


def test_reason_prompt_allows_explicit_mentions_without_promoting_usage_to_function():
    prompt = ask_corpus.paper_reason_prompt(
        "Find papers mentioning SENSOR_A.",
        [{"paper_id": "P1", "retrieved_excerpts": [
            "SENSOR_A was one input to a composite segmentation channel. "
            "The role of each component was not separately measured.",
        ]}],
    )

    assert "For an explicit identifier-mention search, you may instead report the literal mention" in prompt
    assert "For other requests, do not explain relevance merely as a keyword" in prompt
    assert "Distinguish a mention or list entry, a measurement, and use as an input or channel component" in prompt
    assert "demonstrated biological function, marker specificity, or study focus" in prompt
    assert "does not establish a component's localization or function" in prompt
    assert "only when the supplied evidence states them" in prompt
    assert "Do not add, remove, or reorder them" in prompt


def test_reason_prompt_preserves_source_spelling_without_claiming_alias_equivalence():
    candidates = [{"paper_id": "P1", "retrieved_excerpts": ["SENSOR−A was listed as an input."]}]
    prompt = ask_corpus.paper_reason_prompt("Find papers mentioning SENSOR-A.", candidates)

    assert "Use the identifier spelling shown in the source, including punctuation" in prompt
    assert "Do not silently substitute\nthe query spelling" in prompt
    assert "spelling or punctuation variants are aliases unless the supplied\nevidence establishes their equivalence" in prompt
    supplied = prompt.split("Candidate records and retrieved excerpts:\n", 1)[1]
    serialized_candidates, _end = json.JSONDecoder().raw_decode(supplied)
    assert serialized_candidates == candidates
    assert all(symbol not in prompt for symbol in ("ACTB", "CD3D", "HLA-DRA"))


def test_reason_prompt_keeps_requested_detail_field_and_grounding_instructions():
    prompt = ask_corpus.paper_reason_prompt(
        "Give the assay and cohort for each sensor paper.",
        [{"paper_id": "P1", "retrieved_excerpts": ["Synthetic sensors were measured."]}],
        requested_fields=("assay", "cohort"),
    )

    assert "The user explicitly requested these per-paper fields: assay, cohort." in prompt
    assert "Return every requested field for every paper." in prompt
    assert ask_corpus.PAPER_SEARCH_DETAIL_NOT_REPORTED in prompt
    assert "longer than 18 words" in prompt
    assert "report the stated direction or null result, not a direction inferred" in prompt
    assert 'Never write "not adjusted" merely because adjustment' in prompt
    assert "Use only the supplied title, topics, and excerpts" in prompt
    assert "Do not infer a result, cohort, independence, or\ncausal relationship that is not stated" in prompt
