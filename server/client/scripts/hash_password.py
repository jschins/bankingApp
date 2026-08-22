"""Print a scrypt password_hash for users.json.

Usage:
  uv run python scripts/hash_password.py mypassword
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import hash_password  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: hash_password.py <password>", file=sys.stderr)
        sys.exit(1)
    print(hash_password(sys.argv[1]))


if __name__ == "__main__":
    main()
