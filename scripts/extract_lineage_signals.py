from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from pfempire.trace.io import read_jsonl, read_tsv, write_tsv  # noqa: E402
from pfempire.trace.lineage import suggest_lineage_signals  # noqa: E402

FIELDS = [
    "signal_id",
    "paper_id_a",
    "paper_id_b",
    "signal_type",
    "signal_strength",
    "source",
    "details",
    "status",
    "reviewer",
]
PER_POOL_FIELDS = ["question_id", *FIELDS]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--papers", default="data/metadata/papers.tsv")
    p.add_argument("--chunks-dir", default="data/chunks")
    p.add_argument(
        "--candidate-manifest", default="outputs/trace_pilot/candidate_pool_manifest.tsv"
    )
    p.add_argument("--candidate-dir", default="outputs/trace_pilot/candidate_pools")
    p.add_argument("--out", default="data/metadata/trace_lineage_signals.tsv")
    p.add_argument("--review-out", default="outputs/trace_pilot/lineage_review_needed.tsv")
    p.add_argument(
        "--per-pool",
        action="store_true",
        help="Screen paper pairs within each question pool and retain question_id.",
    )
    a = p.parse_args()
    papers_by_id = {r["paper_id"]: r for r in read_tsv(resolve(a.papers))}
    chunks_dir = resolve(a.chunks_dir)
    candidate_papers = {}
    text_by_paper = {}
    pools_by_question = {}
    for m in read_tsv(resolve(a.candidate_manifest)):
        pool = resolve(a.candidate_dir) / f"{m['question_id']}.jsonl"
        if not pool.exists():
            continue
        question_papers = {}
        for row in read_jsonl(pool):
            pid = row["paper_id"]
            paper = {
                **papers_by_id.get(pid, {}),
                **{k: row.get(k, "") for k in ["paper_id", "title", "doi", "author", "year"]},
            }
            candidate_papers[pid] = paper
            question_papers[pid] = paper
            text_by_paper[pid] = text_by_paper.get(pid, "") + " " + str(
                row.get("text", "")
            )
        pools_by_question[m["question_id"]] = question_papers
    for pid in candidate_papers:
        text_by_paper[pid] = lineage_chunks_for_paper(
            pid,
            chunks_dir,
            fallback=text_by_paper.get(pid, ""),
        )
    rows = []
    import json

    if a.per_pool:
        signal_groups = [
            (
                question_id,
                suggest_lineage_signals(list(papers.values()), text_by_paper),
            )
            for question_id, papers in pools_by_question.items()
        ]
    else:
        signal_groups = [
            (None, suggest_lineage_signals(list(candidate_papers.values()), text_by_paper))
        ]
    for question_id, signals in signal_groups:
        for signal in signals:
            row = signal.model_dump(mode="json")
            row["details"] = json.dumps(row["details"], sort_keys=True)
            if question_id is not None:
                row["question_id"] = question_id
            rows.append(row)
    fields = PER_POOL_FIELDS if a.per_pool else FIELDS
    write_tsv(resolve(a.out), rows, fields)
    write_tsv(resolve(a.review_out), rows, fields)
    print(f"Suggested lineage signals: {len(rows)}")
    return 0


def resolve(v: str) -> Path:
    q = Path(v)
    return q if q.is_absolute() else REPO_ROOT / q


def lineage_text_for_paper(paper_id: str, chunks_dir: Path, *, fallback: str) -> str:
    """Use complete local paper text for origin signals, not retrieval snippets."""
    return " ".join(
        str(row.get("text") or "")
        for row in lineage_chunks_for_paper(paper_id, chunks_dir, fallback=fallback)
    )


def lineage_chunks_for_paper(
    paper_id: str, chunks_dir: Path, *, fallback: str
) -> list[dict]:
    """Return every local chunk so origin signals retain exact page/chunk evidence."""
    full_chunks_path = Path(chunks_dir) / f"{paper_id}.jsonl"
    if not full_chunks_path.exists():
        return (
            [{"chunk_id": f"{paper_id}_FALLBACK", "text": fallback[:30000]}]
            if fallback
            else []
        )
    return [dict(row) for row in read_jsonl(full_chunks_path)]


if __name__ == "__main__":
    raise SystemExit(main())
