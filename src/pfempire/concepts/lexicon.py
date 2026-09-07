from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


def load_concept_lexicons(config_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Load concept lexicons from YAML files in a directory."""
    lexicons: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(Path(config_dir).glob("*.yaml")):
        with path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        concepts = payload.get("concepts", [])
        if not isinstance(concepts, list):
            raise ValueError(f"Concept file {path} must contain a concepts list")
        lexicons[path.stem] = [_normalize_concept(item, path) for item in concepts]
    return lexicons


def match_concepts_in_text(text: str, lexicons: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Return exact name/alias matches with character spans."""
    normalized_text = normalize_text(text)
    if not normalized_text:
        return []

    matches: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for lexicon_name, concepts in lexicons.items():
        for concept in concepts:
            terms = [concept["name"], *concept.get("aliases", [])]
            for term in terms:
                normalized_term = normalize_text(term)
                if not normalized_term:
                    continue
                pattern = re.compile(rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])")
                for match in pattern.finditer(normalized_text):
                    key = (concept["id"], match.start(), match.end())
                    if key in seen:
                        continue
                    seen.add(key)
                    matches.append(
                        {
                            "concept_id": concept["id"],
                            "name": concept["name"],
                            "entity_type": concept["type"],
                            "matched_text": term,
                            "normalized_match": normalized_term,
                            "lexicon": lexicon_name,
                            "start": match.start(),
                            "end": match.end(),
                        }
                    )
    return sorted(matches, key=lambda item: (item["start"], item["end"], item["concept_id"]))


def normalize_text(value: str) -> str:
    normalized = (value or "").lower()
    replacements = {
        "α": "alpha",
        "β": "beta",
        "γ": "gamma",
        "δ": "delta",
        "–": "-",
        "—": "-",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_concept(item: Any, path: Path) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"Concept entries in {path} must be mappings")
    concept_id = str(item.get("id") or "").strip()
    name = str(item.get("name") or "").strip()
    entity_type = str(item.get("type") or "").strip()
    aliases = item.get("aliases") or []
    if not concept_id or not name or not entity_type:
        raise ValueError(f"Concept entries in {path} require id, name, and type")
    if not isinstance(aliases, list):
        raise ValueError(f"Concept {concept_id!r} aliases must be a list")
    return {
        "id": concept_id,
        "name": name,
        "type": entity_type,
        "aliases": [str(alias).strip() for alias in aliases if str(alias).strip()],
    }
