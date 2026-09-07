from __future__ import annotations

import hashlib
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable

import rapidfuzz
from rapidfuzz import fuzz

from pfempire.provenance.library import (
    normalize_doi as canonical_doi,
    normalize_pmcid as canonical_pmcid,
    normalize_pmid as canonical_pmid,
)

try:
    import fitz
except ImportError:
    fitz = None  # type: ignore[assignment]


PARSER_VERSION = "claim_source_trace_v1.2"
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
PMCID_PATTERN = re.compile(r"\bPMC\s*\d+\b", re.IGNORECASE)
PMID_PATTERN = re.compile(r"\b(?:PMID|PubMed)\s*[: ]\s*(\d+)\b", re.IGNORECASE)
REFERENCE_HEADING = re.compile(
    r"(?im)^\s*(?:references(?:\s+and\s+notes)?|bibliography)\s*$"
)
NUMBERED_REFERENCE = re.compile(
    r"(?m)^[ \t]*(?:\[(?P<bracket_number>\d{1,3})\]"
    r"|(?P<punctuated_number>\d{1,3})[.)]"
    r"|(?P<bare_number>\d{1,3})(?=[ \t]+\S))[ \t]*"
)
BM_REFERENCE = re.compile(r"^bm_CR(?P<number>\d+)$", re.IGNORECASE)
FULL_NAMED_REFERENCE = re.compile(
    r":(?:ï»¿|\ufeff|\s)*(?P<number>\d{1,3})\.(?:ï»¿|\ufeff|\s)*(?P<citation>.+)",
    re.DOTALL,
)
SQUARE_CITATION = re.compile(
    r"(?P<marker>\[(?P<numbers>\d{1,3}(?:\s*[-–,;]\s*\d{1,3})*)\])"
)
PAREN_CITATION = re.compile(
    r"(?P<marker>\((?P<numbers>\d{1,3}(?:\s*[-–,;]\s*\d{1,3})*)\))"
)
REFERENCE_END_SECTION = re.compile(
    r"(?im)^[ \t]*(?:acknowledg(?:e)?ments?|author contributions?|competing interests?|"
    r"additional information|publisher['’]s note|data availability)[ \t]*$"
)
PUBLISHER_FOOTER = re.compile(
    r"(?i)(?:\b(?:Nature\s+\w+\s+)?Article\s+https?://(?:dx\.)?doi\.\s*org/"
    r"|https?://(?:dx\.)?doi\.\s*org/10\.\d{4,9}/\S+?\s+Article\s+"
    r"(?:Communications|Nature)\b)"
)


@dataclass(frozen=True)
class ReferenceRecord:
    reference_index: int | None
    citation_text: str
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""
    title: str = ""
    author: str = ""
    year: str = ""
    journal: str = ""
    work_id: str = ""
    metadata_source: str = "pdf_reference_text"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CitationMention:
    mention_id: str
    citing_paper_id: str
    reference_index: int | None
    marker_text: str
    context_text: str
    source_page: int | None
    source_rect: tuple[float, float, float, float] | None
    citation_link_status: str
    scope_status: str
    parser_version: str
    source_sha256: str
    reference: ReferenceRecord | None = None
    claim_match_score: float | None = None
    support_role: str = "unresolved"
    support_relation: str = "unclear"
    origin_review_status: str = "review_required"

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        return payload


def normalize_doi(value: object) -> str:
    return canonical_doi(value)


def normalize_pmcid(value: object) -> str:
    return canonical_pmcid(value)


def normalize_pmid(value: object) -> str:
    return canonical_pmid(value)


def normalized_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.replace("\u00ad", "")
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def stable_work_id(
    *,
    doi: object = "",
    pmcid: object = "",
    pmid: object = "",
    citation_text: object = "",
    title: object = "",
) -> str:
    normalized_doi = normalize_doi(doi)
    if normalized_doi:
        return f"doi:{normalized_doi}"
    normalized_pmcid = normalize_pmcid(pmcid)
    if normalized_pmcid:
        return f"pmcid:{normalized_pmcid}"
    normalized_pmid = normalize_pmid(pmid)
    if normalized_pmid:
        return f"pmid:{normalized_pmid}"
    fallback = normalized_text(title) or normalized_text(citation_text)
    digest = hashlib.sha256(fallback.encode("utf-8")).hexdigest()[:20]
    return f"citation:{digest}"


def extract_identifiers(text: str) -> tuple[str, str, str]:
    searchable = re.sub(
        r"(?i)https?://(?:dx\.)?doi\.\s*org/",
        "doi:",
        text,
    )



    searchable = re.sub(
        r"(10\.\d{4,9}/)[ \t\r\n]+(?=[-._;()/:A-Z0-9])",
        r"\1",
        searchable,
        flags=re.IGNORECASE,
    )
    searchable = re.sub(
        r"(10\.\d{4,9}/[A-Z][A-Z0-9]{0,3}\.)\s+(?=\d{5,}\b)",
        r"\1",
        searchable,
        flags=re.IGNORECASE,
    )
    doi_match = DOI_PATTERN.search(searchable)
    pmcid_match = PMCID_PATTERN.search(text)
    pmid_match = PMID_PATTERN.search(text)
    return (
        normalize_doi(doi_match.group(0)) if doi_match else "",
        normalize_pmid(pmid_match.group(1)) if pmid_match else "",
        normalize_pmcid(pmcid_match.group(0)) if pmcid_match else "",
    )


def parse_numbered_references(reference_text: str) -> list[ReferenceRecord]:
    """Parse a numbered reference list while rejecting year/page-number lookalikes."""

    matches = list(NUMBERED_REFERENCE.finditer(reference_text))
    if not matches:
        return []
    start_index = next(
        (index for index, match in enumerate(matches) if _reference_number(match) == 1),
        None,
    )
    if start_index is None:
        return []

    accepted: list[re.Match[str]] = [matches[start_index]]
    expected = 2
    for match in matches[start_index + 1 :]:
        number = _reference_number(match)
        if number == expected:
            accepted.append(match)
            expected += 1
        elif number > expected + 2:
            continue
    records: list[ReferenceRecord] = []
    for index, match in enumerate(accepted):
        end = accepted[index + 1].start() if index + 1 < len(accepted) else len(reference_text)
        raw_citation = reference_text[match.end() : end]
        if len(clean_citation_text(raw_citation)) < 8:
            continue
        records.append(reference_record(_reference_number(match), raw_citation))
    return records


def _reference_number(match: re.Match[str]) -> int:
    value = next(group for group in match.groups() if group is not None)
    return int(value)


def reference_record(
    reference_index: int | None,
    citation_text: str,
    *,
    metadata_source: str = "pdf_reference_text",
) -> ReferenceRecord:
    citation = clean_citation_text(citation_text)
    doi, pmid, pmcid = extract_identifiers(citation)
    years = re.findall(r"\b(?:18|19|20)\d{2}\b", citation)
    author_match = re.match(r"\s*([A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’\-]+)", citation)
    work_id = stable_work_id(
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        citation_text=citation,
    )
    return ReferenceRecord(
        reference_index=reference_index,
        citation_text=citation,
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        author=author_match.group(1) if author_match else "",
        year=years[-1] if years else "",
        work_id=work_id,
        metadata_source=metadata_source,
    )


def clean_citation_text(text: object) -> str:
    value = str(text or "")
    value = value.replace("\ufeff", "").replace("ï»¿", "")
    value = value.replace("â\x80\x93", "–").replace("â\x80\x94", "—")
    value = re.sub(r"\r:\d+\s*$", "", value)
    value = re.sub(r"(?<=\w)-\s*\n\s*(?=[a-z])", "", value)
    value = _trim_reference_tail(value)
    return " ".join(value.split()).strip()


def _trim_reference_tail(value: str) -> str:
    """Remove article furniture accidentally concatenated to a printed reference."""

    boundaries = [
        match.start()
        for pattern in (REFERENCE_END_SECTION, PUBLISHER_FOOTER)
        for match in pattern.finditer(value)
        if _looks_like_complete_reference(value[: match.start()])
    ]
    return value[: min(boundaries)] if boundaries else value


def _looks_like_complete_reference(value: str) -> bool:
    compact = " ".join(value.split()).strip()
    return len(compact) >= 40 and bool(re.search(r"\b(?:18|19|20)\d{2}\b", compact))


def extract_pdf_citation_map(pdf_path: Path, paper_id: str) -> dict[str, object]:
    if fitz is None:
        raise RuntimeError("PyMuPDF is required for PDF citation tracing.")
    path = Path(pdf_path)
    source_sha256 = sha256_file(path)
    with fitz.open(path) as document:
        references, reference_start_page = _pdf_references(document)
        reference_by_index = {
            record.reference_index: record
            for record in references
            if record.reference_index is not None
        }
        named_mentions = _pdf_named_link_mentions(
            document,
            paper_id=paper_id,
            source_sha256=source_sha256,
            references=reference_by_index,
            reference_start_page=reference_start_page,
        )
        heuristic_mentions = _pdf_heuristic_mentions(
            document,
            paper_id=paper_id,
            source_sha256=source_sha256,
            references=reference_by_index,
            reference_start_page=reference_start_page,
            allow_parenthetical=not bool(named_mentions),
        )
        mentions = _merge_pdf_mentions(named_mentions, heuristic_mentions)
    return {
        "schema_version": "claim_source_trace_v1",
        "parser_version": PARSER_VERSION,
        "runtime_dependencies": runtime_dependencies(),
        "paper_id": paper_id,
        "source_pdf": str(path),
        "source_sha256": source_sha256,
        "reference_start_page": reference_start_page,
        "references": [record.to_dict() for record in references],
        "mentions": [mention.to_dict() for mention in mentions],
    }


def _pdf_references(document: fitz.Document) -> tuple[list[ReferenceRecord], int | None]:
    reference_start_page = _reference_start_page(document)
    text_records: list[ReferenceRecord] = []
    if reference_start_page is not None:
        parts = []
        for page_index in range(reference_start_page - 1, len(document)):
            text = document[page_index].get_text("text")
            if page_index == reference_start_page - 1:
                heading = REFERENCE_HEADING.search(text)
                text = text[heading.end() :] if heading else text
            parts.append(text)
        text_records = parse_numbered_references("\n".join(parts))

    named_records = _named_destination_references(document)
    merged = {record.reference_index: record for record in text_records}



    merged.update({record.reference_index: record for record in named_records})
    records = [merged[key] for key in sorted(key for key in merged if key is not None)]
    return records, reference_start_page


def _reference_start_page(document: fitz.Document) -> int | None:
    start = max(0, int(len(document) * 0.35))
    for page_index in range(start, len(document)):
        if REFERENCE_HEADING.search(document[page_index].get_text("text")):
            return page_index + 1
    return None


def _named_destination_references(document: fitz.Document) -> list[ReferenceRecord]:
    records: dict[int, ReferenceRecord] = {}
    for page in document:
        for link in page.get_links():
            destination = str(link.get("nameddest") or "")
            match = FULL_NAMED_REFERENCE.search(destination)
            if not match:
                continue
            number = int(match.group("number"))
            citation = clean_citation_text(match.group("citation"))
            if len(citation) < 8:
                continue
            records.setdefault(
                number,
                reference_record(number, citation, metadata_source="pdf_named_destination"),
            )
    return list(records.values())


def _pdf_named_link_mentions(
    document: fitz.Document,
    *,
    paper_id: str,
    source_sha256: str,
    references: dict[int, ReferenceRecord],
    reference_start_page: int | None,
) -> list[CitationMention]:
    mentions: list[CitationMention] = []
    seen = set()
    for page_index, page in enumerate(document):
        page_number = page_index + 1
        if reference_start_page and page_number >= reference_start_page:
            break
        for link in page.get_links():
            destination = str(link.get("nameddest") or "")
            number: int | None = None
            full_match = FULL_NAMED_REFERENCE.search(destination)
            bm_match = BM_REFERENCE.match(destination)
            if bm_match:
                number = int(bm_match.group("number"))
            elif full_match:
                number = int(full_match.group("number"))
                references.setdefault(
                    number,
                    reference_record(
                        number,
                        full_match.group("citation"),
                        metadata_source="pdf_named_destination",
                    ),
                )
            if number is None or number not in references:
                continue
            rect = fitz.Rect(link.get("from"))
            dedupe_key = (page_number, number, round(rect.x0, 1), round(rect.y0, 1))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            context = _context_for_rect(page, rect)
            mention_id = stable_mention_id(
                paper_id=paper_id,
                page=page_number,
                reference_index=number,
                rect=rect,
                context=context,
            )
            role, relation = suggest_support_role(context)
            mentions.append(
                CitationMention(
                    mention_id=mention_id,
                    citing_paper_id=paper_id,
                    reference_index=number,
                    marker_text=str(number),
                    context_text=context,
                    source_page=page_number,
                    source_rect=tuple(round(value, 3) for value in rect),
                    citation_link_status="link_rect",
                    scope_status="candidate_sentence_scope",
                    parser_version=PARSER_VERSION,
                    source_sha256=source_sha256,
                    reference=references[number],
                    support_role=role,
                    support_relation=relation,
                )
            )
    return mentions


def _pdf_heuristic_mentions(
    document: fitz.Document,
    *,
    paper_id: str,
    source_sha256: str,
    references: dict[int, ReferenceRecord],
    reference_start_page: int | None,
    allow_parenthetical: bool,
) -> list[CitationMention]:
    mentions: list[CitationMention] = []
    last_page = (reference_start_page - 1) if reference_start_page else len(document)
    patterns = _infer_numeric_citation_patterns(
        document,
        references,
        last_page,
        allow_parenthetical=allow_parenthetical,
    )
    for page_index in range(last_page):
        page_number = page_index + 1
        for block in document[page_index].get_text("blocks"):
            raw_text = str(block[4] or "")
            if _looks_like_numeric_table(raw_text):
                continue
            text = clean_citation_text(raw_text)
            for pattern in patterns:
                for match in pattern.finditer(text):
                    for number in expand_reference_numbers(match.group("numbers")):
                        if number not in references:
                            continue
                        context = context_window(text, match.start(), match.end())
                        role, relation = suggest_support_role(context)
                        mention_id = stable_mention_id(
                            paper_id=paper_id,
                            page=page_number,
                            reference_index=number,
                            rect=None,
                            context=context,
                        )
                        mentions.append(
                            CitationMention(
                                mention_id=mention_id,
                                citing_paper_id=paper_id,
                                reference_index=number,
                                marker_text=match.group("marker"),
                                context_text=context,
                                source_page=page_number,
                                source_rect=None,
                                citation_link_status="heuristic_sentence",
                                scope_status="ambiguous",
                                parser_version=PARSER_VERSION,
                                source_sha256=source_sha256,
                                reference=references[number],
                                support_role=role,
                                support_relation=relation,
                            )
                        )
    return dedupe_mentions(mentions)


def _infer_numeric_citation_patterns(
    document: fitz.Document,
    references: dict[int, ReferenceRecord],
    last_page: int,
    *,
    allow_parenthetical: bool,
) -> list[re.Pattern[str]]:
    """Choose one numeric citation style conservatively for heuristic parsing."""

    square_numbers: list[int] = []
    paren_numbers: list[int] = []
    for page_index in range(last_page):
        text = document[page_index].get_text("text")
        for match in SQUARE_CITATION.finditer(text):
            square_numbers.extend(expand_reference_numbers(match.group("numbers")))
        for match in PAREN_CITATION.finditer(text):
            paren_numbers.extend(expand_reference_numbers(match.group("numbers")))
    square_numbers = [number for number in square_numbers if number in references]
    paren_numbers = [number for number in paren_numbers if number in references]
    if square_numbers:
        return [SQUARE_CITATION]


    if (
        allow_parenthetical
        and len(paren_numbers) >= 12
        and len(set(paren_numbers)) >= 6
        and max(paren_numbers, default=0) >= 8
    ):
        return [PAREN_CITATION]
    return []


def _looks_like_numeric_table(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    numeric_tokens = re.findall(r"\b\d+(?:\.\d+)?\b", text)
    word_tokens = re.findall(r"\b[A-Za-z]{3,}\b", text)
    return len(numeric_tokens) >= 8 and len(numeric_tokens) > len(word_tokens)


def _merge_pdf_mentions(
    named_mentions: list[CitationMention],
    heuristic_mentions: list[CitationMention],
) -> list[CitationMention]:
    """Union structured and heuristic mentions while preferring link geometry."""

    merged = list(named_mentions)
    for candidate in heuristic_mentions:
        duplicate = any(
            existing.source_page == candidate.source_page
            and existing.reference_index == candidate.reference_index
            and fuzz.token_set_ratio(
                normalized_text(existing.context_text),
                normalized_text(candidate.context_text),
            )
            >= 85.0
            for existing in named_mentions
        )
        if not duplicate:
            merged.append(candidate)
    return dedupe_mentions(merged)


def _context_for_rect(page: fitz.Page, rect: fitz.Rect) -> str:
    best_lines: list[str] = []
    best_index = 0
    best_distance = float("inf")
    payload = page.get_text("dict")
    for block in payload.get("blocks", []):
        if block.get("type") != 0:
            continue
        lines = block.get("lines", [])
        for line_index, line in enumerate(lines):
            bbox = fitz.Rect(line.get("bbox"))
            vertical = 0.0 if bbox.y0 - 3 <= rect.y0 <= bbox.y1 + 3 else abs(bbox.y0 - rect.y0)
            horizontal = 0.0 if bbox.x0 - 8 <= rect.x0 <= bbox.x1 + 8 else min(
                abs(bbox.x0 - rect.x0), abs(bbox.x1 - rect.x0)
            )
            distance = vertical * 4 + horizontal
            if distance >= best_distance:
                continue
            best_distance = distance
            best_lines = [
                "".join(str(span.get("text") or "") for span in candidate.get("spans", []))
                for candidate in lines
            ]
            best_index = line_index
    if not best_lines:
        return ""
    start = max(0, best_index - 3)
    end = min(len(best_lines), best_index + 3)
    return clean_citation_text("\n".join(best_lines[start:end]))


def context_window(text: str, start: int, end: int, radius: int = 260) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    fragment = text[left:right]
    sentence_start = max(fragment.rfind(". ", 0, start - left), fragment.rfind("? ", 0, start - left))
    sentence_end = fragment.find(". ", end - left)
    if sentence_start >= 0:
        fragment = fragment[sentence_start + 2 :]
    if sentence_end >= 0:
        fragment = fragment[: sentence_end + 1]
    return clean_citation_text(fragment)


def expand_reference_numbers(text: str, *, max_range: int = 25) -> list[int]:
    values: list[int] = []
    for part in re.split(r"\s*[,;]\s*", text.strip()):
        range_match = re.fullmatch(r"(\d{1,3})\s*[-–]\s*(\d{1,3})", part)
        if range_match:
            start, end = map(int, range_match.groups())
            if start <= end and end - start <= max_range:
                values.extend(range(start, end + 1))
            continue
        if part.isdigit():
            values.append(int(part))
    return list(dict.fromkeys(values))


def stable_mention_id(
    *,
    paper_id: str,
    page: int | None,
    reference_index: int | None,
    rect: fitz.Rect | None,
    context: str,
) -> str:
    rect_text = "" if rect is None else ",".join(f"{value:.2f}" for value in rect)
    material = "|".join(
        [paper_id, str(page or ""), str(reference_index or ""), rect_text, normalized_text(context)]
    )
    return f"MENTION_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def dedupe_mentions(mentions: Iterable[CitationMention]) -> list[CitationMention]:
    unique: dict[str, CitationMention] = {}
    for mention in mentions:
        unique.setdefault(mention.mention_id, mention)
    return list(unique.values())


def trace_claim_sources(
    citation_map: dict[str, object],
    claim_text: str,
    *,
    min_score: float = 55.0,
    limit: int = 12,
) -> dict[str, object]:
    mentions = [dict(item) for item in citation_map.get("mentions", []) if isinstance(item, dict)]
    normalized_claim = normalized_text(claim_text)
    ranked = []
    for mention in mentions:
        context = normalized_text(mention.get("context_text"))
        if normalized_claim:
            score = max(
                fuzz.token_set_ratio(normalized_claim, context),
                fuzz.partial_ratio(normalized_claim, context),
            )
        else:
            score = 100.0
        mention["claim_match_score"] = round(float(score), 3)
        if score >= min_score:
            ranked.append(mention)
    ranked.sort(
        key=lambda item: (
            -float(item.get("claim_match_score") or 0.0),
            int(item.get("source_page") or 0),
            int(item.get("reference_index") or 0),
            str(item.get("mention_id") or ""),
        )
    )
    if normalized_claim and ranked:
        best = float(ranked[0]["claim_match_score"])
        ranked = [item for item in ranked if float(item["claim_match_score"]) >= max(min_score, best - 8.0)]
    selected = ranked[: max(0, limit)]
    return {
        "schema_version": "claim_source_trace_v1",
        "parser_version": citation_map.get("parser_version", PARSER_VERSION),
        "runtime_dependencies": citation_map.get("runtime_dependencies", runtime_dependencies()),
        "paper_id": citation_map.get("paper_id", ""),
        "source_sha256": citation_map.get("source_sha256", ""),
        "claim_text": claim_text,
        "trace_parameters": {"min_score": float(min_score), "limit": int(limit)},
        "status": "linked_candidates" if selected else "citation_context_unresolved",
        "n_mentions_considered": len(mentions),
        "n_linked_candidates": len(selected),
        "source_chain_policy": (
            "Citation identity and semantic support are separate. Every PDF-derived edge requires review; "
            "no cited work is treated as an independent scientific origin automatically."
        ),
        "mentions": selected,
    }


def runtime_dependencies() -> dict[str, str]:
    return {
        "pymupdf": str(getattr(fitz, "VersionBind", "unavailable")),
        "rapidfuzz": str(rapidfuzz.__version__),
    }


def suggest_support_role(context: str) -> tuple[str, str]:
    text = normalized_text(context)
    if any(term in text for term in ["meta analysis", "systematic review", "pooled studies"]):
        return "evidence_synthesis_candidate", "synthesizes"
    if any(
        term in text
        for term in [
            "reanaly",
            "re analy",
            "publicly available",
            "external dataset",
            "independent cohort",
            "leveraged",
            "integrated datasets",
        ]
    ):
        return "reanalysis_candidate", "uses_data"
    if any(term in text for term in ["our study", "our work", "we found", "we show", "we demonstrate"]):
        return "mixed_candidate", "unclear"
    return "cited_background_candidate", "cites_context"


def _xml_local_name(value: object) -> str:
    """Return an XML name without a namespace or lexical prefix."""

    name = str(value or "")
    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _xml_attribute(element: ET.Element, name: str) -> str:
    for key, value in element.attrib.items():
        if _xml_local_name(key) == name:
            return str(value).strip()
    return ""


def _xml_elements(element: ET.Element, *names: str) -> Iterable[ET.Element]:
    wanted = set(names)
    return (node for node in element.iter() if _xml_local_name(node.tag) in wanted)


def _xml_element_text(element: ET.Element) -> str:
    return clean_citation_text(" ".join(element.itertext()))


def _xml_text_excluding(element: ET.Element, *excluded_names: str) -> str:
    """Flatten an element while omitting selected child elements, but not their tails."""

    excluded = set(excluded_names)
    parts: list[str] = []

    def visit(node: ET.Element) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            if _xml_local_name(child.tag) not in excluded:
                visit(child)
            if child.tail:
                parts.append(child.tail)

    visit(element)
    return clean_citation_text(" ".join(parts))


def _first_xml_text(element: ET.Element, *names: str) -> str:
    for node in _xml_elements(element, *names):
        value = _xml_element_text(node)
        if value:
            return value
    return ""


def _jats_reference_index(ref: ET.Element) -> int | None:
    label = next(_xml_elements(ref, "label"), None)
    if label is None:
        return None
    match = re.fullmatch(r"\s*\[?(\d{1,6})\]?[.)]?\s*", _xml_element_text(label))
    return int(match.group(1)) if match else None


def _jats_structured_author(ref: ET.Element) -> str:
    person_groups = list(_xml_elements(ref, "person-group"))
    author_groups = [
        group
        for group in person_groups
        if _xml_attribute(group, "person-group-type").casefold() == "author"
    ]
    search_roots = author_groups or person_groups or [ref]
    for search_root in search_roots:
        surname = _first_xml_text(search_root, "surname")
        if surname:
            return surname
        collaboration = _first_xml_text(search_root, "collab")
        if collaboration:
            return collaboration
    return ""


def _jats_structured_identifiers(ref: ET.Element) -> tuple[str, str, str]:
    identifiers = {"doi": "", "pmid": "", "pmcid": ""}
    for pub_id in _xml_elements(ref, "pub-id"):
        id_type = _xml_attribute(pub_id, "pub-id-type").casefold().replace("_", "-")
        value = _xml_element_text(pub_id)
        if id_type == "doi" and not identifiers["doi"]:
            identifiers["doi"] = normalize_doi(value)
        elif id_type == "pmid" and not identifiers["pmid"]:
            identifiers["pmid"] = normalize_pmid(value)
        elif id_type in {"pmc", "pmcid", "pmc-id"} and not identifiers["pmcid"]:
            identifiers["pmcid"] = normalize_pmcid(value)
    return identifiers["doi"], identifiers["pmid"], identifiers["pmcid"]


def _jats_reference_record(ref: ET.Element) -> ReferenceRecord | None:
    citation = _xml_text_excluding(ref, "label")
    if not citation:
        return None
    reference_index = _jats_reference_index(ref)
    record = reference_record(reference_index, citation, metadata_source="jats_ref")
    structured_doi, structured_pmid, structured_pmcid = _jats_structured_identifiers(ref)
    doi = structured_doi or record.doi
    pmid = structured_pmid or record.pmid
    pmcid = structured_pmcid or record.pmcid
    title = _first_xml_text(ref, "article-title", "chapter-title", "data-title")
    author = _jats_structured_author(ref)
    year = _first_xml_text(ref, "year")
    if not re.fullmatch(r"(?:18|19|20)\d{2}", year):
        year = record.year
    journal = _first_xml_text(ref, "source")
    return replace(
        record,
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        title=title or record.title,
        author=author or record.author,
        year=year,
        journal=journal or record.journal,
        work_id=stable_work_id(
            doi=doi,
            pmcid=pmcid,
            pmid=pmid,
            citation_text=citation,
            title=title,
        ),
    )


def _jats_paragraph_xrefs(
    paragraph: ET.Element,
) -> tuple[str, list[tuple[ET.Element, int, int]]]:
    """Flatten a paragraph and retain character spans for bibliographic xrefs."""

    parts: list[str] = []
    xrefs: list[tuple[ET.Element, int, int]] = []
    cursor = 0

    def append(value: str | None) -> None:
        nonlocal cursor
        if value:
            parts.append(value)
            cursor += len(value)

    def visit(node: ET.Element) -> None:
        append(node.text)
        for child in node:
            start = cursor
            visit(child)
            end = cursor
            if (
                _xml_local_name(child.tag) == "xref"
                and _xml_attribute(child, "ref-type").casefold() == "bibr"
            ):
                xrefs.append((child, start, end))
            append(child.tail)

    visit(paragraph)
    return "".join(parts), xrefs


def _jats_sentence_context(text: str, start: int, end: int) -> str:
    sentence_start = 0
    for boundary in re.finditer(r"[.!?][\"')\]]*\s+", text[:start]):
        sentence_start = boundary.end()
    boundary = re.search(r"[.!?][\"')\]]*(?=\s|$)", text[end:])
    sentence_end = end + boundary.end() if boundary else len(text)
    return clean_citation_text(text[sentence_start:sentence_end])


def extract_jats_citation_map(xml_text: str, paper_id: str) -> dict[str, object]:
    """Extract exact JATS rid-to-reference links without inferring semantic support."""

    root = ET.fromstring(xml_text)
    source_sha256 = hashlib.sha256(xml_text.encode("utf-8")).hexdigest()
    references_by_rid: dict[str, ReferenceRecord] = {}
    references = []
    for ref in _xml_elements(root, "ref"):
        rid = _xml_attribute(ref, "id")
        record = _jats_reference_record(ref)
        if not rid or record is None:
            continue
        references_by_rid[rid] = record
        references.append(record)

    mentions: list[CitationMention] = []
    xref_ordinal = 0
    for paragraph in _xml_elements(root, "p"):
        paragraph_text, xrefs = _jats_paragraph_xrefs(paragraph)
        for xref, start, end in xrefs:
            xref_ordinal += 1
            marker = _xml_element_text(xref)
            context = _jats_sentence_context(paragraph_text, start, end)
            role, relation = suggest_support_role(context)
            rids = list(dict.fromkeys(_xml_attribute(xref, "rid").split()))
            for rid_position, rid in enumerate(rids, start=1):
                record = references_by_rid.get(rid)
                if record is None:
                    continue
                mention_id = stable_mention_id(
                    paper_id=paper_id,
                    page=None,
                    reference_index=record.reference_index,
                    rect=None,
                    context=(
                        f"jats_xref={xref_ordinal}|rid_position={rid_position}|rid={rid}|{context}"
                    ),
                )
                mentions.append(
                    CitationMention(
                        mention_id=mention_id,
                        citing_paper_id=paper_id,
                        reference_index=record.reference_index,
                        marker_text=marker or rid,
                        context_text=context,
                        source_page=None,
                        source_rect=None,
                        citation_link_status="exact_rid",
                        scope_status="jats_sentence_context",
                        parser_version=PARSER_VERSION,
                        source_sha256=source_sha256,
                        reference=record,
                        support_role=role,
                        support_relation=relation,
                        origin_review_status="review_required",
                    )
                )
    return {
        "schema_version": "claim_source_trace_v1",
        "parser_version": PARSER_VERSION,
        "runtime_dependencies": runtime_dependencies(),
        "paper_id": paper_id,
        "source_sha256": source_sha256,
        "references": [record.to_dict() for record in references],
        "mentions": [mention.to_dict() for mention in dedupe_mentions(mentions)],
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
