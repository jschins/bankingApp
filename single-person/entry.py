"""PyInstaller entry point for the single-person executable."""

from __future__ import annotations

import sys

from single_app import main


if __name__ == "__main__":
    sys.exit(main())
