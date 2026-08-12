"""Entry point for centraleAdmin.exe — always central_admin role."""
from __future__ import annotations

import os
import sys

# Ensure role even if lokale_config.json is missing or copied from a peer.
os.environ.setdefault("LOKALE_ROLE", "central_admin")


def main() -> None:
    from app.main import run

    run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        raise
