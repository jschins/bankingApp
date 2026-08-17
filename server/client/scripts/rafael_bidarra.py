#!/usr/bin/env python3
"""PyInstaller onefile build → ``server/workspaces/dkg/rafael_bidarra/rafael_bidarra.exe``.

Reads ``data/*.xlsx`` next to the exe and writes:
  - data/categorized_transactions.json
  - data/category_totals.json
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ENTRY = PROJECT / "rafael_bidarra_entry.py"
NAME = "rafael_bidarra"
DEPLOY = PROJECT.parent / "workspaces" / "dkg" / "rafael_bidarra"


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
        f"--workpath={PROJECT / 'build' / 'rafael_bidarra'}",
        f"--specpath={PROJECT / 'build' / 'rafael_bidarra'}",
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
