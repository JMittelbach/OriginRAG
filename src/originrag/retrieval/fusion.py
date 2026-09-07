from __future__ import annotations

import math
from copy import deepcopy


def reciprocal_rank_fusion(
    ranked_lists: list[list[dict]],
    id_key: str = "chunk_id",
    k: int = 60,
) -> list[dict]:
    fused: dict[str, dict] = {}
    for list_index, ranked_list in enumerate(ranked_lists, start=1):
        for rank, item in enumerate(ranked_list, start=1):
            item_id = str(item.get(id_key) or "")
            if not item_id:
                continue
            query_label = str(item.get("query_id") or item.get("query_label") or f"query_{list_index}")
            contribution = 1.0 / (k + rank)
            if item_id not in fused:
                fused[item_id] = deepcopy(item)
                fused[item_id]["rrf_score"] = 0.0
                fused[item_id]["contributing_queries"] = []
                fused[item_id]["query_ranks"] = {}
                fused[item_id]["rank_sources"] = []
            elif numeric_score(item.get("score")) > numeric_score(
                fused[item_id].get("score")
            ):
                aggregate = {
                    key: fused[item_id][key]
                    for key in (
                        "rrf_score",
                        "contributing_queries",
                        "query_ranks",
                        "rank_sources",
                    )
                }
                fused[item_id] = deepcopy(item)
                fused[item_id].update(aggregate)
            fused[item_id]["rrf_score"] += contribution
            if query_label not in fused[item_id]["contributing_queries"]:
                fused[item_id]["contributing_queries"].append(query_label)
            fused[item_id]["query_ranks"][query_label] = rank
            fused[item_id]["rank_sources"].append(
                {
                    "query": query_label,
                    "rank": rank,
                    "score": item.get("score", ""),
                }
            )

    results = sorted(
        fused.values(),
        key=lambda item: (
            -numeric_score(item.get("rrf_score")),
            -numeric_score(item.get("score")),
            str(item.get(id_key) or ""),
        ),
    )
    for rank, item in enumerate(results, start=1):
        item["rank"] = rank
        item["rrf_score"] = round(float(item.get("rrf_score") or 0.0), 6)
    return results


def numeric_score(value: object) -> float:
    try:
        parsed = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0
