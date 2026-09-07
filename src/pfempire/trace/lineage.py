from __future__ import annotations

import itertools
import re
from collections import defaultdict
from typing import Any

from rapidfuzz.fuzz import ratio

from pfempire.trace.dependence import build_edge
from pfempire.trace.schemas import EvidenceLineageEdge, LineageSignal


ACCESSION_PATTERNS = {
    "geo": r"\bGSE\d+\b",
    "sra": r"\b(?:SRP|SRA|SRR|ERR|DRR|DRP)\d+\b",
    "bioproject": r"\bPRJ[NE][AB]\d+\b",
    "ena": r"\b(?:ERP|E-MTAB-)\d+\b",
    "dbgap": r"\bPHS\d+(?:\.V\d+\.P\d+)?\b",
    "nct": r"\bNCT\d{8}\b",
    "isrctn": r"\bISRCTN\d+\b",
    "pactr": r"\bPACTR\d+\b",
}

GENERIC_COHORT_LABELS = {
    "CASE", "CASES", "CHMI", "CLINICAL", "COHORT", "CONTROL", "CONTROLS",
    "MALARIA", "PATIENT", "PATIENTS", "PROSPECTIVE", "RETROSPECTIVE",
    "STUDY", "THE", "THIS", "TRIAL",
}

COHORT_PATTERNS = [
    r"\b([A-Z][A-Z0-9_-]{2,24})\s+(?:cohort|trial|study)\b",
    r"\b(?:cohort|trial|study)\s+(?:called|named)\s+([A-Za-z][A-Za-z0-9_-]{2,24})\b",
]

OWN_DATA_SECTIONS = {
    "data availability",
    "data_availability",
    "materials and methods",
    "methods",
    "participants",
    "patients",
    "results",
    "study population",
}
BACKGROUND_SECTIONS = {
    "background",
    "bibliography",
    "discussion",
    "introduction",
    "references",
}
SECTION_HEADING_RE = re.compile(
    r"(?im)^\s*(abstract|background|introduction|materials\s+and\s+methods|methods|"
    r"participants|patients|study\s+population|results|discussion|data\s+availability|"
    r"references|bibliography)\s*[:.]?\s*$"
)
OWN_DATA_PATTERNS = [
    re.compile(
        r"\bwe\b.{0,100}\b(?:analysed|analyzed|collected|deposited|downloaded|enrolled|"
        r"generated|obtained|recruited|registered|submitted|used)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:our|the present|the current|this)\s+(?:analysis|cohort|data(?:set)?|patients|"
        r"participants|samples|study|trial)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:data|reads|sequences?|sequencing data)\b.{0,100}\b(?:accession|available|"
        r"deposited|submitted)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:patients|participants|samples|subjects)\b.{0,100}\b(?:collected|enrolled|"
        r"obtained|recruited)\b",
        re.IGNORECASE,
    ),
]
BACKGROUND_PATTERNS = [
    re.compile(
        r"\b(?:as previously|described by|previous(?:ly)? reported|previous study|prior study|"
        r"reported by|study by)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b[A-Z][A-Za-z-]+\s+et\s+al\.", re.IGNORECASE),
]

DATA_REUSE_PATTERN = re.compile(
    r"\b(?:published\s+)?(?:data(?:set)?s?|profiles?|samples?|isolates?|sequences?|"
    r"transcripts?)\b.{0,180}\b(?:were\s+used|was\s+used|used\s+(?:for|in)\s+this\s+"
    r"study|previously\s+(?:described|published|reported|generated)|published\s+"
    r"previously|obtained\s+from|derived\s+from)\b.{0,220}",
    re.IGNORECASE | re.DOTALL,
)
AUTHOR_YEAR_PATTERN = re.compile(
    r"\b([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+)\s+et\s+al\.?\s*[,;(]?\s*"
    r"((?:19|20)\d{2})\b",
    re.IGNORECASE,
)
NUMERIC_CITATION_PATTERN = re.compile(r"\[([0-9,;\s–-]+)\]")
NUMBERED_REFERENCE_PATTERN = re.compile(
    r"(?m)^\s*(\d{1,3})[.)]\s+([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+)\b"
    r".{0,220}?\b((?:19|20)\d{2})\b",
)


def extract_identifiers(text: str) -> dict[str, list[str]]:
    return {
        kind: sorted({match.upper() for match in re.findall(pattern, text or "", flags=re.IGNORECASE)})
        for kind, pattern in ACCESSION_PATTERNS.items()
    }


def extract_named_cohorts(text: str) -> list[str]:
    """Extract explicit cohort/trial labels, never generic disease descriptions."""
    labels = {
        match.upper()
        for pattern in COHORT_PATTERNS
        for match in re.findall(pattern, text or "", flags=re.IGNORECASE if "called|named" in pattern else 0)
    }
    return sorted(label for label in labels if label not in GENERIC_COHORT_LABELS)


def extract_identifier_mentions(document: str | list[dict[str, Any]]) -> dict[str, dict[str, list[dict]]]:
    """Extract origin identifiers with stable document locations and mention roles."""

    mentions: dict[str, dict[str, list[dict]]] = {
        kind: defaultdict(list) for kind in ACCESSION_PATTERNS
    }
    for chunk in _document_chunks(document):
        text = str(chunk.get("text") or "")
        for kind, pattern in ACCESSION_PATTERNS.items():
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                value = match.group(0).upper()
                mentions[kind][value].append(
                    _mention_record(chunk, text, match.start(), match.end(), value)
                )
    return {
        kind: {value: records for value, records in sorted(values.items())}
        for kind, values in mentions.items()
    }


def extract_named_cohort_mentions(
    document: str | list[dict[str, Any]],
) -> dict[str, list[dict]]:
    """Extract named cohort mentions with the same contextual audit trail."""

    mentions: dict[str, list[dict]] = defaultdict(list)
    for chunk in _document_chunks(document):
        text = str(chunk.get("text") or "")
        for pattern in COHORT_PATTERNS:
            flags = re.IGNORECASE if "called|named" in pattern else 0
            for match in re.finditer(pattern, text, flags=flags):
                label = match.group(1).upper()
                if label in GENERIC_COHORT_LABELS:
                    continue
                mentions[label].append(
                    _mention_record(chunk, text, match.start(1), match.end(1), label)
                )
    return {label: records for label, records in sorted(mentions.items())}


def extract_data_origin_mentions(
    document: str | list[dict[str, Any]],
) -> dict[str, list[dict]]:
    """Extract cited origins only from explicit data/sample reuse statements.

    This deliberately ignores ordinary background citations. It supports both
    author-year citations and numbered references resolved from the same local
    document, which is common in biomedical full text.
    """

    chunks = _document_chunks(document)
    full_text = "\n".join(str(chunk.get("text") or "") for chunk in chunks)
    numbered_references = {
        match.group(1): _author_year_key(match.group(2), match.group(3))
        for match in NUMBERED_REFERENCE_PATTERN.finditer(full_text)
    }
    mentions: dict[str, list[dict]] = defaultdict(list)
    for chunk in chunks:
        text = str(chunk.get("text") or "")
        for reuse in DATA_REUSE_PATTERN.finditer(text):
            excerpt = reuse.group(0)
            keys = {
                _author_year_key(match.group(1), match.group(2))
                for match in AUTHOR_YEAR_PATTERN.finditer(excerpt)
            }
            for numeric in NUMERIC_CITATION_PATTERN.finditer(excerpt):
                for number in re.findall(r"\d+", numeric.group(1)):
                    if number in numbered_references:
                        keys.add(numbered_references[number])
            for key in sorted(keys):
                mentions[key].append(
                    {
                        "chunk_id": str(chunk.get("chunk_id") or ""),
                        "section": str(chunk.get("section") or "unknown"),
                        "page": chunk.get("page") or chunk.get("page_start"),
                        "excerpt": " ".join(excerpt.split())[:500],
                        "role": "own_data_reuse_statement",
                        "substantive_data_reuse": _is_substantive_data_reuse(excerpt),
                    }
                )
    return {key: records for key, records in sorted(mentions.items())}


def suggest_lineage_signals(
    papers: list[dict],
    paper_text: dict[str, str | list[dict[str, Any]]] | None = None,
) -> list[LineageSignal]:
    paper_text = paper_text or {}
    signals: list[LineageSignal] = []
    identifier_mentions: dict[str, dict[str, dict[str, list[dict]]]] = {}
    cohort_mentions: dict[str, dict[str, list[dict]]] = {}
    data_origin_mentions: dict[str, dict[str, list[dict]]] = {}
    for paper in papers:
        paper_id = str(paper.get("paper_id"))
        document = [
            {
                "chunk_id": f"{paper_id}_TITLE",
                "section": "title",
                "text": str(paper.get("title") or ""),
            },
            *_document_chunks(paper_text.get(paper_id, "")),
        ]
        identifier_mentions[paper_id] = extract_identifier_mentions(document)
        cohort_mentions[paper_id] = extract_named_cohort_mentions(document)
        data_origin_mentions[paper_id] = extract_data_origin_mentions(document)
    for a, b in itertools.combinations(sorted(papers, key=lambda row: str(row.get("paper_id"))), 2):
        pa, pb = str(a.get("paper_id")), str(b.get("paper_id"))
        pair_signals: list[tuple[str, float, dict, str]] = []
        doi_a, doi_b = normalize_doi(str(a.get("doi", ""))), normalize_doi(str(b.get("doi", "")))
        if doi_a and doi_a == doi_b:
            pair_signals.append(("same_doi_family", 1.0, {"doi": doi_a}, "deterministic_metadata"))
        elif doi_a and doi_b and elife_doi_family(doi_a) == elife_doi_family(doi_b):
            family = elife_doi_family(doi_a)
            if family:
                pair_signals.append(
                    (
                        "same_publication_version",
                        1.0,
                        {"identifier_type": "elife_doi_family", "identifier": family},
                        "deterministic_metadata",
                    )
                )
        for identifier in ("pmcid", "pmid"):
            value_a = str(a.get(identifier, "")).strip().upper()
            value_b = str(b.get(identifier, "")).strip().upper()
            if value_a and value_a == value_b:
                pair_signals.append(
                    (
                        "same_publication_version",
                        1.0,
                        {"identifier_type": identifier, "identifier": value_a},
                        "deterministic_metadata",
                    )
                )
        title_a, title_b = normalize_title(str(a.get("title", ""))), normalize_title(str(b.get("title", "")))
        title_score = ratio(title_a, title_b) / 100.0
        if title_a and title_a == title_b:
            if is_biorxiv_medrxiv_doi(doi_a) != is_biorxiv_medrxiv_doi(doi_b):
                pair_signals.append(
                    (
                        "same_preprint_family",
                        1.0,
                        {"normalized_title": title_a, "dois": sorted([doi_a, doi_b])},
                        "deterministic_metadata",
                    )
                )
            else:



                pair_signals.append(
                    (
                        "high_title_similarity",
                        1.0,
                        {"normalized_title": title_a},
                        "deterministic_metadata",
                    )
                )
        elif likely_preprint_journal_title_pair(title_a, title_b, doi_a, doi_b):
            pair_signals.append(
                (
                    "same_preprint_family",
                    0.95,
                    {
                        "match_type": "long_title_containment",
                        "titles": sorted([title_a, title_b]),
                        "dois": sorted([doi_a, doi_b]),
                    },
                    "deterministic_metadata",
                )
            )
        elif title_score >= 0.90:
            pair_signals.append(
                (
                    "high_title_similarity",
                    title_score,
                    {"ratio": title_score},
                    "deterministic_metadata",
                )
            )
        for kind in ACCESSION_PATTERNS:
            values_a = identifier_mentions[pa][kind]
            values_b = identifier_mentions[pb][kind]
            shared = sorted(set(values_a) & set(values_b))
            if not shared:
                continue
            signal_type = (
                "same_trial_id"
                if kind in {"nct", "isrctn", "pactr"}
                else "same_dataset_accession"
            )
            strong = [
                value
                for value in shared
                if _has_own_data_mention(values_a[value])
                and _has_own_data_mention(values_b[value])
            ]
            weak = [value for value in shared if value not in strong]
            if strong:
                pair_signals.append(
                    (
                        signal_type,
                        1.0,
                        _shared_mention_details(
                            pa, pb, kind, strong, values_a, values_b, review_required=True
                        ),
                        "deterministic_contextual_identifier",
                    )
                )
            if weak:
                details = _shared_mention_details(
                    pa, pb, kind, weak, values_a, values_b, review_required=True
                )
                details["candidate_signal_type"] = signal_type
                pair_signals.append(
                    (
                        "shared_identifier_mention",
                        0.25,
                        details,
                        "deterministic_contextual_identifier",
                    )
                )
        labels_a = cohort_mentions[pa]
        labels_b = cohort_mentions[pb]
        shared_cohorts = sorted(set(labels_a) & set(labels_b))
        strong_cohorts = [
            label
            for label in shared_cohorts
            if _has_own_data_mention(labels_a[label])
            and _has_own_data_mention(labels_b[label])
        ]
        weak_cohorts = [label for label in shared_cohorts if label not in strong_cohorts]
        if strong_cohorts:
            details = _shared_mention_details(
                pa,
                pb,
                "named_cohort",
                strong_cohorts,
                labels_a,
                labels_b,
                review_required=True,
            )
            details["shared_labels"] = details.pop("shared")
            pair_signals.append(
                ("same_cohort", 0.80, details, "deterministic_contextual_identifier")
            )
        if weak_cohorts:
            details = _shared_mention_details(
                pa,
                pb,
                "named_cohort",
                weak_cohorts,
                labels_a,
                labels_b,
                review_required=True,
            )
            details["shared_labels"] = details.pop("shared")
            details["candidate_signal_type"] = "same_cohort"
            pair_signals.append(
                (
                    "shared_cohort_mention",
                    0.20,
                    details,
                    "deterministic_contextual_identifier",
                )
            )
        origins_a = data_origin_mentions[pa]
        origins_b = data_origin_mentions[pb]
        shared_data_origins = sorted(
            key
            for key in set(origins_a) & set(origins_b)
            if any(record.get("substantive_data_reuse") for record in origins_a[key])
            and any(record.get("substantive_data_reuse") for record in origins_b[key])
        )
        author_year_a = _paper_author_year_key(a)
        author_year_b = _paper_author_year_key(b)
        direct_reuse = []
        if author_year_a and author_year_a in origins_b:
            direct_reuse.append(author_year_a)
        if author_year_b and author_year_b in origins_a:
            direct_reuse.append(author_year_b)
        if direct_reuse:
            pair_signals.append(
                (
                    "document_explicit_reuse_mention",
                    0.95,
                    {
                        "cited_origins": sorted(set(direct_reuse)),
                        "mentions_a": {key: origins_a.get(key, []) for key in direct_reuse},
                        "mentions_b": {key: origins_b.get(key, []) for key in direct_reuse},
                        "review_required": True,
                    },
                    "deterministic_document_data_reuse",
                )
            )
        if shared_data_origins:
            pair_signals.append(
                (
                    "shared_cited_data_origin",
                    0.85,
                    {
                        "shared_origins": shared_data_origins,
                        "mentions_a": {key: origins_a[key] for key in shared_data_origins},
                        "mentions_b": {key: origins_b[key] for key in shared_data_origins},
                        "review_required": True,
                    },
                    "deterministic_document_data_reuse",
                )
            )
        for signal_type, strength, details, source in pair_signals:
            signals.append(
                LineageSignal(
                    signal_id=f"LS{len(signals)+1:06d}", paper_id_a=pa, paper_id_b=pb,
                    signal_type=signal_type, signal_strength=strength, source=source,
                    details=details, status="suggested",
                )
            )
    return signals


def _document_chunks(document: str | list[dict[str, Any]] | Any) -> list[dict[str, Any]]:
    if isinstance(document, str):
        return [{"chunk_id": "TEXT_0001", "text": document}] if document else []
    if isinstance(document, list):
        chunks: list[dict[str, Any]] = []
        current_section = "unknown"
        for row in document:
            if not isinstance(row, dict):
                continue
            chunk = dict(row)
            declared = re.sub(
                r"\s+", " ", str(chunk.get("section") or "").strip().lower()
            )
            if declared and declared != "unknown":
                current_section = declared
            chunk["section"] = current_section
            text = str(chunk.get("text") or "")
            headings = list(SECTION_HEADING_RE.finditer(text))
            if headings:
                current_section = re.sub(
                    r"\s+", " ", headings[-1].group(1).strip().lower()
                )
            chunks.append(chunk)
        return chunks
    return []


def _mention_record(
    chunk: dict[str, Any], text: str, start: int, end: int, value: str
) -> dict[str, Any]:
    context_start = max(0, start - 320)
    context_end = min(len(text), end + 320)
    context = re.sub(r"\s+", " ", text[context_start:context_end]).strip()
    section = _section_for_offset(text, start, str(chunk.get("section") or "unknown"))
    return {
        "value": value,
        "mention_class": _classify_mention(context, section),
        "chunk_id": str(chunk.get("chunk_id") or ""),
        "page_start": chunk.get("page_start"),
        "page_end": chunk.get("page_end"),
        "section": section,
        "char_start": start,
        "char_end": end,
        "context": context,
    }


def _section_for_offset(text: str, offset: int, declared: str) -> str:
    normalized_declared = re.sub(r"\s+", " ", declared.strip().lower())
    headings = list(SECTION_HEADING_RE.finditer(text[:offset]))
    if headings:
        return re.sub(r"\s+", " ", headings[-1].group(1).strip().lower())
    return normalized_declared or "unknown"


def _classify_mention(context: str, section: str) -> str:
    if section in {"references", "bibliography"}:
        return "cited_or_background"
    if any(pattern.search(context) for pattern in OWN_DATA_PATTERNS):
        return "own_data"
    if section in OWN_DATA_SECTIONS:
        return "own_data"
    if section in BACKGROUND_SECTIONS or any(
        pattern.search(context) for pattern in BACKGROUND_PATTERNS
    ):
        return "cited_or_background"
    return "ambiguous"


def _has_own_data_mention(records: list[dict]) -> bool:
    return any(record.get("mention_class") == "own_data" for record in records)


def _shared_mention_details(
    paper_id_a: str,
    paper_id_b: str,
    identifier_type: str,
    shared: list[str],
    values_a: dict[str, list[dict]],
    values_b: dict[str, list[dict]],
    *,
    review_required: bool,
) -> dict[str, Any]:
    evidence = {
        paper_id_a: [record for value in shared for record in values_a[value]][:8],
        paper_id_b: [record for value in shared for record in values_b[value]][:8],
    }
    return {
        "identifier_type": identifier_type,
        "shared": shared,
        "mention_classification": {
            paper_id: sorted({str(record.get("mention_class")) for record in records})
            for paper_id, records in evidence.items()
        },
        "evidence": evidence,
        "review_required": review_required,
        "interpretation": (
            "shared active-study identifier; candidate dependence signal only"
            if all(_has_own_data_mention(records) for records in evidence.values())
            else "shared mention only; not evidence of a shared origin"
        ),
    }


def build_lineage_edges(
    signals: list[LineageSignal], weights: dict[str, float] | None = None
) -> list[EvidenceLineageEdge]:
    grouped: dict[tuple[str, str], list[LineageSignal]] = defaultdict(list)
    for signal in signals:
        key = tuple(sorted([signal.paper_id_a, signal.paper_id_b]))
        grouped[key].append(signal)
    return [build_edge(a, b, pair_signals, weights) for (a, b), pair_signals in sorted(grouped.items())]


def assign_lineage_ids(
    paper_ids: list[str],
    edges: list[EvidenceLineageEdge],
) -> dict[str, str]:
    validate_lineage_consistency(paper_ids, edges)
    parent = {paper_id: paper_id for paper_id in paper_ids}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for edge in edges:
        if (
            (edge.is_hard_duplicate or edge.origin_relation == "same_origin")
            and edge.paper_id_a in parent
            and edge.paper_id_b in parent
        ):
            union(edge.paper_id_a, edge.paper_id_b)
    roots = {root: f"LINEAGE_{index:04d}" for index, root in enumerate(sorted({find(p) for p in paper_ids}), 1)}
    return {paper_id: roots[find(paper_id)] for paper_id in paper_ids}


def validate_lineage_consistency(
    paper_ids: list[str],
    edges: list[EvidenceLineageEdge],
) -> None:
    """Reject reviewed pair decisions that contradict transitive clustering."""

    trusted = [
        edge
        for edge in edges
        if (edge.is_hard_duplicate or edge.origin_relation != "unresolved")
        and edge.paper_id_a in paper_ids
        and edge.paper_id_b in paper_ids
    ]
    parent = {paper_id: paper_id for paper_id in paper_ids}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for edge in trusted:
        if edge.is_hard_duplicate or edge.origin_relation == "same_origin":
            union(edge.paper_id_a, edge.paper_id_b)
    contradictions = [
        tuple(sorted((edge.paper_id_a, edge.paper_id_b)))
        for edge in trusted
        if edge.origin_relation == "distinct_origin"
        and find(edge.paper_id_a) == find(edge.paper_id_b)
    ]
    if contradictions:
        preview = ", ".join(f"{left}-{right}" for left, right in contradictions[:5])
        raise ValueError(
            "lineage graph has contradictory reviewed pair decisions within a "
            f"transitive cluster: {preview}"
        )


def normalize_doi(value: str) -> str:
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value.strip(), flags=re.IGNORECASE).lower()


def _author_year_key(author: object, year: object) -> str:
    normalized_author = re.sub(r"[^a-z0-9]+", "", str(author or "").lower())
    normalized_year = str(year or "").strip()
    return f"{normalized_author}:{normalized_year}" if normalized_author and normalized_year else ""


def _paper_author_year_key(paper: dict) -> str:
    author = str(paper.get("author") or paper.get("authors") or "").split(",", 1)[0]
    return _author_year_key(author.split()[0] if author else "", paper.get("year"))


def _is_substantive_data_reuse(excerpt: str) -> bool:
    normalized = " ".join(str(excerpt or "").lower().split())
    return bool(
        re.search(
            r"\b(?:published (?:data|profiles)|(?:data(?:set)?|profiles?|samples?|isolates?)"
            r"\b.{0,140}\b(?:were used|was used|used for this study|obtained from|derived from|"
            r"previously described|previously published))\b",
            normalized,
        )
    )


def elife_doi_family(value: str) -> str | None:
    """Return the stable eLife article DOI across reviewed-preprint versions."""

    match = re.fullmatch(r"(10\.7554/elife\.\d+)(?:\.\d+)?", normalize_doi(value))
    return match.group(1) if match else None


def is_biorxiv_medrxiv_doi(value: str) -> bool:
    return normalize_doi(value).startswith("10.1101/")


def likely_preprint_journal_title_pair(
    title_a: str, title_b: str, doi_a: str, doi_b: str
) -> bool:
    """Return a conservative version signal for truncated/extended titles."""

    if is_biorxiv_medrxiv_doi(doi_a) == is_biorxiv_medrxiv_doi(doi_b):
        return False
    shorter, longer = sorted([title_a, title_b], key=len)
    return len(shorter.split()) >= 7 and shorter in longer


def normalize_title(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))
