from __future__ import annotations

import csv
import re
from pathlib import Path

from originrag.utils.io import read_json


REQUIRED_SECTIONS = {"references", "limitations / uncertainty", "missing evidence"}
ISSUE_FIELDS = ["severity", "issue", "detail"]
GROUNDING_CHECK_SCOPE = "structural citations plus conservative lexical overlap"
GROUNDING_CHECK_NOTE = (
    "The lexical check can detect citations attached to wholly unrelated text; "
    "it is not semantic entailment or factual verification."
)






LEXICAL_STOPWORDS = {
    "about",
    "after",
    "also",
    "among",
    "and",
    "answer",
    "are",
    "been",
    "before",
    "being",
    "between",
    "but",
    "can",
    "claim",
    "could",
    "data",
    "der",
    "des",
    "die",
    "dies",
    "diese",
    "dieser",
    "dieses",
    "durch",
    "eine",
    "einem",
    "einen",
    "einer",
    "eines",
    "evidence",
    "for",
    "from",
    "für",
    "gegen",
    "haben",
    "has",
    "have",
    "here",
    "ihre",
    "ihren",
    "indicate",
    "indicated",
    "indicates",
    "into",
    "ist",
    "kann",
    "kein",
    "keine",
    "mit",
    "might",
    "nach",
    "nicht",
    "oder",
    "paper",
    "papers",
    "question",
    "relevant",
    "report",
    "reported",
    "reports",
    "result",
    "results",
    "retrieved",
    "sich",
    "sind",
    "source",
    "sources",
    "study",
    "studies",
    "suggest",
    "suggested",
    "suggests",
    "that",
    "the",
    "their",
    "these",
    "this",
    "those",
    "und",
    "unter",
    "von",
    "was",
    "werden",
    "which",
    "with",
    "without",
    "would",
    "wurde",
    "wurden",
    "zur",
    "über",
}
MIN_ALIGNMENT_TERMS = 4


def check_answer_grounding(answer_text: str, evidence_pack: dict) -> dict:
    chunks = list(evidence_pack.get("chunks") or [])
    known_ids = {str(chunk.get("evidence_id")) for chunk in chunks if chunk.get("evidence_id")}
    cited_ids = set(re.findall(r"\bE\d{3}\b", answer_text or ""))
    issues: list[dict[str, str]] = []

    for cited_id in sorted(cited_ids):
        if cited_id not in known_ids:
            issues.append({"severity": "error", "issue": "unknown_citation", "detail": cited_id})

    if known_ids and not cited_ids:
        issues.append({"severity": "error", "issue": "no_evidence_citations", "detail": "answer cites no evidence IDs"})

    lower = (answer_text or "").lower()
    if "## references" not in lower and "references" in REQUIRED_SECTIONS:
        issues.append({"severity": "warning", "issue": "missing_references_section", "detail": "missing ## References"})
    if "limitation" not in lower and "missing evidence" not in lower and "insufficient" not in lower:
        issues.append({"severity": "warning", "issue": "missing_uncertainty_language", "detail": "no limitation/uncertainty wording found"})

    evidence_backed_region = evidence_backed_text(answer_text)
    for index, paragraph in enumerate(nonempty_blocks(evidence_backed_region), start=1):
        if looks_like_content_block(paragraph) and not re.search(r"\bE\d{3}\b", paragraph):
            issues.append(
                {
                    "severity": "warning",
                    "issue": "uncited_evidence_statement",
                    "detail": f"block_{index}: {paragraph[:160]}",
                }
            )

    suspicious_phrases = [
        "not in the evidence shows",
        "not in the evidence demonstrates",
        "without evidence proves",
    ]
    for phrase in suspicious_phrases:
        if phrase in lower:
            issues.append({"severity": "warning", "issue": "suspicious_grounding_phrase", "detail": phrase})

    lexical_alignment = check_citation_text_alignment(answer_text, chunks)
    for alignment in lexical_alignment:
        if alignment["status"] != "mismatch":
            continue
        citation_text = ", ".join(alignment["evidence_ids"])
        issues.append(
            {
                "severity": "error",
                "issue": "citation_text_mismatch",
                "detail": (
                    f"block_{alignment['block_index']} cites {citation_text} but has no "
                    f"meaningful lexical overlap: {alignment['text'][:120]}"
                ),
            }
        )

    return {
        "passed": not any(issue["severity"] == "error" for issue in issues),
        "check_scope": GROUNDING_CHECK_SCOPE,
        "check_scope_note": GROUNDING_CHECK_NOTE,
        "known_evidence_ids": sorted(known_ids),
        "cited_evidence_ids": sorted(cited_ids),
        "lexical_alignment": lexical_alignment,
        "issues": issues,
        "n_issues": len(issues),
    }


def write_grounding_report(report: dict, out_md: Path, out_tsv: Path) -> None:
    out_md.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Answer Grounding Report",
        "",
        f"- Passed: {report.get('passed')}",
        f"- Check scope: {report.get('check_scope', GROUNDING_CHECK_SCOPE)}",
        f"- Scope note: {report.get('check_scope_note', GROUNDING_CHECK_NOTE)}",
        f"- Cited evidence IDs: {', '.join(report.get('cited_evidence_ids', [])) or 'none'}",
        f"- Known evidence IDs: {', '.join(report.get('known_evidence_ids', [])) or 'none'}",
        f"- Issues: {report.get('n_issues', 0)}",
        "",
    ]
    if report.get("issues"):
        lines.extend(["## Issues", ""])
        for issue in report["issues"]:
            lines.append(f"- {issue['severity']}: {issue['issue']} - {issue['detail']}")
        lines.append("")
    out_md.write_text("\n".join(lines), encoding="utf-8")

    with out_tsv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=ISSUE_FIELDS)
        writer.writeheader()
        for issue in report.get("issues", []):
            writer.writerow({field: issue.get(field, "") for field in ISSUE_FIELDS})


def load_and_check(answer_path: Path, evidence_pack_path: Path) -> dict:
    return check_answer_grounding(
        Path(answer_path).read_text(encoding="utf-8"),
        dict(read_json(Path(evidence_pack_path))),
    )


def evidence_backed_text(answer_text: str) -> str:
    text = answer_text or ""
    match = re.search(r"(?is)(evidence-backed statements|short answer|^)(.*?)(## references|## key sources|$)", text)
    return match.group(2) if match else text


def nonempty_blocks(text: str) -> list[str]:
    blocks = []
    for raw in re.split(r"\n\s*\n", text or ""):
        block = " ".join(line.strip("- ").strip() for line in raw.splitlines()).strip()
        if block:
            blocks.append(block)
    return blocks


def looks_like_content_block(block: str) -> bool:
    clean = block.strip()
    if not clean or clean.startswith("#") or clean.lower().startswith("references"):
        return False
    return len(clean.split()) >= 8


def check_citation_text_alignment(answer_text: str, chunks: list[dict]) -> list[dict]:
    """Conservatively compare direct cited claims with the cited source text.

    This is deliberately a lexical guardrail, not an entailment check.  It only
    emits a hard mismatch when a sufficiently substantive claim and its cited
    evidence share no meaningful normalized term at all.  Short labels,
    headings, citation-only lines, and evidence records without enough text are
    left unassessed rather than failed.
    """

    evidence_by_id = {
        str(chunk.get("evidence_id")): chunk
        for chunk in chunks
        if chunk.get("evidence_id")
    }
    alignments = []
    for block_index, block in enumerate(citation_claim_blocks(direct_evidence_text(answer_text)), start=1):
        evidence_ids = sorted(
            evidence_id
            for evidence_id in set(re.findall(r"\bE\d{3}\b", block))
            if evidence_id in evidence_by_id
        )
        if not evidence_ids:
            continue
        claim_terms = lexical_terms(block)
        cited_text = " ".join(
            f"{evidence_by_id[evidence_id].get('title', '')} "
            f"{evidence_by_id[evidence_id].get('text', '')}"
            for evidence_id in evidence_ids
        )
        evidence_terms = lexical_terms(cited_text)
        overlapping_terms = sorted(claim_terms & evidence_terms)
        assessed = (
            len(claim_terms) >= MIN_ALIGNMENT_TERMS
            and len(evidence_terms) >= MIN_ALIGNMENT_TERMS
        )
        alignments.append(
            {
                "block_index": block_index,
                "evidence_ids": evidence_ids,
                "status": (
                    "not_assessed"
                    if not assessed
                    else "aligned"
                    if overlapping_terms
                    else "mismatch"
                ),
                "overlapping_terms": overlapping_terms,
                "claim_term_count": len(claim_terms),
                "evidence_term_count": len(evidence_terms),
                "text": block,
            }
        )
    return alignments


def direct_evidence_text(answer_text: str) -> str:
    """Return explicit direct-evidence sections, or a legacy unheaded region."""

    text = answer_text or ""
    patterns = [
        re.compile(
            r"(?ims)^##\s*(?:evidence[-_ ]backed[-_ ]statements|short answer|"
            r"supported research gaps)\s*$\n(.*?)(?=^##\s|\Z)"
        ),
        re.compile(
            r"(?ims)^###\s*evidence[-_ ]backed[-_ ]statements\s*$\n"
            r"(.*?)(?=^#{2,3}\s|\Z)"
        ),
    ]
    matches = sorted(
        (match.start(), match.group(1).strip())
        for pattern in patterns
        for match in pattern.finditer(text)
    )
    if matches:
        return "\n\n".join(section for _, section in matches if section)
    return evidence_backed_text(text)


def citation_claim_blocks(text: str) -> list[str]:
    """Keep list items and table rows separate for source-alignment checks."""

    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        block = " ".join(current).strip()
        if block:
            blocks.append(block)
        current.clear()

    lines = [re.sub(r"^(?:\s*>\s*)+", "", line) for line in (text or "").splitlines()]
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if index + 1 < len(lines):
            header = _markdown_table_cells(line)
            separator = _markdown_table_cells(lines[index + 1].strip())
            if (
                header
                and separator
                and len(header) == len(separator)
                and all(re.fullmatch(r":?-+:?", cell) for cell in separator)
            ):
                flush()
                blocks.extend(cell for cell in header if re.search(r"\bE\d{3}\b", cell))
                index += 2
                while index < len(lines):
                    cells = _markdown_table_cells(lines[index].strip())
                    if not cells:
                        break
                    row = " ".join(cells).strip()
                    if not re.search(r"\bE\d{3}\b", row):
                        inherited_ids = sorted({
                            evidence_id
                            for column, cell in enumerate(cells)
                            if cell and column < len(header)
                            for evidence_id in re.findall(r"\bE\d{3}\b", header[column])
                        })
                        if inherited_ids:
                            row += f" ({', '.join(inherited_ids)})"
                    blocks.append(row)
                    index += 1
                continue
        index += 1
        if not line:
            flush()
            continue
        if line.startswith("#"):
            flush()
            continue
        if re.match(r"^(?:[-*+]|\d{1,9}[.)])\s+", line):
            flush()
            line = re.sub(r"^(?:[-*+]|\d{1,9}[.)])\s+", "", line)
        current.append(line)
    flush()
    return blocks


def _markdown_table_cells(line: str) -> list[str] | None:
    line = re.sub(r"^(?:[-*+]|\d{1,9}[.)])\s+(?=\|)", "", line)
    cells: list[str] = []
    start = 0
    code_marker = ""
    for match in re.finditer(r"\\.|`+|\|", line):
        token = match.group()
        if token.startswith("\\"):
            continue
        if token.startswith("`"):
            if token == code_marker:
                code_marker = ""
            elif not code_marker:
                code_marker = token
            continue
        if not code_marker:
            cells.append(line[start : match.start()].strip())
            start = match.end()
    if not cells:
        return None
    cells.append(line[start:].strip())
    if not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    return cells or None


def lexical_terms(text: str) -> set[str]:
    """Normalize content terms for a transparent overlap-only heuristic."""

    raw_terms = re.findall(r"[^\W_]+", (text or "").lower(), flags=re.UNICODE)
    terms = set()
    for raw_term in raw_terms:
        if re.fullmatch(r"e\d{3}", raw_term) or raw_term in LEXICAL_STOPWORDS:
            continue
        term = normalize_lexical_term(raw_term)
        if len(term) >= 3 and term not in LEXICAL_STOPWORDS:
            terms.add(term)
    return terms


def normalize_lexical_term(term: str) -> str:
    """Apply small, intentionally conservative English inflection folding."""

    if len(term) > 5 and term.endswith("ies"):
        return f"{term[:-3]}y"
    if len(term) > 6 and term.endswith("ing"):
        stem = term[:-3]
        return stem[:-1] if len(stem) > 3 and stem[-1:] == stem[-2:-1] else stem
    if len(term) > 5 and term.endswith("ed"):
        return term[:-2]
    if len(term) > 4 and term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term
