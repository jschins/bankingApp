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
MODE_MARKER = FRONTEND_DIST / ".vite_app_mode"
ENTRY = PROJECT / "entry.py"
NAME = "lokaleBoekhouding"
# Peer deploy roots (exe + person packs + lokale_config.json).
DEPLOY_DIRS = (PROJECT / "dkg", PROJECT / "jl")
# PyInstaller dist intermediate (first deploy dir).
BUILD_DIST = DEPLOY_DIRS[0]
REQUIRED_MODE = "local"


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def _force_frontend() -> bool:
    if "--force-frontend" in sys.argv:
        return True
    return os.environ.get("FORCE_FRONTEND", "").strip().lower() in ("1", "true", "yes", "on")


def _dist_is_local() -> bool:
    if not (FRONTEND_DIST / "index.html").is_file():
        return False
    if not MODE_MARKER.is_file():
        # Plain ``npm run build`` without marker: treat as reusable local dist.
        return True
    try:
        return MODE_MARKER.read_text(encoding="utf-8").strip() == REQUIRED_MODE
    except OSError:
        return False


def _ensure_frontend() -> None:
    if not _force_frontend() and _dist_is_local():
        print(
            f"Reusing existing frontend dist at {FRONTEND_DIST}. "
            "Pass --force-frontend to rebuild."
        )
        return
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    print("Building frontend (local mode)...")
    env = {**os.environ, "VITE_APP_MODE": REQUIRED_MODE}
    # Clear central_admin mode from env if present.
    env["VITE_APP_MODE"] = REQUIRED_MODE
    _run([npm, "install"], cwd=FRONTEND, env=env)
    _run([npm, "run", "build"], cwd=FRONTEND, env=env)
    if not (FRONTEND_DIST / "index.html").is_file():
        raise SystemExit(f"frontend build failed: {FRONTEND_DIST / 'index.html'} missing")
    MODE_MARKER.write_text(REQUIRED_MODE + "\n", encoding="utf-8")


def main() -> int:
    _ensure_frontend()
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
