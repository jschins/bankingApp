"""Entry point for PyInstaller onefile build."""
from __future__ import annotations

import sys


def _pause_if_frozen() -> None:
    """Keep the console open after a failed double-click launch on Windows."""
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass


if __name__ == "__main__":
    try:
        from app.main import run

        run()
    except SystemExit as exc:
        if exc.code not in (0, None):
            _pause_if_frozen()
        raise
    except Exception:
        import traceback

        traceback.print_exc()
        _pause_if_frozen()
        raise
