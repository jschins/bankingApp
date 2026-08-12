#!/usr/bin/env python3
"""Build centraleAdmin.exe into ``boekhouding/`` (next to the hub exe).

The hub itself has no React UI. ``centraleAdmin`` reuses the
``lokaleBoekhouding`` app + frontend with ``role=central_admin``
(``entry_centrale_admin.py``).

Always rebuilds the lokale frontend so the workspace switcher / no-Refresh
UI is not shipped stale.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CENTRALE = Path(__file__).resolve().parents[1]
LOKALE = CENTRALE.parent / "lokaleBoekhouding"
FRONTEND = LOKALE / "frontend"
FRONTEND_DIST = FRONTEND / "dist"
ENTRY = LOKALE / "entry_centrale_admin.py"
NAME = "centraleAdmin"
DEPLOY = CENTRALE / "boekhouding"


def _run(cmd: list[str], *, cwd: Path) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def _ensure_lokale_frontend() -> None:
    if not LOKALE.is_dir():
        raise SystemExit(f"Missing lokaleBoekhouding sources at {LOKALE}")
    if not ENTRY.is_file():
        raise SystemExit(f"Missing entry point: {ENTRY}")
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    print(f"Building frontend from {FRONTEND} (VITE_APP_MODE=central_admin) ...")
    env = {**os.environ, "VITE_APP_MODE": "central_admin"}
    _run([npm, "install"], cwd=FRONTEND)
    print("+", " ".join([npm, "run", "build"]), f"(cwd={FRONTEND}, VITE_APP_MODE=central_admin)")
    subprocess.run([npm, "run", "build"], cwd=FRONTEND, env=env, check=True)
    if not (FRONTEND_DIST / "index.html").is_file():
        raise SystemExit(f"frontend build failed: {FRONTEND_DIST / 'index.html'} missing")


def main() -> int:
    _ensure_lokale_frontend()
    DEPLOY.mkdir(parents=True, exist_ok=True)

    sep = os.pathsep
    add_data = f"{FRONTEND_DIST}{sep}frontend/dist"
    exe_name = NAME + (".exe" if sys.platform == "win32" else "")

    # Prefer hub venv pyinstaller if present; else PATH.
    pyinstaller = "pyinstaller"
    cmd = [
        pyinstaller,
        "--onefile",
        "--clean",
        "--noconfirm",
        "--noupx",
        "--name",
        NAME,
        "--paths",
        str(LOKALE),
        "--distpath",
        str(DEPLOY),
        "--workpath",
        str(CENTRALE / "build_centrale_admin"),
        "--specpath",
        str(CENTRALE / "build_centrale_admin"),
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

    _run(cmd, cwd=LOKALE)

    built = DEPLOY / exe_name
    if not built.is_file():
        raise SystemExit(f"Build failed: {built} not created")

    cfg = DEPLOY / "lokale_config.json"
    cfg.write_text(
        "{\n"
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
    print("Run centraleBoekhouding.exe (:8400) first, then centraleAdmin.exe (:8300).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
