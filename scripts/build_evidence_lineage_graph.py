from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from originrag.trace.io import read_tsv, read_yaml, write_json, write_tsv  # noqa: E402
from originrag.trace.lineage import build_lineage_edges  # noqa: E402
from originrag.trace.method_versions import LINEAGE_GRAPH_SCHEMA_VERSION  # noqa: E402
from originrag.trace.schemas import EvidenceLineageEdge, LineageSignal  # noqa: E402


OVERRIDE_AUDIT_FIELDS = [
    "row_number",
    "paper_id_a",
    "paper_id_b",
    "action",
    "edge_created",
    "dependence_score",
    "is_verified",
    "origin_relation",
    "distinct_dependence_lower",
    "distinct_dependence_upper",
    "origin_review_status",
    "origin_reviewers",
    "origin_adjudicator",
    "origin_review_timestamp",
    "origin_evidence_locator",
    "origin_rationale",
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--signals", default="data/metadata/trace_lineage_signals.tsv")
    p.add_argument("--overrides", default="data/metadata/trace_lineage_overrides.tsv")
    p.add_argument("--config", default="config/trace_lineage_rules.yaml")
    p.add_argument("--out-edges", default="data/metadata/trace_lineage_edges.tsv")
    p.add_argument("--out-graph", default="outputs/trace_pilot/evidence_lineage_graph.json")
    p.add_argument(
        "--out-override-audit",
        default="outputs/trace_pilot/lineage_override_audit.tsv",
    )
    p.add_argument(
        "--graph-status",
        choices=["developmental", "adjudicated", "frozen"],
        default="developmental",
    )
    a = p.parse_args()
    signals = []
    for r in read_tsv(resolve(a.signals)):
        x = dict(r)
        x["details"] = json.loads(x.get("details") or "{}")
        x["signal_strength"] = float(x["signal_strength"])
        signals.append(LineageSignal.model_validate(x))
    weights = read_yaml(resolve(a.config))["lineage"]["weights"]
    edges = build_lineage_edges(signals, weights)
    override_audit = apply_overrides(edges, resolve(a.overrides))
    rows = [
        {
            "paper_id_a": e.paper_id_a,
            "paper_id_b": e.paper_id_b,
            "dependence_score": e.dependence_score,
            "relationship_types": ";".join(e.relationship_types),
            "is_hard_duplicate": e.is_hard_duplicate,
            "is_verified": e.is_verified,
            "origin_relation": e.origin_relation,
            "distinct_dependence_lower": e.distinct_dependence_lower,
            "distinct_dependence_upper": e.distinct_dependence_upper,
            "origin_review_status": e.origin_review_status,
            "origin_reviewers": ";".join(e.origin_reviewers),
            "origin_adjudicator": e.origin_adjudicator or "",
            "origin_review_timestamp": e.origin_review_timestamp or "",
            "origin_evidence_locator": e.origin_evidence_locator or "",
            "origin_rationale": e.origin_rationale or "",
            "explanation": e.explanation,
        }
        for e in edges
    ]
    write_tsv(resolve(a.out_edges), rows)
    write_json(
        resolve(a.out_graph),
        {
            "schema_version": LINEAGE_GRAPH_SCHEMA_VERSION,
            "status": a.graph_status,
            "edges": [e.model_dump(mode="json") for e in edges],
            "override_audit": override_audit,
        },
    )
    write_tsv(resolve(a.out_override_audit), override_audit, OVERRIDE_AUDIT_FIELDS)
    print(f"Lineage edges: {len(edges)}; verified: {sum(e.is_verified for e in edges)}")
    return 0


def apply_overrides(edges: list[EvidenceLineageEdge], path: Path) -> list[dict]:
    if not path.exists():
        return []
    lookup = {tuple(sorted([e.paper_id_a, e.paper_id_b])): e for e in edges}
    audit = []
    allowed_actions = {
        "reject",
        "verify",
        "mark_same_publication",
        "mark_same_study",
        "mark_independent",
        "mark_distinct_origin",
        "override_score",
    }
    for row_number, r in enumerate(read_tsv(path), start=2):
        left = str(r.get("paper_id_a") or "").strip()
        right = str(r.get("paper_id_b") or "").strip()
        action = str(r.get("action") or "").strip()
        if not left or not right or left == right:
            raise ValueError(f"invalid lineage override pair at {path}:{row_number}")
        if action not in allowed_actions:
            raise ValueError(
                f"unknown lineage override action {action!r} at {path}:{row_number}"
            )
        pair = tuple(sorted((left, right)))
        edge = lookup.get(pair)
        created = edge is None
        if edge is None:
            edge = EvidenceLineageEdge(
                paper_id_a=pair[0],
                paper_id_b=pair[1],
                dependence_score=0.0,
                explanation="created by human override",
            )
            edges.append(edge)
            lookup[pair] = edge
        if action == "reject":
            edge.dependence_score = 0
            edge.is_verified = True
            edge.origin_relation = "unresolved"
            edge.distinct_dependence_lower = None
            edge.distinct_dependence_upper = None
        elif action == "verify":
            edge.is_verified = True
        elif action == "mark_same_publication":
            edge.dependence_score = 1.0
            edge.is_verified = True
            edge.is_hard_duplicate = True
            edge.origin_relation = "same_origin"
            edge.distinct_dependence_lower = None
            edge.distinct_dependence_upper = None
            edge.relationship_types = sorted(
                {*edge.relationship_types, "same_publication_version"}
            )
        elif action == "mark_same_study":
            edge.dependence_score = 1.0
            edge.is_verified = True
            edge.origin_relation = "same_origin"
            edge.distinct_dependence_lower = None
            edge.distinct_dependence_upper = None
            edge.relationship_types = sorted({*edge.relationship_types, "same_study"})
        elif action == "mark_independent":
            edge.dependence_score = 0
            edge.is_verified = True
            edge.origin_relation = "distinct_origin"
            edge.distinct_dependence_lower = 0.0
            edge.distinct_dependence_upper = 0.0
            edge.relationship_types = sorted(
                {*edge.relationship_types, "distinct_origin_review"}
            )
        elif action == "mark_distinct_origin":
            lower, upper = parse_distinct_interval(r, path, row_number)
            edge.is_verified = True
            edge.origin_relation = "distinct_origin"
            edge.distinct_dependence_lower = lower
            edge.distinct_dependence_upper = upper
            edge.relationship_types = sorted(
                {*edge.relationship_types, "distinct_origin_review"}
            )
        elif action == "override_score":
            value = float(r.get("dependence_score_override") or "nan")
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"dependence_score_override must be finite and within [0, 1] "
                    f"at {path}:{row_number}"
                )
            edge.dependence_score = value
            edge.is_verified = True
        if action in {
            "mark_same_publication",
            "mark_same_study",
            "mark_independent",
            "mark_distinct_origin",
        }:
            apply_origin_review_metadata(edge, r, path, row_number)
            relationship_type = str(r.get("relationship_type") or "").strip()
            if relationship_type:
                edge.relationship_types = sorted(
                    {*edge.relationship_types, relationship_type}
                )
        EvidenceLineageEdge.model_validate(edge.model_dump(mode="python"))
        edge.explanation = "; ".join(
            part
            for part in [edge.explanation, f"human_override:{action}"]
            if part
        )
        audit.append(
            {
                "row_number": row_number,
                "paper_id_a": pair[0],
                "paper_id_b": pair[1],
                "action": action,
                "edge_created": created,
                "dependence_score": edge.dependence_score,
                "is_verified": edge.is_verified,
                "origin_relation": edge.origin_relation,
                "distinct_dependence_lower": edge.distinct_dependence_lower,
                "distinct_dependence_upper": edge.distinct_dependence_upper,
                "origin_review_status": edge.origin_review_status,
                "origin_reviewers": ";".join(edge.origin_reviewers),
                "origin_adjudicator": edge.origin_adjudicator or "",
                "origin_review_timestamp": edge.origin_review_timestamp or "",
                "origin_evidence_locator": edge.origin_evidence_locator or "",
                "origin_rationale": edge.origin_rationale or "",
            }
        )
    return audit


def apply_origin_review_metadata(
    edge: EvidenceLineageEdge,
    row: dict,
    path: Path,
    row_number: int,
) -> None:
    status = str(row.get("origin_review_status") or "single_reviewed").strip()
    if status not in {"single_reviewed", "adjudicated", "frozen"}:
        raise ValueError(
            f"invalid origin_review_status {status!r} at {path}:{row_number}"
        )
    reviewers = split_reviewers(row.get("reviewer"))
    adjudicator = str(row.get("adjudicator") or "").strip() or None
    timestamp = str(row.get("review_timestamp") or "").strip() or None
    locator = str(row.get("evidence_locator") or "").strip() or None
    rationale = str(row.get("reason") or "").strip() or None
    if status in {"adjudicated", "frozen"}:
        missing = [
            field
            for field, present in (
                ("at least two reviewers", len(reviewers) >= 2),
                ("adjudicator", bool(adjudicator)),
                ("review_timestamp", bool(timestamp)),
                ("evidence_locator", bool(locator)),
                ("reason", bool(rationale)),
            )
            if not present
        ]
        if missing:
            raise ValueError(
                "adjudicated origin decision lacks "
                + ", ".join(missing)
                + f" at {path}:{row_number}"
            )
    edge.origin_review_status = status
    edge.origin_reviewers = reviewers
    edge.origin_adjudicator = adjudicator
    edge.origin_review_timestamp = timestamp
    edge.origin_evidence_locator = locator
    edge.origin_rationale = rationale


def split_reviewers(value: object) -> list[str]:
    raw = str(value or "").replace(",", ";")
    return list(dict.fromkeys(part.strip() for part in raw.split(";") if part.strip()))


def parse_distinct_interval(
    row: dict,
    path: Path,
    row_number: int,
) -> tuple[float, float]:
    values = []
    for field in (
        "distinct_dependence_lower_override",
        "distinct_dependence_upper_override",
    ):
        raw = str(row.get(field) or "").strip()
        if not raw:
            raise ValueError(f"{field} is required at {path}:{row_number}")
        value = float(raw)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(
                f"{field} must be finite and within [0, 1] at {path}:{row_number}"
            )
        values.append(value)
    lower, upper = values
    if lower > upper:
        raise ValueError(
            "distinct dependence lower override cannot exceed upper override "
            f"at {path}:{row_number}"
        )
    return lower, upper


def resolve(v: str) -> Path:
    q = Path(v)
    return q if q.is_absolute() else REPO_ROOT / q


if __name__ == "__main__":
    raise SystemExit(main())
