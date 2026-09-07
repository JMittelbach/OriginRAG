from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


PASSWORD_MIN_LENGTH = 13
PASSWORD_MAX_LENGTH = 128
USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{2,31}$")
SESSION_TTL_SECONDS = 8 * 60 * 60
SESSION_IDLE_SECONDS = 30 * 60
REMEMBER_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60



SESSION_TOUCH_INTERVAL_SECONDS = 60
VERIFICATION_TTL_SECONDS = 10 * 60
VERIFICATION_MAX_ATTEMPTS = 5
REGISTRATION_RESEND_SECONDS = 60
REGISTRATION_EMAIL_HOURLY_LIMIT = 5
REGISTRATION_SOURCE_HOURLY_LIMIT = 12
MAX_ACTIVE_SESSIONS = 3
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60
DAILY_MODEL_REQUEST_LIMIT: int | None = None
DAILY_INPUT_TOKEN_RESERVATION: int | None = None
DAILY_OUTPUT_TOKEN_RESERVATION: int | None = None
INPUT_TOKENS_RESERVED_PER_REQUEST = 10_000
OUTPUT_TOKENS_RESERVED_PER_REQUEST = 2_200
MODEL_REQUEST_BURST_LIMIT = 5
MODEL_REQUEST_BURST_WINDOW_SECONDS = 10 * 60
MAX_SAVED_CHATS = 200
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 256 * 1024 * 1024

COMMON_PASSWORDS = {
    "1234567890123456",
    "correcthorsebatterystaple",
    "letmeinletmeinletmein",
    "passwordpassword",
    "pfempirepfempire",
    "qwertyqwertyqwerty",
}


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    username: str
    display_name: str
    role: str


@dataclass(frozen=True)
class AuthSession:
    user: AuthenticatedUser
    csrf_token: str
    expires_at: int


@dataclass(frozen=True)
class QuotaStatus:
    model_requests_used: int
    model_requests_limit: int | None
    input_tokens_reserved: int
    input_tokens_limit: int | None
    output_tokens_reserved: int
    output_tokens_limit: int | None


class QuotaExceededError(ValueError):
    pass


class AuthenticationError(ValueError):
    pass


class PasswordPolicyError(ValueError):
    pass


class RegistrationError(ValueError):
    pass


@dataclass(frozen=True)
class RegistrationChallenge:
    email: str
    code: str
    expires_at: int


class AuthStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self._db_lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.db_path.parent, 0o700)
        except OSError:
            pass
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:



        with self._db_lock:
            connection = sqlite3.connect(self.db_path, timeout=30)
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 30000")
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
            finally:
                connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:




            journal_mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
            if str(journal_mode).lower() != "delete":
                raise sqlite3.OperationalError(
                    f"auth database requires DELETE journal mode, got {journal_mode!r}"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS web_users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'reviewer', 'admin')),
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until INTEGER,
                    disabled_at INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS web_sessions (
                    token_hash BLOB PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    csrf_hash BLOB NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    idle_expires_at INTEGER NOT NULL,
                    revoked_at INTEGER,
                    remembered INTEGER NOT NULL DEFAULT 0,
                    user_agent_hash BLOB,
                    FOREIGN KEY (user_id) REFERENCES web_users(user_id)
                );
                CREATE INDEX IF NOT EXISTS web_sessions_user_idx
                    ON web_sessions(user_id, created_at);
                CREATE TABLE IF NOT EXISTS web_audit_events (
                    event_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    event_type TEXT NOT NULL,
                    occurred_at INTEGER NOT NULL,
                    detail TEXT,
                    FOREIGN KEY (user_id) REFERENCES web_users(user_id)
                );
                CREATE INDEX IF NOT EXISTS web_audit_user_time_idx
                    ON web_audit_events(user_id, occurred_at);
                CREATE TABLE IF NOT EXISTS web_usage_daily (
                    user_id TEXT NOT NULL,
                    usage_day TEXT NOT NULL,
                    model_requests INTEGER NOT NULL DEFAULT 0,
                    input_tokens_reserved INTEGER NOT NULL DEFAULT 0,
                    output_tokens_reserved INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, usage_day),
                    FOREIGN KEY (user_id) REFERENCES web_users(user_id)
                );
                CREATE TABLE IF NOT EXISTS web_pending_registrations (
                    email TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    code_salt BLOB NOT NULL,
                    code_hash BLOB NOT NULL,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    last_sent_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS web_registration_events (
                    event_id TEXT PRIMARY KEY,
                    email_hash BLOB NOT NULL,
                    source_hash BLOB NOT NULL,
                    occurred_at INTEGER NOT NULL,
                    outcome TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS web_registration_events_time_idx
                    ON web_registration_events(occurred_at);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(web_users)").fetchall()
            }
            if "email" not in columns:
                connection.execute("ALTER TABLE web_users ADD COLUMN email TEXT")
            if "email_verified_at" not in columns:
                connection.execute("ALTER TABLE web_users ADD COLUMN email_verified_at INTEGER")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS web_users_email_unique "
                "ON web_users(lower(email)) WHERE email IS NOT NULL"
            )
            session_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(web_sessions)").fetchall()
            }
            if "remembered" not in session_columns:
                connection.execute(
                    "ALTER TABLE web_sessions ADD COLUMN remembered INTEGER NOT NULL DEFAULT 0"
                )
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def user_count(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM web_users").fetchone()
        return int(row["count"])

    def create_user(
        self,
        username: str,
        password: str,
        *,
        display_name: str | None = None,
        role: str = "user",
    ) -> AuthenticatedUser:
        clean_username = normalize_username(username)
        clean_display_name = normalize_display_name(display_name or clean_username)
        clean_role = normalize_role(role)
        validate_password(password, username=clean_username, display_name=clean_display_name)
        salt = secrets.token_bytes(32)
        password_hash = hash_password(password, salt)
        now = int(time.time())
        user = AuthenticatedUser(
            user_id=str(uuid.uuid4()),
            username=clean_username,
            display_name=clean_display_name,
            role=clean_role,
        )
        try:
            with self.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO web_users (
                        user_id, username, display_name, role, password_salt,
                        password_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user.user_id,
                        user.username,
                        user.display_name,
                        user.role,
                        salt,
                        password_hash,
                        now,
                        now,
                    ),
                )
                self._audit(connection, user.user_id, "account_created", user.role)
        except sqlite3.IntegrityError as exc:
            raise ValueError("Account name already exists.") from exc
        return user

    def begin_registration(
        self,
        email: str,
        password: str,
        *,
        display_name: str,
        source_key: str,
        now: int | None = None,
    ) -> RegistrationChallenge | None:
        clean_email = normalize_bnitm_email(email)
        clean_display_name = normalize_display_name(display_name)
        username_hint = username_from_email(clean_email)
        validate_password(
            password,
            username=username_hint,
            display_name=clean_display_name,
        )
        timestamp = int(now if now is not None else time.time())
        email_hash = hash_token(clean_email)
        source_hash = hash_token(source_key or "unknown")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._enforce_registration_rate_limit(
                connection,
                email_hash=email_hash,
                source_hash=source_hash,
                now=timestamp,
            )


            password_salt = secrets.token_bytes(32)
            password_hash = hash_password(password, password_salt)
            existing = connection.execute(
                "SELECT 1 FROM web_users WHERE lower(email) = ?",
                (clean_email,),
            ).fetchone()
            pending = connection.execute(
                "SELECT last_sent_at FROM web_pending_registrations WHERE email = ?",
                (clean_email,),
            ).fetchone()
            if pending and timestamp - int(pending["last_sent_at"]) < REGISTRATION_RESEND_SECONDS:
                raise RegistrationError("Please wait before requesting another code.")
            if existing:
                self._registration_event(
                    connection, email_hash, source_hash, timestamp, "existing_account"
                )
                return None

            code = f"{secrets.randbelow(1_000_000):06d}"
            code_salt = secrets.token_bytes(32)
            code_hash = hash_password(code, code_salt)
            expires_at = timestamp + VERIFICATION_TTL_SECONDS
            connection.execute(
                """
                INSERT INTO web_pending_registrations (
                    email, display_name, password_salt, password_hash,
                    code_salt, code_hash, failed_attempts, created_at,
                    expires_at, last_sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    display_name = excluded.display_name,
                    password_salt = excluded.password_salt,
                    password_hash = excluded.password_hash,
                    code_salt = excluded.code_salt,
                    code_hash = excluded.code_hash,
                    failed_attempts = 0,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at,
                    last_sent_at = excluded.last_sent_at
                """,
                (
                    clean_email,
                    clean_display_name,
                    password_salt,
                    password_hash,
                    code_salt,
                    code_hash,
                    timestamp,
                    expires_at,
                    timestamp,
                ),
            )
            self._registration_event(connection, email_hash, source_hash, timestamp, "code_created")
        return RegistrationChallenge(clean_email, code, expires_at)

    def cancel_registration(self, email: str) -> None:
        try:
            clean_email = normalize_bnitm_email(email)
        except ValueError:
            return
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM web_pending_registrations WHERE email = ?", (clean_email,)
            )

    def complete_registration(
        self,
        email: str,
        code: str,
        *,
        user_agent: str = "",
        remember: bool = False,
        now: int | None = None,
    ) -> tuple[str, AuthSession]:
        clean_email = normalize_bnitm_email(email)
        clean_code = re.sub(r"\s+", "", str(code or ""))
        timestamp = int(now if now is not None else time.time())
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM web_pending_registrations WHERE email = ?",
                (clean_email,),
            ).fetchone()
            if not row:
                dummy_verify_password(clean_code)
                raise RegistrationError("Invalid or expired verification code.")
            if int(row["expires_at"]) <= timestamp:
                connection.execute(
                    "DELETE FROM web_pending_registrations WHERE email = ?", (clean_email,)
                )
                raise RegistrationError("Invalid or expired verification code.")
            failures = int(row["failed_attempts"] or 0)
            if failures >= VERIFICATION_MAX_ATTEMPTS or not verify_password(
                clean_code,
                bytes(row["code_salt"]),
                bytes(row["code_hash"]),
            ):
                failures += 1
                if failures >= VERIFICATION_MAX_ATTEMPTS:
                    connection.execute(
                        "DELETE FROM web_pending_registrations WHERE email = ?",
                        (clean_email,),
                    )
                else:
                    connection.execute(
                        "UPDATE web_pending_registrations SET failed_attempts = ? WHERE email = ?",
                        (failures, clean_email),
                    )
                connection.commit()
                raise RegistrationError("Invalid or expired verification code.")

            username = self._available_username(connection, username_from_email(clean_email))
            user = AuthenticatedUser(
                user_id=str(uuid.uuid4()),
                username=username,
                display_name=str(row["display_name"]),
                role="user",
            )
            try:
                connection.execute(
                    """
                    INSERT INTO web_users (
                        user_id, username, display_name, role, password_salt,
                        password_hash, email, email_verified_at, created_at, updated_at
                    ) VALUES (?, ?, ?, 'user', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user.user_id,
                        user.username,
                        user.display_name,
                        row["password_salt"],
                        row["password_hash"],
                        clean_email,
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RegistrationError("This account cannot be created.") from exc
            connection.execute(
                "DELETE FROM web_pending_registrations WHERE email = ?", (clean_email,)
            )
            token, session = self._create_session(
                connection,
                user,
                now=timestamp,
                user_agent=user_agent,
                remember=remember,
            )
            self._audit(connection, user.user_id, "account_verified", "bnitm_email")
            return token, session

    def list_users(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id, username, display_name, role, disabled_at,
                       failed_attempts, locked_until, created_at
                FROM web_users ORDER BY username
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def set_disabled(self, username: str, *, disabled: bool) -> None:
        clean_username = normalize_username(username)
        now = int(time.time())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT user_id FROM web_users WHERE username = ?", (clean_username,)
            ).fetchone()
            if not row:
                raise ValueError("User not found.")
            disabled_at = now if disabled else None
            connection.execute(
                "UPDATE web_users SET disabled_at = ?, updated_at = ? WHERE user_id = ?",
                (disabled_at, now, row["user_id"]),
            )
            if disabled:
                connection.execute(
                    "UPDATE web_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                    (now, row["user_id"]),
                )
            self._audit(
                connection,
                str(row["user_id"]),
                "account_disabled" if disabled else "account_enabled",
            )

    def reset_password(self, username: str, password: str) -> None:
        clean_username = normalize_username(username)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT user_id, display_name FROM web_users WHERE username = ?",
                (clean_username,),
            ).fetchone()
            if not row:
                raise ValueError("User not found.")
            validate_password(
                password,
                username=clean_username,
                display_name=str(row["display_name"]),
            )
            salt = secrets.token_bytes(32)
            password_hash = hash_password(password, salt)
            now = int(time.time())
            connection.execute(
                """
                UPDATE web_users
                SET password_salt = ?, password_hash = ?, failed_attempts = 0,
                    locked_until = NULL, updated_at = ?
                WHERE user_id = ?
                """,
                (salt, password_hash, now, row["user_id"]),
            )
            connection.execute(
                "UPDATE web_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (now, row["user_id"]),
            )
            self._audit(connection, str(row["user_id"]), "password_reset")

    def authenticate(
        self,
        username: str,
        password: str,
        *,
        user_agent: str = "",
        remember: bool = False,
    ) -> tuple[str, AuthSession]:
        clean_username = str(username or "").strip().lower()
        now = int(time.time())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM web_users WHERE username = ? OR lower(email) = ?",
                (clean_username, clean_username),
            ).fetchone()
            if not row:
                dummy_verify_password(password)
                raise AuthenticationError("Invalid username or password.")
            if row["disabled_at"] is not None:
                raise AuthenticationError("Invalid username or password.")
            locked_until = int(row["locked_until"] or 0)
            if locked_until > now:
                raise AuthenticationError("Account temporarily locked. Try again later.")
            if not verify_password(password, bytes(row["password_salt"]), bytes(row["password_hash"])):
                failures = int(row["failed_attempts"] or 0) + 1
                new_locked_until = now + LOCKOUT_SECONDS if failures >= MAX_FAILED_ATTEMPTS else None
                connection.execute(
                    "UPDATE web_users SET failed_attempts = ?, locked_until = ? WHERE user_id = ?",
                    (failures, new_locked_until, row["user_id"]),
                )
                self._audit(connection, str(row["user_id"]), "login_failed")


                connection.commit()
                raise AuthenticationError("Invalid username or password.")

            connection.execute(
                "UPDATE web_users SET failed_attempts = 0, locked_until = NULL WHERE user_id = ?",
                (row["user_id"],),
            )
            user = row_to_user(row)
            token, session = self._create_session(
                connection,
                user,
                now=now,
                user_agent=user_agent,
                remember=remember,
            )
            self._audit(connection, user.user_id, "login_succeeded")
            return token, session

    def get_session(self, token: str, *, touch: bool = True) -> AuthSession | None:
        if not token:
            return None
        token_hash = hash_token(token)
        now = int(time.time())
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT s.*, u.username, u.display_name, u.role, u.disabled_at
                FROM web_sessions s JOIN web_users u ON u.user_id = s.user_id
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if not row:
                return None
            if (
                row["revoked_at"] is not None
                or row["disabled_at"] is not None
                or int(row["expires_at"]) <= now
                or int(row["idle_expires_at"]) <= now
            ):
                connection.execute(
                    "UPDATE web_sessions SET revoked_at = COALESCE(revoked_at, ?) WHERE token_hash = ?",
                    (now, token_hash),
                )
                return None
            if touch and int(row["last_seen_at"]) <= now - SESSION_TOUCH_INTERVAL_SECONDS:
                idle_ttl = (
                    REMEMBER_SESSION_TTL_SECONDS
                    if int(row["remembered"] or 0)
                    else SESSION_IDLE_SECONDS
                )



                connection.execute(
                    """
                    UPDATE web_sessions
                    SET last_seen_at = ?, idle_expires_at = ?
                    WHERE token_hash = ? AND revoked_at IS NULL
                      AND last_seen_at <= ?
                    """,
                    (
                        now,
                        now + idle_ttl,
                        token_hash,
                        now - SESSION_TOUCH_INTERVAL_SECONDS,
                    ),
                )
            return AuthSession(
                user=AuthenticatedUser(
                    user_id=str(row["user_id"]),
                    username=str(row["username"]),
                    display_name=str(row["display_name"]),
                    role=str(row["role"]),
                ),
                csrf_token="",
                expires_at=int(row["expires_at"]),
            )

    def verify_csrf(self, token: str, csrf_token: str) -> bool:
        if not token or not csrf_token:
            return False
        with self.connect() as connection:
            row = connection.execute(
                "SELECT csrf_hash FROM web_sessions WHERE token_hash = ? AND revoked_at IS NULL",
                (hash_token(token),),
            ).fetchone()
        return bool(row) and hmac.compare_digest(bytes(row["csrf_hash"]), hash_token(csrf_token))

    def logout(self, token: str) -> None:
        if not token:
            return
        now = int(time.time())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT user_id FROM web_sessions WHERE token_hash = ?",
                (hash_token(token),),
            ).fetchone()
            connection.execute(
                "UPDATE web_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                (now, hash_token(token)),
            )
            if row:
                self._audit(connection, str(row["user_id"]), "logout")

    def reserve_model_request(self, user_id: str, *, now: int | None = None) -> QuotaStatus:
        timestamp = int(now if now is not None else time.time())
        usage_day = datetime.fromtimestamp(timestamp, tz=UTC).date().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            recent = connection.execute(
                """
                SELECT COUNT(*) AS count FROM web_audit_events
                WHERE user_id = ? AND event_type = 'model_request_reserved' AND occurred_at > ?
                """,
                (user_id, timestamp - MODEL_REQUEST_BURST_WINDOW_SECONDS),
            ).fetchone()
            if int(recent["count"]) >= MODEL_REQUEST_BURST_LIMIT:
                raise QuotaExceededError("Too many model requests. Try again in a few minutes.")
            row = connection.execute(
                "SELECT * FROM web_usage_daily WHERE user_id = ? AND usage_day = ?",
                (user_id, usage_day),
            ).fetchone()
            requests = int(row["model_requests"] if row else 0)
            input_tokens = int(row["input_tokens_reserved"] if row else 0)
            output_tokens = int(row["output_tokens_reserved"] if row else 0)
            if DAILY_MODEL_REQUEST_LIMIT is not None and requests >= DAILY_MODEL_REQUEST_LIMIT:
                raise QuotaExceededError("Daily model-request quota reached.")
            if (
                DAILY_INPUT_TOKEN_RESERVATION is not None
                and input_tokens + INPUT_TOKENS_RESERVED_PER_REQUEST
                > DAILY_INPUT_TOKEN_RESERVATION
            ):
                raise QuotaExceededError("Daily input-token quota reached.")
            if (
                DAILY_OUTPUT_TOKEN_RESERVATION is not None
                and output_tokens + OUTPUT_TOKENS_RESERVED_PER_REQUEST
                > DAILY_OUTPUT_TOKEN_RESERVATION
            ):
                raise QuotaExceededError("Daily output-token quota reached.")
            requests += 1
            input_tokens += INPUT_TOKENS_RESERVED_PER_REQUEST
            output_tokens += OUTPUT_TOKENS_RESERVED_PER_REQUEST
            connection.execute(
                """
                INSERT INTO web_usage_daily (
                    user_id, usage_day, model_requests,
                    input_tokens_reserved, output_tokens_reserved
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, usage_day) DO UPDATE SET
                    model_requests = excluded.model_requests,
                    input_tokens_reserved = excluded.input_tokens_reserved,
                    output_tokens_reserved = excluded.output_tokens_reserved
                """,
                (user_id, usage_day, requests, input_tokens, output_tokens),
            )
            self._audit(
                connection,
                user_id,
                "model_request_reserved",
                occurred_at=timestamp,
            )
        return quota_status(requests, input_tokens, output_tokens)

    def quota_status(self, user_id: str, *, now: int | None = None) -> QuotaStatus:
        timestamp = int(now if now is not None else time.time())
        usage_day = datetime.fromtimestamp(timestamp, tz=UTC).date().isoformat()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM web_usage_daily WHERE user_id = ? AND usage_day = ?",
                (user_id, usage_day),
            ).fetchone()
        return quota_status(
            int(row["model_requests"] if row else 0),
            int(row["input_tokens_reserved"] if row else 0),
            int(row["output_tokens_reserved"] if row else 0),
        )

    def _create_session(
        self,
        connection: sqlite3.Connection,
        user: AuthenticatedUser,
        *,
        now: int,
        user_agent: str,
        remember: bool = False,
    ) -> tuple[str, AuthSession]:
        token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        session_ttl = REMEMBER_SESSION_TTL_SECONDS if remember else SESSION_TTL_SECONDS
        idle_ttl = REMEMBER_SESSION_TTL_SECONDS if remember else SESSION_IDLE_SECONDS
        expires_at = now + session_ttl
        connection.execute(
            """
            INSERT INTO web_sessions (
                token_hash, user_id, csrf_hash, created_at, last_seen_at,
                expires_at, idle_expires_at, remembered, user_agent_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hash_token(token),
                user.user_id,
                hash_token(csrf_token),
                now,
                now,
                expires_at,
                now + idle_ttl,
                int(remember),
                hash_token(user_agent) if user_agent else None,
            ),
        )
        active = connection.execute(
            """
            SELECT token_hash FROM web_sessions
            WHERE user_id = ? AND revoked_at IS NULL
            ORDER BY created_at DESC
            """,
            (user.user_id,),
        ).fetchall()
        for old in active[MAX_ACTIVE_SESSIONS:]:
            connection.execute(
                "UPDATE web_sessions SET revoked_at = ? WHERE token_hash = ?",
                (now, old["token_hash"]),
            )
        return token, AuthSession(user=user, csrf_token=csrf_token, expires_at=expires_at)

    @staticmethod
    def _available_username(connection: sqlite3.Connection, base: str) -> str:
        candidate = base
        suffix = 1
        while connection.execute(
            "SELECT 1 FROM web_users WHERE username = ?", (candidate,)
        ).fetchone():
            suffix += 1
            suffix_text = f"-{suffix}"
            candidate = f"{base[: 32 - len(suffix_text)]}{suffix_text}"
        return candidate

    @staticmethod
    def _registration_event(
        connection: sqlite3.Connection,
        email_hash: bytes,
        source_hash: bytes,
        occurred_at: int,
        outcome: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO web_registration_events (
                event_id, email_hash, source_hash, occurred_at, outcome
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), email_hash, source_hash, occurred_at, outcome),
        )

    @staticmethod
    def _enforce_registration_rate_limit(
        connection: sqlite3.Connection,
        *,
        email_hash: bytes,
        source_hash: bytes,
        now: int,
    ) -> None:
        cutoff = now - 60 * 60
        email_count = connection.execute(
            "SELECT COUNT(*) AS count FROM web_registration_events "
            "WHERE email_hash = ? AND occurred_at > ?",
            (email_hash, cutoff),
        ).fetchone()
        source_count = connection.execute(
            "SELECT COUNT(*) AS count FROM web_registration_events "
            "WHERE source_hash = ? AND occurred_at > ?",
            (source_hash, cutoff),
        ).fetchone()
        if int(email_count["count"]) >= REGISTRATION_EMAIL_HOURLY_LIMIT:
            raise RegistrationError("Too many registration attempts. Try again later.")
        if int(source_count["count"]) >= REGISTRATION_SOURCE_HOURLY_LIMIT:
            raise RegistrationError("Too many registration attempts. Try again later.")

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        user_id: str | None,
        event_type: str,
        detail: str | None = None,
        occurred_at: int | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO web_audit_events (event_id, user_id, event_type, occurred_at, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                user_id,
                event_type,
                int(occurred_at if occurred_at is not None else time.time()),
                detail,
            ),
        )


def normalize_username(username: str) -> str:
    value = str(username or "").strip().lower()
    if not USERNAME_PATTERN.fullmatch(value):
        raise ValueError(
            "Account name must be 3-32 characters and use lowercase letters, numbers, dot, dash, or underscore."
        )
    return value


def normalize_bnitm_email(email: str) -> str:
    value = str(email or "").strip().lower()
    if len(value) > 254 or value.count("@") != 1:
        raise ValueError("Use a valid @bnitm.de email address.")
    local, domain = value.rsplit("@", 1)
    if domain != "bnitm.de" or not local or len(local) > 64:
        raise ValueError("Registration requires an @bnitm.de email address.")
    if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", local):
        raise ValueError("Use a valid @bnitm.de email address.")
    if local.startswith(".") or local.endswith(".") or ".." in local:
        raise ValueError("Use a valid @bnitm.de email address.")
    return value


def username_from_email(email: str) -> str:
    local = normalize_bnitm_email(email).split("@", 1)[0]
    value = re.sub(r"[^a-z0-9._-]+", "-", local).strip("._-")
    if not value or not value[0].isalpha():
        value = f"user-{value}" if value else "user"
    if len(value) < 3:
        value = f"{value}-user"
    return value[:32].rstrip("._-")


def normalize_display_name(display_name: str) -> str:
    value = " ".join(str(display_name or "").split())
    if not 1 <= len(value) <= 80 or any(ord(character) < 32 for character in value):
        raise ValueError("Display name must contain 1-80 printable characters.")
    return value


def normalize_role(role: str) -> str:
    value = str(role or "user").strip().lower()
    if value not in {"user", "reviewer", "admin"}:
        raise ValueError("Role must be user, reviewer, or admin.")
    return value


def validate_password(password: str, *, username: str = "", display_name: str = "") -> None:
    value = str(password or "")
    if len(value) < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyError(f"Password must contain at least {PASSWORD_MIN_LENGTH} characters.")
    if len(value) > PASSWORD_MAX_LENGTH:
        raise PasswordPolicyError(f"Password must contain at most {PASSWORD_MAX_LENGTH} characters.")
    normalized = re.sub(r"\s+", "", value).lower()
    if normalized in COMMON_PASSWORDS:
        raise PasswordPolicyError("Password is too common.")
    identity_terms = {
        re.sub(r"[^a-z0-9]", "", candidate.lower())
        for candidate in (username, display_name)
        if candidate
    }
    if any(term and len(term) >= 3 and term in normalized for term in identity_terms):
        raise PasswordPolicyError("Password must not contain the username or display name.")
    if not re.search(r"[A-Za-z]", value):
        raise PasswordPolicyError("Password must contain at least one letter.")
    if len(re.findall(r"[0-9]", value)) < 2:
        raise PasswordPolicyError("Password must contain at least two numbers.")
    if not re.search(r"[^A-Za-z0-9\s]", value):
        raise PasswordPolicyError("Password must contain at least one special character.")


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        str(password).encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM,
        dklen=64,
    )


def verify_password(password: str, salt: bytes, expected: bytes) -> bool:
    candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, expected)


def dummy_verify_password(password: str) -> None:
    salt = b"pfempire-dummy-auth-salt-000000"
    hash_password(str(password or ""), salt)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(str(token).encode("utf-8")).digest()


def row_to_user(row: sqlite3.Row) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=str(row["user_id"]),
        username=str(row["username"]),
        display_name=str(row["display_name"]),
        role=str(row["role"]),
    )


def format_timestamp(value: int | None) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(int(value), tz=UTC).isoformat()


def quota_status(requests: int, input_tokens: int, output_tokens: int) -> QuotaStatus:
    return QuotaStatus(
        model_requests_used=requests,
        model_requests_limit=DAILY_MODEL_REQUEST_LIMIT,
        input_tokens_reserved=input_tokens,
        input_tokens_limit=DAILY_INPUT_TOKEN_RESERVATION,
        output_tokens_reserved=output_tokens,
        output_tokens_limit=DAILY_OUTPUT_TOKEN_RESERVATION,
    )
