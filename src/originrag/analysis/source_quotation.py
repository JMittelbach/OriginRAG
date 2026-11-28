"""Select source excerpts without letting a model invent the quoted wording."""

import json
import re
import unicodedata

from originrag.analysis.reading_support import interpretation_passages, source_sentences


_WORDS = re.compile(r"\w{4,}")
_STOP = set("please paper study quote quotation short exact source passage statement show give directly"
            " that this which what with from they their were have does prove bitte diese dieser"
            " quelle zitat wörtlich zeigen textstelle about supporting supports explanation distinction"
            " earlier previous dafür dazu with".split())
_LIMITATION_REQUEST = re.compile(r"\b(?:limitations?|limits?|einschr[aä]nkung\w*|grenzen)\b", re.I)
_LIMITATION_TEXT = re.compile(r"\b(?:limitation\w*|limited|only|unavailable|"
                               r"not (?:directly )?(?:measured|evaluated|assessed|tested)|"
                               r"could not|cannot|unable to)\b", re.I)


def wants_quotation(question):
    """Only a standalone, single-passage request; don't discard additional tasks."""
    text = question.casefold()
    if re.search(r"\b(?:don't|do not|without|no|kein\w*|ohne|nicht)\s+(?:direct\s+)?(?:quot\w*|zitat\w*|zitier\w*)", text):
        return False
    if re.search(r"\b(?:explain|summari[sz]e|compare|translate|erklär\w*|erlaeuter\w*|übersetz\w*|vergleiche)\b", text):
        return False
    if re.search(r"\b(?:two|three|four|five|several|multiple|zwei|drei|mehrere|[2-9])\s+"
                 r"(?:(?:short|exact|brief|kurze|wörtliche)\s+)*(?:quot\w*|sentences?|passages?|zitate?|sätze)\b", text):
        return False
    if re.search(r"\b(?:quotes|quotations|zitate)\b", text):
        return False
    return bool(re.search(r"\b(?:quote|quotation|quotations|verbatim|wörtlich\w*|woertlich\w*|zitat\w*|zitier\w*)\b", text)
                and re.search(r"\b(?:show|give|find|provide|quote|copy|one|an?|zeig\w*|gib|finde|zitier\w*|ein\w*)\b", text))


def quotation_candidates(passages, question, history=(), limit=12):
    def words(text):
        return set(_WORDS.findall(unicodedata.normalize("NFKC", text).casefold())) - _STOP
    recent = history[-2:]
    previous_question = recent[-1][0] if recent else ""
    current_words = words(question)
    previous_words = words(" ".join(q for q, _ in recent))
    limitation_request = bool(_LIMITATION_REQUEST.search(question))
    answer_words = words(recent[-1][1]) if recent else set()
    anchors = interpretation_passages(passages, previous_question)
    preferred = {(part["page"], part["text"]) for part in anchors}
    candidates, seen = [], set()
    for part in passages:
        page = part.get("page")
        if type(page) is not int or page < 1:
            continue
        text = " ".join(str(part.get("text") or "").split())
        for sentence in source_sentences(text):
            if not 20 <= len(sentence) <= 800 or sentence in seen:
                continue
            if not re.search(r'[.!?][”"’\')\]]*$', sentence):
                continue
            terms = words(sentence)
            overlap = len(terms & current_words)
            prior = len(terms & previous_words)
            related = len(terms & answer_words)
            limitation = limitation_request and bool(_LIMITATION_TEXT.search(sentence))
            if not overlap and not limitation and not prior and related < 2:
                continue
            score = 8 * overlap + 12 * limitation + min(prior + related, 6)
            if not current_words:
                score += 8 * ((page, sentence) in preferred)
            seen.add(sentence)
            candidates.append((score, len(candidates), {"page": page, "text": sentence}))
    return [dict(id=f"Q{i}", **part) for i, (_, _, part) in enumerate(
        sorted(candidates, key=lambda item: (-item[0], item[1]))[:limit], 1)]


def render_selected_quotation(response, candidates):
    """The selection may come from a model; the displayed quote never does."""
    try:
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(response or "").strip())
        result = json.loads(raw)
        identifier = result.get("passage_id") if isinstance(result, dict) else None
    except (TypeError, ValueError):
        identifier = None
    chosen = next((part for part in candidates if part["id"] == identifier), None)
    if chosen:
        # Escape Markdown syntax in the source, preserving its visible wording.
        text = re.sub(r"([\\`*_\[\]<>])", r"\\\1", chosen["text"])
        return f'> {text}\n\n[PDF p. {chosen["page"]}]'
    return ("I couldn't identify an exact supporting quotation in the available paper text. "
            "That does not establish the earlier claim. Ask about a specific statement or check the PDF.")


def quotation_schema(candidates):
    return {"type": "object", "properties": {"passage_id": {"enum": [None, *[p["id"] for p in candidates]]}},
            "required": ["passage_id"], "additionalProperties": False}
