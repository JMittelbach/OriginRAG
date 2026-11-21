"""Explicit report identity shared by the chat router and passage retriever."""

import re

from originrag.retrieval.keyword import normalize_text


def report_question_body(question: str, doi: str) -> str | None:
    """Recognise a cited-paper question, not a bare link or a request for related work."""
    prefix = (r"^\s*(?:in\s+(?:(?:the|this)\s+(?:paper|study|article|report)\s+(?:with\s+)?)?|"
              r"according\s+to\s+(?:(?:the|this)\s+(?:paper|study|article|report)\s+(?:with\s+)?)?|"
              r"(?:in\s+(?:dem|der|dieser)|im)\s+(?:paper|artikel|bericht|studie)\s+(?:mit\s+)?|laut\s+)"
              r"(?:doi\s*:?\s*)?(?:https?://(?:dx\.)?doi\.org/)?")
    match = re.match(prefix + re.escape(doi) + r"\s*[,;:\u2013\u2014-]\s*(.+)$", question, re.I | re.S)
    if match and re.match(r"(?:who|which|what|where|when|how|did|were|was|wo|wann|wer|wie|welche\w*|wurde\w*)\b",
                          match[1], re.I):
        return match[1]
    return None


def extract_explicit_dois(text: str) -> set[str]:
    matches = re.findall(r"10\.\d{4,9}/[^\s<>\]\[{}]+", text or "", flags=re.IGNORECASE)
    return {normalize_doi_text(match) for match in matches if normalize_doi_text(match)}


def exclusive_single_report_doi(question: str) -> str | None:
    """Recognize explicit single-report instructions, never infer them from a DOI alone."""
    requested = extract_explicit_dois(question)
    if len(requested) != 1:
        return None
    normalized = normalize_text(question.casefold())
    if re.search(
        r"\b(?:compare|contrast|versus|vs|vergleiche|vergleichen|vergleich|gegenuber)\b|"
        r"\b(?:not|nicht) (?:only|exclusively|solely|nur|ausschliesslich)\b|"
        r"\b(?:not|dont|nicht) (?:limit|restrict|confine|beschrank\w*)\b|"
        r"\b(?:other|additional|related|independent|weitere|anderen|andere|zusatzliche) "
        r"(?:papers?|reports?|studies|articles?|literature|studien|artikel|berichte)\b",
        normalized,
    ):
        return None
    explicit_only = re.search(
        r"\b(?:describe|discuss|explain|analyse|analyze|use|review|consider) "
        r"(?:this|that|the|one) (?:single )?(?:paper|report|study|article) only\b|"
        r"\b(?:describe|discuss|explain|analyse|analyze|use|review|consider) "
        r"(?:only|exclusively|solely) (?:this|that|the|one) (?:single )?"
        r"(?:paper|report|study|article)\b|"
        r"\b(?:beschreibe|analysiere|verwende|nutze|erlautere|bitte) "
        r"(?:nur|ausschliesslich) (?:dieses|diesen|diese|das|den|die) "
        r"(?:(?:einzelne[nmsr]?|eine[nmsr]?) )?(?:paper|bericht|artikel|studie)\b",
        normalized,
    )
    doi = next(iter(requested))
    if explicit_only:
        return doi
    if re.search(r"\b(?:elsewhere|literature|papers|studies|laboratories|subsequent|other|"
                 r"andere\w*|literatur|studien)\b", normalized):
        return None
    return doi if report_question_body(question, doi) is not None else None


def normalize_doi_text(value: str) -> str:
    clean = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value.strip(), flags=re.IGNORECASE)
    return clean.rstrip(".,;:!?)]}\"").lower()
