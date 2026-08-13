#!/usr/bin/env python3
"""PyInstaller onefile build for the identical boekhouding client.

Bundles ``frontend/dist`` into ``boekhouding-client.exe`` (or ``boekhouding-client``
on non-Windows). Output lands in ``client/dist/`` next to a default
``client_config.json`` if one is not already there.

Reuse an existing frontend build unless ``--force-frontend`` / ``FORCE_FRONTEND=1``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
FRONTEND = PROJECT / "frontend"
FRONTEND_DIST = FRONTEND / "dist"
ENTRY = PROJECT / "entry.py"
NAME = "boekhouding-client"
DEPLOY = PROJECT / "dist"


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def _force_frontend() -> bool:
    if "--force-frontend" in sys.argv:
        return True
    return os.environ.get("FORCE_FRONTEND", "").strip().lower() in ("1", "true", "yes", "on")


def _ensure_frontend() -> None:
    if not _force_frontend() and (FRONTEND_DIST / "index.html").is_file():
        print(
            f"Reusing existing frontend dist at {FRONTEND_DIST}. "
            "Pass --force-frontend to rebuild."
        )
        return
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    print("Building frontend...")
    _run([npm, "install"], cwd=FRONTEND)
    _run([npm, "run", "build"], cwd=FRONTEND)
    if not (FRONTEND_DIST / "index.html").is_file():
        raise SystemExit(f"frontend build failed: {FRONTEND_DIST / 'index.html'} missing")


def _ensure_deploy_config() -> None:
    """Copy default client_config.json beside the exe when missing."""
    dest = DEPLOY / "client_config.json"
    if dest.is_file():
        return
    src = PROJECT / "client_config.json"
    if src.is_file():
        shutil.copy2(src, dest)
        print(f"Copied {src.name} -> {dest}")
        return
    dest.write_text(
        "{\n"
        '  "server_url": "http://127.0.0.1:8400",\n'
        '  "port": 8300,\n'
        '  "workspaces": ["dkg", "jl"],\n'
        '  "author": "dkg",\n'
        '  "api_key": "",\n'
        '  "enabled": true\n'
        "}\n",
        encoding="utf-8",
    )
    print(f"Wrote {dest}")


def main() -> int:
    if not ENTRY.is_file():
        raise SystemExit(f"Missing entry point: {ENTRY}")

    _ensure_frontend()
    DEPLOY.mkdir(parents=True, exist_ok=True)

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
        str(DEPLOY),
        "--workpath",
        str(PROJECT / "build"),
        "--specpath",
        str(PROJECT / "build"),
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

    built = DEPLOY / exe_name
    if not built.is_file():
        raise SystemExit(f"Build failed: {built} not created")

    _ensure_deploy_config()
    print(f"\nBuilt: {built}")
    print("Place client_config.json next to the exe (server_url, port, workspaces).")
    print("Hub must be running (default http://127.0.0.1:8400).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
