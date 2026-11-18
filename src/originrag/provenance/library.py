"""Deterministic, local-first paper library status resolution.

This module deliberately does not infer availability from stale metadata such as
``fulltext_status``.  It matches a source conservatively against the paper
registry, then checks the current machine for the registered PDF and chunk file.
No network access is performed here.
"""

from __future__ import annotations

import csv
import html
import re
import unicodedata
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Mapping, Sequence


STATUS_PRESENTATION: Final[dict[str, tuple[str, str]]] = {
    "corpus": ("●", "In searchable corpus"),
    "library": ("✓", "PDF in local library"),
    "registry_only": ("◐", "Registered; PDF missing"),
    "oa_available": ("↓", "Open-access copy available"),
    "not_in_library": ("○", "No registered local copy"),
    "unresolved": ("?", "Source unresolved"),
}

_DOI_PREFIX_RE = re.compile(r"^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)", re.IGNORECASE)
_PMID_PREFIX_RE = re.compile(r"^pmid\s*:\s*", re.IGNORECASE)
_PMCID_PREFIX_RE = re.compile(r"^pmcid\s*:\s*", re.IGNORECASE)
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_PMID_RE = re.compile(r"^[0-9]+$")
_PMCID_RE = re.compile(r"^PMC[0-9]+$", re.IGNORECASE)
_YEAR_RE = re.compile(r"^[12][0-9]{3}$")
_TRUE_VALUES = {"1", "true", "yes", "y", "open", "oa"}


def normalize_doi(value: object) -> str:
    """Return a canonical DOI or an empty string for an invalid value."""
    text = html.unescape(_text(value)).strip(" \t\r\n\"'<>[]{}")
    if not text:
        return ""
    text = urllib.parse.unquote(text)
    text = _DOI_PREFIX_RE.sub("", text, count=1).strip()
    text = text.rstrip(".,;")

    while text.endswith(")") and text.count(")") > text.count("("):
        text = text[:-1]
    text = text.strip().lower()
    return text if _DOI_RE.fullmatch(text) else ""


def normalize_pmid(value: object) -> str:
    """Return a digits-only PubMed identifier or an empty string."""
    text = html.unescape(_text(value)).strip()
    if not text:
        return ""
    parsed = urllib.parse.urlparse(text)
    if parsed.netloc.lower() in {"pubmed.ncbi.nlm.nih.gov", "www.pubmed.ncbi.nlm.nih.gov"}:
        text = parsed.path.strip("/").split("/", 1)[0]
    text = _PMID_PREFIX_RE.sub("", text, count=1).strip().strip("/.,;)")
    return text if _PMID_RE.fullmatch(text) else ""


def normalize_pmcid(value: object) -> str:
    """Return an uppercase PMC identifier or an empty string."""
    text = html.unescape(_text(value)).strip()
    if not text:
        return ""
    parsed = urllib.parse.urlparse(text)
    host = parsed.netloc.lower()
    if host in {
        "pmc.ncbi.nlm.nih.gov",
        "www.ncbi.nlm.nih.gov",
        "europepmc.org",
        "www.europepmc.org",
    }:
        match = re.search(r"/(PMC[0-9]+)(?:/|$)", parsed.path, re.IGNORECASE)
        if match:
            text = match.group(1)
    text = _PMCID_PREFIX_RE.sub("", text, count=1).strip().strip("/.,;)")
    if text.isdigit():
        text = f"PMC{text}"
    text = text.upper()
    return text if _PMCID_RE.fullmatch(text) else ""


def normalize_title(value: object) -> str:
    """Normalize only representation details; do not perform fuzzy matching."""
    text = unicodedata.normalize("NFKC", html.unescape(_text(value))).casefold()
    return " ".join(text.split())


def normalize_year(value: object) -> str:
    """Return an exact four-digit year or an empty string."""
    text = _text(value).strip()
    return text if _YEAR_RE.fullmatch(text) else ""


def load_paper_registry(path: Path | str) -> list[dict[str, str]]:
    """Load a tab-separated paper registry as string-only dictionaries."""
    registry_path = Path(path)
    with registry_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            raise ValueError(f"Paper registry has no header: {registry_path}")
        return [
            {str(key): _text(value).strip() for key, value in row.items() if key is not None}
            for row in reader
        ]


@dataclass(frozen=True)
class _Match:
    record: dict[str, str] | None = None
    matched_by: tuple[str, ...] = ()
    reason: str = ""
    candidate_paper_ids: tuple[str, ...] = ()

    @property
    def is_ambiguous(self) -> bool:
        return bool(self.reason)


class LibraryStatusResolver:
    """Resolve citation metadata against one registry and the current machine."""

    def __init__(
        self,
        *,
        registry_path: Path | str,
        bib_dir: Path | str,
        chunks_dir: Path | str | None = None,
        allow_missing_registry: bool = False,
    ) -> None:
        self.registry_path = Path(registry_path)
        self.bib_dir = Path(bib_dir).expanduser().resolve()
        self.chunks_dir = (
            Path(chunks_dir)
            if chunks_dir is not None
            else self.registry_path.parent.parent / "chunks"
        ).expanduser().resolve()
        try:
            self.records = load_paper_registry(self.registry_path)
        except FileNotFoundError:
            if not allow_missing_registry:
                raise
            self.records = []
        self._indexes = self._build_indexes(self.records)

    def resolve(
        self,
        reference: Mapping[str, object] | None = None,
        **overrides: object,
    ) -> dict[str, Any]:
        """Return a JSON-serializable status dictionary for one source.

        ``reference`` may contain ``paper_id``, ``doi``, ``pmid``, ``pmcid``,
        ``title``, ``year``, ``oa_url``/``pdf_url``, and ``is_open_access``.
        Keyword arguments override values in the mapping.
        """
        source: dict[str, object] = dict(reference or {})
        source.update(overrides)
        query = {
            "paper_id": _text(source.get("paper_id")).strip(),
            "doi": normalize_doi(source.get("doi")),
            "pmid": normalize_pmid(source.get("pmid")),
            "pmcid": normalize_pmcid(source.get("pmcid")),
            "title": normalize_title(source.get("title")),
            "year": normalize_year(source.get("year")),
        }

        match = self._match(query)
        record = match.record
        resolved_paper_id = (
            _text(record.get("paper_id")).strip() if record else query["paper_id"]
        )
        local_pdf = self._local_pdf_status(record)
        local_chunk = self._local_chunk_status(resolved_paper_id)
        oa_url = self._open_access_url(source, record)
        has_external_identity = bool(
            query["doi"]
            or query["pmid"]
            or query["pmcid"]
            or (query["title"] and query["year"])
        )
        has_identity = bool(record or query["paper_id"] or has_external_identity)

        if match.is_ambiguous:
            state = "unresolved"
            reason = match.reason
        elif local_chunk["exists"]:
            state = "corpus"
            reason = "A current-machine chunk file exists for this paper."
        elif local_pdf["exists"]:
            state = "library"
            reason = "The registered PDF exists under the selected bibliography directory."
        elif oa_url and has_identity:
            state = "oa_available"
            reason = "A curated open-access PDF URL is available, but no local PDF exists."
        elif record:
            state = "registry_only"
            reason = "The source is registered, but its PDF is absent on this machine."
        elif has_identity:
            state = "not_in_library"
            reason = "No exact registry match or local asset was found."
        else:
            state = "unresolved"
            reason = "A stable identifier or an exact title-and-year pair is required."

        symbol, label = STATUS_PRESENTATION[state]
        can_queue = state in {"registry_only", "oa_available", "not_in_library"} and bool(
            record or has_external_identity
        )
        registry_data = {
            "matched": record is not None,
            "matched_by": list(match.matched_by),
            "record": dict(record) if record is not None else None,
            "candidate_paper_ids": list(match.candidate_paper_ids),
        }
        return {
            "state": state,
            "symbol": symbol,
            "label": label,
            "display": f"{symbol} {label}",
            "reason": reason,
            "paper_id": resolved_paper_id or None,
            "identifiers": {
                "doi": query["doi"] or None,
                "pmid": query["pmid"] or None,
                "pmcid": query["pmcid"] or None,
            },
            "registry": registry_data,
            "local_pdf": local_pdf,
            "local_chunk": local_chunk,
            "oa": {"available": bool(oa_url), "url": oa_url or None},
            "can_queue": can_queue,
        }

    def resolve_many(
        self, references: Sequence[Mapping[str, object]]
    ) -> list[dict[str, Any]]:
        """Resolve multiple sources while loading the registry only once."""
        return [self.resolve(reference) for reference in references]

    @staticmethod
    def _build_indexes(
        records: Sequence[dict[str, str]],
    ) -> dict[str, dict[object, tuple[int, ...]]]:
        mutable: dict[str, defaultdict[object, list[int]]] = {
            "paper_id": defaultdict(list),
            "doi": defaultdict(list),
            "pmid": defaultdict(list),
            "pmcid": defaultdict(list),
            "title_year": defaultdict(list),
        }
        for position, record in enumerate(records):
            values: dict[str, object] = {
                "paper_id": _text(record.get("paper_id")).strip(),
                "doi": normalize_doi(record.get("doi")),
                "pmid": normalize_pmid(record.get("pmid")),
                "pmcid": normalize_pmcid(record.get("pmcid")),
            }
            title = normalize_title(record.get("title"))
            year = normalize_year(record.get("year"))
            if title and year:
                values["title_year"] = (title, year)
            for field, value in values.items():
                if value:
                    mutable[field][value].append(position)
        return {
            field: {key: tuple(positions) for key, positions in index.items()}
            for field, index in mutable.items()
        }

    def _match(self, query: Mapping[str, str]) -> _Match:
        matched_positions: dict[int, list[str]] = defaultdict(list)
        for field in ("paper_id", "doi", "pmid", "pmcid"):
            value = query[field]
            if not value:
                continue
            positions = self._indexes[field].get(value, ())
            if len(positions) > 1:
                return self._ambiguous_match(
                    positions, f"Exact {field} match is duplicated in the registry."
                )
            if positions:
                matched_positions[positions[0]].append(field)

        if len(matched_positions) > 1:
            return self._ambiguous_match(
                tuple(matched_positions),
                "Supplied identifiers resolve to different registry records.",
            )
        if matched_positions:
            position, fields = next(iter(matched_positions.items()))
            conflict = self._identifier_conflict(self.records[position], query)
            if conflict:
                return self._ambiguous_match((position,), conflict)
            return _Match(record=self.records[position], matched_by=tuple(fields))

        title_year = (query["title"], query["year"])
        if all(title_year):
            positions = self._indexes["title_year"].get(title_year, ())
            if len(positions) > 1:
                return self._ambiguous_match(
                    positions,
                    "Exact normalized title-and-year match is ambiguous in the registry.",
                )
            if positions:
                position = positions[0]
                conflict = self._identifier_conflict(self.records[position], query)
                if conflict:
                    return self._ambiguous_match((position,), conflict)
                return _Match(record=self.records[position], matched_by=("title_year",))
        return _Match()

    def _identifier_conflict(self, record: Mapping[str, str], query: Mapping[str, str]) -> str:
        normalizers = {
            "paper_id": lambda value: _text(value).strip(),
            "doi": normalize_doi,
            "pmid": normalize_pmid,
            "pmcid": normalize_pmcid,
        }
        for field, normalizer in normalizers.items():
            supplied = query[field]
            registered = normalizer(record.get(field))
            if supplied and registered and supplied != registered:
                return f"Supplied {field} conflicts with the matched registry record."
        return ""

    def _ambiguous_match(self, positions: Sequence[int], reason: str) -> _Match:
        paper_ids = tuple(
            sorted(
                {
                    _text(self.records[position].get("paper_id")).strip()
                    for position in positions
                    if 0 <= position < len(self.records)
                    and _text(self.records[position].get("paper_id")).strip()
                }
            )
        )
        return _Match(reason=reason, candidate_paper_ids=paper_ids)

    def _local_pdf_status(self, record: Mapping[str, str] | None) -> dict[str, Any]:
        relative = _text(record.get("local_pdf_relpath")).strip() if record else ""
        path = self._safe_child(self.bib_dir, relative) if relative else None
        return {
            "exists": bool(path and path.is_file()),
            "relative_path": relative or None,
            "path": str(path) if path is not None else None,
            "path_is_safe": path is not None if relative else True,
        }

    def _local_chunk_status(self, paper_id: str) -> dict[str, Any]:
        relative = f"{paper_id}.jsonl" if paper_id else ""
        path = self._safe_child(self.chunks_dir, relative) if relative else None
        return {
            "exists": bool(path and path.is_file()),
            "relative_path": relative or None,
            "path": str(path) if path is not None else None,
            "path_is_safe": path is not None if relative else True,
        }

    @staticmethod
    def _safe_child(root: Path, relative: str) -> Path | None:
        candidate_relative = Path(relative)
        if not relative or candidate_relative.is_absolute():
            return None
        candidate = (root / candidate_relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    @staticmethod
    def _open_access_url(
        source: Mapping[str, object], record: Mapping[str, str] | None
    ) -> str:
        explicit_oa_url = _text(source.get("oa_pdf_url") or source.get("oa_url")).strip()
        if explicit_oa_url:
            return explicit_oa_url
        if _truthy(source.get("is_open_access")) or _truthy(source.get("oa_available")):
            source_pdf_url = _text(source.get("pdf_url")).strip()
            if source_pdf_url:
                return source_pdf_url

        if record:
            return _text(record.get("oa_url") or record.get("pdf_url")).strip()
        return ""


def resolve_library_status(
    reference: Mapping[str, object],
    *,
    registry_path: Path | str,
    bib_dir: Path | str,
    chunks_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for resolving a single source."""
    resolver = LibraryStatusResolver(
        registry_path=registry_path,
        bib_dir=bib_dir,
        chunks_dir=chunks_dir,
    )
    return resolver.resolve(reference)


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).strip().lower() in _TRUE_VALUES


def _text(value: object) -> str:
    return "" if value is None else str(value)
