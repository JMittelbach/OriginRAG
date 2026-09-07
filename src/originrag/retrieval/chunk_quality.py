from __future__ import annotations

import re
import string
from collections.abc import Callable
from typing import TypedDict


class ChunkQuality(TypedDict):
    is_empty: bool
    is_too_short: bool
    is_reference_like: bool
    is_table_like: bool
    is_figure_like: bool
    is_supplement_like: bool
    is_methods_like: bool
    is_low_text_quality: bool
    word_count: int
    char_count: int
    digit_fraction: float
    punctuation_fraction: float
    uppercase_fraction: float
    quality_penalty: float
    flags: list[str]


FIGURE_TERMS = [
    "fig.",
    "figure",
    "table",
    "supplementary fig",
    "supplementary figure",
    "supplementary table",
]

SUPPLEMENT_TERMS = [
    "supplementary",
    "supplemental",
    "appendix",
    "table s",
    "fig. s",
    "figure s",
]

METHODS_TERMS = [
    "methods",
    "materials and methods",
    "statistical analysis",
    "ethics statement",
    "data availability",
    "study approval",
    "sample collection",
]


def classify_chunk_quality(text: str) -> ChunkQuality:
    stripped = (text or "").strip()
    char_count = len(stripped)
    words = re.findall(r"[A-Za-z0-9α-ωΑ-Ω]+(?:[-'][A-Za-z0-9α-ωΑ-Ω]+)?", stripped)
    word_count = len(words)
    lower = stripped.lower()
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]

    digit_fraction = _fraction(stripped, lambda char: char.isdigit())
    punctuation_fraction = _fraction(stripped, lambda char: char in string.punctuation)
    uppercase_fraction = _uppercase_fraction(stripped)

    is_empty = not stripped
    is_too_short = word_count < 80 or char_count < 500
    is_reference_like = _is_reference_like(lower, lines)
    is_table_like = _is_table_like(stripped, lines, digit_fraction)
    is_figure_like = _term_count(lower, FIGURE_TERMS) >= 4
    is_supplement_like = _term_count(lower, SUPPLEMENT_TERMS) >= 1
    is_methods_like = _term_count(lower, METHODS_TERMS) >= 2 or lower.startswith("methods")
    is_low_text_quality = (
        is_empty
        or is_too_short
        or digit_fraction > 0.35
        or punctuation_fraction > 0.22
        or uppercase_fraction > 0.55
    )

    flags = []
    if is_empty:
        flags.append("empty")
    if is_too_short:
        flags.append("too_short")
    if is_reference_like:
        flags.append("reference_like")
    if is_table_like:
        flags.append("table_like")
    if is_figure_like:
        flags.append("figure_like")
    if is_supplement_like:
        flags.append("supplement_like")
    if is_methods_like:
        flags.append("methods_like")
    if is_low_text_quality:
        flags.append("low_text_quality")

    quality_penalty = 0.0
    if is_empty:
        quality_penalty += 100.0
    if is_too_short:
        quality_penalty += 3.0
    if is_reference_like:
        quality_penalty += 10.0
    if is_table_like:
        quality_penalty += 6.0
    if is_figure_like:
        quality_penalty += 4.0
    if is_supplement_like:
        quality_penalty += 4.0
    if is_low_text_quality and not is_empty:
        quality_penalty += 4.0

    return {
        "is_empty": is_empty,
        "is_too_short": is_too_short,
        "is_reference_like": is_reference_like,
        "is_table_like": is_table_like,
        "is_figure_like": is_figure_like,
        "is_supplement_like": is_supplement_like,
        "is_methods_like": is_methods_like,
        "is_low_text_quality": is_low_text_quality,
        "word_count": word_count,
        "char_count": char_count,
        "digit_fraction": round(digit_fraction, 4),
        "punctuation_fraction": round(punctuation_fraction, 4),
        "uppercase_fraction": round(uppercase_fraction, 4),
        "quality_penalty": round(quality_penalty, 3),
        "flags": flags,
    }


def _is_reference_like(lower: str, lines: list[str]) -> bool:
    if re.search(r"(^|\n)\s*(references|bibliography)\s*($|\n)", lower):
        return True
    doi_count = lower.count("doi") + lower.count("https://doi.org")
    numbered_reference_lines = sum(1 for line in lines if re.match(r"^\[?\d{1,3}\]?[\).]\s+", line))
    return (
        doi_count >= 4
        or numbered_reference_lines >= 5
    )


def _is_table_like(text: str, lines: list[str], digit_fraction: float) -> bool:
    if not text:
        return False
    delimiter_count = sum(text.count(token) for token in ["\t", "|", "  ", ";"])
    numeric_lines = sum(1 for line in lines if len(re.findall(r"\d+(?:\.\d+)?", line)) >= 3)
    single_value_lines = sum(1 for line in lines if re.match(r"^[<>]?\d+(?:\.\d+)?(?:[–-]\d+(?:\.\d+)?)?$", line))
    short_lines = sum(1 for line in lines if len(line) <= 60)
    statistical_table_terms = len(
        re.findall(
            r"\b(odds ratios?|confidence intervals?|p values?|logistic regression|model \d+|anova)\b",
            text.lower(),
        )
    )
    return (
        (digit_fraction > 0.18 and numeric_lines >= 4)
        or (single_value_lines >= 8 and statistical_table_terms >= 2)
        or (len(lines) >= 8 and short_lines / max(len(lines), 1) > 0.65 and numeric_lines >= 3)
        or delimiter_count > 60
    )


def _term_count(lower: str, terms: list[str]) -> int:
    return sum(lower.count(term) for term in terms)


def _fraction(text: str, predicate: Callable[[str], bool]) -> float:
    if not text:
        return 0.0
    return sum(1 for char in text if predicate(char)) / len(text)


def _uppercase_fraction(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for char in letters if char.isupper()) / len(letters)
