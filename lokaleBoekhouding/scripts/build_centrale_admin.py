#!/usr/bin/env python3
"""Build centraleAdmin.exe (lokaleBoekhouding in central_admin role).

Output: ``centraleBoekhouding/boekhouding/centraleAdmin.exe`` next to the hub
data root and ``lokale_config.json`` (role=central_admin, port 8300).
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
NAME = "centraleAdmin"
HUB_DEPLOY = PROJECT.parent / "centraleBoekhouding" / "boekhouding"
BUILD_DIST = HUB_DEPLOY


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
    HUB_DEPLOY.mkdir(parents=True, exist_ok=True)

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
        str(PROJECT / "build_centrale_admin"),
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

    cfg = HUB_DEPLOY / "lokale_config.json"
    if not cfg.is_file():
        cfg.write_text(
            '{\n'
            '  "role": "central_admin",\n'
            '  "enabled": true,\n'
            '  "centrale_url": "http://127.0.0.1:8400",\n'
            '  "workspace": "dkg",\n'
            '  "port": 8300,\n'
            '  "api_key": ""\n'
            "}\n",
            encoding="utf-8",
        )
        print(f"Wrote {cfg}")

    print(f"\nBuilt: {built}")
    print("Run hub centraleBoekhouding.exe (:8400) first, then centraleAdmin.exe (:8300).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
