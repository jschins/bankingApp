#!/usr/bin/env python3
"""PyInstaller onefile build → centraleBoekhouding.exe."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ENTRY = PROJECT / "entry.py"
NAME = "centraleBoekhouding"
DEPLOY = PROJECT / "boekhouding"


def main() -> int:
    DEPLOY.mkdir(parents=True, exist_ok=True)
    cmd = [
        "pyinstaller",
        "--onefile",
        "--clean",
        "--noconfirm",
        f"--name={NAME}",
        f"--distpath={DEPLOY}",
        f"--workpath={PROJECT / 'build'}",
        f"--specpath={PROJECT / 'build'}",
        str(ENTRY),
    ]
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT, check=True)
    out = DEPLOY / (NAME + (".exe" if sys.platform == "win32" else ""))
    print(f"Built: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
