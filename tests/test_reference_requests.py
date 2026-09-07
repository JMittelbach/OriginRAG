from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from pfempire.provenance.requests import queue_missing_sources, read_requests


def test_source_request_queue_is_explicit_and_idempotent(tmp_path) -> None:
    path = tmp_path / "paper_requests.tsv"
    reference = {
        "doi": "https://doi.org/10.1000/EXAMPLE",
        "title": "Example source",
    }

    first = queue_missing_sources([reference], path, category="malaria_omics")
    second = queue_missing_sources([reference], path, category="malaria_omics")

    assert len(first["added"]) == 1
    assert len(second["added"]) == 0
    assert second["skipped"][0]["reason"] == "already_queued"
    rows = read_requests(path)
    assert len(rows) == 1
    assert rows[0]["doi"] == "10.1000/example"
    assert rows[0]["pdf_url"] == ""


def test_source_request_queue_preserves_concurrent_unique_rows(tmp_path) -> None:
    path = tmp_path / "paper_requests.tsv"

    def queue(index: int) -> None:
        queue_missing_sources(
            [{"doi": f"10.1000/source-{index}", "title": f"Source {index}"}],
            path,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(queue, range(24)))

    rows = read_requests(path)
    assert len(rows) == 24
    assert len({row["doi"] for row in rows}) == 24


def test_source_request_queue_deduplicates_concurrent_identical_rows(tmp_path) -> None:
    path = tmp_path / "paper_requests.tsv"

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda _: queue_missing_sources([{"doi": "10.1000/same"}], path),
                range(24),
            )
        )

    assert [row["doi"] for row in read_requests(path)] == ["10.1000/same"]


def test_source_request_queue_rejects_title_only_candidates(tmp_path) -> None:
    result = queue_missing_sources(
        [{"title": "A plausible but unverified title"}],
        tmp_path / "paper_requests.tsv",
    )

    assert result["added"] == []
    assert result["skipped"][0]["reason"] == "no_stable_identifier"
