"""Runtime for identical BFF: config selects workspace; data lives on the hub."""
from __future__ import annotations

import sys
from contextvars import ContextVar
from pathlib import Path

from shared.user_access import ACCESS_LOCAL, ACCESS_PERSONAL, ACCESS_REGIONAL_ADMIN

_selected_workspace: str | None = None
_allowed_workspaces: list[str] = []
_access_mode: str = ACCESS_LOCAL

# Per-request overrides (multi-user auth). Fall back to process globals when unset.
_cv_selected_workspace: ContextVar[str | None] = ContextVar("selected_workspace", default=None)
_cv_allowed_workspaces: ContextVar[tuple[str, ...] | None] = ContextVar(
    "allowed_workspaces", default=None
)
_cv_access_mode: ContextVar[str | None] = ContextVar("access_mode", default=None)
_cv_username: ContextVar[str | None] = ContextVar("username", default=None)
_cv_title: ContextVar[str | None] = ContextVar("title", default=None)
_cv_workspace_key: ContextVar[str | None] = ContextVar("workspace_key", default=None)
_cv_person_key: ContextVar[str | None] = ContextVar("person_key", default=None)


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
    access: str | None = None,
    username: str | None = None,
    title: str | None = None,
    workspace_key: str | None = None,
    person_key: str | None = None,
    request_scoped: bool = False,
    **_ignored: object,
) -> None:
    """Update process globals, or only the current request context when ``request_scoped``."""
    global _selected_workspace, _allowed_workspaces, _access_mode

    access_n = str(access).strip().lower() if access is not None else None
    allowed_t = (
        tuple(str(w).strip() for w in allowed_workspaces if str(w).strip())
        if allowed_workspaces is not None
        else None
    )
    ws = workspace.strip() if workspace else None
    title_s = title.strip() if title is not None else None

    if request_scoped:
        if access_n is not None:
            _cv_access_mode.set(access_n)
        if allowed_t is not None:
            _cv_allowed_workspaces.set(allowed_t)
        if ws:
            _cv_selected_workspace.set(ws)
        elif allowed_t and _cv_selected_workspace.get() is None:
            _cv_selected_workspace.set(allowed_t[0])
        if username is not None:
            _cv_username.set(username.strip() or None)
        if title is not None:
            _cv_title.set(title_s or None)
        if workspace_key is not None:
            _cv_workspace_key.set(workspace_key.strip() or None)
        if person_key is not None:
            _cv_person_key.set(person_key.strip() or None)
        return

    if access_n is not None:
        _access_mode = access_n
        _cv_access_mode.set(None)
    if allowed_t is not None:
        _allowed_workspaces = list(allowed_t)
        _cv_allowed_workspaces.set(None)
    if ws:
        _selected_workspace = ws
        _cv_selected_workspace.set(None)
    elif _allowed_workspaces and not _selected_workspace:
        _selected_workspace = _allowed_workspaces[0]
    if username is not None:
        _cv_username.set(username.strip() or None)
    if title is not None:
        _cv_title.set(None)
    if workspace_key is not None:
        _cv_workspace_key.set(None)
    if person_key is not None:
        _cv_person_key.set(None)


def clear_request_runtime() -> None:
    _cv_selected_workspace.set(None)
    _cv_allowed_workspaces.set(None)
    _cv_access_mode.set(None)
    _cv_username.set(None)
    _cv_title.set(None)
    _cv_workspace_key.set(None)
    _cv_person_key.set(None)


def bind_request_runtime(
    *,
    access: str,
    allowed_workspaces: list[str] | None = None,
    workspace: str | None = None,
    username: str | None = None,
    title: str | None = None,
    workspace_key: str | None = None,
    person_key: str | None = None,
) -> None:
    set_runtime(
        access=access,
        allowed_workspaces=allowed_workspaces,
        workspace=workspace,
        username=username,
        title=title,
        workspace_key=workspace_key,
        person_key=person_key,
        request_scoped=True,
    )


def request_workspace_key() -> str | None:
    return _cv_workspace_key.get()


def request_person_key() -> str | None:
    return _cv_person_key.get()


def access_mode() -> str:
    cv = _cv_access_mode.get()
    return cv if cv is not None else _access_mode


def is_regional_admin() -> bool:
    """True when this login may switch workspaces."""
    return access_mode() == ACCESS_REGIONAL_ADMIN


def selected_workspace() -> str | None:
    cv = _cv_selected_workspace.get()
    if cv is not None:
        return cv
    return _selected_workspace


def set_selected_workspace(workspace: str) -> None:
    global _selected_workspace
    ws = workspace.strip()
    mode = access_mode()
    allowed = allowed_workspaces()
    if is_regional_admin():
        if _cv_access_mode.get() is not None:
            _cv_selected_workspace.set(ws)
        else:
            _selected_workspace = ws
        return
    if allowed and ws not in allowed:
        raise ValueError(f"Workspace {ws!r} not in config: {allowed}")
    if _cv_access_mode.get() is not None:
        _cv_selected_workspace.set(ws)
    else:
        _selected_workspace = ws


def request_allowed_workspaces() -> list[str] | None:
    """Per-request workspace allow-list, or ``None`` when unset."""
    cv = _cv_allowed_workspaces.get()
    return list(cv) if cv is not None else None


def allowed_workspaces() -> list[str]:
    cv = _cv_allowed_workspaces.get()
    if cv is not None:
        return list(cv)
    return list(_allowed_workspaces)


def current_username() -> str | None:
    return _cv_username.get()


def current_title() -> str | None:
    return _cv_title.get()


def bundle_dir() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


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


def _has_ui(dist: Path) -> bool:
    return (dist / "index.html").is_file()


def frontend_dist_ok() -> bool:
    return _has_ui(frontend_dist_dir())

