from __future__ import annotations


SECTION_WEIGHTS: dict[str, dict[str, float]] = {
    "evidence": {
        "abstract": 1.32,
        "results": 1.35,
        "discussion": 1.30,
        "methods": 0.82,
        "statistical_analysis": 0.82,
        "introduction": 0.80,
        "figure_legend": 0.62,
        "table": 0.55,
        "supplement": 0.70,
        "data_availability": 0.70,
        "references": 0.05,
        "unknown": 0.92,
    },
    "methods": {
        "methods": 1.35,
        "statistical_analysis": 1.30,
        "data_availability": 1.20,
        "supplement": 1.00,
        "results": 0.90,
        "discussion": 0.80,
        "abstract": 0.80,
        "introduction": 0.60,
        "figure_legend": 0.70,
        "table": 0.80,
        "references": 0.05,
        "unknown": 0.90,
    },
    "background": {
        "abstract": 1.25,
        "introduction": 1.15,
        "discussion": 1.05,
        "results": 0.95,
        "methods": 0.65,
        "statistical_analysis": 0.65,
        "figure_legend": 0.65,
        "table": 0.60,
        "supplement": 0.65,
        "data_availability": 0.60,
        "references": 0.05,
        "unknown": 1.00,
    },
}


def get_section_weight(section: str, query_mode: str = "evidence") -> float:
    mode = (query_mode or "evidence").strip().lower()
    section_key = (section or "unknown").strip().lower()
    weights = SECTION_WEIGHTS.get(mode, SECTION_WEIGHTS["evidence"])
    return weights.get(section_key, weights.get("unknown", 1.0))
