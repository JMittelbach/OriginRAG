from __future__ import annotations

import csv
from pathlib import Path

from pfempire.analysis.template_answers import evidence_contribution


EVIDENCE_TABLE_FIELDS = [
    "evidence_id",
    "paper_id",
    "bib_key",
    "citation_short",
    "citation_apa",
    "author",
    "year",
    "doi",
    "pmid",
    "pmcid",
    "title",
    "journal",
    "section",
    "page_range",
    "chunk_id",
    "score",
    "quality_flags",
    "contribution",
]


def build_evidence_table_rows(evidence_pack: dict) -> list[dict]:
    rows = []
    for chunk in evidence_pack.get("chunks", []):
        rows.append(
            {
                "evidence_id": str(chunk.get("evidence_id") or ""),
                "paper_id": str(chunk.get("paper_id") or ""),
                "bib_key": str(chunk.get("bib_key") or ""),
                "citation_short": str(chunk.get("citation_short") or ""),
                "citation_apa": str(chunk.get("citation_apa") or ""),
                "author": str(chunk.get("author") or ""),
                "year": str(chunk.get("year") or ""),
                "doi": str(chunk.get("doi") or ""),
                "pmid": str(chunk.get("pmid") or ""),
                "pmcid": str(chunk.get("pmcid") or ""),
                "title": str(chunk.get("title") or ""),
                "journal": str(chunk.get("journal") or ""),
                "section": str(chunk.get("section") or ""),
                "page_range": str(chunk.get("page_range") or ""),
                "chunk_id": str(chunk.get("chunk_id") or ""),
                "score": str(chunk.get("score") or ""),
                "quality_flags": ";".join(chunk.get("quality_flags") or []),
                "contribution": evidence_contribution(chunk),
            }
        )
    return rows


def write_evidence_table_tsv(path: Path, evidence_pack: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = build_evidence_table_rows(evidence_pack)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=EVIDENCE_TABLE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def format_evidence_table_markdown(evidence_pack: dict) -> str:
    rows = build_evidence_table_rows(evidence_pack)
    lines = [
        "# Evidence Table",
        "",
        f"Question: {evidence_pack.get('question', '')}",
        "",
        "| Evidence | Paper | Section | Contribution |",
        "|---|---|---|---|",
    ]
    for row in rows:
        paper = row["citation_short"] or " ".join(part for part in [row["author"], row["year"]] if part)
        paper = paper or row["paper_id"] or "Unknown"
        lines.append(
            f"| {row['evidence_id']} | {paper} | {row['section']} | {row['contribution']} |"
        )
    references = dedupe_preserve_order(row["citation_apa"] for row in rows if row["citation_apa"])
    if references:
        lines.extend(["", "## References", ""])
        lines.extend(f"- {reference}" for reference in references)
    return "\n".join(lines) + "\n"


def dedupe_preserve_order(values: list[str] | object) -> list[str]:
    seen = set()
    deduped = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped
