"""Source cues for questions about one named method, independent of research field."""

import re


_DEFINITION = re.compile(
    r"\bwe\s+(?:(?:here|have|also)\s+)?(?:develop(?:ed)?|introduced?|present(?:ed)?|proposed?)"
    r"\s+(?!results\b|findings\b|evidence\b)[^.!?;]{0,105}?\b"
    r"(?:method|metric|model|algorithm|score|tool|framework|approach|pipeline|package|simulator)\b",
    re.I,
)
_TABLE_CAPTION = re.compile(r"^\s*Table\s+[A-Z]?\d+[.:]?\s*$", re.I | re.M)


def describes_named_method(text: str, title: str, name: str) -> bool:
    if not name or name.casefold() not in title.casefold():
        return False
    term = rf"(?<!\w){re.escape(name)}(?!\w)"
    if not re.search(term, title, re.I):
        return False
    branded_title = re.match(rf"\s*{term}\s*[:–—-]", title, re.I)
    return bool((branded_title and _DEFINITION.search(text)) or re.search(
        rf"\bwe\s+(?:(?:here|have)\s+)?(?:develop(?:ed)?|introduced?|present(?:ed)?|proposed?)\s+{term}",
        text, re.I))


def contains_flattened_table(text: str) -> bool:
    """A table callout in running prose is not a table of detached PDF cells."""
    match = _TABLE_CAPTION.search(text)
    if not match:
        return False
    lines = [line.strip() for line in text[match.end():match.end() + 700].splitlines() if line.strip()]
    return sum(len(line.split()) <= 3 for line in lines[:14]) >= 5


def source_reading_excerpt(pack: dict, name: str) -> str:
    """A short contiguous passage from visible evidence, never a rejected draft."""
    for chunk in pack.get("chunks") or ():
        title = chunk.get("title") or ""
        if (not chunk.get("paper_id") or not title or not name or name.casefold() not in title.casefold()
                or chunk.get("section") in {"references", "table", "figure_legend"}
                or set(chunk.get("quality_flags") or ()) & {"reference_like", "table_like"}):
            continue
        text = " ".join(str(chunk.get("text") or "").split())
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for i, sentence in enumerate(sentences):
            if (len(sentence) <= 440 and sentence.endswith((".", "!", "?"))
                    and not any(mark in sentence for mark in ("...", "[...]", "…", "[["))
                    and describes_named_method(sentence, title, name)
                    and re.match(r"(?:Here[, ]+)?We\b", sentence, re.I)):
                following = sentences[i + 1] if i + 1 < len(sentences) else ""
                if (re.match(rf"{re.escape(name)}\b", following, re.I)
                        and following.endswith(".") and len(sentence) + len(following) < 510
                        and not any(mark in following for mark in ("...", "[...]", "…", "[["))):
                    sentence += " " + following
                pages = chunk.get("page_range")
                label = f"{title} (p. {pages})" if pages and pages not in ("?", "unknown") else title
                return f"\n\n**From the paper (not a synthesis):** {label}\n\n“{sentence}”"
    return ""
