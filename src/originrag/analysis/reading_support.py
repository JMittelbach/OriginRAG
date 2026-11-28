"""Source-backed reminders for questions about measurements and conclusions."""

import re
import unicodedata


_CHECK = re.compile(
    r"\b(?:prov(?:e|es|en)|proof|direct(?:ly)?|measur\w*|infer\w*|conclud\w*|"
    r"beweis\w*|beleg\w*|direkt\w*|gemessen|mess\w*|schlussfolg\w*|"
    r"heisst|bedeutet|zeigt das|does (?:that|this) mean)\b", re.I)
_CUES = (
    (5, re.compile(r"\b(?:did not|do not|could not|not possible to|unable to)\b.{0,70}"
                   r"\b(?:measur\w*|establish|determine|conclud\w*|distinguish|infer\w*)\b", re.I)),
    (4, re.compile(r"\b(?:hypothes\w*|one (?:possible )?explanation|may reflect|might reflect)\b", re.I)),
    (3, re.compile(r"\b(?:not (?:statistically )?significant|did not differ significantly|"
                   r"no significant|does not (?:prove|establish|imply)|cannot (?:exclude|rule out))\b", re.I)),
    (1, re.compile(r"\b(?:limitation\w*|inferred|inference|consistent with)\b", re.I)),
)
_STOP = set("what which were this that they their does could would mean please paper study"
            " explain briefly measured directly measure about findings nicht bitte haben wurden"
            " diese dieser dieses paper zeigt means prove evidence".split())
_CAUSAL = re.compile(r"\b(?:causal\w*|causat\w*|kausal\w*|urs[aä]ch\w*)\b", re.I)
_CAUSAL_DESIGN = re.compile(r"\b(?:causal\w*|causat\w*|kausal\w*|urs[aä]ch\w*|"
                            r"random\w*|confound\w*|observational|beobachtungs\w*)\b", re.I)


def _words(text):
    return set(re.findall(r"\w{4,}", unicodedata.normalize("NFKC", text).casefold())) - _STOP


def source_sentences(text):
    """Keep figure references and parenthetical qualifications inside their sentence."""
    text = ' '.join(str(text or '').split())
    start = 0
    for boundary in re.finditer(r'(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ])', text):
        sentence = text[start:boundary.start()]
        if re.search(r'\b(?:Figs?|Drs?|Prof|al|vs|e\.g|i\.e)\.$', sentence, re.I):
            continue
        if sentence.count('(') > sentence.count(')') or sentence.count('[') > sentence.count(']'):
            continue
        yield sentence
        start = boundary.end()
    if start < len(text):
        yield text[start:]


def interpretation_passages(passages, question, previous_question=""):
    """Copy measured-vs-inferred qualifiers; never invent a conclusion or substitute prior replies."""
    if not _CHECK.search(unicodedata.normalize("NFKC", question)):
        return []
    keywords = _words(question) | _words(previous_question)
    candidates, seen = [], set()
    for part in passages:
        page = part.get("page")
        if not isinstance(page, int) or page <= 0:
            continue
        text = " ".join(part.get("text", "").split())
        for sentence in source_sentences(text):
            if not 40 <= len(sentence) <= 900 or sentence in seen:
                continue
            normalized = unicodedata.normalize("NFKC", sentence)
            if _CAUSAL.search(question) and not _CAUSAL_DESIGN.search(normalized):
                continue
            strength = sum(weight for weight, pattern in _CUES if pattern.search(normalized))
            overlap = len(_words(sentence) & keywords)
            if not strength or not overlap:
                continue
            seen.add(sentence)
            candidates.append((strength + 2 * overlap, page, sentence))
    selected, used_pages = [], set()
    while candidates and len(selected) < 3:
        best = max(range(len(candidates)), key=lambda i: candidates[i][0] + (candidates[i][1] not in used_pages))
        _, page, text = candidates.pop(best)
        selected.append({"page": page, "text": text})
        used_pages.add(page)
    return selected


def experimental_context_passages(passages, question):
    """Keep negative controls and conditional interventions attached to their actual system."""
    if not re.search(r'\b(?:summ\w*|findings?|results?|knock\w*|modif\w*|delet\w*|mutan\w*|'
                     r'perturb\w*|disrupt\w*|experiment\w*|phenotyp\w*|production|what did)\b', question, re.I):
        return []
    candidates = []
    for part in passages:
        if type(part.get('page')) is not int or part['page'] < 1:
            continue
        for sentence in source_sentences(part.get('text', '')):
            if not 40 <= len(sentence) <= 1100:
                continue
            system = re.search(r'\b(?:mutant\w*|mutation\w*|strain\w*|clone\w*|cell line\w*|'
                               r'knock\w*|loss-of-function|deletion\w*|condition\w*)\b', sentence, re.I)
            qualifier = re.search(r'\b(?:failed|unable|did not|could not|loss-of-function|'
                                  r'conditional|only when|in (?:the )?(?:absence|presence))\b', sentence, re.I)
            if system and qualifier and sentence not in [row['text'] for row in candidates]:
                candidates.append({'page': part['page'], 'text': sentence})
    return candidates[:4]
