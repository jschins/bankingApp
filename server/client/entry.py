"""PyInstaller entry point for boekhouding-client.exe."""

from __future__ import annotations

import sys


def _pause_if_frozen() -> None:
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass


def main() -> int:
    from app.main import run

    try:
        run()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        _pause_if_frozen()
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit as exc:
        if exc.code not in (0, None):
            _pause_if_frozen()
        raise
    except Exception:
        import traceback

        traceback.print_exc()
        _pause_if_frozen()
        raise
