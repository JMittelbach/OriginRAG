from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import trace_claim_sources as cli  # noqa: E402


def test_default_output_paths_are_claim_specific() -> None:
    first, _ = cli.output_paths(
        "",
        "P001",
        "",
        "First scientific claim",
        all_mentions=False,
    )
    second, _ = cli.output_paths(
        "",
        "P001",
        "",
        "Second scientific claim",
        all_mentions=False,
    )

    assert first != second
    assert first.name.startswith("P001_claim_")


def test_default_output_path_sanitizes_carrier_and_claim_ids() -> None:
    output, _ = cli.output_paths(
        "",
        "../../carrier",
        "claim/with/slashes",
        "claim text",
        all_mentions=False,
    )

    assert output.parent == REPO_ROOT / "outputs" / "source_traces"
    assert output.name == "carrier_claim_with_slashes.json"


def test_queueable_references_require_verified_identifier() -> None:
    def trace(resolution: str) -> dict:
        return {
            "mentions": [
                {
                    "reference": {
                        "work_id": "doi:10.1000/source",
                        "doi": "10.1000/source",
                        "bibliographic_resolution_status": resolution,
                        "verified_identifiers": (
                            {"doi": "10.1000/source"} if resolution == "exact_id" else {}
                        ),
                        "library_status": {"state": "not_in_library"},
                    }
                }
            ]
        }

    assert cli.queueable_references(trace("identifier_extracted_unverified")) == []
    assert cli.queueable_references(trace("exact_title")) == []
    assert len(cli.queueable_references(trace("exact_id"))) == 1

    mixed = trace("exact_title")
    mixed_reference = mixed["mentions"][0]["reference"]
    mixed_reference["doi"] = "10.9999/unverified"
    mixed_reference["pmid"] = "123"
    mixed_reference["verified_identifiers"] = {"pmid": "123"}
    queued = cli.queueable_references(mixed)
    assert queued[0]["doi"] == ""
    assert queued[0]["pmid"] == "123"


def test_cli_rejects_claim_carrier_override_before_persistence(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "parse_args",
        lambda: SimpleNamespace(
            db="unused.sqlite",
            claim_id="CLAIM_1",
            paper_id="P_WRONG",
        ),
    )
    monkeypatch.setattr(cli, "load_project_env", lambda path: None)
    monkeypatch.setattr(
        cli,
        "read_claim",
        lambda db_path, claim_id: {"source_paper_id": "P_CARRIER"},
    )

    with pytest.raises(ValueError, match="does not match claim carrier"):
        cli.main()


def test_cli_rejects_text_override_for_stored_claim(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "parse_args",
        lambda: SimpleNamespace(
            db="unused.sqlite",
            claim_id="CLAIM_1",
            paper_id="P_CARRIER",
            claim_text="Unrelated override",
            claim_file=None,
        ),
    )
    monkeypatch.setattr(cli, "load_project_env", lambda path: None)
    monkeypatch.setattr(
        cli,
        "read_claim",
        lambda db_path, claim_id: {
            "source_paper_id": "P_CARRIER",
            "claim_text": "Stored claim",
        },
    )

    with pytest.raises(ValueError, match="stored claim text"):
        cli.main()


def test_cli_requires_no_store_for_all_mentions_of_stored_claim(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "parse_args",
        lambda: SimpleNamespace(
            db="unused.sqlite",
            claim_id="CLAIM_1",
            paper_id="P_CARRIER",
            claim_text="",
            claim_file=None,
            all_mentions=True,
            no_store=False,
        ),
    )
    monkeypatch.setattr(cli, "load_project_env", lambda path: None)
    monkeypatch.setattr(
        cli,
        "read_claim",
        lambda db_path, claim_id: {
            "source_paper_id": "P_CARRIER",
            "claim_text": "Stored claim",
        },
    )

    with pytest.raises(ValueError, match="requires --no-store"):
        cli.main()
