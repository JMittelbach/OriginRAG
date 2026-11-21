"""Short, attributable extracts from the actual retrieval window, not generated claims."""

import re

from originrag.analysis.evidence_pack_grading import question_relevance_terms
from originrag.retrieval.chunk_quality import REFERENCE_HEADING, reference_like_passage
from originrag.retrieval.keyword import normalize_text


def report_author(question):
    """Recognise a direct question about a single named report, not a literature comparison."""
    if re.search(r"\b(?:other|elsewhere|replicat\w*|across|compare|contrast|papers|studies|andere\w*|vergleich\w*|studien)\b", question, re.I):
        return None
    if not re.match(r"(?:did|does|was|were|which|what|how|hat|haben|wurde[n]?|welche\w*|wie)\b", question.strip(), re.I):
        return None
    names = re.findall(r"\b(?:paper|article|report|study|artikel|studie)\s+(?:by|von)\s+([^\W\d_]+(?:[-’'][^\W\d_]+)*)\s+et\s+al\b", question, re.I)
    years = re.findall(r"\b(?:19|20)\d{2}\b", question)
    if len(names) == 1 and len(years) <= 1 and len(re.findall(r"\bet\s+al\b", question, re.I)) == 1:
        return names[0], years[0] if years else ""
    return None


def reading_passages(question, chunks, limit=3):
    query = re.sub(r"(?:https?://(?:dx\.)?doi\.org/|\bdoi:?\s*)?10\.\d{4,9}/\S+", "", question, flags=re.I)
    terms = set(question_relevance_terms(query, include_short_identifiers=True))
    methods = bool(re.search(r"\b(?:methods?|measured|groups|participants|methode\w*|gemessen|gruppen)\b", query, re.I))
    candidates = []
    for chunk in chunks:
        if not chunk.get("paper_id") or not chunk.get("evidence_id"):
            continue
        if chunk.get("section") in {"references", "bibliography", "table"} or set(chunk.get("quality_flags") or ()) & {"reference_like", "table_like"}:
            continue
        body = REFERENCE_HEADING.split(chunk.get("text") or "", maxsplit=1)[0]
        if reference_like_passage(body):
            continue
        text = " ".join(body.split())
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9“])", text):
            if not 35 <= len(sentence) <= 580 or not sentence.endswith((".", "!", "?")):
                continue
            if any(mark in sentence for mark in ("[[", "[...]", "…")) or re.search(r"10\.\d{4,9}/|doi\.org/", sentence, re.I):
                continue
            matched = terms.intersection(normalize_text(sentence).split())
            if matched:
                section_bonus = int(methods and chunk.get("section") in {"abstract", "methods"})
                candidates.append((len(matched) + section_bonus, chunk, sentence))
    candidates.sort(key=lambda x: -x[0])  # retain retrieval order for equal matches
    hits, seen, chunks_seen = [], set(), set()
    for _, chunk, sentence in candidates:
        if sentence in seen or chunk["evidence_id"] in chunks_seen:
            continue
        hits.append((chunk, sentence))
        seen.add(sentence)
        chunks_seen.add(chunk["evidence_id"])
        if len(hits) == limit:
            break
    return hits


def reading_result(hits):
    if not hits:
        return "I couldn’t locate a directly matching passage in this search. Try a paper title, DOI or a more specific term."
    lines = ["I couldn’t verify a summary. These are passages from the papers, not a conclusion:"]
    for chunk, quote in hits:
        page = chunk.get("page_range")
        location = f", p. {page}" if page and page != "?" else ""
        lines.append(f"“{quote}” — {chunk.get('title') or chunk['paper_id']}{location} ({chunk['evidence_id']})")
    return "\n\n".join(lines)
