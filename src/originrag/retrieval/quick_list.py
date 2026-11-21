"""Recognise simple bibliography requests. More specific questions keep the normal retriever."""

from dataclasses import dataclass
import heapq
import re
import sqlite3
import unicodedata
from originrag.analysis.chat_commands import controls


_AUTHOR = re.compile(r"(?<![\w-])([^\W\d_]+(?:[-’'][^\W\d_]+)*)\s+et\s+al\b", re.I)


def named_catalogue_request(question):
    """An explicit presence check, or a potentially ambiguous author/year methods question."""
    text = " ".join(question.strip().rstrip("?! .").split())
    authors = _AUTHOR.findall(text)
    if len(authors) != 1:
        return None
    name = re.escape(authors[0]) + r"\s+et\s+al\.?"
    subject = rf"(?:{name} (?:paper|article|study)|(?:paper|article|study) by {name})(?: (?:on|about|from) [\w ’'–-]+)?"
    de_subject = rf"(?:(?:paper|artikel|studie) von {name}|{name} (?:paper|artikel|studie))(?: (?:zu|über|von) [\w ’'–-]+)?"
    presence = bool(re.fullmatch(
        rf"(?:is|do (?:you|we|i) have) (?:the )?{subject} in (?:my|our|the) (?:corpus|library)"
        rf"|(?:ist|haben wir|hast du) (?:das |der |die )?{de_subject} (?:im (?:korpus|bestand)|in (?:der|meiner|unserer) bibliothek)",
        text, re.I)) and not re.search(r"\b(?:and (?:tell|explain|summarize)|und (?:erklär\w*|fass\w*))\b", text, re.I)
    years = re.findall(r"\b[12][0-9]{3}\b", text)
    if len(years) > 1:
        return None
    methods = (years and re.search(r"\b(?:paper|article|study|studie|artikel)\b", text, re.I)
               and re.search(r"\b(?:methods?|methoden?|verfahren)\b", text, re.I)
               and not re.search(r"\b(?:other|others|andere\w*|across|literature|papers|studies|studien)\b", text, re.I))
    if not presence and not methods:
        return None
    return authors[0], years[0] if years else "", presence


@dataclass(frozen=True)
class PaperList:
    topic: str
    limit: int
    language: str
    newest: bool = False
    since: int = 0


def publication_year(row):
    year = str(row.get("year") or "").strip()
    return int(year) if re.fullmatch(r"[12][0-9]{3}", year) else 0


_COUNTS = dict(zip("one two three four five six seven eight nine ten".split(), range(1, 11)))
_COUNTS.update(zip("ein zwei drei vier fünf sechs sieben acht neun zehn".split(), range(1, 11)))
for numbers in ("un deux trois quatre cinq six sept huit neuf dix", "un dos tres cuatro cinco seis siete ocho nueve diez"):
    _COUNTS.update(zip(numbers.split(), range(1, 11)))
_COUNT = r"(?:(?P<count>[1-9]\d?|100|" + "|".join(_COUNTS) + r")\s+)?"
_MORE = {
    "en": r"(?:(?:(?:can|could) you )?(?:please )?(?:show|give|find)(?: me)? )?(?:the )?" + _COUNT + r"more(?: (?:papers|articles|studies))?",
    "de": r"(?:(?:zeig[e]?|gib|nenn[e]?)(?: mir)? )?(?:bitte )?(?:noch )?" + _COUNT + r"(?:weitere|mehr)(?: (?:papers?|artikel|studien))?",
    "fr": r"(?:(?:donne|montre)(?:-moi)? )?" + _COUNT + r"(?:autres|de plus)(?: articles)?",
    "es": r"(?:dame )?" + _COUNT + r"(?:artículos |estudios )?más",
}


def more_list_request(message):
    """Only a standalone continuation, never 'tell me more about …'."""
    text = message.casefold().strip(" \t\r\n.!?¿¡")
    text = re.sub(r"(?:,?\s+)(?:please|bitte|por favor|merci)$", "", text)
    for lang, pattern in _MORE.items():
        match = re.fullmatch(pattern, text)
        if match:
            count = match["count"]
            return lang, (int(count) if count and count.isdecimal() else _COUNTS.get(count))
    # Allow "another five" and "noch fünf" without interpreting an ordinal
    # like "the fifth" as a request for a new page.
    for lang, prefix, noun in (("en", r"(?:(?:show|give) me )?another", r"papers|articles|studies"),
                               ("de", r"(?:(?:zeig[e]?|gib) mir )?noch", r"papers?|artikel|studien")):
        match = re.fullmatch(prefix + r" (\w+)(?: (?:" + noun + r"))?", text)
        if match:
            word = match[1]
            count = int(word) if re.fullmatch(r"[1-9]\d?|100", word) else _COUNTS.get(word)
            if count:
                return lang, count
    return None
_REQUESTS = {
    "en": re.compile(r"(?:(?:hey[, ]+)?(?:(?:can|could|would) you )?(?:please )?(?:give|show|find|list|suggest|recommend)(?: me)? )?"
                     + r"(?:the )?" + _COUNT + r"(?:(?P<recent>latest|newest|most recent|recent|newer) )?(?:papers?|articles?|studies) "
                     r"(?:(?:in|from) (?:my|our|the) (?:library|corpus|catalogue|catalog) )?(?:on|about|covering) (?P<topic>.+)", re.I),
    "de": re.compile(r"(?:(?:hey[, ]+)?(?:kannst du )?(?:bitte )?(?:gib|gibst du|zeig[e]?|finde|liste|nenn[e]?)(?: mir)? (?:bitte )?)?"
                     + r"(?:die )?" + _COUNT + r"(?:(?P<recent>neuesten?|neusten?|neuere[n]?|aktuell(?:e|en|sten)) )?(?:papers?|artikel|studien) "
                     r"(?:(?:im Korpus|(?:in|aus) (?:meiner|unserer|der) Bibliothek|aus (?:dem|meinem|unserem) Korpus) )?(?:zu[mr]?|über|ueber) (?P<topic>.+)", re.I),
    "fr": re.compile(r"(?:(?:montre|donne|trouve)(?:-moi| moi)? )?" + _COUNT
                     + r"(?:articles?|études?|papers?) sur (?P<topic>.+)", re.I),
    "es": re.compile(r"(?:(?:dame|busca|muéstrame|muestra)(?:me)? )?" + _COUNT
                     + r"(?:artículos?|articulos?|estudios?|papers?) sobre (?P<topic>.+)", re.I),
}


def paper_list_request(message):
    text = " ".join(message.strip(" \r\n\t.?!").split())
    # Correct the command prefix only. The topic is an untouched slice of the message.
    divider = re.search(r"\s(?:on|about|covering|zu[mr]?|über|ueber|sur|sobre)\s", text, re.I)
    if divider and not any(pattern.fullmatch(text) for pattern in _REQUESTS.values()):
        text = controls(text[:divider.start()]) + text[divider.start():]
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
        if language == "en":
            topic = re.sub(r"\s+(?:in|from) (?:my|our) (?:corpus|library|catalogue|catalog)$", "", topic, flags=re.I)
        elif language == "de":
            topic = re.sub(r"\s+(?:in|aus) (?:meiner Bibliothek|unserer Bibliothek|meinem Korpus|unserem Korpus)$",
                           "", topic, flags=re.I)
        newest = bool(match.groupdict().get("recent"))
        since = 0
        date_pattern = {"en": r"\s+(?:published\s+)?(since|after)\s+([12][0-9]{3})$",
                        "de": r"\s+(seit|ab|nach)\s+([12][0-9]{3})$"}.get(language)
        if date_pattern and (date := re.search(date_pattern, topic, re.I)):
            since = int(date[2]) + int(date[1].casefold() in {"after", "nach"})
            topic = topic[:date.start()].strip()
            newest = True
        # A second instruction, filter, or relative clause needs interpretation.
        if (not re.fullmatch(r"[\w /+’'–-]{1,140}", topic)
                or len(topic.split()) > 12 or re.search(
                    r"\b(?:and then|then|tell|give|explain|compare|extract|summari[sz]e|report|list|what|why|how|"
                    r"that|which|using|published|since|before|after|without|excluding|only|not|except|or|"
                    r"assess|evaluate|identify|describe|interpret|include|their (?:main |key )?(?:findings|methods|results)|from \d{4}|"
                    r"sag|gib|erklär\w*|vergleich\w*|fass\w*|nenne|welche\w*|ohne|seit|vor|nach|nur|"
                    r"puis|ensuite|dis|indique\w*|qui|dont|résum\w*|explique\w*|compare\w*|sans|sauf|depuis|avant|après|"
                    r"luego|después|dime|indica\w*|que|resum\w*|explica\w*|compara\w*|sin|excepto|desde|antes|no|ni|pas)\b", topic, re.I)):
            return None
        count = (match["count"] or "10").casefold()
        return PaperList(topic, int(count) if count.isdecimal() else _COUNTS[count], language, newest, since)
    return None


def _words(text):
    folded = unicodedata.normalize("NFD", text.casefold())
    return re.findall(r"[^\W_]+", "".join(c for c in folded if not unicodedata.combining(c)))


def _body_match(text, title, terms):
    from originrag.retrieval.chunk_quality import REFERENCE_HEADING, reference_like_passage

    heading = REFERENCE_HEADING.search(text)
    if heading:
        # The last paragraph and the start of the bibliography can share a PDF chunk.
        body = text[:heading.start()].strip()
        return bool(body) and terms.issubset(_words(title + " " + body)) and not reference_like_passage(body)
    return not reference_like_passage(text)


def indexed_list_ids(request, index, chunks, papers, *, eligible, limit):
    """Return ranked distinct IDs; None means full text could not be searched."""
    from originrag.retrieval.fts_index import RetrievalIndexError, ensure_current_index
    from originrag.retrieval.index_cache import RetrievalIndexCacheError, resolve_read_bundle
    if limit <= 0:
        return []
    words = _words(request.topic)
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
                "SELECT paper_id, rowid FROM chunk_fts WHERE chunk_fts MATCH ? ORDER BY rank",
                (" AND ".join(f'"{w}"' for w in terms),),
            )
            selected = []
            selected_ids = set()
            newest_hits = []
            required = frozenset(terms)
            for rank, (paper_id, rowid) in enumerate(matches):
                if paper_id not in eligible or paper_id in selected_ids:
                    continue
                if request.newest:
                    year = publication_year(eligible[paper_id])
                    if year < max(1, request.since) or (len(newest_hits) == limit and year <= newest_hits[0][0]):
                        continue
                title, text = db.execute("SELECT title, text FROM chunk_fts WHERE rowid=?", (rowid,)).fetchone()
                if _body_match(text or "", title or "", required):
                    selected_ids.add(paper_id)
                    if request.newest:
                        heapq.heappush(newest_hits, (year, -rank, paper_id))
                        if len(newest_hits) > limit:
                            heapq.heappop(newest_hits)
                        continue
                    selected.append(paper_id)
                    if len(selected) == limit:
                        break
            return [pid for _, _, pid in sorted(newest_hits, reverse=True)] if request.newest else selected
        finally:
            db.close()
    except (OSError, sqlite3.Error, RetrievalIndexError, RetrievalIndexCacheError):
        return None
