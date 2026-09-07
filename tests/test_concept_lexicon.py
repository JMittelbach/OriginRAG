from __future__ import annotations

from pathlib import Path

from originrag.concepts.lexicon import load_concept_lexicons, match_concepts_in_text


def test_concept_lexicon_matches_aliases() -> None:
    lexicons = load_concept_lexicons(Path("config/concepts"))

    matches = match_concepts_in_text(
        "CIDR alpha 1 domains in PfEMP1 can bind endothelial protein C receptor.",
        lexicons,
    )
    concept_ids = {match["concept_id"] for match in matches}

    assert "cidralpha1" in concept_ids
    assert "pfemp1" in concept_ids
    assert "epcr" in concept_ids
