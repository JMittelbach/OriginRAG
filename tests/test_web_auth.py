from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import time
from collections.abc import Iterator

import pytest

from originrag.web_auth import (
    DAILY_MODEL_REQUEST_LIMIT,
    LOCKOUT_SECONDS,
    MAX_FAILED_ATTEMPTS,
    MODEL_REQUEST_BURST_LIMIT,
    MODEL_REQUEST_BURST_WINDOW_SECONDS,
    SESSION_TOUCH_INTERVAL_SECONDS,
    AuthStore,
    AuthenticationError,
    PasswordPolicyError,
    QuotaExceededError,
    RegistrationError,
    hash_token,
    normalize_bnitm_email,
    validate_password,
)


def test_password_policy_enforces_registration_requirements_and_rejects_identity() -> None:
    validate_password("quiet-lantern-river-orbit-47", username="alice")

    with pytest.raises(PasswordPolicyError, match="at least"):
        validate_password("short-password")
    with pytest.raises(PasswordPolicyError, match="username"):
        validate_password("alice-has-a-very-long-password", username="alice")
    with pytest.raises(PasswordPolicyError, match="letter"):
        validate_password("1234567890!23")
    with pytest.raises(PasswordPolicyError, match="two numbers"):
        validate_password("long-password-1")
    with pytest.raises(PasswordPolicyError, match="special"):
        validate_password("longpassword123")
    with pytest.raises(PasswordPolicyError, match="two numbers"):
        validate_password("aaaaaaaaaaaaaaaa")


def test_account_authentication_stores_only_hashes_and_revokes_logout(tmp_path) -> None:
    store = AuthStore(tmp_path / "auth.sqlite")
    user = store.create_user(
        "alice",
        "quiet-lantern-river-orbit-47",
        display_name="Alice Example",
    )

    token, session = store.authenticate(
        "alice",
        "quiet-lantern-river-orbit-47",
        user_agent="test-browser",
    )
    assert session.user == user
    assert session.csrf_token
    assert store.get_session(token).user == user
    assert store.verify_csrf(token, session.csrf_token) is True
    assert store.verify_csrf(token, "wrong") is False

    with sqlite3.connect(store.db_path) as connection:
        row = connection.execute(
            "SELECT token_hash, csrf_hash FROM web_sessions"
        ).fetchone()
    assert row[0] == hash_token(token)
    assert token.encode() not in bytes(row[0])
    assert session.csrf_token.encode() not in bytes(row[1])

    store.logout(token)
    assert store.get_session(token) is None


def test_auth_store_expands_home_relative_database_path(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    store = AuthStore("~/.local/state/pfempire/auth.sqlite")

    assert store.db_path == tmp_path / ".local/state/pfempire/auth.sqlite"
    assert store.db_path.is_file()


def test_auth_store_replaces_wal_with_rollback_journaling(tmp_path) -> None:
    db_path = tmp_path / "auth.sqlite"
    with closing(sqlite3.connect(db_path)) as connection:
        mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        assert mode == "wal"
        connection.execute("CREATE TABLE legacy_state (value INTEGER)")
        connection.commit()

    store = AuthStore(db_path)

    with store.connect() as connection:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        legacy_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'legacy_state'"
        ).fetchone()
    assert mode == "delete"
    assert legacy_table is not None
    assert not db_path.with_name(f"{db_path.name}-wal").exists()
    assert not db_path.with_name(f"{db_path.name}-shm").exists()


def test_session_touch_is_throttled_and_extends_idle_deadline(tmp_path, monkeypatch) -> None:
    store = AuthStore(tmp_path / "auth.sqlite")
    store.create_user("alice", "quiet-lantern-river-orbit-47")
    token, _ = store.authenticate("alice", "quiet-lantern-river-orbit-47")
    token_hash = hash_token(token)
    baseline = 1_800_000_000
    with store.connect() as connection:
        connection.execute(
            """
            UPDATE web_sessions
            SET last_seen_at = ?, idle_expires_at = ?, expires_at = ?
            WHERE token_hash = ?
            """,
            (baseline, baseline + 10_000, baseline + 20_000, token_hash),
        )

    monkeypatch.setattr(
        "originrag.web_auth.time.time",
        lambda: baseline + SESSION_TOUCH_INTERVAL_SECONDS - 1,
    )
    assert store.get_session(token) is not None
    with store.connect() as connection:
        unchanged = connection.execute(
            "SELECT last_seen_at, idle_expires_at FROM web_sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
    assert tuple(unchanged) == (baseline, baseline + 10_000)

    due_at = baseline + SESSION_TOUCH_INTERVAL_SECONDS
    monkeypatch.setattr("originrag.web_auth.time.time", lambda: due_at)
    assert store.get_session(token) is not None
    with store.connect() as connection:
        refreshed = connection.execute(
            "SELECT last_seen_at, idle_expires_at FROM web_sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
    assert tuple(refreshed) == (due_at, due_at + 30 * 60)


def test_parallel_session_touches_are_coalesced_without_database_lock(
    tmp_path, monkeypatch
) -> None:
    store = AuthStore(tmp_path / "auth.sqlite")
    user = store.create_user("alice", "quiet-lantern-river-orbit-47")
    token, _ = store.authenticate("alice", "quiet-lantern-river-orbit-47")
    token_hash = hash_token(token)
    baseline = 1_800_000_000
    due_at = baseline + SESSION_TOUCH_INTERVAL_SECONDS
    with store.connect() as connection:
        connection.execute(
            """
            UPDATE web_sessions
            SET last_seen_at = ?, idle_expires_at = ?, expires_at = ?
            WHERE token_hash = ?
            """,
            (baseline, baseline + 10_000, baseline + 20_000, token_hash),
        )

    original_connect = store.connect

    @contextmanager
    def contention_sensitive_connect() -> Iterator[sqlite3.Connection]:
        with original_connect() as connection:
            connection.execute("PRAGMA busy_timeout = 0")
            connection.create_function("slow_session_touch", 0, lambda: time.sleep(0.05))
            yield connection

    with original_connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER slow_web_session_touch
            BEFORE UPDATE OF last_seen_at ON web_sessions
            WHEN NEW.last_seen_at > OLD.last_seen_at
            BEGIN
                SELECT slow_session_touch();
            END
            """
        )
    monkeypatch.setattr(store, "connect", contention_sensitive_connect)
    monkeypatch.setattr("originrag.web_auth.time.time", lambda: due_at)

    with ThreadPoolExecutor(max_workers=12) as executor:
        sessions = list(executor.map(lambda _: store.get_session(token), range(24)))

    assert all(session is not None and session.user == user for session in sessions)
    with original_connect() as connection:
        refreshed = connection.execute(
            "SELECT last_seen_at, idle_expires_at FROM web_sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
    assert tuple(refreshed) == (due_at, due_at + 30 * 60)


def test_failed_logins_lock_account_and_password_reset_revokes_sessions(tmp_path, monkeypatch) -> None:
    store = AuthStore(tmp_path / "auth.sqlite")
    store.create_user("alice", "quiet-lantern-river-orbit-47")
    for _ in range(MAX_FAILED_ATTEMPTS):
        with pytest.raises(AuthenticationError):
            store.authenticate("alice", "this-is-the-wrong-password")

    with pytest.raises(AuthenticationError, match="temporarily locked"):
        store.authenticate("alice", "quiet-lantern-river-orbit-47")

    with store.connect() as connection:
        connection.execute(
            "UPDATE web_users SET locked_until = ? WHERE username = 'alice'",
            (1,),
        )
    token, _ = store.authenticate("alice", "quiet-lantern-river-orbit-47")
    store.reset_password("alice", "violet-planet-canvas-forest-83")
    assert store.get_session(token) is None
    with pytest.raises(AuthenticationError):
        store.authenticate("alice", "quiet-lantern-river-orbit-47")
    store.authenticate("alice", "violet-planet-canvas-forest-83")


def test_disabling_account_revokes_sessions(tmp_path) -> None:
    store = AuthStore(tmp_path / "auth.sqlite")
    store.create_user("alice", "quiet-lantern-river-orbit-47")
    token, _ = store.authenticate("alice", "quiet-lantern-river-orbit-47")
    store.set_disabled("alice", disabled=True)

    assert store.get_session(token) is None
    with pytest.raises(AuthenticationError):
        store.authenticate("alice", "quiet-lantern-river-orbit-47")

    store.set_disabled("alice", disabled=False)
    store.authenticate("alice", "quiet-lantern-river-orbit-47")


def test_maximum_active_sessions_revokes_oldest(tmp_path) -> None:
    store = AuthStore(tmp_path / "auth.sqlite")
    store.create_user("alice", "quiet-lantern-river-orbit-47")
    tokens = [
        store.authenticate("alice", "quiet-lantern-river-orbit-47")[0]
        for _ in range(4)
    ]
    assert store.get_session(tokens[0]) is None
    assert all(store.get_session(token) is not None for token in tokens[1:])


def test_lockout_configuration_is_not_accidentally_weakened() -> None:
    assert MAX_FAILED_ATTEMPTS <= 5
    assert LOCKOUT_SECONDS >= 15 * 60


def test_model_quota_is_persistent_and_burst_limited(tmp_path) -> None:
    store = AuthStore(tmp_path / "auth.sqlite")
    user = store.create_user("alice", "quiet-lantern-river-orbit-47")
    for offset in range(MODEL_REQUEST_BURST_LIMIT):
        status = store.reserve_model_request(user.user_id, now=1_800_000_000 + offset)
    assert status.model_requests_used == MODEL_REQUEST_BURST_LIMIT
    assert status.model_requests_limit is DAILY_MODEL_REQUEST_LIMIT is None
    assert status.input_tokens_reserved > 0

    with pytest.raises(QuotaExceededError, match="Too many"):
        store.reserve_model_request(user.user_id, now=1_800_000_010)

    persisted = AuthStore(store.db_path).quota_status(user.user_id, now=1_800_000_010)
    assert persisted == status


def test_model_usage_has_no_daily_hard_limit(tmp_path) -> None:
    store = AuthStore(tmp_path / "auth.sqlite")
    user = store.create_user("unlimited", "quiet-lantern-river-orbit-47")
    for day_offset in range(40):
        status = store.reserve_model_request(
            user.user_id,
            now=1_800_000_000 + day_offset * (MODEL_REQUEST_BURST_WINDOW_SECONDS + 1),
        )
    assert status.model_requests_limit is None
    assert status.input_tokens_limit is None
    assert status.output_tokens_limit is None


def test_bnitm_registration_verifies_mailbox_and_stores_only_hashes(tmp_path) -> None:
    store = AuthStore(tmp_path / "auth.sqlite")
    challenge = store.begin_registration(
        "Alice.Example@BNITM.DE",
        "quiet-lantern-river-orbit-47",
        display_name="Alice Example",
        source_key="127.0.0.1",
        now=1_800_000_000,
    )

    assert challenge is not None
    assert challenge.email == "alice.example@bnitm.de"
    assert len(challenge.code) == 6
    with sqlite3.connect(store.db_path) as connection:
        row = connection.execute(
            "SELECT password_hash, code_hash FROM web_pending_registrations"
        ).fetchone()
    assert challenge.code.encode() not in bytes(row[1])
    assert b"quiet-lantern-river-orbit-47" not in bytes(row[0])

    token, session = store.complete_registration(
        challenge.email,
        challenge.code,
        user_agent="test-browser",
        remember=True,
        now=1_800_000_010,
    )

    assert session.user.username == "alice.example"
    assert session.user.role == "user"
    assert session.expires_at - 1_800_000_010 >= 29 * 24 * 60 * 60
    assert store.get_session(token, touch=False) is not None
    store.authenticate("alice.example@bnitm.de", "quiet-lantern-river-orbit-47")


def test_registration_rejects_non_bnitm_domains_and_expired_or_reused_codes(tmp_path) -> None:
    store = AuthStore(tmp_path / "auth.sqlite")
    with pytest.raises(ValueError, match="@bnitm.de"):
        normalize_bnitm_email("person@example.org")

    challenge = store.begin_registration(
        "person@bnitm.de",
        "violet-planet-canvas-forest-83",
        display_name="Person",
        source_key="127.0.0.1",
        now=1_800_000_000,
    )
    assert challenge is not None
    with pytest.raises(RegistrationError, match="expired"):
        store.complete_registration(
            challenge.email,
            challenge.code,
            now=challenge.expires_at + 1,
        )


def test_registration_code_attempts_and_resends_are_rate_limited(tmp_path) -> None:
    store = AuthStore(tmp_path / "auth.sqlite")
    challenge = store.begin_registration(
        "person@bnitm.de",
        "violet-planet-canvas-forest-83",
        display_name="Person",
        source_key="127.0.0.1",
        now=1_800_000_000,
    )
    assert challenge is not None
    with pytest.raises(RegistrationError, match="wait"):
        store.begin_registration(
            "person@bnitm.de",
            "violet-planet-canvas-forest-83",
            display_name="Person",
            source_key="127.0.0.1",
            now=1_800_000_010,
        )
    for offset in range(5):
        with pytest.raises(RegistrationError, match="Invalid or expired"):
            store.complete_registration(
                challenge.email,
                "000000" if challenge.code != "000000" else "999999",
                now=1_800_000_020 + offset,
            )
    with pytest.raises(RegistrationError, match="Invalid or expired"):
        store.complete_registration(
            challenge.email,
            challenge.code,
            now=1_800_000_030,
        )
