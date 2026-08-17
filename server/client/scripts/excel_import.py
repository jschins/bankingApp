#!/usr/bin/env python3
"""PyInstaller onefile build → ``server/workspaces/excel_import.exe``.

Place the exe in a person folder (or run it with that folder as cwd). It reads
``data/*.xlsx`` and writes categorized_transactions.json + category_totals.json.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ENTRY = PROJECT / "excel_import_entry.py"
NAME = "excel_import"
DEPLOY = PROJECT.parent / "workspaces"


def main() -> int:
    if not ENTRY.is_file():
        raise SystemExit(f"Missing entry point: {ENTRY}")
    DEPLOY.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--clean",
        "--noconfirm",
        "--noupx",
        f"--name={NAME}",
        f"--distpath={DEPLOY}",
        f"--workpath={PROJECT / 'build' / 'excel_import'}",
        f"--specpath={PROJECT / 'build' / 'excel_import'}",
        f"--paths={PROJECT}",
        str(ENTRY),
    ]
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT, check=True)

    out = DEPLOY / (NAME + (".exe" if sys.platform == "win32" else ""))
    if not out.is_file():
        raise SystemExit(f"Build failed: {out} not created")
    print(f"Built: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
