from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_evidence_lineage_graph import apply_overrides  # noqa: E402


def write_overrides(path: Path, rows: list[str]) -> None:
    width = 13
    normalized_rows = []
    for row in rows:
        values = row.rstrip("\n").split("\t")
        normalized_rows.append("\t".join([*values, *([""] * (width - len(values)))]) + "\n")
    path.write_text(
        "paper_id_a\tpaper_id_b\taction\tdependence_score_override\t"
        "distinct_dependence_lower_override\t"
        "distinct_dependence_upper_override\trelationship_type\treason\t"
        "reviewer\torigin_review_status\tadjudicator\treview_timestamp\t"
        "evidence_locator\n"
        + "".join(normalized_rows),
        encoding="utf-8",
    )


def test_same_study_override_can_create_a_clusterable_edge(tmp_path: Path) -> None:
    path = tmp_path / "overrides.tsv"
    write_overrides(path, ["A\tB\tmark_same_study\t\t\t\n"])
    edges = []

    audit = apply_overrides(edges, path)

    assert len(edges) == 1
    assert edges[0].dependence_score == 1.0
    assert edges[0].is_verified is True
    assert edges[0].origin_relation == "same_origin"
    assert "same_study" in edges[0].relationship_types
    assert audit[0]["edge_created"] is True


def test_signal_verification_does_not_decide_origin_relation(tmp_path: Path) -> None:
    path = tmp_path / "overrides.tsv"
    write_overrides(path, ["A\tB\tverify\t\t\t\n"])
    edges = []

    apply_overrides(edges, path)

    assert edges[0].is_verified is True
    assert edges[0].origin_relation == "unresolved"


def test_independence_override_records_explicit_distinct_origin(tmp_path: Path) -> None:
    path = tmp_path / "overrides.tsv"
    write_overrides(path, ["A\tB\tmark_independent\t\t\t\n"])
    edges = []

    apply_overrides(edges, path)

    assert edges[0].origin_relation == "distinct_origin"
    assert edges[0].distinct_dependence_lower == 0.0
    assert edges[0].distinct_dependence_upper == 0.0
    assert "distinct_origin_review" in edges[0].relationship_types


def test_distinct_origin_override_records_conditional_interval(tmp_path: Path) -> None:
    path = tmp_path / "overrides.tsv"
    write_overrides(path, ["A\tB\tmark_distinct_origin\t\t0.2\t0.7\n"])
    edges = []

    audit = apply_overrides(edges, path)

    assert edges[0].origin_relation == "distinct_origin"
    assert edges[0].distinct_dependence_lower == pytest.approx(0.2)
    assert edges[0].distinct_dependence_upper == pytest.approx(0.7)
    assert audit[0]["distinct_dependence_lower"] == pytest.approx(0.2)
    assert audit[0]["distinct_dependence_upper"] == pytest.approx(0.7)


def test_adjudicated_override_preserves_reproducible_review_provenance(
    tmp_path: Path,
) -> None:
    path = tmp_path / "overrides.tsv"
    write_overrides(
        path,
        [
            "A\tB\tmark_distinct_origin\t\t0.2\t0.7\t\t"
            "Separate cohorts\tR1;R2\tadjudicated\tR3\t"
            "2026-07-18T12:00:00Z\tMethods p. 4\n"
        ],
    )
    edges = []

    apply_overrides(edges, path)

    assert edges[0].origin_review_status == "adjudicated"
    assert edges[0].origin_reviewers == ["R1", "R2"]
    assert edges[0].origin_adjudicator == "R3"
    assert edges[0].origin_review_timestamp == "2026-07-18T12:00:00Z"
    assert edges[0].origin_evidence_locator == "Methods p. 4"
    assert edges[0].origin_rationale == "Separate cohorts"


def test_distinct_origin_override_requires_ordered_interval(tmp_path: Path) -> None:
    missing = tmp_path / "missing.tsv"
    write_overrides(missing, ["A\tB\tmark_distinct_origin\t\t\t0.7\n"])
    with pytest.raises(ValueError, match="lower_override is required"):
        apply_overrides([], missing)

    reversed_interval = tmp_path / "reversed.tsv"
    write_overrides(
        reversed_interval,
        ["A\tB\tmark_distinct_origin\t\t0.8\t0.2\n"],
    )
    with pytest.raises(ValueError, match="cannot exceed"):
        apply_overrides([], reversed_interval)


def test_override_rejects_unknown_actions_and_nonfinite_scores(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.tsv"
    write_overrides(unknown, ["A\tB\tdo_magic\t\t\t\n"])
    with pytest.raises(ValueError, match="unknown lineage override action"):
        apply_overrides([], unknown)

    nonfinite = tmp_path / "nonfinite.tsv"
    write_overrides(nonfinite, ["A\tB\toverride_score\tnan\t\t\n"])
    with pytest.raises(ValueError, match="must be finite"):
        apply_overrides([], nonfinite)
