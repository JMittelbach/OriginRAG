from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

from originrag.concepts.lexicon import load_concept_lexicons, match_concepts_in_text
from originrag.retrieval.chunk_quality import classify_chunk_quality
from originrag.retrieval.keyword import normalize_text


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONCEPT_DIR = REPO_ROOT / "config/concepts"

PREFERRED_SECTIONS = {"abstract", "results", "discussion", "methods", "data_availability"}
EXCLUDED_SECTIONS = {"references", "table", "figure_legend", "supplement"}
BAD_QUALITY_FLAGS = {"reference_like", "table_like", "figure_like", "supplement_like"}

CLAIM_SIGNAL_TERMS = {
    "associate",
    "associated",
    "association",
    "bind",
    "binding",
    "protective",
    "protection",
    "risk",
    "odds",
    "increased",
    "decreased",
    "higher",
    "lower",
    "reduced",
    "enriched",
    "enrichment",
    "upregulated",
    "downregulated",
    "overrepresented",
    "depleted",
    "expressed",
    "expression",
    "linked",
    "correlate",
    "correlated",
    "predict",
    "predicted",
    "identified",
    "demonstrate",
    "demonstrated",
    "show",
    "showed",
    "found",
    "suggest",
    "suggested",
    "revealed",
    "required",
}
HEDGING_TERMS = {"may", "might", "could", "suggest", "suggests", "possible", "potential", "likely", "appears"}
NEGATIVE_TERMS = {"not associated", "no association", "did not", "failed to", "lack of", "absence of", "without"}
LIMITATION_TERMS = {"limitation", "limited", "underpowered", "unclear", "unknown", "further studies", "future work", "not known"}
METHOD_TERMS = {"method", "pipeline", "tool", "algorithm", "classified", "model", "assay", "sequencing", "qPCR", "annotation"}
DATASET_TERMS = {"cohort", "dataset", "samples", "participants", "accession", "GEO", "SRA", "RNA-seq", "transcriptome"}
RELATION_TERMS = {"bind", "binding", "associate", "associated", "correlate", "linked", "increased", "decreased", "expressed"}
SENTENCE_ABBREVIATIONS = [
    "Fig.",
    "Figs.",
    "Eq.",
    "Ref.",
    "Refs.",
    "Dr.",
    "Prof.",
    "et al.",
    "e.g.",
    "i.e.",
    "vs.",
    "P.",
    "No.",
]


def extract_claims_from_chunks(
    chunks: list[dict],
    *,
    metadata: dict[str, dict[str, str]] | None = None,
    max_claims_per_chunk: int = 3,
    concept_dir: Path = DEFAULT_CONCEPT_DIR,
) -> list[dict]:
    claims: list[dict] = []
    metadata = metadata or {}
    for chunk in chunks:
        paper_id = str(chunk.get("paper_id") or "")
        claims.extend(
            extract_claims_from_chunk(
                chunk,
                paper_meta=metadata.get(paper_id, {}),
                max_claims_per_chunk=max_claims_per_chunk,
                concept_dir=concept_dir,
            )
        )
    return dedupe_claims(claims)


def extract_claims_from_chunk(
    chunk: dict,
    *,
    paper_meta: dict[str, str] | None = None,
    max_claims_per_chunk: int = 3,
    min_chars: int = 55,
    concept_dir: Path = DEFAULT_CONCEPT_DIR,
) -> list[dict]:
    """Extract conservative, sentence-level scientific claim candidates from one chunk.

    This is not meant to replace LLM extraction. It creates a deterministic first-pass
    claim database that can be reviewed, benchmarked, and later overwritten by better
    model-assisted extractors.
    """
    paper_meta = paper_meta or {}
    text = str(chunk.get("text") or "").strip()
    if not text:
        return []

    section = str(chunk.get("section") or "unknown").strip().lower()
    if section in EXCLUDED_SECTIONS:
        return []

    quality = classify_chunk_quality(text)
    if set(quality.get("flags") or []) & BAD_QUALITY_FLAGS:
        return []

    paper_id = str(chunk.get("paper_id") or paper_meta.get("paper_id") or "").strip()
    chunk_id = str(chunk.get("chunk_id") or "").strip()
    sentences = split_sentences(text)
    ranked = []
    for sentence in sentences:
        cleaned = clean_sentence(sentence)
        if len(cleaned) < min_chars:
            continue
        concepts = match_concepts_in_text(cleaned, concept_lexicons(concept_dir))
        score = claim_candidate_score(cleaned, concepts=concepts, section=section)
        if score <= 0:
            continue
        ranked.append((score, cleaned, concepts))

    ranked.sort(key=lambda item: (-item[0], len(item[1])))
    claims = []
    for score, sentence, concepts in ranked[: max(0, max_claims_per_chunk)]:
        claim_type = classify_claim_type(sentence)
        source_pages = page_range(chunk)
        claim_id = make_claim_id(paper_id, chunk_id, sentence)
        claim = {
            "claim_id": claim_id,
            "claim_hash": claim_hash(sentence),
            "claim_text": sentence,
            "claim_type": claim_type,
            "evidence_type": infer_evidence_type(sentence, claim_type),
            "evidence_strength": infer_evidence_strength(sentence, section, claim_type),
            "direction": infer_direction(sentence),
            "uncertainty": infer_uncertainty(sentence),
            "organism": infer_organism(sentence),
            "pathogen": infer_pathogen(sentence),
            "host_system": first_concept_name(concepts, {"cell_type", "immune_state", "pathway"}),
            "disease_context": first_concept_name(concepts, {"disease"}),
            "phenotype": first_concept_name(concepts, {"phenotype"}),
            "parasite_feature": first_concept_name(concepts, {"gene", "protein", "domain"}),
            "host_feature": first_concept_name(concepts, {"receptor", "immune_state", "pathway"}),
            "method": first_concept_name(concepts, {"method", "assay", "tool"}),
            "dataset_or_cohort": infer_dataset_or_cohort(sentence),
            "source_paper_id": paper_id,
            "source_chunk_id": chunk_id,
            "source_section": section,
            "source_pages": source_pages,
            "source_text": sentence,
            "entities": compact_entities(concepts),
            "entity_ids": ";".join(entity["concept_id"] for entity in compact_entities(concepts)),
            "claim_quality_flags": claim_quality_flags(
                sentence,
                concepts=concepts,
                section=section,
                paper_meta=paper_meta,
            ),
            "duplicate_count": 1,
            "confidence": round(min(1.0, max(0.1, score / 10.0)), 3),
            "citation_short": paper_meta.get("citation_short") or citation_short(paper_meta, paper_id),
            "title": paper_meta.get("title", ""),
            "year": paper_meta.get("year", ""),
            "doi": paper_meta.get("doi", ""),
        }
        claims.append(claim)
    return claims


def split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []
    protected = protect_sentence_abbreviations(normalized)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])", protected)
    return [restore_sentence_abbreviations(part).strip() for part in parts if part.strip()]


def clean_sentence(sentence: str) -> str:
    sentence = re.sub(r"\s+", " ", sentence or "").strip()
    sentence = re.sub(r"^\W+", "", sentence)
    return sentence.strip()


def claim_candidate_score(sentence: str, *, concepts: list[dict], section: str) -> float:
    normalized = normalize_text(sentence)
    signal_hits = sum(1 for term in CLAIM_SIGNAL_TERMS if normalize_text(term) in normalized)
    relation_hits = sum(1 for term in RELATION_TERMS if normalize_text(term) in normalized)
    concept_hits = min(5, len({concept["concept_id"] for concept in concepts}))
    if signal_hits == 0 and concept_hits < 2:
        return 0.0
    score = (signal_hits * 1.4) + relation_hits + (concept_hits * 1.2)
    if section in {"results", "discussion", "abstract"}:
        score += 1.2
    elif section in {"methods", "data_availability"}:
        score += 0.5
    if normalized.startswith(("we ", "our ", "this study")):
        score += 0.4
    if len(sentence) > 450:
        score -= 1.0
    return score


def classify_claim_type(sentence: str) -> str:
    normalized = normalize_text(sentence)
    if contains_phrase(normalized, NEGATIVE_TERMS):
        return "negative_result"
    if contains_phrase(normalized, LIMITATION_TERMS):
        return "limitation"
    if contains_phrase(normalized, DATASET_TERMS):
        return "dataset"
    if contains_phrase(normalized, METHOD_TERMS):
        return "method"
    if contains_phrase(normalized, HEDGING_TERMS):
        return "speculation"
    if any(normalize_text(term) in normalized for term in CLAIM_SIGNAL_TERMS):
        return "finding"
    return "background"


def infer_evidence_type(sentence: str, claim_type: str) -> str:
    normalized = normalize_text(sentence)
    if claim_type == "method":
        return "method"
    if claim_type == "dataset" or contains_phrase(normalized, DATASET_TERMS):
        return "dataset"
    if any(term in normalized for term in ["associated", "association", "correlat", "linked"]):
        return "association"
    if any(term in normalized for term in ["bind", "binding", "adhesion", "cytoadherence"]):
        return "mechanistic"
    if any(term in normalized for term in ["higher", "lower", "increased", "decreased", "reduced"]):
        return "comparative"
    return "textual"


def infer_evidence_strength(sentence: str, section: str, claim_type: str) -> str:
    normalized = normalize_text(sentence)
    if claim_type in {"speculation", "limitation"}:
        return "weak"
    if contains_phrase(normalized, HEDGING_TERMS):
        return "weak"
    if section in {"results", "abstract"} and any(term in normalized for term in ["demonstrated", "showed", "found", "revealed"]):
        return "strong"
    if section in {"results", "discussion", "abstract"}:
        return "moderate"
    return "unclear"


def infer_direction(sentence: str) -> str:
    normalized = normalize_text(sentence)
    if contains_phrase(normalized, NEGATIVE_TERMS):
        return "negative"
    if any(term in normalized for term in ["increased", "higher", "enriched", "upregulated", "promotes"]):
        return "positive"
    if any(term in normalized for term in ["decreased", "lower", "reduced", "downregulated"]):
        return "negative"
    return "unknown"


def infer_uncertainty(sentence: str) -> str:
    normalized = normalize_text(sentence)
    if contains_phrase(normalized, HEDGING_TERMS | LIMITATION_TERMS):
        return "high"
    if any(term in normalized for term in ["demonstrated", "showed", "found", "revealed"]):
        return "low"
    return "medium"


def infer_organism(sentence: str) -> str | None:
    normalized = normalize_text(sentence)
    if "human" in normalized or "children" in normalized or "patients" in normalized:
        return "human"
    if "mosquito" in normalized:
        return "mosquito"
    return None


def infer_pathogen(sentence: str) -> str | None:
    normalized = normalize_text(sentence)
    if "falciparum" in normalized:
        return "Plasmodium falciparum"
    if "vivax" in normalized:
        return "Plasmodium vivax"
    if "plasmodium" in normalized or "malaria" in normalized:
        return "Plasmodium"
    return None


def infer_dataset_or_cohort(sentence: str) -> str | None:
    match = re.search(r"\b([A-Z][A-Za-z-]+(?:\s+[A-Z][A-Za-z-]+){0,3}\s+cohort)\b", sentence)
    if match:
        return match.group(1)
    normalized = normalize_text(sentence)
    if "cohort" in normalized:
        return "cohort"
    if "dataset" in normalized:
        return "dataset"
    return None


def first_concept_name(concepts: list[dict], entity_types: set[str]) -> str | None:
    for concept in concepts:
        if concept.get("entity_type") in entity_types:
            return str(concept.get("name") or "")
    return None


def compact_entities(concepts: list[dict]) -> list[dict]:
    seen = set()
    entities = []
    for concept in concepts:
        concept_id = str(concept.get("concept_id") or "")
        if not concept_id or concept_id in seen:
            continue
        seen.add(concept_id)
        entities.append(
            {
                "concept_id": concept_id,
                "name": concept.get("name", ""),
                "entity_type": concept.get("entity_type", ""),
                "matched_text": concept.get("matched_text", ""),
                "lexicon": concept.get("lexicon", ""),
            }
        )
    return entities


def claim_quality_flags(
    sentence: str,
    *,
    concepts: list[dict],
    section: str,
    paper_meta: dict[str, str],
) -> list[str]:
    flags = []
    normalized = normalize_text(sentence)
    if not paper_meta.get("doi"):
        flags.append("missing_doi")
    if section == "unknown":
        flags.append("unknown_section")
    if len({concept["concept_id"] for concept in concepts}) < 2:
        flags.append("low_concept_count")
    if contains_phrase(normalized, HEDGING_TERMS):
        flags.append("hedged_language")
    if len(sentence) > 450:
        flags.append("long_sentence")
    if re.search(r"\b\d+\s*[-–]\s*\d+\b", sentence) and "table" in normalized:
        flags.append("table_like_sentence")
    return flags


def dedupe_claims(claims: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    duplicate_counts: dict[tuple[str, str], int] = {}
    for claim in claims:
        key = (
            str(claim.get("source_paper_id") or ""),
            str(claim.get("claim_hash") or ""),
        )
        duplicate_counts[key] = duplicate_counts.get(key, 0) + 1
        current = grouped.get(key)
        if current is None or claim_preference_score(claim) > claim_preference_score(current):
            grouped[key] = claim
    deduped = []
    for key, claim in grouped.items():
        claim = dict(claim)
        claim["duplicate_count"] = duplicate_counts.get(key, 1)
        if claim["duplicate_count"] > 1:
            flags = list(claim.get("claim_quality_flags") or [])
            if "duplicate_sentence" not in flags:
                flags.append("duplicate_sentence")
            claim["claim_quality_flags"] = flags
        deduped.append(claim)
    return sorted(
        deduped,
        key=lambda item: (
            str(item.get("source_paper_id") or ""),
            str(item.get("source_chunk_id") or ""),
            str(item.get("claim_id") or ""),
        ),
    )


def claim_preference_score(claim: dict) -> float:
    section = str(claim.get("source_section") or "")
    score = float(claim.get("confidence") or 0.0)
    if section in {"results", "abstract"}:
        score += 0.3
    elif section == "discussion":
        score += 0.2
    if claim.get("doi"):
        score += 0.1
    score -= len(claim.get("claim_quality_flags") or []) * 0.04
    return score


def page_range(chunk: dict) -> str:
    start = str(chunk.get("page_start") or "").strip()
    end = str(chunk.get("page_end") or "").strip()
    if start and end and start != end:
        return f"{start}-{end}"
    return start or end


def citation_short(paper_meta: dict[str, str], paper_id: str) -> str:
    author = str(paper_meta.get("author") or "").strip()
    year = str(paper_meta.get("year") or "").strip()
    if author and year:
        return f"{author} {year}"
    return paper_id


def make_claim_id(paper_id: str, chunk_id: str, sentence: str) -> str:
    prefix = paper_id or "CLAIM"
    digest = hashlib.sha1(f"{paper_id}|{chunk_id}|{normalize_text(sentence)}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_CLM_{digest}"


def claim_hash(sentence: str) -> str:
    return hashlib.sha1(normalize_text(sentence).encode("utf-8")).hexdigest()


def contains_phrase(normalized_text: str, phrases: set[str]) -> bool:
    return any(normalize_text(phrase) in normalized_text for phrase in phrases)


def protect_sentence_abbreviations(text: str) -> str:
    protected = text
    for index, abbreviation in enumerate(SENTENCE_ABBREVIATIONS):
        placeholder = f"__ABBR{index}__"
        protected = protected.replace(abbreviation, placeholder)
    return protected


def restore_sentence_abbreviations(text: str) -> str:
    restored = text
    for index, abbreviation in enumerate(SENTENCE_ABBREVIATIONS):
        restored = restored.replace(f"__ABBR{index}__", abbreviation)
    return restored


@lru_cache(maxsize=4)
def concept_lexicons(concept_dir: Path = DEFAULT_CONCEPT_DIR) -> dict[str, list[dict]]:
    if not Path(concept_dir).exists():
        return {}
    return load_concept_lexicons(Path(concept_dir))
