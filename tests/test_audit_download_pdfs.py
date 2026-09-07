from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from audit_download_pdfs import classify_document, extract_doi  # noqa: E402


def test_extract_doi_normalizes_trailing_punctuation() -> None:
    assert extract_doi("doi: 10.1186/s12936-022-04301-8.") == "10.1186/s12936-022-04301-8"


def test_audit_excludes_generated_research_memos() -> None:
    status, reason = classify_document(
        "research.pdf",
        "Literature Gaps in Malaria Bioinformatics",
        "ChatGPT Deep Research",
        "Executive summary of a literature-scoping memo.",
        "",
    )

    assert status == "exclude_research_memo"
    assert "memo" in reason


def test_audit_accepts_malaria_paper_without_opening_page_doi() -> None:
    status, reason = classify_document(
        "jiac086.pdf",
        "Transcriptome Analysis of Plasmodium falciparum Isolates From Benin",
        "Guillochon et al.",
        "Cerebral malaria parasites displayed reduced circulation times. " * 8,
        "",
    )

    assert status == "new_scholarly_candidate"
    assert "malaria paper" in reason
