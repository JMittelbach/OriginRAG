"""Recognise simple bibliography requests. More specific questions keep the normal retriever."""

from dataclasses import dataclass
from collections import Counter
import heapq
import math
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


def requested_paper_count(question):
    """An explicit list size, never a sample size or a year in a content question."""
    pattern = (r"\b(?:show|give|find|list|suggest|recommend|name|nenn[e]?|zeig[e]?|gib|finde|liste|"
               r"montre|donne|trouve|dame|muestra|busca)(?: (?:me|mir|moi))? "
               + _COUNT + r"(?:papers?|articles?|studies|artikel|studien|artículos?|estudios?)\b")
    matches = list(re.finditer(pattern, controls(question)))
    if len(matches) != 1 or not matches[0]["count"]:
        return None
    count = matches[0]["count"]
    return int(count) if count.isdecimal() else _COUNTS[count]


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
    text = controls(message)
    text = re.sub(r"(?:,?\s+)(?:please|bitte|por favor|merci)$", "", text)
    text = re.sub(r"\s+(?:from|in) (?:that|this|the) (?:same )?(?:web )?(?:search|list|results)$", "", text)
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
    if not divider:
        for candidate in re.finditer(r"(?<=\s)[^\W\d_]+(?=\s)", text[:160]):
            link = controls(candidate[0])
            head = controls(text[:candidate.start()])
            if link in {"about", "covering", "über", "ueber", "sobre"} and re.search(
                    r"\b(?:papers?|articles?|studies|artikel|studien|artículos?)$", head):
                text = head + " " + link + text[candidate.end():]
                break
    if divider and not any(pattern.fullmatch(text) for pattern in _REQUESTS.values()):
        prefix, tail = controls(text[:divider.start()]), text[divider.start():]
        modal = re.fullmatch(r"(?:kannst|könntest|magst|würdest) du (?:mir )?(?:bitte )?(.+)", prefix)
        ending = re.search(r"\s+(\S+)(?:\s+bitte)?$", tail)
        if modal and ending and controls(ending[1]) in {'zeigen', 'finden', 'geben', 'schicken'}:
            prefix, tail = "zeige mir " + modal[1], tail[:ending.start()]
        text = prefix + tail
    inventory = controls(text)
    if re.fullmatch(r"(?:which|what) (?:papers|articles) (?:can you see|do i have|are (?:there )?in (?:my|the) (?:library|corpus))"
                    r"(?: in (?:my|the) (?:library|corpus))?|(?:please )?(?:show|list)(?: me)? my papers", inventory, re.I):
        return PaperList("", 10, "en")
    if re.fullmatch(r"welche (?:papers?|artikel) (?:siehst du|habe ich|sind (?:im korpus|in der bibliothek))"
                    r"(?: (?:im korpus|in meiner bibliothek))?|(?:zeige?|liste) (?:mir )?meine papers?", inventory, re.I):
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


def topic_terms(topic):
    return list(dict.fromkeys(w for w in _words(topic) if w not in {
        "the", "of", "and", "in", "for", "on", "der", "die", "das", "und", "von", "zur", "zum", "fur"}))


def topic_spelling_candidate(topic, catalogue):
    """Suggest at most two unambiguous title-word corrections after an empty search."""
    from originrag.retrieval.paper_lookup import catalogue_words, near_title_word

    if not topic or len(topic) > 300:
        return None
    vocabulary = set().union(*(set(catalogue_words(str(p.get('title') or '')).split()) for p in catalogue))
    quoted = list(re.finditer(r'`[^`]*`|"[^"]*"|“[^”]*”|«[^»]*»', topic))
    candidates, corrections = [], []
    forms = query_word_forms(topic)
    for token in re.finditer(r'\S+', topic):
        word = token[0]
        if (not word.isalpha() or word.isupper() or not 5 <= len(word) <= 32
                or any(q.start() <= token.start() < q.end() for q in quoted)):
            continue
        typed = catalogue_words(word)
        if forms.get(typed, {typed}).intersection(vocabulary):
            continue
        alternatives = {w for w in vocabulary if near_title_word(typed, w)}
        if len(alternatives) != 1:
            continue
        replacement = alternatives.pop()
        candidates.append((token.start(), token.end(), replacement))
        corrections.append((word, replacement))
    if not 1 <= len(candidates) <= 2:
        return None
    corrected = topic
    for start, end, replacement in reversed(candidates):
        corrected = corrected[:start] + replacement + corrected[end:]
    return corrected, corrections


def query_word_forms(query):
    """Small English noun inflections, never fuzzy identifiers or acronym expansions."""
    protected = {word.casefold() for word in re.findall(r"[A-Z][A-Z0-9_./-]{2,}", query)}
    for token in query.split():
        if re.search(r"\d|[/_]", token):
            protected.update(_words(token))
    result = {}
    for term in _words(query):
        forms = {term}
        if (re.fullmatch(r"[a-z]{4,32}", term) and term not in protected
                and term not in {"species", "series", "news"}):
            if term.endswith("ies"):
                forms.add(term[:-3] + "y")
            elif term.endswith(("ches", "shes", "xes", "zes")):
                forms.add(term[:-2])
            elif term.endswith("s"):
                if not term.endswith(("ss", "us", "is", "ous")):
                    forms.add(term[:-1])
            elif term.endswith("y") and term[-2] not in "aeiou":
                forms.add(term[:-1] + "ies")
            elif term.endswith(("ch", "sh", "x", "z")):
                forms.add(term + "es")
            else:
                forms.add(term + "s")
        result[term] = frozenset(forms)
    return result


def _body_match(text, title, terms, forms=None):
    from originrag.retrieval.chunk_quality import REFERENCE_HEADING, reference_like_passage

    heading = REFERENCE_HEADING.search(text)
    if heading:
        # The last paragraph and the start of the bibliography can share a PDF chunk.
        body = text[:heading.start()].strip()
        words = set(_words(title + " " + body))
        matched = all(words.intersection((forms or {}).get(term, {term})) for term in terms)
        return bool(body) and matched and not reference_like_passage(body)
    return not reference_like_passage(text)


def indexed_list_ids(request, index, chunks, papers, *, eligible, limit):
    """Return ranked distinct IDs; None means full text could not be searched."""
    from originrag.retrieval.fts_index import RetrievalIndexError, ensure_current_index
    from originrag.retrieval.index_cache import RetrievalIndexCacheError, resolve_read_bundle
    if limit <= 0:
        return []
    terms = topic_terms(request.topic)
    forms = query_word_forms(request.topic)
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
                (" AND ".join("(" + " OR ".join(f'"{v}"' for v in sorted(forms[w])) + ")" for w in terms),),
            )
            selected = []
            selected_ids = set()
            newest_hits = []
            required = frozenset(terms)
            titles = list(eligible.values()) if hasattr(eligible, "values") else []
            frequency = Counter(w for row in titles for w in set(_words(str(row.get("title") or ""))) if w in required)
            weights = {w: math.log1p(len(titles) / (1 + frequency[w])) if titles else 1 for w in terms}
            for rank, (paper_id, rowid) in enumerate(matches):
                if not request.newest and rank >= 1000:
                    break
                if paper_id not in eligible or paper_id in selected_ids:
                    continue
                if request.newest:
                    year = publication_year(eligible[paper_id])
                    if year < max(1, request.since) or (len(newest_hits) == limit and year <= newest_hits[0][0]):
                        continue
                title, text = db.execute("SELECT title, text FROM chunk_fts WHERE rowid=?", (rowid,)).fetchone()
                if _body_match(text or "", title or "", required, forms):
                    selected_ids.add(paper_id)
                    if request.newest:
                        heapq.heappush(newest_hits, (year, -rank, paper_id))
                        if len(newest_hits) > limit:
                            heapq.heappop(newest_hits)
                        continue
                    title_terms = set(_words(title or ""))
                    specificity = sum(weights[w] for w in terms if title_terms.intersection(forms[w]))
                    selected.append((-specificity, rank, paper_id))
            return ([pid for _, _, pid in sorted(newest_hits, reverse=True)] if request.newest else
                    [pid for _, _, pid in sorted(selected)[:limit]])
        finally:
            db.close()
    except (OSError, sqlite3.Error, RetrievalIndexError, RetrievalIndexCacheError):
        return None
