"""Recognise simple bibliography requests. More specific questions keep the normal retriever."""

from dataclasses import dataclass
import re
import sqlite3



@dataclass(frozen=True)
class PaperList:
    topic: str
    limit: int
    language: str


_COUNTS = dict(zip("one two three four five six seven eight nine ten".split(), range(1, 11)))
_COUNTS.update(zip("ein zwei drei vier fünf sechs sieben acht neun zehn".split(), range(1, 11)))
_COUNT = r"(?:(?P<count>[1-9]\d?|100|" + "|".join(_COUNTS) + r")\s+)?"
_REQUESTS = {
    "en": re.compile(r"(?:(?:hey[, ]+)?(?:(?:can|could|would) you )?(?:please )?(?:give|show|find|list|suggest|recommend)(?: me)? )?"
                     + _COUNT + r"(?:papers?|articles?|studies) (?:on|about|covering) (?P<topic>.+)", re.I),
    "de": re.compile(r"(?:(?:hey[, ]+)?(?:kannst du )?(?:bitte )?(?:gib|gibst du|zeig[e]?|finde|liste|nenn[e]?)(?: mir)? (?:bitte )?)?"
                     + _COUNT + r"(?:papers?|artikel|studien) (?:zu[mr]?|über|ueber) (?P<topic>.+)", re.I),
}


def paper_list_request(message):
    text = " ".join(message.strip(" \r\n\t.?!").split())
    if re.fullmatch(r"(?:which|what) (?:papers|articles) (?:can you see|do i have|are (?:there )?in (?:my|the) (?:library|corpus))"
                    r"(?: in (?:my|the) (?:library|corpus))?|(?:please )?(?:show|list)(?: me)? my papers", text, re.I):
        return PaperList("", 10, "en")
    if re.fullmatch(r"welche (?:papers?|artikel) (?:siehst du|habe ich|sind (?:im korpus|in der bibliothek))"
                    r"(?: (?:im korpus|in meiner bibliothek))?|(?:zeige?|liste) (?:mir )?meine papers?", text, re.I):
        return PaperList("", 10, "de")
    for language, pattern in _REQUESTS.items():
        match = pattern.fullmatch(text)
        if not match:
            continue
        topic = match["topic"]
        # A second instruction, filter, or relative clause needs interpretation.
        if (not re.fullmatch(r"[\w /+’'–-]{1,140}", topic)
                or len(topic.split()) > 12 or re.search(
                    r"\b(?:and then|then|tell|give|explain|compare|extract|summari[sz]e|report|list|what|why|how|"
                    r"that|which|using|published|since|before|after|without|excluding|only|not|except|or|"
                    r"assess|evaluate|identify|describe|interpret|include|their (?:main |key )?(?:findings|methods|results)|from \d{4}|"
                    r"sag|gib|erklär\w*|vergleich\w*|fass\w*|nenne|welche\w*|ohne|seit|vor|nach|nur)\b", topic, re.I)):
            return None
        count = (match["count"] or "10").casefold()
        return PaperList(topic, int(count) if count.isdecimal() else _COUNTS[count], language)
    return None


def indexed_list_ids(request, index, chunks, papers):
    """Return ranked distinct IDs; None means full text could not be searched."""
    from originrag.retrieval.fts_index import RetrievalIndexError, ensure_current_index
    from originrag.retrieval.index_cache import RetrievalIndexCacheError, resolve_read_bundle
    words = re.findall(r"[^\W_]+", request.topic.casefold())
    terms = list(dict.fromkeys(w for w in words if w not in {"the", "of", "and", "in", "der", "die", "das", "und", "von"}))
    if not terms or not index.is_file():
        return None
    try:
        bundle = resolve_read_bundle(index, chunks, papers)
        if bundle.corpus_content_digest is None:
            ensure_current_index(index, chunks, papers)
        db = sqlite3.connect(bundle.index_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            matches = db.execute(
                "SELECT DISTINCT paper_id FROM chunk_fts WHERE chunk_fts MATCH ? ORDER BY rank LIMIT ?",
                (" AND ".join(f'"{w}"' for w in terms), max(200, request.limit * 20)),
            )
            return list(dict.fromkeys(row[0] for row in matches))
        finally:
            db.close()
    except (OSError, sqlite3.Error, RetrievalIndexError, RetrievalIndexCacheError):
        return None
