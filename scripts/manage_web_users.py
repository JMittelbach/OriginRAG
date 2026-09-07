from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.web_auth import AuthStore, format_timestamp  # noqa: E402


DEFAULT_AUTH_DB = Path(
    os.environ.get(
        "ORIGINRAG_AUTH_DB",
        os.environ.get(
            "PFEMPIRE_AUTH_DB",
            str(REPO_ROOT / "data/local_auth/pfempire_web_auth.sqlite"),
        ),
    )
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage local OriginRAG pilot accounts.")
    parser.add_argument("--db", default=str(DEFAULT_AUTH_DB))
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Create an administrator-managed local account.")
    create.add_argument("username")
    create.add_argument("--display-name")
    create.add_argument("--role", choices=["user", "reviewer", "admin"], default="user")

    reset = commands.add_parser("reset-password", help="Replace a password and revoke sessions.")
    reset.add_argument("username")

    disable = commands.add_parser("disable", help="Disable an account and revoke sessions.")
    disable.add_argument("username")

    enable = commands.add_parser("enable", help="Re-enable an account.")
    enable.add_argument("username")

    commands.add_parser("list", help="List accounts without password or session material.")
    return parser.parse_args()


def read_new_password() -> str:
    password = getpass.getpass("New password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("Passwords do not match.")
    return password


def main() -> int:
    args = parse_args()
    store = AuthStore(args.db)
    if args.command == "create":
        user = store.create_user(
            args.username,
            read_new_password(),
            display_name=args.display_name,
            role=args.role,
        )
        print(f"Created {user.username} ({user.role}).")
        return 0
    if args.command == "reset-password":
        store.reset_password(args.username, read_new_password())
        print(f"Password reset and active sessions revoked for {args.username}.")
        return 0
    if args.command == "disable":
        store.set_disabled(args.username, disabled=True)
        print(f"Disabled {args.username} and revoked active sessions.")
        return 0
    if args.command == "enable":
        store.set_disabled(args.username, disabled=False)
        print(f"Enabled {args.username}.")
        return 0
    for user in store.list_users():
        status = "disabled" if user["disabled_at"] else "active"
        locked = format_timestamp(user["locked_until"])
        suffix = f" locked_until={locked}" if locked else ""
        print(f"{user['username']}\t{user['role']}\t{status}{suffix}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
