from __future__ import annotations

from typing import Any


def chunk_pages(
    paper_id: str,
    pages: list[dict],
    target_chars: int = 2500,
    overlap_chars: int = 300,
) -> list[dict]:
    """Create simple character chunks while preserving paper IDs and page ranges."""
    if not paper_id or not paper_id.strip():
        raise ValueError("paper_id is required")
    if target_chars <= 0:
        raise ValueError("target_chars must be positive")
    if overlap_chars < 0:
        raise ValueError("overlap_chars cannot be negative")
    if overlap_chars >= target_chars:
        raise ValueError("overlap_chars must be smaller than target_chars")

    full_text, page_spans = _build_text_with_page_spans(pages)
    if not full_text.strip():
        return []

    chunks: list[dict] = []
    step = target_chars - overlap_chars
    start = 0
    chunk_index = 1

    while start < len(full_text):
        end = min(start + target_chars, len(full_text))
        text = full_text[start:end].strip()
        if text:
            page_start, page_end = _page_range_for_span(page_spans, start, end)
            chunks.append(
                {
                    "chunk_id": f"{paper_id}_CHUNK_{chunk_index:04d}",
                    "paper_id": paper_id,
                    "chunk_index": chunk_index,
                    "page_start": page_start,
                    "page_end": page_end,
                    "text": text,
                }
            )
            chunk_index += 1
        if end == len(full_text):
            break
        start += step

    return chunks


def _build_text_with_page_spans(pages: list[dict]) -> tuple[str, list[tuple[int, int, int]]]:
    full_text = ""
    page_spans: list[tuple[int, int, int]] = []

    for fallback_page_number, page in enumerate(pages, start=1):
        page_number = _as_int(page.get("page"), fallback_page_number)
        text = str(page.get("text") or "")
        if not text:
            continue

        prefix = "\n\n" if full_text else ""
        full_text += prefix
        start = len(full_text)
        full_text += text
        end = len(full_text)
        page_spans.append((page_number, start, end))

    return full_text, page_spans


def _page_range_for_span(
    page_spans: list[tuple[int, int, int]], start: int, end: int
) -> tuple[int | None, int | None]:
    pages = [page for page, page_start, page_end in page_spans if page_end > start and page_start < end]
    if not pages:
        return None, None
    return min(pages), max(pages)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

