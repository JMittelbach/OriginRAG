from __future__ import annotations

from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ask_corpus  # noqa: E402


QUESTION = "How accurate is sensor calibration?"


def quality(text, section="methods"):
    return ask_corpus.prompt_visible_answer_quality(
        {"paper_id": "S1", "chunk_id": "S1_a", "text": text, "section": section},
        question=QUESTION, max_chars_per_chunk=900,
    )


@pytest.mark.parametrize("text", [
    "We evaluated whether sensor calibration reduces measurement error across devices.",
    "We assessed how sensor calibration increases measurement accuracy.",
    "We aimed to determine whether sensor calibration reduces error.",
    "The study sought to test whether sensor calibration differs between conditions.",
])
def test_inquiry_is_relevant_but_not_credited_as_a_reported_result(text):
    score = quality(text)
    assert score[4:10] == (0, 0, 0, 0, 0, 0)
    assert score[10] > 0


def test_measured_result_is_not_displaced_by_a_question_like_methods_sentence():
    methods = {"paper_id": "S1", "chunk_id": "S1_methods", "section": "methods", "text": (
        "We evaluated whether sensor calibration reduces measurement error across devices. "
        "Our analysis assessed sensor calibration accuracy using recorded signals."
    )}
    results = {"paper_id": "S1", "chunk_id": "S1_results", "section": "results", "text": (
        "Sensor calibration yielded a mean absolute error of 2.1 units versus 3.4 units "
        "without calibration, measured over 40 devices in Canada."
    )}
    selected = ask_corpus.shortlist_answer_candidates(
        [methods, results], max_papers=2, question=QUESTION, max_chars_per_chunk=900,
    )
    assert [item["chunk_id"] for item in selected] == ["S1_results"]
    assert results["text"] == selected[0]["text"]


def test_methods_stay_available_for_a_methods_question():
    methods = {"paper_id": "S1", "chunk_id": "S1_methods", "section": "methods", "text": (
        "We evaluated whether sensor calibration changes accuracy using cross-validation "
        "with ten held-out devices."
    )}
    selected = ask_corpus.shortlist_answer_candidates(
        [methods], max_papers=2, question="Which cross-validation method evaluated sensor calibration?",
        max_chars_per_chunk=900,
    )
    assert selected == [methods]


def test_inquiry_followed_by_a_real_reported_result_keeps_result_credit():
    score = quality(
        "We evaluated whether sensor calibration reduces error and found lower error with calibration."
    )
    assert score[4] == 1
    assert score[6] == 1


def test_direct_measurement_without_an_inquiry_keeps_result_credit():
    score = quality("We measured sensor calibration accuracy in forty devices.")
    assert score[6] == 1
