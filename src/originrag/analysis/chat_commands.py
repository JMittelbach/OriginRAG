"""Parsing helpers for conversational controls, never for paper contents or search terms."""

from dataclasses import dataclass
from functools import lru_cache
import re


_DECLINE_PREFIX = re.compile(
    r"^\s*(?:no(?:[, ]+(?:thanks|thank you))?|(?:nein|nee)(?:[, ]+danke)?)\b[\s,;:.!?–—-]+"
    r"(?:(?:but|ubt|aber)\b[\s,]+)?(?=(?:i (?:only )?mean(?:t)?|ich meinte?|only|onyl|just|nur|"
    r"please|bitte|show|find|give|zeig\w*|welche\w*|which|what)\b)", re.I)


def without_conversational_decline(text):
    """Drop a polite rejection of the previous reply, not an exclusion in the request."""
    return _DECLINE_PREFIX.sub("", text, count=1)


def acknowledgement(text):
    """Handle standalone courtesies only; never consume a request after them."""
    text = text.casefold().strip(" \t\n.!?,:;😊🙂👍")
    if re.fullmatch(r"(?:ok(?:ay)?[, ]+)?(?:thanks(?: a lot)?|thank you(?: very much)?)", text):
        return "You're welcome!"
    if re.fullmatch(r"(?:ok(?:ay)?[, ]+)?(?:danke(?: schön| schoen| dir)?|vielen dank)", text):
        return "Gern!"
    if text in {"merci", "merci beaucoup"}:
        return "Avec plaisir !"
    if text in {"gracias", "muchas gracias"}:
        return "¡De nada!"
    return None


def starts_new_topic(text):
    return bool(re.match(
        r"^\s*(?:(?:ok(?:ay)?|now|jetzt)[, :]+)?(?:new (?:topic|question)|different topic|unrelated question|"
        r"switch(?:ing)? (?:the )?topic|(?:neues|anderes) thema|(?:neue|andere|unabhängige) frage|"
        r"nouveau sujet|otra pregunta|nuevo tema)\s*[:.!?–—-]", text, re.I))


_CONTROLS = frozenset("""please could would give show find list liste papers paper article articles study studies library online internet search scrape webscrape webscraping
summarize summarise first second third zeige finde bitte artikel studien paper fass fasse zusammen
kannst bibliothek siehst zeig zeige nenn nenne mir meine mein diese dieses denen montre trouve donne articles études résume résumer dame muestra busca
artículo artículos estudios resume resumir
discuss explain describe summarize summarise summary briefly compare comparison
latest newest recent recently published oldest earliest another weitere mehr
corpus catalogue catalog preprint preprints publications
lesen durchlesen besprechen unterhalten zusammenfassen zusammenfassung erklären erkläre
neuste neueste neuesten neusten aktuelle aktuellsten einmal könntest würdest
suche suchen finden zeigen geben schicke schicken lies
vierte vierten fünfte fünften sechste sechsten siebte siebten achte achten neunte neunten zehnte zehnten
erste ersten zweite zweiten dritte dritten fourth fifth sixth seventh eighth ninth tenth
about covering über ueber sobre""".split())

_UNCHANGED = frozenset("""not no never neither nor without except excluding don't dont cannot can't
nicht nichts nie kein keine keinen keiner keinem keines ohne außer ausser weder noch
sans jamais sin pas ni none one two three four five six seven eight nine ten
ein eine zwei drei vier fünf sechs sieben acht neun zehn can will should shall
where which what why when how was wie wer wo wann warum who whose with other
more than then these those them their there here from have has had read time some same
findings results subjects abstract figure table methods method model models data
severe severity disease gene genes protein proteins cohort cohorts review reviews""".split())
_UNCHANGED |= frozenset('eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen '
                       'twenty thirty forty fifty sixty seventy eighty ninety hundred thousand '
                       'elf zwölf dreizehn vierzehn fünfzehn sechzehn siebzehn achtzehn neunzehn '
                       'zwanzig dreißig vierzig fünfzig sechzig siebzig achtzig neunzig hundert tausend'.split())

_LITERAL = r'https?://\S+|10\.\d{4,9}/\S+|`[^`]*`|"[^"]*"|“[^”]*”|«[^»]*»'
_TOKEN = re.compile(_LITERAL + r'|[\w]+(?:[-./’\'][\w]+)*', re.I)
_SPLITTABLE = frozenset('summarize summarise summarizing summarising discuss explain zusammenfassen zusammenfassung durchlesen besprechen unterhalten'.split())
_NEGATIONS = frozenset('nicht nichts keine keinen keiner keinem keines ohne without never neither except excluding dont'.split())

_JOINED_CONTROLS = {
    a + b: a + " " + b for a, endings in (
        ("can", ("you",)), ("could", ("you",)), ("would", ("you",)),
        ("kannst", ("du",)), ("könntest", ("du",)), ("magst", ("du",)),
        ("show", ("me",)), ("give", ("me",)), ("find", ("me",)),
        ("zeig", ("mir",)), ("zeige", ("mir",)), ("gib", ("mir",)),
        ("bitte", ("zeige", "zeig", "finde",)), ("please", ("show", "find", "give",)),
    ) for b in endings
}

for _prefix in ('kannst du', 'könntest du', 'magst du', 'kannst du mir', 'könntest du mir',
                'can you', 'could you', 'would you', 'show me', 'give me'):
    for _suffix in ('', ' bitte' if 'du' in _prefix.split() else ' please'):
        _phrase = _prefix + _suffix
        _JOINED_CONTROLS[_phrase.replace(' ', '')] = _phrase
for _owner in ('my', 'our', 'the'):
    for _place in ('library', 'corpus', 'catalogue'):
        _JOINED_CONTROLS[_owner + _place] = _owner + ' ' + _place


def _near(s, word):
    if len(s) == len(word):
        diff = [i for i, (a, b) in enumerate(zip(s, word)) if a != b]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and s[diff[0]:diff[1]+1] == word[diff[0]:diff[1]+1][::-1])
    short, long = sorted((s, word), key=len)
    return len(long) == len(short)+1 and any(short == long[:i]+long[i+1:] for i in range(len(long)))


def _two_edits(a, b):
    """Bounded edit distance, including adjacent transpositions, for control words only."""
    if abs(len(a) - len(b)) > 2:
        return False
    previous = list(range(len(b) + 1))
    older = None
    for i, x in enumerate(a, 1):
        row = [i] + [3] * len(b)
        for j in range(max(1, i - 2), min(len(b), i + 2) + 1):
            row[j] = min(row[j-1] + 1, previous[j] + 1, previous[j-1] + (x != b[j-1]))
            if older is not None and j > 1 and x == b[j-2] and a[i-2] == b[j-1]:
                row[j] = min(row[j], older[j-2] + 1)
        if min(row) > 2:
            return False
        older, previous = previous, row
    return previous[-1] <= 2


@lru_cache(maxsize=1)
def _control_vocabulary():
    words = {word: word for word in _CONTROLS}
    words.update(_JOINED_CONTROLS)
    for group in _ORDINALS:
        names = group.split()
        for ordinals, articles, nouns in ((names[:1], ('the',), ('paper', 'article')),
                                         (names[1:3], ('das', 'dem', 'den'), ('paper', 'artikel'))):
            for ordinal in ordinals:
                for noun in nouns:
                    words[ordinal + noun] = ordinal + ' ' + noun
                    for article in articles:
                        phrase = f'{article} {ordinal} {noun}'
                        words[phrase.replace(' ', '')] = phrase
    return words


def _join_fragments(text):
    tokens = list(_TOKEN.finditer(text))
    result = []
    end = 0
    i = 0
    while i < len(tokens):
        for size in (3, 2):
            parts = tokens[i:i+size]
            if len(parts) != size or not all(p[0].isalpha() for p in parts):
                continue
            if not all(text[a.end():b.start()] == ' ' for a, b in zip(parts, parts[1:])):
                continue
            joined = ''.join(p[0] for p in parts).casefold()
            if joined in _SPLITTABLE and any(len(p[0]) <= 3 for p in parts):
                result.extend((text[end:parts[0].start()], joined))
                end = parts[-1].end()
                i += size
                break
        else:
            i += 1
    return ''.join(result) + text[end:]


def spelling_only(original, corrected):
    """A conservative fallback: same word order, only single edits in longer words."""
    pattern = r"\w+(?:[./’'-]\w+)*|[^\w\s.,;:!?]"
    before = re.findall(pattern, original.casefold())
    after = re.findall(pattern, corrected.casefold())
    return bool(before) and len(before) == len(after) and all(
        a == b or (a not in _UNCHANGED and min(len(a), len(b)) >= 4
                   and a.isalpha() and b.isalpha() and _near(a, b))
        for a, b in zip(before, after))


@lru_cache(maxsize=512)
def _repair(word):
    negations = [w for w in _NEGATIONS if len(w) == len(word) and sorted(w) == sorted(word) and _near(word, w)]
    if len(negations) == 1:
        return negations[0]
    vocabulary = _control_vocabulary()
    if word in vocabulary:
        return vocabulary[word]
    if not 4 <= len(word) <= 32 or word in _UNCHANGED:
        return word
    choices = {vocabulary[w] for w in vocabulary if _near(word, w)}
    if not choices and len(word) >= 8:
        choices = {vocabulary[w] for w in vocabulary if len(w) >= 8 and _two_edits(word, w)}
    return next(iter(choices)) if len(choices) == 1 else word


def controls(text):
    """Only consume the result on a full grammar match; do not send it to retrieval."""
    text = _join_fragments(" ".join(text.split()).strip(" ¿¡!?.,"))
    def repair_token(match):
        word = match[0]
        numbered = re.fullmatch(r'([^\W\d_]{5,12})([1-9]\d{0,2})', word)
        if numbered and not word.isupper() and _repair(numbered[1].casefold()) in {'paper', 'article', 'artikel'}:
            return _repair(numbered[1].casefold()) + ' ' + numbered[2]
        if word.casefold() in {"pls", "plz"}:
            return "please"
        if not word.isalpha() or (word.isupper() and word.casefold() not in _CONTROLS):
            return word.casefold()
        return _repair(word.casefold())
    text = _TOKEN.sub(repair_token, text).casefold()
    text = re.sub(r"^(?:hey|ey|hi|okay|ok|salut|hola)[,!:]?\s+", "", text)
    text = re.sub(_LITERAL + r"|\b(can|could|would) u\b",
                  lambda m: m[1] + " you" if m[1] else m[0], text)
    text = re.sub(r"^gimme\b", "give me", text)
    text = re.sub(r",\s*(?:please|bitte|por favor|s’il te plaît|merci)$", "", text)
    return text


_ORDINALS = (
    "first erste ersten premier première primero primer",
    "second zweite zweiten deuxième segundo",
    "third dritte dritten troisième tercero tercer",
    "fourth vierte vierten quatrième cuarto",
    "fifth fünfte fünften cinquième quinto",
    "sixth sechste sechsten sixième sexto",
    "seventh siebte siebten septième séptimo",
    "eighth achte achten huitième octavo",
    "ninth neunte neunten neuvième noveno",
    "tenth zehnte zehnten dixième décimo",
)
_POSITION = {word: i for i, words in enumerate(_ORDINALS, 1) for word in words.split()}
_POS = r"(?:#?\d{1,3}(?:st|nd|rd|th|\.)?|" + "|".join(_POSITION) + ")"
_ITEM = rf"(?:(?:the|das|den|die|le|la|el) )?(?:(?:paper|article|artikel|artículo|number|nummer|numéro|número) )?({_POS})(?: (?:one|paper|article|artikel|artículo))?"


@dataclass(frozen=True)
class ReadingSelection:
    number: int
    language: str
    correction: bool = False


def reading_selection(message):
    text = controls(message)
    for lang, prefix, suffix in (
        ("en", r"(?:(?:can|could|would) you )?(?:please )?summari[sz]e ", r"(?: (?:briefly|in brief|in (?:two|three|four|five|[1-5]) (?:short |brief )?sentences))?"),
        ("de", r"(?:(?:kannst|könntest|magst) du )?(?:mir )?(?:bitte )?(?:fass[e]? (?:mir )?(?:bitte )?)?", r"(?: kurz)? zusammen(?:fassen)?(?: bitte)?"),
        ("fr", r"(?:(?:peux-tu|pouvez-vous) )?résum(?:e|er) ", ""),
        ("es", r"(?:(?:puedes|podrías) )?resum(?:e|ir) ", ""),
    ):
        match = re.fullmatch(prefix + _ITEM + suffix, text)
        if match:
            break
    else:
        for lang, prefix in (("en", r"(?:no|actually|i meant)"), ("de", r"(?:nee|nein|ich meinte|doch lieber)"),
                             ("fr", r"(?:non|plutôt)"), ("es", r"(?:no quería decir|mejor)")):
            match = re.fullmatch(prefix + r"[, :]\s*" + _ITEM, text)
            if match:
                break
        else:
            return None
        number = match[1]
        return ReadingSelection(_number(number), lang, True)
    return ReadingSelection(_number(match[1]), lang)


def _number(text):
    digits = re.search(r"\d+", text)
    return int(digits[0]) if digits else _POSITION[text]


def list_recency(message):
    """A bounded selection from the displayed list, not a new subject search."""
    text = controls(without_conversational_decline(message))
    match = re.fullmatch(
        r"(?:(?:i (?:only )?meant|show(?: me)?|give me|just|only|please) )*"
        r"(?:the )?(newest|latest|most recent|oldest|earliest) (?:one|paper|article|study)"
        r"(?: (?:from|in|on|of) (?:that|this|the|your) list| of (?:these|those))?"
        r"|(?:(?:ich meinte|zeig(?:e)?(?: mir)?|bitte|nur|davon) )*(?:das |den )?"
        r"(neueste|neuste|aktuellste|älteste|aelteste) (?:paper|artikel)"
        r"(?: (?:aus|in) (?:der|dieser|deiner) liste| davon)?", text)
    if not match:
        return None
    return "oldest" if (match[1] or match[2]) in {"oldest", "earliest", "älteste", "aelteste"} else "newest"


def discussion_number(message):
    """Resolve a numbered paper in a question without treating scientific numbers as ordinals."""
    text = controls(without_conversational_decline(message))
    correction = re.fullmatch(r"(?:actually[, ]+)?i (?:only )?meant\s+" + _ITEM +
                              r"(?:[, ]+not\s+" + _ITEM + r")?", text)
    if correction:
        return _number(correction[1])
    matches = list(re.finditer(
        rf"\b(?:(?:the|das|der|dem|den) ({_POS}) (?:paper|article|study|one|artikel|studie)|"
        rf"(?:paper|article|study|artikel|studie) (?:number |nummer )?({_POS}))(?!\w)", text))
    if len(matches) != 1 or re.search(r"\b(?:not|except|excluding|without|nicht|ohne|kein\w*|außer|ausser)\b", text):
        return None
    if re.search(r'\b(?:compare|comparison|versus|vs|vergleich\w*)\b|(?:\band|\bund|&)\s+(?:the |das |dem |den |paper )?' + _POS + r'(?!\w)', text):
        return None
    asking = re.match(r"(?:what|why|how|explain|describe|tell me|was|wie|warum|erklär\w*|beschreib\w*)\b", text)
    discussing = re.search(r'\b(?:summari[sz]e|discuss|read|talk|look at|go through|unterhalt\w*|besprech\w*|durchles\w*|lesen|lies)\b', text)
    if not asking and not discussing:
        return None
    return _number(matches[0][1] or matches[0][2])
