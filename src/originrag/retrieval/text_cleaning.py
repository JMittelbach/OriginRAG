from __future__ import annotations

import re


_CHAR_REPLACEMENTS = {
    "\u00ad": "",
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\u0001": "alpha",
    "\u0002": "beta",
}

_LIKELY_PARTIAL_START_WORDS = {
    "arial",
    "ost",
    "pared",
    "nto",
    "ingle",
    "lti",
}


def clean_pdf_text_for_display(text: str) -> str:
    """Normalize common PDF extraction artifacts without changing scientific meaning."""
    value = str(text or "")
    for source, target in _CHAR_REPLACEMENTS.items():
        value = value.replace(source, target)

    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"([A-Za-z])- *\n *([a-z])", r"\1\2", value)
    value = re.sub(r"([A-Za-z])- +([a-z]{2,})", r"\1\2", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\s+", " ", value).strip()
    return _trim_partial_edges(value)


def evidence_window(question: str, text: str, snippet: str = "", max_chars: int = 1800) -> str:
    clean_text = clean_pdf_text_for_display(text)
    if max_chars <= 0 or len(clean_text) <= max_chars:
        return clean_text

    tokens = _query_tokens(question)
    if not tokens:
        return _trim_to_sentence_boundary(clean_text[:max_chars], trim_start=False)

    lower = clean_text.lower()
    candidate_positions = _candidate_window_positions(lower, tokens)
    if snippet:
        clean_snippet = clean_pdf_text_for_display(snippet)
        if clean_snippet:
            snippet_pos = lower.find(clean_snippet[:80].lower())
            if snippet_pos >= 0:
                candidate_positions.append(snippet_pos)

    if not candidate_positions:
        return _trim_to_sentence_boundary(clean_text[:max_chars], trim_start=False)

    center = _best_window_center(clean_text, tokens, candidate_positions, max_chars)
    start, end, prefix, suffix = _window_bounds(len(clean_text), center, max_chars)
    window = clean_text[start:end].strip()
    return prefix + _trim_to_sentence_boundary(window, trim_start=start > 0) + suffix


def _candidate_window_positions(lower_text: str, tokens: list[str]) -> list[int]:
    """Sample first and last matches throughout the text, at most 256 positions."""
    tokens = list(dict.fromkeys(tokens))
    bucket_width = max(1, (len(lower_text) + 127) // 128)
    positions = set()
    for start in range(0, len(lower_text), bucket_width):
        end = min(len(lower_text), start + bucket_width)
        first = last = None
        for token in tokens:
            search_end = min(len(lower_text), end + len(token) - 1)
            left = lower_text.find(token, start, search_end)
            if left < 0 or left >= end:
                continue
            right = lower_text.rfind(token, start, search_end)
            first = left if first is None else min(first, left)
            last = right if last is None else max(last, right)
        if first is not None:
            positions.update((first, last))
    return sorted(positions)


def _window_bounds(length: int, position: int, max_chars: int) -> tuple[int, int, str, str]:
    start = max(0, position - max_chars // 3)
    prefix = "[...] " if start else ""
    suffix = " [...]" if start + max_chars - len(prefix) < length else ""
    body_max = max(0, max_chars - len(prefix) - len(suffix))
    return start, min(length, start + body_max), prefix, suffix


def _query_tokens(question: str) -> list[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(question or "").lower())
    stopwords = {
        "a",
        "about",
        "an",
        "and",
        "any",
        "anything",
        "are",
        "as",
        "does",
        "find",
        "for",
        "from",
        "how",
        "in",
        "information",
        "is",
        "of",
        "the",
        "to",
        "what",
        "whatever",
        "with",
    }
    return [token for token in normalized.split() if len(token) >= 4 and token not in stopwords]


def _best_window_center(text: str, tokens: list[str], positions: list[int], max_chars: int) -> int:
    best_position = positions[0]
    best_score = -1
    for position in positions:
        start, end, _prefix, _suffix = _window_bounds(len(text), position, max_chars)
        window = _trim_to_sentence_boundary(text[start:end], trim_start=start > 0).lower()
        score = sum(window.count(token) for token in tokens)
        coverage = sum(1 for token in tokens if token in window)
        score += coverage * 3
        if score > best_score:
            best_score = score




            best_position = position
    return best_position


def _trim_partial_edges(text: str) -> str:
    value = text.strip()
    if not value:
        return value
    first_word_match = re.match(r"^([a-z][a-z]{2,})\b\s+", value)
    if first_word_match and first_word_match.group(1) in _LIKELY_PARTIAL_START_WORDS:
        value = value[first_word_match.end() :].lstrip()
    return value


def _trim_to_sentence_boundary(text: str, *, trim_start: bool = True) -> str:
    value = text.strip()
    if len(value) < 80:
        return value
    start_match = re.search(r"(?<=[.!?])\s+[A-Z0-9]", value[:250]) if trim_start else None
    if start_match:
        value = value[start_match.end() - 1 :].lstrip()
    end_match = list(re.finditer(r"[.!?](?:\s|$)", value))
    if end_match and end_match[-1].end() > len(value) * 0.65:
        value = value[: end_match[-1].end()].rstrip()
    return value
