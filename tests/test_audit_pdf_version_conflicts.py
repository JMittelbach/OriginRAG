from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts/audit_pdf_version_conflicts.py"
SPEC = importlib.util.spec_from_file_location("audit_pdf_version_conflicts", SCRIPT_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

normalize_text = module.normalize_text
shingle_jaccard = module.shingle_jaccard


def test_normalize_text_collapses_case_unicode_and_spacing() -> None:
    assert normalize_text("  PfEMP1\nＰＡＰＥＲ  ") == "pfemp1 paper"


def test_shingle_jaccard_identical_text_is_one() -> None:
    tokens = "one two three four five six seven eight nine".split()
    assert shingle_jaccard(tokens, tokens) == 1.0


def test_shingle_jaccard_distinguishes_different_text() -> None:
    left = "one two three four five six seven eight nine".split()
    right = "alpha beta gamma delta epsilon zeta eta theta iota".split()
    assert shingle_jaccard(left, right) == 0.0
