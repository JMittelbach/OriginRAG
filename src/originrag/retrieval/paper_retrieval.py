from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from originrag.provenance.library import normalize_doi
from originrag.retrieval.keyword import (
    contains_normalized_term,
    infer_query_category_boosts,
    normalize_text,
    query_tokens,
)
from originrag.retrieval.metadata import load_paper_metadata


PAPER_FIELDS = [
    "paper_id",
    "score",
    "author",
    "year",
    "title",
    "journal",
    "category",
    "topics",
    "rationale",
    "doi",
    "pmid",
    "pmcid",
    "bib_key",
    "citation_short",
    "citation_apa",
    "domain",
    "methods",
    "systems",
    "paper_type",
    "fulltext_status",
    "local_fulltext_available",
    "local_pdf_relpath",
]

DOI_QUERY_PATTERN = re.compile(
    r"10\.\d{4,9}/[^\s<>\]\[{}\"']+",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class _PaperQueryFeatures:
    normalized_query: str
    dois: frozenset[str]
    pmids: frozenset[str]
    pmcids: frozenset[str]
    bib_keys: frozenset[str]


def search_papers(
    query: str,
    papers_tsv: Path,
    limit: int = 20,
    category: str | None = None,
    task_mode: str = "evidence_qa",
    local_pdf_root: Path | None = None,
    chunks_dir: Path | None = None,
) -> list[dict]:
    rows = read_metadata_rows(Path(papers_tsv))
    tokens = query_tokens(query)
    boosts = infer_query_category_boosts(query)
    query_features = _prepare_query_features(query)
    results = []
    for row in rows:
        if category and category not in normalize_text(
            " ".join([row.get("category", ""), row.get("topics", "")])
        ):
            continue
        local_fulltext_available = current_fulltext_available(
            row,
            local_pdf_root=local_pdf_root,
            chunks_dir=chunks_dir,
        )
        score, rationale = _score_paper_prepared(
            row,
            query_features,
            tokens,
            boosts,
            task_mode,
            local_fulltext_available=local_fulltext_available,
        )
        if score <= 0:
            continue
        results.append(
            {
                "paper_id": row.get("paper_id", ""),
                "score": round(score, 3),
                "author": row.get("author", ""),
                "year": row.get("year", ""),
                "title": row.get("title", ""),
                "journal": row.get("journal", ""),
                "category": row.get("category", ""),
                "topics": row.get("topics", ""),
                "rationale": "; ".join(rationale),
                "local_fulltext_available": (
                    ""
                    if local_fulltext_available is None
                    else str(local_fulltext_available).lower()
                ),
                **{
                    field: row.get(field, "")
                    for field in (
                        "doi",
                        "pmid",
                        "pmcid",
                        "bib_key",
                        "citation_short",
                        "citation_apa",
                        "domain",
                        "methods",
                        "systems",
                        "paper_type",
                        "fulltext_status",
                        "local_pdf_relpath",
                    )
                },
            }
        )
    results.sort(key=lambda item: (-float(item["score"]), -safe_year(item.get("year")), item.get("paper_id", "")))
    for rank, result in enumerate(results[:limit], start=1):
        result["rank"] = rank
    return results[:limit]


def score_paper(
    row: dict[str, str],
    query: str,
    tokens: list[str],
    category_boosts: dict[str, float],
    task_mode: str,
    local_fulltext_available: bool | None = None,
) -> tuple[float, list[str]]:
    return _score_paper_prepared(
        row, _prepare_query_features(query), tokens, category_boosts, task_mode,
        local_fulltext_available=local_fulltext_available,
    )


def _prepare_query_features(query: str) -> _PaperQueryFeatures:
    explicit_query_dois = frozenset(
        doi
        for match in DOI_QUERY_PATTERN.finditer(query or "")
        if (doi := normalize_doi(match.group(0).rstrip(".:;!?")))
    )
    explicit_query_pmids = frozenset(
        re.findall(r"\bpmid\s*[:#]?\s*(\d+)\b", query or "", flags=re.IGNORECASE)
    )
    explicit_query_pmcids = frozenset(
        value.upper()
        for value in re.findall(r"\b(PMC\d+)\b", query or "", flags=re.IGNORECASE)
    )
    explicit_query_bib_keys = frozenset(
        normalize_text(value)
        for value in re.findall(
            r"\b(?:bib(?:liography)?\s*key|citation\s*key|paper\s*id)"
            r"\s*[:#]?\s*([A-Za-z0-9_.:-]+)",
            query or "",
            flags=re.IGNORECASE,
        )
    )
    return _PaperQueryFeatures(
        normalize_text(query), explicit_query_dois, explicit_query_pmids,
        explicit_query_pmcids, explicit_query_bib_keys,
    )


def _score_paper_prepared(
    row: dict[str, str],
    query_features: _PaperQueryFeatures,
    tokens: list[str],
    category_boosts: dict[str, float],
    task_mode: str,
    local_fulltext_available: bool | None = None,
) -> tuple[float, list[str]]:
    normalized_query = query_features.normalized_query
    title = normalize_text(row.get("title", ""))
    metadata_text = normalize_text(
        " ".join(
            str(row.get(field, ""))
            for field in [
                "title",
                "author",
                "year",
                "journal",
                "bib_key",
                "doi",
                "pmid",
                "pmcid",
                "citation_short",
                "citation_apa",
                "category",
                "topics",
                "methods",
                "systems",
                "notes",
                "paper_type",
                "activation_status",
                "fulltext_status",
            ]
        )
    )
    if not metadata_text:
        return 0.0, []

    score = 0.0
    rationale: list[str] = []
    for field, label in [
        ("doi", "DOI"),
        ("pmid", "PMID"),
        ("pmcid", "PMCID"),
        ("bib_key", "BibKey"),
    ]:
        identifier = str(row.get(field, "") or "").strip()
        if field == "doi":
            exact_match = normalize_doi(identifier) in query_features.dois
        elif field == "pmid":
            exact_match = identifier in query_features.pmids
        elif field == "pmcid":
            exact_match = identifier.upper() in query_features.pmcids
        else:
            exact_match = normalize_text(identifier) in query_features.bib_keys
        if exact_match:
            score += 60.0
            rationale.append(f"exact {label} match")
    if normalized_query and normalized_query in title:
        score += 25.0
        rationale.append("exact query phrase in title")
    elif normalized_query and normalized_query in metadata_text:
        score += 10.0
        rationale.append("exact query phrase in metadata")

    matched = [
        token for token in tokens if contains_normalized_term(metadata_text, token)
    ]
    if matched:
        score += min(25.0, len(matched) * 3.0)
        rationale.append(f"matched terms: {', '.join(matched[:8])}")

    category_text = normalize_text(" ".join([row.get("category", ""), row.get("topics", "")]))
    for boost_category, boost in category_boosts.items():
        if normalize_text(boost_category) in category_text:
            score += boost
            rationale.append(f"category/topic boost: {boost_category}")

    if task_mode == "dataset_finder" and any(term in metadata_text for term in ["dataset", "cohort", "rna seq", "transcript"]):
        score += 4.0
        rationale.append("dataset/method metadata")
    if task_mode == "method_comparison" and any(term in metadata_text for term in ["method", "tool", "pipeline"]):
        score += 4.0
        rationale.append("method/tool metadata")
    if score <= 0:




        return 0.0, []
    if row.get("activation_status") in {"active_mvp", "active_full", "candidate"}:
        score += 2.0
        rationale.append("curated corpus status")
    metadata_reports_fulltext = row.get("fulltext_status") in {
        "chunked",
        "extracted",
        "pdf_available",
    }
    if (
        local_fulltext_available is True
        or (local_fulltext_available is None and metadata_reports_fulltext)
    ):
        score += 1.0
        rationale.append("local full text available")

    year = safe_year(row.get("year"))
    if year:
        score += min(1.5, max(0, year - 2000) / 30.0)
    return score, rationale or ["metadata term overlap"]


def read_metadata_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    raw_rows = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if row.get("paper_id"):
                raw_rows[row["paper_id"]] = dict(row)
    enriched = load_paper_metadata(path)
    return [
        {**raw_rows.get(paper_id, {}), **{field: value for field, value in record.items() if value}}
        for paper_id, record in enriched.items()
    ]


def safe_year(value: object) -> int:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return 0


def current_fulltext_available(
    row: dict[str, str],
    *,
    local_pdf_root: Path | None,
    chunks_dir: Path | None,
) -> bool | None:
    """Return current local availability, or ``None`` when it was not checked."""
    if local_pdf_root is None and chunks_dir is None:
        return None
    paper_id = str(row.get("paper_id") or "").strip()
    if chunks_dir is not None and paper_id:
        if (Path(chunks_dir) / f"{paper_id}.jsonl").is_file():
            return True
    relative_pdf = str(row.get("local_pdf_relpath") or "").strip()
    if local_pdf_root is not None and relative_pdf:
        root = Path(local_pdf_root).resolve()
        candidate = (root / relative_pdf).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return True
    return False
