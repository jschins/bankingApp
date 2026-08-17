#!/usr/bin/env python3
"""PyInstaller onefile build → ``server/workspaces/dkg/miguelangel_palacios/miguelangel_palacios.exe``.

Reads ``data/*.xlsx`` next to the exe and writes:
  - data/categorized_transactions.json
  - data/category_totals.json
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ENTRY = PROJECT / "miguelangel_palacios_entry.py"
NAME = "miguelangel_palacios"
DEPLOY = PROJECT.parent / "workspaces" / "dkg" / "miguelangel_palacios"


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
        f"--workpath={PROJECT / 'build' / 'miguelangel_palacios'}",
        f"--specpath={PROJECT / 'build' / 'miguelangel_palacios'}",
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
