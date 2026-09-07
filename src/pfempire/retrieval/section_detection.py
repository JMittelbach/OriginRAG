from __future__ import annotations

import re
from typing import TypedDict


class SectionDetection(TypedDict):
    section: str
    section_confidence: float
    section_markers: list[str]


SECTION_UNKNOWN = "unknown"
CONTEXT_SECTIONS = {
    "abstract",
    "introduction",
    "methods",
    "results",
    "discussion",
    "references",
    "supplement",
}
_NUMBERED_HEADING = r"(?:\d+(?:\.\d+)*\s+)?"

_HEADING_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("abstract", "Abstract", re.compile(rf"^\s*{_NUMBERED_HEADING}(abstract|summary)\s*:?\s*$", re.IGNORECASE)),
    (
        "introduction",
        "Introduction",
        re.compile(rf"^\s*{_NUMBERED_HEADING}(introduction|background)\s*:?\s*$", re.IGNORECASE),
    ),
    (
        "methods",
        "Methods",
        re.compile(
            rf"^\s*{_NUMBERED_HEADING}((materials\s+and\s+methods)|methods|methodology|statistical\s+analysis|ethics\s+statement|study\s+approval|sample\s+collection)\s*:?\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "results",
        "Results",
        re.compile(rf"^\s*{_NUMBERED_HEADING}(results|findings|results\s+and\s+discussion)\s*:?\s*$", re.IGNORECASE),
    ),
    (
        "discussion",
        "Discussion",
        re.compile(rf"^\s*{_NUMBERED_HEADING}(discussion|conclusion|conclusions|summary\s+and\s+conclusions)\s*:?\s*$", re.IGNORECASE),
    ),
    (
        "data_availability",
        "Data availability",
        re.compile(r"^\s*(data\s+availability|data\s+and\s+materials\s+availability|availability\s+of\s+data.*)\s*:?\s*$", re.IGNORECASE),
    ),
    (
        "references",
        "References",
        re.compile(r"^\s*(references|bibliography|literature\s+cited)\s*:?\s*$", re.IGNORECASE),
    ),
    (
        "supplement",
        "Supplement",
        re.compile(r"^\s*(supplementary\s+(information|materials?|methods?|data)|supplemental\s+(information|materials?|methods?|data)|appendix)\s*:?\s*$", re.IGNORECASE),
    ),
]

_FIGURE_START = re.compile(r"^\s*((supplementary|supplemental)\s+)?(figure|fig\.)\s+[A-Za-z0-9]+", re.IGNORECASE)
_TABLE_START = re.compile(r"^\s*((supplementary|supplemental)\s+)?table\s+[A-Za-z0-9]+", re.IGNORECASE)

_INLINE_MARKERS: list[tuple[str, str, re.Pattern[str]]] = [
    ("references", "References inline", re.compile(r"(?:^|\n)\s*(references|bibliography)\s*(?:\n|$)", re.IGNORECASE)),
    ("data_availability", "Data availability inline", re.compile(r"(?:^|\n)\s*data\s+availability\s*:?", re.IGNORECASE)),
    ("supplement", "Supplement inline", re.compile(r"(?:^|\n)\s*supplementary\s+(information|materials?|methods?|data)", re.IGNORECASE)),
    ("discussion", "Discussion inline", re.compile(r"(?:^|\n)\s*(discussion|conclusion|conclusions)\s*:?\s*(?:\n|$)", re.IGNORECASE)),
    ("results", "Results inline", re.compile(r"(?:^|\n)\s*results\s*:?\s*(?:\n|$)", re.IGNORECASE)),
    ("methods", "Methods inline", re.compile(r"(?:^|\n)\s*(materials\s+and\s+methods|methods|statistical\s+analysis)\s*:?\s*(?:\n|$)", re.IGNORECASE)),
    ("introduction", "Introduction inline", re.compile(r"(?:^|\n)\s*(introduction|background)\s*:?\s*(?:\n|$)", re.IGNORECASE)),
    ("abstract", "Abstract inline", re.compile(r"(?:^|\n)\s*abstract\s*:?\s*(?:\n|$)", re.IGNORECASE)),
]


def detect_section_for_chunk(text: str, previous_section: str | None = None) -> SectionDetection:
    """Infer a coarse paper section for a chunk using conservative PDF-text heuristics."""
    clean_text = (text or "").strip()
    if not clean_text:
        return {"section": SECTION_UNKNOWN, "section_confidence": 0.0, "section_markers": []}

    lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
    first_lines = lines[:12]
    first_text = "\n".join(first_lines)
    markers: list[str] = []

    if _TABLE_START.search(first_text):
        return {"section": "table", "section_confidence": 0.95, "section_markers": ["table_start"]}
    if _FIGURE_START.search(first_text):
        return {"section": "figure_legend", "section_confidence": 0.95, "section_markers": ["figure_start"]}

    line_match = _match_heading_lines(first_lines)
    if line_match:
        section, marker = line_match
        return {"section": section, "section_confidence": 0.95, "section_markers": [marker]}




    if not carries_forward_section(previous_section) and _looks_like_unlabelled_article_abstract(clean_text):
        return {
            "section": "abstract",
            "section_confidence": 0.68,
            "section_markers": ["unlabelled_article_first_page"],
        }

    inline_match = _match_inline_marker(clean_text[:1800])
    if inline_match:
        section, marker, confidence = inline_match
        markers.append(marker)
        return {"section": section, "section_confidence": confidence, "section_markers": markers}

    lower = clean_text[:1800].lower()
    if lower.count("doi:") >= 2 and lower.count("et al") >= 3:
        return {"section": "references", "section_confidence": 0.75, "section_markers": ["reference_density"]}
    if _table_density(lines):
        return {"section": "table", "section_confidence": 0.70, "section_markers": ["table_density"]}

    if carries_forward_section(previous_section):
        return {
            "section": previous_section,
            "section_confidence": 0.55,
            "section_markers": ["previous_section_context"],
        }

    return {"section": SECTION_UNKNOWN, "section_confidence": 0.25, "section_markers": []}


def carries_forward_section(section: str | None) -> bool:
    """Return whether a section label is safe to propagate to the next chunk."""
    return bool(section and section in CONTEXT_SECTIONS)


def stored_section_needs_redetection(chunk: dict) -> bool:
    """Identify labels produced by superseded, over-broad propagation rules."""
    if not chunk.get("section"):
        return True
    if chunk.get("section_source") == "manual":
        return False
    markers = {str(marker) for marker in (chunk.get("section_markers") or [])}
    section = str(chunk.get("section") or "unknown")
    if "supplement_term" in markers:
        return True
    return "previous_section_context" in markers and section in {
        "supplement",
        "table",
        "figure_legend",
    }


def _looks_like_unlabelled_article_abstract(text: str) -> bool:
    sample = text[:2200]
    lower = sample.lower()
    if len(sample) < 700:
        return False
    has_article_marker = bool(
        re.search(r"^\s*(article|review)\b", sample, re.IGNORECASE)
        or re.search(r"\bhttps?://doi\.org/10\.", lower)
        or re.search(r"\bdoi:\s*10\.", lower)
    )
    has_authorship_signal = bool(
        re.search(r"\b(received|accepted|correspondence|check for updates)\b", lower)
        or re.search(r"\b(university|institute|department)\b", lower)
    )
    return has_article_marker and has_authorship_signal


def _match_heading_lines(lines: list[str]) -> tuple[str, str] | None:
    for line in lines:
        compact = re.sub(r"\s+", " ", line).strip()
        if len(compact) > 80:
            continue
        for section, marker, pattern in _HEADING_PATTERNS:
            if pattern.match(compact):
                return section, marker
    return None


def _match_inline_marker(text: str) -> tuple[str, str, float] | None:
    matches: list[tuple[int, str, str]] = []
    for section, marker, pattern in _INLINE_MARKERS:
        match = pattern.search(text)
        if match:
            matches.append((match.start(), section, marker))
    if not matches:
        return None
    position, section, marker = min(matches, key=lambda item: item[0])
    confidence = 0.90 if position < 300 else 0.78
    return section, marker, confidence


def _table_density(lines: list[str]) -> bool:
    if len(lines) < 8:
        return False
    sample = lines[:18]
    numeric_lines = sum(1 for line in sample if len(re.findall(r"\d+(?:\.\d+)?", line)) >= 3)
    delimiter_lines = sum(1 for line in sample if "\t" in line or "|" in line or re.search(r"\s{4,}", line))
    short_lines = sum(1 for line in sample if len(line) <= 55)
    digit_count = sum(1 for line in sample for char in line if char.isdigit())
    char_count = sum(len(line) for line in sample)
    digit_fraction = digit_count / max(char_count, 1)
    return (
        numeric_lines >= 8
        and short_lines / max(len(sample), 1) > 0.65
        and (delimiter_lines >= 4 or digit_fraction > 0.22)
    )
