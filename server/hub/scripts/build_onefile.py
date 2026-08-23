#!/usr/bin/env python3
"""PyInstaller onefile build → ``server/workspaces/server.exe``.

Run the hub from ``workspaces/`` so ``data_root`` is the folder next to the exe
(dkg/, jl/, categories.json, …).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]  # server/hub
SERVER = PROJECT.parent
REPO = SERVER.parent
SHARED_ROOT = REPO / "shared"  # contains the ``shared`` package
ENTRY = PROJECT / "entry.py"
NAME = "server"
DEPLOY = SERVER / "workspaces"
STAGE_DIST = PROJECT / "build" / "dist"


def _target_exe() -> Path:
    return DEPLOY / (NAME + (".exe" if sys.platform == "win32" else ""))


def _running_server_pids() -> list[int]:
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq server.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    pids: list[int] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith('"INFO:'):
            continue
        # "server.exe","12345",...
        parts = line.split(",")
        if len(parts) >= 2 and parts[0].strip('"').lower() == "server.exe":
            try:
                pids.append(int(parts[1].strip('"')))
            except ValueError:
                pass
    return pids


def _preflight(target: Path) -> None:
    pids = _running_server_pids()
    if pids:
        pid_list = ", ".join(str(p) for p in pids)
        raise SystemExit(
            f"Cannot rebuild {target.name}: server.exe is still running (PID {pid_list}).\n"
            "Stop it first, then rebuild:\n"
            "  taskkill /IM server.exe /F\n"
            "Or close the hub console window."
        )
    if not target.is_file():
        return
    try:
        with target.open("r+b"):
            pass
    except PermissionError as exc:
        raise SystemExit(
            f"Cannot overwrite {target} — file is locked ({exc}).\n"
            "Close any running hub / antivirus scan on that file, then retry."
        ) from exc


def main() -> int:
    if not ENTRY.is_file():
        raise SystemExit(f"Missing entry point: {ENTRY}")
    if not (SHARED_ROOT / "shared").is_dir():
        raise SystemExit(f"Missing shared package: {SHARED_ROOT / 'shared'}")
    DEPLOY.mkdir(parents=True, exist_ok=True)
    STAGE_DIST.mkdir(parents=True, exist_ok=True)

    target = _target_exe()
    _preflight(target)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--clean",
        "--noconfirm",
        "--noupx",
        f"--name={NAME}",
        f"--distpath={STAGE_DIST}",
        f"--workpath={PROJECT / 'build'}",
        f"--specpath={PROJECT / 'build'}",
        f"--paths={PROJECT}",
        f"--paths={SHARED_ROOT}",
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
        "--hidden-import=shared.passwords",
        "--hidden-import=shared.user_access",
        str(ENTRY),
    ]
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT, check=True)

    staged = STAGE_DIST / target.name
    if not staged.is_file():
        raise SystemExit(f"Build failed: {staged} not created")

    try:
        shutil.copy2(staged, target)
    except PermissionError as exc:
        raise SystemExit(
            f"Build succeeded at {staged} but could not copy to {target} ({exc}).\n"
            "Stop server.exe, then run:\n"
            f'  copy /Y "{staged}" "{target}"'
        ) from exc

    print(f"Built: {target}")
    print("Run from server/workspaces/ (data_root = that folder).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
