from __future__ import annotations


def page_quality_warnings(pages: list[dict], min_chars_per_page: int = 500) -> list[str]:
    warnings: list[str] = []
    for page in pages:
        page_number = page.get("page", "?")
        char_count = int(page.get("char_count") or len(str(page.get("text") or "")))
        if char_count < min_chars_per_page:
            warnings.append(f"Page {page_number} has only {char_count} extracted characters.")
    return warnings

