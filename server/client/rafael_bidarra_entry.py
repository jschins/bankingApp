"""PyInstaller entry point for rafael_bidarra.exe."""

from __future__ import annotations

import sys


def main() -> int:
    from app.rafael_bidarra import write_outputs

    try:
        categorized_path, totals_path = write_outputs()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {categorized_path}")
    print(f"Wrote {totals_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
