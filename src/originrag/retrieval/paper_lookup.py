"""A request for one citation, constrained by title terms and publication venue."""

from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
import re
import unicodedata

from originrag.analysis.chat_commands import controls, without_conversational_decline

@lru_cache(maxsize=16000)
def catalogue_words(value):
    folded = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(re.findall(r"[^\W_]+", "".join(c for c in folded if not unicodedata.combining(c))))


def normalize_recency(words):
    return re.sub(r"\b(re\w+) published\b",
                  lambda m: "recent" if SequenceMatcher(None, m[1], "recently").ratio() >= .8 else m[0], words)


_RECENT = re.compile(r"\b(?:latest|most recent|recent|newest|new|neu(?:e|en|es|e?ste[nrs]?)|(?:kurzlich|zuletzt) (?:erschienene[nrs]?|veroffentlichte[nrs]?))\b")


@dataclass(frozen=True)
class CitationLookup:
    topic: tuple[str, ...]
    venue: str
    newest: bool
    year: str


def excludes_venue(text):
    return bool(re.search(r"\b(?:(?:not|nicht)\s+(?:(?:published|erschienen)\s+)?(?:in|from|aus|im)|"
                          r"(?:except|excluding|außer|ausser)\s+(?:papers?\s+(?:in|from)\s+)?\w+)\b", text, re.I))


def lookup_instructions(text):
    """Repair a leading request wrapper, stopping before the actual title clues."""
    permitted = set('can could would you kannst konntest könntest magst wurdest würdest du mir me '
                    'please bitte einmal mal show find give zeig zeige finde gib schick schicke '
                    'the one das den die dem der ein eine einen eines only just nur '
                    'latest newest recent recently published new neuste neueste neuesten neusten '
                    'paper article study artikel studie on about for fur zu uber'.split())
    repaired = []
    for match in re.finditer(r'\S+', text):
        candidate = controls(match[0])
        if not candidate or not set(candidate.split()) <= permitted:
            return ' '.join([*repaired, text[match.start():]])
        repaired.append(candidate)
    return ' '.join(repaired)


def citation_lookup(text):
    # Only a standalone request to locate a paper, not to explain its contents.
    text = without_conversational_decline(text)
    if re.search(r"[?!.;]\s+\w|10\.\d{4,9}/|\bet al\b|\b(?:and|und)\s+(?:then|tell|explain|summari[sz]e|describe|sag|erklär\w*|fass\w*)\b", text, re.I):
        return None
    words = catalogue_words(lookup_instructions(text))
    words = re.sub(r'\b(\w+) (?=(?:in|from|aus|im)\b)',
                   lambda m: controls(m[1]) + ' ' if controls(m[1]) in
                   {'paper', 'article', 'study', 'artikel', 'studie'} else m[0], words)
    if re.search(r'\b(?:this|that|dieses|diesem|dieser) (?:paper|article|study|artikel|studie)\b', words):
        return None
    if re.search(r'\b(?:erklaren|zusammenfassen|besprechen|durchlesen|explain|summari[sz]e|discuss)$', words):
        return None
    words = re.sub(r' (?:please|bitte|geben|zeigen|finden|schicken)$', '', words)
    if re.search(r"\b(?:no|not|without|excluding|except|nicht|ohne|kein\w*|au(?:ss|ß)er)\b", words):
        return None
    command = re.fullmatch(
        r"(?:(?:(?:can|could|would) you |kannst du )?(?:please |bitte )?"
        r"(?:show|find|give|zeige?|finde?|gib)(?: me| mir)? (?:bitte )?|"
        r"(?:i mean(?:t)?|ich meinte?) |(?=(?:only|onyl|just|nur|the one|das eine) ))"
        r"(?:(?:only|onyl|just|nur) )?(?:the|das|den|die) (?:(?:one|eine[nr]?) )?"
        r"(.{1,160}) (?:in|from|aus|im) (.{1,95})", words)
    if not command:
        return conversational_lookup(words)
    subject, venue = command.groups()
    if re.search(r"\b(?:library|catalogue|corpus|bibliothek|korpus|which|where|that|please|bitte|"
                 r"sentences?|words|english|german|deutsch|englisch|satzen|worte[nr]?)\b", venue):
        return None
    subject = normalize_recency(subject)
    time = _RECENT.match(subject)
    subject = subject[time.end():].lstrip() if time else subject
    noun = r"(?:paper|article|study|artikel|studie)"
    m = re.fullmatch(rf"(.+) {noun}|{noun} (?:on|about|for|fur|fpur|zu|uber) (.+)", subject)
    if not m:
        return conversational_lookup(words)
    topic = (m[1] or m[2]).split()
    years = [word for word in topic if re.fullmatch(r"(?:19|20)\d\d", word)]
    terms = tuple(w for w in topic if w not in years and w not in {"on", "of", "the", "and", "und"})
    if not terms or len(years) > 1 or any(w in topic for w in ("by", "von", "papers", "articles", "studies")):
        return None
    return CitationLookup(terms, venue, bool(time), next(iter(years), ""))


def conversational_lookup(words):
    """Locate one citation from a request wrapper, title clues and a venue."""
    match = re.fullmatch(r'(.{1,220}) (?:in|from|aus|im) (.{1,95})', words)
    if not match:
        return None
    subject, venue = match.groups()
    if re.search(r'\b(?:library|corpus|bibliothek|korpus|please|bitte|and|und|'
                 r'sentences?|words|english|german|deutsch|englisch|satzen|worte[nr]?)\b', venue):
        return None
    if not re.match(r'(?:(?:can|could|would) you|(?:kannst|konntest|magst|wurdest) du|'
                    r'please|bitte|show|find|give|zeig\w*|finde\w*|gib|schick\w*)\b', subject):
        return None
    if re.search(r'\b(?:explain|summari[sz]e|describe|why|how|erkl\w*|fass\w*|beschreib\w*|'
                 r'discuss|read|talk|besprech\w*|unterhalt\w*|lesen|durchles\w*|'
                 r'warum|wieso|wie|what|was|compare|vergleich\w*|by|von|papers|articles|studies|studien)\b', subject):
        return None
    if len(re.findall(r'\b(?:paper|article|study|artikel|studie)\b', subject)) != 1:
        return None
    newest = mentions_recency(subject)
    subject = _RECENT.sub('', normalize_recency(subject))
    ignored = set('can could would you kannst konntest magst wurdest du mir me please bitte einmal mal '
                  'show find give zeig zeige finde gib schick schicke geben zeigen finden schicken '
                  'the one das den die dem der ein eine einen eines paper article study artikel studie '
                  'about on for fur zu uber of in and und that with mit zum zur'.split())
    terms = [w for w in subject.split() if w not in ignored and not any(
        near_title_word(w, filler) for filler in ('please', 'einmal'))]
    years = [w for w in terms if re.fullmatch(r'(?:19|20)\d\d', w)]
    terms = tuple(w for w in terms if w not in years)
    if not terms or len(terms) > 12 or len(years) > 1 or any(w.isdigit() for w in terms):
        return None
    return CitationLookup(terms, venue, newest, next(iter(years), ''))


@lru_cache(maxsize=8192)
def near_title_word(typed, actual):
    """A single edit may locate a title; scientific identifiers keep their digits."""
    if min(len(typed), len(actual)) < 5 or abs(len(typed) - len(actual)) > 1:
        return False
    if re.findall(r'\d+', typed) != re.findall(r'\d+', actual):
        return False
    if any(c.isdigit() for c in typed) and [(i, c) for i, c in enumerate(typed) if c.isdigit()] != [(i, c) for i, c in enumerate(actual) if c.isdigit()]:
        return False
    i = next((k for k, (a, b) in enumerate(zip(typed, actual)) if a != b), min(len(typed), len(actual)))
    a, b = typed[i:], actual[i:]
    return (a[1:] == b or a == b[1:] or a[1:] == b[1:] or
            (len(a) >= 2 and a[1] + a[0] + a[2:] == b))


@lru_cache(maxsize=4096)
def venue_matches(query, journal):
    a, b = query.split(), catalogue_words(journal).split()
    if len(a) != len(b):
        if len(a) < len(b) and len(''.join(a)) >= 8:
            typed, actual = ''.join(a), ''.join(b)
            if typed == actual or near_title_word(typed, actual):
                return True
            if len(''.join(b[:-1])) + 4 <= len(typed) < len(actual):
                prefix = actual[:len(typed)]
                return typed == prefix or near_title_word(typed, prefix)
        return False
    for i, (typed, actual) in enumerate(zip(a, b)):
        if typed == actual or (i == len(a)-1 and len(typed) >= 4 and actual.startswith(typed)):
            continue
        if min(len(typed), len(actual)) < 5:
            return False
        # Permit one insertion, deletion, substitution or neighbouring transposition.
        j = next((k for k, (x, y) in enumerate(zip(typed, actual)) if x != y), min(len(typed), len(actual)))
        x, y = typed[j:], actual[j:]
        if not (x[1:] == y or x == y[1:] or x[1:] == y[1:] or
                (len(x) >= 2 and x[1] + x[0] + x[2:] == y)):
            return False
    return bool(b)


def citation_candidates(request, catalogue):
    terms = set(request.topic)
    indexed = [(p, set(catalogue_words(p.get('title') or '').split())) for p in catalogue]
    vocabulary = set().union(*(words for _, words in indexed))
    rows = []
    for paper, words in indexed:
        if ((request.year and str(paper.get('year')) != request.year) or
                not venue_matches(request.venue, paper.get('journal') or '')):
            continue
        corrections = []
        for term in terms - words:
            # A known term is a different subject, not a typo of another title.
            alternatives = {w for w in words if near_title_word(term, w)} if term not in vocabulary else set()
            if len(alternatives) != 1:
                break
            corrections.append((term, alternatives.pop()))
        else:
            if len(corrections) <= 2:
                rows.append({**paper, 'lookup_corrections': sorted(corrections)} if corrections else paper)
    return newest_if_known(rows) if request.newest else rows


def mentions_recency(words):
    return bool(_RECENT.search(normalize_recency(words)))


def newest_if_known(rows):
    years = [str(p.get("year") or "") for p in rows]
    if len(rows) > 1 and all(re.fullmatch(r"(?:19|20)\d\d", y) for y in years):
        newest = max(years)
        rows = [p for p, year in zip(rows, years) if year == newest]
    return rows
