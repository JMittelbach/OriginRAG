"""Parsing helpers for conversational controls, never for paper contents or search terms."""

from dataclasses import dataclass
from functools import lru_cache
import re


_CONTROLS = frozenset("""please could would give show find list liste papers paper article articles study studies library
summarize summarise first second third zeige finde bitte artikel studien paper fass fasse zusammen
kannst bibliothek siehst zeig zeige nenn nenne mir meine mein diese dieses denen montre trouve donne articles études résume résumer dame muestra busca
artículo artículos estudios resume resumir""".split())


def _near(s, word):
    if len(s) == len(word):
        diff = [i for i, (a, b) in enumerate(zip(s, word)) if a != b]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and s[diff[0]:diff[1]+1] == word[diff[0]:diff[1]+1][::-1])
    short, long = sorted((s, word), key=len)
    return len(long) == len(short)+1 and any(short == long[:i]+long[i+1:] for i in range(len(long)))


@lru_cache(maxsize=512)
def _repair(word):
    if not 4 <= len(word) <= 14 or word in _CONTROLS:
        return word
    choices = [w for w in _CONTROLS if _near(word, w)]
    return choices[0] if len(choices) == 1 else word


def controls(text):
    """Only consume the result on a full grammar match; do not send it to retrieval."""
    text = text.casefold().strip(" \t\n¿¡!?.,")
    text = re.sub(r"^(?:hey|ey|hi|okay|ok|salut|hola)[,!:]?\s+", "", text)
    text = re.sub(r"\b(?:pls|plz)\b", "please", text)
    text = re.sub(r"\b(can|could|would) u\b", r"\1 you", text)
    text = re.sub(r"^gimme\b", "give me", text)
    text = re.sub(r",\s*(?:please|bitte|por favor|s’il te plaît|merci)$", "", text)
    return re.sub(r"[^\W\d_]+", lambda m: _repair(m[0]), text)


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
        ("en", r"(?:(?:can|could|would) you )?(?:please )?summari[sz]e ", ""),
        ("de", r"(?:(?:kannst du|bitte) )?(?:fass[e]? (?:mir )?(?:bitte )?)?", r" zusammen(?:fassen)?"),
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
