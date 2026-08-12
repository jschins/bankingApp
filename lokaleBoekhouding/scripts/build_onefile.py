#!/usr/bin/env python3
"""Cross-platform PyInstaller build for lokaleBoekhouding (onefile).

Builds once, then copies ``lokaleBoekhouding.exe`` into both workspace
deploy folders ``dkg/`` and ``jl/`` (next to each peer's person packs).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from app.runtime import project_root

PROJECT = project_root()
FRONTEND = PROJECT / "frontend"
FRONTEND_DIST = FRONTEND / "dist"
ENTRY = PROJECT / "entry.py"
NAME = "lokaleBoekhouding"
# Peer deploy roots (exe + person packs + lokale_config.json).
DEPLOY_DIRS = (PROJECT / "dkg", PROJECT / "jl")
# PyInstaller dist intermediate (first deploy dir).
BUILD_DIST = DEPLOY_DIRS[0]


def _run(cmd: list[str], *, cwd: Path) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def _ensure_frontend(*, force: bool = False) -> None:
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    if force or not (FRONTEND_DIST / "index.html").is_file():
        print("Building frontend...")
        _run([npm, "install"], cwd=FRONTEND)
        _run([npm, "run", "build"], cwd=FRONTEND)
    if not (FRONTEND_DIST / "index.html").is_file():
        raise SystemExit(f"frontend build failed: {FRONTEND_DIST / 'index.html'} missing")


def main() -> int:
    # Rebuild UI when shipping so status-bar / sync changes are not stale.
    _ensure_frontend(force=True)
    for deploy in DEPLOY_DIRS:
        deploy.mkdir(parents=True, exist_ok=True)

    sep = os.pathsep
    add_data = f"{FRONTEND_DIST}{sep}frontend/dist"
    exe_name = NAME + (".exe" if sys.platform == "win32" else "")

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
        str(BUILD_DIST),
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

    built = BUILD_DIST / exe_name
    if not built.is_file():
        raise SystemExit(f"Build failed: {built} not created")

    for deploy in DEPLOY_DIRS[1:]:
        dest = deploy / exe_name
        shutil.copy2(built, dest)
        print(f"Copied {built} -> {dest}")

    print("\nBuilt:")
    for deploy in DEPLOY_DIRS:
        out = deploy / exe_name
        print(f"  {out}")
        if not out.is_file():
            raise SystemExit(f"Missing deploy output: {out}")

    print(
        "Person packs are sibling folders of each exe under dkg/ and jl/ "
        "(each must contain data/ and secret/). Configure centrale via "
        "lokale_config.json next to the exe (centrale_url, workspace)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
