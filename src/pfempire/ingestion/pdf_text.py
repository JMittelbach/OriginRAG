from __future__ import annotations

from pathlib import Path

import fitz


def extract_pdf_text(pdf_path: Path) -> list[dict]:
    """Extract text from a PDF with one dictionary per page."""
    path = Path(pdf_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    if not path.is_file():
        raise ValueError(f"PDF path is not a file: {path}")

    pages: list[dict] = []
    with fitz.open(path) as document:
        for page_index, page in enumerate(document, start=1):
            text = page.get_text("text")
            pages.append(
                {
                    "page": page_index,
                    "text": text,
                    "char_count": len(text),
                }
            )
    return pages

