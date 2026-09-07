from __future__ import annotations

from pfempire.retrieval.section_weights import get_section_weight


def test_evidence_mode_prefers_results_and_discussion_over_introduction() -> None:
    assert get_section_weight("results", "evidence") > get_section_weight("introduction", "evidence")
    assert get_section_weight("discussion", "evidence") > get_section_weight("introduction", "evidence")


def test_references_strongly_downweighted() -> None:
    assert get_section_weight("references", "evidence") < 0.1


def test_methods_mode_prefers_methods_over_results() -> None:
    assert get_section_weight("methods", "methods") > get_section_weight("results", "methods")
