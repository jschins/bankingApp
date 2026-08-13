"""Runtime for identical BFF: config selects workspace; data lives on the hub."""
from __future__ import annotations

import sys
from pathlib import Path

_selected_workspace: str | None = None
_allowed_workspaces: list[str] = []


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def project_root() -> Path:
    """``server/client``."""
    if is_frozen():
        base = getattr(sys, "_MEIPASS", None)
        return Path(base) if base else Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def exe_dir() -> Path:
    if is_frozen():
        exe = Path(sys.executable).resolve()
        if "Contents" in exe.parts and "MacOS" in exe.parts:
            return Path(*exe.parts[: exe.parts.index("Contents")]).parent
        return exe.parent
    return project_root()


def server_root() -> Path:
    if is_frozen():
        return exe_dir()
    return project_root().parent


def set_runtime(
    *,
    workspace: str | None = None,
    allowed_workspaces: list[str] | None = None,
    **_ignored: object,
) -> None:
    global _selected_workspace, _allowed_workspaces
    if allowed_workspaces is not None:
        _allowed_workspaces = [str(w).strip() for w in allowed_workspaces if str(w).strip()]
    if workspace:
        _selected_workspace = workspace.strip()
    elif _allowed_workspaces and not _selected_workspace:
        _selected_workspace = _allowed_workspaces[0]


def is_central_admin() -> bool:
    """True when this BFF may switch among multiple workspaces."""
    return len(_allowed_workspaces) > 1


def role() -> str:
    return "central_admin" if is_central_admin() else "local"


def selected_workspace() -> str | None:
    return _selected_workspace


def set_selected_workspace(workspace: str) -> None:
    global _selected_workspace
    ws = workspace.strip()
    if _allowed_workspaces and ws not in _allowed_workspaces:
        raise ValueError(f"Workspace {ws!r} not in config: {_allowed_workspaces}")
    _selected_workspace = ws


def allowed_workspaces() -> list[str]:
    return list(_allowed_workspaces)


def bundle_dir() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def _has_ui(dist: Path) -> bool:
    return (dist / "index.html").is_file()


def frontend_dist_dir() -> Path:
    candidates: list[Path] = []
    bundle = bundle_dir()
    if bundle is not None:
        candidates.extend([bundle / "frontend" / "dist", bundle / "dist"])
    root = project_root()
    candidates.extend([root / "frontend" / "dist", root / "dist"])
    for path in candidates:
        if _has_ui(path):
            return path
    return candidates[0] if candidates else root / "frontend" / "dist"


def frontend_dist_ok() -> bool:
    return _has_ui(frontend_dist_dir())
