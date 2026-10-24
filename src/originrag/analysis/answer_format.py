from __future__ import annotations

import re


CANONICAL_HEADINGS = {
    "hypothesis": {
        "hypothesis card": "## Hypothesis Card",
        "hypothesis_id": "### hypothesis_id",
        "hypothesis id": "### hypothesis_id",
        "title": "### title",
        "short_summary": "### short_summary",
        "short summary": "### short_summary",
        "evidence_backed_statements": "### evidence_backed_statements",
        "evidence backed statements": "### evidence_backed_statements",
        "inference": "### inference",
        "speculation": "### speculation",
        "testable_prediction": "### testable_prediction",
        "testable prediction": "### testable_prediction",
        "required_data": "### required_data",
        "required data": "### required_data",
        "analysis_plan": "### analysis_plan",
        "analysis plan": "### analysis_plan",
        "risk_plan_b": "### risk_plan_b",
        "risk plan b": "### risk_plan_b",
        "novelty": "### novelty",
        "impact": "### impact",
        "key_references": "### key_references",
        "key references": "### key_references",
        "references": "### key_references",
    },
    "gaps": {
        "short answer": "## Short Answer",
        "supported research gaps": "## Supported Research Gaps",
        "candidate testable questions": "## Candidate Testable Questions",
        "best next data / analysis": "## Best Next Data / Analysis",
        "best next data analysis": "## Best Next Data / Analysis",
        "key sources": "## Key Sources",
    },
    "answer": {
        "evidence backed statements": "## Evidence-backed statements",
        "evidence-backed statements": "## Evidence-backed statements",
        "cautious inference": "## Cautious inference",
        "limitations / uncertainty": "## Limitations / uncertainty",
        "limitations uncertainty": "## Limitations / uncertainty",
        "missing evidence": "## Missing evidence",
        "key sources": "## Key sources",
        "references": "## References",
    },
    "sources": {
        "best match": "## Best match",
        "why this looks like the source": "## Why this looks like the source",
        "other possible matches": "## Other possible matches",
    },
}


def normalize_answer_format(answer: str | None, task: str) -> str | None:
    """Canonicalize known markdown headings without changing answer content."""
    if answer is None:
        return None
    headings = CANONICAL_HEADINGS.get((task or "answer").strip().lower(), {})
    if not headings:
        return answer

    normalized_lines = []
    for line in answer.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            normalized_lines.append(line)
            continue
        heading_text = match.group(2).strip().strip(":")
        key = _heading_key(heading_text)
        normalized_lines.append(headings.get(key, line))
    normalized = "\n".join(_deduplicate_adjacent_headings(normalized_lines, set(headings.values()))).strip()
    normalized = _normalize_evidence_id_width(normalized)
    normalized_task = (task or "answer").strip().lower()
    if normalized_task == "gaps":
        normalized = _fill_empty_gap_short_answer(normalized)
    elif normalized_task == "answer":
        normalized = _fill_empty_cautious_inference(normalized)
    return normalized


def _deduplicate_adjacent_headings(lines: list[str], headings: set[str]) -> list[str]:
    result = []
    last_nonblank = None
    fence = None
    for line in lines:
        fence_match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence_match:
            marker, trailing = fence_match.groups()
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not trailing.strip():
                fence = None
        if fence is None and line in headings and line == last_nonblank:
            continue
        result.append(line)
        if line.strip():
            last_nonblank = line
    return result


def _normalize_evidence_id_width(answer: str) -> str:
    """Convert common local-LLM citation variants like E05 to E005."""

    def replace(match: re.Match[str]) -> str:
        return f"E{int(match.group(1)):03d}"

    return re.sub(r"\bE(\d{1,2})\b", replace, answer)


def _heading_key(value: str) -> str:
    key = value.strip().lower()
    key = key.replace("_", " ")
    key = re.sub(r"[*`]+", "", key)
    key = key.replace("-", " ")
    key = re.sub(r"\s+", " ", key)
    return key


def _fill_empty_gap_short_answer(answer: str) -> str:
    short_answer = _section_text(answer, "## Short Answer")
    if short_answer:
        return answer
    first_gap = _first_gap_statement(answer)
    if not first_gap:
        return answer
    summary = (
        "The retrieved evidence points first to this gap: "
        f"{first_gap.rstrip('.')}."
    )
    return answer.replace("## Short Answer\n## Supported Research Gaps", f"## Short Answer\n{summary}\n\n## Supported Research Gaps", 1)


def _fill_empty_cautious_inference(answer: str) -> str:
    """Keep a factual answer when the model correctly makes no inference."""

    heading = "## Cautious inference"
    if heading not in answer or _section_text(answer, heading):
        return answer
    statement = (
        "No additional inference is warranted beyond the evidence-backed "
        "statements above."
    )
    return re.sub(
        rf"{re.escape(heading)}\s*(?=\n##\s+)",
        f"{heading}\n{statement}\n",
        answer,
        count=1,
    )


def _first_gap_statement(answer: str) -> str:
    gaps_text = _section_text(answer, "## Supported Research Gaps")
    if not gaps_text:
        return ""
    match = re.search(
        r"(?:^|\n)\s*[-*]?\s*(?:\*\*)?gap(?:\*\*)?\s*:\s*(.+)",
        gaps_text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return _clean_inline_markdown(match.group(1).strip())


def _section_text(answer: str, heading: str) -> str:
    start = answer.find(heading)
    if start < 0:
        return ""
    remainder = answer[start + len(heading) :]
    next_heading = re.search(r"\n#{2,3}\s+", remainder)
    if not next_heading:
        return remainder.strip()
    return remainder[: next_heading.start()].strip()


def _clean_inline_markdown(text: str) -> str:
    clean = re.sub(r"[*_`]+", "", text)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()
