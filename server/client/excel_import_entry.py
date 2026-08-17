"""PyInstaller entry point for excel_import.exe (Rafael + Miguel layouts)."""

from __future__ import annotations

import sys


def main() -> int:
    from app.excel_import import main as run

    try:
        return run()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
