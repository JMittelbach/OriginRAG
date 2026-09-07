from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from originrag.trace.blueprint import suggest_blueprint  # noqa: E402
from originrag.trace.io import read_jsonl, read_tsv, write_json, write_tsv  # noqa: E402
from originrag.trace.schemas import EvidenceBlueprint  # noqa: E402
from originrag.analysis.ollama_client import call_ollama_generate  # noqa: E402
from originrag.trace.runtime import candidate_from_result  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--benchmark", default="data/benchmark/trace_pilot_questions.tsv")
    p.add_argument("--backend", choices=["rule_based", "ollama"], default="rule_based")
    p.add_argument("--model")
    p.add_argument("--out-dir", default="outputs/trace_pilot/blueprints")
    p.add_argument("--candidate-dir", default="outputs/trace_pilot/candidate_pools")
    p.add_argument("--score-dir", default="outputs/trace_pilot/facet_candidate_scores")
    a = p.parse_args()
    out = resolve(a.out_dir)
    scores = resolve(a.score_dir)
    out.mkdir(parents=True, exist_ok=True)
    for row in read_tsv(resolve(a.benchmark)):
        bp = suggest_blueprint(row["question_id"], row["question"], row["task_mode"])
        if a.backend == "ollama":
            result = call_ollama_generate(
                ollama_prompt(bp), model=a.model, temperature=0.0, timeout=120, num_predict=4096
            )
            (out / f"{row['question_id']}_planner_raw_output.txt").write_text(
                result.text or "", encoding="utf-8"
            )
            bp = validate_ollama_blueprint(result.text, bp)
        write_json(out / f"{row['question_id']}.json", bp.model_dump(mode="json"))
        pool = resolve(a.candidate_dir) / f"{row['question_id']}.jsonl"
        if pool.exists():
            candidates = [candidate_from_result(from_pool(x), bp) for x in read_jsonl(pool)]
            write_tsv(
                scores / f"{row['question_id']}.tsv",
                [
                    {"chunk_id": c.chunk_id, "paper_id": c.paper_id, **c.facet_scores}
                    for c in candidates
                ],
            )
    print(f"Wrote rule-based suggested blueprints to {out}")
    return 0


def ollama_prompt(bp):
    import json

    return (
        "You suggest an Evidence Blueprint for scientific retrieval. Return only valid JSON matching the supplied object. Improve facets but never answer the question. Every facet and the blueprint must keep review_status suggested.\n"
        + json.dumps(bp.model_dump(mode="json"), ensure_ascii=False)
    )


def validate_ollama_blueprint(text, fallback):
    import json
    import re

    try:
        raw = (text or "").strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.I | re.M).strip()
        bp = EvidenceBlueprint.model_validate(json.loads(raw))
        bp.review_status = "suggested"
        bp.reviewer = None
        for facet in bp.required_facets:
            facet.notes = (
                (facet.notes + "; ") if facet.notes else ""
            ) + "LLM suggestion; requires human review."
        return bp
    except Exception:
        return fallback


def from_pool(x: dict) -> dict:
    return {**x, "score": x.get("raw_retrieval_score", 0)}


def resolve(v: str) -> Path:
    q = Path(v)
    return q if q.is_absolute() else REPO_ROOT / q


if __name__ == "__main__":
    raise SystemExit(main())
