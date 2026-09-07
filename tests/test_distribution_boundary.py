from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_application_checkout_excludes_internal_research_harnesses():
    assert not list((ROOT / "src/pfempire/evaluation").glob("*.py"))
    for relative in (
        "src/pfempire/trace/lineage_trap_v1.py",
        "scripts/build_malaria_evidence_bench.py",
        "docs/manuscript/main.tex",
        "docs/paper_reproduction.md",
    ):
        assert not (ROOT / relative).exists(), relative


def test_user_guides_and_software_checks_are_retained():
    for relative in (
        "README.md", "LICENSE", "CITATION.cff", "setup.sh", "web", "terminal",
        "docs/getting_started.md", "docs/web_chat.md", "docs/models.md",
        ".github/workflows/public-ci.yml", "tests/trace/test_exact_lineage.py",
        "tests/test_web_chat.py",
    ):
        assert (ROOT / relative).is_file(), relative
