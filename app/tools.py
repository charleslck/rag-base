"""Operational CLI for running instances.

Usage:
    python -m app.tools reset-password <email> <new_password>
    python -m app.tools create-user <email> <password> [--admin]
    python -m app.tools list-users

Works directly against the SQLite database — no server or admin
session required. Useful when the admin password itself is lost.
"""
import argparse
import sys

from . import auth, database as db


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.tools")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_reset = sub.add_parser("reset-password", help="Reset a user's password")
    p_reset.add_argument("email")
    p_reset.add_argument("new_password")

    p_create = sub.add_parser("create-user", help="Create a user account")
    p_create.add_argument("email")
    p_create.add_argument("password")
    p_create.add_argument("--admin", action="store_true")

    sub.add_parser("list-users", help="List all users")

    args = parser.parse_args()
    db.init_db()

    if args.cmd == "reset-password":
        user = db.get_user_by_email(args.email.strip().lower())
        if not user:
            print(f"User not found: {args.email}", file=sys.stderr)
            return 1
        db.update_password(user["id"], auth.hash_password(args.new_password))
        print(f"Password reset for {user['email']}")
        return 0

    if args.cmd == "create-user":
        email = args.email.strip().lower()
        if db.get_user_by_email(email):
            print(f"User already exists: {email}", file=sys.stderr)
            return 1
        db.create_user(email, auth.hash_password(args.password), is_admin=args.admin)
        print(f"Created {'admin' if args.admin else 'user'} {email}")
        return 0

    if args.cmd == "list-users":
        for u in db.list_users():
            print(f"{u['email']:<40} {'admin' if u['is_admin'] else 'user'}  id={u['id'][:8]}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
