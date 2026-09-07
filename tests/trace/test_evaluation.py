import pytest

from pfempire.trace.evaluation import paired_bootstrap

def test_paired_bootstrap_is_reproducible():
    rows=[{"question_id":f"q{i}","selector":"a","m":i+1} for i in range(3)]+[{"question_id":f"q{i}","selector":"b","m":i} for i in range(3)]
    assert paired_bootstrap(rows,"a","b","m",100,42)==paired_bootstrap(rows,"a","b","m",100,42)

def test_weak_certificate_is_treated_as_abstention(blueprint):
    from conftest import make_candidate
    from pfempire.trace.evaluation import evaluate_selector
    from pfempire.trace.selector import SelectorConfig

    result=evaluate_selector(
        "topk_relevance",
        [make_candidate(1,facets={"f1":1,"f2":0})],
        blueprint,
        {},
        should_answer=True,
        config=SelectorConfig(token_budget=100),
    )
    assert result["evidence_sufficiency"] in {"weak","insufficient"}
    assert result["abstention_correctness"]==0


def test_unannotated_gold_recall_is_not_reported_as_zero(blueprint):
    from conftest import make_candidate
    from pfempire.trace.evaluation import evaluate_selector
    from pfempire.trace.selector import SelectorConfig

    result = evaluate_selector(
        "topk_relevance",
        [make_candidate(1)],
        blueprint,
        {},
        gold_papers=None,
        config=SelectorConfig(token_budget=100),
    )

    assert result["gold_paper_recall"] is None
    assert result["weighted_gold_paper_recall"] is None


def test_paired_bootstrap_rejects_duplicate_rows_and_invalid_confidence():
    duplicate = [
        {"question_id": "q", "selector": "a", "m": 1},
        {"question_id": "q", "selector": "a", "m": 2},
        {"question_id": "q", "selector": "b", "m": 0},
    ]
    with pytest.raises(ValueError, match="duplicate evaluation row"):
        paired_bootstrap(duplicate, "a", "b", "m")
    with pytest.raises(ValueError, match="confidence"):
        paired_bootstrap([], "a", "b", "m", confidence=5)
