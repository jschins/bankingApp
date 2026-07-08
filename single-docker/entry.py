"""PyInstaller entry point for the single-docker executable."""

from __future__ import annotations

import sys


def main() -> int:
    from app.main import run

    try:
        run()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
