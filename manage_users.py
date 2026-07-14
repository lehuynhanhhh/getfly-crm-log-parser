#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import (
    init_db,
    list_users,
    register_user,
    update_user_password,
    update_user_role,
    revoke_all_user_sessions,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Getfly CRM users")
    sub = parser.add_subparsers(dest="command")

    p_create = sub.add_parser("create-admin", help="Create an admin user")
    p_create.add_argument("email", help="Email address")
    p_create.add_argument("password", nargs="?", default=None, help="Password (prompts if omitted)")

    p_list = sub.add_parser("list", help="List all users")

    p_role = sub.add_parser("set-role", help="Change a user's role")
    p_role.add_argument("email")
    p_role.add_argument("role", choices=["admin", "user"])

    p_passwd = sub.add_parser("reset-password", help="Reset a user's password")
    p_passwd.add_argument("email")
    p_passwd.add_argument("password", nargs="?", default=None, help="New password (prompts if omitted)")

    p_lock = sub.add_parser("lock", help="Lock a user account")
    p_lock.add_argument("email")

    p_unlock = sub.add_parser("unlock", help="Unlock a user account")
    p_unlock.add_argument("email")

    args = parser.parse_args()

    if args.command == "create-admin":
        pw = args.password or _prompt_password()
        if register_user(args.email, pw, role="admin", display_name="Admin"):
            print(f"Admin user '{args.email}' created.")
        else:
            print(f"User '{args.email}' already exists.", file=sys.stderr)
            sys.exit(1)

    elif args.command == "list":
        init_db()
        users = list_users()
        if not users:
            print("No users found.")
            return
        for u in users:
            locked = " 🔒" if u.get("locked_until") else ""
            inactive = " ⛔" if not u.get("is_active") else ""
            print(f"  {u['email']:40s} {u['role']:8s}{locked}{inactive}")

    elif args.command == "set-role":
        if update_user_role(args.email, args.role):
            print(f"Role for '{args.email}' set to '{args.role}'.")
        else:
            print(f"Failed to update role for '{args.email}'.", file=sys.stderr)
            sys.exit(1)

    elif args.command == "reset-password":
        pw = args.password or _prompt_password()
        if update_user_password(args.email, pw):
            print(f"Password for '{args.email}' updated.")
            revoke_all_user_sessions(args.email)
            print("  Revoked all active sessions.")
        else:
            print(f"User '{args.email}' not found.", file=sys.stderr)
            sys.exit(1)

    elif args.command == "lock":
        from database import get_user_by_email
        from datetime import datetime, timedelta
        import bcrypt  # noqa: needed for import side effects

        user = get_user_by_email(args.email)
        if not user:
            print(f"User '{args.email}' not found.", file=sys.stderr)
            sys.exit(1)
        from database import _connect, DB_PATH, _execute

        lock_until = (datetime.utcnow() + timedelta(hours=24 * 365)).isoformat()
        with _connect(DB_PATH) as conn:
            _execute(conn, "UPDATE users SET locked_until = ? WHERE email = ?", (lock_until, args.email))
        revoke_all_user_sessions(args.email)
        print(f"User '{args.email}' locked.")

    elif args.command == "unlock":
        from database import _connect, DB_PATH, _execute

        init_db()
        with _connect(DB_PATH) as conn:
            _execute(
                conn,
                "UPDATE users SET locked_until = NULL, failed_login_count = 0 WHERE email = ?",
                (args.email,),
            )
        print(f"User '{args.email}' unlocked.")

    else:
        parser.print_help()
        sys.exit(1)


def _prompt_password() -> str:
    import getpass

    while True:
        pw = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm: ")
        if pw != confirm:
            print("Passwords do not match.", file=sys.stderr)
        elif len(pw) < 4:
            print("Password must be at least 4 characters.", file=sys.stderr)
        else:
            return pw


if __name__ == "__main__":
    main()
