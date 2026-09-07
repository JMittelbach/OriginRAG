import pytest

from originrag.trace.dependence import (
    combine_signal_dependence,
    dependence_between,
    dependence_lookup,
)
from originrag.trace.schemas import EvidenceLineageEdge, LineageSignal


def signal(kind, strength=1):
    return LineageSignal(
        signal_id="s",
        paper_id_a="a",
        paper_id_b="b",
        signal_type=kind,
        signal_strength=strength,
        source="test",
        status="suggested",
    )


def test_dependence_is_bounded_and_duplicate_high():
    assert combine_signal_dependence([signal("same_publication_version")]) == 1
    assert 0 <= combine_signal_dependence([signal("high_author_overlap", 0.5)]) <= 1


def test_diagonal_is_one_and_independent_zero():
    assert dependence_between("a", "a", {}) == 1
    assert dependence_between("a", "b", {}) == 0


def test_dependence_lookup_is_symmetric_and_rejects_conflicts():
    edge = EvidenceLineageEdge(
        paper_id_a="a", paper_id_b="b", dependence_score=0.7
    )
    lookup = dependence_lookup([edge])

    assert dependence_between("a", "b", lookup) == pytest.approx(0.7)
    assert dependence_between("b", "a", lookup) == pytest.approx(0.7)

    conflicting = edge.model_copy(update={"paper_id_a": "b", "paper_id_b": "a", "dependence_score": 0.2})
    with pytest.raises(ValueError, match="conflicting duplicate"):
        dependence_lookup([edge, conflicting])
