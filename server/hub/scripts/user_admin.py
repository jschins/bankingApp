"""Admin helpers for the hub SQLite user store."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HUB_ROOT))
sys.path.insert(0, str(HUB_ROOT.parent.parent / "shared"))

from shared.passwords import hash_password  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Hub user store admin")
    sub = parser.add_subparsers(dest="cmd", required=True)

    hash_cmd = sub.add_parser("hash", help="print scrypt password_hash")
    hash_cmd.add_argument("password")

    list_cmd = sub.add_parser("list", help="list users in users.db")

    args = parser.parse_args()
    from app import user_store

    if args.cmd == "hash":
        print(hash_password(args.password))
        return

    if args.cmd == "list":
        user_store.init_user_store()
        print(user_store.users_db_path())
        for user in user_store.list_users():
            ws = user.get("workspace") or ""
            person = user.get("person") or ""
            access = user.get("access") or ""
            allow = user.get("workspaces") or []
            extra = f" access={access}"
            if allow:
                extra += f" workspaces={','.join(allow)}"
            elif ws:
                extra += f" workspace={ws!r}"
            if person:
                extra += f" person={person}"
            print(f"  {user['username']}{extra}")
        return


if __name__ == "__main__":
    main()
