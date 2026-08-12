"""Runtime layout: deploy root vs active workspace (person packs)."""
from __future__ import annotations

import sys
from pathlib import Path

_role: str = "local"  # "local" | "central_admin"
_central_data_root: Path | None = None
_selected_workspace: str | None = None
_local_deploy_root: Path | None = None


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def project_root() -> Path:
    """Source tree (``lokaleBoekhouding/``): app/, frontend/, scripts/, …"""
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


def set_runtime(
    *,
    role: str = "local",
    central_data_root: Path | None = None,
    workspace: str | None = None,
    local_deploy_root: Path | None = None,
) -> None:
    """Bind role and roots after config load (and on workspace switch)."""
    global _role, _central_data_root, _selected_workspace, _local_deploy_root
    _role = role if role in ("local", "central_admin") else "local"
    _central_data_root = central_data_root.resolve() if central_data_root else None
    _selected_workspace = workspace
    _local_deploy_root = local_deploy_root.resolve() if local_deploy_root else None


def is_central_admin() -> bool:
    return _role == "central_admin"


def role() -> str:
    return _role


def central_data_root() -> Path | None:
    return _central_data_root


def selected_workspace() -> str | None:
    return _selected_workspace


def set_selected_workspace(workspace: str) -> None:
    global _selected_workspace
    _selected_workspace = workspace.strip()


def app_root() -> Path:
    """Active workspace folder (person packs + categories.json)."""
    if _role == "central_admin" and _central_data_root is not None and _selected_workspace:
        return (_central_data_root / _selected_workspace).resolve()
    if _local_deploy_root is not None:
        return _local_deploy_root
    if is_frozen():
        return exe_dir()
    dkg = project_root() / "dkg"
    if dkg.is_dir():
        return dkg
    return project_root() / "boekhouding"


def project_path(*parts: str) -> Path:
    return project_root().joinpath(*parts)


def bundle_dir() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def _has_ui(dist: Path) -> bool:
    return (dist / "index.html").is_file()


def frontend_dist_dir() -> Path:
    candidates: list[Path] = []
    bundle = bundle_dir()
    if bundle is not None:
        candidates.extend(
            [
                bundle / "frontend" / "dist",
                bundle / "dist",
            ]
        )
    root = project_root()
    candidates.extend(
        [
            root / "frontend" / "dist",
            root / "dist",
        ]
    )
    for path in candidates:
        if _has_ui(path):
            return path
    return candidates[0] if candidates else root / "frontend" / "dist"


def frontend_dist_ok() -> bool:
    return _has_ui(frontend_dist_dir())
