#!/usr/bin/env python3
"""PyInstaller onefile build → ``server/workspaces/server.exe``.

Run the hub from ``workspaces/`` so ``data_root`` is the folder next to the exe
(dkg/, jl/, categories.json, …).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]  # server/hub
SERVER = PROJECT.parent
ENTRY = PROJECT / "entry.py"
NAME = "server"
DEPLOY = SERVER / "workspaces"


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
        f"--workpath={PROJECT / 'build'}",
        f"--specpath={PROJECT / 'build'}",
        f"--paths={PROJECT}",
        "--collect-submodules=uvicorn",
        "--collect-submodules=fastapi",
        "--collect-submodules=starlette",
        "--collect-submodules=pydantic",
        "--collect-submodules=shared",
        "--copy-metadata=fastapi",
        "--copy-metadata=uvicorn",
        "--copy-metadata=starlette",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols",
        "--hidden-import=uvicorn.protocols.http",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.lifespan.on",
        str(ENTRY),
    ]
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT, check=True)
    out = DEPLOY / (NAME + (".exe" if sys.platform == "win32" else ""))
    if not out.is_file():
        raise SystemExit(f"Build failed: {out} not created")
    print(f"Built: {out}")
    print("Run from server/workspaces/ (data_root = that folder).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
