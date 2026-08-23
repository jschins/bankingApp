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
SERVER = PROJECT.parent
REPO = SERVER.parent
SHARED_ROOT = REPO / "shared"
FRONTEND = PROJECT / "frontend"
FRONTEND_DIST = FRONTEND / "dist"
ENTRY = PROJECT / "entry.py"
NAME = "boekhouding-client"
DEPLOY = PROJECT / "dist"
STAGE_DIST = PROJECT / "build" / "dist"


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
        '  "server_url": "http://127.0.0.1:8200",\n'
        '  "port": 8300,\n'
        '  "access": "local",\n'
        '  "workspace": "dkg",\n'
        '  "person": "",\n'
        '  "api_key": "",\n'
        '  "enabled": true,\n'
        '  "auth_enabled": true\n'
        "}\n",
        encoding="utf-8",
    )
    print(f"Wrote {dest}")


def _target_exe() -> Path:
    return DEPLOY / (NAME + (".exe" if sys.platform == "win32" else ""))


def _running_client_pids() -> list[int]:
    if sys.platform != "win32":
        return []
    names = {f"{NAME}.exe", "boekh-client.exe"}
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
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
        parts = line.split(",")
        if len(parts) < 2:
            continue
        image = parts[0].strip('"').lower()
        if image in names:
            try:
                pids.append(int(parts[1].strip('"')))
            except ValueError:
                pass
    return pids


def _preflight(target: Path) -> None:
    pids = _running_client_pids()
    if pids:
        pid_list = ", ".join(str(p) for p in pids)
        raise SystemExit(
            f"Cannot rebuild {target.name}: client is still running (PID {pid_list}).\n"
            f"Stop it first: taskkill /IM {target.name} /F"
        )
    if not target.is_file():
        return
    try:
        with target.open("r+b"):
            pass
    except PermissionError as exc:
        raise SystemExit(
            f"Cannot overwrite {target} — file is locked ({exc}).\n"
            "Close the running client, then retry."
        ) from exc


def main() -> int:
    if not ENTRY.is_file():
        raise SystemExit(f"Missing entry point: {ENTRY}")
    if not (SHARED_ROOT / "shared").is_dir():
        raise SystemExit(f"Missing shared package: {SHARED_ROOT / 'shared'}")

    _ensure_frontend()
    DEPLOY.mkdir(parents=True, exist_ok=True)
    STAGE_DIST.mkdir(parents=True, exist_ok=True)

    target = _target_exe()
    _preflight(target)

    sep = os.pathsep
    add_data = f"{FRONTEND_DIST}{sep}frontend/dist"
    py = sys.executable

    cmd = [
        py,
        "-m",
        "PyInstaller",
        "--onefile",
        "--clean",
        "--noconfirm",
        "--noupx",
        "--name",
        NAME,
        "--paths",
        str(PROJECT),
        "--paths",
        str(SHARED_ROOT),
        "--distpath",
        str(STAGE_DIST),
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
        "--hidden-import",
        "shared.passwords",
        "--hidden-import",
        "shared.user_access",
        str(ENTRY),
    ]

    _run(cmd, cwd=PROJECT)

    staged = STAGE_DIST / target.name
    if not staged.is_file():
        raise SystemExit(f"Build failed: {staged} not created")

    try:
        shutil.copy2(staged, target)
    except PermissionError as exc:
        raise SystemExit(
            f"Build succeeded at {staged} but could not copy to {target} ({exc}).\n"
            "Stop the running client, then copy manually."
        ) from exc

    _ensure_deploy_config()
    print(f"\nBuilt: {target}")
    print("Place client_config.json next to the exe (server_url, port, workspace, access).")
    print("Hub must be running (default http://127.0.0.1:8200).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
