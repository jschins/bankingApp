#!/usr/bin/env python3
"""Build centraleAdmin.exe into ``boekhouding/`` (next to the hub exe).

Bundles ``lokaleBoekhouding`` with ``VITE_APP_MODE=central_admin``.

Frontend rebuild is skipped when ``lokaleBoekhouding/frontend/dist`` already
exists and was built for central admin (see ``dist/.vite_app_mode``).
Force a rebuild with ``--force-frontend`` or ``FORCE_FRONTEND=1``.
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
MODE_MARKER = FRONTEND_DIST / ".vite_app_mode"
ENTRY = LOKALE / "entry_centrale_admin.py"
NAME = "centraleAdmin"
DEPLOY = CENTRALE / "boekhouding"
REQUIRED_MODE = "central_admin"


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def _force_frontend() -> bool:
    if "--force-frontend" in sys.argv:
        return True
    return os.environ.get("FORCE_FRONTEND", "").strip().lower() in ("1", "true", "yes", "on")


def _dist_is_central_admin() -> bool:
    if not (FRONTEND_DIST / "index.html").is_file():
        return False
    if not MODE_MARKER.is_file():
        return False
    try:
        return MODE_MARKER.read_text(encoding="utf-8").strip() == REQUIRED_MODE
    except OSError:
        return False


def _ensure_lokale_frontend() -> None:
    if not LOKALE.is_dir():
        raise SystemExit(f"Missing lokaleBoekhouding sources at {LOKALE}")
    if not ENTRY.is_file():
        raise SystemExit(f"Missing entry point: {ENTRY}")

    if not _force_frontend() and _dist_is_central_admin():
        print(
            f"Reusing existing frontend dist ({MODE_MARKER}={REQUIRED_MODE}). "
            "Pass --force-frontend to rebuild."
        )
        return

    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    print(f"Building frontend from {FRONTEND} (VITE_APP_MODE={REQUIRED_MODE}) ...")
    env = {**os.environ, "VITE_APP_MODE": REQUIRED_MODE}
    _run([npm, "install"], cwd=FRONTEND, env=env)
    _run([npm, "run", "build"], cwd=FRONTEND, env=env)
    if not (FRONTEND_DIST / "index.html").is_file():
        raise SystemExit(f"frontend build failed: {FRONTEND_DIST / 'index.html'} missing")
    MODE_MARKER.write_text(REQUIRED_MODE + "\n", encoding="utf-8")
    print(f"Wrote {MODE_MARKER}")


def main() -> int:
    _ensure_lokale_frontend()
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
