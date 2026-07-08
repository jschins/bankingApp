#!/usr/bin/env python3
"""Cross-platform PyInstaller build for bankingApp-single-docker."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.runtime import app_root, project_path

PROJECT = app_root()
FRONTEND = PROJECT / "frontend"
FRONTEND_DIST = FRONTEND / "dist"
ENTRY = PROJECT / "entry.py"
NAME = "bankingApp"


def _run(cmd: list[str], *, cwd: Path) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def _ensure_frontend() -> None:
    if (FRONTEND_DIST / "index.html").is_file():
        return
    print("Building frontend...")
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    _run([npm, "install"], cwd=FRONTEND)
    _run([npm, "run", "build"], cwd=FRONTEND)
    if not (FRONTEND_DIST / "index.html").is_file():
        raise SystemExit(f"frontend build failed: {FRONTEND_DIST / 'index.html'} missing")


def main() -> int:
    _ensure_frontend()

    sep = os.pathsep  # ';' on Windows, ':' on macOS/Linux
    add_data = f"{FRONTEND_DIST}{sep}frontend/dist"

    cmd = [
        "pyinstaller",
        "--onefile",
        "--clean",
        "--noconfirm",
        "--noupx",
        "--name",
        NAME,
        "--paths",
        str(PROJECT),
        "--distpath",
        str(PROJECT / "dist"),
        "--workpath",
        str(PROJECT / "build"),
        "--specpath",
        str(PROJECT),
        "--add-data",
        add_data,
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "fastapi",
        "--collect-submodules",
        "starlette",
        "--collect-submodules",
        "pydantic",
        "--copy-metadata",
        "fastapi",
        "--copy-metadata",
        "uvicorn",
        "--copy-metadata",
        "starlette",
        "--hidden-import",
        "uvicorn.logging",
        "--hidden-import",
        "uvicorn.loops",
        "--hidden-import",
        "uvicorn.loops.auto",
        "--hidden-import",
        "uvicorn.protocols",
        "--hidden-import",
        "uvicorn.protocols.http",
        "--hidden-import",
        "uvicorn.protocols.http.auto",
        "--hidden-import",
        "uvicorn.lifespan.on",
        str(ENTRY),
    ]

    _run(cmd, cwd=PROJECT)

    out = PROJECT / "dist" / (NAME + (".exe" if sys.platform == "win32" else ""))
    if not out.is_file():
        raise SystemExit(f"Build failed: {out} not created")

    print(f"\nBuilt {out}")
    print("Deploy: place data/ and secret/ beside the executable (see README).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
