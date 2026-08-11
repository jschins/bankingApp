#!/usr/bin/env python3
"""Cross-platform PyInstaller build for boekhouding admin (onedir)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.runtime import app_root, project_root

PROJECT = project_root()
DEPLOY = app_root()
FRONTEND = PROJECT / "frontend"
FRONTEND_DIST = FRONTEND / "dist"
ENTRY = PROJECT / "entry.py"
NAME = "lokaleBoekhouding_onedir"


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
    DEPLOY.mkdir(parents=True, exist_ok=True)

    sep = os.pathsep
    add_data = f"{FRONTEND_DIST}{sep}frontend/dist"

    cmd = [
        "pyinstaller",
        "--onedir",
        "--clean",
        "--noconfirm",
        "--noupx",
        "--name",
        NAME,
        "--paths",
        str(PROJECT),
        "--distpath",
        str(DEPLOY),
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
        "--collect-submodules",
        "shared",
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

    out_dir = DEPLOY / NAME
    exe = out_dir / (NAME + (".exe" if sys.platform == "win32" else ""))
    if not exe.is_file():
        raise SystemExit(f"Build failed: {exe} not created")

    print(f"\nBuilt {exe}")
    print(
        "For onedir, either move person packs beside the .exe inside "
        f"{NAME}/, or prefer scripts/build_onefile.py which drops "
        "boekhouding.exe directly into boekhouding/."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
