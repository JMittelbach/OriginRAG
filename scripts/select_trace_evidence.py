from __future__ import annotations
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from originrag.trace.io import read_jsonl  # noqa: E402
from originrag.trace.runtime import run_trace_selection  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--question-id", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--task-mode", default="evidence_qa")
    p.add_argument("--candidate-pool", required=True)
    p.add_argument(
        "--selector",
        choices=[
            "topk",
            "mmr",
            "trace_no_lineage",
            "trace",
            "trace_flow",
            "trace_flow_no_anchor",
            "trace_flow_unbounded",
            "trace_flow_relevance_reserve",
            "trace_flow_diverse_reserve",
        ],
        default="trace_flow",
    )
    p.add_argument("--blueprint")
    p.add_argument("--lineage-graph", default="outputs/trace_pilot/evidence_lineage_graph.json")
    p.add_argument("--trace-config", default="config/trace_rag.yaml")
    p.add_argument("--token-budget", type=int, default=8000)
    p.add_argument("--out-dir", required=True)
    a = p.parse_args()
    rows = []
    for x in read_jsonl(resolve(a.candidate_pool)):
        rows.append(
            {
                **x,
                "score": x.get("raw_retrieval_score", 0),
                "paper_relevance_score": x.get("paper_rrf_score", 0),
            }
        )
    result = run_trace_selection(
        a.question,
        a.task_mode,
        rows,
        resolve(a.out_dir),
        selector=a.selector,
        config_path=resolve(a.trace_config),
        blueprint_path=resolve(a.blueprint) if a.blueprint else None,
        lineage_graph_path=resolve(a.lineage_graph),
        token_budget=a.token_budget,
        question_id=a.question_id,
    )
    print(
        f"Selected {len(result['selected_results'])} chunks; certificate={result['certificate'].evidence_sufficiency}"
    )
    return 0


def resolve(v: str) -> Path:
    q = Path(v)
    return q if q.is_absolute() else REPO_ROOT / q


if __name__ == "__main__":
    raise SystemExit(main())
