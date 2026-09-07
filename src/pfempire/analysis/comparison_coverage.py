from __future__ import annotations

import re

from pfempire.analysis.evidence_pack_grading import (
    comparison_report_descriptors,
    named_report_references,
)


COMPARISON_GROUP_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "asymptomatic infection",
        (
            "asymptomatic",
            "asymp tomatic",
            "asymptomatic infection",
            "asymptomatic malaria",
            "asymptomatic parasitemia",
            "asymptomatic parasitaemia",
        ),
    ),
    (
        "clinical malaria",
        (
            "clinical malaria",
            "clinical infection",
            "clinical infections",
            "symptomatic malaria",
        ),
    ),
    ("severe malaria", ("severe malaria", "severe disease")),
    ("uncomplicated malaria", ("uncomplicated malaria",)),
    ("cerebral malaria", ("cerebral malaria",)),
    ("severe anaemia", ("severe anaemia", "severe anemia")),
    (
        "hyperlactataemia or acidosis",
        ("hyperlactataemia", "hyperlactatemia", "acidosis"),
    ),
    ("respiratory distress", ("respiratory distress",)),
    ("healthy controls", ("healthy control", "healthy controls")),
    ("infected", ("infected participants", "infected patients", "infected people")),
    (
        "uninfected",
        ("uninfected participants", "uninfected patients", "uninfected people"),
    ),
    ("treated", ("treated participants", "treated patients", "treated group")),
    ("untreated", ("untreated participants", "untreated patients", "untreated group")),
    ("responders", ("responders", "responder group")),
    ("nonresponders", ("nonresponders", "non responders", "nonresponder group")),
)

NULL_COMPARISON_MARKERS = (
    "did not differ",
    "does not differ",
    "no difference",
    "no significant difference",
    "not significantly different",
    "was not associated",
    "were not associated",
    "no association",
    "not enriched",
    "was similar",
    "were similar",
    "similar between",
)


def normalize_comparison_text(value: object) -> str:
    normalized = str(value or "").lower().translate(
        str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalized).split())


def contains_comparison_alias(normalized_text: str, aliases: tuple[str, ...]) -> bool:
    padded_text = f" {normalized_text} "
    return any(
        f" {normalize_comparison_text(alias)} " in padded_text for alias in aliases
    )


def requested_comparison_groups(
    question: str,
) -> list[tuple[str, tuple[str, ...]]]:
    normalized_question = normalize_comparison_text(question)
    return [
        (label, aliases)
        for label, aliases in COMPARISON_GROUP_ALIASES
        if contains_comparison_alias(normalized_question, aliases)
    ]


def direct_comparison_candidate_chunks(question: str, evidence_pack: dict) -> list[dict]:
    """Return prompt-visible chunks that explicitly name every requested group."""
    groups = requested_comparison_groups(question)
    if len(groups) < 2:
        return []

    candidates = []
    for chunk in evidence_pack.get("chunks", []):
        if not str(chunk.get("evidence_id") or "").strip():
            continue
        normalized_evidence = normalize_comparison_text(chunk.get("text") or "")
        if all(
            contains_comparison_alias(normalized_evidence, aliases)
            for _, aliases in groups
        ):
            candidates.append(chunk)
    return candidates


def comparison_text_has_null(value: object) -> bool:
    normalized = normalize_comparison_text(value)
    return any(marker in normalized for marker in NULL_COMPARISON_MARKERS)


def explicit_evidence_strength_question(question: str) -> bool:
    """Recognize explicit requests to judge strength or robustness of evidence."""
    normalized = normalize_comparison_text(question)
    markers = (
        "how strong is the evidence",
        "how robust is the evidence",
        "strength of the evidence",
        "evidence strength",
        "robustness of the evidence",
        "wie stark ist die evidenz",
        "wie belastbar ist die evidenz",
        "starke der evidenz",
        "belastbarkeit der evidenz",
    )
    return any(marker in normalized for marker in markers)


def explicit_group_comparison_question(question: str) -> bool:
    """Require an explicit two-group comparison rather than a named-paper comparison."""
    if named_report_references(question) or comparison_report_descriptors(question):
        return False
    groups = requested_comparison_groups(question)
    if len(groups) != 2:
        return False
    normalized = normalize_comparison_text(question)
    return bool(
        re.search(
            r"\b(?:compare|comparison|distinguish|differ(?:s|ed|ence|ences)?|"
            r"between|versus|vs|than)\b",
            normalized,
        )
    )


def eligible_direct_comparison_candidate_chunks(
    question: str, evidence_pack: dict
) -> list[dict]:
    """Return clean, sentence-local results that directly compare both groups."""
    return [
        chunk
        for chunk in evidence_pack.get("chunks", [])
        if direct_comparison_result_sentence(question, chunk)
    ]


def direct_comparison_result_sentence(question: str, chunk: dict) -> str:
    """Return the first clean sentence that directly answers a group comparison."""
    if not explicit_group_comparison_question(question):
        return ""
    if not _eligible_direct_result_metadata(chunk):
        return ""
    groups = requested_comparison_groups(question)
    feature_terms = _comparison_feature_terms(question, groups)
    if not feature_terms:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+|[\r\n]+", str(chunk.get("text") or ""))
    for sentence in sentences:
        cleaned = " ".join(sentence.split())
        if _sentence_is_direct_group_result(cleaned, groups, feature_terms):
            return cleaned
    return ""


def _eligible_direct_result_metadata(chunk: dict) -> bool:
    if chunk.get("prompt_context_only"):
        return False
    section = normalize_comparison_text(chunk.get("section") or "unknown")
    if any(
        blocked in section
        for blocked in ("method", "reference", "table", "figure", "supplement")
    ):
        return False

    flags = {
        normalize_comparison_text(flag)
        for flag in (chunk.get("quality_flags") or [])
        if str(flag).strip()
    }


    if flags - {"figure like"}:
        return False

    assessment = chunk.get("source_type_assessment") or {}
    source_type = normalize_comparison_text(
        assessment.get("source_type") or assessment.get("label") or ""
    )
    return not any(
        marker in source_type
        for marker in ("review", "meta analysis", "commentary", "editorial", "protocol")
    )


def _sentence_is_direct_group_result(
    sentence: str,
    groups: list[tuple[str, tuple[str, ...]]],
    feature_terms: set[str],
) -> bool:
    normalized = normalize_comparison_text(sentence)
    if not normalized or re.search(r"\b(?:fig(?:ure)?|table|panel)\b", normalized):
        return False
    if not all(_sentence_contains_group(normalized, label, aliases) for label, aliases in groups):
        return False
    if feature_terms.isdisjoint(_comparison_tokens(normalized)):
        return False
    relation = re.search(
        r"\b(?:associat(?:e|ed|es|ion)|compar(?:e|ed|es|ison)|decreas\w*|"
        r"differ\w*|domin\w*|enrich\w*|fewer|focused|greater|higher|increas\w*|"
        r"less|lower|more|reduc\w*|similar|upregulat\w*|downregulat\w*)\b",
        normalized,
    )
    return comparison_text_has_null(normalized) or bool(relation)


def _sentence_contains_group(
    normalized: str, label: str, aliases: tuple[str, ...]
) -> bool:
    if contains_comparison_alias(normalized, aliases):
        return True



    if label == "clinical malaria":
        tokens = set(normalized.split())
        return "clinical" in tokens and bool(tokens.intersection({"infection", "infections"}))
    return False


def _comparison_feature_terms(
    question: str, groups: list[tuple[str, tuple[str, ...]]]
) -> set[str]:
    group_terms = {
        token
        for _, aliases in groups
        for alias in aliases
        for token in _comparison_tokens(alias)
    }
    stopwords = {
        "and",
        "are",
        "between",
        "change",
        "compare",
        "comparison",
        "difference",
        "distinguish",
        "dry",
        "evidence",
        "falciparum",
        "from",
        "how",
        "malaria",
        "plasmodium",
        "robust",
        "season",
        "strong",
        "than",
        "that",
        "the",
        "their",
        "these",
        "this",
        "versus",
        "what",
        "which",
        "with",
    }
    return _comparison_tokens(question) - group_terms - stopwords


def _comparison_tokens(value: object) -> set[str]:
    tokens = set(normalize_comparison_text(value).split())
    return {
        token[:-1] if len(token) > 4 and token.endswith("s") else token
        for token in tokens
        if len(token) >= 3
    }


def direct_comparison_candidate_groups(
    question: str, evidence_pack: dict, *, strict: bool = False
) -> list[dict]:
    """Collapse direct-comparison candidates by detected publication family."""
    candidates = (
        eligible_direct_comparison_candidate_chunks(question, evidence_pack)
        if strict
        else direct_comparison_candidate_chunks(question, evidence_pack)
    )
    if not candidates:
        return []

    paper_keys: set[str] = set()
    chunk_keys: list[tuple[dict, str]] = []
    for chunk in candidates:
        evidence_id = str(chunk.get("evidence_id") or "").strip()
        paper_id = str(chunk.get("paper_id") or "").strip()
        key = paper_id or f"evidence:{evidence_id}"
        paper_keys.add(key)
        chunk_keys.append((chunk, key))

    parent = {key: key for key in paper_keys}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    accepted_relations = {
        "same_doi_family",
        "same_preprint_family",
        "same_publication_version",
    }
    for signal in evidence_pack.get("automatic_lineage_signals", []):
        if str(signal.get("signal_type") or "") not in accepted_relations:
            continue
        left = str(signal.get("paper_id_a") or "").strip()
        right = str(signal.get("paper_id_b") or "").strip()
        if left in parent and right in parent:
            union(left, right)

    grouped: dict[str, dict[str, set[str]]] = {}
    grouped_result_sentences: dict[str, list[dict[str, str]]] = {}
    order: list[str] = []
    for chunk, key in chunk_keys:
        root = find(key)
        if root not in grouped:
            grouped[root] = {
                "evidence_ids": set(),
                "paper_ids": set(),
                "citation_labels": set(),
            }
            grouped_result_sentences[root] = []
            order.append(root)
        evidence_id = str(chunk.get("evidence_id") or "").strip()
        paper_id = str(chunk.get("paper_id") or "").strip()
        citation_label = str(chunk.get("citation_label") or "").strip()
        grouped[root]["evidence_ids"].add(evidence_id)
        if paper_id:
            grouped[root]["paper_ids"].add(paper_id)
        if citation_label:
            grouped[root]["citation_labels"].add(citation_label)
        if strict:
            sentence = direct_comparison_result_sentence(question, chunk)
            if sentence:
                result_sentences = grouped_result_sentences[root]
                item = {"evidence_id": evidence_id, "sentence": sentence}
                if item not in result_sentences:
                    result_sentences.append(item)

    return [
        {
            "evidence_ids": sorted(grouped[root]["evidence_ids"]),
            "paper_ids": sorted(grouped[root]["paper_ids"]),
            "citation_labels": sorted(grouped[root]["citation_labels"]),
            **(
                {"result_sentences": grouped_result_sentences[root]}
                if strict
                else {}
            ),
        }
        for root in order
    ]
