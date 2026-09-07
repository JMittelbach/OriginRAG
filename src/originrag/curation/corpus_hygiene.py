from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from originrag.curation.relevance_policy import relevance_decision
from originrag.retrieval.chunk_quality import classify_chunk_quality
from originrag.retrieval.section_detection import (
    carries_forward_section,
    detect_section_for_chunk,
    stored_section_needs_redetection,
)


KEEP_ACTIONS = {"keep_core", "keep_methods"}
QUARANTINE_ACTIONS = {"quarantine_duplicate", "quarantine_corrupt"}
USEFUL_SECTIONS = {"abstract", "introduction", "results", "discussion", "conclusion", "methods"}
PROBLEM_SECTIONS = {"references", "table", "figure_legend", "supplement"}
PLACEHOLDER_TITLE_MARKERS = (
    "reporting summary",
    "supplementary information",
    "contents lists available",
    "microsoft word",
    "europe pmc funders group",
    "the online version of this article",
    "the following resources related to this article",
    "this copy is for your personal",
    "this information is current as of",
    "updated information and services can be found",
    "this peer-reviewed article was published",
    "article is distributed under the",
    "authors contributed equally",
    "contributed by ",
    "nature methods doi ",
    "data supplement",
)
PLACEHOLDER_TITLE_PREFIXES = (
    "accepted ",
    "background:",
    "doi:",
    "in the format provided by",
    "onlineopen:",
    "published online ",
    "this article cites ",
)


@dataclass(frozen=True)
class PaperChunkStats:
    total_chunks: int = 0
    useful_chunks: int = 0
    problem_chunks: int = 0
    unknown_chunks: int = 0
    low_quality_chunks: int = 0
    empty_chunks: int = 0
    median_word_count: float = 0.0

    @property
    def useful_fraction(self) -> float:
        return _fraction(self.useful_chunks, self.total_chunks)

    @property
    def problem_fraction(self) -> float:
        return _fraction(self.problem_chunks, self.total_chunks)

    @property
    def unknown_fraction(self) -> float:
        return _fraction(self.unknown_chunks, self.total_chunks)

    @property
    def low_quality_fraction(self) -> float:
        return _fraction(self.low_quality_chunks, self.total_chunks)


@dataclass(frozen=True)
class HygieneAssessment:
    paper_id: str
    recommended_action: str
    confidence: str
    reasons: tuple[str, ...]
    duplicate_of: str
    domain: str
    topics: str
    fulltext_status: str
    title: str
    doi: str
    pmid: str
    pmcid: str
    local_pdf_relpath: str
    total_chunks: int
    useful_fraction: float
    problem_fraction: float
    unknown_fraction: float
    low_quality_fraction: float
    median_word_count: float

    def to_row(self) -> dict[str, str]:
        row = asdict(self)
        row["reasons"] = ";".join(self.reasons)
        for field in (
            "useful_fraction",
            "problem_fraction",
            "unknown_fraction",
            "low_quality_fraction",
            "median_word_count",
        ):
            row[field] = f"{float(row[field]):.3f}"
        row["total_chunks"] = str(self.total_chunks)
        return {key: str(value) for key, value in row.items()}


def collect_chunk_stats(chunks_dir: Path) -> dict[str, PaperChunkStats]:
    """Summarize extraction quality without changing chunks or PDFs."""
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    word_counts: dict[str, list[int]] = defaultdict(list)

    for chunk_file in sorted(chunks_dir.glob("*.jsonl")):
        previous_section: str | None = None
        with chunk_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                paper_id = str(chunk.get("paper_id") or chunk_file.stem)
                text = str(chunk.get("text") or "")
                if not stored_section_needs_redetection(chunk):
                    section = normalize_section(str(chunk.get("section") or "unknown"))
                else:
                    section = detect_section_for_chunk(text, previous_section=previous_section)["section"]
                if carries_forward_section(section):
                    previous_section = section
                quality = classify_chunk_quality(text)
                counts[paper_id]["total"] += 1
                word_counts[paper_id].append(int(quality["word_count"]))
                if section in USEFUL_SECTIONS:
                    counts[paper_id]["useful"] += 1



                if (
                    section in PROBLEM_SECTIONS
                    or quality["is_reference_like"]
                    or (quality["is_table_like"] and quality["is_low_text_quality"])
                ):
                    counts[paper_id]["problem"] += 1
                if section == "unknown":
                    counts[paper_id]["unknown"] += 1
                if quality["is_low_text_quality"]:
                    counts[paper_id]["low_quality"] += 1
                if quality["is_empty"]:
                    counts[paper_id]["empty"] += 1

    return {
        paper_id: PaperChunkStats(
            total_chunks=paper_counts["total"],
            useful_chunks=paper_counts["useful"],
            problem_chunks=paper_counts["problem"],
            unknown_chunks=paper_counts["unknown"],
            low_quality_chunks=paper_counts["low_quality"],
            empty_chunks=paper_counts["empty"],
            median_word_count=_median(word_counts[paper_id]),
        )
        for paper_id, paper_counts in counts.items()
    }


def find_exact_duplicates(
    papers: list[dict[str, str]],
    chunk_stats: dict[str, PaperChunkStats],
) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Return duplicate -> canonical mappings from exact publication identifiers."""
    parent = {str(row.get("paper_id") or ""): str(row.get("paper_id") or "") for row in papers}
    signals: dict[frozenset[str], set[str]] = defaultdict(set)

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[max(root_a, root_b)] = min(root_a, root_b)

    for field, normalizer in (
        ("doi", normalize_doi),
        ("pmid", normalize_identifier),
        ("pmcid", normalize_identifier),
        ("title", normalize_title),
    ):
        groups: dict[str, list[str]] = defaultdict(list)
        for row in papers:
            paper_id = str(row.get("paper_id") or "")
            value = normalizer(str(row.get(field) or ""))
            if paper_id and value and (
                field != "title" or (len(value) >= 40 and not title_is_placeholder(str(row.get("title") or "")))
            ):
                groups[value].append(paper_id)
        for members in groups.values():
            if len(members) < 2:
                continue
            anchor = members[0]
            for member in members[1:]:
                union(anchor, member)
                signals[frozenset((anchor, member))].add(f"exact_{field}")

    components: dict[str, list[str]] = defaultdict(list)
    for paper_id in parent:
        components[find(paper_id)].append(paper_id)

    rows_by_id = {str(row.get("paper_id") or ""): row for row in papers}
    duplicate_map: dict[str, tuple[str, tuple[str, ...]]] = {}
    for members in components.values():
        if len(members) < 2:
            continue
        canonical = max(members, key=lambda item: canonical_score(rows_by_id[item], chunk_stats.get(item)))
        for member in members:
            if member == canonical:
                continue
            component_signals = sorted(
                signal
                for pair, pair_signals in signals.items()
                if member in pair and pair.issubset(set(members))
                for signal in pair_signals
            )
            duplicate_map[member] = (canonical, tuple(component_signals or ["exact_publication_match"]))
    return duplicate_map


def assess_paper(
    paper: dict[str, str],
    stats: PaperChunkStats | None = None,
    duplicate: tuple[str, tuple[str, ...]] | None = None,
) -> HygieneAssessment:
    stats = stats or PaperChunkStats()
    paper_id = str(paper.get("paper_id") or "")
    title = str(paper.get("title") or "").strip()
    status = str(paper.get("fulltext_status") or "").strip()
    domain = str(paper.get("domain") or "").strip()
    topics = str(paper.get("topics") or "").strip()
    reasons: list[str] = []
    duplicate_of = ""

    if duplicate:
        duplicate_of, duplicate_signals = duplicate
        reasons.extend(duplicate_signals)
        action, confidence = "quarantine_duplicate", "high"
    elif status == "duplicate":
        action, confidence = "quarantine_duplicate", "high"
        reasons.append("fulltext_status_duplicate")
    elif stats.total_chunks and stats.empty_chunks == stats.total_chunks:
        action, confidence = "quarantine_corrupt", "high"
        reasons.append("all_chunks_empty")
    elif status == "chunked" and stats.total_chunks == 0:
        action, confidence = "review_extraction", "high"
        reasons.append("chunked_status_without_chunks")
    elif title_is_placeholder(title):
        action, confidence = "review_metadata", "high"
        reasons.append("malformed_or_boilerplate_title")
    elif stats.total_chunks >= 3 and stats.low_quality_fraction >= 0.80:
        action, confidence = "review_extraction", "medium"
        reasons.append("mostly_low_text_quality")
    elif stats.total_chunks >= 3 and stats.problem_fraction >= 0.80:
        action, confidence = "review_extraction", "medium"
        reasons.append("mostly_reference_table_figure_or_supplement")
    elif domain in {"single_cell_bioinformatics", "bioinformatics"}:
        action, confidence = "keep_methods", "high"
        reasons.append("methods_or_computational_collection")
    else:
        context = " ".join(
            str(paper.get(field) or "")
            for field in ("topics", "methods", "systems", "paper_type", "notes")
        )
        scope = relevance_decision(
            title=title,
            category=domain,
            query=context,
        )
        if not scope.keep:
            action, confidence = "review_scope", "medium"
            reasons.extend(scope.reasons)
        else:
            action, confidence = "keep_core", "high"
            reasons.append("within_configured_scientific_scope")

    return HygieneAssessment(
        paper_id=paper_id,
        recommended_action=action,
        confidence=confidence,
        reasons=tuple(dict.fromkeys(reasons)),
        duplicate_of=duplicate_of,
        domain=domain,
        topics=topics,
        fulltext_status=status,
        title=title,
        doi=str(paper.get("doi") or ""),
        pmid=str(paper.get("pmid") or ""),
        pmcid=str(paper.get("pmcid") or ""),
        local_pdf_relpath=str(paper.get("local_pdf_relpath") or ""),
        total_chunks=stats.total_chunks,
        useful_fraction=stats.useful_fraction,
        problem_fraction=stats.problem_fraction,
        unknown_fraction=stats.unknown_fraction,
        low_quality_fraction=stats.low_quality_fraction,
        median_word_count=stats.median_word_count,
    )


def canonical_score(row: dict[str, str], stats: PaperChunkStats | None) -> tuple[int, int, int, int, int, int, str]:
    stats = stats or PaperChunkStats()
    status_score = {"chunked": 4, "extracted": 3, "pdf_available": 2, "duplicate": 0}.get(
        str(row.get("fulltext_status") or ""), 1
    )
    metadata_score = sum(bool(str(row.get(field) or "").strip()) for field in ("doi", "pmid", "pmcid", "title"))
    return (
        status_score,
        round((1.0 - stats.problem_fraction) * 1000),
        round((1.0 - stats.low_quality_fraction) * 1000),
        stats.useful_chunks,
        stats.total_chunks,
        metadata_score,
        str(row.get("paper_id") or ""),
    )


def normalize_section(value: str) -> str:
    section = value.strip().lower().replace(" ", "_")
    aliases = {"figure": "figure_legend", "figures": "figure_legend", "reference": "references"}
    return aliases.get(section, section or "unknown")


def normalize_doi(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)
    return value.rstrip(" .")


def normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def title_is_placeholder(title: str) -> bool:
    normalized = title.lower().strip()
    if not normalized:
        return True
    if any(marker in normalized for marker in PLACEHOLDER_TITLE_MARKERS):
        return True
    if any(normalized.startswith(prefix) for prefix in PLACEHOLDER_TITLE_PREFIXES):
        return True
    if re.search(r"\bet al\.,?\s+20\d{2}\s+elife\b", normalized):
        return True
    if re.search(r"\bvolume\s+\d+\s+number\s+\d+\b", normalized):
        return True
    return False


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[midpoint])
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _fraction(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
