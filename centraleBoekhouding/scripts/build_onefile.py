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
DIST = PROJECT / "dist"


def main() -> int:
    DIST.mkdir(parents=True, exist_ok=True)
    cmd = [
        "pyinstaller",
        "--onefile",
        "--clean",
        "--noconfirm",
        f"--name={NAME}",
        f"--distpath={DIST}",
        f"--workpath={PROJECT / 'build'}",
        f"--specpath={PROJECT / 'build'}",
        str(ENTRY),
    ]
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT, check=True)
    print(f"Built: {DIST / (NAME + ('.exe' if sys.platform == 'win32' else ''))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
