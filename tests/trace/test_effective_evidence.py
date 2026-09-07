import pytest

from conftest import make_candidate
from pfempire.trace.evidence_weights import effective_independent_evidence


def test_five_independent_papers_near_five():
    result = effective_independent_evidence([make_candidate(i) for i in range(5)], {})
    assert result["evidence_accounting_version"] == "unit_max_v2"
    assert result["n_eff"] == pytest.approx(5.0)
    assert result["independent_evidence_ratio"] == pytest.approx(1.0)
    assert result["evidence_inflation_factor"] == pytest.approx(1.0)


def test_five_duplicate_studies_near_one():
    candidates = [make_candidate(i) for i in range(5)]
    dependence = {
        (left.paper_id, right.paper_id): 1
        for left in candidates
        for right in candidates
    }
    result = effective_independent_evidence(candidates, dependence)
    assert result["n_eff"] == pytest.approx(1.0)
    assert result["independent_evidence_ratio"] == pytest.approx(0.2)
    assert result["evidence_inflation_factor"] == pytest.approx(5.0)


def test_independent_complement_increases_neff():
    first, second = make_candidate(1), make_candidate(2)
    assert effective_independent_evidence(
        [first, second], {}
    )["n_eff"] > effective_independent_evidence([first], {})["n_eff"]


def test_shared_study_lineage_is_fully_dependent_without_pairwise_edges():
    candidates = [
        make_candidate(index).model_copy(update={"study_lineage_id": "L_SHARED"})
        for index in range(5)
    ]

    result = effective_independent_evidence(candidates, {})

    assert result["evidence_lineage_count"] == 1
    assert result["n_eff"] == pytest.approx(1.0)
    assert result["evidence_inflation_factor"] == pytest.approx(5.0)


def test_conflicting_lineages_for_one_paper_are_rejected():
    first = make_candidate(1).model_copy(
        update={"paper_id": "P_SHARED", "study_lineage_id": "L1"}
    )
    second = make_candidate(2).model_copy(
        update={"paper_id": "P_SHARED", "study_lineage_id": "L2"}
    )

    with pytest.raises(ValueError, match="conflicting study lineage"):
        effective_independent_evidence([first, second], {})


def test_irrelevant_papers_do_not_inflate_effective_evidence():
    relevant = make_candidate(1, facets={"f1": 1.0})
    irrelevant = [
        make_candidate(index, facets={"unrelated": 0.0}) for index in range(2, 5)
    ]

    result = effective_independent_evidence([relevant, *irrelevant], {})

    assert result["raw_paper_count"] == 4
    assert result["evidence_paper_count"] == 1
    assert result["n_eff"] == pytest.approx(1.0)


def test_conflicting_publication_families_for_one_paper_are_rejected():
    first = make_candidate(1, paper="P_SHARED", family="F1")
    second = make_candidate(2, paper="P_SHARED", family="F2")

    with pytest.raises(ValueError, match="conflicting publication family"):
        effective_independent_evidence([first, second], {})


def test_publication_versions_are_fully_dependent_without_graph_edges():
    candidates = [
        make_candidate(1, paper="PREPRINT", family="DOI_FAMILY"),
        make_candidate(2, paper="JOURNAL", family="DOI_FAMILY"),
    ]

    result = effective_independent_evidence(candidates, {})

    assert result["publication_family_count"] == 1
    assert result["n_eff"] == pytest.approx(1.0)


def test_reanalyses_of_same_dataset_are_not_independent() -> None:
    candidates = [
        make_candidate(index).model_copy(update={"dataset_lineage_id": "GSE_SHARED"})
        for index in range(1, 4)
    ]

    result = effective_independent_evidence(candidates, {})

    assert result["evidence_lineage_count"] == 1
    assert result["n_eff"] == pytest.approx(1.0)
