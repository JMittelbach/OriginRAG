from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path


NETWORK_FILESYSTEMS = {
    "9p",
    "afs",
    "ceph",
    "cifs",
    "fuse.ceph",
    "fuse.glusterfs",
    "fuse.sshfs",
    "gfs",
    "gfs2",
    "glusterfs",
    "gpfs",
    "lustre",
    "nfs",
    "nfs4",
    "smb2",
}
EPHEMERAL_ROOTS = tuple(
    Path(path) for path in ("/dev/shm", "/run", "/tmp", "/var/tmp")
)
STATE_TABLES = {
    "audit_events": "web_audit_events",
    "pending_registrations": "web_pending_registrations",
    "registration_events": "web_registration_events",
    "sessions": "web_sessions",
    "usage_daily": "web_usage_daily",
    "users": "web_users",
}
REQUIRED_TABLES = set(STATE_TABLES.values())
MIGRATION_METADATA_TABLE = "pfempire_auth_storage_metadata"
MIGRATION_MARKER_KEY = "migration_marker"
MIGRATION_MARKER_VERSION = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check or migrate the authentication database."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser(
        "check",
        help="Require a persistent local filesystem and guard legacy account state.",
    )
    check.add_argument("--db", type=Path, required=True)
    check.add_argument("--legacy-db", type=Path)
    check.add_argument("--create-parent", action="store_true")

    migrate = commands.add_parser(
        "migrate",
        help="Atomically copy a stopped SQLite auth store to local storage.",
    )
    migrate.add_argument("--source", type=Path, required=True)
    migrate.add_argument("--destination", type=Path, required=True)
    return parser.parse_args()


def unescape_mount_field(value: str) -> str:
    for encoded, decoded in (
        (r"\040", " "),
        (r"\011", "\t"),
        (r"\012", "\n"),
        (r"\134", "\\"),
    ):
        value = value.replace(encoded, decoded)
    return value


def linux_mounts(path: Path = Path("/proc/self/mountinfo")) -> list[tuple[Path, str]]:
    if not path.is_file():
        return []
    mounts: list[tuple[Path, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
            mount_point = Path(unescape_mount_field(fields[4])).resolve()
            filesystem = fields[separator + 1].lower()
        except (IndexError, ValueError):
            continue
        mounts.append((mount_point, filesystem))
    return mounts


def nearest_existing_path(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    if not candidate.exists():
        raise ValueError(f"No existing ancestor for authentication path: {path}")
    return candidate.resolve()


def filesystem_type(path: Path) -> str | None:
    existing = nearest_existing_path(path)
    matches = [
        (mount_point, fs_type)
        for mount_point, fs_type in linux_mounts()
        if existing == mount_point or mount_point in existing.parents
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item[0].parts))[1]


def is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def check_auth_storage(
    db_path: Path,
    *,
    legacy_db: Path | None = None,
    create_parent: bool = False,
) -> dict[str, object]:
    expanded = db_path.expanduser()
    if not expanded.is_absolute():
        raise ValueError("PFEMPIRE_AUTH_DB must be an absolute path.")
    resolved = expanded.resolve(strict=False)
    if any(is_within(resolved, root) for root in EPHEMERAL_ROOTS):
        raise ValueError(
            f"Authentication database must survive reboot; refusing ephemeral path: {resolved}"
        )
    fs_type = filesystem_type(resolved.parent)
    if fs_type is None:
        raise ValueError(
            f"Could not determine the filesystem for authentication path: {resolved}"
        )
    if fs_type in NETWORK_FILESYSTEMS or fs_type.startswith("fuse."):
        raise ValueError(
            "Authentication database must use persistent node-local storage; "
            f"refusing filesystem {fs_type!r}: {resolved}"
        )

    legacy = legacy_db.expanduser().resolve(strict=False) if legacy_db else None
    if legacy is not None and legacy != resolved and legacy.is_file():
        if not resolved.exists():
            raise ValueError(
                "Existing account state was found at the legacy path, but the configured local "
                f"database does not exist: {legacy}. Stop the service and run "
                "'./originrag auth-storage migrate --source LEGACY --destination LOCAL' before "
                "setup or startup. The migration never removes the legacy database."
            )
        verify_migration_marker(legacy, resolved)

    if resolved.exists() and not resolved.is_file():
        raise ValueError(f"Authentication database is not a regular file: {resolved}")
    if create_parent:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        try:
            resolved.parent.chmod(0o700)
        except OSError:
            pass
    return {
        "database": str(resolved),
        "filesystem": fs_type,
        "exists": resolved.is_file(),
    }


def database_summary(connection: sqlite3.Connection) -> dict[str, int]:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or str(integrity[0]).lower() != "ok":
        detail = integrity[0] if integrity else "no result"
        raise ValueError(f"SQLite integrity_check failed: {detail}")
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        raise ValueError(f"Source is not a OriginRAG auth database; missing tables: {missing}")
    return {
        label: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for label, table in STATE_TABLES.items()
    }


def database_state_digest(connection: sqlite3.Connection) -> str:
    """Hash all authentication state independently of SQLite journal metadata."""
    digest = hashlib.sha256()

    def add(payload: bytes) -> None:
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)

    for table in sorted(REQUIRED_TABLES):
        add(table.encode("utf-8"))
        columns = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        add(json.dumps([tuple(row) for row in columns]).encode("utf-8"))
        rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
        for row in rows:
            add(b"row")
            for value in row:
                if isinstance(value, bytes):
                    encoded = {"bytes": value.hex()}
                else:
                    encoded = {"value": value}
                add(
                    json.dumps(encoded, sort_keys=True, separators=(",", ":")).encode("utf-8")
                )
    return digest.hexdigest()


def read_migration_marker(connection: sqlite3.Connection) -> dict[str, object]:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if MIGRATION_METADATA_TABLE not in tables:
        raise ValueError(
            "Both legacy and local authentication databases exist, but the local database "
            "has no verified migration marker. Refusing ambiguous account state."
        )
    row = connection.execute(
        f'SELECT value FROM "{MIGRATION_METADATA_TABLE}" WHERE key = ?',
        (MIGRATION_MARKER_KEY,),
    ).fetchone()
    if row is None:
        raise ValueError("The local authentication database has no migration marker value.")
    try:
        marker = json.loads(str(row[0]))
    except json.JSONDecodeError as exc:
        raise ValueError("The authentication migration marker is invalid JSON.") from exc
    if not isinstance(marker, dict):
        raise ValueError("The authentication migration marker must be a JSON object.")
    return marker


def verify_migration_marker(source: Path, destination: Path) -> dict[str, object]:
    source = source.resolve(strict=False)
    destination = destination.resolve(strict=True)
    with closing(
        sqlite3.connect(f"{destination.as_uri()}?mode=ro", uri=True, timeout=60)
    ) as connection:
        destination_summary = database_summary(connection)
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        marker = read_migration_marker(connection)
    expected = {
        "version": MIGRATION_MARKER_VERSION,
        "source": str(source),
        "destination": str(destination),
        "journal_mode": "delete",
    }
    for key, value in expected.items():
        if marker.get(key) != value:
            raise ValueError(
                f"Authentication migration marker mismatch for {key!r}; "
                "refusing ambiguous account state."
            )
    if journal_mode.lower() != "delete":
        raise ValueError("Local authentication database is not using DELETE journal mode.")
    source_summary = marker.get("source_summary")
    destination_initial_summary = marker.get("destination_summary")
    if not isinstance(source_summary, dict) or set(source_summary) != set(STATE_TABLES):
        raise ValueError("Authentication migration marker has an invalid source summary.")
    if any(not isinstance(value, int) or value < 0 for value in source_summary.values()):
        raise ValueError("Authentication migration marker contains invalid table counts.")
    if destination_initial_summary != source_summary:
        raise ValueError("Authentication migration marker records unequal source and copy counts.")
    source_digest = marker.get("source_state_sha256")
    destination_digest = marker.get("destination_state_sha256")
    if (
        not isinstance(source_digest, str)
        or len(source_digest) != 64
        or any(character not in "0123456789abcdef" for character in source_digest)
    ):
        raise ValueError("Authentication migration marker has an invalid source digest.")
    if destination_digest != source_digest:
        raise ValueError("Authentication migration marker records unequal source and copy state.")
    return {
        "marker": marker,
        "destination_summary": destination_summary,
    }


def migrate_auth_storage(source: Path, destination: Path) -> dict[str, object]:
    source = source.expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError(f"Source authentication database is not a file: {source}")
    destination_check = check_auth_storage(destination, create_parent=True)
    destination = Path(str(destination_check["database"]))
    if source == destination:
        raise ValueError("Source and destination authentication databases are identical.")
    if destination.exists():
        raise FileExistsError(
            f"Destination already exists; refusing to overwrite account state: {destination}"
        )

    temp_path: Path | None = None
    try:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".migrating",
            dir=destination.parent,
        )
        os.close(descriptor)
        temp_path = Path(temp_name)
        temp_path.chmod(0o600)
        with closing(
            sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=60)
        ) as src:
            src.execute("BEGIN")
            source_summary = database_summary(src)
            source_digest = database_state_digest(src)
            with closing(sqlite3.connect(temp_path, timeout=60)) as dst:
                src.backup(dst)
                journal_mode = dst.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
                if str(journal_mode).lower() != "delete":
                    raise ValueError(
                        "Copied authentication database did not enter DELETE journal mode."
                    )
                destination_summary = database_summary(dst)
                destination_digest = database_state_digest(dst)
                marker = {
                    "version": MIGRATION_MARKER_VERSION,
                    "source": str(source),
                    "destination": str(destination),
                    "source_summary": source_summary,
                    "destination_summary": destination_summary,
                    "source_state_sha256": source_digest,
                    "destination_state_sha256": destination_digest,
                    "journal_mode": "delete",
                }
                dst.execute(
                    f"CREATE TABLE \"{MIGRATION_METADATA_TABLE}\" "
                    "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                dst.execute(
                    f'INSERT INTO "{MIGRATION_METADATA_TABLE}" (key, value) VALUES (?, ?)',
                    (
                        MIGRATION_MARKER_KEY,
                        json.dumps(marker, sort_keys=True, separators=(",", ":")),
                    ),
                )
                dst.commit()
            src.rollback()
        if source_summary != destination_summary:
            raise ValueError(
                "Authentication migration verification failed: table counts changed during copy."
            )
        if source_digest != destination_digest:
            raise ValueError(
                "Authentication migration verification failed: copied state digest changed."
            )
        with closing(sqlite3.connect(temp_path, timeout=60)) as verified:
            persisted_mode = verified.execute("PRAGMA journal_mode").fetchone()[0]
            if str(persisted_mode).lower() != "delete":
                raise ValueError(
                    "Authentication migration verification failed: DELETE journal mode "
                    "did not persist after reopening the copy."
                )
            if database_summary(verified) != destination_summary:
                raise ValueError(
                    "Authentication migration verification failed after reopening the copy."
                )
            persisted_marker = read_migration_marker(verified)
            if persisted_marker != marker:
                raise ValueError(
                    "Authentication migration marker changed after reopening the copy."
                )
        with temp_path.open("rb") as handle:
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, destination)
        except FileExistsError as exc:
            raise FileExistsError(
                f"Destination appeared during migration; refusing to overwrite it: {destination}"
            ) from exc
        temp_path.unlink()
        temp_path = None
        destination.chmod(0o600)
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    return {
        "source": str(source),
        "destination": str(destination),
        "source_preserved": True,
        "filesystem": destination_check["filesystem"],
        "journal_mode": "delete",
        "state_sha256": destination_digest,
        **source_summary,
    }


def main() -> int:
    args = parse_args()
    if args.command == "check":
        result = check_auth_storage(
            args.db,
            legacy_db=args.legacy_db,
            create_parent=args.create_parent,
        )
    else:
        result = migrate_auth_storage(args.source, args.destination)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileExistsError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
