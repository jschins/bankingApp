"""Admin helpers for the hub SQLite user store."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HUB_ROOT))
sys.path.insert(0, str(HUB_ROOT.parent.parent / "shared"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Hub user store admin")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list users in users.db")

    args = parser.parse_args()
    from app import user_store

    if args.cmd == "list":
        user_store.init_user_store()
        print(user_store.users_db_path())
        for user in user_store.list_users():
            ws = user.get("workspace") or ""
            person = user.get("person") or ""
            access = user.get("access") or ""
            allow = user.get("workspaces") or []
            fmt = user.get("format") or ""
            extra = f" access={access}"
            if allow:
                extra += f" workspaces={','.join(allow)}"
            elif ws:
                extra += f" workspace={ws!r}"
            if person:
                extra += f" person={person}"
            if fmt:
                extra += f" format={fmt}"
            print(f"  {user['username']}{extra}")
        return


if __name__ == "__main__":
    main()
