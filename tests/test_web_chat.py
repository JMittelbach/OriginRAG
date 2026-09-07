from __future__ import annotations

import http.client
import io
import json
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import web_chat  # noqa: E402
from ask_corpus import merge_explicit_doi_anchors, shortlist_answer_candidates  # noqa: E402
from web_chat import (  # noqa: E402
    AuthenticatedWebChatRegistry,
    LibrarySyncManager,
    TurnJobManager,
    WebChatSettings,
    WebChatApp,
    attach_automatic_paper_search_traces,
    append_anonymous_review_event,
    authenticated_turn_settings,
    build_automatic_claim_source_chains,
    build_handler,
    build_ask_args,
    build_public_abstention,
    build_public_assurance,
    enforce_saved_chat_limit,
    format_public_sources,
    infer_session_title,
    library_catalog,
    library_catalog_stats,
    list_saved_sessions,
    merge_settings,
    public_answer_text,
    read_session_turns,
    safe_public_output_file,
    safe_repo_file,
    sanitize_session_title,
    search_library_catalog,
    serialize_turn_result,
    session_messages,
    session_title,
    sessions_with_job_status,
    write_session_title,
)
from pfempire.web_auth import AuthStore  # noqa: E402


def test_library_catalog_merges_metadata_and_searches_without_model(tmp_path: Path) -> None:
    registry = tmp_path / "paper_registry.tsv"
    registry.write_text(
        "paper_id\tauthor\tyear\ttitle\tjournal\tdoi\tlocal_pdf_relpath\tcategory\n"
        "P1\tShabani\t2017\tEPCR-binding PfEMP1 expression\tBMC Medicine\t10.1000/p1\tpapers/p1.pdf\tmalaria\n"
        "P2\tTurner\t2013\tSevere malaria and endothelial protein C receptor\tNature\t10.1000/p2\tpapers/p2.pdf\tmalaria\n",
        encoding="utf-8",
    )
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tdomain\n"
        "P1\tLess complete title\t2017\t10.1000/p1\tmalaria\n"
        "P3\tMetadata-only transcriptome study\t2018\t10.1000/p3\tmalaria\n",
        encoding="utf-8",
    )
    bib_dir = tmp_path / "bib"
    (bib_dir / "papers").mkdir(parents=True)
    (bib_dir / "papers/p1.pdf").write_bytes(b"%PDF-1.7")
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    (chunks / "P1.jsonl").write_text("{}\n", encoding="utf-8")

    catalogue = library_catalog(
        str(registry), str(papers), str(bib_dir), str(chunks)
    )

    assert len(catalogue) == 3
    first = search_library_catalog("sha", catalogue=catalogue)["items"][0]
    assert first["paper_id"] == "P1"
    assert first["title"] == "EPCR-binding PfEMP1 expression"
    assert first["local_pdf"] is True
    assert first["searchable"] is True
    assert first["doi_url"] == "https://doi.org/10.1000/p1"
    assert "local_pdf_relpath" not in first
    assert search_library_catalog("turn nature", catalogue=catalogue)["total"] == 1


def test_library_catalog_paginates_and_bounds_page_size() -> None:
    catalogue = tuple(
        {
            "paper_id": f"P{index}",
            "title": f"Paper {index:03d}",
            "author": "Author",
        }
        for index in range(130)
    )

    first = search_library_catalog(limit=200, catalogue=catalogue)
    second = search_library_catalog(offset=100, limit=40, catalogue=catalogue)

    assert len(first["items"]) == 100
    assert first["has_more"] is True
    assert len(second["items"]) == 30
    assert second["has_more"] is False


def test_library_catalog_stats_counts_categories_and_availability() -> None:
    catalogue = (
        {"paper_id": "P1", "category": "malaria_omics", "searchable": True, "local_pdf": True},
        {"paper_id": "P2", "category": "malaria_omics", "searchable": False, "local_pdf": True},
        {"paper_id": "P3", "category": "sc_tools", "searchable": True, "local_pdf": False},
    )

    stats = library_catalog_stats(catalogue)

    assert stats == {
        "total": 3,
        "searchable": 2,
        "local_pdf": 2,
        "categories": [
            {"category": "malaria_omics", "count": 2},
            {"category": "sc_tools", "count": 1},
        ],
        "groups": [
            {
                "group": "malaria_omics_resistance",
                "label": "Malaria omics & resistance",
                "count": 2,
            },
            {
                "group": "single_cell_methods",
                "label": "Single-cell methods",
                "count": 1,
            },
        ],
    }


def test_turn_jobs_survive_navigation_and_keep_the_captured_chat(tmp_path: Path) -> None:
    app = WebChatApp(WebChatSettings(), out_dir=str(tmp_path / "sessions"))
    original_session = app.session_dir
    started = threading.Event()
    release = threading.Event()
    targets: list[Path] = []

    def run_turn_for_session(session_dir, message, settings_update, progress_callback=None):
        targets.append(Path(session_dir))
        started.set()
        assert release.wait(timeout=2)
        return {"answer": "finished", "output_dir": str(session_dir)}

    app.run_turn_for_session = run_turn_for_session
    manager = TurnJobManager(1)
    try:
        queued = manager.enqueue(
            owner_key="alice",
            app=app,
            session_dir=original_session,
            message="Find the Blase paper",
            settings_update={},
        )
        assert started.wait(timeout=2)

        new_session = Path(app.reset()["session_dir"])
        assert new_session != original_session
        release.set()

        completed = manager.wait_for_terminal(queued["job_id"], timeout=2)
        assert completed["state"] == "complete"
        assert targets == [original_session]
        assert app.session_dir == new_session
    finally:
        release.set()
        manager.shutdown()


def test_queued_chat_titles_remain_bound_to_each_session_during_navigation(
    tmp_path: Path,
) -> None:
    app = WebChatApp(WebChatSettings(), out_dir=str(tmp_path / "sessions"))
    t_cell_session = app.session_dir
    manager = TurnJobManager(1, auto_worker=False)
    try:
        manager.enqueue(
            owner_key="alice",
            app=app,
            session_dir=t_cell_session,
            message="Find papers about memory T-cell repertoires in adolescence",
            settings_update={},
        )
        rifin_session = Path(app.reset()["session_dir"])
        manager.enqueue(
            owner_key="alice",
            app=app,
            session_dir=rifin_session,
            message=(
                "Find primary RIFIN or STEVOR studies on receptor binding, "
                "immune evasion, and host-cell remodelling"
            ),
            settings_update={},
        )

        assert app.load_session(str(t_cell_session))["title"] == "T Cells Search"
        assert app.session_dir == t_cell_session
        assert app.read_session(str(rifin_session))["title"] == "RIFIN/STEVOR Search"
        assert app.session_dir == t_cell_session
        assert app.load_session(str(rifin_session))["title"] == "RIFIN/STEVOR Search"
    finally:
        manager.shutdown()


def test_session_get_is_navigation_side_effect_free_and_deleted_chat_is_404(
    tmp_path: Path,
) -> None:
    app = WebChatApp(WebChatSettings(), out_dir=str(tmp_path / "sessions"))
    first_session = app.session_dir
    active_session = Path(app.reset()["session_dir"])
    handler = build_handler(app, max_concurrent_turns=1)
    try:
        server = web_chat.create_server("127.0.0.1", 0, handler)
    except PermissionError:
        handler.turn_job_manager.shutdown()
        pytest.skip("execution sandbox does not permit loopback sockets")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    try:
        status, _, raw, _ = http_request(
            port,
            "GET",
            f"/api/session?session_dir={quote(str(first_session), safe='')}",
        )
        assert status == 200, raw
        assert app.session_dir == active_session

        app.delete_session(str(first_session))
        status, _, raw, _ = http_request(
            port,
            "GET",
            f"/api/session?session_dir={quote(str(first_session), safe='')}",
        )
        assert status == 404
        assert json.loads(raw)["error"] == "Session not found."
        assert app.session_dir == active_session
    finally:
        server.shutdown()
        server.server_close()
        handler.turn_job_manager.shutdown()
        thread.join(timeout=2)


def test_turn_job_queue_is_global_fifo_but_job_details_are_owner_isolated(
    tmp_path: Path,
) -> None:
    order: list[str] = []
    first_started = threading.Event()
    release_first = threading.Event()

    class FakeApp:
        def __init__(self, root: Path) -> None:
            self.out_dir = root
            self.settings = WebChatSettings()
            self.session_dir = root / "chat"
            self.session_dir.mkdir(parents=True)

        def run_turn_for_session(
            self, session_dir, message, settings_update, progress_callback=None
        ):
            order.append(message)
            if message == "first":
                first_started.set()
                assert release_first.wait(timeout=2)
            return {"answer": message, "output_dir": str(session_dir)}

    alice_app = FakeApp(tmp_path / "alice")
    bob_app = FakeApp(tmp_path / "bob")
    alice_second = tmp_path / "alice" / "chat-2"
    alice_second.mkdir()
    manager = TurnJobManager(1)
    try:
        first = manager.enqueue(
            owner_key="alice",
            app=alice_app,
            session_dir=alice_app.session_dir,
            message="first",
            settings_update={},
        )
        assert first_started.wait(timeout=2)
        second = manager.enqueue(
            owner_key="bob",
            app=bob_app,
            session_dir=bob_app.session_dir,
            message="second",
            settings_update={},
        )
        third = manager.enqueue(
            owner_key="alice",
            app=alice_app,
            session_dir=alice_second,
            message="third",
            settings_update={},
        )

        assert second["jobs_ahead"] == 1
        assert third["jobs_ahead"] == 2
        assert manager.global_status() == {"queued": 2, "running": 1, "capacity": 1}
        assert {job["job_id"] for job in manager.owner_jobs("alice")} == {
            first["job_id"],
            third["job_id"],
        }
        assert {job["job_id"] for job in manager.owner_jobs("bob")} == {
            second["job_id"]
        }
        assert manager.job("alice", second["job_id"]) is None

        release_first.set()
        for job in (first, second, third):
            assert manager.wait_for_terminal(job["job_id"], timeout=2)["state"] == "complete"
        assert order == ["first", "second", "third"]
    finally:
        release_first.set()
        manager.shutdown()


def test_same_chat_queued_followup_loads_completed_predecessor_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = WebChatApp(WebChatSettings(), out_dir=str(tmp_path / "sessions"))
    retrieval_questions: list[str] = []

    def fake_run_question(args, turn_dir, progress_callback=None):
        retrieval_questions.append(args.retrieval_question)
        turn_dir.mkdir(parents=True)
        answer_path = turn_dir / "answer.md"
        answer_path.write_text("## Answer\n\nSaved answer.\n", encoding="utf-8")
        return {
            "answer_path": answer_path,
            "pack": {"chunks": []},
            "answer_qc": {"passed": True},
            "answer_attempts": [],
            "evidence_grade": {"grade": "moderate", "score": 0.5},
            "grounding_report": {"passed": True},
        }

    monkeypatch.setattr(web_chat.ask_corpus, "run_question", fake_run_question)
    manager = TurnJobManager(1)
    try:
        first = manager.enqueue(
            owner_key="alice",
            app=app,
            session_dir=app.session_dir,
            message="Which var genes associate with severe malaria?",
            settings_update={},
        )
        second = manager.enqueue(
            owner_key="alice",
            app=app,
            session_dir=app.session_dir,
            message="What about developmental-stage adjustment in those studies?",
            settings_update={},
        )
        first_status = manager.wait_for_terminal(first["job_id"], timeout=3)
        second_status = manager.wait_for_terminal(second["job_id"], timeout=3)
        assert first_status["state"] == "complete", first_status["error"]
        assert second_status["state"] == "complete", second_status["error"]
    finally:
        manager.shutdown()

    assert len(retrieval_questions) == 2
    assert retrieval_questions[0] == "Which var genes associate with severe malaria?"
    assert "Previous user question: Which var genes associate with severe malaria?" in retrieval_questions[1]


def test_auto_routed_source_followup_reads_saved_task_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = WebChatApp(WebChatSettings(), out_dir=str(tmp_path / "sessions"))
    web_chat.append_session_line(
        app.session_dir,
        "\n".join(
            [
                "## Turn: Find primary papers about EPCR-binding PfEMP1.",
                "Retrieval question: Find primary papers about EPCR-binding PfEMP1.",
                "Task: sources",
                "Sources:",
                "",
            ]
        ),
    )
    observed_tasks: list[str] = []

    def fake_run_question(args, turn_dir, progress_callback=None):
        observed_tasks.append(args.task)
        turn_dir.mkdir(parents=True)
        return {
            "pack": {"chunks": []},
            "answer_qc": {"passed": True},
            "answer_attempts": [],
            "paper_search": {},
            "paper_search_results": [],
        }

    monkeypatch.setattr(web_chat.ask_corpus, "run_question", fake_run_question)

    result = app.run_turn_for_session(
        app.session_dir,
        "And newer ones?",
        {"task": "auto"},
    )

    assert observed_tasks == ["sources"]
    assert result["task"] == "sources"
    assert read_session_turns(app.session_dir)[0]["task"] == "sources"


def test_sessions_with_job_status_does_not_mix_owner_jobs() -> None:
    sessions = [
        {"session_dir": "outputs/alice/a", "title": "A"},
        {"session_dir": "outputs/alice/b", "title": "B"},
    ]
    owner_jobs = [
        {
            "session_dir": "outputs/alice/a",
            "state": "queued",
            "queue_position": 3,
            "created_at": 1,
        }
    ]

    enriched = sessions_with_job_status(sessions, owner_jobs)

    assert enriched[0]["job_state"] == "queued"
    assert enriched[0]["queue_position"] == 3
    assert enriched[1]["job_state"] == ""


def test_persisted_incomplete_job_is_owner_scoped_and_marked_interrupted(
    tmp_path: Path,
) -> None:
    app = WebChatApp(WebChatSettings(), out_dir=str(tmp_path / "sessions"))
    first_manager = TurnJobManager(1, auto_worker=False)
    queued = first_manager.enqueue(
        owner_key="alice",
        app=app,
        session_dir=app.session_dir,
        message="Question left during restart",
        settings_update={},
    )
    first_manager.shutdown()

    recovered_manager = TurnJobManager(1, auto_worker=False)
    recovered_manager.recover_persisted("alice", app)
    recovered = recovered_manager.job("alice", queued["job_id"])

    assert recovered is not None
    assert recovered["state"] == "interrupted"
    assert "submit it again" in recovered["error"]
    assert recovered_manager.job("bob", queued["job_id"]) is None


def test_turn_job_snapshot_is_atomically_replaceable(tmp_path: Path) -> None:
    path = tmp_path / "turn_jobs" / "job.json"
    web_chat.write_json_atomically(path, {"state": "queued", "progress": 2})
    web_chat.write_json_atomically(path, {"state": "running", "progress": 35})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "state": "running",
        "progress": 35,
    }
    assert not list(path.parent.glob(".*.tmp"))


def test_library_sync_manager_stages_pdf_and_reports_import_progress(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    seen: list[Path] = []

    def importer(path: Path, *, timeout: int, progress):
        assert timeout == 30
        files = list(path.glob("*.pdf"))
        seen.extend(files)
        for file in files:
            file.unlink()
        progress(
            {
                "stage": "Building the searchable library index",
                "total": 1,
                "processed": 1,
                "imported": 1,
                "duplicates": 0,
            }
        )
        return {
            "scanned": 1,
            "imported": 1,
            "duplicates": 0,
            "review_required": 0,
            "rejected": 0,
            "batch_id": "batch-1",
            "report": "report.tsv",
        }

    manager = LibrarySyncManager(
        inbox,
        importer=importer,
        refresh_callback=lambda: None,
        auto_worker=False,
    )
    result = manager.store_upload(
        io.BytesIO(b"%PDF-1.7\nsmall test"),
        content_length=len(b"%PDF-1.7\nsmall test"),
        filename="Unsafe ../../ paper name.pdf",
        doi="10.1000/Example",
    )

    assert result["status"] == "queued"
    stored = list(inbox.glob("*.pdf"))
    assert len(stored) == 1
    assert ".." not in stored[0].name
    assert stored[0].with_suffix(".pdf.doi.txt").read_text().strip() == "10.1000/example"

    status = manager.run_pending_now()

    assert seen == stored
    assert status["state"] == "complete"
    assert status["imported"] == 1
    assert status["progress"] == 100


def test_library_sync_refreshes_read_cache_before_runtime_caches(tmp_path: Path) -> None:
    events: list[str] = []

    def importer(_path: Path, *, timeout: int, progress):
        events.append("canonical-index")
        return {
            "scanned": 1,
            "imported": 1,
            "duplicates": 0,
            "review_required": 0,
            "rejected": 0,
        }

    manager = LibrarySyncManager(
        tmp_path / "inbox",
        importer=importer,
        read_cache_refresh=lambda: events.append("read-cache"),
        refresh_callback=lambda: events.append("runtime-caches"),
        auto_worker=False,
    )

    status = manager.run_pending_now()

    assert status["state"] == "complete"
    assert events == ["canonical-index", "read-cache", "runtime-caches"]


def test_library_sync_fails_closed_when_read_cache_refresh_fails(
    tmp_path: Path,
) -> None:
    runtime_cache_refreshed = False

    def importer(_path: Path, *, timeout: int, progress):
        return {
            "scanned": 1,
            "imported": 1,
            "duplicates": 0,
            "review_required": 0,
            "rejected": 0,
        }

    def fail_read_cache() -> None:
        raise RuntimeError("read cache is stale; run setup")

    def refresh_runtime_cache() -> None:
        nonlocal runtime_cache_refreshed
        runtime_cache_refreshed = True

    manager = LibrarySyncManager(
        tmp_path / "inbox",
        importer=importer,
        read_cache_refresh=fail_read_cache,
        refresh_callback=refresh_runtime_cache,
        auto_worker=False,
    )

    with pytest.raises(RuntimeError, match="read cache is stale"):
        manager.run_pending_now()

    assert manager.status()["state"] == "error"
    assert runtime_cache_refreshed is False


def test_library_sync_manager_rejects_non_pdf_without_leaving_file(tmp_path: Path) -> None:
    manager = LibrarySyncManager(
        tmp_path / "inbox",
        importer=lambda *_args, **_kwargs: {},
        refresh_callback=lambda: None,
        auto_worker=False,
    )

    with pytest.raises(ValueError, match="PDF signature"):
        manager.store_upload(
            io.BytesIO(b"plain text"),
            content_length=len(b"plain text"),
            filename="paper.pdf",
        )

    assert not list((tmp_path / "inbox").iterdir())


def test_library_sync_waits_for_active_corpus_reader(tmp_path: Path) -> None:
    corpus_lock = threading.RLock()
    importer_started = threading.Event()

    def importer(_path: Path, *, timeout: int, progress):
        importer_started.set()
        return {
            "scanned": 0,
            "imported": 0,
            "duplicates": 0,
            "review_required": 0,
            "rejected": 0,
        }

    manager = LibrarySyncManager(
        tmp_path / "inbox",
        importer=importer,
        corpus_lock=corpus_lock,
        refresh_callback=lambda: None,
        auto_worker=False,
    )
    corpus_lock.acquire()
    worker = threading.Thread(target=manager.run_pending_now)
    worker.start()
    try:
        assert not importer_started.wait(timeout=0.1)
    finally:
        corpus_lock.release()
    assert importer_started.wait(timeout=1)
    worker.join(timeout=1)
    assert not worker.is_alive()


def test_library_sync_queues_upload_arriving_during_active_import(tmp_path: Path) -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    calls: list[list[str]] = []

    def importer(path: Path, *, timeout: int, progress):
        files = sorted(path.glob("*.pdf"))
        calls.append([file.name for file in files])
        for file in files:
            file.unlink()
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(timeout=3)
        return {
            "scanned": len(files),
            "imported": len(files),
            "duplicates": 0,
            "review_required": 0,
            "rejected": 0,
            "batch_id": f"batch-{len(calls)}",
            "results": [],
        }

    manager = LibrarySyncManager(
        tmp_path / "inbox",
        importer=importer,
        refresh_callback=lambda: None,
    )
    manager.store_upload(
        io.BytesIO(b"%PDF-1.7\nfirst"),
        content_length=len(b"%PDF-1.7\nfirst"),
        filename="first.pdf",
    )
    assert first_started.wait(timeout=3)
    manager.store_upload(
        io.BytesIO(b"%PDF-1.7\nsecond"),
        content_length=len(b"%PDF-1.7\nsecond"),
        filename="second.pdf",
    )
    assert manager.status()["state"] == "running"
    release_first.set()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and (
        len(calls) < 2 or manager.status()["state"] != "complete"
    ):
        time.sleep(0.02)

    assert len(calls) == 2
    assert len(calls[0]) == 1
    assert len(calls[1]) == 1
    assert calls[0] != calls[1]
    assert manager.status()["state"] == "complete"


def test_completed_library_status_expires_after_ten_seconds(
    tmp_path: Path, monkeypatch
) -> None:
    manager = LibrarySyncManager(
        tmp_path / "inbox",
        importer=lambda *_args, **_kwargs: {},
        refresh_callback=lambda: None,
        auto_worker=False,
    )
    manager._update(state="complete", completed_at=100.0, progress=100)
    monkeypatch.setattr(web_chat.time, "time", lambda: 111.0)

    assert manager.status()["state"] == "idle"
    assert manager.status()["progress"] == 0


def http_request(
    port: int,
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    body: bytes | None = None,
    headers: dict | None = None,
) -> tuple[int, dict[str, str], bytes, list[tuple[str, str]]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    if payload is not None and body is not None:
        raise ValueError("Specify payload or body, not both.")
    request_body = json.dumps(payload).encode() if payload is not None else body
    request_headers = dict(headers or {})
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    connection.request(method, path, body=request_body, headers=request_headers)
    response = connection.getresponse()
    raw = response.read()
    header_items = response.getheaders()
    normalized = {key.lower(): value for key, value in header_items}
    connection.close()
    return response.status, normalized, raw, header_items


def login_headers(port: int, username: str, password: str) -> tuple[dict, dict]:
    status, _, raw, header_items = http_request(
        port,
        "POST",
        "/api/login",
        payload={"username": username, "password": password},
    )
    assert status == 200, raw
    cookies = {}
    for key, value in header_items:
        if key.lower() == "set-cookie":
            name, cookie_value = value.split(";", 1)[0].split("=", 1)
            cookies[name] = cookie_value
    headers = {
        "Cookie": "; ".join(f"{key}={value}" for key, value in cookies.items()),
        "X-CSRF-Token": cookies["pfempire_csrf"],
    }
    return headers, json.loads(raw)


def test_merge_settings_casts_numeric_and_boolean_values() -> None:
    settings = merge_settings(
        WebChatSettings(),
        {
            "backend": "template",
            "repair_attempts": "2",
            "limit": "12",
            "template_fallback": False,
            "category": "",
        },
    )

    assert settings.backend == "template"
    assert settings.repair_attempts == 2
    assert settings.limit == 12
    assert settings.template_fallback is False
    assert settings.category is None


def test_authenticated_turn_settings_keep_server_owned_paths_and_policy() -> None:
    base = WebChatSettings(
        backend="template",
        planner_backend="rule_based",
        repair_attempts=1,
        template_fallback=True,
        chunks_dir="/srv/pfempire/chunks",
        papers="/srv/pfempire/papers.tsv",
        retrieval_index="/srv/pfempire/index.sqlite",
        trace_lineage_graph="/srv/pfempire/lineage.json",
    )

    update = authenticated_turn_settings(
        {
            "chunks_dir": "/tmp/attacker/chunks",
            "papers": "../../etc/passwd",
            "retrieval_index": "/tmp/attacker/index.sqlite",
            "trace_lineage_graph": "../../attacker-lineage.json",
            "backend": "qwen",
            "planner_backend": "ollama",
            "answer_model": "unapproved-model",
            "retriever": "keyword",
            "trace_token_budget": 999999,
            "repair_attempts": 1_000_000,
            "template_fallback": False,
            "preset": "quick",
            "evidence_selector": "current",
            "task": "sources",
            "category": "var_pfemp1",
            "profile_instruction": "Use a compact table.",
        }
    )
    merged = merge_settings(base, update)

    assert merged.chunks_dir == "/srv/pfempire/chunks"
    assert merged.papers == "/srv/pfempire/papers.tsv"
    assert merged.retrieval_index == "/srv/pfempire/index.sqlite"
    assert merged.trace_lineage_graph == "/srv/pfempire/lineage.json"
    assert merged.backend == "template"
    assert merged.planner_backend == "rule_based"
    assert merged.answer_model is None
    assert merged.retriever == "bm25"
    assert merged.trace_token_budget == 2000
    assert merged.repair_attempts == 1
    assert merged.template_fallback is True
    assert merged.preset == "pro"
    assert merged.evidence_selector == "trace_flow"
    assert merged.task == "auto"
    assert merged.category is None
    assert merged.profile_instruction == "Use a compact table."
    assert "task" not in update


def test_authenticated_turn_settings_require_an_object() -> None:
    with pytest.raises(ValueError, match="settings must be an object"):
        authenticated_turn_settings([("chunks_dir", "/tmp/attacker")])


def test_authenticated_server_isolates_users_and_requires_csrf(tmp_path: Path) -> None:
    auth_store = AuthStore(tmp_path / "auth.sqlite")
    alice = auth_store.create_user("alice", "quiet-lantern-river-orbit-47")
    bob = auth_store.create_user("bob", "violet-planet-canvas-forest-83")
    settings = WebChatSettings(backend="template")
    registry = AuthenticatedWebChatRegistry(settings, tmp_path / "sessions")
    handler = build_handler(
        None,
        auth_store=auth_store,
        app_registry=registry,
        secure_cookies=False,
    )
    try:
        server = web_chat.create_server("127.0.0.1", 0, handler)
    except PermissionError:
        pytest.skip("execution sandbox does not permit loopback sockets")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    try:
        status, headers, _, _ = http_request(port, "GET", "/")
        assert status == 303
        assert headers["location"] == "/login"

        alice_headers, _ = login_headers(
            port, "alice", "quiet-lantern-river-orbit-47"
        )
        status, _, raw, _ = http_request(
            port, "GET", "/api/status", headers=alice_headers
        )
        assert status == 200
        alice_status = json.loads(raw)
        assert alice_status["user"]["user_id"] == alice.user_id
        assert alice.user_id in alice_status["session_dir"]

        status, _, raw, _ = http_request(
            port,
            "GET",
            "/api/library?query=Shabani&offset=0&limit=5",
            headers=alice_headers,
        )
        assert status == 200
        library_page = json.loads(raw)
        assert library_page["total"] >= 1
        assert len(library_page["items"]) <= 5
        assert all("local_pdf_relpath" not in item for item in library_page["items"])

        status, _, raw, _ = http_request(
            port, "GET", "/api/library-stats", headers=alice_headers
        )
        assert status == 200
        library_stats = json.loads(raw)
        assert library_stats["total"] >= library_stats["searchable"]
        assert library_stats["categories"]

        no_csrf = {"Cookie": alice_headers["Cookie"]}
        status, _, _, _ = http_request(port, "POST", "/api/reset", headers=no_csrf)
        assert status == 403
        status, _, _, _ = http_request(
            port, "POST", "/api/reset", headers=alice_headers
        )
        assert status == 200

        bob_headers, _ = login_headers(port, "bob", "violet-planet-canvas-forest-83")
        status, _, raw, _ = http_request(
            port, "GET", "/api/status", headers=bob_headers
        )
        assert status == 200
        bob_status = json.loads(raw)
        assert bob_status["user"]["user_id"] == bob.user_id
        assert bob.user_id in bob_status["session_dir"]
        assert alice.user_id not in bob_status["session_dir"]

        foreign_path = alice_status["session_dir"]
        status, _, _, _ = http_request(
            port,
            "GET",
            f"/api/session?session_dir={foreign_path}",
            headers=bob_headers,
        )
        assert status == 400

        status, _, _, _ = http_request(
            port, "POST", "/api/logout", headers=alice_headers
        )
        assert status == 200
        status, _, _, _ = http_request(
            port, "GET", "/api/status", headers=alice_headers
        )
        assert status == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_authenticated_chat_payload_cannot_override_server_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_store = AuthStore(tmp_path / "auth.sqlite")
    auth_store.create_user("alice", "quiet-lantern-river-orbit-47")
    server_settings = WebChatSettings(
        backend="template",
        planner_backend="rule_based",
        repair_attempts=1,
        template_fallback=True,
        chunks_dir="/srv/pfempire/chunks",
        papers="/srv/pfempire/papers.tsv",
        retrieval_index="/srv/pfempire/index.sqlite",
        trace_lineage_graph="/srv/pfempire/lineage.json",
    )
    registry = AuthenticatedWebChatRegistry(server_settings, tmp_path / "sessions")
    observed: list[WebChatSettings] = []

    def fake_turn(self, session_dir, message, settings_update, progress_callback=None):
        observed.append(merge_settings(self.settings, settings_update))
        return {"answer": message, "output_dir": str(session_dir)}

    monkeypatch.setattr(WebChatApp, "run_turn_for_session", fake_turn)
    handler = build_handler(
        None,
        auth_store=auth_store,
        app_registry=registry,
        max_concurrent_turns=1,
    )
    try:
        server = web_chat.create_server("127.0.0.1", 0, handler)
    except PermissionError:
        handler.turn_job_manager.shutdown()
        pytest.skip("execution sandbox does not permit loopback sockets")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    try:
        headers, _ = login_headers(port, "alice", "quiet-lantern-river-orbit-47")
        _, _, status_raw, _ = http_request(
            port, "GET", "/api/status", headers=headers
        )
        session_dir = json.loads(status_raw)["session_dir"]
        status, _, raw, _ = http_request(
            port,
            "POST",
            "/api/chat",
            payload={
                "message": "Find the Blase paper",
                "session_dir": session_dir,
                "settings": {
                    "chunks_dir": "/tmp/attacker/chunks",
                    "papers": "../../etc/passwd",
                    "retrieval_index": "/tmp/attacker/index.sqlite",
                    "trace_lineage_graph": "../../attacker-lineage.json",
                    "backend": "qwen",
                    "planner_backend": "ollama",
                    "answer_model": "unapproved-model",
                    "trace_token_budget": 999999,
                    "repair_attempts": 1_000_000,
                    "template_fallback": False,
                    "preset": "quick",
                    "evidence_selector": "current",
                    "task": "sources",
                    "category": "var_pfemp1",
                    "profile_instruction": "Use a compact table.",
                },
            },
            headers=headers,
        )
        assert status == 202, raw
        job = json.loads(raw)
        completed = handler.turn_job_manager.wait_for_terminal(
            job["job_id"], timeout=3
        )
        assert completed["state"] == "complete", completed["error"]
    finally:
        server.shutdown()
        server.server_close()
        handler.turn_job_manager.shutdown()
        thread.join(timeout=2)

    assert len(observed) == 1
    effective = observed[0]
    assert effective.chunks_dir == "/srv/pfempire/chunks"
    assert effective.papers == "/srv/pfempire/papers.tsv"
    assert effective.retrieval_index == "/srv/pfempire/index.sqlite"
    assert effective.trace_lineage_graph == "/srv/pfempire/lineage.json"
    assert effective.backend == "template"
    assert effective.planner_backend == "rule_based"
    assert effective.answer_model is None
    assert effective.trace_token_budget == 2000
    assert effective.repair_attempts == 1
    assert effective.template_fallback is True
    assert effective.preset == "pro"
    assert effective.evidence_selector == "trace_flow"
    assert effective.task == "auto"
    assert effective.category is None
    assert effective.profile_instruction == "Use a compact table."


def test_authenticated_job_api_keeps_navigation_independent_and_owner_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_store = AuthStore(tmp_path / "auth.sqlite")
    auth_store.create_user("alice", "quiet-lantern-river-orbit-47")
    auth_store.create_user("bob", "violet-planet-canvas-forest-83")
    auth_store.create_user(
        "admin", "silver-harbor-meadow-comet-59", role="admin"
    )
    registry = AuthenticatedWebChatRegistry(
        WebChatSettings(backend="template"), tmp_path / "sessions"
    )
    first_started = threading.Event()
    release_first = threading.Event()

    def fake_turn(self, session_dir, message, settings_update, progress_callback=None):
        if message == "alice first":
            first_started.set()
            assert release_first.wait(timeout=3)
        return {"answer": message, "output_dir": str(session_dir)}

    monkeypatch.setattr(WebChatApp, "run_turn_for_session", fake_turn)
    handler = build_handler(
        None,
        auth_store=auth_store,
        app_registry=registry,
        max_concurrent_turns=1,
    )
    try:
        server = web_chat.create_server("127.0.0.1", 0, handler)
    except PermissionError:
        handler.turn_job_manager.shutdown()
        pytest.skip("execution sandbox does not permit loopback sockets")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    try:
        alice_headers, _ = login_headers(
            port, "alice", "quiet-lantern-river-orbit-47"
        )
        bob_headers, _ = login_headers(port, "bob", "violet-planet-canvas-forest-83")
        admin_headers, _ = login_headers(
            port, "admin", "silver-harbor-meadow-comet-59"
        )
        _, _, alice_status_raw, _ = http_request(
            port, "GET", "/api/status", headers=alice_headers
        )
        _, _, bob_status_raw, _ = http_request(
            port, "GET", "/api/status", headers=bob_headers
        )
        alice_session = json.loads(alice_status_raw)["session_dir"]
        bob_session = json.loads(bob_status_raw)["session_dir"]

        status, _, raw, _ = http_request(
            port,
            "POST",
            "/api/chat",
            payload={"message": "alice first", "session_dir": alice_session},
            headers=alice_headers,
        )
        assert status == 202, raw
        alice_job = json.loads(raw)
        assert first_started.wait(timeout=2)
        status, _, raw, _ = http_request(
            port,
            "POST",
            "/api/chat",
            payload={"message": "bob second", "session_dir": bob_session},
            headers=bob_headers,
        )
        assert status == 202, raw
        bob_job = json.loads(raw)
        assert bob_job["jobs_ahead"] == 1

        _, _, alice_jobs_raw, _ = http_request(
            port, "GET", "/api/jobs", headers=alice_headers
        )
        _, _, bob_jobs_raw, _ = http_request(
            port, "GET", "/api/jobs", headers=bob_headers
        )
        alice_jobs = json.loads(alice_jobs_raw)
        bob_jobs = json.loads(bob_jobs_raw)
        assert [job["message"] for job in alice_jobs["jobs"]] == ["alice first"]
        assert [job["message"] for job in bob_jobs["jobs"]] == ["bob second"]
        assert alice_jobs["queue"] == {"queued": 1, "running": 1, "capacity": 1}
        assert "queue_jobs" not in alice_jobs

        _, _, alice_details_raw, _ = http_request(
            port, "GET", "/api/jobs?details=1", headers=alice_headers
        )
        alice_details = json.loads(alice_details_raw)
        assert alice_details["queue_scope"] == "global"
        assert [job["owner_display_name"] for job in alice_details["queue_jobs"]] == [
            "alice",
            "bob",
        ]
        assert [job["is_current_user"] for job in alice_details["queue_jobs"]] == [
            True,
            False,
        ]
        assert all(job["owner_username"] == "" for job in alice_details["queue_jobs"])
        assert all("question" not in job for job in alice_details["queue_jobs"])
        assert all("job_id" not in job for job in alice_details["queue_jobs"])

        _, _, admin_details_raw, _ = http_request(
            port, "GET", "/api/jobs?details=1", headers=admin_headers
        )
        admin_details = json.loads(admin_details_raw)
        assert admin_details["queue_scope"] == "global"
        assert [job["owner_username"] for job in admin_details["queue_jobs"]] == [
            "alice",
            "bob",
        ]
        status, _, _, _ = http_request(
            port,
            "GET",
            f"/api/job?job_id={bob_job['job_id']}",
            headers=alice_headers,
        )
        assert status == 404

        status, _, reset_raw, _ = http_request(
            port, "POST", "/api/reset", headers=alice_headers
        )
        assert status == 200, reset_raw
        assert json.loads(reset_raw)["session_dir"] != alice_job["session_dir"]
        release_first.set()
        assert handler.turn_job_manager.wait_for_terminal(
            alice_job["job_id"], timeout=3
        )["state"] == "complete"
        assert handler.turn_job_manager.wait_for_terminal(
            bob_job["job_id"], timeout=3
        )["state"] == "complete"
    finally:
        release_first.set()
        server.shutdown()
        server.server_close()
        handler.turn_job_manager.shutdown()
        thread.join(timeout=2)


def test_authenticated_web_upload_stages_pdf_and_requires_csrf(tmp_path: Path) -> None:
    auth_store = AuthStore(tmp_path / "auth.sqlite")
    auth_store.create_user("alice", "quiet-lantern-river-orbit-47")
    registry = AuthenticatedWebChatRegistry(
        WebChatSettings(backend="template"), tmp_path / "sessions"
    )
    manager = LibrarySyncManager(
        tmp_path / "inbox",
        importer=lambda *_args, **_kwargs: {},
        refresh_callback=lambda: None,
        auto_worker=False,
    )
    handler = build_handler(
        None,
        auth_store=auth_store,
        app_registry=registry,
        library_manager=manager,
    )
    try:
        server = web_chat.create_server("127.0.0.1", 0, handler)
    except PermissionError:
        pytest.skip("execution sandbox does not permit loopback sockets")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    pdf = b"%PDF-1.7\nweb upload"
    try:
        auth_headers, _ = login_headers(
            port, "alice", "quiet-lantern-river-orbit-47"
        )
        no_csrf = {
            "Cookie": auth_headers["Cookie"],
            "Content-Type": "application/pdf",
            "X-Paper-Filename": "example.pdf",
        }
        status, _, _, _ = http_request(
            port, "POST", "/api/library-upload", body=pdf, headers=no_csrf
        )
        assert status == 403

        upload_headers = {
            **auth_headers,
            "Content-Type": "application/pdf",
            "X-Paper-Filename": "example.pdf",
            "X-Paper-DOI": "10.1000/example",
        }
        status, _, raw, _ = http_request(
            port, "POST", "/api/library-upload", body=pdf, headers=upload_headers
        )
        assert status == 202, raw
        assert json.loads(raw)["status"] == "queued"
        assert len(list((tmp_path / "inbox").glob("*.pdf"))) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_web_registration_creates_unique_account_and_logs_user_in(tmp_path: Path) -> None:
    auth_store = AuthStore(tmp_path / "auth.sqlite")
    registry = AuthenticatedWebChatRegistry(
        WebChatSettings(backend="template"), tmp_path / "sessions"
    )
    handler = build_handler(
        None,
        auth_store=auth_store,
        app_registry=registry,
    )
    try:
        server = web_chat.create_server("127.0.0.1", 0, handler)
    except PermissionError:
        pytest.skip("execution sandbox does not permit loopback sockets")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    try:
        status, _, raw, header_items = http_request(
            port,
            "POST",
            "/api/register",
            payload={
                "username": "new.user",
                "display_name": "New User",
                "password": "quiet-lantern-river-orbit-47",
                "password_confirm": "quiet-lantern-river-orbit-47",
                "remember": True,
            },
        )
        assert status == 200, raw
        assert json.loads(raw)["user"]["username"] == "new.user"
        cookies = [value for key, value in header_items if key.lower() == "set-cookie"]
        assert any("pfempire_session=" in value for value in cookies)
        remember_ages = [
            int(match.group(1))
            for value in cookies
            if (match := re.search(r"Max-Age=(\d+)", value))
        ]
        assert remember_ages
        assert all(2_591_995 <= age <= 2_592_000 for age in remember_ages)

        status, _, raw, _ = http_request(
            port,
            "POST",
            "/api/register",
            payload={
                "username": "new.user",
                "display_name": "Someone Else",
                "password": "another-secure-pass-93",
                "password_confirm": "another-secure-pass-93",
            },
        )
        assert status == 409
        assert "already exists" in json.loads(raw)["error"].lower()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_login_page_exposes_direct_account_registration_and_password_metadata() -> None:
    html = web_chat.LOGIN_HTML

    assert "<h1>OriginRAG</h1>" in html
    assert "PFEMPIRE" not in html
    assert 'id="loginTab"' in html
    assert 'id="registerTab"' in html
    assert 'id="registerUsername"' in html
    assert 'autocomplete="username"' in html
    assert "The name must still be available." in html
    assert 'autocomplete="current-password"' in html
    assert html.count('autocomplete="new-password"') == 2
    assert "Keep me signed in on this device" in html
    assert 'fetch("/api/register"' in html
    assert "/api/register/start" not in html
    assert "/api/register/verify" not in html
    assert "verification code" not in html.lower()
    assert "BNITM email" not in html
    assert 'id="passwordStrength"' in html
    assert 'id="passwordRequirements"' in html
    assert "At least 13 characters" in html
    assert "At least one letter (upper- or lowercase)" in html
    assert "At least two numbers" in html
    assert "At least one special character" in html
    assert "Does not contain your account or display name" in html
    assert "Both password fields match" in html
    assert 'id="togglePassword"' in html
    assert 'id="generatePassword"' in html
    assert "window.crypto.getRandomValues" in html
    assert "const unbiasedLimit" in html
    assert html.count(
        'window.sessionStorage.removeItem("pfempire.currentSession");'
    ) == 2


def test_saved_chat_limit_blocks_only_creation_of_another_chat(
    tmp_path: Path, monkeypatch
) -> None:
    app = WebChatApp(WebChatSettings(), out_dir=str(tmp_path / "sessions"))
    existing = tmp_path / "sessions" / "existing"
    existing.mkdir(parents=True)
    (existing / "session.md").write_text(
        "## Turn: answer\nUser: question\nOutput: answer\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(web_chat, "MAX_SAVED_CHATS", 1)

    with pytest.raises(web_chat.QuotaExceededError, match="Saved-chat limit"):
        enforce_saved_chat_limit(app, creating_new=True)
    with pytest.raises(web_chat.QuotaExceededError, match="Saved-chat limit"):
        enforce_saved_chat_limit(app, creating_new=False)



    enforce_saved_chat_limit(
        app,
        creating_new=False,
        target_session=existing,
    )

    app.session_dir = existing
    enforce_saved_chat_limit(app, creating_new=False)


def test_answer_shortlist_keeps_one_best_passage_per_top_paper() -> None:
    rows = [
        {"paper_id": "P1", "chunk_id": "C1"},
        {"paper_id": "P1", "chunk_id": "C2"},
        {"paper_id": "P2", "chunk_id": "C3"},
        {"paper_id": "P3", "chunk_id": "C4"},
    ]

    selected = shortlist_answer_candidates(rows, max_papers=2)

    assert [(row["paper_id"], row["chunk_id"]) for row in selected] == [
        ("P1", "C1"),
        ("P2", "C3"),
    ]


def test_answer_shortlist_prefers_question_linked_current_result_passage() -> None:
    rows = [
        {
            "paper_id": "P1",
            "chunk_id": "CONTEXT",
            "section": "results",
            "text": (
                "Asymptomatic infections are associated with broad antibody responses. "
                "The transition from clinical malaria remains unclear."
            ),
        },
        {
            "paper_id": "P1",
            "chunk_id": "DIRECT_RESULT",
            "section": "discussion",
            "text": (
                "Here, we further show that asymptomatic infections have more focused "
                "var gene expression than infections causing clinical malaria."
            ),
        },
        {"paper_id": "P2", "chunk_id": "OTHER", "text": "Other evidence."},
    ]

    selected = shortlist_answer_candidates(
        rows,
        max_papers=2,
        question=(
            "What changes in var gene expression distinguish asymptomatic infections "
            "from clinical malaria?"
        ),
    )

    assert [(row["paper_id"], row["chunk_id"]) for row in selected] == [
        ("P1", "DIRECT_RESULT"),
        ("P2", "OTHER"),
    ]


def test_answer_shortlist_prefers_directional_result_over_cited_background() -> None:
    rows = [
        {
            "paper_id": "P1",
            "chunk_id": "PRIOR_WORK",
            "section": "results",
            "text": (
                "Recently, Kaestli et al. reported increased var group B transcripts "
                "in clinical malaria and group C transcripts in asymptomatic infections."
            ),
        },
        {
            "paper_id": "P1",
            "chunk_id": "OWN_RESULT",
            "section": "abstract",
            "text": (
                "Var group A and B transcripts were more abundant in clinical malaria "
                "than in asymptomatic infections."
            ),
        },
    ]

    selected = shortlist_answer_candidates(
        rows,
        max_papers=1,
        question=(
            "What changes in var gene expression distinguish asymptomatic infections "
            "from clinical malaria?"
        ),
    )

    assert selected[0]["chunk_id"] == "OWN_RESULT"


def test_answer_shortlist_carries_prior_attribution_across_sentence_boundary() -> None:
    rows = [
        {
            "paper_id": "P1",
            "chunk_id": "PRIOR_WORK",
            "text": (
                "Recently, Jones et al. (2020) analysed var gene expression. "
                "This study showed higher var gene expression in clinical malaria "
                "than in asymptomatic infections."
            ),
        },
        {
            "paper_id": "P1",
            "chunk_id": "OWN_RESULT",
            "text": (
                "Var transcripts were higher in clinical malaria than in "
                "asymptomatic infections."
            ),
        },
    ]

    selected = shortlist_answer_candidates(
        rows,
        max_papers=1,
        question=(
            "What changes in var gene expression distinguish asymptomatic infections "
            "from clinical malaria?"
        ),
    )

    assert selected[0]["chunk_id"] == "OWN_RESULT"


def test_answer_shortlist_does_not_treat_anaphoric_prior_result_as_own() -> None:
    rows = [
        {
            "paper_id": "P1",
            "chunk_id": "PRIOR_WORK",
            "text": (
                "Recently, Jones et al. (2020) analysed var gene expression in malaria. "
                "These results suggest higher var gene expression in clinical malaria "
                "than in asymptomatic infections."
            ),
        },
        {
            "paper_id": "P1",
            "chunk_id": "OWN_RESULT",
            "text": (
                "Var transcripts were higher in clinical malaria than in "
                "asymptomatic infections."
            ),
        },
    ]

    selected = shortlist_answer_candidates(
        rows,
        max_papers=1,
        question=(
            "What changes in var gene expression distinguish asymptomatic infections "
            "from clinical malaria?"
        ),
    )

    assert selected[0]["chunk_id"] == "OWN_RESULT"


def test_answer_shortlist_rejects_author_year_attribution_relation_bonus() -> None:
    rows = [
        {
            "paper_id": "P1",
            "chunk_id": "PRIOR_WORK",
            "section": "results",
            "text": (
                "Jones (2020) reported that SPP1 expression was associated with "
                "severe COVID-19."
            ),
        },
        {
            "paper_id": "P1",
            "chunk_id": "OWN_RESULT",
            "section": "abstract",
            "text": "SPP1 expression correlated with disease severity in COVID-19.",
        },
    ]

    selected = shortlist_answer_candidates(
        rows,
        max_papers=1,
        question="How is SPP1 expression associated with severe COVID-19?",
    )

    assert selected[0]["chunk_id"] == "OWN_RESULT"


def test_answer_shortlist_recognizes_results_presented_here_as_current() -> None:
    rows = [
        {
            "paper_id": "P1",
            "chunk_id": "BACKGROUND",
            "text": "Var expression was associated with clinical malaria.",
        },
        {
            "paper_id": "P1",
            "chunk_id": "OWN_RESULT",
            "text": (
                "Previous studies described switching in clinical isolates [1]. "
                "In contrast, the results presented here indicate higher var gene "
                "switching in clinical malaria."
            ),
        },
    ]

    selected = shortlist_answer_candidates(
        rows,
        max_papers=1,
        question="How does var gene expression differ in clinical malaria?",
    )

    assert selected[0]["chunk_id"] == "OWN_RESULT"


def test_answer_shortlist_does_not_join_unrelated_direction_sentences() -> None:
    rows = [
        {
            "paper_id": "P1",
            "chunk_id": "UNRELATED_SENTENCES",
            "text": (
                "We measured var gene expression in asymptomatic infections. "
                "Clinical malaria was more common in adults."
            ),
        },
        {
            "paper_id": "P1",
            "chunk_id": "DIRECT_RESULT",
            "text": (
                "Var transcript profiles differed between clinical malaria and "
                "asymptomatic infections."
            ),
        },
    ]

    selected = shortlist_answer_candidates(
        rows,
        max_papers=1,
        question=(
            "What changes in var gene expression distinguish asymptomatic infections "
            "from clinical malaria?"
        ),
    )

    assert selected[0]["chunk_id"] == "DIRECT_RESULT"


def test_answer_shortlist_does_not_rank_context_anchor_above_result() -> None:
    common = {
        "paper_id": "P1",
        "title": "Marker expression in malaria",
        "required_entity_anchors": [["malaria", "plasmodium"]],
        "required_entity_anchors_verified": True,
        "anchor_only_evidence_window": False,
    }
    rows = [
        {
            **common,
            "chunk_id": "BACKGROUND",
            "text": "Malaria remains a major disease caused by Plasmodium parasites.",
        },
        {
            **common,
            "chunk_id": "DIRECT_RESULT",
            "text": (
                "We found higher marker expression in clinical cases than in controls."
            ),
        },
    ]

    selected = shortlist_answer_candidates(
        rows,
        max_papers=1,
        question="How does marker expression differ in clinical malaria?",
    )

    assert selected[0]["chunk_id"] == "DIRECT_RESULT"


def test_answer_shortlist_prefers_prompt_visible_joint_entity_passage() -> None:
    anchors = [["spp1", "osteopontin"], ["covid 19"], ["severity", "severe"]]
    rows = [
        {
            "paper_id": "LIAO",
            "chunk_id": "CONTEXT_ONLY",
            "text": (
                "Macrophages in severe COVID-19 contributed to inflammation. "
                + "Unrelated technical table content. " * 30
                + "SPP1 appeared only in a distant figure table."
            ),
            "required_entity_anchors": anchors,
            "required_entity_anchors_verified": True,
        },
        {
            "paper_id": "LIAO",
            "chunk_id": "JOINT",
            "text": (
                "SPP1-positive macrophage groups were enriched in patients with severe "
                "COVID-19 compared with moderate disease."
            ),
            "required_entity_anchors": anchors,
            "required_entity_anchors_verified": True,
        },
        {"paper_id": "OTHER", "chunk_id": "OTHER_1"},
    ]

    selected = shortlist_answer_candidates(
        rows,
        max_papers=2,
        question="How are SPP1 macrophages associated with COVID-19 severity?",
        max_chars_per_chunk=180,
    )

    assert [(row["paper_id"], row["chunk_id"]) for row in selected] == [
        ("LIAO", "JOINT"),
        ("OTHER", "OTHER_1"),
    ]


def test_answer_shortlist_uses_title_context_but_requires_relation_in_excerpt() -> None:
    anchors = [["spp1", "osteopontin"], ["covid 19"], ["severity", "severe"]]
    rows = [
        {
            "paper_id": "LIAO",
            "chunk_id": "TABLE_CONTEXT",
            "title": "Macrophage states in COVID-19",
            "text": (
                "Severe COVID-19 was associated with inflammatory cytokines. "
                "Figure values: expression SPP1 0 2 4 6."
            ),
            "required_entity_anchors": anchors,
            "required_entity_anchors_verified": True,
        },
        {
            "paper_id": "LIAO",
            "chunk_id": "DIRECT_RELATION",
            "title": "Macrophage states in COVID-19",
            "text": "SPP1 was highly expressed in patients with severe infection.",
            "required_entity_anchors": anchors,
            "required_entity_anchors_verified": True,
        },
    ]

    selected = shortlist_answer_candidates(
        rows,
        max_papers=1,
        question="How are SPP1 macrophages associated with COVID-19 severity?",
        max_chars_per_chunk=180,
    )

    assert selected[0]["chunk_id"] == "DIRECT_RELATION"


def test_explicit_doi_anchors_keep_every_named_local_paper(tmp_path: Path) -> None:
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tdoi\tauthor\tyear\n"
        "P1\tPreprint\t10.1101/example\tOne\t2023\n"
        "P2\tJournal article\t10.1000/example\tOne\t2024\n",
        encoding="utf-8",
    )
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    (chunks / "P1.jsonl").write_text(
        '{"paper_id":"P1","chunk_id":"C1","section":"abstract","text":"preprint"}\n',
        encoding="utf-8",
    )
    (chunks / "P2.jsonl").write_text(
        '{"paper_id":"P2","chunk_id":"C2","section":"abstract","text":"journal"}\n',
        encoding="utf-8",
    )

    anchored = merge_explicit_doi_anchors(
        "Compare DOI 10.1101/example and DOI 10.1000/example.",
        [{"paper_id": "P1", "chunk_id": "OLD"}],
        chunks_dir=chunks,
        papers_tsv=papers,
    )

    assert [row["paper_id"] for row in anchored[:2]] == ["P1", "P2"]
    assert all(row["explicit_identifier_anchor"] for row in anchored[:2])


def test_merge_settings_validates_selector_and_string_booleans() -> None:
    settings = merge_settings(
        WebChatSettings(),
        {
            "evidence_selector": "trace_flow",
            "template_fallback": "false",
        },
    )

    assert settings.evidence_selector == "trace_flow"
    assert settings.template_fallback is False
    with pytest.raises(ValueError, match="Unsupported evidence selector"):
        merge_settings(WebChatSettings(), {"evidence_selector": "pretend_certified"})


def test_web_source_queue_rejects_forged_resolution_with_invalid_identifier(
    tmp_path: Path,
) -> None:
    app = WebChatApp(WebChatSettings(), out_dir=str(tmp_path / "sessions"))

    with pytest.raises(ValueError, match="Invalid persistent identifier"):
        app.queue_source(
            {
                "doi": "not a DOI",
                "title": "Untrusted source",
                "bibliographic_resolution_status": "exact_id",
            }
        )


def test_web_source_queue_verifies_external_identifier_server_side(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = tmp_path / "paper_registry.tsv"
    registry.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tlocal_pdf_relpath\n",
        encoding="utf-8",
    )
    resolver = web_chat.LibraryStatusResolver(
        registry_path=registry,
        bib_dir=tmp_path / "bib",
        chunks_dir=tmp_path / "chunks",
    )
    captured: list[dict] = []
    monkeypatch.setattr(web_chat, "default_library_resolver", lambda: resolver)
    monkeypatch.setattr(
        web_chat,
        "enrich_reference_online",
        lambda reference: {
            **reference,
            "doi": "10.1000/external",
            "title": "Verified external source",
            "bibliographic_resolution_status": "exact_id",
            "metadata_source": "crossref_exact_doi",
            "oa_pdf_url": "https://example.org/external.pdf",
        },
    )

    def fake_queue(references, requests_path, **kwargs):
        captured.extend(references)
        return {"added": list(references), "skipped": [], "path": str(requests_path)}

    monkeypatch.setattr(web_chat, "queue_missing_sources", fake_queue)
    app = WebChatApp(WebChatSettings(), out_dir=str(tmp_path / "sessions"))

    result = app.queue_source(
        {
            "doi": "https://doi.org/10.1000/EXTERNAL",
            "title": "Printed title",
            "bibliographic_resolution_status": "unresolved",
        }
    )

    assert len(result["added"]) == 1
    assert captured[0]["bibliographic_resolution_status"] == "exact_id"
    assert captured[0]["library_status"]["state"] == "oa_available"


def test_web_source_queue_does_not_trust_identifier_from_title_only_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = tmp_path / "paper_registry.tsv"
    registry.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tlocal_pdf_relpath\n"
        "P1\tSource without registry identifier\t2024\t\t\t\tmissing.pdf\n",
        encoding="utf-8",
    )
    resolver = web_chat.LibraryStatusResolver(
        registry_path=registry,
        bib_dir=tmp_path / "bib",
        chunks_dir=tmp_path / "chunks",
    )
    monkeypatch.setattr(web_chat, "default_library_resolver", lambda: resolver)
    monkeypatch.setattr(web_chat, "enrich_reference_online", lambda reference: reference)
    monkeypatch.setattr(
        web_chat,
        "queue_missing_sources",
        lambda *args, **kwargs: pytest.fail("unverified identifier reached queue"),
    )
    app = WebChatApp(WebChatSettings(), out_dir=str(tmp_path / "sessions"))

    with pytest.raises(ValueError, match="could not be verified"):
        app.queue_source(
            {
                "doi": "10.9999/unverified",
                "title": "Source without registry identifier",
                "year": "2024",
                "bibliographic_resolution_status": "exact_title",
            }
        )


def test_web_source_queue_serializes_only_verified_identifier_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = tmp_path / "paper_registry.tsv"
    registry.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tlocal_pdf_relpath\n"
        "P1\tSource with PMID only\t2024\t\t123\t\tmissing.pdf\n",
        encoding="utf-8",
    )
    resolver = web_chat.LibraryStatusResolver(
        registry_path=registry,
        bib_dir=tmp_path / "bib",
        chunks_dir=tmp_path / "chunks",
    )
    captured = []
    monkeypatch.setattr(web_chat, "default_library_resolver", lambda: resolver)

    def fake_queue(references, *args, **kwargs):
        captured.extend(references)
        return {"added": list(references), "skipped": []}

    monkeypatch.setattr(web_chat, "queue_missing_sources", fake_queue)
    app = WebChatApp(WebChatSettings(), out_dir=str(tmp_path / "sessions"))

    app.queue_source(
        {
            "doi": "10.9999/unverified",
            "pmid": "123",
            "title": "Source with PMID only",
            "year": "2024",
        }
    )

    assert captured[0]["doi"] == ""
    assert captured[0]["pmid"] == "123"


def test_build_ask_args_passes_profile_instruction() -> None:
    settings = WebChatSettings(profile_instruction="Keep answers concise.")

    args = build_ask_args(settings, "Question", "answer")

    assert args.profile_instruction == "Keep answers concise."


def test_automatic_claim_source_chain_maps_claim_carrier_reference_and_origin() -> None:
    class Resolver:
        def resolve(self, source):
            return {
                "state": "searchable_corpus",
                "display": "● In searchable corpus",
                "local_pdf": {"exists": True},
                "local_chunk": {"exists": True},
            }

    def trace_builder(paper_id, claim_text):
        assert paper_id == "P1"
        assert "EPCR binding" in claim_text
        return {
            "status": "linked_candidates",
            "references": [
                {
                    "paper_id": "P0",
                    "citation": "Primary et al. 2012",
                    "bibliographic_resolution_status": "exact_id",
                    "review_status": "review_required",
                    "support_relation": "uses_data",
                    "library_status": {
                        "state": "registry_only",
                        "display": "◐ Registered; PDF missing",
                    },
                }
            ],
        }

    chains = build_automatic_claim_source_chains(
        "## Evidence-backed statements\n\n- EPCR binding is associated with disease (E001).",
        {
            "chunks": [
                {
                    "evidence_id": "E001", "paper_id": "P1", "title": "Carrier",
                    "citation_short": "Carrier et al. 2020", "text": "EPCR binding",
                }
            ],
            "trace_lineage_edges": [
                {
                    "paper_id_a": "P1", "paper_id_b": "P2",
                    "relationship_types": ["same_publication_version"],
                    "origin_relation": "unresolved", "origin_review_status": "suggested",
                }
            ],
        },
        trace_builder=trace_builder,
        library_resolver=Resolver(),
    )

    assert chains[0]["claim_text"].startswith("EPCR binding")
    carrier = chains[0]["carriers"][0]
    assert carrier["role"] == "claim_carrier"
    assert carrier["references"][0]["direct_report_status"] == "source_type_uncertain"
    assert carrier["source_type"]["source_type"] == "uncertain"
    assert carrier["references"][0]["library_status"]["display"] == "◐ Registered; PDF missing"
    assert carrier["lineage_relations"][0]["other_paper_id"] == "P2"
    assert chains[0]["origin_status"] == "unresolved_review_required"


def test_claim_source_trace_stops_at_direct_primary_carrier() -> None:
    class Resolver:
        def resolve(self, source):
            return {
                "state": "searchable_corpus",
                "display": "● In searchable corpus",
                "local_pdf": {"exists": True},
                "local_chunk": {"exists": True},
            }

    chains = build_automatic_claim_source_chains(
        "## Evidence-backed statements\n\nA direct finding was reported (E001).",
        {
            "chunks": [
                {
                    "evidence_id": "E001",
                    "paper_id": "P1",
                    "title": "Primary carrier",
                    "text": "A direct finding was reported.",
                    "source_type_assessment": {
                        "source_type": "primary_research",
                        "label": "Primary research",
                    },
                }
            ]
        },
        trace_builder=lambda *_: pytest.fail("primary carrier bibliography must not be chased"),
        library_resolver=Resolver(),
    )

    assert chains == []


def test_build_ask_args_uses_fast_deterministic_planner_for_normal_answers() -> None:
    args = build_ask_args(WebChatSettings(), "A question", "answer")

    assert args.planner_backend == "rule_based"
    assert args.retriever == "bm25"
    assert args.trace_token_budget == 2000
    assert args.repair_attempts == 2


def test_web_answers_default_to_fail_closed_lineage_audit() -> None:
    settings = WebChatSettings()

    args = build_ask_args(settings, "A question", "answer")

    assert settings.evidence_selector == "trace_flow"
    assert args.evidence_selector == "trace_flow"


def test_build_ask_args_forwards_lineage_audit_without_forcing_an_answer() -> None:
    settings = WebChatSettings(
        evidence_selector="trace_flow",
        trace_lineage_graph="data/metadata/reviewed_lineage_graph.json",
        trace_token_budget=6000,
    )

    args = build_ask_args(settings, "Is the evidence sufficient?", "answer")

    assert args.evidence_selector == "trace_flow"
    assert args.trace_lineage_graph == "data/metadata/reviewed_lineage_graph.json"
    assert args.trace_token_budget == 6000
    assert args.task_mode == "evidence_qa"
    assert not hasattr(args, "force_answer")
    assert args.allow_provisional_synthesis is True


def test_build_ask_args_keeps_source_lookup_outside_lineage_certificate() -> None:
    settings = WebChatSettings(evidence_selector="trace_flow")

    args = build_ask_args(settings, "Which paper reports this?", "sources")

    assert args.evidence_selector == "current"
    assert args.task_mode == "evidence_qa"
    assert args.paper_search_mode == "targeted_lookup"
    assert args.planner_backend == "rule_based"
    assert args.backend == "none"


def test_known_domain_paper_search_skips_redundant_llm_planner() -> None:
    settings = WebChatSettings(planner_backend="ollama")

    args = build_ask_args(
        settings,
        "Find primary papers linking var expression and PfEMP1 to cerebral malaria.",
        "sources",
    )

    assert args.paper_search_mode == "topical_discovery"
    assert args.planner_backend == "rule_based"


def test_ambiguous_topical_paper_search_keeps_llm_planner() -> None:
    settings = WebChatSettings(planner_backend="ollama")

    args = build_ask_args(
        settings,
        "Find primary papers about an unusual biological relationship.",
        "sources",
    )

    assert args.paper_search_mode == "topical_discovery"
    assert args.planner_backend == "ollama"


def test_build_ask_args_allows_planner_only_for_topical_paper_discovery() -> None:
    args = build_ask_args(
        WebChatSettings(backend="qwen", planner_backend="ollama"),
        "Encuentra estudios primarios sobre una relación biológica inusual.",
        "sources",
    )

    assert args.paper_search_mode == "topical_discovery"
    assert args.planner_backend == "ollama"
    assert args.backend == "none"
    assert args.configured_answer_backend == "qwen"


def test_public_answer_replaces_internal_ids_with_paper_citations() -> None:
    answer = "Finding one (E001; E002).\n\n## References\n- E001: First\n- E002: Second"
    rendered = public_answer_text(
        answer,
        chunks=[
            {"evidence_id": "E001", "citation_short": "Kaestli et al., 2006"},
            {"evidence_id": "E002", "citation_short": "Warimwe et al., 2009"},
        ],
    )

    assert "[[cite:E001]]" in rendered
    assert "[[cite:E002]]" in rendered
    assert "References" not in rendered


def test_public_fallback_answer_keeps_one_linked_citation_and_locator() -> None:
    answer = (
        "## Evidence-backed statements\n"
        "E001 (Discussion, pages 14) reports that long-lived IgM memory B cells "
        "dominated the antibody response.\n\n"
        "## References\n"
        "- E001: [Macalinao et al., 2023, Discussion, pages 14]"
    )

    rendered = public_answer_text(
        answer,
        chunks=[
            {
                "evidence_id": "E001",
                "citation_short": "Macalinao et al., 2023",
            }
        ],
    )

    assert rendered.count("[[cite:E001]]") == 1
    assert "Macalinao" not in rendered
    assert "[[cite:E001]] (Discussion, pages 14) reports that" in rendered


def test_public_answer_does_not_render_author_year_and_same_source_twice() -> None:
    rendered = public_answer_text(
        "The response was dominated by IgM cells (Macalinao et al., 2023; E001).",
        chunks=[
            {
                "evidence_id": "E001",
                "paper_id": "MAL_HOST_0200",
                "author": "Macalinao",
                "year": "2023",
                "citation_short": "Macalinao et al., 2023",
                "doi": "10.15252/emmm.202317713",
            }
        ],
    )

    assert rendered.count("[[cite:E001]]") == 1
    assert "Macalinao" not in rendered


def test_public_answer_collapses_comma_paired_author_year_and_source() -> None:
    rendered = public_answer_text(
        "The response was dominated by IgM cells (Macalinao et al., 2023, E001).",
        chunks=[
            {
                "evidence_id": "E001",
                "paper_id": "MAL_HOST_0200",
                "author": "Macalinao",
                "year": "2023",
                "citation_short": "Macalinao et al., 2023",
                "doi": "10.15252/emmm.202317713",
            }
        ],
    )

    assert rendered.count("[[cite:E001]]") == 1
    assert "Macalinao" not in rendered


def test_public_answer_deduplicates_two_chunks_from_same_paper() -> None:
    rendered = public_answer_text(
        "The same report supports both clauses (E001; E002).",
        chunks=[
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "citation_short": "Smith et al., 2024",
            },
            {
                "evidence_id": "E002",
                "paper_id": "P1",
                "citation_short": "Smith et al., 2024",
            },
        ],
    )

    assert rendered.count("[[cite:") == 1
    assert "[[cite:E001]]" in rendered


def test_public_answer_deduplicates_same_doi_across_internal_paper_ids() -> None:
    rendered = public_answer_text(
        "The duplicate records support the same clause (E001; E002).",
        chunks=[
            {
                "evidence_id": "E001",
                "paper_id": "DUPLICATE_A",
                "doi": "10.1234/example",
                "citation_short": "Smith et al., 2024",
            },
            {
                "evidence_id": "E002",
                "paper_id": "DUPLICATE_B",
                "doi": "https://doi.org/10.1234/EXAMPLE",
                "citation_short": "Smith et al., 2024",
            },
        ],
    )

    assert rendered.count("[[cite:") == 1
    assert "[[cite:E001]]" in rendered


def test_build_ask_args_keeps_answer_and_retrieval_questions_separate() -> None:
    settings = WebChatSettings()

    args = build_ask_args(
        settings,
        "Und was heißt das für EPCR?",
        "answer",
        retrieval_question="Previous user question: Warum ist P. falciparum virulent?\nCurrent user question: Und was heißt das für EPCR?",
    )

    assert args.question == "Und was heißt das für EPCR?"
    assert args.answer_question == "Und was heißt das für EPCR?"
    assert args.retrieval_question.startswith("Previous user question:")


def test_public_assurance_distinguishes_standard_and_fail_closed_lineage_views() -> None:
    standard = build_public_assurance({})
    assert standard == {
        "mode": "standard",
        "label": "Standard evidence view",
        "audit_run": False,
    }

    lineage = build_public_assurance(
        {
            "trace_sufficiency_certificate": {
                "evidence_sufficiency": "weak",
                "allowed_claim_strength": "hypothesis_only",
                "facet_coverage": 0.75,
                "required_facets_covered": ["mechanism"],
                "unresolved_facets": ["clinical_outcome"],
                "raw_paper_count": 8,
                "substantive_paper_count": 7,
                "minimum_required_papers": 14,
                "selection_stopping_reason": "constraint_limited",
                "lineage_graph_status": "not_provided",
                "reasons": [
                    "lineage_graph_not_provided",
                    "blueprint_not_human_reviewed",
                ],
            },
            "trace_robust_lineage": {
                "status": "lineage_review_required",
                "effective_evidence": {
                    "lower": 1.0,
                    "upper": 6.2,
                    "substantive_paper_count": 5,
                },
                "pessimistic_flow": {
                    "normalized_coverage": 0.5,
                    "bottleneck_coverage": 0.25,
                    "unresolved_facets": ["clinical_outcome"],
                },
                "lineage_count_lower": 1,
                "lineage_count_upper": 8,
                "required_lineages": 3,
                "required_effective_evidence": 2.7,
                "uncertain_pair_count": 28,
                "missing_pair_count": 28,
                "partition_bound_method": "safe_relaxation",
                "exact_for_declared_lineage_uncertainty": False,
            },
            "trace_lineage_review_priorities": [{"paper_id_a": "P1", "paper_id_b": "P2"}],
        },
        trace_selection={
            "raw_paper_count": 8,
            "token_cost": 1974,
            "run_config": {
                "token_budget": 2000,
                "stopping_reason": "constraint_limited",
            },
        },
    )

    assert lineage["audit_run"] is True
    assert lineage["provisional"] is True
    assert lineage["status"] == "lineage_review_required"
    assert lineage["lineage_count_lower"] == 1
    assert lineage["lineage_count_upper"] == 8
    assert lineage["required_lineages"] == 3
    assert lineage["robust_facet_coverage_lower"] == 0.5
    assert lineage["facet_coverage"] == 0.75
    assert lineage["review_priority_count"] == 1
    assert lineage["substantive_paper_count"] == 5
    assert lineage["certifiable_substantive_report_count"] == 5
    assert lineage["retrieval_substantive_report_count"] == 7
    assert lineage["lineage_count_scope"] == "certifiable_substantive_reports"
    assert lineage["selected_report_count"] == 8
    assert lineage["requested_minimum_report_count"] == 14
    assert lineage["selected_report_floor_met"] is False
    assert lineage["selection_token_cost"] == 1974
    assert lineage["selection_token_budget"] == 2000
    assert lineage["selection_stopping_reason"] == "constraint_limited"


def test_public_abstention_explains_study_overlap_without_solver_jargon() -> None:
    answer = build_public_abstention(
        assurance={
            "audit_run": True,
            "status": "lineage_review_required",
        },
        evidence_grade={"grade": "good"},
        grounding_rejected=False,
    )

    assert "Study overlap needs review" in answer
    assert "Independent studies remain unknown" in answer
    assert "scientific-origin" not in answer
    assert "admissible resolution" not in answer


def test_public_abstention_names_missing_facets_for_zero_match_corpus() -> None:
    answer = build_public_abstention(
        assurance={
            "audit_run": True,
            "status": "insufficient_even_optimistically",
        },
        evidence_grade={"grade": "bad"},
        grounding_rejected=False,
        pack={
            "chunks": [],
            "trace_sufficiency_certificate": {
                "unresolved_facets": [
                    "a574b20ed851_memory_t_cell_identity",
                    "a574b20ed851_tcr_repertoire",
                    "a574b20ed851_developmental_age_span",
                ]
            },
        },
    )

    assert "No directly matching evidence retrieved" in answer
    assert "memory T-cell identity" in answer
    assert "TCR repertoire" in answer
    assert "developmental age span" in answer
    assert "corpus-conditional" in answer


def test_public_assurance_exposes_automatic_publication_family_signal() -> None:
    assurance = build_public_assurance(
        {
            "chunks": [
                {"paper_id": "P1", "author": "Author", "year": "2024"},
                {"paper_id": "P2", "author": "Author", "year": "2025"},
            ],
            "n_unique_papers": 2,
            "automatic_lineage_signals": [
                {
                    "paper_id_a": "P1",
                    "paper_id_b": "P2",
                    "signal_type": "same_preprint_family",
                    "status": "suggested",
                    "details": {"normalized_title": "shared title"},
                }
            ],
            "trace_sufficiency_certificate": {
                "lineage_graph_status": "not_provided",
                "raw_paper_count": 2,
            },
            "trace_robust_lineage": {
                "status": "lineage_review_required",
                "effective_evidence": {},
                "pessimistic_flow": {},
            },
        }
    )

    assert assurance["detected_publication_family_links"] == 1
    assert assurance["publication_family_upper_bound"] == 1
    assert assurance["publication_family_links"][0]["automatic"] is True
    assert assurance["lineage_graph_status"] == "not_provided"


def test_public_assurance_separates_review_only_identifier_mentions() -> None:
    assurance = build_public_assurance(
        {
            "chunks": [
                {"paper_id": "P1", "author": "One", "year": "2024"},
                {"paper_id": "P2", "author": "Two", "year": "2025"},
            ],
            "automatic_lineage_signals": [
                {
                    "paper_id_a": "P1",
                    "paper_id_b": "P2",
                    "signal_type": "shared_identifier_mention",
                    "details": {
                        "shared": ["GSE12345"],
                        "interpretation": "shared mention only; not evidence of a shared origin",
                    },
                }
            ],
            "trace_sufficiency_certificate": {
                "lineage_graph_status": "not_provided",
                "raw_paper_count": 2,
            },
            "trace_robust_lineage": {
                "status": "lineage_review_required",
                "effective_evidence": {},
                "pessimistic_flow": {},
            },
        }
    )

    assert assurance["detected_shared_origin_signals"] == 0
    assert assurance["review_only_origin_mention_count"] == 1
    assert assurance["review_only_origin_mentions"][0]["details"]["shared"] == [
        "GSE12345"
    ]


def test_serialize_turn_result_returns_actionable_withheld_state(
    tmp_path: Path,
) -> None:
    result = {
        "pack": {"grade": {"grade": "weak"}, "chunks": [], "n_chunks": 0},
        "evidence_grade": {"grade": "weak"},
        "answer_qc": {},
        "grounding_report": None,
    }

    public = serialize_turn_result(
        result,
        tmp_path,
        retrieval_question="Broad question",
        resolved_task="answer",
    )

    assert public["answer_state"] == "withheld"
    assert "More evidence needed" in public["answer"]
    assert "source-grounded answer" in public["answer"]
    assert public["assurance"]["audit_run"] is False


def test_serialize_turn_result_labels_lineage_bounded_answer_provisional(
    tmp_path: Path,
) -> None:
    answer_path = tmp_path / "answer.md"
    answer_path.write_text("A reported association (E001).", encoding="utf-8")
    result = {
        "answer_path": answer_path,
        "partial_answer_path": answer_path,
        "pack": {
            "grade": {"grade": "good"},
            "chunks": [{"evidence_id": "E001", "paper_id": "P1", "text": "association"}],
            "n_chunks": 1,
            "n_unique_papers": 1,
            "provisional_synthesis_authorized": True,
            "trace_sufficiency_certificate": {"lineage_graph_status": "not_provided"},
            "trace_robust_lineage": {"status": "lineage_review_required"},
        },
        "evidence_grade": {"grade": "good"},
        "answer_qc": {"passed": True},
    }

    public = serialize_turn_result(result, tmp_path, "Question", "answer")

    assert public["answer_state"] == "provisional"
    assert public["assurance"]["provisional_synthesis"] is True
    assert "[[cite:E001]]" in public["answer"]
    assert public["sources"][0]["cited"] is True
    assert public["assurance"]["cited_report_count"] == 1


def test_serialize_turn_result_limits_overlap_audit_to_cited_reports(
    tmp_path: Path,
) -> None:
    answer_path = tmp_path / "answer.md"
    answer_path.write_text("A result (E001; E002).", encoding="utf-8")
    result = {
        "answer_path": answer_path,
        "partial_answer_path": answer_path,
        "pack": {
            "grade": {"grade": "good"},
            "chunks": [
                {"evidence_id": "E001", "paper_id": "P1", "author": "One", "year": "2024"},
                {"evidence_id": "E002", "paper_id": "P2", "author": "Two", "year": "2025"},
                {"evidence_id": "E003", "paper_id": "P3", "author": "Three", "year": "2023"},
            ],
            "n_unique_papers": 3,
            "provisional_synthesis_authorized": True,
            "automatic_lineage_signals": [
                {
                    "paper_id_a": "P1",
                    "paper_id_b": "P2",
                    "signal_type": "same_preprint_family",
                    "details": {},
                },
                {
                    "paper_id_a": "P2",
                    "paper_id_b": "P3",
                    "signal_type": "same_preprint_family",
                    "details": {},
                },
            ],
            "trace_sufficiency_certificate": {
                "lineage_graph_status": "not_provided",
                "raw_paper_count": 3,
            },
            "trace_robust_lineage": {"status": "lineage_review_required"},
        },
        "evidence_grade": {"grade": "good"},
        "answer_qc": {"passed": True},
    }

    public = serialize_turn_result(result, tmp_path, "Question", "answer")

    assert public["assurance"]["cited_report_count"] == 2
    assert public["assurance"]["detected_publication_family_links"] == 1
    assert public["assurance"]["publication_family_upper_bound"] == 1
    assert public["assurance"]["publication_family_links"][0]["paper_id_a"] == "P1"


def test_serialize_turn_result_includes_cautious_inference_citations(
    tmp_path: Path,
) -> None:
    answer_path = tmp_path / "answer.md"
    answer_path.write_text(
        """## Evidence-backed statements
A direct result was reported (E001).

## Cautious inference
A related mechanism may help explain the result (E002).

## References
- E001: Direct report
- E002: Mechanistic report
- E003: Retrieved but uncited report
""",
        encoding="utf-8",
    )
    result = {
        "answer_path": answer_path,
        "partial_answer_path": answer_path,
        "pack": {
            "grade": {"grade": "good"},
            "chunks": [
                {
                    "evidence_id": "E001",
                    "paper_id": "P1",
                    "author": "Direct",
                    "year": "2024",
                    "text": "A direct result was reported.",
                },
                {
                    "evidence_id": "E002",
                    "paper_id": "P2",
                    "author": "Mechanistic",
                    "year": "2025",
                    "text": "A related mechanism was reported.",
                },
                {
                    "evidence_id": "E003",
                    "paper_id": "P3",
                    "author": "Uncited",
                    "year": "2023",
                    "text": "A retrieved but uncited result.",
                },
            ],
            "n_unique_papers": 3,
            "provisional_synthesis_authorized": True,
            "automatic_lineage_signals": [
                {
                    "paper_id_a": "P1",
                    "paper_id_b": "P2",
                    "signal_type": "same_preprint_family",
                    "details": {},
                },
                {
                    "paper_id_a": "P2",
                    "paper_id_b": "P3",
                    "signal_type": "same_preprint_family",
                    "details": {},
                },
            ],
            "trace_sufficiency_certificate": {"lineage_graph_status": "not_provided"},
            "trace_robust_lineage": {"status": "lineage_review_required"},
        },
        "evidence_grade": {"grade": "good"},
        "answer_qc": {"passed": True},
    }

    public = serialize_turn_result(result, tmp_path, "Question", "answer")

    cited_by_id = {
        source["evidence_ids"][0]: source["cited"] for source in public["sources"]
    }
    assert cited_by_id == {"E001": True, "E002": True, "E003": False}
    assert public["assurance"]["cited_report_count"] == 2
    assert public["assurance"]["detected_publication_family_links"] == 1
    assert public["assurance"]["publication_family_upper_bound"] == 1
    assert "[[cite:E002]]" in public["answer"]
    assert "[[cite:E003]]" not in public["answer"]
    assert "## References" not in public["answer"]


def test_serialize_turn_result_names_selected_and_cited_family_count_scopes(
    tmp_path: Path,
) -> None:
    answer_path = tmp_path / "answer.md"
    answer_path.write_text(
        "A result (E001; E002; E003; E004; E005).",
        encoding="utf-8",
    )
    chunks = [
        {
            "evidence_id": f"E{index:03d}",
            "paper_id": f"P{index:02d}",
            "author": f"Author {index}",
            "year": "2024",
            "text": "A direct result.",
        }
        for index in range(1, 11)
    ]
    result = {
        "answer_path": answer_path,
        "partial_answer_path": answer_path,
        "pack": {
            "grade": {"grade": "good"},
            "chunks": chunks,
            "n_unique_papers": 10,
            "provisional_synthesis_authorized": True,
            "trace_sufficiency_certificate": {
                "lineage_graph_status": "not_provided",
                "raw_paper_count": 10,
                "substantive_paper_count": 10,
            },
            "trace_robust_lineage": {
                "status": "lineage_review_required",
                "effective_evidence": {"substantive_paper_count": 7},
                "pessimistic_flow": {},
            },
        },
        "evidence_grade": {"grade": "good"},
        "answer_qc": {"passed": True},
    }

    public = serialize_turn_result(result, tmp_path, "Question", "answer")
    assurance = public["assurance"]

    assert assurance["raw_paper_count"] == 10
    assert assurance["cited_report_count"] == 5
    assert assurance["detected_publication_family_links"] == 0
    assert assurance["selected_publication_family_upper_bound"] == 10
    assert assurance["cited_publication_family_upper_bound"] == 5
    assert assurance["publication_family_upper_bound"] == 5
    assert assurance["publication_family_count_scope"] == "cited_reports"


def test_serialize_paper_search_returns_ranked_results_without_model_answer(
    tmp_path: Path,
) -> None:
    result = {
        "pack": {
            "grade": {"grade": "bad"},
            "chunks": [],
            "paper_search": {
                "mode": "topical_discovery",
                "method": "llm_assisted_query_expansion",
                "planner_assisted": True,
                "ranking": "deterministic_local_retrieval",
                "generative_answer_used": False,
                "requested_detail_fields": [
                    "clinical_phenotype",
                    "parasite_feature",
                    "direction_of_association",
                    "cohort",
                    "assay",
                    "developmental_stage_adjustment",
                ],
            },
        },
        "paper_search_results": [
            {
                "paper_id": "P1",
                "author": "Author",
                "year": "2024",
                "title": "Relevant primary study",
                "doi": "10.1000/relevant",
                "rationale": "matched terms: var, syndrome",
                "question_fields": {
                    "clinical_phenotype": "cerebral malaria",
                    "parasite_feature": "group A var expression",
                    "direction_of_association": "higher in cases",
                    "cohort": "children in Ghana",
                    "assay": "RT-qPCR",
                    "developmental_stage_adjustment": "adjusted using stage markers",
                },
                "question_field_statuses": {
                    "clinical_phenotype": "qwen_grounded",
                    "parasite_feature": "qwen_grounded",
                    "direction_of_association": "qwen_grounded",
                    "cohort": "qwen_grounded",
                    "assay": "qwen_grounded",
                    "developmental_stage_adjustment": "qwen_grounded",
                },
                "question_fields_status": "qwen_grounded",
            }
        ],
    }

    public = serialize_turn_result(
        result,
        tmp_path,
        retrieval_question="Find papers about var expression and malaria syndromes",
        resolved_task="sources",
    )

    assert public["answer_state"] == "results"
    assert "Best-matching papers" in public["answer"]
    assert "table below shows why each paper was selected" in public["answer"]
    assert public["sources"][0]["source_role"] == "search_result"
    assert public["sources"][0]["match_reason"] == "matched terms: var, syndrome"
    assert public["sources"][0]["question_fields"]["clinical_phenotype"] == "cerebral malaria"
    assert (
        public["sources"][0]["question_field_statuses"]["assay"]
        == "model_extracted_unverified"
    )
    assert (
        public["sources"][0]["question_fields_status"]
        == "model_extracted_unverified"
    )
    assert public["paper_search"]["ranking"] == "deterministic_local_retrieval"
    assert public["evidence_grade"] == "not_applicable"


def test_paper_search_assurance_exposes_detected_publication_overlap() -> None:
    assurance = build_public_assurance(
        {
            "paper_search_sources": [
                {"paper_id": "PRE", "author": "Author", "year": "2021"},
                {"paper_id": "JOURNAL", "author": "Author", "year": "2022"},
            ],
            "automatic_lineage_signals": [
                {
                    "paper_id_a": "PRE",
                    "paper_id_b": "JOURNAL",
                    "signal_type": "same_preprint_family",
                    "details": {},
                }
            ],
        }
    )

    assert assurance["mode"] == "lineage_screen"
    assert assurance["detected_publication_family_links"] == 1
    assert assurance["publication_family_upper_bound"] == 1


def test_collapsed_paper_search_family_remains_visible_and_correctly_counted() -> None:
    displayed_sources = [
        {
            "paper_id": "JOURNAL",
            "author": "Author",
            "year": "2022",
            "title": "Journal version",
            "publication_version_display": "representative_of_detected_family",
            "publication_version_relationship_types": ["same_preprint_family"],
            "publication_family_report_count": 2,
            "publication_family_reports": [
                {
                    "paper_id": "PREPRINT",
                    "title": "Preprint version",
                    "year": "2021",
                    "doi": "10.1101/example",
                },
                {
                    "paper_id": "JOURNAL",
                    "title": "Journal version",
                    "year": "2022",
                    "doi": "10.1000/journal",
                },
            ],
        },
        *[
            {
                "paper_id": f"P{index:02d}",
                "author": f"Author {index}",
                "year": "2024",
            }
            for index in range(2, 18)
        ],
    ]
    assurance = build_public_assurance(
        {
            "task": "sources",
            "paper_search_sources": displayed_sources,
            "automatic_lineage_signals": [
                {
                    "paper_id_a": "PREPRINT",
                    "paper_id_b": "JOURNAL",
                    "signal_type": "same_preprint_family",
                    "details": {},
                }
            ],
        }
    )

    assert len(displayed_sources) == 17
    assert assurance["raw_paper_count"] == 18
    assert assurance["detected_publication_family_links"] == 1
    assert assurance["publication_family_upper_bound"] == 17
    link = assurance["publication_family_links"][0]
    assert {link["paper_id_a"], link["paper_id_b"]} == {
        "PREPRINT",
        "JOURNAL",
    }
    assert "Preprint version" in link["label_a"]


def test_paper_search_uses_displayed_origin_screen_instead_of_answer_certificate() -> None:
    assurance = build_public_assurance(
        {
            "task": "sources",
            "paper_search_sources": [
                {"paper_id": "NEW", "author": "Ndugwa", "year": "2026"},
                {"paper_id": "OLD", "author": "Warimwe", "year": "2013"},
            ],
            "automatic_lineage_signals": [
                {
                    "paper_id_a": "NEW",
                    "paper_id_b": "OLD",
                    "signal_type": "shared_cited_data_origin",
                    "details": {"shared_origins": ["warimwe:2009"]},
                }
            ],
            "trace_sufficiency_certificate": {"raw_paper_count": 32},
            "trace_robust_lineage": {"status": "insufficient_even_optimistically"},
        }
    )

    assert assurance["mode"] == "lineage_screen"
    assert assurance["raw_paper_count"] == 2
    assert assurance["detected_shared_origin_signals"] == 1


def test_paper_search_automatically_exposes_exact_cited_source(monkeypatch) -> None:
    monkeypatch.setattr(
        web_chat,
        "build_public_source_trace",
        lambda _paper_id, _claim: {
            "status": "resolved",
            "references": [
                {
                    "citation": "Direct et al. (2019)",
                    "doi": "10.1000/direct",
                    "url": "https://doi.org/10.1000/direct",
                    "bibliographic_resolution_status": "exact_id",
                }
            ],
        },
    )
    sources = attach_automatic_paper_search_traces(
        [
            {
                "paper_id": "REVIEW",
                "source_type": {"source_type": "narrative_review"},
                "trace_claim_text": "The association was reported by Direct et al. 2019.",
                "can_trace_citations": True,
            }
        ]
    )

    assert sources[0]["citation_trace_required"] is True
    assert sources[0]["cited_sources"][0]["doi"] == "10.1000/direct"


def test_safe_repo_file_accepts_repo_relative_path() -> None:
    path = safe_repo_file("README.md")

    assert path == REPO_ROOT / "README.md"


def test_safe_repo_file_rejects_path_outside_repo() -> None:
    with pytest.raises(ValueError, match="inside the OriginRAG repository"):
        safe_repo_file("/etc/passwd")


def test_safe_public_output_file_restricts_root_and_file_type(tmp_path: Path) -> None:
    output_root = tmp_path / "sessions"
    output_root.mkdir()
    report = output_root / "answer.md"
    report.write_text("answer", encoding="utf-8")
    secret = tmp_path / ".env"
    secret.write_text("SECRET=value", encoding="utf-8")
    binary = output_root / "artifact.bin"
    binary.write_bytes(b"\x00")

    assert safe_public_output_file(str(report), output_root) == report
    with pytest.raises(ValueError, match="web chat output"):
        safe_public_output_file(str(secret), output_root)
    with pytest.raises(ValueError, match="public text"):
        safe_public_output_file(str(binary), output_root)


def test_public_web_templates_use_generic_product_text() -> None:
    for html in (web_chat.INDEX_HTML, web_chat.LOGIN_HTML):
        assert html.lstrip().lower().startswith("<!doctype html>")
        assert "gaia" not in html.lower()
        assert "planned HTTPS" not in html
        assert "e.g. jannes" not in html


def test_web_ui_exposes_one_auto_routed_chat_and_accessible_assurance_controls() -> None:
    html = web_chat.INDEX_HTML

    assert "<title>OriginRAG</title>" in html
    assert "<h1>OriginRAG</h1>" in html
    assert "<h1>PfEMPire</h1>" not in html
    assert 'class="mark"' not in html
    assert "userBadge.textContent = data.user.display_name;" in html
    assert "${data.user.display_name} · ${data.user.role}" not in html
    assert 'document.body.appendChild(menu);' in html
    assert '.session-menu.open {' in html
    assert "position: fixed;" in html
    assert 'id="evidenceSelector"' in html
    assert 'value="trace_flow"' in html
    assert 'role="log"' in html
    assert 'aria-live="polite"' in html
    assert 'id="message"' in html
    assert 'id="form"' in html
    assert 'id="task"' not in html
    assert 'data-task=' not in html
    assert 'class="hero-actions"' not in html
    assert 'id="composerMode"' not in html
    assert 'id="modeHelp"' not in html
    assert "Ask in your own words." not in html
    assert "PfEMP1 · malaria · scientific evidence" not in html
    assert "LOCAL LITERATURE ASSISTANT" not in html
    assert 'class="header-title"' not in html
    assert "Evidence, with <span>its origin intact.</span>" in html
    assert "checks whether papers share scientific origins" not in html
    assert "Advanced controls" not in html
    assert "Lineage-aware mode treats missing origin relations" not in html
    assert "Local only" not in html
    assert 'id="connectionIndicator"' in html
    assert 'id="profileButton" type="button" hidden' in html
    assert 'id="libraryButton" type="button" hidden>Library</button>' in html
    assert 'id="libraryStatsButton" type="button"' in html
    assert 'fetch("/api/library-stats")' in html
    assert 'id="libraryStatsDonut"' in html
    assert 'id="libraryStatsGroups"' in html
    assert "conic-gradient" in html
    assert 'class="library-search" id="librarySearch" type="search"' in html
    assert 'fetch(`/api/library?${params}`)' in html
    assert "This does not run Qwen." not in html
    assert 'doi.target = "_blank";' in html
    assert 'malaria_host_omics: "Malaria · host omics"' in html
    assert 'general_methods: "General methods"' in html
    assert 'article.append(title, metadata, category, links);' in html
    assert 'id="paperUploadButton" type="button" hidden>Add papers</button>' in html
    assert 'id="paperDropZone" role="button"' in html
    assert 'fetch("/api/library-upload"' in html
    assert 'id="librarySyncStatus" role="status"' in html
    assert "already present" in html
    assert 'fieldStatuses[name] === "deterministic_excerpt_supported"' in html
    assert "Deterministically supported by an explicit statement" in html
    assert "Fields have mixed provenance" in html
    assert "Live pipeline status" not in html
    assert 'className = "working-progress"' in html
    assert "Waiting in queue · ${ahead}" in Path(web_chat.__file__).read_text(encoding="utf-8")
    assert 'assuranceFlowStep("Cited reports"' in html
    assert '"Publication families"' in html
    assert 'assuranceFlowStep("Independent studies"' in html
    assert "assurance.cited_publication_family_upper_bound" in html
    assert '`≤${formatNumber(familyCount)}`' in html
    assert 'return {label: "Supported", tone: "ok"}' in html
    assert 'return {label: "More evidence needed", tone: "bad"}' in html
    assert 'return {label: "Study overlap needs review", tone: "warn"}' in html
    assert 'The retrieved papers did not cover all parts of the question.' in html
    assert '.replace(/^[a-f0-9]{12}_/, "").replaceAll("_", " ")' in html
    assert 'unnamed reuse may remain.' in html
    assert 'id="studyAuditBackdrop"' in html
    assert 'id="studyAuditTitle">Study overlap details' in html
    assert 'button.textContent = "View study overlap"' in html
    assert "if (hasOriginScreen(assurance))" in html
    assert 'assurance.mode === "lineage_screen" ? "Returned reports" : "Cited reports"' in html
    assurance_builder = html.split("function buildAssurancePanel(assurance)", 1)[1].split("function buildStudyAuditAction", 1)[0]
    assert "panel.open = false;" in assurance_builder
    assert "Shows the automatic study-overlap check for this result." in html
    assert "The exact independent-study count remains unresolved." in html
    assert "Automatic study-origin check" not in html
    assert "Compare <" not in html
    assert 'Independent studies: unknown until study overlap has been reviewed.' in html
    assert 'id="queueSummary" type="button"' in html
    assert 'id="queueBackdrop"' in html
    assert 'fetch("/api/jobs?details=1")' in html
    assert "Active jobs across all users · questions stay private" in html
    assert "job.question" not in html
    assert 'fetch("/api/jobs")' in html
    assert "Browse or search the shared bibliography. This does not run Qwen." not in html
    assert "Foundation models/RAG" not in html
    assert web_chat.LIBRARY_TRACKER_GROUPS[5][1] == "AI & computational methods"
    assert (
        web_chat.LIBRARY_TRACKER_GROUPS[6][1]
        == "Evidence synthesis & general methods"
    )
    assert "Loaded ${data.title" not in html
    assert "Waiting in queue · ${ahead}" in html
    assert "session-job-indicator" in html
    assert "activeController" not in html
    assert "let sessionLoadSequence = 0;" in html
    assert "let sessionsLoadSequence = 0;" in html
    assert "let sessionNavigationPending = false;" in html
    assert "const loadSequence = ++sessionLoadSequence;" in html
    assert "loadSequence !== sessionLoadSequence" in html
    assert "submittedSession === currentSessionDir" in html
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert web_chat.LOGIN_HTML.lstrip().lower().startswith("<!doctype html>")
    assert (
        "job.session_dir === currentSessionDir\n"
        "            && !previous\n"
        "            && !wrap"
    ) in html
    assert "function scheduleVisibleSessionReconciliation()" in html
    assert 'document.addEventListener("visibilitychange"' in html
    assert 'window.addEventListener("focus", scheduleVisibleSessionReconciliation)' in html
    assert "function shouldDeferSessionReconciliation()" in html
    assert "submittingTurn\n        || sessionNavigationPending" in html
    assert "document.activeElement === message && message.value.trim()" in html
    assert "if (sessionReconcilePending) scheduleVisibleSessionReconciliation();" in html
    assert 'Array.from(document.querySelectorAll("[data-job-id]"))' in html
    assert "if (existing) {\n        updateJobMessage(existing, job);" in html
    assert "if (requestSequence !== sessionsLoadSequence) return;" in html
    assert "window.sessionStorage.setItem(currentSessionStorageKey, currentSessionDir);" in html
    assert (
        'if (response.status === 401'
        in html
        and 'window.sessionStorage.removeItem("pfempire.currentSession");'
        in html
    )
    assert "if (error.httpStatus === 404)" in html
    assert "Your draft was kept;" in html
    assert 'const previousSession = currentSessionDir;' in html
    assert 'currentSessionDir = "";' in html
    assert "const initializeSession = !currentSessionDir && !sessionNavigationPending;" in html
    assert 'search.requested_detail_fields || []' in html
    assert 'developmental_stage_adjustment: "Stage adjusted"' in html
    assert (
        "Model-extracted from retrieved excerpts; verify against the full paper."
        in html
    )
    assert 'if (button.dataset.bound === "true") return;' not in html
    assert "bindExamples" not in html
    assert "loadStatus();" in html
    assert "prefers-reduced-motion" in html
    assert "LLM query expansion" in html
    assert "Deterministic ranking" in html
    assert 'id="sidebarResizer"' in html
    assert 'id="sidebarClose"' in html
    assert "pfempire.sidebarWidth" in html
    assert "pfempire.sidebarCollapsed" in html
    assert "body.sidebar-collapsed main { grid-column: 1 / -1; }" in html
    assert "Auto task" not in html
    assert "Paper search:</strong> finds and ranks local papers" not in html
    assert 'task: document.getElementById("task").value' not in html
    assert 'className = "paper-table"' in html
    assert "Why selected" in html
    assert "const identifiers = buildSourceIdentifierLine(src);" in html
    assert "if (identifiers) paper.appendChild(identifiers);" in html
    assert 'link.textContent = identifier.label;' in html
    assert "Main-paper metadata is shown in the Paper column." in html
    assert 'id="quotaBadge"' not in html
    assert "Secondary-source follow-up" in html
    assert "Likely direct report · automatic" in html
    assert "Reanalysis; original dataset source still needed" in html
    assert "Scientific origin · independence layer" in html
    assert "Independent studies not yet verified." in html
    assert "appendCitationAwareText" in html
    assert "P primary research" in html
    assert "S review/meta-analysis" in html
    assert "Author-year citations open the DOI, PubMed, or PMC page" in html
    assert '<div class="header-left">' in html
    assert 'id="sidebarToggle"' in html.split('<div class="header-actions">')[0]
    assert ">Answer style</button>" in html
    assert "Optional: choose Qwen's tone, length, or level of detail" in html
    assert 'aria-live="polite"></div>' in html
    assert "height: 100dvh;" in html
    assert "grid-template-rows: auto auto minmax(0, 1fr) auto;" in html
    assert '"header"\n        "library-status"\n        "chat"\n        "composer";' in html
    assert "header {\n      grid-area: header;" in html
    assert ".library-sync-status {\n      grid-area: library-status;" in html
    assert "#chat {\n      grid-area: chat;\n      overflow-y: auto;" in html
    assert ".composer {\n      grid-area: composer;\n      position: relative;" in html
    assert "overflow-y: auto;\n      min-height: 0;" in html
    assert "padding: 28px 24px 110px;" not in html
    assert "padding: 20px 14px 110px;" not in html
    assert "grid-area: composer;\n      position: relative;" in html
    assert "position: sticky;\n      bottom: 0;" not in html
    assert "full-text passages" in html
    assert "Study overlap needs review" in html


def test_web_ui_about_explains_evidence_and_overlap_in_plain_language() -> None:
    html = web_chat.INDEX_HTML

    assert 'id="aboutButton"' in html
    assert 'aria-label="About OriginRAG"' in html
    assert 'id="aboutBackdrop" aria-hidden="true"' in html
    assert 'id="aboutTitle">What does this tool do?' in html
    assert "It searches your local scientific literature and generates " \
        "source-grounded answers." in html
    assert "Multiple papers may use the same study or dataset" in html
    assert "Independent studies: ?" in html
    assert "study overlap has not yet been sufficiently reviewed" in html
    assert "Always check the cited paper before using extracted details " \
        "in a publication." in html
    assert 'aboutButton.addEventListener("click", openAbout);' in html
    assert 'if (event.key === "Escape") closeAbout();' in html

    exposed_jargon = (
        "Deterministic fallback",
        "Conservative fallback",
        "Robustly sufficient",
        "Origin review needed",
        "Origins not assessed",
        "study-origin check",
    )
    for phrase in exposed_jargon:
        assert phrase not in html


def test_public_answer_text_hides_internal_chunk_details() -> None:
    answer = """[Warimwe 2009, Results, pages 3, chunk MAL_VAR_0053_CHUNK_0009] reports something (E001).

## Evidence Map
- internal audit detail

## References
- E001: [Warimwe 2009, Results, pages 3, chunk MAL_VAR_0053_CHUNK_0009].
"""

    public = public_answer_text(answer)

    assert "chunk" not in public.lower()
    assert "E001" in public
    assert "Evidence Map" not in public
    assert "References" not in public
    assert "Warimwe 2009" in public


def test_public_answer_text_hides_supporting_evidence_label_lines() -> None:
    answer = """## Supported Research Gaps
- gap: Sex-disaggregated patterns are not mechanistically linked (E001, E002).
  why it matters: It affects interpretation.
  supporting evidence labels: E001, E002
  what evidence is still missing: Matched cohort data.
"""

    public = public_answer_text(answer)

    assert "supporting evidence labels" not in public.lower()
    assert "E001" in public
    assert "Sex-disaggregated patterns" in public


def test_public_answer_text_keeps_normal_chat_prose_readable() -> None:
    answer = """## Evidence-backed statements

PfEMP1 binding to EPCR was associated with severe malaria (E001).

## Limitations / uncertainty

The retrieved evidence does not establish causality (E002).

## References
- E001: [Jespersen 2016, Results]
"""

    public = public_answer_text(answer)

    assert "Evidence-backed statements" not in public
    assert "Limitations / uncertainty" not in public
    assert "PfEMP1 binding to EPCR" in public
    assert "## References" not in public


def test_public_answer_text_does_not_duplicate_retrieved_evidence_phrase() -> None:
    answer = "This answer is limited to the retrieved evidence chunks and should be checked."

    public = public_answer_text(answer)

    assert public == "This answer is limited to the retrieved evidence and should be checked."


def test_format_public_sources_keeps_reference_identifiers() -> None:
    sources = format_public_sources(
        [
            {
                "paper_id": "MAL_VAR_0003",
                "author": "Ndugwa",
                "year": "2026",
                "title": "Expression of specific var gene subtypes",
                "journal": "eLife",
                "doi": "10.7554/eLife.110810.1",
                "pmid": "123",
                "pmcid": "PMC123",
            }
        ]
    )

    assert len(sources) == 1
    source = sources[0]
    assert source["paper_id"] == "MAL_VAR_0003"
    assert source["author"] == "Ndugwa"
    assert source["year"] == "2026"
    assert source["doi"] == "10.7554/eLife.110810.1"
    assert source["pmid"] == "123"
    assert source["pmcid"] == "PMC123"
    assert source["url"] == "https://doi.org/10.7554/eLife.110810.1"
    assert source["title"] == "Expression of specific var gene subtypes"
    assert source["journal"] == "eLife"
    assert source["source_role"] == "answer_evidence"
    assert source["source_type"]["label"]
    assert source["evidence_ids"] == []
    assert source["library_status"]["display"]
    assert source["bib_key"] == "Ndugwa2026ExpressionSpecific"


def test_format_public_sources_preserves_publication_family_audit_fields() -> None:
    reports = [
        {
            "paper_id": "PREPRINT",
            "title": "Preprint version",
            "year": "2021",
            "doi": "10.1101/example",
        },
        {
            "paper_id": "JOURNAL",
            "title": "Journal version",
            "year": "2022",
            "doi": "10.1000/journal",
        },
    ]

    sources = format_public_sources(
        [
            {
                "paper_id": "JOURNAL",
                "title": "Journal version",
                "year": "2022",
                "doi": "10.1000/journal",
                "publication_version_display": "representative_of_detected_family",
                "publication_version_relationship_types": ["same_preprint_family"],
                "publication_family_reports": reports,
                "publication_family_report_count": 2,
            }
        ]
    )

    assert len(sources) == 1
    assert sources[0]["publication_version_display"] == (
        "representative_of_detected_family"
    )
    assert sources[0]["publication_version_relationship_types"] == [
        "same_preprint_family"
    ]
    assert sources[0]["publication_family_report_count"] == 2
    assert [report["paper_id"] for report in sources[0]["publication_family_reports"]] == [
        "PREPRINT",
        "JOURNAL",
    ]


def test_format_public_sources_normalizes_legacy_detail_provenance() -> None:
    sources = format_public_sources(
        [
            {
                "paper_id": "P1",
                "title": "Persisted source result",
                "question_fields": {
                    "cohort": "children with severe malaria",
                    "assay": "RT-qPCR",
                },

                "question_fields_status": "qwen_grounded",
            }
        ]
    )

    assert sources[0]["question_field_statuses"] == {
        "cohort": "model_extracted_unverified",
        "assay": "model_extracted_unverified",
    }
    assert sources[0]["question_fields_status"] == "model_extracted_unverified"


def test_format_public_sources_preserves_deterministic_excerpt_provenance() -> None:
    sources = format_public_sources(
        [
            {
                "paper_id": "P1",
                "title": "Stage-aware transcriptome analysis",
                "question_fields": {
                    "developmental_stage_adjustment": (
                        "yes—parasite developmental stage was adjusted or included "
                        "as a covariate"
                    )
                },
                "question_field_statuses": {
                    "developmental_stage_adjustment": (
                        "deterministic_excerpt_supported"
                    )
                },
                "question_fields_status": "deterministic_excerpt_supported",
            }
        ]
    )

    assert sources[0]["question_field_statuses"] == {
        "developmental_stage_adjustment": "deterministic_excerpt_supported"
    }
    assert sources[0]["question_fields_status"] == (
        "deterministic_excerpt_supported"
    )


def test_format_public_sources_returns_more_than_three_unique_sources() -> None:
    chunks = [
        {
            "paper_id": f"MAL_VAR_{index:04d}",
            "author": f"Author{index}",
            "year": "2026",
            "title": f"Paper {index}",
            "journal": "Journal",
        }
        for index in range(12)
    ]

    sources = format_public_sources(chunks)

    assert len(sources) == 12
    assert sources[0]["paper_id"] == "MAL_VAR_0000"
    assert sources[-1]["paper_id"] == "MAL_VAR_0011"


def test_format_public_sources_deduplicates_by_identifier_not_author_year() -> None:
    sources = format_public_sources(
        [
            {
                "paper_id": "P1",
                "author": "Smith",
                "year": "2024",
                "title": "First work",
                "doi": "https://doi.org/10.1000/ONE",
            },
            {
                "paper_id": "P2",
                "author": "Smith",
                "year": "2024",
                "title": "Second work",
                "doi": "10.1000/two",
            },
            {
                "paper_id": "P1-copy",
                "author": "Smith",
                "year": "2024",
                "title": "First work duplicate metadata",
                "doi": "10.1000/one.",
            },
        ]
    )

    assert [source["doi"] for source in sources] == [
        "https://doi.org/10.1000/ONE",
        "10.1000/two",
    ]


def test_infer_session_title_uses_task_and_topic() -> None:
    assert (
        infer_session_title("find research gaps in sex differences in malaria", "gaps")
        == "Sex Gaps"
    )
    assert (
        infer_session_title("make a hypothesis about PfEMP1 antibody breadth", "hypothesis")
        == "PfEMP1 Hypothesis"
    )
    assert (
        infer_session_title("wie hieß das paper mit der Kenya Kohorte", "sources") == "Kenya Cohort"
    )
    assert (
        infer_session_title(
            "Find papers linking parasite expression to clinical phenotypes; "
            "report the assay and cohort for each paper",
            "sources",
        )
        == "Parasite Expression Search"
    )
    assert (
        infer_session_title(
            "Find RIFIN studies of host-cell remodelling",
            "sources",
        )
        == "RIFIN Search"
    )
    assert infer_session_title("Find studies from a Brazilian cohort", "sources") != (
        "Kenya Cohort"
    )


def test_sanitize_session_title_rejects_numeric_names() -> None:
    assert sanitize_session_title("20260618_154622") == "New chat"
    assert sanitize_session_title("  Sex Gaps\n") == "Sex Gaps"


def test_session_title_uses_saved_title_without_folder_fallback(tmp_path: Path) -> None:
    session_dir = tmp_path / "20260618_154622"
    session_dir.mkdir()

    assert session_title(session_dir) == "New chat"

    write_session_title(session_dir, "Kenya Cohort")

    assert session_title(session_dir) == "Kenya Cohort"


def test_session_title_infers_old_session_title(tmp_path: Path) -> None:
    session_dir = tmp_path / "20260618_154622"
    session_dir.mkdir()
    (session_dir / "session.md").write_text(
        "# OriginRAG Web Chat Session\n\n## Turn: find research gaps in sex differences in malaria\n",
        encoding="utf-8",
    )

    assert session_title(session_dir) == "Sex Gaps"


def test_read_session_turns_parses_saved_chat_summary(tmp_path: Path) -> None:
    session_dir = tmp_path / "20260618_154622"
    session_dir.mkdir()
    (session_dir / "session.md").write_text(
        "\n".join(
            [
                "# OriginRAG Web Chat Session",
                "",
                "## Turn: Which paper had the Kenya cohort?",
                "Retrieval question: Which paper had the Kenya cohort?",
                "Task: sources",
                "Output: outputs/web_chat_sessions/example/turn_001",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert read_session_turns(session_dir) == [
        {
            "user": "Which paper had the Kenya cohort?",
            "retrieval_question": "Which paper had the Kenya cohort?",
            "task": "sources",
            "output": "outputs/web_chat_sessions/example/turn_001",
        }
    ]


def test_read_session_turns_parses_sources_for_followup_context(tmp_path: Path) -> None:
    session_dir = tmp_path / "20260618_154622"
    session_dir.mkdir()
    (session_dir / "session.md").write_text(
        "\n".join(
            [
                "# OriginRAG Web Chat Session",
                "",
                "## Turn: Why is P. falciparum virulent?",
                "Retrieval question: Why is P. falciparum virulent?",
                "Task: answer",
                "Output: outputs/web_chat_sessions/example/turn_001",
                "Sources:",
                "- E001: [Turner 2013, Results]",
                "- E002: [Jespersen 2016, Results]",
                "",
            ]
        ),
        encoding="utf-8",
    )

    turns = read_session_turns(session_dir)

    assert turns[0]["sources"] == [
        "E001: [Turner 2013, Results]",
        "E002: [Jespersen 2016, Results]",
    ]


def test_session_messages_restore_saved_public_result_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_chat, "REPO_ROOT", tmp_path)
    session_dir = tmp_path / "outputs" / "web_chat_sessions" / "saved"
    turn_dir = session_dir / "turn_001_question"
    turn_dir.mkdir(parents=True)
    (session_dir / "session.md").write_text(
        "\n".join(
            [
                "## Turn: Is the evidence sufficient?",
                "Output: outputs/web_chat_sessions/saved/turn_001_question",
                "",
            ]
        ),
        encoding="utf-8",
    )
    web_chat.write_json(
        turn_dir / "public_result.json",
        {
            "answer": "The answer remains provisional.",
            "answer_state": "answered",
            "evidence_grade": "usable",
            "assurance": {"mode": "lineage_aware", "audit_run": True},
            "sources": [{"paper_id": "P1", "title": "Paper one"}],
        },
    )

    messages = session_messages(session_dir)

    assert messages[1]["text"] == "The answer remains provisional."
    assert messages[1]["result"]["assurance"]["audit_run"] is True
    assert messages[1]["result"]["sources"][0]["paper_id"] == "P1"


def test_list_saved_sessions_returns_all_short_titles(tmp_path: Path) -> None:
    first = tmp_path / "20260618_100000"
    second = tmp_path / "20260618_110000"
    first.mkdir()
    second.mkdir()
    (first / "session.md").write_text(
        "## Turn: find research gaps in sex differences in malaria\n",
        encoding="utf-8",
    )
    (second / "session.md").write_text(
        "## Turn: which paper had the Kenya cohort\n",
        encoding="utf-8",
    )

    sessions = list_saved_sessions(tmp_path, second)

    assert len(sessions) == 2
    assert {session["title"] for session in sessions} == {"Sex Gaps", "Kenya Cohort"}
    assert any(session["current"] for session in sessions)


def test_list_saved_sessions_hides_empty_new_chat_dirs(tmp_path: Path) -> None:
    empty = tmp_path / "20260618_100000"
    saved = tmp_path / "20260618_110000"
    empty.mkdir()
    saved.mkdir()
    (empty / "session.md").write_text("# OriginRAG Web Chat Session\n", encoding="utf-8")
    (saved / "session.md").write_text(
        "## Turn: Which paper had the Kenya cohort?\n", encoding="utf-8"
    )

    sessions = list_saved_sessions(tmp_path, empty)

    assert [session["title"] for session in sessions] == ["Kenya Cohort"]


def test_delete_session_removes_only_requested_session(tmp_path: Path) -> None:
    app = WebChatApp(WebChatSettings(), out_dir=str(tmp_path))
    saved = tmp_path / "saved_chat"
    saved.mkdir()
    (saved / "session.md").write_text(
        "## Turn: Which paper had the Kenya cohort?\n", encoding="utf-8"
    )
    current_dir = app.session_dir

    response = app.delete_session(str(saved))

    assert not saved.exists()
    assert current_dir.exists()
    assert response["session_dir"] == str(current_dir)


def test_anonymous_admin_review_log_excludes_account_and_session_identity(
    tmp_path: Path,
) -> None:
    append_anonymous_review_event(
        tmp_path,
        conversation_id="anonymous-conversation",
        question="Which paper supports this claim?",
        response={
            "answer": "Paper A supports the claim.",
            "task": "answer",
            "evidence_grade": "usable",
            "elapsed_seconds": 12.345,
            "sources": [
                {
                    "citation": "Paper A (2024)",
                    "doi": "10.1000/example",
                    "source_type": "primary_research",
                }
            ],
            "session_dir": "outputs/users/secret-user/session",
            "user": {"email": "person@bnitm.de"},
        },
        lock=threading.Lock(),
    )

    review_path = tmp_path / "admin_review" / "anonymous_turns.jsonl"
    event = json.loads(review_path.read_text(encoding="utf-8"))

    assert event["conversation_id"] == "anonymous-conversation"
    assert event["question"] == "Which paper supports this claim?"
    assert event["elapsed_seconds"] == 12.345
    assert "secret-user" not in review_path.read_text(encoding="utf-8")
    assert "person@bnitm.de" not in review_path.read_text(encoding="utf-8")
