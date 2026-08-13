"""Runtime paths for the always-on hub under server/."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_active_workspace: str | None = None


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def project_root() -> Path:
    """``server/hub`` (source) or exe parent when frozen."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def server_root() -> Path:
    """``bankingApp/server`` (parent of hub/)."""
    if is_frozen():
        return project_root()
    return project_root().parent


def data_root() -> Path:
    """Hub data root: ``server/workspaces/``."""
    env = os.environ.get("CENTRALE_DATA_ROOT", "").strip() or os.environ.get(
        "BOEKHOUDING_DATA_ROOT", ""
    ).strip()
    if env:
        return Path(env).resolve()
    if is_frozen():
        sibling = project_root() / "workspaces"
        if sibling.is_dir():
            return sibling.resolve()
        return project_root()
    return (server_root() / "workspaces").resolve()


def set_active_workspace(workspace: str | None) -> None:
    """Bind calc ``app_root()`` to ``data_root/<workspace>``."""
    global _active_workspace
    _active_workspace = (workspace or "").strip() or None


def active_workspace() -> str | None:
    return _active_workspace


def app_root() -> Path:
    """Active workspace folder (person packs) for calc."""
    root = data_root()
    if _active_workspace:
        return (root / _active_workspace).resolve()
    return root


def is_central_admin() -> bool:
    """Hub always runs calc against workspace data (secrets optional)."""
    return True
