"""Extractive, request-scoped memory. Saved conversations remain unchanged."""

import json
import re

from originrag.analysis.context_budget import estimate_prompt_tokens as cost


def terms(text):
    return set(re.findall(r"\w{4,}", text.casefold()))


_QUESTION_WORDS = frozenset("""about above after again also answer authors before below between could directly
discuss discussion difference differences explain findings found give have into main many mean measured measurement method methods more
most only paper please question report research result results said sample show significant study studies summary
than that their them then there these they this those total used using were what when where which while will with would
aber alle also antwort artikel aufgabe bitte dabei damit dann dass daten diese diesen dieser dieses direkt ergebnis
ergebnisse etwas frage fragen gefunden gibt haben hatten hier kann kannst keine können koennen methode methoden
mehr mich nicht noch oder paper bitte sagt sagen sein sich sind soll studie studien über ueber und unser unsere
unserer unterschied viele waren welche welchen welcher welches werden wurde wurden zeigen zusammen""".split())


def follows_paper_topic(question, previous_question, previous_answer):
    """Recognize an elliptical follow-up by shared content, without rewriting any term."""
    def content(text):
        return {word for word in terms(text) if word not in _QUESTION_WORDS and not word.isdigit()}
    current = content(question)
    if not current:
        return False
    known = content(previous_question + " " + previous_answer)
    shared = current & known
    return bool(shared and (len(shared) >= 2 or any(len(word) >= 6 for word in shared)))


def relevant_excerpt(text, keywords, limit):
    if len(text) <= limit:
        return text
    # Retain complete sentences where possible, in their original order.
    pieces = re.split(r"(?<=[.!?])\s+|\n+", text)
    candidates = []
    for i, piece in enumerate(pieces):
        for start in range(0, len(piece), 400):
            part = piece[start:start + 400]
            candidates.append((len(terms(part) & keywords), i, start, part))
    selected, remaining = [], max(0, limit - 6)
    for score, i, start, part in sorted(candidates, key=lambda x: (-x[0], x[1], x[2])):
        if len(part) <= remaining:
            selected.append((i, start, part))
            remaining -= len(part) + 5
    if not selected:
        return text[:max(0, limit - 2)] + " …"
    return " … ".join(p for _, _, p in sorted(selected)) + " …"


def compact_history(history, question, token_budget):
    """Keep recent turns, the initial intent and relevant earlier wording, never inferred facts."""
    keywords = terms(question)
    if history:
        keywords |= terms(history[-1][0])
    def rank(i):
        q, a = history[i]
        return (3 if i >= len(history) - 2 else 2 if i == 0 else 1,
                len(terms(q + " " + a) & keywords), i)
    selected = []
    for i in sorted(range(len(history)), key=rank, reverse=True):
        q, a = history[i]
        pair = [q, a]
        trial = sorted(selected + [(i, pair)])
        if cost(json.dumps([p for _, p in trial], ensure_ascii=False)) > token_budget:
            pair = [relevant_excerpt(q, keywords, 360), relevant_excerpt(a, keywords, 700)]
        trial = sorted(selected + [(i, pair)])
        if cost(json.dumps([p for _, p in trial], ensure_ascii=False)) <= token_budget:
            selected = trial
    memory = [pair for _, pair in selected]
    shortened = len(selected) != len(history) or any(pair != list(history[i]) for i, pair in selected)
    return memory, shortened


def history_turns(turns, paper_id, question, limit=32):
    """Choose a bounded number of result files to load, all from this paper and this chat."""
    matching = [t for t in turns if t.get("task") == "paper_discussion" and t.get("paper_id") == paper_id]
    keywords = terms(question)
    order = sorted(range(len(matching)), key=lambda i: (
        3 if i >= len(matching)-2 else 2 if i == 0 else 1,
        len(terms(matching[i].get("user", "")) & keywords), i), reverse=True)[:limit]
    return [matching[i] for i in sorted(order)], len(matching)


def returning_to_paper(question):
    if re.search(r"\b(?:not|never|don't|dont|nicht|kein\w*)\b", question, re.I):
        return False
    return bool(re.search(r"\b(?:back to (?:the |that |our )?(?:previous |original )?paper|"
                          r"zur[uü]ck (?:zu dem|zum) (?:vorherigen |urspr[uü]nglichen )?paper)\b", question, re.I))


def returning_paper_id(question, turns):
    """Resolve a return within this conversation, not another chat or the catalogue."""
    if not returning_to_paper(question):
        return None
    visits = []
    for turn in turns:
        paper = turn.get("paper_id") if turn.get("task") == "paper_discussion" else None
        if paper and (not visits or visits[-1] != paper):
            visits.append(paper)
    if not visits:
        return None
    if re.search(r"\b(?:original|urspr[uü]nglichen)\b", question, re.I):
        return visits[0]
    if re.search(r"\b(?:previous|vorherigen)\b", question, re.I) and len(visits) > 1:
        return visits[-2]
    return visits[-1]
